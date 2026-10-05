#!/usr/bin/env python3
"""An ask never reconnects a stale set itself (live 2026-10-05: 12 stale sets, 139 s). Its time must not
grow with the number of stale sets: a stale set is searched from its last prepared file list, or reported
as refreshing; the heal starts in the background. Made-up Quillbrook notes, fake slow reconnects, no network.

    python3 -m pytest skills/super-jev/tests/test_ask_no_inline_heal.py -q
"""
import subprocess
import time

import pytest

from test_ask_json import Q, ah, ask, clean, notes, run, world  # noqa: F401  (fixtures: clean is autouse)

SLOW = 0.4  # one fake reconnect's cost, when a caller waits for it


def stale_world(tmp_path, monkeypatch, notes, n, with_list):
    """n stale sets named s0..; each has a prepared file list when `with_list`. Returns the reconnect timeouts seen."""
    names = [f"s{i}" for i in range(n)]
    w = notes / "warranty.md"
    cache = {p: {str(w): {"pass": True, "sha256": ask.sha256_file(w), "description": "a note"}} for p in names} if with_list else {}
    rows = [{"pointer": p, "snapshotStatus": "refresh-required"} for p in names]
    world(tmp_path, monkeypatch, pointers=names, panel={"pointers": rows}, navigate=lambda p: {"status": "refresh-required" if not with_list else "no-candidates"},
          cache=cache, scores={str(w): 0.95}, lines={str(w): 4})
    seen = []

    def slow_reconnect(ptr, principal, timeout=None):
        seen.append(timeout)
        if timeout:  # a caller that waits pays for it, as a real reconnect makes it
            time.sleep(SLOW)
        return "reconnected" if timeout else "timeout"
    monkeypatch.setattr(ah, "reconnect_now", slow_reconnect)
    monkeypatch.setattr(ah, "maybe_heal", lambda *a, **k: "no-change")  # nothing to redraft: only a reconnect is due
    return seen


@pytest.mark.parametrize("n", [1, 6, 12])
def test_ask_time_does_not_grow_with_the_number_of_stale_sets(tmp_path, monkeypatch, capsys, notes, n):
    seen = stale_world(tmp_path, monkeypatch, notes, n, with_list=True)
    t0 = time.time()
    run(monkeypatch, capsys, Q)
    assert time.time() - t0 < SLOW * 3  # twelve inline reconnects would be 4.8 s
    assert len(seen) == n and not any(seen)  # each set's reconnect was started with no wait


def test_a_stale_set_with_a_prepared_list_still_answers(tmp_path, monkeypatch, capsys, notes):
    stale_world(tmp_path, monkeypatch, notes, 3, with_list=True)
    rc, out = run(monkeypatch, capsys, Q)
    assert rc == 0 and "warranty.md" in out
    assert "[STALE]" in out or "stale" in out


def test_a_stale_set_with_no_list_says_it_is_refreshing(tmp_path, monkeypatch, capsys, notes):
    stale_world(tmp_path, monkeypatch, notes, 2, with_list=False)
    monkeypatch.setattr(ah, "maybe_heal", lambda *a, **k: "started")
    rc, out = run(monkeypatch, capsys, "--json", Q)
    import json
    obj = json.loads(out)
    assert rc != 0 and obj["outcome"] == "needs-setup"
    assert [u["healing"] for u in obj["unsearched"]] == [True, True]  # shown to the caller as "refreshing, ask again"
    assert not obj.get("files")


def test_the_background_heal_is_started_once_per_stale_set(tmp_path, monkeypatch, capsys, notes):
    stale_world(tmp_path, monkeypatch, notes, 4, with_list=False)
    calls = []
    monkeypatch.setattr(ah, "maybe_heal", lambda ptr, principal, **k: calls.append(ptr) or "started")
    run(monkeypatch, capsys, Q)
    assert sorted(calls) == ["s0", "s1", "s2", "s3"]


# --- heal_in_background itself: it never waits, and covers the sets maybe_heal skips -----------------

