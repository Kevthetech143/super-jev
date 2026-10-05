#!/usr/bin/env python3
"""A queued heal left pending with no drain to heal it is reclaimed by the next ask, even when the
ask's own set only cools down (made-up principals and sets; Popen is faked, so no drain runs).

    python3 -m pytest skills/super-jev/tests/test_auto_heal_reclaim.py -q
"""
import json
import os
import time

from test_auto_heal import _add_pointer, _setup, ah


def _orphan(tmp_path, monkeypatch, kind="refresh"):
    """`stuck` is queued and due; `asked` (the set this ask is about) is cooling down; no lock."""
    calls, cache_dir = _setup(tmp_path, monkeypatch, name="asked")
    _add_pointer(cache_dir, "stuck")
    ah._queue("agent", "stuck", kind)
    ah._mark("agent", "asked", time.time())
    return calls


def _drains(calls):
    return [c for c in calls if "--drain" in c]


def _reclaims():
    if not ah.LOG_PATH.is_file():
        return []
    return [e for e in map(json.loads, ah.LOG_PATH.read_text().splitlines()) if e.get("action") == "reclaim"]


def test_an_ask_for_a_cooling_set_starts_one_drain_for_a_due_queued_set(tmp_path, monkeypatch):
    calls = _orphan(tmp_path, monkeypatch)
    assert ah.heal_in_background("asked", "agent") == "cooldown"  # the ask's own answer is unchanged
    assert len(calls) == 1 and calls[0][2:5] == ["--drain", "agent", "stuck"]
    assert ah._lock_path("agent", "stuck").is_file()  # the drain runs under the set's lock
    assert [(e["pointer"], e["result"]) for e in _reclaims()] == [("stuck", "started")]
    # The lock that drain holds keeps the next ask from starting a second one.
    assert ah.heal_in_background("asked", "agent") == "cooldown"
    assert len(calls) == 1


def test_an_ask_with_nothing_pending_starts_no_drain(tmp_path, monkeypatch):
    calls, _ = _setup(tmp_path, monkeypatch, name="asked")
    ah._mark("agent", "asked", time.time())
    assert ah.heal_in_background("asked", "agent") == "cooldown"
    assert calls == [] and _reclaims() == []


def test_a_queued_set_still_cooling_starts_no_drain(tmp_path, monkeypatch):
    calls = _orphan(tmp_path, monkeypatch)
    with ah._state_txn("agent") as state:
        state["pointers"]["stuck"] = time.time()
    assert ah.heal_in_background("asked", "agent") == "cooldown"
    assert calls == []


def test_a_refresh_over_the_hourly_cap_is_not_reclaimed(tmp_path, monkeypatch):
    calls = _orphan(tmp_path, monkeypatch)
    with ah._state_txn("agent") as state:
        state["attempts"] = [time.time()] * ah.MAX_PER_HOUR
    ah.heal_in_background("asked", "agent")
    assert calls == []


def test_a_live_lock_holder_is_left_to_drain_it(tmp_path, monkeypatch):
    calls = _orphan(tmp_path, monkeypatch)
    ah._lock_path("agent", "other").write_text(
        json.dumps({"pid": os.getpid(), "ts": time.time(), "token": "live", "drain": True}))
    ah.heal_in_background("asked", "agent")
    assert calls == []


def test_another_principals_queue_is_not_picked_up(tmp_path, monkeypatch):
    calls = _orphan(tmp_path, monkeypatch)
    ah._mark("someone", "asked", time.time())
    assert ah.heal_in_background("asked", "someone") == "cooldown"
    assert calls == []
    assert "stuck" in ah._load_state("agent")["pending"]


def test_a_drain_that_cannot_start_waits_its_retry_timer(tmp_path, monkeypatch):
    _orphan(tmp_path, monkeypatch)

    def no_spawn(cmd, **kw):
        raise OSError("no spawn")
    monkeypatch.setattr(ah.subprocess, "Popen", no_spawn)
    ah.heal_in_background("asked", "agent")
    assert not ah._lock_path("agent", "stuck").exists()
    assert ah._wait_secs(ah._load_state("agent"), "stuck", time.time()) > 0
    ah.heal_in_background("asked", "agent")  # not picked again while its retry timer runs
    assert [e["result"] for e in _reclaims()] == ["failed"]


def _kill_drain():
    """The started drain died: its lock names a gone pid (and is too old to be a live holder)."""
    ah._lock_path("agent", "stuck").write_text(
        json.dumps({"pid": 999999, "ts": time.time() - ah.LOCK_STALE_SECS - 1, "token": "gone", "drain": True}))


def test_a_drain_that_dies_is_not_respawned_within_retry_secs(tmp_path, monkeypatch):
    calls = _orphan(tmp_path, monkeypatch)
    ah.heal_in_background("asked", "agent")
    assert len(_drains(calls)) == 1
    _kill_drain()
    ah.heal_in_background("asked", "agent")
    assert len(_drains(calls)) == 1 and len(_reclaims()) == 1


def test_a_dead_drain_is_reclaimed_again_after_retry_secs(tmp_path, monkeypatch):
    calls = _orphan(tmp_path, monkeypatch)
    ah.heal_in_background("asked", "agent")
    _kill_drain()
    with ah._state_txn("agent") as state:
        state["reclaimed"] = time.time() - ah.RETRY_SECS - 1
    ah.heal_in_background("asked", "agent")
    assert [c[2:5] for c in _drains(calls)] == [["--drain", "agent", "stuck"]] * 2
    assert "gone" not in ah._lock_path("agent", "stuck").read_text()  # the dead holder's lock was taken over
