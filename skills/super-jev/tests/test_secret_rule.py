#!/usr/bin/env python3
"""The secret-scan promise: hold only real secret values. A known token shape, a Luhn card, or a
password/key keyword followed by a literal value (a digit or symbol in it, not a placeholder or a
call) is held; an identifier, attribute, call, '...', placeholder or 'moved to' note is not.
One placeholder list serves every keyword rule. A held file is released only by a per-file,
sha256-pinned --approve-held review; there is no blanket flag. Offline; no provider calls.
Secret-shaped values are built by concatenation so this file never holds one.

    python3 -m pytest skills/super-jev/tests/test_secret_rule.py -q
"""
import hashlib
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
_spec = importlib.util.spec_from_file_location("pb_secret_rule", SKILL / "prepare_bulk.py")
pb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pb)

PW = "hunter2" + "xyz9"
HELD = [
    "password=" + PW, "password: " + PW, 'password = "' + PW + '"', "PASSWORD: 12345678",
    "api_key=" + "Zx9Qp2Lm8" + "Rt4Vb6Nc1Hs3Kd7", "apikey: " + "Zx9Qp2Lm8" + "Rt4", "api-key: sk" + "-" + "a1B2c3D4",
    "secret_key=" + "Zx9Qp2Lm8" + "Rt4Vb6", "access_token: " + "Zx9Qp2Lm8" + "Rt4Vb6",
    "client_secret = '" + "Zx9Qp2Lm8" + "Rt4'", "auth-token=" + "Zx9Qp2Lm8" + "Rt4",
    "aws_secret_access_key=" + "wJalrXUtnFEMI/K7MDENG" + "/bPxRfiCYEXAMPLEKEY",
    "password is " + "hunter" + "2!", "password=$3cret" + "Pass9", "password=<Literal" + "Secret9",
    "sk" + "_live_9fQ2xZ7pL0aB3cD8eF", "gh" + "p_" + "a1" * 18, "AK" + "IA1234567890ABCDEF",
    "8" * 9 + ":AA" + "Zx9Qp2Lm8" * 4, "-----BEGIN RSA PRIV" + "ATE KEY-----", "4111 1111 " + "1111 1111",
    "Authorization: Basic " + "dXNlcjpwYXNzd29yZA==",
]
# The false holds named in the audit (ordinary code, pointers, placeholders) and their kin.
NOT_HELD = [
    "password = self.password", "api_key = get_key()", 'password: str = ""', "x.api_key = api_key",
    'api_key=os.environ["X"]', "password=password)", "api_key=...", "api_key=yes", "apikey=demo",
    "Password: (moved to logins.md)", "password: moved to vault", "password: in vault", "KEY: in other-file.md",
    "api_key: <your key here>", 'password":"<bar>"', "api-key: <zerox...>", "api_key=builder...",
    "api_key=get_key2()", "password=${VAR}", "password=$VAR1", "password=$(cat ~/.pw)",
    'export TYPESAFE_API_KEY="$(cat ~/.key)"', "access_token = get_token()", "auth_token = self.token",
    "client_secret: <your secret>", "secret_key=os.environ.get('K')", "aws_secret_access_key=$(cat creds)",
    "1password: " + "abc123", "password", "the api key is yours",
]


@pytest.mark.parametrize("text", HELD)
def test_real_looking_secret_is_held(text):
    assert pb.has_secret(text) is True


@pytest.mark.parametrize("text", NOT_HELD)
def test_identifier_call_placeholder_or_pointer_is_not_held(text):
    assert pb.has_secret(text) is False