def test_heal_in_background_starts_a_reconnect_without_waiting(tmp_path, monkeypatch):
    from test_auto_heal_fresh import Proc, _setup, ah as real
    _setup(tmp_path, monkeypatch)  # files match the prepare cache: maybe_heal says no-change
    Proc.outcome = "timeout"
    waits = []
    orig = Proc.wait
    monkeypatch.setattr(Proc, "wait", lambda self, timeout=None: waits.append(timeout) or orig(self, timeout))
    assert real.heal_in_background("alpha", "tester") == "started"
    assert waits == [0] and len(Proc.launched) == 1 and "--no-findability" in Proc.launched[0][-1]


def test_heal_in_background_leaves_a_manual_note_alone(tmp_path, monkeypatch):
    from test_auto_heal_fresh import _setup, ah as real
    _setup(tmp_path, monkeypatch, names=())
    assert real.heal_in_background("gamma-manual-1", "tester") == "manual"


# --- a recipe set is gated before any drain is spawned ------------------------------------------------

def _recipe_world(tmp_path, monkeypatch):
    from test_auto_heal_fresh import Proc, _setup, ah as real
    _setup(tmp_path, monkeypatch, names=())
    src = tmp_path / "notes" / "gamma.md"
    src.parent.mkdir(exist_ok=True)
    src.write_text("# gamma\n")
    rec = {"pointer": "gamma", "dataset": "d", "principals": ["tester"], "structure": {}, "sources": [{"path": str(src)}]}
    monkeypatch.setattr(real, "_memory", lambda req: {"status": "ok", "recipe": rec})
    spawned = []
    monkeypatch.setattr(real, "_spawn_detached", lambda argv: spawned.append(argv) or Proc(argv))
    return real, spawned, rec


def test_a_second_ask_during_cooldown_spawns_nothing(tmp_path, monkeypatch):
    real, spawned, _ = _recipe_world(tmp_path, monkeypatch)
    assert real.heal_in_background("gamma", "tester") == "started" and len(spawned) == 1
    assert real._load_state("tester")["pending"]["gamma"]["kind"] == "recipe"  # queued for the drain
    real._settle("tester", "gamma", False)  # the replay failed: the pointer waits out its retry
    real._release_lock("tester", "gamma")
    assert real.heal_in_background("gamma", "tester") == "cooldown" and len(spawned) == 1


def test_a_held_set_spawns_nothing(tmp_path, monkeypatch):
    real, spawned, rec = _recipe_world(tmp_path, monkeypatch)
    fp = real._fingerprint([rec["sources"][0]["path"]], rec)
    with real._state_txn("tester") as st:
        st["held"]["gamma"] = {"fp": fp, "ts": real.time.time()}
    assert real.heal_in_background("gamma", "tester") == "held" and not spawned


def test_the_concurrency_cap_is_respected(tmp_path, monkeypatch):
    real, spawned, _ = _recipe_world(tmp_path, monkeypatch)
    monkeypatch.setattr(real, "_live_locks", lambda principal, besides: real.MAX_CONCURRENT)
    assert real.heal_in_background("gamma", "tester") == "in-progress" and not spawned
    assert real._load_state("tester")["pending"]["gamma"]["kind"] == "recipe"  # queued, not dropped


def test_a_set_with_no_recipe_is_not_claimed_as_refreshing(tmp_path, monkeypatch, capsys, notes):
    import json
    stale_world(tmp_path, monkeypatch, notes, 1, with_list=False)
    monkeypatch.setattr(ah, "heal_in_background", lambda *a, **k: "no-recipe")
    _, out = run(monkeypatch, capsys, "--json", Q)
    assert [u["healing"] for u in json.loads(out)["unsearched"]] == [False]
    assert "refreshing in the background" not in out and "refresh started" not in out


def test_a_started_heal_says_to_ask_again_in_a_minute(tmp_path, monkeypatch, capsys, notes):
    stale_world(tmp_path, monkeypatch, notes, 1, with_list=False)
    monkeypatch.setattr(ah, "heal_in_background", lambda *a, **k: "started")
    _, out = run(monkeypatch, capsys, Q)
    assert "refreshing in the background; ask again in a minute" in out


def test_eight_parallel_asks_launch_one_replay(tmp_path, monkeypatch):
    import threading
    real, spawned, _ = _recipe_world(tmp_path, monkeypatch)
    out = []
    ts = [threading.Thread(target=lambda: out.append(real.heal_in_background("gamma", "tester"))) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(spawned) == 1 and len(out) == 8 and "started" in out
