#!/usr/bin/env python3
"""Offline tests for a stranger's first run: the built-in judge client, the
fail-closed gate, setup/uninstall, the no-model writer and the
"nothing connected yet" message.

No network and no real key: the judge client is driven through an injected
transport or a local plain-HTTP fake server, and every test runs with HOME
and SUPERJEV_STATE_DIR in tmp_path.

    python3 -m pytest skills/super-jev/tests/test_selfboot.py -q
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
FAKE_KEY = "sk-test-never-print-me"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jev = load("jev_client_under_test", SKILL / "lib" / "jev_client.py")
setup = load("setup_under_test", SKILL / "setup.py")
sys.path.insert(0, str(SKILL))
ask = load("ask_under_test", SKILL / "ask.py")
prepare_bulk = load("prepare_bulk_under_test", SKILL / "prepare_bulk.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SUPERJEV_LEDGER", str(tmp_path / "ledger" / "calls.jsonl"))
    monkeypatch.delenv("SUPERJEV_GATE_CMD", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_KEY)
    return tmp_path


def answers(*claim_verdicts):
    a = {f"c{i}": {"choice": v, "confidence": 0.95} for i, v in enumerate(claim_verdicts, 1)}
    a.update({"leaked_internal": {"choice": "CLEAN", "confidence": 0.99},
              "time_sensitive": {"choice": "NOT_TIME_SENSITIVE", "confidence": 0.99},
              "self_contradictory": {"choice": "CONSISTENT", "confidence": 0.99},
              "overclaim": {"choice": "HONEST", "confidence": 0.99}})
    return {"model": "jev-test", "answers": a, "usage": {"input_tokens": 42}}


# ------------------------------------------------ built-in judge client

def test_client_supported_claim_exits_0_and_sends_the_key_only_as_a_header(env, monkeypatch, capsys):
    seen = []

    def fake(url, body, headers, timeout):
        seen.append((url, json.loads(body), headers))
        return answers("SUPPORTED")

    monkeypatch.setattr(jev, "transport", fake)
    ev = env / "ev.md"
    ev.write_text("The sky is blue.\n")
    code = jev.main([str(ev), "--kit", "reply", "--claim", "The sky is blue"])
    out = capsys.readouterr()
    assert code == 0
    url, body, headers = seen[0]
    assert url == jev.API_URL
    assert headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert "The sky is blue." in body["state"] and set(body["questions"]) >= {"c1", "overclaim"}
    assert FAKE_KEY not in out.out + out.err
    assert "c1   SUPPORTED" in out.out


@pytest.mark.parametrize("reply", [answers("NOT_SUPPORTED"), answers("CONTRADICTED"),
                                   {"model": "m", "answers": {}}])
def test_client_red_or_missing_answer_exits_3(env, monkeypatch, capsys, reply):
    monkeypatch.setattr(jev, "transport", lambda *a: reply)
    ev = env / "ev.md"
    ev.write_text("The sky is blue.\n")
    assert jev.main([str(ev), "--claim", "The sky is green"]) == 3


def test_client_without_a_key_exits_1_with_the_export_line(env, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    ev = env / "ev.md"
    ev.write_text("x\n")
    assert jev.main([str(ev), "--claim", "x is x here"]) == 1
    assert "export TYPESAFE_API_KEY" in capsys.readouterr().err


def test_default_gate_door_is_the_shipped_client():
    sj = load("superjev_for_door", SKILL / "superjev.py")
    assert sj.JEV_LIB == SKILL / "lib" / "jev_client.py"
    assert sj.JEV_LIB.is_file()
    assert ".claude" not in str(sj.JEV_LIB)


class _FakeJev(BaseHTTPRequestHandler):
    verdict = "SUPPORTED"

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps(answers(self.verdict)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_server(env, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _FakeJev)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("SUPERJEV_JEV_URL", f"http://127.0.0.1:{srv.server_port}/")
    yield _FakeJev
    srv.shutdown()


@pytest.mark.parametrize("verdict,rc,word", [("SUPPORTED", 0, "CLEAN"), ("NOT_SUPPORTED", 3, "READ"),
                                             ("CONTRADICTED", 3, "READ")])
def test_dispatch_check_uses_the_shipped_client_end_to_end(env, fake_server, verdict, rc, word):
    fake_server.verdict = verdict
    ev = env / "ev.md"
    ev.write_text("The sky is blue.\n")
    r = subprocess.run([sys.executable, str(SKILL / "dispatch.py"), "check", "--claim",
                        "The sky is blue", str(ev)], capture_output=True, text=True, env=os.environ)
    assert r.returncode == rc, r.stdout + r.stderr
    assert f"VERDICT: {word}" in r.stdout
    assert FAKE_KEY not in r.stdout + r.stderr


# ------------------------------------------------ fail closed

@pytest.mark.parametrize("printed,rc", [
    ("verdict: NOT_SUPPORTED (0.95) claim 1\n", 1),   # the tester's format: unreadable
    ("", 1),                                          # nothing at all
    ("  c1   NOT_SUPPORTED  0.95  the sky\n", 3),     # readable, red
    ("  c1   SUPPORTED      0.40  the sky\n", 3),     # readable, under the line
    ("  c1   SUPPORTED      0.95  a\n  overclaim  OVERCLAIMS  0.97\n", 3),
])
def test_gate_never_says_clean_on_a_door_that_exits_0_without_a_clean_table(env, printed, rc):
    stub = env / "stub.py"
    stub.write_text(f"import sys\nsys.stdout.write({printed!r})\n")
    ev = env / "ev.md"
    ev.write_text("The sky is blue.\n")
    e = dict(os.environ, SUPERJEV_GATE_CMD=f"{sys.executable} {stub}")
    r = subprocess.run([sys.executable, str(SKILL / "dispatch.py"), "check", "--claim",
                        "The sky is blue", str(ev)], capture_output=True, text=True, env=e)
    assert r.returncode == rc, r.stdout
    assert "VERDICT: CLEAN" not in r.stdout


def test_gate_fail_closed_needs_a_row_per_explicit_claim():
    sj = load("superjev_for_fc", SKILL / "superjev.py")
    one_row = "  c1   SUPPORTED      0.95  a\n"
    assert sj.gate_fail_closed(0, one_row, 1) == 0
    assert sj.gate_fail_closed(0, one_row, 2) == sj.GATE_UNREADABLE_EXIT
    assert sj.gate_fail_closed(3, one_row, 1) == 3


# ------------------------------------------------ setup / uninstall

def test_setup_is_idempotent_and_never_overwrites_config(env, capsys):
    assert setup.main([]) == 0
    cfg = setup.config_path()
    assert json.loads(cfg.read_text())["registry"] == "registry.json"
    assert oct(cfg.parent.stat().st_mode & 0o777) == "0o700"
    cfg.write_text('{"db": "mine.sqlite3", "registry": "mine.json"}\n')
    assert setup.main([]) == 0
    assert "mine.sqlite3" in cfg.read_text()
    out = capsys.readouterr().out
    assert "left unchanged" in out and "Next step" in out and FAKE_KEY not in out


def test_setup_without_a_key_says_how_to_set_it(env, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert setup.main([]) == 1
    assert "export TYPESAFE_API_KEY" in capsys.readouterr().out


def test_uninstall_removes_state_caches_and_links_into_the_checkout(env, monkeypatch, tmp_path):
    setup.main([])
    fake_cache = tmp_path / "prepare-cache"
    fake_ledger = tmp_path / "ledger-dir"
    for d in (fake_cache, fake_ledger):
        d.mkdir()
        (d / "x.json").write_text("{}")
        (d / setup.WRITTEN_MANIFEST).write_text("x.json\n")
    monkeypatch.setattr(setup, "IN_REPO_LEFTOVERS", (fake_cache, fake_ledger))
    skills = Path(os.environ["HOME"]) / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "super-jev").symlink_to(SKILL)
    (skills / "other").symlink_to(tmp_path)
    assert setup.main(["--uninstall"]) == 0
    assert not setup.state_root().exists()
    assert not fake_cache.exists() and not fake_ledger.exists()
    assert not (skills / "super-jev").exists()
    assert (skills / "other").is_symlink()  # not ours, left alone


def test_memory_before_setup_names_the_setup_command(env):
    r = subprocess.run([sys.executable, str(SKILL / "dispatch.py"), "memory", "--input", "/dev/stdin"],
                       input='{"action": "panel", "principal": "me"}', capture_output=True, text=True)
    body = json.loads(r.stdout)
    assert body["reason"] == "not-set-up" and "setup.py" in body["message"]


# ------------------------------------------------ ask on nothing connected

def test_ask_with_nothing_connected_says_run_connect(env, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "ok", "pointers": []}
                        if req["action"] == "panel" else {"status": "miss"})
    assert ask.lookup("where is it?", "me", env / "state" / "me") == 1
    out = capsys.readouterr().out
    assert "nothing connected yet" in out and "prepare_bulk.py" in out and "--principal me" in out


def test_ask_before_setup_says_run_setup(env, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "error", "reason": "not-set-up"})
    assert ask.lookup("where is it?", "me", env / "state" / "me") == 1
    assert "setup.py" in capsys.readouterr().out


# ------------------------------------------------ built-in writer

def test_builtin_writer_quotes_the_file_and_needs_no_model(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("# Dentist visit\n\n## Follow-up\n\nCleaning booked for March.\n")
    got = prepare_bulk.builtin_writer([prepare_bulk.excerpt(f)])[str(f)]
    assert got["description"] == ('This file is titled "Dentist visit" with sections "Follow-up". '
                                  'It begins: "Cleaning booked for March."')
    assert got["kind"] == "unknown" and got["question"]
