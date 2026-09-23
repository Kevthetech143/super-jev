#!/usr/bin/env python3
"""Offline tests for the rc1 hardening round 5 fixes: check never sends an evidence
file holding a secret, more token shapes, a linear-time generic scan, uninstall keeps
a user's files inside the in-repo folders, and the 0.85 confirm floor.

No network and no real key. Token-shaped strings are built by concatenation so
this file itself never holds one.

    python3 -m pytest skills/super-jev/tests/test_hardening_round5.py -q
"""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(SKILL))
ask = load("ask_r5", SKILL / "ask.py")
pb = load("prepare_bulk_r5", SKILL / "prepare_bulk.py")
jc = load("jev_client_r5", SKILL / "lib" / "jev_client.py")
setup = load("setup_r5", SKILL / "setup.py")


# 1. check refuses an evidence file holding a secret and never calls Jev
def test_check_refuses_evidence_with_a_secret(tmp_path, monkeypatch, capsys):
    f = tmp_path / "notes.md"
    f.write_text("The rate is 4%.\nstripe " + "sk_" + "live_" + "abcdefghij1234567890\n")
    monkeypatch.setattr(jc, "check", lambda *a, **k: pytest.fail("evidence was sent"))
    assert jc.main([str(f), "--claim", "The rate is 4%"]) == 1
    assert f"evidence file {f} contains a secret; not sent" in capsys.readouterr().err


# 2. new token shapes are held; 1Password prose is not
@pytest.mark.parametrize("text", [
    "AI" + "za" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r",
    "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0.sig",
    "123456789:" + "AA" + "Fq8Zr2LmX7vKp4TnB9wYc3HdJ6sFa1Ge5",
    "SG" + ".q8Zr2LmX7vKp4TnB9wYc3H.dJ6sFa1Ge5q8Zr2LmX7vKp4TnB9",
    "hf" + "_q8Zr2LmX7vKp4TnB9wYc3HdJ6sFa1Ge5",
    "MT" + "A1q8Zr2LmX7vKp4TnB9wYc3H.GhIjKl.dJ6sFa1Ge5q8Zr2LmX7vKp4TnB9",
    "Authorization: " + "Basic dXNlcjpwYXNzd29yZA==",
])
def test_new_token_shapes_are_held(text):
    assert pb.has_secret(text)


def test_1password_prose_is_not_held_but_a_password_is():
    assert not pb.has_secret("my API token is in 1Password")
    assert pb.has_secret("password: hunter2")


@pytest.mark.parametrize("line", ["key" * 150000, "key_" * 112500, "a" + "-key" * 112000 + "=",
                                  "secret-" * 64000 + ":"])
def test_pathological_line_scans_quickly(line):
    t = time.monotonic()
    pb.has_secret(line)
    assert time.monotonic() - t < 0.3


# 3. uninstall deletes only files Super Jev writes in its in-repo folders
def test_uninstall_leaves_a_user_folder_in_prepare_cache(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    cache, ledger = tmp_path / "prepare-cache", tmp_path / "ledger"
    (cache / "mine").mkdir(parents=True)
    (cache / "mine" / "keep.txt").write_text("user file")
    (cache / "notes.json").write_text("{}")
    (cache / "notes-held.txt").write_text("x")
    (cache / setup.WRITTEN_MANIFEST).write_text("notes.json\nnotes-held.txt\n")
    ledger.mkdir()
    (ledger / "calls.jsonl").write_text("{}\n")
    monkeypatch.setattr(setup, "IN_REPO_LEFTOVERS", (cache, ledger))
    assert setup.main(["--uninstall"]) == 0
    assert (cache / "mine" / "keep.txt").read_text() == "user file"
    assert not (cache / "notes.json").exists() and not (cache / "notes-held.txt").exists()
    assert not ledger.exists()
    out = capsys.readouterr().out
    assert "left in place" in out and str(cache / "mine") in out


# 4. the confirm floor is 0.85: a 0.83 near miss is not confirmed (the lookup may
# still show it as "possible", see test_retrieval_recall.py), a 0.90 hit is
@pytest.mark.parametrize("score,confirmed", [(0.83, False), (0.90, True)])
def test_confirm_floor_is_085(tmp_path, monkeypatch, score, confirmed):
    f = tmp_path / "garden.md"
    f.write_text("Fertilized once in May.")
    monkeypatch.setattr(ask.subprocess, "run", lambda cmd, input, **kw: subprocess.CompletedProcess(
        cmd, 0, json.dumps({"status": "candidates", "candidates": [{"score": score}]}), ""))
    assert (ask.confirm_one("What date did I fertilize in May?", str(f))[0] >= ask.CONFIRM_FLOOR) is confirmed
