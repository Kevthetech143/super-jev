#!/usr/bin/env python3
"""Bounded offline repair comparison. Trusted build code only; never deploys a repair."""
import argparse
import contextlib
import difflib
import hashlib
import io
import importlib.machinery
import json
import os
import sys
import sysconfig
from pathlib import Path
from unittest.mock import patch
import scorecard

MAX_CASES = 32
MAX_FILES = 2000
MAX_BYTES = 64 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def allowed(path):
    p = Path(path)
    return (p.is_absolute() and not any(x.lower() in ('profile', 'documents') for x in p.parts)
            and p.name.lower() != 'logins.md' and not p.name.lower().endswith('-secret.md')
            and not p.name.lower().startswith('.env'))


def copy_build(ask_path, dest):
    """Copy Python dependencies, not credentials, caches or release launchers."""
    ask_path = scorecard.build_path(Path(ask_path).resolve())
    dest.mkdir()
    hashes = {}
    paths = list(ask_path.parent.glob('*.py')) + list((ask_path.parent / 'lib').rglob('*.py'))
    for shared in ('secret_patterns.json', 'judge_profiles.json'):
        if (ask_path.parent / shared).is_file():
            paths.append(ask_path.parent / shared)
    for source in paths:
        rel = source.relative_to(ask_path.parent)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        target.write_bytes(raw)
        hashes[str(rel)] = digest(raw)
    return {'entry': ask_path.name, 'files': hashes}


def validate_bundle(root, expected):
    raw = (root / 'manifest.json').read_bytes()
    if digest(raw) != expected:
        raise ValueError('snapshot checksum mismatch')
    data = json.loads(raw)
    expected_root = {'manifest.json', 'sources', 'baseline'}
    if data.get('held_out_sha256'):
        expected_root.add('held-out.json')
    if {p.name for p in root.iterdir()} != expected_root or any(p.is_symlink() for p in root.iterdir()):
        raise ValueError('unlisted or linked snapshot file')
    if data['baseline']['entry'] not in data['baseline']['files']:
        raise ValueError('unlisted baseline entry')
    for folder, expected_files in [('sources', set(data['blobs'])),
                                   ('baseline', set(data['baseline']['files']))]:
        actual = {str(p.relative_to(root / folder)) for p in (root / folder).rglob('*') if p.is_file()}
        if actual != expected_files or any(p.is_symlink() for p in (root / folder).rglob('*')):
            raise ValueError('unlisted or linked snapshot file')
    for name, sha in data['blobs'].items():
        if Path(name).name != name or digest((root / 'sources' / name).read_bytes()) != sha:
            raise ValueError('source snapshot changed')
    for rel, sha in data['baseline']['files'].items():
        if not (root / 'baseline' / rel).resolve().is_relative_to((root / 'baseline').resolve()):
            raise ValueError('invalid build path')
        if digest((root / 'baseline' / rel).read_bytes()) != sha:
            raise ValueError('baseline snapshot changed')
    if data.get('held_out_sha256'):
        if scorecard.frozen_cases(root / 'held-out.json', data['held_out_sha256']) != data.get('reserved'):
            raise ValueError('reserved rows differ from pinned manifest')
    elif data.get('reserved'):
        raise ValueError('reserved rows lack checksum')
    return data



@contextlib.contextmanager
def no_bytecode():
    def source_only(loader, fullname):
        return loader.source_to_code(loader.get_data(loader.path), loader.path)
    with patch.object(sys, 'dont_write_bytecode', True), patch.object(
            importlib.machinery.SourceFileLoader, 'get_code', source_only):
        yield


# Python audit events cover read_text, builtin/io/os.open and resolved aliases.
# This is a fail-closed guard for trusted Python builds, not an OS sandbox.
_READ_GUARDS = []
_STDLIB = Path(sysconfig.get_path("stdlib")).resolve()
_FORBIDDEN_EVENTS = {
    'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec', 'os.fork', 'os.forkpty',
    'os.mkdir', 'os.rename', 'os.remove', 'os.rmdir', 'os.symlink', 'os.link',
    'os.truncate', 'os.chmod', 'os.chown', 'os.utime', 'os.chdir', 'os.fchdir',
}
def _audit_reads(event, args):
    if not _READ_GUARDS:
        return
    permitted, violations = _READ_GUARDS[-1]
    if event in _FORBIDDEN_EVENTS or event.startswith('os.exec'):
        violations.append('forbidden operation: ' + event)
        raise PermissionError('operation forbidden during replay: ' + event)
    if event != 'open':
        return
    path = args[0]
    if isinstance(path, int):
        violations.append('file descriptor read')
        raise PermissionError('unfrozen file descriptor access')
    resolved = os.path.realpath(os.fsdecode(path))
    if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
        violations.append(resolved)
        raise PermissionError('writes forbidden during replay: ' + resolved)
    runtime = (Path(resolved).is_relative_to(_STDLIB) and Path(resolved).suffix == '.py'
               and not {'site-packages', 'dist-packages'}.intersection(Path(resolved).parts))
    if resolved not in permitted and not runtime:
        violations.append(resolved)
        raise PermissionError('unfrozen file access: ' + resolved)

