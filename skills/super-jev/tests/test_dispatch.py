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
    assert json.loads(result.stdout)['status'] == 'preparation-required'


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


def test_missing_tool_and_unknown_tool_fail_without_fallback(tmp_path):
    _, skill = installed(tmp_path)
    assert run(skill, 'find', '--list-datasets').returncode == 2
    assert 'not installed' in json.loads(run(skill, 'skills').stdout)['reason']
    assert run(skill, 'invented').returncode == 2
    assert set(json.loads(run(skill).stdout)['tools']) == {'skills','find','check','verify'}
