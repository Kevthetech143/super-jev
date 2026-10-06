#!/usr/bin/env python3
"""After a refresh bumps a pointer's generation, the index is re-seeded at once: an ask that finds the mismatch and a
refresh that changed a generation both start the updater inside the 10-minute throttle, and a refresh landing while the
updater runs queues exactly one more pass. Made-up state, stubs, no network, no spend.

    python3 -m pytest skills/super-jev/tests/test_index_catchup.py -q
"""
import fcntl
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_index_read_path import PLANTED, PRINCIPAL, Rig, ask, ask_it, build, flag, sync  # noqa: E402
from test_prepare_bulk import base_argv  # noqa: E402
import prepare_bulk as pb  # noqa: E402

pytestmark = pytest.mark.real_toc


def asked(tmp_path, monkeypatch, capsys, *, generations=None, edit=False):
    """One flag-on ask over a synced corpus with a fresh stamp (inside the throttle). Returns the spawns."""
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    if generations:
        monkeypatch.setattr(ask, "engine_generations", lambda: {n: generations(n) for n in names})
    if edit:
        next(notes.rglob("zorblax.md")).write_text("# zorblax\nzorblax quenth shipment arrives on the ninth of June.\n")
    spawned = []
    monkeypatch.setattr(ask, "spawn_index_updater", lambda p: spawned.append(p))
    flag(monkeypatch, True)
    (sdir / ask.INDEX_STAMP).touch()
    ask_it(PLANTED[0][0], sdir, capsys)
    return spawned


def test_a_generation_mismatch_ask_spawns_inside_the_throttle(tmp_path, monkeypatch, capsys):
    assert asked(tmp_path, monkeypatch, capsys, generations=lambda n: 2) == [PRINCIPAL]


def test_a_served_file_sha_mismatch_still_spawns(tmp_path, monkeypatch, capsys):
    assert asked(tmp_path, monkeypatch, capsys, edit=True) == [PRINCIPAL]


def test_an_ask_with_no_mismatch_inside_the_throttle_does_not_spawn(tmp_path, monkeypatch, capsys):
    assert asked(tmp_path, monkeypatch, capsys) == []


def kicks(tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    seen = []
    monkeypatch.setattr(pb.subprocess, "Popen", lambda cmd, **k: seen.append(cmd))
    (tmp_path / "state" / PRINCIPAL).mkdir(parents=True)
    (tmp_path / "state" / PRINCIPAL / ask.INDEX_STAMP).touch()  # inside the throttle
    pb.kick_index_updater([PRINCIPAL], **kw)
    return seen


def test_kick_after_a_generation_change_spawns_despite_the_stamp(tmp_path, monkeypatch):
    assert len(kicks(tmp_path, monkeypatch, bypass_throttle=True)) == 1


def test_kick_without_a_generation_change_is_still_throttled(tmp_path, monkeypatch):
    assert kicks(tmp_path, monkeypatch) == []


def test_kick_is_skipped_in_a_replay_even_after_a_generation_change(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERJEV_REPLAY", "1")
    assert kicks(tmp_path, monkeypatch, bypass_throttle=True) == []


@pytest.mark.parametrize("bump,expect", [(True, True), (False, False)])
def test_a_refresh_bypasses_only_when_a_generation_changed(tmp_path, monkeypatch, bump, expect):
    root = tmp_path / "root"; root.mkdir()
    (root / "a.md").write_text("# A\nbody\n")
    (tmp_path / "cache").mkdir()
    gen = {"n": 1}

    def memory(req):
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "my-records", "generation": gen["n"]}]}
        if "reviewed" not in req:
            return {"status": "preparation-required",
                    "sources": [{"path": s["path"], "sha256": pb.sha(Path(s["path"]))} for s in req["sources"]]}
        gen["n"] += 1 if bump else 0
        return {"status": "registered", "pointer": req["pointer"], "sources": req["sources"]}
    got = []
    sys.modules["share_pointers"] = type(sys)("share_pointers")
    sys.modules["share_pointers"].share_defaults = lambda principals, memory=None: None
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: {i["path"]: {"description": "A note.", "question": "q?"} for i in items})
    monkeypatch.setattr(pb, "gate", lambda path, claim, *a, **k: {"state": "SUPPORTED", "confidence": 0.95, "secs": 0})
    monkeypatch.setattr(pb, "gate_many", lambda path, claims, *a, **k: [{"state": "SUPPORTED", "confidence": 0.95, "secs": 0} for _ in claims], raising=False)
    monkeypatch.setattr(pb, "memory", memory)
    monkeypatch.setattr(pb, "kick_index_updater", lambda principals, bypass_throttle=False: got.append(bypass_throttle))
    monkeypatch.setattr(sys, "argv", base_argv(root, principal="agent"))
    assert pb.main() == 0
    assert got == [expect]


def test_a_second_updater_marks_the_lock_holder_to_run_one_more_pass(tmp_path, monkeypatch):
    sdir = tmp_path / "state"
    sdir.mkdir()
    held = open(sdir / ask.INDEX_LOCK, "a")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # an updater is running
    assert ask.index_sync(PRINCIPAL, sdir) == 0 and (sdir / ask.INDEX_RERUN).exists()
    held.close()


def test_the_running_updater_makes_exactly_one_extra_pass_for_a_marker(tmp_path, monkeypatch):
    sdir = tmp_path / "state"
    passes = []

    def fake_pass(principal, d):
        passes.append(1)
        (d / ask.INDEX_RERUN).touch()  # a refresh lands during every pass: still one extra, never a loop
        return 0
    monkeypatch.setattr(ask, "_index_sync", fake_pass)
    assert ask.index_sync(PRINCIPAL, sdir) == 0
    assert len(passes) == 2


def test_no_marker_means_a_single_pass(tmp_path, monkeypatch):
    sdir = tmp_path / "state"
    passes = []
    monkeypatch.setattr(ask, "_index_sync", lambda p, d: passes.append(1) or 0)
    assert ask.index_sync(PRINCIPAL, sdir) == 0
    assert len(passes) == 1
