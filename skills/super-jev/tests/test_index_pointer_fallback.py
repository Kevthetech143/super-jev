#!/usr/bin/env python3
"""Flag on (SUPERJEV_INDEX=1) loses no answer: the index answers only for a pointer it holds completely and currently
(same generation as the registry, files held, not stale); every other pointer is served by today's path in the same
ask, named in the trace as index.fallback.pointers. Made-up state and a stub judge, no network, no spend.

    python3 -m pytest skills/super-jev/tests/test_index_pointer_fallback.py -q
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_index_read_path import PLANTED, PRINCIPAL, Rig, ask, ask_it, build, flag, sync, top5  # noqa: E402
from file_index import FileIndex  # noqa: E402

pytestmark = pytest.mark.real_toc


def stage_of(sdir):
    return json.loads(Path(sdir / "traces.jsonl").read_text().splitlines()[-1])["stages"]["index"]


def db(sdir):
    return FileIndex(PRINCIPAL, sdir / "index.sqlite")


def same_top5(monkeypatch, capsys, sdir, question, answer_file):
    """Flag off, then flag on: the same top 5, the answer in it. Returns the flag-on trace stage."""
    flag(monkeypatch, False)
    base = top5(ask_it(question, sdir, capsys)[1])
    assert any(answer_file in p for p in base)
    flag(monkeypatch, True)
    got = top5(ask_it(question, sdir, capsys)[1])
    assert got == base
    return stage_of(sdir)


def owner_of_planted(notes, fname):
    return next(notes.rglob(fname)).parent.name


def test_stale_pointer_is_served_by_todays_path(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    target = next(notes.rglob("zorblax.md"))
    target.write_text("# zorblax\nzorblax quenth shipment arrives on the ninth of June.\n")
    sync(sdir)  # the updater saw the edit: its pointer is stale
    stage = same_top5(monkeypatch, capsys, sdir, PLANTED[0][0], "zorblax.md")
    assert stage["used"] is True
    assert [r.split(":")[0] for r in stage["fallback"]["pointers"]] == [target.parent.name]
    assert "stale" in stage["fallback"]["pointers"][0]


def test_zero_file_pointer_is_served_by_todays_path(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    owner = owner_of_planted(notes, "plimbus.md")
    i = db(sdir)  # the pointer lost every file row (the cause seen live: its paths were taken by another pointer)
    for t in ("files", "seen", "toc"):
        i.db.execute(f"DELETE FROM {t} WHERE " + ("path IN (SELECT path FROM files WHERE pointer=?)" if t == "toc" else "pointer=?"), (owner,))
    i.db.commit(); i.close()
    stage = same_top5(monkeypatch, capsys, sdir, PLANTED[1][0], "plimbus.md")
    assert [r.split(":")[0] for r in stage["fallback"]["pointers"]] == [owner]
    assert "0 files indexed" in stage["fallback"]["pointers"][0]


def test_generation_mismatch_is_served_by_todays_path(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    owner = owner_of_planted(notes, "wumpet.md")
    # the registry refreshed the pointer after the last sync: its generation is no longer the one indexed
    monkeypatch.setattr(ask, "engine_generations", lambda: {n: (2 if n == owner else 1) for n in names})
    stage = same_top5(monkeypatch, capsys, sdir, PLANTED[2][0], "wumpet.md")
    assert stage["fallback"]["pointers"] == [f"{owner}: generation mismatch (index holds an older refresh)"]


def test_a_pointer_a_walk_emptied_is_held_complete_after_the_fix(tmp_path, monkeypatch, capsys):
    """The live cause: a pointer connected over a folder (no review of its own) walked the same folder after the
    reviewed pointer and took the file rows away from it. Now a walk never takes a path another pointer holds."""
    notes, names, sdir = build(tmp_path, monkeypatch, 30)
    Rig(monkeypatch, notes, names)
    cdir = ask.prepare_bulk.CACHE_DIR
    sweep = next(notes.rglob("glorpen.md"))
    (cdir / "zz-sweep.json").write_text(json.dumps({str(sweep): {"sha256": hashlib.sha256(sweep.read_bytes()).hexdigest(), "pass": False}}))
    (cdir / "zz-sweep-report.json").write_text(json.dumps({"pointer": "zz-sweep", "principals": [PRINCIPAL], "roots": [str(notes / sweep.parent.name)]}))
    allp = [*names, "zz-sweep"]
    monkeypatch.setattr(ask, "engine_visible", lambda principal: set(allp))
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "cache-miss", "checked": []} if req["action"] == "cached" else
                        {"pointers": [{"pointer": n, "snapshotStatus": "ready", "generation": 1} for n in allp]})
    sync(sdir)
    i = db(sdir)
    for n in names:
        assert i.count(pointer=n) == len(ask.prepare_bulk.load_cache_files(n)), n  # no pointer lost its files to the walk
    assert i.coverage()[sweep.parent.name]["complete"] == 1
    i.close()
    flag(monkeypatch, True)
    out = ask_it(PLANTED[3][0], sdir, capsys)[1]
    assert "glorpen.md" in out and stage_of(sdir)["used"] is True


def test_mixed_ask_one_good_and_one_stale_pointer(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 60)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    stale_one = owner_of_planted(notes, "zorblax.md")
    good_one = owner_of_planted(notes, "plimbus.md")
    assert stale_one != good_one
    next(notes.rglob("zorblax.md")).write_text("# zorblax\nzorblax quenth shipment arrives on the ninth of June.\n")
    sync(sdir)
    for question, fname in (PLANTED[0][0], "zorblax.md"), (PLANTED[1][0], "plimbus.md"):
        stage = same_top5(monkeypatch, capsys, sdir, question, fname)
        assert stage["used"] is True and [r.split(":")[0] for r in stage["fallback"]["pointers"]] == [stale_one]
    flag(monkeypatch, True)
    both = ask_it("zorblax quenth shipment plimbus frontier gate", sdir, capsys)[1]
    flag(monkeypatch, False)
    base = ask_it("zorblax quenth shipment plimbus frontier gate", sdir, capsys)[1]
    assert top5(both) == top5(base)


def test_complete_current_pointers_are_not_reported(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    flag(monkeypatch, True)
    ask_it(PLANTED[4][0], sdir, capsys)
    stage = stage_of(sdir)
    assert stage["used"] is True and "fallback" not in stage


def test_index_made_before_the_completeness_flag_is_not_trusted(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    i = db(sdir)
    i.db.execute("UPDATE pointers SET complete=NULL"); i.db.commit(); i.close()
    stage = same_top5(monkeypatch, capsys, sdir, PLANTED[3][0], "glorpen.md")
    assert len(stage["fallback"]["pointers"]) == len(names)
    assert all("files missing from the index" in r for r in stage["fallback"]["pointers"])


def test_index_locked_by_the_updater_mid_ask_falls_back_in_the_same_ask(tmp_path, monkeypatch, capsys):
    """Found live: an ask that overlapped the detached updater died with "database is locked" (no answer at all)."""
    import sqlite3
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    Rig(monkeypatch, notes, names)
    sync(sdir)

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(FileIndex, "fts_top", locked)  # the shortlist read is guarded on its own ...
    monkeypatch.setattr(FileIndex, "candidates", locked)  # ... so the S3a candidate read is the one that must fall back
    monkeypatch.setattr(FileIndex, "mark_stale", locked)
    stage = same_top5(monkeypatch, capsys, sdir, PLANTED[2][0], "wumpet.md")
    assert stage["used"] is False and stage["fallback"].startswith("index read failed (OperationalError")