sys.addaudithook(_audit_reads)


@contextlib.contextmanager
def guarded_reads(paths, violations):
    _READ_GUARDS.append(({os.path.realpath(p) for p in paths}, violations))
    try:
        yield
    finally:
        _READ_GUARDS.pop()


def evidence_gates(data, reads, target_i):
    reserved = data.get('reserved', [])
    held = [i for i, c in enumerate(data['cases']) if c in reserved]
    controls = [i for i, c in enumerate(data['cases'])
                if i != target_i and c['split'] != 'held-out']
    controls_ok = (any(reads[i][0] for i in controls)
                   and all(not reads[i][0] or reads[i][1] for i in controls))
    held_count = sum(bool(reads[i][0]) for i in held)
    held_ok = all(not reads[i][0] or reads[i][1] for i in held)
    return controls_ok, held_count, held_ok


def build_diff(old_root, old_spec, new_root, new_spec):
    result = []
    for rel in sorted(set(old_spec['files']) | set(new_spec['files'])):
        old = (old_root / rel).read_text() if rel in old_spec['files'] else ''
        new = (new_root / rel).read_text() if rel in new_spec['files'] else ''
        result.extend(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                      'a/skills/super-jev/' + rel, 'b/skills/super-jev/' + rel))
    return ''.join(result)

def freeze(target, controls, held, held_sha, baseline, cache, out):
    """Freeze only the principals' reviewed, non-secret files; no model call."""
    reserved = scorecard.frozen_cases(held, held_sha) if held else []
    target = {**target, 'split': 'tuned'}
    for c in scorecard.normalize_cases(controls, target['principal']):
        if c['split'] == 'held-out' and c not in reserved:
            raise ValueError('held-out input differs from the frozen reservation')
    cases = scorecard.normalize_cases([target] + controls + reserved, target['principal'])
    if len(cases) > MAX_CASES:
        raise ValueError('request budget exceeded')
    if not any((c['principal'], c['question']) != (target['principal'], target['question']) for c in cases if c['split'] != 'held-out'):
        raise ValueError('at least one independent control is required')
    if not allowed(target['gold'][0]):
        raise ValueError('target path is outside permitted note scope')
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    (out / 'sources').mkdir()
    baseline_spec = copy_build(baseline, out / 'baseline')
    with no_bytecode():
        ask = scorecard.load_ask(out / 'baseline' / baseline_spec['entry'], 'shadow_capture')
    # The existing dispatcher provides read-only connector panel metadata only.
    # Import/search code comes from the hashed copy, never the mutable baseline.
    if hasattr(ask, 'SKILL'):
        ask.SKILL = scorecard.build_path(Path(baseline).resolve()).parent / 'dispatch.py'
    ask.prepare_bulk.CACHE_DIR = Path(cache)
    principals = sorted({c['principal'] for c in cases})
    # Metadata only. Error/empty scopes cannot be called successful searches.
    pointers = {}
    for principal in principals:
        panel = ask.memory({'action': 'panel', 'principal': principal})
        if not isinstance(panel.get('pointers'), list) or panel.get('status') == 'error':
            raise ValueError('pointer metadata unavailable')
        pointers[principal] = [r['pointer'] if isinstance(r, dict) else r for r in panel['pointers']]
        if not pointers[principal]:
            raise ValueError('principal has no connected pointers')
    entries, names, admit, sources, blobs = {}, {}, {}, {}, {}
    total = 0
    for ptr in sorted({p for ps in pointers.values() for p in ps}):
        entries[ptr] = {path: entry for path, entry in ask.load_cache_files(ptr).items()
                        if allowed(path) and allowed(str(Path(path).resolve()))}
        names[ptr] = ask.connector_names(ptr)
        admit[ptr] = {}
        for path, entry in entries[ptr].items():
            if not isinstance(entry, dict) or not entry.get('pass'):
                continue
            admit[ptr][path] = ask.refresh_would_admit(path, ptr)
            if path in sources:
                continue
            if len(sources) >= MAX_FILES:
                raise ValueError('snapshot file budget exceeded')
            sources[path] = {'status': 'excluded'}
            if not allowed(path) or not allowed(str(Path(path).resolve())):
                continue
            try:
                if Path(path).stat().st_size > ask.prepare_bulk.CEILING_BYTES:
                    continue
                raw = Path(path).read_bytes()
            except OSError:
                sources[path] = {'status': 'missing'}
                continue
            if len(raw) > ask.prepare_bulk.CEILING_BYTES or ask.has_secret(raw.decode('utf-8', 'replace')):
                continue
            total += len(raw)
            if total > MAX_BYTES:
                raise ValueError('snapshot byte budget exceeded')
            sha = digest(raw)
            (out / 'sources' / sha).write_bytes(raw)
            blobs[sha] = sha
            sources[path] = {'status': 'captured', 'blob': sha, 'sha256': sha}
    data = dict(reserved=reserved, version=1, target=target, cases=cases, pointers=pointers, entries=entries,
                names=names, admit=admit, sources=sources, blobs=blobs, baseline=baseline_spec,
                held_out_sha256=held_sha, controls=[c['question'] for c in controls])
    if held:
        (out / 'held-out.json').write_bytes(Path(held).read_bytes())
    write_json(out / 'manifest.json', data)
    return digest((out / 'manifest.json').read_bytes())


