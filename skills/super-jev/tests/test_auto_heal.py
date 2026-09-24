#!/usr/bin/env python3
"""Bounded background auto-heal: one refresh in flight per principal, a per-pointer cooldown,
an hourly cap, no blocking, no change to unchanged pointers. subprocess.Popen is faked, so
prepare_bulk.py never actually runs.

    python3 -m pytest skills/super-jev/tests/test_auto_heal.py -q
"""
import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("auto_heal_t", SKILL / "auto_heal.py")
ah = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ah)


def _setup(tmp_path, monkeypatch, name="moving", principal="agent", changed=True):
    root = tmp_path / "brain"; root.mkdir(exist_ok=True)
    cache_dir = tmp_path / "cache"; cache_dir.mkdir(exist_ok=True)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(ah, "STATE_DIR", state_dir)
    monkeypatch.setattr(ah, "LOG_PATH", state_dir / "autoheal.log")
    monkeypatch.setattr(ah.rc, "CACHE_DIR", cache_dir)
    f = root / f"{name}.md"
    f.write_text(f"# {name}\n")
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    (cache_dir / f"{name}.json").write_text(json.dumps(
        {} if changed else {str(f): {"sha256": sha, "pass": True}}))
    report = {"pointer": name, "roots": [str(root)], "approved": [str(f)],
              "excludes": [], "noRecurse": True, "principal": principal, "principals": [principal]}
    (cache_dir / f"{name}-report.json").write_text(json.dumps(report))
    calls = []
    monkeypatch.setattr(ah.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd) or object())
    return calls, cache_dir


def test_starts_a_background_refresh_for_a_changed_pointer(tmp_path, monkeypatch):
    calls, _ = _setup(tmp_path, monkeypatch)
    result = ah.maybe_heal("moving", "agent")
    assert result == "started"
    assert len(calls) == 1
    assert calls[0][:2] == ["/bin/sh", "-c"]  # a detached shell wrapper, not a blocking call
    log_lines = ah.LOG_PATH.read_text().splitlines()
    assert json.loads(log_lines[-1])["action"] == "started"


def test_never_touches_an_unchanged_pointer(tmp_path, monkeypatch):
    calls, _ = _setup(tmp_path, monkeypatch, changed=False)
    assert ah.maybe_heal("moving", "agent") == "no-change"
    assert calls == []


def test_missing_report_is_a_clean_skip(tmp_path, monkeypatch):
    calls, _ = _setup(tmp_path, monkeypatch)
    assert ah.maybe_heal("no-such-pointer", "agent") == "no-report"
    assert calls == []


def test_second_refresh_for_same_principal_is_blocked_while_one_is_in_flight(tmp_path, monkeypatch):
    calls, cache_dir = _setup(tmp_path, monkeypatch, name="moving")
    assert ah.maybe_heal("moving", "agent") == "started"
    # A second, different, also-changed pointer for the SAME principal must not also fire --
    # only one refresh in flight per principal.
    f2 = cache_dir.parent / "brain" / "other.md"
    f2.write_text("# other\n")
    (cache_dir / "other-report.json").write_text(json.dumps(
        {"pointer": "other", "roots": [str(f2.parent)], "approved": [str(f2)],
         "excludes": [], "noRecurse": True, "principal": "agent", "principals": ["agent"]}))
    (cache_dir / "other.json").write_text(json.dumps({}))
    assert ah.maybe_heal("other", "agent") == "in-progress"
    assert len(calls) == 1


def test_concurrent_lookups_for_the_same_stale_pointer_start_only_one_heal(tmp_path, monkeypatch):
    # Regression: _acquire_lock used to check lock.is_file() and then write_text() as two
    # separate steps, leaving a gap where several racing threads/processes could all see "no
    # lock" before any of them wrote one, each starting its own refresh. Real Popen is faked
    # (see _setup), but the lock file itself is real, so this exercises the actual race.
    import threading
    calls, _ = _setup(tmp_path, monkeypatch)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        r = ah.maybe_heal("moving", "agent")
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Exactly one racer wins the lock and starts a refresh; the rest must see either
    # "in-progress" (lock already held) or "cooldown" (state file already recorded the
    # attempt) -- never a second "started".
    assert results.count("started") == 1
    assert results.count("in-progress") + results.count("cooldown") == 7
    assert len(calls) == 1


def test_a_stale_lock_from_a_dead_pid_does_not_wedge_the_principal_forever(tmp_path, monkeypatch):
    calls, _ = _setup(tmp_path, monkeypatch)
    ah.STATE_DIR.mkdir(parents=True, exist_ok=True)
    # A pid that cannot possibly be alive, timestamped fresh -- must still be rejected as dead.
    ah._lock_path("agent").write_text(json.dumps({"pid": 999999999, "ts": time.time()}))
    assert ah.maybe_heal("moving", "agent") == "started"


def test_cooldown_blocks_an_immediate_retry_on_the_same_pointer(tmp_path, monkeypatch):
    calls, _ = _setup(tmp_path, monkeypatch)
    assert ah.maybe_heal("moving", "agent") == "started"
    ah._release_lock("agent")  # simulate the background refresh having finished
    assert ah.maybe_heal("moving", "agent") == "cooldown"
    assert len(calls) == 1


def test_cooldown_expired_allows_a_fresh_refresh(tmp_path, monkeypatch):
    calls, _ = _setup(tmp_path, monkeypatch)
    assert ah.maybe_heal("moving", "agent", cooldown_secs=0) == "started"
    ah._release_lock("agent")
    assert ah.maybe_heal("moving", "agent", cooldown_secs=0) == "started"
    assert len(calls) == 2


def test_hourly_cap_blocks_further_refreshes_once_reached(tmp_path, monkeypatch):
    calls, cache_dir = _setup(tmp_path, monkeypatch)
    for i in range(2):
        name = f"p{i}"
        f = cache_dir.parent / "brain" / f"{name}.md"
        f.write_text(f"# {name}\n")
        (cache_dir / f"{name}-report.json").write_text(json.dumps(
            {"pointer": name, "roots": [str(f.parent)], "approved": [str(f)],
             "excludes": [], "noRecurse": True, "principal": "agent", "principals": ["agent"]}))
        (cache_dir / f"{name}.json").write_text(json.dumps({}))
        assert ah.maybe_heal(name, "agent", cooldown_secs=0, max_per_hour=2) == "started"
        ah._release_lock("agent")
    assert ah.maybe_heal("moving", "agent", cooldown_secs=0, max_per_hour=2) == "rate-limited"
    assert len(calls) == 2


def test_stale_kind_detection():
    assert ah.is_stale_kind("preparation-required")
    assert ah.is_stale_kind("preparation-required: files changed")
    assert ah.is_stale_kind("refresh-required")
    assert not ah.is_stale_kind("error")
    assert not ah.is_stale_kind("no-candidates")
    assert not ah.is_stale_kind("")
