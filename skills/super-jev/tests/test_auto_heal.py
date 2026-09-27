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


# ---- pointers prepare_bulk did not build: replay the connect recipe recorded at connect time ----
# The live failure (primary, 2026-09-25): a connector-built pointer went stale when one of its
# files changed, every ask printed "no recorded recipe" and a prepare_bulk command that could
# not rebuild it, and auto-heal skipped it ("no-report") on every ask, for good.

def _path_connected(tmp_path, monkeypatch):
    import sys
    exp = SKILL.parent.parent / "experiments" / "verified-pointer-memory"
    sys.path.insert(0, str(exp))
    from path_connect import connect
    from service import Service
    state_dir = tmp_path / "state"
    monkeypatch.setattr(ah, "STATE_DIR", state_dir)
    monkeypatch.setattr(ah, "LOG_PATH", state_dir / "autoheal.log")
    src = tmp_path / "facts.json"
    src.write_text('{"version": "0.2.0"}\n')
    config = {"db": str(tmp_path / "answers.sqlite"), "registry": str(tmp_path / "registry.json")}
    service = Service(config["db"], config["registry"], lambda *_: {"status": "no-match"})

    def memory(req):
        if req["action"] == "recipe":
            return service.recipe(req["pointer"], req["principal"])
        return connect(req, config)

    monkeypatch.setattr(ah, "_memory", memory)
    req = {"pointer": "structured", "principals": ["agent"],
           "sources": [{"path": str(src), "description": "package manifest"}]}
    preview = connect(req, config)
    assert connect({**req, "reviewed": True, "sources": preview["sources"]}, config)["status"] == "registered"
    return src, service


def test_a_changed_connector_pointer_heals_from_its_recorded_recipe(tmp_path, monkeypatch):
    src, service = _path_connected(tmp_path, monkeypatch)
    src.write_text('{"version": "1.0.6"}\n')
    assert service.pointer("structured", "agent")[1] == {"status": "preparation-required"}
    assert ah.reconnect_now("structured", "agent", cache_dir=tmp_path) == "no-report"
    assert ah.reconnect_recipe("structured", "agent") == "reconnected"
    pointer, error = service.pointer("structured", "agent")
    assert error is None
    sources = service.sources("structured", "agent")["sources"]
    assert [s["description"] for s in sources] == ["package manifest"]
    assert "1.0.6" in Path(sources[0]["path"]).read_text()
    # Cooled down after a success: an immediate second stale sighting does not reconnect again.
    assert ah.reconnect_recipe("structured", "agent") == "cooldown"


def test_a_recipe_never_widens_or_leaks_to_another_principal(tmp_path, monkeypatch):
    _path_connected(tmp_path, monkeypatch)
    assert ah.reconnect_recipe("structured", "intruder") == "no-recipe"


def test_a_hand_built_dataset_with_no_recipe_is_reported_not_retried(tmp_path, monkeypatch):
    _, service = _path_connected(tmp_path, monkeypatch)
    registry = json.loads(Path(service.registry).read_text())
    entry = registry["datasets"]["structured"]
    del entry["recipe"], entry["pathConnection"]
    Path(service.registry).write_text(json.dumps(registry))
    assert ah.reconnect_recipe("structured", "agent") == "no-recipe"
    assert json.loads(ah.LOG_PATH.read_text().splitlines()[-1])["reason"] == "no-recipe"


def test_a_connector_pointer_from_before_recipes_heals_from_its_manifest(tmp_path, monkeypatch):
    src, service = _path_connected(tmp_path, monkeypatch)
    registry = json.loads(Path(service.registry).read_text())
    del registry["datasets"]["structured"]["recipe"]
    Path(service.registry).write_text(json.dumps(registry))
    src.write_text('{"version": "1.0.7"}\n')
    assert ah.reconnect_recipe("structured", "agent") == "reconnected"
    assert service.pointer("structured", "agent")[1] is None


