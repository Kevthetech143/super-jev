"""An escaped quote before a placeholder is not a secret; a real value after one still is. No provider calls.

    python3 -m pytest skills/super-jev/tests/test_held_approval.py -q
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
sys.path.insert(0, str(SKILL))
_spec = importlib.util.spec_from_file_location("pb_held_approval", SKILL / "prepare_bulk.py")
pb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pb)

BS = "\\"
# Placeholders behind a backslash-escaped quote, as they sit inside a code string literal.
PLACEHOLDERS = [
    "export TYPESAFE_API_KEY=" + BS + '"$(cat /path/to/key-file)' + BS + '"',
    "API_KEY=" + BS + '"${TYPESAFE_API_KEY}' + BS + '"',
    "api_key=" + BS + "'<your key here>" + BS + "'",
    "password=" + BS + '"$(pass show mail)' + BS + '"',
    "aws_secret_access_key=" + BS + '"$(cat creds)' + BS + '"',
    'print("export OPENAI_API_KEY=' + BS + '"$(cat ~/.key)' + BS + '"")',
    # A whole-value placeholder, plain quote or none, followed by a value boundary.
    'password="${VAR}"', "password=$VAR", "password=$VAR;", "api_key=${VAR}", "password: <your password>",
    'API_KEY="$(cat f)", other', "api_key=" + BS + '"${VAR}' + BS + '", x',
]
# Real values behind the same escaped quote must still be held.
REAL = [
    "API_KEY=" + BS + '"' + "sk" + "-live-9fQ2xZ7pL0aBcD3eF4" + BS + '"',
    "password=" + BS + '"' + "hunter2" + "xyz9" + BS + '"',
    "api_key=" + BS + "'" + "Zx9Qp2Lm8Rt4Vb6Nc1Hs3Kd7" + BS + "'",
    "API_KEY=" + BS + '"' + "hunter2" + "xyz9" + BS + '"',
    # A literal value that merely starts with $ is not placeholder syntax ($(...) or ${...}).
    "password=" + BS + '"' + "$3cret!" + "Pass9" + BS + '"',
    "password=" + BS + '"' + "$uper" + "Secret9" + BS + '"',
    "API_KEY=" + BS + '"' + "$9qP7vK2" + "mR8wL6z" + BS + '"',
    "api_key=" + BS + "'" + "$abc" + "Def123" + BS + "'",
    # Only a closed placeholder is exempt: an unclosed or malformed opening is a literal value.
    "password=" + BS + '"' + "$(Literal" + "Secret9" + BS + '"',
    "password=" + BS + '"' + "${Literal" + "Secret9" + BS + '"',
    "password=" + BS + '"' + "<Literal" + "Secret9" + BS + '"',
    "API_KEY=" + BS + '"' + "$(Literal" + "Secret9" + BS + '"',
    "API_KEY=" + BS + '"' + "${Literal" + "Secret9" + BS + '"',
    "API_KEY=" + BS + '"' + "<Literal" + "Secret9" + BS + '"',
    "password=" + BS + '"' + "$(Literal" + "Secret9}" + BS + '"',
    "password=" + BS + '"' + "$(" + "x" * 201 + ")" + BS + '"',
    # A placeholder must be the whole value: literal text glued after it is held (escaped quote, plain, none).
    "password=" + BS + '"${VAR}' + "hunter2" + "xyz9" + BS + '"',
    "password=" + BS + '"$(cat f)' + "hunter2" + "xyz9" + BS + '"',
    "password=" + BS + '"<x>' + "hunter2" + "xyz9" + BS + '"',
    "API_KEY=" + BS + '"${VAR}' + "Zx9Qp2Lm8" + "Rt4" + BS + '"',
    'password="${VAR}' + "hunter2" + 'xyz9"',
    'password="$(cat f)' + "hunter2" + 'xyz9"',
    'password="<x>' + "hunter2" + 'xyz9"',
    "password=${VAR}" + "hunter2" + "xyz9",
    'api_key="${VAR}' + "Zx9Qp2Lm8" + 'Rt4"',
]


def _node(inputs):
    js = ("import {hasSecret} from %s; import fs from 'node:fs';"
          "console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(hasSecret)));"
          % json.dumps(str(REPO / "src" / "secret-scan.ts")))
    out = subprocess.run(["node", "--input-type=module", "-e", js], input=json.dumps(inputs),
                         capture_output=True, text=True, check=True, cwd=REPO)
    return json.loads(out.stdout)


@pytest.mark.parametrize("text", PLACEHOLDERS)
def test_escaped_quote_placeholder_is_not_a_secret(text):
    assert pb.has_secret(text) is False


@pytest.mark.parametrize("text", REAL)
def test_real_value_after_escaped_quote_is_still_held(text):
    assert pb.has_secret(text) is True


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
def test_node_scanner_agrees_on_escaped_quotes():
    assert _node(PLACEHOLDERS + REAL) == [False] * len(PLACEHOLDERS) + [True] * len(REAL)