def test_one_placeholder_list_serves_every_keyword_rule():
    pat = json.loads((SKILL / "secret_patterns.json").read_text())
    assert "placeholder" in pat
    assert "in|see|stored" not in pat["word"] + pat["token"]  # the old pointer-word lists are gone
    assert pat["word"].count("{PH}") == 1 and "{PH}" not in pat["placeholder"]
    assert pat["token"].count("$(") == 0  # no second copy of the placeholder shapes inside token


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
def test_node_scanner_agrees():
    js = ("import {hasSecret} from %s; import fs from 'node:fs';"
          "console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(hasSecret)));"
          % json.dumps(str(REPO / "src" / "secret-scan.ts")))
    out = subprocess.run(["node", "--input-type=module", "-e", js], input=json.dumps(HELD + NOT_HELD),
                         capture_output=True, text=True, check=True, cwd=REPO)
    assert json.loads(out.stdout) == [True] * len(HELD) + [False] * len(NOT_HELD)


# Per-file, sha256-pinned approval replaces the blanket flag.

SECRETISH = "# fixture\nmode = 'pass" + "word: " + PW + "'\n"


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    monkeypatch.setattr(pb, "gate", lambda *a, **k: pytest.fail("builtin writer needs no judge"))
    root = tmp_path / "r"
    root.mkdir()
    (root / "ok.py").write_text("def add(a, b):\n    return a + b\n")

    def go(*extra, refresh=False):
        base = ["prepare_bulk.py", "--pointer", "code", "--principal", "reader", "--writer", "builtin",
                "--no-connect"]
        base += ["--refresh"] if refresh else ["--root", str(root), "--ext", "py"]
        monkeypatch.setattr(sys, "argv", base + list(extra))
        code = pb.main()
        rep_path = tmp_path / "cache" / "code-report.json"
        return code, (json.loads(rep_path.read_text()) if rep_path.is_file() else None)
    go.root = root
    return go


def test_allow_held_flag_is_gone(run, capsys):
    (run.root / "fixture.py").write_text(SECRETISH)
    with pytest.raises(SystemExit) as e:
        run("--allow-held")
    assert e.value.code == 2
    assert "--allow-held" not in pb.__doc__ and "--allow-held" not in (SKILL / "ask.py").read_text()


def test_secret_held_file_stays_held_until_approved_by_name(run, capsys):
    f, other = run.root / "fixture.py", run.root / "other.py"
    f.write_text(SECRETISH)
    other.write_text(SECRETISH + "# second\n")
    code, rep = run()
    assert str(f) not in rep["approved"] and str(other) not in rep["approved"]
    out = capsys.readouterr().out
    assert "--approve-held" in out and PW not in out
    code, rep = run("--approve-held", str(f))
    assert code == 0 and str(f) in rep["approved"] and str(other) not in rep["approved"]
    assert rep["approvedHeld"] == [{"path": str(f), "sha256": _sha(f)}]
    assert PW not in capsys.readouterr().out


def test_approval_is_pinned_to_the_reviewed_bytes(run):
    f = run.root / "fixture.py"
    f.write_text(SECRETISH)
    run("--approve-held", str(f))
    code, rep = run(refresh=True)
    assert str(f) in rep["approved"]  # same bytes: the approval replays
    f.write_text(SECRETISH + "# edited after review\n")
    code, rep = run(refresh=True)
    assert str(f) not in rep["approved"]
    assert "changed since its --approve-held review" in dict(rep["held"])[str(f)]


def test_secret_name_can_be_approved_but_credential_suffix_never(run):
    named = run.root / "password-notes.py"
    named.write_text("x = 1\n")
    code, rep = run("--approve-held", str(named))
    assert code == 0 and str(named) in rep["approved"]
    pem = run.root / "deploy.pem"
    pem.write_text("plain\n")
    code, rep = run("--approve-held", str(pem))
    assert code == 2


def test_approved_secret_file_over_the_hard_cap_is_never_admitted_whole(run):
    f = run.root / "huge.py"
    f.write_text(SECRETISH + "x = 1\n" * (pb.APPROVE_MAX_BYTES // 6 + 10))
    code, rep = run("--approve-held", str(f))
    assert str(f) not in rep["approved"] and not rep.get("approvedHeld")
