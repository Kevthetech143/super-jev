"""Bounded, explicitly reviewed local paths -> immutable prepared dataset."""
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from service import Service

MAX_FILES = 50
MAX_BYTES = 5 * 1024 * 1024


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _problem(reason, hint):
    return {'status': 'preparation-required', 'reason': reason, 'hint': hint,
            'nextAction': 'review-local-sources-then-connect'}


def _write(path, raw):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(raw)


def _atomic(path, data):
    fd, name = tempfile.mkstemp(prefix='.registry-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps(data, indent=2).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def connect(request, config):
    """Prepare approved bytes without network calls; return no source content."""
    try:
        return _connect(request, config)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.SubprocessError):
        # Never reflect subprocess stderr, document text, or arbitrary exception data.
        return _problem('connect-failed', 'No ready connection was confirmed. Check local file access, registry/DB access and Node 24+; retry the same explicit sources after review.')


def _connect(request, config):
    pointer = request.get('pointer')
    dataset = request.get('dataset', pointer)
    if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', v)
           for v in (pointer, dataset)):
        return _problem('invalid-name', 'Use pointer/dataset names of 1–128 letters, digits, dots, underscores, colons or hyphens.')
    principals = request.get('principals')
    if not isinstance(principals, list) or not principals or any(not isinstance(p, str) or not p.strip() for p in principals) or len(set(principals)) != len(principals):
        return _problem('invalid-principals', 'Supply a nonempty, duplicate-free list of authorized principal labels.')
    items = request.get('sources')
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_FILES:
        return _problem('source-limit', 'Supply 1–50 explicit UTF-8 text file paths, up to 5 MiB total. Directories are not expanded.')
    sources, seen_paths, seen_ids, total = [], set(), set(), 0
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            return _problem('invalid-source', 'Each source needs a path; description and stable id are optional.')
        path = Path(item['path']).expanduser().resolve(strict=True)
        if not path.is_file():
            return _problem('unsupported-source', 'Directories and special files are unsupported. Supply explicit authorized text files; export PDFs or other binary formats to reviewed UTF-8 first.')
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        total += len(raw)
        if total > MAX_BYTES:
            return _problem('byte-limit', 'The complete source set must fit within 5 MiB; split it into explicitly scoped connectors.')
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            return _problem('unsupported-source', 'Export non-UTF-8 or binary files into reviewed UTF-8 text first. No partial connection was created.')
        if not text.strip() or any(ord(c) < 32 and c not in '\n\r\t' for c in text):
            return _problem('unsupported-source', 'Empty or binary/control-character content cannot be connected. Supply nonempty reviewed UTF-8 text.')
        source_id = item.get('id', 'file:' + _hash(str(path).encode())[:24])
        if not isinstance(source_id, str) or not source_id or len(source_id) > 256 or source_id in seen_ids or path in seen_paths:
            return _problem('duplicate-source', 'Each source needs a unique path and nonempty unique id of at most 256 characters.')
        description = item.get('description', 'Local text file: ' + path.name)
        if not isinstance(description, str) or not description.strip() or len(description) > 4000:
            return _problem('invalid-description', 'Omit description for a filename description, or supply a factual description of at most 4000 characters.')
        seen_paths.add(path)
        seen_ids.add(source_id)
        sources.append({'id': source_id, 'path': str(path), 'raw': raw, 'text': text,
                        'description': description, 'sha256': _hash(raw), 'reviewedSHA': item.get('sha256')})
    if request.get('reviewed') is not True or any(s['reviewedSHA'] != s['sha256'] for s in sources):
        return {**_problem('review-required', 'Review these local files within authorized scope. To permit their entire text for provider processing, repeat connect with reviewed:true and each returned sha256. This is not automatic privacy approval.'),
                'sources': [{k: s[k] for k in ('path', 'id', 'sha256')} for s in sources],
                'fileCount': len(sources), 'bytes': total}
    if 'replace' in request and not isinstance(request['replace'], bool):
        return _problem('invalid-replace', 'replace must be true or false.')
    chunks = json.loads(subprocess.run(
        ['node', str(Path(__file__).with_name('chunk_paths.mjs'))],
        input=json.dumps([{'id': s['id'], 'text': s['text']} for s in sources]),
        text=True, capture_output=True, check=True, timeout=60).stdout)
    if len(chunks) != len(sources) or any(not c for c in chunks):
        return _problem('empty-preparation', 'All files must yield readable passages; no partial connection was created.')
    registry = Path(config['registry']).resolve()
    registry.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(registry) + '.connect.lock', os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(registry.read_text()) if registry.exists() else {'version': 1, 'datasets': {}}
        service = Service(config['db'], registry, lambda *_: (_ for _ in ()).throw(RuntimeError('provider must not run')))
        with service.connect() as db:
            rows = {name: json.loads(body) for name, body in db.execute('SELECT name, body FROM pointers')}
        old = rows.get(pointer)
        if (old or dataset in data['datasets']) and request.get('replace') is not True:
            return _problem('already-connected', 'This pointer or dataset exists. Review its scope and explicitly set replace:true to refresh it.')
        if old and (old['dataset'] != dataset or sorted(old['principals']) != sorted(principals)):
            return _problem('scope-change', 'Replacement must retain the same dataset and exact principal scope. Use a separate explicitly authorized connector for a different scope.')
        if any(name != pointer and row['dataset'] == dataset for name, row in rows.items()):
            return _problem('shared-dataset', 'Another pointer uses this dataset; choose a separate dataset name instead of replacing shared preparation.')
        if (dataset in data['datasets'] and not old
                and data['datasets'][dataset].get('pathConnection') != {'pointer': pointer, 'principals': sorted(principals)}):
            return _problem('unowned-dataset', 'An existing dataset cannot be overwritten by a new pointer. Choose a new dataset name.')
        if any(_hash(Path(s['path']).read_bytes()) != s['sha256'] for s in sources):
            return _problem('source-changed', 'A source changed after review. Review current bytes and repeat connect with their hashes.')
        folder = Path(tempfile.mkdtemp(prefix='.prepared-', dir=registry.parent))
        manifest = {'expectedPolicy': 'reviewed', 'descriptionsAffirmed': True, 'sources': [], 'preparations': []}
        for i, (s, passages) in enumerate(zip(sources, chunks)):
            prepared_path = folder / f'{i}.txt'
            _write(prepared_path, s['raw'])
            manifest['sources'].append({'id': s['id'], 'path': str(prepared_path), 'originalPath': s['path'], 'contentSHA': s['sha256'], 'description': s['description']})
            for p in passages:
                manifest['preparations'].append({'sourceId': s['id'], 'contentSHA': s['sha256'], 'chunkIndex': p['chunkIndex'], 'startLine': p['startLine'], 'endLine': p['endLine'], 'reviewedText': p['text'], 'safeHeading': p['heading'] or s['description'], 'policy': 'reviewed', 'status': 'reviewed'})
        manifest_path = folder / 'manifest.json'
        manifest_raw = json.dumps(manifest, ensure_ascii=False).encode()
        _write(manifest_path, manifest_raw)
        data['datasets'][dataset] = {'description': 'Reviewed local text connector: ' + dataset,
            'pathConnection': {'pointer': pointer, 'principals': sorted(principals)},
            'scope': f'Only the {len(sources)} explicitly supplied local files; no recursive discovery or automatic synchronization.',
            'manifestPath': str(manifest_path), 'manifestSHA256': _hash(manifest_raw),
            'originals': [{'path': s['path'], 'sha256': s['sha256']} for s in sources]}
        _atomic(registry, data)
        # Publication changes the fingerprint first, so interruption cannot reuse an old cache.
        service.register(pointer, dataset, principals)
        _, error = service.pointer(pointer, principals[0])
        if error:
            return _problem('source-changed', 'A source changed during connection. Review it and reconnect with replace:true.')
    return {'status': 'registered', 'pointer': pointer, 'dataset': dataset,
            'sources': [{'id': s['id'], 'originalPath': s['path']} for s in sources],
            'fileCount': len(sources), 'passageCount': len(manifest['preparations']),
            'nextAction': 'search', 'hint': 'Ready for snapshot searches. Originals are unchanged; upstream synchronization and privacy approval are not automatic.'}
