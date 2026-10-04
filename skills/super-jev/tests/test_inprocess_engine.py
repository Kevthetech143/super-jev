"""Engine actions run inside ask.py's own process (no interpreter chain per memory() call).
Made-up state from setup.py's own config and one made-up note; no Jev, no network.

    python3 -m pytest skills/super-jev/tests/test_inprocess_engine.py -q
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SKILL = REPO / "skills" / "super-jev"
EXP = REPO / "experiments" / "verified-pointer-memory"
Q = "What is the Acme refund window?"


@pytest.fixture
def state(tmp_path, monkeypatch):
    for name in ("SUPERJEV_REPO", "SUPERJEV_JUDGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    subprocess.run([sys.executable, str(SKILL / "setup.py")], capture_output=True, text=True,
                   cwd=str(tmp_path), timeout=60)
    config_file = tmp_path / "state" / "_memory" / "config.json"
    assert config_file.is_file()
    note = tmp_path / "notes" / "acme-refunds.md"
    note.parent.mkdir()
    note.write_text("# Acme refund window\nAcme customers can return an order within 30 days of delivery.\n")
    monkeypatch.syspath_prepend(str(EXP))
    from cli import load_config
    from path_connect import connect
    config = load_config(config_file)
    draft = connect({"pointer": "acme", "principals": ["ann"], "sources": [{"path": str(note)}]}, config)
    done = connect({"pointer": "acme", "principals": ["ann"], "reviewed": True,
                    "navigationSHA": draft["navigationSHA"],
                    "sources": [{"path": x["path"], "sha256": x["sha256"]} for x in draft["sources"]]}, config)
    assert done.get("status") == "registered", done
    spec = importlib.util.spec_from_file_location("ask_inprocess", SKILL / "ask.py")
    ask = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ask)
    return ask, note


def requests(note):
    return [
        {"action": "panel", "principal": "ann"},
        {"action": "panel", "principal": "bob"},
        {"action": "cached", "principal": "ann", "question": "what is the acme refund window"},
        {"action": "sources", "pointer": "acme", "principal": "ann", "offset": 0, "limit": 100},
        {"action": "recipe", "pointer": "acme", "principal": "ann"},
        {"action": "sources", "pointer": "acme", "principal": "bob"},
        {"action": "navigate", "pointer": "nope", "principal": "ann", "question": Q},
        {"action": "navigate-many", "pointers": ["nope"], "principal": "ann", "question": Q},
        {"action": "cached", "principal": "ann"},          # missing field -> error
        {"action": "no-such-action", "principal": "ann"},  # refused
    ]


def test_engine_actions_spawn_nothing(state, monkeypatch):
    ask, note = state
    spawns = []

    def no_spawn(*a, **k):
        spawns.append(a)
        raise AssertionError("engine action spawned a process")
    monkeypatch.setattr(subprocess, "Popen", no_spawn)
    monkeypatch.setattr(subprocess, "run", no_spawn)
    ask.ENGINE_PATHS.clear()
    for req in requests(note):
        ask.memory(req)
    assert spawns == []
    assert dict(ask.ENGINE_PATHS) == {"inprocess": len(requests(note))}


def test_outputs_match_the_subprocess_path(state, monkeypatch):
    ask, note = state
    inproc = [ask.memory(r) for r in requests(note)]
    assert ask.ENGINE_PATHS["inprocess"] >= len(inproc)
    monkeypatch.setattr(ask, "_memory_inprocess", lambda req: None)
    sub = [ask.memory(r) for r in requests(note)]
    assert ask.ENGINE_PATHS["subprocess"] == len(sub)
    assert json.dumps(inproc, sort_keys=True) == json.dumps(sub, sort_keys=True)
    assert inproc[0]["status"] == "ok" and inproc[0]["pointers"][0]["pointer"] == "acme"


def test_a_secret_is_still_held_and_never_reaches_the_engine(state):
    ask, note = state
    with pytest.raises(ask.SecretHeld):
        ask.memory({"action": "cached", "principal": "ann", "question": "password: " + "hunter" + "2!"})


def test_falls_back_to_subprocess_when_the_engine_cannot_be_imported(state, monkeypatch, tmp_path):
    ask, note = state
    monkeypatch.setattr(ask, "_engine_module", lambda repo: None)
    ask.ENGINE_PATHS.clear()
    out = ask.memory({"action": "panel", "principal": "ann"})
    assert out["status"] == "ok" and dict(ask.ENGINE_PATHS) == {"subprocess": 1}


def test_trace_line_names_the_engine_path(state, tmp_path):
    ask, note = state
    ask.ENGINE_PATHS.clear()
    ask.memory({"action": "panel", "principal": "ann"})
    ask.write_trace(tmp_path / "t", kind="trace", question="q")
    line = json.loads((tmp_path / "t" / "traces.jsonl").read_text().splitlines()[-1])
    assert line["engine"] == {"inprocess": 1}


def test_concurrent_engine_calls_overlap_and_match_subprocess(state, monkeypatch):
    import threading
    import time
    ask, note = state
    n, nap = 4, 0.8
    req = {"action": "navigate", "pointer": "nope", "principal": "ann", "question": Q}
    with monkeypatch.context() as m:
        m.setattr(ask, "_memory_inprocess", lambda r: None)
        expected = ask.memory(req)
    # real in-process calls from threads return the same as the subprocess path
    outs = [None] * n

    def go(i):
        outs[i] = ask.memory(req)
    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(json.dumps(o, sort_keys=True) == json.dumps(expected, sort_keys=True) for o in outs)
    # a slow provider: calls must overlap, not queue
    target = ask._engine_target()
    cli = ask._engine_module(target[0])
    real = cli.run

    def slow(request, config):
        time.sleep(nap)
        return real(request, config)
    monkeypatch.setattr(cli, "run", slow)
    t0 = time.time()
    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    wall = time.time() - t0
    assert wall < n * nap * 0.6, wall
    assert all(json.dumps(o, sort_keys=True) == json.dumps(expected, sort_keys=True) for o in outs)


def test_engine_target_installed_wrapper_and_bypasses(tmp_path, monkeypatch):
    for name in ("SUPERJEV_REPO", "SUPERJEV_MEMORY_WRAPPER_ACTIVE", "SUPERJEV_STATE_DIR"):
        monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location("ask_target", SKILL / "ask.py")
    ask = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ask)
    skill_dir = tmp_path / "root" / "skills" / "super-jev"
    skill_dir.mkdir(parents=True)
    cfg = tmp_path / "fixed" / "config.json"
    (skill_dir / "memory.sh").write_text(f'#!/bin/sh\nexec python3 x/cli.py --config {cfg} "$@"\n')
    monkeypatch.setattr(ask, "__file__", str(skill_dir / "ask.py"))
    assert ask._engine_target() == (skill_dir.parents[1], cfg)
    monkeypatch.setenv("SUPERJEV_MEMORY_WRAPPER_ACTIVE", "1")
    repo, conf = ask._engine_target()
    assert repo == skill_dir.parents[1] and conf != cfg
    monkeypatch.delenv("SUPERJEV_MEMORY_WRAPPER_ACTIVE")
    monkeypatch.setenv("SUPERJEV_REPO", str(tmp_path / "other"))
    assert ask._engine_target()[0] == tmp_path / "other" and ask._engine_target()[1] != cfg
