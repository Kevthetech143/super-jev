#!/usr/bin/env python3
"""Heal state survives a release (made-up principal and pointer, temp dirs, no real refresh). Two copies of
the skill dir stand in for two releases sharing one SUPERJEV_STATE_DIR:
  - a scan lock held by release A makes release B's scan for that principal skip;
  - a cooldown set by A is honoured by B;
  - the prepare lock held by A makes B's refresh of that pointer skip;
  - the first run after the change carries the old in-release cooldown file over once, never a lock.

    python3 -m pytest skills/super-jev/tests/test_heal_state_survives_release.py -q --basetemp=/tmp/hs-bt
"""
import fcntl
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent


@pytest.fixture
def releases(tmp_path):
    a, b = tmp_path / "rel-A", tmp_path / "rel-B"
    for d in (a, b):
        shutil.copytree(SKILL, d, ignore=shutil.ignore_patterns("tests", "__pycache__", "prepare-cache", "autoheal-state"))
    return a, b, tmp_path / "state"


def _run(rel, state, code, *argv):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(state.parent / "home"), "SUPERJEV_STATE_DIR": str(state)}
    return subprocess.run([sys.executable, "-c", code, *argv], cwd=str(rel), env=env, capture_output=True, text=True, timeout=60)


def test_scan_lock_held_by_one_release_blocks_the_other(releases):
    a, b, state = releases
    (state / "autoheal-state").mkdir(parents=True)
    with open(state / "autoheal-state" / ".tester.scan-lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)  # release A's running scan
        r = _run(b, state, "import auto_heal as ah; print(ah.scan_single_flight('tester', ['p']))")
    assert "scan already running for tester; skipping this one" in r.stdout, r.stdout + r.stderr


def test_cooldown_set_by_one_release_is_honoured_by_the_other(releases):
    a, b, state = releases
    r = _run(a, state, "import auto_heal as ah, time; ah._mark('tester', 'p', time.time())")
    assert r.returncode == 0, r.stderr
    r = _run(b, state, "import auto_heal as ah, time; print(ah._wait_secs(ah._load_state('tester'), 'p', time.time()) > 0)")
    assert r.stdout.strip() == "True", r.stdout + r.stderr


def test_prepare_lock_held_by_one_release_makes_the_other_skip(releases):
    a, b, state = releases
    (state / "locks").mkdir(parents=True)
    (state / "prepare-cache").mkdir()
    (state / "prepare-cache" / "xset-report.json").write_text("{}")
    with open(state / "locks" / ".xset.prepare-lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(state.parent / "home"), "SUPERJEV_STATE_DIR": str(state)}
        r = subprocess.run([sys.executable, str(b / "prepare_bulk.py"), "--refresh", "--pointer", "xset"],
                           env=env, capture_output=True, text=True, timeout=60)
    assert "refresh already running for xset; skipping" in r.stdout, r.stdout + r.stderr


def test_old_in_release_cooldowns_are_carried_over_once_without_locks(releases):
    a, b, state = releases
    old = b / "autoheal-state"
    old.mkdir()
    (old / "tester.json").write_text(json.dumps({"pointers": {"p": 123.0}, "attempts": []}))
    (old / "tester.p.lock").write_text("{}")
    (old / ".tester.scan-lock").write_text("")
    r = _run(b, state, "import auto_heal as ah; print(ah._load_state('tester')['pointers'])")
    assert r.stdout.strip() == "{'p': 123.0}", r.stdout + r.stderr
    assert sorted(p.name for p in (state / "autoheal-state").iterdir()) == ["tester.json"]
