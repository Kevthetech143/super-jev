#!/usr/bin/env python3
"""Offline tests for the rc1 hardening round 2 fixes: safe uninstall, binary
files, empty/all-failed connect exit codes, clear check/ask error causes, long
questions and the ask content check.

No network and no real key.

    python3 -m pytest skills/super-jev/tests/test_hardening_round2.py -q
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


setup = load("setup_r2", SKILL / "setup.py")
sys.path.insert(0, str(SKILL))
ask = load("ask_r2", SKILL / "ask.py")
pb = load("prepare_bulk_r2", SKILL / "prepare_bulk.py")
cc = load("connect_checked_r2", SKILL / "connect_checked.py")
sj = load("superjev_r2", SKILL / "superjev.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    monkeypatch.setattr(setup, "IN_REPO_LEFTOVERS", [])
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    return tmp_path


# 1. uninstall never deletes a folder setup did not make
def test_uninstall_refuses_a_state_dir_without_setups_marker(env, capsys):
    state = env / "state"
    state.mkdir()
    (state / "my-thesis.md").write_text("user file")
    assert setup.main(["--uninstall"]) == 1
    assert (state / "my-thesis.md").read_text() == "user file"
    assert "REFUSED" in capsys.readouterr().out


def test_uninstall_removes_a_state_dir_setup_made(env):
    assert setup.main([]) == 0
    assert setup.main(["--uninstall"]) == 0
    assert not (env / "state").exists()


# 2. binary / non-UTF-8 files are held; good files still go through
def test_binary_and_non_utf8_files_are_held_good_file_kept(tmp_path):
    (tmp_path / "good.md").write_text("# Good\ntext\n")
    (tmp_path / "bin.md").write_bytes(b"# x\x00\x01\x02")
    (tmp_path / "latin.md").write_bytes("# caf\xe9\n".encode("latin-1"))
    files, held = pb.inventory([tmp_path])
    assert [p.name for p in files] == ["good.md"]
    why = {Path(p).name: w for p, w in held}
    assert "binary" in why["bin.md"] and "UTF-8" in why["latin.md"]


def test_gate_turns_a_null_byte_into_one_file_error_not_a_crash():
    v = cc.gate("bad\x00description", "/nonexistent.md")
    assert v["state"] == "ERROR" and "null byte" in v["reason"]


# 5. connect exits non-zero when nothing can connect
def test_connect_empty_folder_exits_nonzero(env, monkeypatch, capsys):
    root = env / "empty"
    root.mkdir()
    monkeypatch.setattr(sys, "argv", ["p", "--root", str(root), "--pointer", "x", "--principal", "me",
                                      "--writer", "builtin"])
    assert pb.main() == 1
    assert "no .md files found" in capsys.readouterr().out


def test_connect_all_files_failed_exits_nonzero_with_cause(env, monkeypatch, capsys):
    root = env / "r"
    root.mkdir()
    (root / "a.md").write_text("# A\nalpha\n")
    (root / "b.md").write_text("# B\nbeta\n")
    cause = "ERROR — Jev could not check this: 401 -- TypeSafe rejected the API key."
    monkeypatch.setattr(pb, "gate", lambda d, p: {"state": "ERROR", "reason": cause})
    monkeypatch.setattr(pb, "memory", lambda req: pytest.fail("must not connect"))
    # a model writer's drafts go through the gate (built-in quotes are checked locally)
    monkeypatch.setattr(pb, "writer", lambda items, model, feedback=None, command=None:
                        {it["path"]: {"path": it["path"], "description": "d"} for it in items})
    monkeypatch.setattr(sys, "argv", ["p", "--root", str(root), "--pointer", "x", "--principal", "me",
                                      "--writer-command", "fake-writer", "--no-findability"])
    assert pb.main() == 1
    out = capsys.readouterr().out
    assert "all 2 files failed" in out and "401" in out


# 6. check names the real cause and stays fail-closed
@pytest.mark.parametrize("line", ["jev: 401 -- TypeSafe rejected the API key",
                                  "jev: TYPESAFE_API_KEY is not set -- export it first",
                                  "jev: could not reach TypeSafe: URLError"])
def test_gate_verdict_names_the_cause(line):
    code = sj.gate_fail_closed(0, "", 1)
    assert code == sj.GATE_UNREADABLE_EXIT
    text = sj.gate_verdict_line(code, "", line + "\n")
    assert line[4:].strip() in text and "NOT clean" in text


def test_gate_verdict_without_a_cause_keeps_the_generic_line():
    assert sj.gate_verdict_line(1, "", "") == sj.GATE_VERDICT[1]
    assert sj.gate_verdict_line(0) == sj.GATE_VERDICT[0]


# 4. very long question
def test_long_question_gets_a_clear_message(env, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", lambda req: pytest.fail("no call for a too-long question"))
    assert ask.lookup("x" * 11_600, "me", env / "state" / "me") == 2
    assert "question too long (11,600 chars, max 8,000)" in capsys.readouterr().out


def _panel_nav(nav_out):
    def fake(req):
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        return nav_out
    return fake


# 7. navigation error carries its reason
def test_ask_error_line_names_the_reason(env, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", _panel_nav(
        {"status": "error", "reason": "Navigation provider failed: could not reach TypeSafe (network)"}))
    assert ask.lookup("q?", "me", env / "state" / "me") == 1
    assert "[p1] error: Navigation provider failed: could not reach TypeSafe (network)" in capsys.readouterr().out


# 3. content check: a topical file without the fact is dropped; one with it is kept
def _nav_with(tmp_path, monkeypatch):
    f = tmp_path / "vet.md"
    f.write_text("# Pet care\nBiscuit sees Dr. Chen.\n")
    monkeypatch.setattr(ask, "memory", _panel_nav(
        {"status": "candidates", "candidates": [{"score": 0.8, "originalPath": str(f)}]}))
    return f


def test_content_check_drops_a_topic_only_match(env, monkeypatch, capsys):
    _nav_with(env, monkeypatch)
    monkeypatch.setattr(ask, "confirm", lambda q, paths: ({}, set(), None, {}))
    assert ask.lookup("collar color?", "me", env / "state" / "me") == 0
    out = capsys.readouterr().out
    assert "no-candidates" in out and "did not contain the answer" in out


def test_content_check_keeps_a_file_with_the_fact(env, monkeypatch, capsys):
    f = _nav_with(env, monkeypatch)
    monkeypatch.setattr(ask, "confirm", lambda q, paths: ({str(f): 0.97}, set(), None, {}))
    assert ask.lookup("vet?", "me", env / "state" / "me") == 0
    assert f" 0.97  {f}  [p1]" in capsys.readouterr().out


def test_content_check_failure_is_an_error_with_reason(env, monkeypatch, capsys):
    _nav_with(env, monkeypatch)
    monkeypatch.setattr(ask, "confirm", lambda q, paths: ({}, set(), "Jev HTTP 401", {}))
    assert ask.lookup("vet?", "me", env / "state" / "me") == 1
    assert "[content-check] error: Jev HTTP 401" in capsys.readouterr().out


def test_confirm_checks_each_file_alone_and_applies_the_floor(tmp_path, monkeypatch):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("alpha")
    b.write_text("beta")
    seen = []

    def fake_run(cmd, input, **kw):
        payload = json.loads(input)
        leaves = payload["catalog"]["nodes"][1:]
        seen.append([leaf["description"] for leaf in leaves])
        score = 0.9 if leaves[0]["description"] == "alpha" else 0.05
        body = {"status": "candidates", "candidates": [{"sourceId": "0", "score": score}]}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(body), "")

    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    scores, partial, err, _ = ask.confirm("q", [str(a), str(b)])
    assert err is None and scores == {str(a): 0.9} and not partial
    assert sorted(seen) == [["alpha"], ["beta"]]  # one catalog per file


def test_ask_ignores_near_zero_routing_tail(env, monkeypatch, capsys):
    f = _nav_with(env, monkeypatch)
    junk = env / "junk.md"
    junk.write_text("x")
    monkeypatch.setattr(ask, "memory", _panel_nav({"status": "candidates", "candidates": [
        {"score": 0.8, "originalPath": str(f)}, {"score": 5e-324, "originalPath": str(junk)}]}))
    checked = []
    monkeypatch.setattr(ask, "confirm", lambda q, paths: checked.extend(paths) or ({str(f): 0.9}, set(), None, {}))
    assert ask.lookup("vet?", "me", env / "state" / "me") == 0
    assert checked == [str(f)] and "junk.md" not in capsys.readouterr().out


# 8. --claims-file expands ~
def test_claims_file_expands_home(tmp_path, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    (home / "claims.txt").write_text("claim one\n")
    (home / "ev.md").write_text("claim one\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    r = subprocess.run([sys.executable, str(SKILL / "lib" / "jev_client.py"), "~/ev.md", "--kit", "reply",
                        "--claims-file", "~/claims.txt"], capture_output=True, text=True,
                       env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
    assert "No such file" not in r.stderr + r.stdout
    assert "TYPESAFE_API_KEY is not set" in r.stdout + r.stderr


# 9. size hold says how to split
def test_oversize_hold_says_how_to_split(tmp_path, monkeypatch):
    (tmp_path / "big.md").write_text("x" * 100)
    monkeypatch.setattr(pb, "CEILING_BYTES", 50)
    _, held = pb.inventory([tmp_path])
    assert "split it into smaller .md files" in held[0][1] and "100 bytes" in held[0][1]
