#!/usr/bin/env python3
"""A refresh storm is cut at both ends (made-up principal and pointers, temp state, no real refresh):
  (A) prepare_bulk holds a per-pointer lock; a second run for the same pointer says so and exits 0
      without touching the cache;
  (B) auto_heal.scan asks rc.new_files once per report owner, not once per split part.

    python3 -m pytest skills/super-jev/tests/test_heal_lock.py -q
"""
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent


def test_a_second_refresh_for_a_locked_pointer_skips(tmp_path):
    cache = tmp_path / "prepare-cache"; cache.mkdir()
    report = cache / "xset-report.json"
    report.write_text("{}")
    os.utime(report, (1_000_000, 1_000_000))
    env = {**os.environ, "SUPERJEV_STATE_DIR": str(tmp_path)}
    with open(cache / ".xset.prepare-lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = subprocess.run([sys.executable, str(SKILL / "prepare_bulk.py"), "--refresh", "--pointer", "xset"],
                           env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "refresh already running for xset; skipping" in r.stdout
    assert report.stat().st_mtime == 1_000_000
    assert sorted(p.name for p in cache.iterdir()) == [".xset.prepare-lock", "xset-report.json"]


def test_b_scan_walks_each_owner_once(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("auto_heal_lock_t", SKILL / "auto_heal.py")
    ah = importlib.util.module_from_spec(spec); spec.loader.exec_module(ah)
    cache = tmp_path / "cache"; cache.mkdir()
    monkeypatch.setattr(ah, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(ah, "LOG_PATH", tmp_path / "state" / "autoheal.log")
    (cache / "p-report.json").write_text(json.dumps(
        {"pointer": "p", "roots": [str(tmp_path)], "approved": [], "excludes": [], "principals": ["tester"],
         "parts": [{"pointer": "p-2"}, {"pointer": "p-3"}]}))
    calls = []
    monkeypatch.setattr(ah.rc, "new_files", lambda *a, **k: calls.append(1) or [])
    assert ah.scan("tester", ["p", "p-2", "p-3"], cache_dir=cache) == {}
    assert len(calls) == 1


def _locked_run(tmp_path, *args):
    cache = tmp_path / "prepare-cache"; cache.mkdir()
    env = {**os.environ, "SUPERJEV_STATE_DIR": str(tmp_path)}
    with open(cache / ".xset.prepare-lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return subprocess.run([sys.executable, str(SKILL / "prepare_bulk.py"), "--pointer", "xset", *args],
                              env=env, capture_output=True, text=True, timeout=60)


def test_json_skip_is_one_object(tmp_path):
    r = _locked_run(tmp_path, "--refresh", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout) == {"skipped": True, "pointer": "xset", "reason": "refresh-running"}


def test_watch_is_not_skipped_by_the_lock(tmp_path):
    r = _locked_run(tmp_path, "--watch")
    assert "already running" not in r.stdout + r.stderr