def test_a_pointer_made_through_a_moving_link_heals_to_the_new_target(tmp_path, monkeypatch):
    # A skills folder linked to the current release: after a release moves the link, the pointer
    # goes stale and the heal must find the file by the path it was given, not the old target.
    import sys
    exp = SKILL.parent.parent / "experiments" / "verified-pointer-memory"
    sys.path.insert(0, str(exp))
    from path_connect import connect
    from service import Service
    monkeypatch.setattr(ah, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(ah, "LOG_PATH", tmp_path / "state" / "autoheal.log")
    for rel, text in (("v1", "version one"), ("v2", "version two")):
        (tmp_path / rel).mkdir()
        (tmp_path / rel / "SKILL.md").write_text(text + "\n")
    link = tmp_path / "current"
    link.symlink_to(tmp_path / "v1")
    config = {"db": str(tmp_path / "answers.sqlite"), "registry": str(tmp_path / "registry.json")}
    service = Service(config["db"], config["registry"], lambda *_: {"status": "no-match"})
    monkeypatch.setattr(ah, "_memory", lambda req: service.recipe(req["pointer"], req["principal"])
                        if req["action"] == "recipe" else connect(req, config))
    req = {"pointer": "skills", "principals": ["agent"],
           "sources": [{"path": str(link / "SKILL.md"), "description": "skill page"}]}
    preview = connect(req, config)
    assert connect({**req, "reviewed": True, "sources": preview["sources"]}, config)["status"] == "registered"
    link.unlink()
    link.symlink_to(tmp_path / "v2")
    assert service.pointer("skills", "agent")[1] == {"status": "preparation-required"}
    assert ah.reconnect_recipe("skills", "agent") == "reconnected"
    assert service.pointer("skills", "agent")[1] is None
    src = service.sources("skills", "agent")["sources"][0]["path"]
    assert "version two" in Path(src).read_text()


def test_a_failed_refresh_is_named_not_hidden_behind_cooldown(tmp_path, monkeypatch):
    # Live 2026-09-27: every refresh failed ("the writer prompt contains a secret") for 2 hours
    # while each ask said only "refreshed recently, cooling down".
    monkeypatch.setattr(ah, "STATE_DIR", tmp_path)
    assert ah.last_refresh_error("me", "docs") == ""
    (tmp_path / "me-docs-last-refresh.log").write_text("inventory: 3 files\nERROR: description writer failed: boom\n  hint\n")
    assert ah.last_refresh_error("me", "docs") == "description writer failed: boom"
    (tmp_path / "me-docs-last-refresh.log").write_text("inventory: 3 files\nconnect: registered\n")
    assert ah.last_refresh_error("me", "docs") == ""


def test_a_split_parts_failed_refresh_is_read_from_its_parents_log(tmp_path, monkeypatch):
    monkeypatch.setattr(ah, "STATE_DIR", tmp_path)
    (tmp_path / "docs-report.json").write_text(json.dumps({"parts": [{"pointer": "docs-2"}]}))
    (tmp_path / "me-docs-last-refresh.log").write_text("ERROR: description writer failed: boom\n")
    assert ah.last_refresh_error("me", "docs-2", cache_dir=tmp_path) == "description writer failed: boom"


# Starvation (live 2026-09-27): an ask heals its stale pointers in a fixed order, so the first
# changed one took the per-principal lock and the last one was skipped "in-progress" on every
# ask while the others kept changing. Now a pointer that finds the lock held is queued, and
# the refresh holding the lock heals the queue before it releases it.

def _add_pointer(cache_dir, name, principal="agent"):
    f = cache_dir.parent / "brain" / f"{name}.md"
    f.write_text(f"# {name}\n")
    (cache_dir / f"{name}-report.json").write_text(json.dumps(
        {"pointer": name, "roots": [str(f.parent)], "approved": [str(f)], "excludes": [],
         "noRecurse": True, "principal": principal, "principals": [principal]}))
    (cache_dir / f"{name}.json").write_text(json.dumps({}))


def _token(wrapper_call):
    # The detached child's shell line ends in: auto_heal.py --drain <principal> <token> ...
    words = wrapper_call[2].split()
    return words[words.index("--drain") + 2]


def _fake_run(monkeypatch, principal="agent", code=0):
    runs = []

    def run(cmd, **kw):
        # Serial: the drain runs each refresh while it still holds the principal's lock.
        assert ah._lock_path(principal).is_file()
        runs.append(cmd[cmd.index("--pointer") + 1] if "--pointer" in cmd else cmd)
        return ah.subprocess.CompletedProcess(cmd, code)
    monkeypatch.setattr(ah.subprocess, "run", run)
    return runs


def test_a_pointer_skipped_for_the_lock_is_refreshed_before_the_lock_is_released(tmp_path, monkeypatch):
    calls, cache_dir = _setup(tmp_path, monkeypatch, name="first")
    for name in ("second", "last"):
        _add_pointer(cache_dir, name)
    runs = _fake_run(monkeypatch)
    for _ in range(2):  # two lookups while the first refresh is still running
        assert ah.maybe_heal("first", "agent") in ("started", "cooldown")
        assert ah.maybe_heal("second", "agent") == "in-progress"
        assert ah.maybe_heal("last", "agent") == "in-progress"
    assert len(calls) == 1  # never two refreshes at once
    assert ah.drain("agent", _token(calls[0])) == 0  # the first refresh has finished
    assert [r for r in runs] == ["second", "last"]  # oldest first, one at a time
    assert not ah._lock_path("agent").exists()
    assert ah._load_state("agent")["pending"] == {}
    assert ah.maybe_heal("last", "agent") == "cooldown"  # counted like any other refresh


def test_the_drain_keeps_the_hourly_cap_and_cooldown_and_leaves_those_queued(tmp_path, monkeypatch):
    calls, cache_dir = _setup(tmp_path, monkeypatch, name="first")
    for name in ("cool", "over"):
        _add_pointer(cache_dir, name)
    runs = _fake_run(monkeypatch)
    monkeypatch.setattr(ah, "MAX_PER_HOUR", 2)
    assert ah.maybe_heal("first", "agent", max_per_hour=2) == "started"
    ah._mark("agent", "cool", time.time())  # refreshed a moment ago: cooling down
    assert ah.maybe_heal("cool", "agent", cooldown_secs=0) == "in-progress"
    assert ah.maybe_heal("over", "agent") == "in-progress"
    ah._mark("agent", "x", time.time(), [time.time(), time.time()])  # hourly cap reached
    ah.drain("agent", _token(calls[0]))
    assert runs == []
    assert set(ah._load_state("agent")["pending"]) == {"cool", "over"}
    assert not ah._lock_path("agent").exists()


def test_a_drain_never_touches_a_lock_that_is_no_longer_its_own(tmp_path, monkeypatch):
    calls, cache_dir = _setup(tmp_path, monkeypatch, name="first")
    _add_pointer(cache_dir, "last")
    runs = _fake_run(monkeypatch)
    assert ah.maybe_heal("first", "agent") == "started"
    assert ah.maybe_heal("last", "agent") == "in-progress"
    token = _token(calls[0])
    ah._release_lock("agent")
    assert ah._acquire_lock("agent", "someone-else")  # e.g. taken over as stale
    ah.drain("agent", token)
    assert runs == [] and ah._lock_path("agent").is_file()


def test_a_running_refresh_keeps_the_lock_after_the_lookup_that_started_it_exits(tmp_path, monkeypatch):
    # The lock used to name the lookup's pid; once that lookup exited the lock looked dead while
    # its refresh still ran, and the next lookup started a second refresh for the principal.
    live_child = os.getpid()

    class Proc:
        pid = live_child

        def __init__(self, cmd, **kw):
            calls.append(cmd)
    calls, cache_dir = _setup(tmp_path, monkeypatch, name="first")
    _add_pointer(cache_dir, "other")
    monkeypatch.setattr(ah.subprocess, "Popen", Proc)
    monkeypatch.setattr(ah.os, "getpid", lambda: 999999999)  # the lookup, gone by now
    assert ah.maybe_heal("first", "agent") == "started"
    assert ah.maybe_heal("other", "agent") == "in-progress"
    assert len(calls) == 1


def test_a_queued_connector_pointer_is_replayed_by_the_drain(tmp_path, monkeypatch):
    src, service = _path_connected(tmp_path, monkeypatch)
    src.write_text('{"version": "1.0.8"}\n')
    token = ah._acquire_lock("agent", "a-running-refresh")
    assert ah.reconnect_recipe_or_queue("structured", "agent") == "in-progress"
    assert service.pointer("structured", "agent")[1] == {"status": "preparation-required"}
    ah.drain("agent", token)
    assert service.pointer("structured", "agent")[1] is None
    assert not ah._lock_path("agent").exists()


# Review of #223 (at 2e3d286): every lock owner must hand the lock to a drain, not only the
# background refresh, and none may lose a pointer queued while it held the lock.

def test_a_recipe_reconnect_keeps_a_pointer_queued_while_it_ran_and_drains_it(tmp_path, monkeypatch):
    src, service = _path_connected(tmp_path, monkeypatch)
    src.write_text('{"version": "1.0.9"}\n')
    inner = ah._memory

    def memory(req):
        if req.get("action") == "connect" and not req.get("reviewed"):
            ah._queue("agent", "another-pointer", "refresh")  # another lookup, meanwhile
        return inner(req)
    spawned, real = [], ah.subprocess.Popen
    monkeypatch.setattr(ah.subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd) or object()
                        if "--drain" in cmd else real(cmd, **kw))  # the connector runs its own
    assert ah.reconnect_recipe("structured", "agent", memory=memory) == "reconnected"
    assert "another-pointer" in ah._load_state("agent")["pending"]
    token = json.loads(ah._lock_path("agent").read_text())["token"]
    assert spawned == [ah._drain_cmd("agent", token)]  # the lock goes to a drain, still held


