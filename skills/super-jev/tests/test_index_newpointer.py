#!/usr/bin/env python3
"""A newly connected pointer is indexed at once: a first connect of a pointer the registry did not hold, and an ask
that finds a searched set "not indexed", both start the updater inside the 10-minute throttle; an unchanged re-connect
still goes through the throttle and neither starts it under a replay or pytest. Made-up state, stubs, no network.

    python3 -m pytest skills/super-jev/tests/test_index_newpointer.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_index_read_path import PLANTED, PRINCIPAL, Rig, ask, ask_it, build, flag, sync  # noqa: E402
from test_prepare_bulk import base_argv  # noqa: E402
import prepare_bulk as pb  # noqa: E402

pytestmark = pytest.mark.real_toc


def asked(tmp_path, monkeypatch, capsys, *, new_set, replay=False):
    """One flag-on ask over a synced corpus, stamp fresh. new_set adds a connected pointer the index never saw."""
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    if new_set:
        names.append("fresh-set")
    if replay:
        monkeypatch.setenv("SUPERJEV_REPLAY", "1")
    spawned = []
    monkeypatch.setattr(ask, "spawn_index_updater", lambda p: spawned.append(p))
    flag(monkeypatch, True)
    (sdir / ask.INDEX_STAMP).touch()
    ask_it(PLANTED[0][0], sdir, capsys)
    return spawned


def test_an_ask_that_finds_a_not_indexed_set_spawns_inside_the_throttle(tmp_path, monkeypatch, capsys):
    assert asked(tmp_path, monkeypatch, capsys, new_set=True) == [PRINCIPAL]


def test_an_ask_with_every_set_indexed_still_respects_the_throttle(tmp_path, monkeypatch, capsys):
    assert asked(tmp_path, monkeypatch, capsys, new_set=False) == []


def test_an_ask_under_replay_never_spawns_for_a_not_indexed_set(tmp_path, monkeypatch, capsys):
    assert asked(tmp_path, monkeypatch, capsys, new_set=True, replay=True) == []


def connect(tmp_path, monkeypatch, *, known_before, runs=1):
    """Connect one pointer with a stubbed registry; returns the bypass_throttle value of each kick."""
    root = tmp_path / "root"; root.mkdir()
    (root / "a.md").write_text("# A\nbody\n")
    (tmp_path / "cache").mkdir()
    state = {"known": known_before}

    def memory(req):
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "my-records", "generation": 1}] if state["known"] else []}
        if "reviewed" not in req:
            return {"status": "preparation-required",
                    "sources": [{"path": s["path"], "sha256": pb.sha(Path(s["path"]))} for s in req["sources"]]}
        state["known"] = True
        return {"status": "registered", "pointer": req["pointer"], "sources": req["sources"]}
    got = []
    monkeypatch.setitem(sys.modules, "share_pointers", type(sys)("share_pointers"))
    sys.modules["share_pointers"].share_defaults = lambda principals, memory=None: None
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: {i["path"]: {"description": "A note.", "question": "q?"} for i in items})
    monkeypatch.setattr(pb, "gate", lambda path, claim, *a, **k: {"state": "SUPPORTED", "confidence": 0.95, "secs": 0})
    monkeypatch.setattr(pb, "gate_many", lambda path, claims, *a, **k: [{"state": "SUPPORTED", "confidence": 0.95, "secs": 0} for _ in claims], raising=False)
    monkeypatch.setattr(pb, "memory", memory)
    monkeypatch.setattr(pb, "kick_index_updater", lambda principals, bypass_throttle=False: got.append(bypass_throttle))
    monkeypatch.setattr(sys, "argv", base_argv(root, principal="agent"))
    for _ in range(runs):
        assert pb.main() == 0
    return got


def test_a_first_connect_of_a_new_pointer_bypasses_the_throttle(tmp_path, monkeypatch):
    assert connect(tmp_path, monkeypatch, known_before=False) == [True]


def test_a_plain_reconnect_of_an_unchanged_pointer_keeps_the_throttle(tmp_path, monkeypatch):
    assert connect(tmp_path, monkeypatch, known_before=True) == [False]


def kicks(tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    seen = []
    monkeypatch.setattr(pb.subprocess, "Popen", lambda cmd, **k: seen.append(cmd))
    (tmp_path / "state" / PRINCIPAL).mkdir(parents=True)
    (tmp_path / "state" / PRINCIPAL / ask.INDEX_STAMP).touch()
    pb.kick_index_updater([PRINCIPAL], **kw)
    return seen


def test_the_connect_kick_starts_the_updater_for_a_new_pointer_despite_the_stamp(tmp_path, monkeypatch):
    assert len(kicks(tmp_path, monkeypatch, bypass_throttle=True)) == 1


def test_the_connect_kick_is_skipped_in_a_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERJEV_REPLAY", "1")
    assert kicks(tmp_path, monkeypatch, bypass_throttle=True) == []


def test_the_connect_kick_is_skipped_under_pytest(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(pb.subprocess, "Popen", lambda cmd, **k: seen.append(cmd))
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    pb.kick_index_updater([PRINCIPAL], bypass_throttle=True)
    assert seen == []
