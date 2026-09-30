#!/usr/bin/env python3
"""Auto-heal keeps note sets fresh, reliably (live 2026-09-29). One rule: an attempt is judged by
how it ENDS, and every launch takes the pointer's own lock first.
  (a) a reconnect still running when the lookup stops waiting is not a failure;
  (b) a long refresh of one set never blocks the principal's other sets (lock per pointer);
  (c) an attempt that failed waits a short retry (not the full cooldown) and gives back its
      hourly-cap slot;
  (d) a refusal that repeats until the file changes (secret-held) is not retried until it does;
  (e) a pointer that already has a run going is never started a second time, by any path.
Made-up principals and pointers, a fake clock, fake processes: no refresh ever runs.

    python3 -m pytest skills/super-jev/tests/test_auto_heal_fresh.py -q
"""
import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("auto_heal_fresh_t", SKILL / "auto_heal.py")
ah = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ah)

START = 1_800_000_000.0


class Proc:
    """A fake detached shell: `outcome` is "timeout" (still running), or the exit code."""
    outcome = 0

    def __init__(self, cmd, **kw):
        Proc.launched.append(cmd)
        self.pid = os.getpid()  # a live pid, so its lock reads as a running refresh

    def wait(self, timeout=None):
        if Proc.outcome == "timeout":
            raise subprocess.TimeoutExpired("sh", timeout)
        return Proc.outcome


