"""Opt-in text inventory and stable symlink recipes, with no provider calls."""
import hashlib
import importlib.util
import json
import sys
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('pb_code', Path(__file__).resolve().parents[1] / 'prepare_bulk.py')
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


def test_default_and_explicit_extensions(tmp_path):
    for name in ['note.md', 'a.py', 'b.ts', 'c.js', 'd.sh', 'e.json-schema']:
        (tmp_path / name).write_text('plain text\n')
    assert pb.CONNECTABLE_EXTENSIONS == ('.md',)
    assert [p.name for p in pb.inventory([tmp_path])[0]] == ['note.md']
    files, held = pb.inventory([tmp_path], extensions=('.md', '.py', '.ts', '.js', '.sh', '.json-schema'))
    assert len(files) == 6 and not held


def test_code_holds_before_writer(tmp_path, monkeypatch):
    for name, data in {'null.py': b'x\x00y', 'control.py': b'x\x01y', 'bad.py': b'\xff',
                       'secret.py': b'password: syntheticTestOnlyValue9', 'big.py': b'x' * 101,
                       'ok.py': b'def add(a, b):\n    return a + b\n'}.items():
        (tmp_path / name).write_bytes(data)
    monkeypatch.setattr(pb, 'CEILING_BYTES', 100)
    monkeypatch.setattr(pb, 'SECTION_MAX_BYTES', 100)  # over the ceiling connects in sections up to this
    files, held = pb.inventory([tmp_path], extensions=('.py',))
    assert [p.name for p in files] == ['ok.py']
    assert len(held) == 5
    secret = tmp_path / 'secret.py'
    approvals = {str(secret): hashlib.sha256(secret.read_bytes()).hexdigest()}  # one file, pinned to its bytes
    files, held = pb.inventory([tmp_path], extensions=('.py',), approvals=approvals)
    assert {p.name for p in files} == {'ok.py', 'secret.py'}


def test_code_excerpt_has_definition_headings(tmp_path):
    p = tmp_path / 'module.py'
    p.write_text('class Example:\n    async def method(self):\n        pass\n')
    assert pb.excerpt(p)['headings'] == ['class Example:', 'async def method(self):']


def test_symlink_root_and_extension_recipe_survive_release_switch(tmp_path, monkeypatch):
    first, second = tmp_path / 'v1', tmp_path / 'v2'
    first.mkdir(); second.mkdir()
    (first / 'module.py').write_text('def old():\n    return 1\n')
    (second / 'module.py').write_text('def fresh():\n    return 2\n')
    link = tmp_path / 'current'
    link.symlink_to(first, target_is_directory=True)
    cache = tmp_path / 'cache'
    monkeypatch.setattr(pb, 'CACHE_DIR', cache)
    monkeypatch.setattr(pb, 'gate', lambda *a, **k: {'state': 'SUPPORTED', 'confidence': 1.0})
    monkeypatch.setenv('SUPERJEV_BATCH_JEV', '0')
    args = ['prepare_bulk.py', '--pointer', 'code', '--principal', 'reader', '--writer', 'builtin', '--no-connect']
    monkeypatch.setattr(sys, 'argv', args + ['--root', str(link), '--ext', 'py'])
    assert pb.main() == 0
    report = json.loads((cache / 'code-report.json').read_text())
    assert report['roots'] == [str(link)]
    assert report['extensions'] == ['.md', '.py']
    assert report['approved'] == [str(link / 'module.py')]
    link.unlink(); link.symlink_to(second, target_is_directory=True)
    monkeypatch.setattr(sys, 'argv', args + ['--refresh'])
    assert pb.main() == 0
    entries = json.loads((cache / 'code.json').read_text())
    assert 'fresh' in entries[str(link / 'module.py')]['description']


@pytest.mark.parametrize('ext', ['../py', '*', 'py,,ts', ''])
def test_bad_extension_refused_before_inventory(tmp_path, monkeypatch, ext):
    monkeypatch.setattr(sys, 'argv', ['prepare_bulk.py', '--root', str(tmp_path), '--pointer', 'code',
                                    '--principal', 'reader', '--ext', ext])
    with pytest.raises(SystemExit) as exc:
        pb.main()
    assert exc.value.code == 2


@pytest.mark.parametrize('ext', ['env', '.PEM', 'key', 'p12', 'pfx', 'jks', 'kdbx', 'keystore', 'pem.txt'])
def test_credential_extensions_cannot_be_opted_in(tmp_path, monkeypatch, ext):
    monkeypatch.setattr(sys, 'argv', ['prepare_bulk.py', '--root', str(tmp_path), '--pointer', 'code',
                                    '--principal', 'reader', '--ext', ext])
    with pytest.raises(SystemExit) as exc:
        pb.main()
    assert exc.value.code == 2


def test_declaration_matchers_agree_between_writer_and_chunker():
    root = Path(__file__).resolve().parents[3]
    cases = {'.py': ['class Demo:', '    async def run(self):', 'def f():', 'not a declaration'],
             '.ts': ['export default class Demo {}', 'export async function go() {}', 'interface Thing {}', 'type Id = string', 'const ordinary = 1'],
             '.js': ['function* generate() {}', 'export function run() {}'],
             '.sh': ['work() {', 'function other {', 'echo ordinary']}
    rows = [{'id': str(i), 'text': line, 'codeExtension': ext}
            for i, (ext, line) in enumerate((ext, line) for ext, lines in cases.items() for line in lines)]
    result = subprocess.run(['node', str(root / 'experiments/verified-pointer-memory/chunk_paths.mjs')],
                            input=json.dumps(rows), text=True, capture_output=True, check=True)
    for row, chunks in zip(rows, json.loads(result.stdout)):
        assert bool(chunks[0]['heading']) == pb.code_heading(row['text'], row['codeExtension'])


def test_credential_target_and_compound_name_held_even_with_override(tmp_path):
    target = tmp_path / 'server.pem'
    target.write_text('an unrecognized credential encoding')
    (tmp_path / 'ordinary.txt').symlink_to(target)
    (tmp_path / 'backup.key.txt').write_text('another unrecognized encoding')
    files, held = pb.inventory([tmp_path], extensions=('.txt',))
    assert not files
    assert len(held) == 2 and all('cannot be overridden' in why for _, why in held)
