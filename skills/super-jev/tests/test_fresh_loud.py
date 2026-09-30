#!/usr/bin/env python3
"""Freshness promise, offline (no Jev, no network; Popen/run are faked):
  1. any heal attempt starts the pointer's cooldown, failed or not;
  2. a stale pointer whose report records no principal is healed (the asking agent is recorded)
     or, from the command line with no asker, reported needs-setup -- never silently skipped;
  3. the per-principal hourly cap is fair: a pointer that already healed this hour queues behind
     one that is waiting, and a capped pointer waits in the queue instead of being dropped;
  4. refresh_changed.py opens with one outcome line and its exit code matches it;
  5. no freshness module imports shadow_repair.

    python3 -m pytest skills/super-jev/tests/test_fresh_loud.py -q
"""
import ast
import hashlib
import importlib.util
import json
import subprocess
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("auto_heal_fl", SKILL / "auto_heal.py")
ah = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ah)
rc = ah.rc


def _pointer(cache_dir, name, changed, principal="agent"):
    root = cache_dir.parent / "brain"; root.mkdir(exist_ok=True)
    f = root / f"{name}.md"; f.write_text(f"# {name}\n")
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    (cache_dir / f"{name}.json").write_text(json.dumps({} if changed else {str(f): {"sha256": sha, "pass": True}}))
    rep = {"pointer": name, "roots": [str(root)], "approved": [str(f)], "excludes": [], "noRecurse": True}
    if principal:
        rep.update(principal=principal, principals=[principal])
    (cache_dir / f"{name}-report.json").write_text(json.dumps(rep))