def classify(data, rank, readable):
    gold = data['target']['gold'][0]
    source = data['sources'].get(gold)
    if not source or source['status'] != 'captured':
        return 'missing-or-unavailable-note', ['The gold file was not captured as reviewed evidence.']
    reviewed = [e[gold] for e in data['entries'].values() if gold in e and isinstance(e[gold], dict)]
    if not any(e.get('sha256') == source['sha256'] for e in reviewed):
        return 'stale-note', ['The current source bytes differ from every captured review.']
    if not readable:
        return 'search-ranking', ['The reviewed gold file misses the free read slots; routing/content remain unmeasured.']
    return 'routing-or-content-unresolved', ['Word search reaches the gold; paid-stage evidence must distinguish routing from content.']


def decide(target_before, target_after, controls_ok, held_count, held_ok, paid_ok):
    reasons = []
    if target_before or not target_after:
        reasons.append('target did not move from unreadable to readable')
    if not controls_ok:
        reasons.append('control or retrospective read loss')
    if not held_count:
        reasons.append('no independently reserved held-out evidence')
    if not held_ok:
        reasons.append('held-out read loss')
    if not paid_ok:
        reasons.append('paid-stage evidence missing or not accepted')
    return ('REJECT' if reasons else 'ACCEPT'), reasons


