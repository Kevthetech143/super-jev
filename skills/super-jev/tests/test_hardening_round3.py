#!/usr/bin/env python3
"""Offline tests for the rc1 hardening round 3 fixes: setup/uninstall never
claim a user's folder, the content check's secret scan, absent facts,
held-file guidance, stale-pointer hints and odd content-check output.

No network and no real key.

    python3 -m pytest skills/super-jev/tests/test_hardening_round3.py -q
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


setup = load("setup_r3", SKILL / "setup.py")
sys.path.insert(0, str(SKILL))
ask = load("ask_r3", SKILL / "ask.py")
pb = load("prepare_bulk_r3", SKILL / "prepare_bulk.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    monkeypatch.setattr(setup, "IN_REPO_LEFTOVERS", ())
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    return tmp_path


# 1. setup never plants its marker in a user's folder; uninstall deletes only its own things
def test_setup_refuses_a_non_empty_folder_it_did_not_make(env, capsys):
    state = env / "state"
    state.mkdir()
    (state / "taxes.txt").write_text("x")
    assert setup.main([]) == 1
    assert "REFUSED" in capsys.readouterr().out
    assert not (state / "_memory").exists()
    assert setup.main(["--uninstall"]) == 1
    assert (state / "taxes.txt").read_text() == "x"


def test_setup_accepts_an_empty_existing_folder(env):
    (env / "state").mkdir()
    assert setup.main([]) == 0


def test_uninstall_keeps_user_files_and_the_folder_holding_them(env, capsys):
    assert setup.main([]) == 0
    state = env / "state"
    (state / "me" / "manual").mkdir(parents=True)
    (state / "me" / "lookups.jsonl").write_text("{}\n")
    (state / "taxes.txt").write_text("x")
    (state / "mine").mkdir()
    (state / "mine" / "notes.md").write_text("y")
    assert setup.main(["--uninstall"]) == 0
    assert not (state / "_memory").exists() and not (state / "me").exists()
    assert (state / "taxes.txt").read_text() == "x"
    assert (state / "mine" / "notes.md").read_text() == "y"
    assert "left in place" in capsys.readouterr().out


def test_uninstall_removes_the_folder_when_only_its_own_things_were_there(env):
    assert setup.main([]) == 0
    (env / "state" / "me").mkdir()
    (env / "state" / "me" / "lookups.jsonl").write_text("{}\n")
    assert setup.main(["--uninstall"]) == 0
    assert not (env / "state").exists()


# 2/3/6. the content check: secret scan, a real bar, odd output
def _fake_nav(monkeypatch, body, calls=None):
    def fake_run(cmd, input, **kw):
        if calls is not None:
            calls.append(input)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(body), "")
    monkeypatch.setattr(ask.subprocess, "run", fake_run)


def test_content_check_never_sends_a_file_that_now_holds_a_secret(tmp_path, monkeypatch):
    f = tmp_path / "notes.md"
    f.write_text("vet is Dr Lee\npassword: hunter2\n")
    calls = []
    _fake_nav(monkeypatch, {"status": "candidates", "candidates": [{"score": 0.99}]}, calls)
    scores, partial, err, notes = ask.confirm("vet?", [str(f)])
    assert calls == [] and scores == {} and err is None
    assert notes == {str(f): ask.HELD_SECRET}


def test_held_file_is_named_in_ask_output(tmp_path, monkeypatch, capsys):
    f = tmp_path / "notes.md"
    f.write_text("x")
    monkeypatch.setattr(ask, "memory", lambda req: {"pointers": ["p1"]} if req["action"] == "panel" else
                        {"status": "candidates", "candidates": [{"score": 0.8, "originalPath": str(f)}]})
    monkeypatch.setattr(ask, "confirm", lambda q, paths: ({}, set(), None, {str(f): ask.HELD_SECRET}))
    assert ask.lookup("vet?", "me", tmp_path / "s") == 0
    out = capsys.readouterr().out
    assert f"HELD  {f}  (contains a secret; not sent)" in out and "did not contain" not in out


@pytest.mark.parametrize("score", [0.51, 0.56])
def test_a_near_tie_with_none_is_not_a_match(tmp_path, monkeypatch, score):
    f = tmp_path / "dentist.md"
    f.write_text("Dr. Alvarez cleaned Maria's teeth on 2026-03-04.")
    _fake_nav(monkeypatch, {"status": "candidates", "candidates": [{"score": score}]})
    assert ask.confirm_one("How much did the cleaning cost?", str(f))[0] is None


def test_content_check_asks_for_the_answer_not_the_topic(tmp_path, monkeypatch):
    f = tmp_path / "a.md"
    f.write_text("x")
    calls = []
    _fake_nav(monkeypatch, {"status": "candidates", "candidates": [{"score": 0.93}]}, calls)
    assert ask.confirm_one("What car do I have?", str(f))[0] == 0.93
    assert "states the exact value asked for" in json.loads(calls[0])["catalog"]["nodes"][1]["label"]


def test_files_past_the_checked_few_are_not_kept_unread(tmp_path, monkeypatch, capsys):
    paths = []
    for i in range(ask.CONFIRM_FILES + 1):
        paths.append(tmp_path / f"f{i}.md")
        paths[-1].write_text("x")
    cands = [{"score": 0.9 - i / 100, "originalPath": str(p)} for i, p in enumerate(paths)]
    monkeypatch.setattr(ask, "memory", lambda req: {"pointers": ["p1"]} if req["action"] == "panel" else
                        {"status": "candidates", "candidates": cands})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {}))
    ask.lookup("absent?", "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert "no-candidates" in out and "f5.md" not in out


def test_odd_candidate_does_not_crash(tmp_path, monkeypatch):
    f = tmp_path / "a.md"
    f.write_text("x")
    _fake_nav(monkeypatch, {"status": "candidates", "candidates": [{"sourceId": "0"}, "junk", {"score": 0.95}]})
    assert ask.confirm_one("q", str(f))[0] == 0.95


def test_budget_exhausted_is_inconclusive_and_keeps_the_file(tmp_path, monkeypatch, capsys):
    f = tmp_path / "a.md"
    f.write_text("x")
    _fake_nav(monkeypatch, {"status": "budget-exhausted", "candidates": []})
    assert ask.confirm_one("q", str(f))[3] == ask.INCONCLUSIVE
    monkeypatch.setattr(ask, "memory", lambda req: {"pointers": ["p1"]} if req["action"] == "panel" else
                        {"status": "candidates", "candidates": [{"score": 0.8, "originalPath": str(f)}]})
    assert ask.lookup("q", "me", tmp_path / "s") == 0
    out = capsys.readouterr().out
    assert str(f) in out and "inconclusive" in out and "no-candidates" not in out


# 4. the built-in writer connects plain notes; set-aside files come with a way back in
def test_builtin_small_note_connects_without_a_judge_call(env, monkeypatch, capsys):
    root = env / "r"
    root.mkdir()
    (root / "coffee.md").write_text("# Coffee\n\n## Beans\nBlue Door, Ethiopia.\n\n## Brew\nV60, 1:16.\n")
    monkeypatch.setattr(pb, "gate", lambda d, p: pytest.fail("quoted description needs no judge"))
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": True})
    monkeypatch.setattr(sys, "argv", ["p", "--root", str(root), "--pointer", "x", "--principal", "me",
                                      "--writer", "builtin", "--no-findability"])
    assert pb.main() == 0
    out = capsys.readouterr().out
    assert "PASS quoted" in out and "approved: 1  exceptions: 0" in out


def test_summary_names_held_file_why_and_command(env, monkeypatch, capsys):
    root = env / "r"
    root.mkdir()
    (root / "ok.md").write_text("# Ok\nfine\n")
    (root / "keys.md").write_text("# Keys\npassword: hunter2\n")
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": True})
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--root", str(root), "--pointer", "x",
                                      "--principal", "me", "--writer", "builtin", "--no-findability"])
    assert pb.main() == 0
    summary = capsys.readouterr().out.split("approved:")[1]
    assert "HELD  keys.md  (card/password-like text" in summary
    assert f"prepare_bulk.py --root {root} --pointer x --principal me --writer builtin --no-findability --allow-held" in summary


# 5. a stale pointer says how to refresh it -- a command that runs as printed
def test_stale_pointer_line_names_the_refresh_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", lambda req: {"pointers": ["x"]} if req["action"] == "panel" else
                        {"status": "preparation-required"})
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", tmp_path)
    (tmp_path / "x-report.json").write_text(json.dumps(
        {"pointer": "x", "roots": ["/r"], "principals": ["me", "helper"]}))
    assert ask.lookup("q", "me", tmp_path / "s") == 1
    out = capsys.readouterr().out
    script = Path(ask.__file__).resolve().parent / "prepare_bulk.py"
    assert (f"[x] preparation-required; its files changed since connect. Run: python3 {script} "
            "--root /r --pointer x --principal me --principal helper --refresh") in out


def test_stale_pointer_without_report_says_root_is_needed(tmp_path, monkeypatch, capsys):
    """v1.0.12 printed a bare --refresh that prepare_bulk refused (needs --root)."""
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", tmp_path)
    assert "--root DIR --pointer x --principal me --refresh" in ask.refresh_hint("x", "me", "preparation-required")