def _setup(tmp_path, monkeypatch, names=("moving",), changed=True, principal="agent"):
    cache_dir = tmp_path / "cache"; cache_dir.mkdir()
    state = tmp_path / "state"
    monkeypatch.setattr(ah, "STATE_DIR", state)
    monkeypatch.setattr(ah, "LOG_PATH", state / "autoheal.log")
    monkeypatch.setattr(rc, "CACHE_DIR", cache_dir)
    for n in names:
        _pointer(cache_dir, n, changed, principal)
    calls = []

    class Proc:
        pid = 0
        def wait(self, timeout=None):
            return Proc.code
    Proc.code = 1
    monkeypatch.setattr(ah.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd) or Proc())
    monkeypatch.setattr(ah.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": Proc.code})())
    return calls, cache_dir, Proc


def test_a_reconnect_that_held_some_files_is_a_success(tmp_path, monkeypatch):
    # prepare_bulk exits 3 = connected, some files held (no override): the pointer itself was refreshed.
    calls, cache_dir, proc = _setup(tmp_path, monkeypatch, changed=False)
    proc.code = 3
    assert ah.reconnect_now("moving", "agent", cache_dir=cache_dir) == "reconnected"


# 1. cooldown on any attempt
def test_a_failed_reconnect_now_starts_the_cooldown(tmp_path, monkeypatch):
    calls, cache_dir, _ = _setup(tmp_path, monkeypatch, changed=False)
    assert ah.reconnect_now("moving", "agent", cache_dir=cache_dir) == "failed"
    assert ah.reconnect_now("moving", "agent", cache_dir=cache_dir) == "cooldown"
    assert len(calls) == 1


def test_a_timed_out_reconnect_now_starts_the_cooldown(tmp_path, monkeypatch):
    calls, cache_dir, Proc = _setup(tmp_path, monkeypatch, changed=False)
    def slow(self, timeout=None):
        raise subprocess.TimeoutExpired("x", 1)
    Proc.wait = slow
    assert ah.reconnect_now("moving", "agent", cache_dir=cache_dir, timeout=1) == "timeout"
    assert ah.reconnect_now("moving", "agent", cache_dir=cache_dir) == "cooldown"


def test_a_failed_recipe_replay_starts_the_cooldown(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    seen = []
    recipe = {"pointer": "p", "dataset": "p", "principals": ["agent"], "structure": "flat",
              "sources": [{"path": "/x.md", "description": "d"}]}

    def memory(req):
        seen.append(req["action"])
        if req["action"] == "recipe":
            return {"status": "ok", "recipe": recipe}
        return {"status": "error", "reason": "secret-held"}
    assert ah.reconnect_recipe("p", "agent", memory=memory) == "failed"
    n = len(seen)
    assert ah.reconnect_recipe("p", "agent", memory=memory) == "held"  # a held file: not retried
    assert seen[n:] == ["recipe"]  # the second look read the recipe and went no further


def test_a_failed_drain_reconnect_starts_the_cooldown(tmp_path, monkeypatch):
    calls, cache_dir, _ = _setup(tmp_path, monkeypatch, changed=False)
    assert ah._drain_prepare("moving", "agent", "reconnect") == "failed"
    assert ah._drain_prepare("moving", "agent", "reconnect") == "cooldown"
    assert len(calls) == 1


# 2. no recorded principal
def test_a_changed_pointer_with_no_recorded_principal_is_healed_and_records_the_asker(tmp_path, monkeypatch):
    calls, cache_dir, _ = _setup(tmp_path, monkeypatch, principal=None)
    assert ah.maybe_heal("moving", "amazon") == "started"
    line = calls[0][2]
    assert "--principal amazon" in line and "--refresh" in line
    assert "--asker-fallback" not in line  # the report records who it now serves


def test_the_drain_heals_a_no_principal_pointer_too(tmp_path, monkeypatch):
    calls, cache_dir, Proc = _setup(tmp_path, monkeypatch, principal=None)
    Proc.code = 0
    assert ah._drain_prepare("moving", "amazon", "refresh") == "refreshed"
    assert "--principal" in calls[0] and calls[0][calls[0].index("--principal") + 1] == "amazon"


def test_prepare_args_uses_the_asker_only_when_no_principal_is_recorded():
    rep = {"pointer": "p", "roots": ["/r"]}
    assert rc.prepare_args(rep) is None
    assert rc.prepare_args(rep, "amazon")[-3:] == ["--principal", "amazon", "--refresh"]
    rep["principals"] = ["owner"]
    assert "amazon" not in rc.prepare_args(rep, "amazon")


# 3. fair cap
def test_a_pointer_that_healed_this_hour_queues_behind_a_waiting_one(tmp_path, monkeypatch):
    calls, cache_dir, _ = _setup(tmp_path, monkeypatch, names=("busy", "quiet"))
    now = time.time()
    with ah._state_txn("agent") as st:
        st["pointers"]["busy"] = now - 1200   # healed 20 min ago: out of cooldown, inside the hour
        st["pending"]["quiet"] = {"kind": "refresh", "ts": now - 900}
    ah.maybe_heal("busy", "agent")
    state = ah._load_state("agent")
    assert "busy" in state["pending"]                      # it queued
    assert list(state["pending"]).index("quiet") == 0       # behind the one that was waiting
    assert not any("--pointer busy" in " ".join(map(str, c)) for c in calls)


def test_a_capped_pointer_waits_in_the_queue_instead_of_being_dropped(tmp_path, monkeypatch):
    calls, cache_dir, _ = _setup(tmp_path, monkeypatch)
    now = time.time()
    with ah._state_txn("agent") as st:
        st["attempts"] = [now - 60] * ah.MAX_PER_HOUR
    assert ah.maybe_heal("moving", "agent") == "rate-limited"
    assert "moving" in ah._load_state("agent")["pending"]


def test_a_pointer_that_has_not_healed_this_hour_goes_first(tmp_path, monkeypatch):
    calls, cache_dir, _ = _setup(tmp_path, monkeypatch, names=("fresh1", "old"))
    with ah._state_txn("agent") as st:
        st["pending"]["old"] = {"kind": "refresh", "ts": time.time() - 900}
    assert ah.maybe_heal("fresh1", "agent") == "started"


# 4. refresh_changed outcome line
def _rc_setup(tmp_path, monkeypatch, principal="agent", code=0):
    calls, cache_dir, Proc = _setup(tmp_path, monkeypatch, changed=True, principal=principal)
    Proc.code = code
    monkeypatch.setattr(rc.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": code})())
    return calls


def _first(capsys):
    return capsys.readouterr().out.splitlines()[0]


def test_refresh_changed_outcomes_and_exit_codes(tmp_path, monkeypatch, capsys):
    _rc_setup(tmp_path, monkeypatch)
    assert rc.main([]) == 0 and _first(capsys).startswith("refreshed")


def test_refresh_changed_error_leads_and_exits_1(tmp_path, monkeypatch, capsys):
    _rc_setup(tmp_path, monkeypatch, code=1)
    assert rc.main([]) == 1 and _first(capsys).startswith("error")


def test_refresh_changed_needs_setup_leads_and_exits_2(tmp_path, monkeypatch, capsys):
    calls = _rc_setup(tmp_path, monkeypatch, principal=None)
    assert rc.main([]) == 2
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("needs-setup") and "--principal AGENT" in out and calls == []


def test_refresh_changed_needs_setup_is_not_hidden_by_an_error_elsewhere(tmp_path, monkeypatch, capsys):
    _rc_setup(tmp_path, monkeypatch, code=1)
    _pointer(rc.CACHE_DIR, "nobody", True, principal=None)
    assert rc.main([]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("error") and "needs-setup" in out.lower()


def test_refresh_changed_dry_run_verdicts(tmp_path, monkeypatch, capsys):
    _rc_setup(tmp_path, monkeypatch)
    assert rc.main(["--dry-run"]) == 3 and _first(capsys).startswith("stale")
    assert rc.main(["--dry-run", "--skip", "moving"]) == 0 and _first(capsys).startswith("fresh")


# 5. shadow_repair is not on the freshness path
def test_no_freshness_module_imports_shadow_repair():
    for name in ("auto_heal.py", "refresh_changed.py", "ask.py", "prepare_bulk.py"):
        tree = ast.parse((SKILL / name).read_text())
        mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not {"shadow_repair", "paid_replay", "scorecard"} & mods, name


# 6. privacy: a stand-in asker is recorded only when every part connected
def test_a_stand_in_asker_never_gains_a_part_it_cannot_see(tmp_path, monkeypatch, capsys):
    import sys as _sys
    _sys.path.insert(0, str(SKILL))
    import prepare_bulk as pb
    root = tmp_path / "root"; root.mkdir()
    cache_dir = tmp_path / "cache"; cache_dir.mkdir()
    monkeypatch.setattr(pb, "CACHE_DIR", cache_dir)
    entries = {}
    for i in range(2):
        f = root / f"f{i}.md"; f.write_text(f"# F{i}\n")
        entries[str(f)] = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "description": f"d{i}.",
                           "question": f"What is f{i}?", "verdict": "SUPPORTED", "confidence": 0.95,
                           "pass": True, "checkedAt": "2026-01-01T00:00:00"}
    (cache_dir / "x.json").write_text(json.dumps(entries))
    report = {"pointer": "x", "roots": [str(root)], "approved": list(entries), "excludes": [], "noRecurse": True,
              "limit": 1}  # split in two parts, no principal recorded
    (cache_dir / "x-report.json").write_text(json.dumps(report))
    registry = {"x": ["alice"], "x-2": ["alice", "bob"]}  # bob sees only part 2 (the one his lookup hit)

    def memory(req):
        act, ptr = req.get("action"), req.get("pointer")
        if act == "panel":
            return {"pointers": [p for p, who in registry.items() if req["principal"] in who]}
        if act == "register":
            registry[ptr] = list(req["principals"]); return {"status": "registered"}
        if act == "connect":
            if ptr in registry and not req.get("replace"):
                return {"status": "error", "reason": "already-connected"}
            if ptr in registry and not set(registry[ptr]) <= set(req["principals"]):
                return {"status": "error", "reason": "scope-change", "registeredPrincipals": registry[ptr]}
            if "reviewed" not in req:
                return {"status": "preparation-required", "sources": [
                    {"path": s["path"], "sha256": hashlib.sha256(Path(s["path"]).read_bytes()).hexdigest()}
                    for s in req["sources"]]}
            registry[ptr] = list(req["principals"]); return {"status": "registered", "pointer": ptr, "sources": req["sources"]}
        return {"pointers": []}
    monkeypatch.setattr(pb, "memory", memory)
    monkeypatch.setattr(pb, "writer", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no writer")))
    argv = ["prepare_bulk.py", "--pointer", "x", "--principal", "bob", "--refresh", "--no-findability"]
    for _ in range(2):  # bob's heal runs twice: the second must not widen part 1 either
        monkeypatch.setattr(_sys, "argv", argv)
        assert pb.main() == 1  # part 1 could not connect for bob
        rep = json.loads((cache_dir / "x-report.json").read_text())
        assert "principal" not in rep and "principals" not in rep
        assert registry["x"] == ["alice"]