def _setup(tmp_path, monkeypatch, names=("alpha",), changed=False, principal="tester"):
    root = tmp_path / "notes"; root.mkdir()
    cache_dir = tmp_path / "cache"; cache_dir.mkdir()
    monkeypatch.setattr(ah, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(ah, "LOG_PATH", tmp_path / "state" / "autoheal.log")
    monkeypatch.setattr(ah.rc, "CACHE_DIR", cache_dir)
    for name in names:
        f = root / f"{name}.md"; f.write_text(f"# {name}\n")
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        (cache_dir / f"{name}.json").write_text(json.dumps(
            {} if changed else {str(f): {"sha256": sha, "pass": True, "checkedAt": "2026-01-01T00:00:00"}}))
        (cache_dir / f"{name}-report.json").write_text(json.dumps(
            {"pointer": name, "roots": [str(root)], "approved": [str(f)], "excludes": [],
             "noRecurse": True, "principal": principal, "principals": [principal]}))
    clock = [START]
    monkeypatch.setattr(ah.time, "time", lambda: clock[0])
    Proc.launched, Proc.outcome = [], 0
    monkeypatch.setattr(ah.subprocess, "Popen", Proc)
    monkeypatch.setattr(ah.subprocess, "run", lambda cmd, **kw: Proc.launched.append(cmd) or
                        type("R", (), {"returncode": Proc.outcome})())
    return cache_dir, clock


def _token(pointer, principal="tester"):
    return json.loads(ah._lock_path(principal, pointer).read_text())["token"]


# (a) ---------------------------------------------------------------------------------------

def test_a_reconnect_still_running_when_the_lookup_stops_waiting_is_not_a_failure(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch)
    Proc.outcome = "timeout"
    assert ah.reconnect_now("alpha", "tester", cache_dir=cache_dir, timeout=1) == "timeout"
    state = ah._load_state("tester")
    assert not state.get("fails") and not state.get("retry")  # nothing judged yet
    assert ah._lock_path("tester", "alpha").is_file()  # the child keeps its lock while it runs


def test_a_timed_out_reconnect_that_then_fails_retries_in_two_minutes_not_ten(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch)
    Proc.outcome = "timeout"
    ah.reconnect_now("alpha", "tester", cache_dir=cache_dir, timeout=1)
    ah.drain("tester", "alpha", _token("alpha"), rc=1)  # the child ends: it failed
    clock[0] += ah.RETRY_SECS - 5
    assert ah.reconnect_now("alpha", "tester", cache_dir=cache_dir) == "cooldown"
    clock[0] += 10
    Proc.outcome = 0
    assert ah.reconnect_now("alpha", "tester", cache_dir=cache_dir) == "reconnected"
    assert ah.RETRY_SECS < ah.COOLDOWN_SECS


def test_a_timed_out_reconnect_that_then_works_keeps_the_full_cooldown(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch)
    Proc.outcome = "timeout"
    ah.reconnect_now("alpha", "tester", cache_dir=cache_dir, timeout=1)
    ah.drain("tester", "alpha", _token("alpha"), rc=0)
    clock[0] += ah.RETRY_SECS + 5
    assert ah.reconnect_now("alpha", "tester", cache_dir=cache_dir) == "cooldown"


# (b) ---------------------------------------------------------------------------------------

def test_a_long_refresh_of_one_set_does_not_block_the_principals_other_sets(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, names=("alpha", "beta"), changed=True)
    assert ah.maybe_heal("alpha", "tester") == "started"
    clock[0] += 25 * 60  # alpha's refresh has been running 25 minutes
    assert ah.maybe_heal("beta", "tester") == "started"
    assert len(Proc.launched) == 2


def test_a_live_refresh_older_than_the_old_thirty_minute_limit_is_still_running(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, changed=True)
    assert ah.maybe_heal("alpha", "tester") == "started"
    clock[0] += 45 * 60
    assert ah.maybe_heal("alpha", "tester", cooldown_secs=0) == "in-progress"
    assert len(Proc.launched) == 1


# (c) ---------------------------------------------------------------------------------------

def test_a_failed_refresh_waits_two_minutes_and_does_not_count_toward_the_hourly_cap(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, changed=True)
    assert ah.maybe_heal("alpha", "tester") == "started"
    assert len(ah._load_state("tester")["attempts"]) == 1
    ah.drain("tester", "alpha", _token("alpha"), rc=1)
    assert ah._load_state("tester")["attempts"] == []  # the slot is given back
    clock[0] += ah.RETRY_SECS - 5
    assert ah.maybe_heal("alpha", "tester") == "cooldown"
    clock[0] += 10
    assert ah.maybe_heal("alpha", "tester") == "started"


def test_a_refresh_that_worked_keeps_its_cooldown_and_its_cap_slot(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, changed=True)
    assert ah.maybe_heal("alpha", "tester") == "started"
    ah.drain("tester", "alpha", _token("alpha"), rc=0)
    assert len(ah._load_state("tester")["attempts"]) == 1
    clock[0] += ah.RETRY_SECS + 5
    assert ah.maybe_heal("alpha", "tester") == "cooldown"


def test_repeated_failures_back_off_up_to_an_hour_so_a_broken_set_is_not_hammered(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, changed=True)
    waits = []
    for _ in range(9):
        assert ah.maybe_heal("alpha", "tester", max_per_hour=100) == "started"
        ah.drain("tester", "alpha", _token("alpha"), rc=1)
        waits.append(ah._load_state("tester")["retry"]["alpha"] - clock[0])
        clock[0] += waits[-1] + 1
    assert waits[:3] == [ah.RETRY_SECS, ah.RETRY_SECS * 2, ah.RETRY_SECS * 4]
    assert max(waits) == ah.MAX_RETRY_SECS and waits[-1] == ah.MAX_RETRY_SECS
    assert ah.MAX_RETRY_SECS > ah.COOLDOWN_SECS  # a failing set is tried less often than a working one


# (d) ---------------------------------------------------------------------------------------

def _recipe_memory(src, calls):
    recipe = {"pointer": "gamma", "dataset": "gamma", "principals": ["tester"], "structure": "flat",
              "sources": [{"path": str(src), "description": "made-up notes"}]}

    def memory(req):
        calls.append(req["action"])
        if req["action"] == "recipe":
            return {"status": "ok", "recipe": recipe}
        return {"status": "error", "reason": "secret-held"}
    return memory


def test_a_held_file_is_not_retried_until_the_file_changes(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch)
    src = tmp_path / "held.md"; src.write_text("made-up note\n")
    calls = []
    memory = _recipe_memory(src, calls)
    assert ah.reconnect_recipe("gamma", "tester", memory=memory) == "failed"
    assert calls.count("connect") == 1
    for _ in range(22):  # a day of lookups, well past every cooldown
        clock[0] += 3600
        assert ah.reconnect_recipe("gamma", "tester", memory=memory) == "held"
    assert calls.count("connect") == 1
    src.write_text("made-up note, edited\n")
    assert ah.reconnect_recipe("gamma", "tester", memory=memory) == "failed"
    assert calls.count("connect") == 2  # the file changed: tried again


def test_a_failure_that_is_not_a_deterministic_refusal_is_still_retried(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch)
    src = tmp_path / "notes.md"; src.write_text("made-up note\n")
    calls = []
    memory = _recipe_memory(src, calls)
    inner = memory
    memory = lambda req: ({"status": "error", "reason": "provider-down"}  # noqa: E731
                          if req["action"] == "connect" else inner(req))
    assert ah.reconnect_recipe("gamma", "tester", memory=memory) == "failed"
    clock[0] += ah.RETRY_SECS + 1
    assert ah.reconnect_recipe("gamma", "tester", memory=memory) == "failed"
    assert calls.count("recipe") == 2


# (e) ---------------------------------------------------------------------------------------

def test_no_path_starts_a_second_run_of_a_pointer_that_already_has_one(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, changed=True)
    token = ah._acquire_lock("tester", "alpha")  # a prepare_bulk for alpha is running
    assert token
    clock[0] += 3600  # every cooldown is over
    assert ah.maybe_heal("alpha", "tester") == "in-progress"
    assert ah._drain_prepare("alpha", "tester", "refresh") == "in-progress"
    (cache_dir / "alpha.json").write_text(json.dumps({str(tmp_path / "notes" / "alpha.md"): {
        "sha256": hashlib.sha256((tmp_path / "notes" / "alpha.md").read_bytes()).hexdigest(), "pass": True}}))
    assert ah.reconnect_now("alpha", "tester", cache_dir=cache_dir) == "in-progress"
    src = tmp_path / "alpha.md"; src.write_text("made-up note\n")
    assert ah.reconnect_recipe("alpha", "tester", memory=_recipe_memory(src, [])) == "in-progress"
    assert Proc.launched == []
    assert ah._lock_path("tester", "alpha").is_file()  # still the first run's lock


# Review of #267 ------------------------------------------------------------------------------

def _parallel_lookups(names):
    import threading
    out = []
    threads = [threading.Thread(target=lambda n=n: out.append(ah.maybe_heal(n, "tester"))) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def test_parallel_lookups_never_start_more_refreshes_than_the_concurrent_bound(tmp_path, monkeypatch):
    names = [f"set{i}" for i in range(12)]
    _setup(tmp_path, monkeypatch, names=names, changed=True)
    results = _parallel_lookups(names)
    assert results.count("started") == ah.MAX_CONCURRENT == len(Proc.launched)
    state = ah._load_state("tester")
    assert len(state["attempts"]) == ah.MAX_CONCURRENT  # every start is recorded
    assert len(state["pending"]) == 12 - ah.MAX_CONCURRENT  # the rest wait, none dropped


def test_parallel_lookups_never_overshoot_the_hourly_cap_and_record_every_attempt(tmp_path, monkeypatch):
    names = [f"set{i}" for i in range(12)]
    _setup(tmp_path, monkeypatch, names=names, changed=True)
    monkeypatch.setattr(ah, "MAX_CONCURRENT", 20)
    results = _parallel_lookups(names)
    assert results.count("started") == ah.MAX_PER_HOUR == 6
    assert len(ah._load_state("tester")["attempts"]) == 6
    assert results.count("rate-limited") == 6


def test_a_pointer_queued_behind_its_own_running_refresh_runs_when_it_ends(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, changed=True)
    assert ah.maybe_heal("alpha", "tester") == "started"
    token = _token("alpha")
    clock[0] += ah.COOLDOWN_SECS + 60  # still running, files changed again, cooldown over
    assert ah.maybe_heal("alpha", "tester") == "in-progress"
    assert "alpha" in ah._load_state("tester")["pending"]
    Proc.launched.clear()
    ah.drain("tester", "alpha", token, rc=0)  # the first refresh ended
    assert len(Proc.launched) == 1 and "prepare_bulk.py" in " ".join(Proc.launched[0])  # ran, under its own lock
    assert ah._load_state("tester")["pending"] == {}
    assert not ah._lock_path("tester", "alpha").exists()


def test_a_reconnect_that_finishes_inside_the_wait_is_judged_once(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch)
    Proc.outcome = 1
    assert ah.reconnect_now("alpha", "tester", cache_dir=cache_dir) == "failed"
    ah.drain("tester", "alpha", _token("alpha"), rc=1)  # the child's own drain reports it too
    state = ah._load_state("tester")
    assert state["fails"]["alpha"] == 1
    assert state["retry"]["alpha"] - clock[0] == ah.RETRY_SECS


def test_a_new_attempt_is_judged_again(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, changed=True)
    for expected in (1, 2):
        assert ah.maybe_heal("alpha", "tester", max_per_hour=100) == "started"
        ah.drain("tester", "alpha", _token("alpha"), rc=1)
        assert ah._load_state("tester")["fails"]["alpha"] == expected
        clock[0] += ah.MAX_RETRY_SECS + 1


def test_a_held_mark_expires_after_a_day_and_a_changed_recipe_clears_it(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch)
    src = tmp_path / "held.md"; src.write_text("made-up note\n")
    calls = []
    memory = _recipe_memory(src, calls)
    ah.reconnect_recipe("gamma", "tester", memory=memory)
    clock[0] += ah.HELD_EXPIRE_SECS - 60
    assert ah.reconnect_recipe("gamma", "tester", memory=memory) == "held"
    clock[0] += 120
    assert ah.reconnect_recipe("gamma", "tester", memory=memory) == "failed"  # a day passed: tried again
    n = calls.count("connect")
    clock[0] += 3600  # past the short retry wait
    inner = memory  # a hand reconnect re-records the recipe: it now names a described note differently
    def renamed(req):
        got = inner(req)
        if req["action"] == "recipe":
            got["recipe"] = {**got["recipe"], "sources": [{**got["recipe"]["sources"][0], "description": "new"}]}
        return got
    assert ah.reconnect_recipe("gamma", "tester", memory=renamed) == "failed"
    assert calls.count("connect") == n + 1


def test_a_refresh_that_cannot_start_is_a_failure_not_an_exception(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, changed=True)

    def broken(cmd, **kw):
        raise OSError("no processes")
    monkeypatch.setattr(ah.subprocess, "Popen", broken)
    assert ah.maybe_heal("alpha", "tester") == "failed"
    state = ah._load_state("tester")
    assert state["fails"]["alpha"] == 1 and state["attempts"] == []
    assert not ah._lock_path("tester", "alpha").exists()


def test_a_lock_naming_a_pid_that_started_after_the_lock_is_a_reused_pid(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, changed=True)
    monkeypatch.setattr(ah.time, "time", ah._WALL)  # this one needs the real clock: ps reports real times
    ah.STATE_DIR.mkdir(parents=True, exist_ok=True)
    # A live pid (this test) named by a lock written an hour before it started: not the holder.
    ah._lock_path("tester", "alpha").write_text(json.dumps({"pid": os.getpid(), "ts": ah._WALL() - 3600}))
    assert ah.maybe_heal("alpha", "tester") == "started"


# Re-review of #267 (at 73d5293) --------------------------------------------------------------

def test_a_drain_and_one_other_running_refresh_still_heal_the_queue(tmp_path, monkeypatch):
    # The drain's own lock only heals the queue; counting it as a run left every queued pointer
    # "busy" whenever one other refresh was running.
    cache_dir, clock = _setup(tmp_path, monkeypatch, names=("alpha", "beta", "queued"), changed=True)
    drain_token = ah._acquire_lock("tester", "alpha")  # alpha's refresh just ended
    assert ah._acquire_lock("tester", "beta")  # another set's refresh is running
    ah._queue("tester", "queued", "refresh")
    ah.drain("tester", "alpha", drain_token, rc=0)
    assert len(Proc.launched) == 1 and "queued" in Proc.launched[0]  # the refresh's own --pointer/--name
    assert ah._load_state("tester")["pending"] == {}
    assert ah._load_state("tester")["pointers"].get("queued")  # it ran


def test_a_drain_running_its_own_pointer_counts_as_a_run(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, names=("alpha", "beta"), changed=True)
    token = ah._acquire_lock("tester", "alpha")
    ah._hold_lock("tester", "alpha", token, drain=True)
    assert ah._live_locks("tester", besides="beta") == 0  # a drain only healing the queue
    ah._hold_lock("tester", "alpha", token, drain=False)
    assert ah._live_locks("tester", besides="beta") == 1  # its own pointer is running


def test_a_reconnect_over_the_concurrent_bound_queues_instead_of_running(tmp_path, monkeypatch):
    cache_dir, clock = _setup(tmp_path, monkeypatch, names=("alpha", "b1", "b2"))
    assert ah._acquire_lock("tester", "b1") and ah._acquire_lock("tester", "b2")  # two running
    assert ah.reconnect_now("alpha", "tester", cache_dir=cache_dir) == "in-progress"
    assert Proc.launched == []
    assert ah._load_state("tester")["pending"]["alpha"]["kind"] == "reconnect"
