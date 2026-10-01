#!/usr/bin/env python3
"""Frozen tests (2026-10-01), PR 2b: a missing registry means nothing is connected.

Setup makes the memory config but not registry.json: that file appears with the first connect. So a
new user who has run setup and connected nothing used to see `--status` fail with "Connection status
unavailable" (exit 1, the connector's FileNotFoundError), while an ask on the same folder said
"nothing connected yet". One rule now, where the registry is read: no registry file, no datasets,
no pointers. Setup grows no second path for it.

These run the real engine (setup.py, ask.py, the memory connector) in a temp state folder, with no
key that matters, no network and no Jev call. Made-up user "sam".

    python3 -m pytest skills/super-jev/tests/test_status_no_registry.py -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent


def engine(env, *argv, stdin=None):
    r = subprocess.run([sys.executable, *map(str, argv)], env=env, input=stdin,
                       capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


@pytest.fixture
def fresh(tmp_path):
    """A new user's Mac: an empty home and an empty state folder. Returns (state folder, env)."""
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    env = {k: v for k, v in os.environ.items() if k not in ("SUPERJEV_REPO", "SUPERJEV_PRINCIPAL")}
    env.update(HOME=str(home), SUPERJEV_STATE_DIR=str(state), SUPERJEV_SKILLS="0",
               TYPESAFE_API_KEY="example-not-a-real-key")
    return state, env


@pytest.fixture
def set_up(fresh):
    """Setup has run and nothing is connected: the state a first launch reaches."""
    state, env = fresh
    engine(env, SKILL / "setup.py")
    assert (state / "_memory" / "config.json").is_file()
    assert not (state / "_memory" / "registry.json").exists(), "setup must not grow a registry path"
    return state, env


def test_status_after_setup_with_nothing_connected_is_ready_and_says_connect(set_up):
    _, env = set_up
    rc, out, _ = engine(env, SKILL / "ask.py", "--principal", "sam", "--status")
    assert rc == 0
    assert "Nothing connected for sam" in out
    assert "Next:" in out and "connect" in out
    assert "unavailable" not in out and "FileNotFoundError" not in out


def test_status_json_after_setup_has_no_sets_and_next_connect(set_up):
    _, env = set_up
    rc, out, _ = engine(env, SKILL / "ask.py", "--principal", "sam", "--json", "--status")
    obj = json.loads(out)
    assert rc == 0
    assert obj["next"] == "connect" and obj["sets"] == []
    assert obj.get("outcome") != "error" and "why" not in obj


def test_the_memory_connector_reads_a_missing_registry_as_no_pointers(set_up):
    """The rule lives in the connector, so every reader of the panel (status, preflight, ask) agrees."""
    _, env = set_up
    rc, out, _ = engine(env, SKILL / "dispatch.py", "memory", "--input", "/dev/stdin",
                        stdin=json.dumps({"action": "panel", "principal": "sam"}))
    panel = json.loads(out)
    assert rc == 0 and panel["status"] == "ok" and panel["pointers"] == []
    assert "reason" not in panel


def test_preflight_names_nothing_connected_not_a_broken_connector(set_up):
    _, env = set_up
    _, out, _ = engine(env, SKILL / "ask.py", "--principal", "sam", "--preflight")
    assert "nothing connected" in out
    assert "unavailable" not in out


def test_no_config_at_all_is_still_not_set_up(fresh):
    """The rule is about a missing registry only: a user who never ran setup is still told so."""
    _, env = fresh
    rc, out, _ = engine(env, SKILL / "ask.py", "--principal", "sam", "--status")
    assert rc == 1
    assert "Not set up" in out and "setup.py" in out
