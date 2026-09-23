#!/usr/bin/env python3
"""Offline tests for rc1 hardening round 7: the secret scan lives in every function
that sends a request (jev_client.ask, ask.py's memory and navigation calls, the
writer, gh issue create), so no entry point can skip it.

Every CLI entry runs with urlopen replaced (via sitecustomize) by a recorder, and
must exit 1 with zero sends. Token-shaped strings are built by concatenation.

    python3 -m pytest skills/super-jev/tests/test_hardening_round7.py -q
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
PW = "password=" + "hunter2" + "hunter2"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(SKILL))
jc = load("jev_client_r7", SKILL / "lib" / "jev_client.py")
pb = load("prepare_bulk_r7", SKILL / "prepare_bulk.py")
ask = load("ask_r7", SKILL / "ask.py")

SITE = """import urllib.request, os
def _rec(*a, **k):
    open(os.environ["R7_SENT"], "a").write("sent\\n")
    raise OSError("blocked in test")
urllib.request.urlopen = _rec
"""


def run_cli(tmp_path, argv):
    (tmp_path / "site").mkdir(exist_ok=True)
    (tmp_path / "site" / "sitecustomize.py").write_text(SITE)
    sent = tmp_path / "sent.log"
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "site"), R7_SENT=str(sent),
               TYPESAFE_API_KEY="test-not-a-key")
    env.pop("SUPERJEV_GATE_CMD", None)
    p = subprocess.run([sys.executable, *argv], env=env, capture_output=True, text=True, timeout=120)
    return p, (sent.read_text().count("sent") if sent.exists() else 0)


# 1. the choke point itself: state, instructions and criteria are all scanned
@pytest.mark.parametrize("where", ["state", "instructions", "criteria"])
def test_ask_refuses_a_secret_anywhere_in_the_request(monkeypatch, where):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-a-key")
    monkeypatch.setattr(jc, "transport", lambda *a: pytest.fail("secret was sent"))
    q = {"instructions": "Is it true?", "criteria": {"YES": "yes", "NO": "no"}}
    state = "def f(): pass"
    if where == "state":
        state += " " + PW
    elif where == "instructions":
        q["instructions"] += " " + PW
    else:
        q["criteria"]["YES"] += " " + PW
    with pytest.raises(jc.JevError, match="contains a secret; not sent"):
        jc.ask(state, {"c1": q})


def test_ask_still_sends_a_clean_request(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-a-key")
    sent = []
    monkeypatch.setattr(jc, "transport", lambda *a: sent.append(a) or {"answers": {}})
    jc.ask("def f(): pass", {"c1": {"instructions": "Is it?", "criteria": {"YES": "y"}}})
    assert sent


# 2. every CLI entry: exit 1, zero sends
@pytest.mark.parametrize("entry", ["check", "gate-code", "gate-evidence"])
def test_cli_entries_send_nothing(tmp_path, entry):
    ev = tmp_path / "ev.py"
    ev.write_text("def f():\n    return 1\n")
    claim = ["--claim", f"f uses {PW} for login"]
    argv = {"check": [str(SKILL / "lib" / "jev_client.py"), str(ev), *claim],
            "gate-code": [str(SKILL / "superjev.py"), "gate", str(ev), *claim, "--claim-mode", "code"],
            "gate-evidence": [str(SKILL / "superjev.py"), "gate", str(ev), *claim]}[entry]
    p, sent = run_cli(tmp_path, argv)
    assert sent == 0
    assert p.returncode != 0 and "CLEAN" not in p.stdout


def test_ask_cli_question_is_never_sent(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ask.subprocess, "run", lambda *a, **k: calls.append(a) or pytest.fail("sent"))
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "t", f"what is {PW}"])
    monkeypatch.setattr(ask, "resolve_principal", lambda a: ("t", [f"what is {PW}"]))
    assert ask.main() == 1 and not calls
    assert "not sent" in capsys.readouterr().err


def test_ask_navigation_payload_is_scanned(tmp_path, monkeypatch):
    f = tmp_path / "n.md"
    f.write_text("The rate is 4%.\n")
    monkeypatch.setattr(ask.subprocess, "run", lambda *a, **k: pytest.fail("sent"))
    assert ask.confirm_one(f"rate {PW}", str(f))[3] == ask.HELD_SECRET


def test_writer_prompt_is_scanned():
    with pytest.raises(pb.WriterError, match="not sent"):
        pb.writer([{"path": "a.md", "excerpt": PW}], "m", command=["false"])


def test_gh_issue_body_is_scanned(monkeypatch):
    sj = load("superjev_r7", SKILL / "superjev.py")
    monkeypatch.setattr(sj.subprocess, "run", lambda *a, **k: pytest.fail("sent"))
    ok, msg = sj._run_gh_issue_create("o/r", "t", f"body {PW}")
    assert not ok and "not sent" in msg
