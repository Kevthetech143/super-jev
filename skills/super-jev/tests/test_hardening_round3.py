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
    monkeypatch.setattr(setup, "IN_REPO_LEFTOVERS", [])
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