def test_an_inline_reconnect_hands_its_lock_to_a_drain(tmp_path, monkeypatch):
    calls, cache_dir = _setup(tmp_path, monkeypatch, name="first", changed=False)

    class Proc:
        pid = 4242

        def __init__(self, cmd, **kw):
            calls.append(cmd)

        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(ah.subprocess, "Popen", Proc)
    assert ah.reconnect_now("first", "agent", cache_dir=cache_dir) == "reconnected"
    token = json.loads(ah._lock_path("agent").read_text())["token"]
    assert shlex_join(ah._drain_cmd("agent", token)) in calls[0][2]


def test_a_timed_out_reconnect_leaves_the_lock_naming_its_child(tmp_path, monkeypatch):
    calls, cache_dir = _setup(tmp_path, monkeypatch, name="first", changed=False)

    class Proc:
        pid = 4242

        def __init__(self, cmd, **kw):
            calls.append(cmd)

        def wait(self, timeout=None):
            raise ah.subprocess.TimeoutExpired("sh", timeout)
    monkeypatch.setattr(ah.subprocess, "Popen", Proc)
    assert ah.reconnect_now("first", "agent", cache_dir=cache_dir, timeout=1) == "timeout"
    assert json.loads(ah._lock_path("agent").read_text())["pid"] == 4242


def test_the_drain_takes_the_lock_over_under_its_own_pid(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    token = ah._acquire_lock("agent", "x")
    ah._hold_lock("agent", token, pid=999999999)  # the refresh's shell, exited
    seen = []
    monkeypatch.setattr(ah, "_load_state", lambda p: seen.append(
        json.loads(ah._lock_path(p).read_text())["pid"]) or {"pointers": {}, "attempts": [], "pending": {}})
    ah.drain("agent", token)
    assert seen and seen[0] == os.getpid()
    assert not ah._lock_path("agent").exists()


def shlex_join(cmd):
    import shlex
    return shlex.join(cmd)
