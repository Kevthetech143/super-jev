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
