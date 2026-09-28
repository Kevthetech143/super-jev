"""Held code files: an escaped quote before a placeholder is not a secret, and a reviewed
--approve-held file (pinned to its sha256) survives --refresh until its bytes change.
No provider calls: builtin writer, --no-connect.

    python3 -m pytest skills/super-jev/tests/test_held_approval.py -q
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
]
# Real values behind the same escaped quote must still be held.
REAL = [
    "API_KEY=" + BS + '"' + "sk" + "-live-9fQ2xZ7pL0aBcD3eF4" + BS + '"',
    "password=" + BS + '"' + "hunter2" + "xyz9" + BS + '"',
    "api_key=" + BS + "'" + "Zx9Qp2Lm8Rt4Vb6Nc1Hs3Kd7" + BS + "'",
    "API_KEY=" + BS + '"' + "hunter2" + "xyz9" + BS + '"',
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


# --approve-held: a reviewed approval pinned to the file's bytes.

SECRETISH = "# fixture\nFAKE = 'pass" + "word: hunter2" + "xyz'\n"


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


def test_approved_file_survives_refresh_until_its_bytes_change(run, capsys):
    f = run.root / "fixture.py"
    f.write_text(SECRETISH)
    code, rep = run()
    assert code == 0 and str(f) not in rep["approved"]
    code, rep = run("--approve-held", str(f))
    assert code == 0 and str(f) in rep["approved"]
    assert rep["approvedHeld"] == [{"path": str(f), "sha256": _sha(f)}]
    out = capsys.readouterr().out
    assert "APPROVED" in out and "fixture.py" in out
    # Refresh (the auto-heal path) with no flag replays the approval: same bytes, still admitted.
    code, rep = run(refresh=True)
    assert code == 0 and str(f) in rep["approved"]
    assert rep["approvedHeld"] == [{"path": str(f), "sha256": _sha(f)}]
    # A changed file is held again, and the reason names the old approval.
    old = _sha(f)
    f.write_text(SECRETISH + "# edited\n")
    code, rep = run(refresh=True)
    assert str(f) not in rep["approved"]
    why = dict(rep["held"])[str(f)]
    assert "card/password-like text" in why and old[:12] in why and "changed since" in why
    assert "--approve-held" in capsys.readouterr().out


def test_allow_held_is_still_never_replayed(run):
    f = run.root / "fixture.py"
    f.write_text(SECRETISH)
    code, rep = run("--allow-held")
    assert str(f) in rep["approved"] and not rep.get("approvedHeld")
    code, rep = run(refresh=True)
    assert str(f) not in rep["approved"]


@pytest.mark.parametrize("name", ["deploy.pem", "prod.env", "a.key.py"])
def test_credential_suffix_can_never_be_approved(run, name):
    f = run.root / name
    f.write_text("plain\n")
    code, rep = run("--approve-held", str(f))
    assert code == 2 and rep is None


def test_size_held_file_can_be_approved_under_the_hard_cap(run):
    big = run.root / "big.py"
    big.write_text("x = 1\n" * (pb.CEILING_BYTES // 6 + 100))
    assert pb.CEILING_BYTES < big.stat().st_size < 1_000_000
    code, rep = run()
    assert "over size ceiling" in dict(rep["held"])[str(big)]
    code, rep = run("--approve-held", str(big))
    assert str(big) in rep["approved"]
    assert rep["approvedHeld"] == [{"path": str(big), "sha256": _sha(big)}]
    code, rep = run(refresh=True)
    assert str(big) in rep["approved"]


def test_size_over_the_hard_cap_stays_held_with_split_hint(run):
    huge = run.root / "huge.py"
    huge.write_text("x = 1\n" * (1_000_000 // 6 + 10))
    assert huge.stat().st_size > 1_000_000
    code, rep = run("--approve-held", str(huge))
    assert str(huge) not in rep["approved"]
    why = dict(rep["held"])[str(huge)]
    assert "over size ceiling" in why and "split it into smaller text files" in why
    assert not rep.get("approvedHeld")


def test_unheld_or_unknown_path_is_not_recorded(run, capsys):
    code, rep = run("--approve-held", str(run.root / "ok.py"))
    assert code == 0 and not rep.get("approvedHeld")
    assert "not held" in capsys.readouterr().out
