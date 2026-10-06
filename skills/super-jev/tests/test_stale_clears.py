#!/usr/bin/env python3
"""A set is stale only while the index cannot serve it as today's path would, and the flag clears once that stops
being true. An unreviewed file the walk finds is served by neither path, so it never makes a set stale; a changed
reviewed file does, until it is handled. An edited file is still served, and scanned once per ask.
Made-up files and a stub judge, no network, no spend.

    python3 -m pytest skills/super-jev/tests/test_stale_clears.py -q
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_index_read_path import PLANTED, PRINCIPAL, Rig, ask, ask_it, build, flag, sync, top5  # noqa: E402
from test_file_index import _entry  # noqa: E402
from file_index import FileIndex  # noqa: E402


@pytest.fixture
def seeded(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    files = []
    for i in range(5):
        f = d / f"note{i}.md"
        f.write_text(f"made up note number {i}\n")
        files.append(f)
    entries = {str(f): _entry(f) for f in files}
    idx = FileIndex("a", db_path=tmp_path / "a.sqlite")
    idx.update("p", entries, [str(d)])  # the seeding pass
    return d, files, entries, idx


def test_unreviewed_new_file_never_makes_a_set_stale(seeded):
    d, _files, entries, idx = seeded
    new = d / "fresh.md"
    new.write_text("brand new, never reviewed\n")
    r = idx.update("p", entries)
    assert r["new"] == [str(new)] and not r["stale"] and not idx.is_stale("p")
    new.write_text("brand new, edited again, still never reviewed\n")
    assert not idx.update("p", entries)["stale"] and not idx.is_stale("p")
    new.unlink()
    assert not idx.update("p", entries)["stale"] and not idx.is_stale("p")


def test_a_stuck_flag_clears_on_a_pass_with_no_change(seeded):
    _d, _files, entries, idx = seeded
    idx.db.execute("UPDATE pointers SET stale=1 WHERE pointer='p'")  # left by an earlier pass that counted a new file
    idx.db.commit()
    assert idx.is_stale("p")
    idx.update("p", entries)
    assert not idx.is_stale("p")


def test_a_changed_reviewed_file_is_stale_until_handled(seeded):
    _d, files, entries, idx = seeded
    original = files[2].read_text()
    files[2].write_text("edited bytes, never reviewed\n")
    assert idx.update("p", entries)["stale"] and idx.is_stale("p")
    assert idx.update("p", entries)["changed"] == [] and idx.is_stale("p")  # no new change, but the edit is still there
    idx.set_panel([{"pointer": "p", "snapshotStatus": "ready", "generation": 1}])
    assert idx.panel_rows()[0]["snapshotStatus"] == "refresh-required"  # the user still hears a refresh is needed
    files[2].write_text(original)  # handled: back to the reviewed bytes
    idx.update("p", entries)
    idx.update("p", entries)
    assert not idx.is_stale("p") and idx.count(pointer="p") == 5
    assert idx.panel_rows()[0]["snapshotStatus"] == "ready"


def test_a_read_side_mismatch_is_not_cleared_by_the_updater(seeded):
    _d, _files, entries, idx = seeded
    idx.mark_stale("p")  # an ask saw a served file differ; the updater may see no stat change
    idx.update("p", entries)
    assert idx.is_stale("p")


def test_a_read_side_mismatch_survives_a_real_change_on_another_file(seeded):
    _d, files, entries, idx = seeded
    idx.mark_stale("p")
    original = files[1].read_text()
    files[1].write_text("edited bytes, never reviewed\n")  # a real change on a different reviewed file
    assert idx.update("p", entries)["stale"]
    files[1].write_text(original)
    idx.update("p", entries)
    idx.update("p", entries)  # a quiet pass
    assert idx.db.execute("SELECT stale FROM pointers WHERE pointer='p'").fetchone()[0] == 2


class _MarkMidPass:
    """The index's connection, with an ask's mark_stale landing between the updater's read of `stale` and its write."""
    def __init__(self, db):
        self._db, self.marked = db, False

    def __getattr__(self, name):
        return getattr(self._db, name)

    def execute(self, sql, *a):
        if sql.startswith("INSERT INTO pointers") and not self.marked:
            self.marked = True
            self._db.execute("UPDATE pointers SET stale=2 WHERE pointer='p'")
        return self._db.execute(sql, *a)


def test_the_updater_never_overwrites_a_mark_made_mid_pass(seeded):
    _d, _files, entries, idx = seeded
    db = idx.db
    idx.db = _MarkMidPass(db)
    try:
        idx.update("p", entries)
        assert idx.db.marked
    finally:
        idx.db = db
    assert db.execute("SELECT stale FROM pointers WHERE pointer='p'").fetchone()[0] == 2


def _stage(sdir):
    return json.loads(Path(sdir / "traces.jsonl").read_text().splitlines()[-1])["stages"]["index"]


def test_a_set_with_only_new_files_is_served_from_the_index(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    new = notes / "p0" / "unreviewed.md"
    new.write_text("# unreviewed\nthe zorblax quenth shipment was moved to a frobnitz depot.\n")
    os.utime(sdir / ask.INDEX_WALK_STAMP, (0, 0))  # the walk is due: this sync finds the new file
    sync(sdir)
    i = FileIndex(PRINCIPAL, sdir / "index.sqlite")
    assert i.db.execute("SELECT reason FROM seen WHERE path=?", (str(new),)).fetchone() == ("new",)
    assert not i.is_stale("p0")
    i.close()
    q = PLANTED[0][0] + " frobnitz depot"
    flag(monkeypatch, False)
    off = ask_it(q, sdir, capsys)[1]
    flag(monkeypatch, True)
    on = ask_it(q, sdir, capsys)[1]
    assert "unreviewed.md" not in off and "unreviewed.md" not in on  # served by neither path
    assert top5(on) == top5(off) and any("zorblax.md" in p for p in top5(on))
    st = _stage(sdir)
    assert st["used"] is True and not (st.get("fallback") or {}).get("pointers"), st


def test_an_edited_file_is_served_and_scanned_once_per_ask(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    target = next(notes.rglob("zorblax.md"))
    target.write_text("# zorblax\nzorblax quenth shipment arrives on the ninth of June.\n")
    sync(sdir)
    scans = []
    real = ask.refresh_would_admit
    monkeypatch.setattr(ask, "refresh_would_admit", lambda path, ptr: scans.append(path) or real(path, ptr))
    flag(monkeypatch, True)
    for n in (1, 2):
        out = ask_it(PLANTED[0][0], sdir, capsys)[1]
        assert any("zorblax.md" in p for p in top5(out)), out
        assert scans.count(str(target)) == n, scans  # once per ask, not carried into the next ask
