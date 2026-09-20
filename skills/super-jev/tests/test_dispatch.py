import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

DISPATCH = Path(__file__).resolve().parents[1] / 'dispatch.py'


def installed(tmp_path, family='.codex'):
    root = tmp_path / family / 'skills'
    skill = root / 'super-jev'
    skill.mkdir(parents=True)
    shutil.copyfile(DISPATCH, skill / 'dispatch.py')
    return root, skill


def stub(path, body='printf "%s\\n" "$@"\nexit 7\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n' + body)


def run(skill, *args):
    return subprocess.run([sys.executable, str(skill / 'dispatch.py'), *args], capture_output=True, text=True)


@pytest.mark.parametrize('family', ['.codex', '.claude'])
def test_skills_preserves_arguments_and_seat_roots(tmp_path, family):
    root, skill = installed(tmp_path, family)
    stub(root / 'skill-search/search.sh')
    result = run(skill, 'skills', '--request-file', 'literal $(do-not-run).json')
    assert result.returncode == 7
    expected = ['--request-file', 'literal $(do-not-run).json']
    if family == '.claude':
        expected += ['--config', str(root / 'skill-search/roots-claude.json')]
    assert result.stdout.splitlines() == expected


def test_find_preserves_backend_json_and_nonzero_status(tmp_path):
    root, skill = installed(tmp_path)
    stub(root / 'fleet-retrieval-experiment/run.sh', 'echo \'{"status":"preparation-required"}\'\nexit 4\n')
    result = run(skill, 'find', '--dataset', 'example', '--request', 'exact request')
    assert result.returncode == 4
    body = json.loads(result.stdout)
    assert body['status'] == 'preparation-required'
    assert body['nextAction'] == 'connect-reviewed-local-files'
    assert body['setup']['requestTemplate']['action'] == 'connect'
    assert body['setup']['requestTemplate']['sources'][0]['path']
    assert body['hint']
    assert body['message'].startswith("Let's connect")
    assert 'memory' in body['setup']['steps'][-1]


@pytest.mark.parametrize('tool,door', [('check','gate'),('verify','verify')])
def test_checks_use_existing_shim_and_keep_verdict(tmp_path, tool, door):
    _, skill = installed(tmp_path)
    stub(skill / 'superjev')
    result = run(skill, tool, 'evidence with spaces', '--json')
    assert result.returncode == 7
    assert result.stdout.splitlines() == [door, 'evidence with spaces', '--json']


def test_check_without_shim_uses_original_python_cli(tmp_path):
    _, skill = installed(tmp_path)
    (skill / 'superjev.py').write_text('import sys; print(repr(sys.argv[1:])); sys.exit(3)')
    result = run(skill, 'check', '--claim', 'literal claim')
    assert result.returncode == 3
    assert result.stdout.strip() == "['gate', '--claim', 'literal claim']"


def test_memory_passthrough_uses_checkout_relative_experiment(tmp_path):
    root, skill = installed(tmp_path)
    entry = tmp_path / '.codex/experiments/verified-pointer-memory/cli.py'
    entry.parent.mkdir(parents=True)
    entry.write_text('import sys; print(repr(sys.argv[1:])); sys.exit(5)')
    result = run(skill, 'memory', '--config', 'private.json', '--input', 'request.json')
    assert result.returncode == 5
    assert result.stdout.strip() == "['--config', 'private.json', '--input', 'request.json']"


def test_memory_describe_is_machine_readable_and_explicitly_experimental(tmp_path):
    _, skill = installed(tmp_path)
    entry = tmp_path / '.codex/experiments/verified-pointer-memory/cli.py'
    entry.parent.mkdir(parents=True)
    entry.write_text('import sys; print(repr(sys.argv[1:])); sys.exit(0)')
    result = run(skill, 'memory', '--describe')
    assert result.returncode == 0
    assert result.stdout.strip() == "['--describe']"


def test_memory_reports_structured_missing_dependency(tmp_path):
    _, skill = installed(tmp_path)
    result = run(skill, 'memory', '--config', 'private.json', '--input', 'request.json')
    assert result.returncode == 2
    assert json.loads(result.stdout)['reason'] == 'missing-dependency'