def compare(root, pin, candidate, out):
    data = validate_bundle(root, pin)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    candidate_spec = copy_build(candidate, out / 'candidate')
    tools_spec = copy_build(Path(__file__), out / 'tools')
    original_read = Path.read_bytes
    selected, slots = {}, {}
    def frozen_read(path):
        rec = data['sources'].get(str(path))
        if rec is None:
            return original_read(path)  # audit guard refuses unknown paths
        if rec['status'] != 'captured':
            raise FileNotFoundError(str(path))
        return original_read(root / 'sources' / rec['blob'])
    original_loader = scorecard.load_ask
    def loader(path, name):
        ask = original_loader(path, name)
        slots[name] = ask.FALLBACK_FILES
        ask.my_pointers = lambda pr: data['pointers'][pr]
        ask.load_cache_files = lambda ptr: data['entries'][ptr]
        ask.connector_names = lambda ptr: data['names'][ptr]
        ask.refresh_would_admit = lambda path, ptr: data['admit'][ptr].get(path, False)
        def no_provider(*args, **kwargs):
            raise RuntimeError('provider calls forbidden during offline shadow replay')
        ask.memory = ask.run_navigation = no_provider
        search = ask.word_search
        def word_search(question, pointers, limit=5, **kwargs):
            result = search(question, pointers, limit=limit, **kwargs)
            selected[(name, tuple(pointers), question)] = [r[1] for r in result[:ask.FALLBACK_FILES]]
            return result
        ask.word_search = word_search
        return ask
    casefile = out / 'requests.jsonl'
    casefile.write_text(''.join(json.dumps(c) + '\n' for c in data['cases']))
    args = ['--no-harvest', '--cases', str(casefile), '--json', '--ask',
            str(root / 'baseline' / data['baseline']['entry']), '--ask',
            str(out / 'candidate' / candidate_spec['entry'])]
    for principal in data['pointers']:
        args += ['--principal', principal]
    if data.get('held_out_sha256'):
        args += ['--held-out', str(root / 'held-out.json'), '--held-out-sha256', data['held_out_sha256']]
    permitted = [casefile, root / 'held-out.json']
    for folder, spec in [(root / 'baseline', data['baseline']), (out / 'candidate', candidate_spec),
                         (Path(__file__).parent, tools_spec)]:
        permitted += [folder / rel for rel in spec['files']]
    permitted += [root / 'sources' / name for name in data['blobs']]
    violations = []
    captured = io.StringIO()
    with no_bytecode(), guarded_reads(permitted, violations), patch.object(scorecard, 'load_ask', loader), patch.object(Path, 'read_bytes', frozen_read), contextlib.redirect_stdout(captured):
        rc = scorecard.main(args)
    report = json.loads(captured.getvalue())
    write_json(out / 'scorecard.json', report)
    target_i = next(i for i,c in enumerate(data['cases']) if c['question'] == data['target']['question'] and c['principal'] == data['target']['principal'])
    reads = [[any(g in selected.get((f'scorecard_ask_{b}', tuple(data['pointers'][c['principal']]), c['question']), []) for g in c['gold'])
              for b in range(2)] for c in data['cases']]
    cause, uncertainty = classify(data, report['rows'][target_i]['ranks'][0], reads[target_i][0])
    controls_ok, held_count, held_ok = evidence_gates(data, reads, target_i)
    # E must supply independently reviewed, snapshot-bound evidence before promotion.
    # This offline runner intentionally cannot turn an unverified external JSON into approval.
    decision, reasons = decide(*reads[target_i], controls_ok, held_count, held_ok, False)
    if violations:
        decision = 'REJECT'
        reasons.append('unfrozen file access attempted')
        uncertainty.extend('Unfrozen access: ' + path for path in sorted(set(violations)))
    if len(set(slots.values())) != 1:
        uncertainty.append('Read-slot budget changed; extra paid-stage work must be measured before promotion.')
    diff = build_diff(root / 'baseline', data['baseline'], out / 'candidate', candidate_spec)
    (out / 'candidate.patch').write_text(diff)
    proposal = ('Propose restoring/refreshing the reviewed note; inspect its source and connector. Do not edit it automatically.'
                if cause in ('missing-or-unavailable-note','stale-note') else
                'Review candidate.patch as an isolated code proposal; no source note was edited.' if diff else
                'Propose an isolated repair; no candidate patch supplied. Keep read-budget changes explicit and validate their paid cost.')
    (out / 'proposal.md').write_text('# Shadow repair proposal\n\n' + proposal + '\n')
    result = dict(decision=decision, reasons=reasons, uncertainty=uncertainty, cause=cause,
                  target_ranks=report['rows'][target_i]['ranks'], target_readable=reads[target_i],
                  read_slots=[slots[f'scorecard_ask_{i}'] for i in range(2)],
                  controls_preserved=controls_ok, held_out_preserved=held_ok, scorecard_exit=rc,
                  snapshot_sha256=pin, baseline=data['baseline'], candidate=candidate_spec,
                  paid_stage='not checked', deployed=False,
                  tools=tools_spec,
                  runner_sha256=tools_spec['files'][Path(__file__).name],
                  scorecard_sha256=tools_spec['files']['scorecard.py'])
    write_json(out / 'report.json', result)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--case', help='JSON target: question, gold list, principal')
    ap.add_argument('--controls', help='explicit JSONL controls/retrospective cases')
    ap.add_argument('--held-out'); ap.add_argument('--held-out-sha256')
    ap.add_argument('--baseline'); ap.add_argument('--cache')
    ap.add_argument('--snapshot'); ap.add_argument('--snapshot-sha256')
    ap.add_argument('--candidate', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    try:
        if a.snapshot:
            if not a.snapshot_sha256 or any((a.case,a.controls,a.held_out,a.baseline,a.cache)):
                raise ValueError('replay needs snapshot checksum and no new capture inputs')
            root, pin = Path(a.snapshot), a.snapshot_sha256
        else:
            if not all((a.case,a.baseline,a.cache)) or bool(a.held_out) != bool(a.held_out_sha256):
                raise ValueError('capture needs case, baseline, cache and paired held-out/checksum')
            root = Path(a.out + '-snapshot')
            pin = freeze(json.loads(Path(a.case).read_text()), scorecard.input_cases(a.controls) if a.controls else [],
                         a.held_out, a.held_out_sha256, a.baseline, a.cache, root)
        result = compare(root, pin, a.candidate, Path(a.out))
        print(json.dumps({'decision':result['decision'], 'report':str(Path(a.out)/'report.json'),
                          'snapshot':str(root), 'snapshot_sha256':pin}))
        return 0 if result['decision'] == 'ACCEPT' else 1
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        ap.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