def test_missing_tool_and_unknown_tool_fail_without_fallback(tmp_path):
    _, skill = installed(tmp_path)
    assert run(skill, 'find', '--list-datasets').returncode == 2
    assert 'not installed' in json.loads(run(skill, 'skills').stdout)['reason']
    assert run(skill, 'invented').returncode == 2
    assert set(json.loads(run(skill).stdout)['tools']) == {'help','skills','find','check','verify','memory'}

@pytest.mark.parametrize('family', ['.codex', '.claude'])
def test_installed_memory_wrapper_reenters_with_config(tmp_path, monkeypatch, family):
    monkeypatch.delenv('SUPERJEV_REPO', raising=False)
    monkeypatch.delenv('SUPERJEV_MEMORY_WRAPPER_ACTIVE', raising=False)
    _, skill = installed(tmp_path, family)
    repo = tmp_path / 'real repo'
    entry = repo / 'experiments/verified-pointer-memory/cli.py'
    entry.parent.mkdir(parents=True)
    entry.write_text('import json,sys; print(json.dumps(sys.argv[1:]))')
    stub(skill / 'memory.sh', f'export SUPERJEV_REPO="{repo}"\nexec "{sys.executable}" "{skill / "dispatch.py"}" memory --config local.json "$@"\n')
    result = run(skill, 'memory', '--describe')
    assert result.returncode == 0
    assert json.loads(result.stdout) == ['--config', 'local.json', '--describe']
    result = run(skill, 'memory', '--input', 'request with spaces.json')
    assert json.loads(result.stdout) == ['--config', 'local.json', '--input', 'request with spaces.json']


def test_explicit_memory_repo_bypasses_wrapper(tmp_path, monkeypatch):
    _, skill = installed(tmp_path)
    repo = tmp_path / 'chosen'
    entry = repo / 'experiments/verified-pointer-memory/cli.py'
    entry.parent.mkdir(parents=True)
    entry.write_text('print("chosen-runtime")')
    monkeypatch.setenv('SUPERJEV_REPO', str(repo))
    stub(skill / 'memory.sh', 'exit 99\n')
    result = run(skill, 'memory', '--describe')
    assert result.returncode == 0
    assert result.stdout.strip() == 'chosen-runtime'


def test_broken_wrapper_returns_setup_hint_without_recursing(tmp_path, monkeypatch):
    monkeypatch.delenv('SUPERJEV_REPO', raising=False)
    monkeypatch.delenv('SUPERJEV_MEMORY_WRAPPER_ACTIVE', raising=False)
    _, skill = installed(tmp_path)
    stub(skill / 'memory.sh', f'exec "{sys.executable}" "{skill / "dispatch.py"}" memory "$@"\n')
    result = run(skill, 'memory', '--describe')
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body['reason'] == 'missing-dependency'
    assert body['nextAction'] == 'configure-memory-runtime'
    assert body['hint'] and body['helpCommand']


@pytest.mark.parametrize('payload', [b'{"status":"ready","passages":[{"text":"unchanged"}]}\n', b'not json\n', b'[]\n', b'{"status":"error","reason":"no access"}\n'])
def test_find_nonpreparation_outputs_unchanged(tmp_path, payload):
    root, skill = installed(tmp_path)
    entry = root / 'fleet-retrieval-experiment/run.sh'
    fixture = tmp_path / 'output'
    fixture.write_bytes(payload)
    stub(entry, f'cat "{fixture}"\nexit 3\n')
    result = run(skill, 'find', '--request', 'original')
    assert result.returncode == 3
    assert result.stdout.encode() == payload


def test_find_preserves_pending_sources_and_backend_reason(tmp_path):
    root, skill = installed(tmp_path)
    payload = {'status': 'preparation-required', 'reason': 'stale-source',
               'pending': [{'sourceId': 'record', 'startLine': 4, 'endLine': 9}],
               'passages': [], 'nextAction': 'existing-backend-action', 'hint': 'Existing specific guidance'}
    fixture = tmp_path / 'response.json'
    fixture.write_text(json.dumps(payload))
    stub(root / 'fleet-retrieval-experiment/run.sh', f'cat "{fixture}"\n')
    body = json.loads(run(skill, 'find').stdout)
    for key, value in payload.items():
        assert body[key] == value
    assert body['setup']['requestTemplate']['action'] == 'connect'
