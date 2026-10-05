#!/usr/bin/env python3
"""The index build on a big connected folder: reads never fail during a build, one updater at a time, progress
survives a stop, and the walk costs by change, not by links. Made-up folders, a stub judge, no network, no spend.

    python3 -m pytest skills/super-jev/tests/test_index_build.py -q
"""
import fcntl
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_index_read_path as rp  # noqa: E402  (its corpus builder and stub engine)

ask = rp.ask
import file_index  # noqa: E402
import prepare_bulk as pb  # noqa: E402
from file_index import FileIndex  # noqa: E402

pytestmark = pytest.mark.real_toc


def last_stage(sdir):
    return json.loads(Path(sdir / "traces.jsonl").read_text().splitlines()[-1])["stages"]["index"]


# (a) reads never fail during a build

def test_reader_during_an_open_write_transaction_still_uses_the_index(tmp_path, monkeypatch, capsys):
    notes, names, sdir = rp.build(tmp_path, monkeypatch, 30)
    rp.Rig(monkeypatch, notes, names)
    rp.sync(sdir)
    rp.flag(monkeypatch, True)
    w = sqlite3.connect(str(sdir / "index.sqlite"), isolation_level=None)
    w.execute("BEGIN IMMEDIATE")
    w.execute("INSERT OR REPLACE INTO meta VALUES('probe','1')")  # the updater is mid-write
    try:
        _rc, out = rp.ask_it(rp.PLANTED[2][0], sdir, capsys)
    finally:
        w.execute("ROLLBACK"); w.close()
    assert "wumpet.md" in out and last_stage(sdir)["used"] is True


def test_reader_locked_out_reports_busy_and_the_answer_still_comes(tmp_path, monkeypatch, capsys):
    notes, names, sdir = rp.build(tmp_path, monkeypatch, 30)
    rp.Rig(monkeypatch, notes, names)
    rp.sync(sdir)
    rp.flag(monkeypatch, True)
    monkeypatch.setattr(file_index, "BUSY_MS", 100)
    c = sqlite3.connect(str(sdir / "index.sqlite"))
    c.execute("PRAGMA journal_mode=DELETE"); c.close()  # an index made before WAL: a write lock stops its readers
    w = sqlite3.connect(str(sdir / "index.sqlite"), isolation_level=None)
    w.execute("BEGIN EXCLUSIVE")
    try:
        _rc, out = rp.ask_it(rp.PLANTED[2][0], sdir, capsys)
    finally:
        w.execute("ROLLBACK"); w.close()
    st = last_stage(sdir)
    assert st["used"] is False and st["fallback"] == "index busy (update running)" and "wumpet.md" in out


def test_index_unusable_words():
    assert ask.index_unusable(sqlite3.OperationalError("database is locked")) == "index busy (update running)"
    assert ask.index_unusable(sqlite3.DatabaseError("database disk image is malformed")).startswith("index corrupt")
    assert ask.index_unusable(sqlite3.DatabaseError("file is not a database")).startswith("index corrupt")
    assert ask.index_unusable(sqlite3.OperationalError("unable to open database file")).startswith("index unreadable")


def test_new_index_is_wal_with_a_busy_timeout(tmp_path):
    idx = FileIndex("t", tmp_path / "index.sqlite")
    assert idx.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert idx.db.execute("PRAGMA busy_timeout").fetchone()[0] == file_index.BUSY_MS


# (b) one updater, progress survives

def test_second_updater_exits_quietly_while_the_lock_is_held(tmp_path, monkeypatch):
    notes, names, sdir = rp.build(tmp_path, monkeypatch, 10)
    rig = rp.Rig(monkeypatch, notes, names)
    sdir.mkdir(parents=True, exist_ok=True)
    with open(sdir / "index-update.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # a running updater
        assert ask.index_sync(rp.PRINCIPAL, sdir) == 0
        assert rig.panel_calls == 0 and not (sdir / "index.sqlite").exists()
    rp.sync(sdir)  # released: the next one runs
    assert rig.panel_calls == 1 and (sdir / "index.sqlite").exists()
    rp.sync(sdir)  # and releases it again when it ends
    assert rig.panel_calls == 2


def test_stopped_run_keeps_its_committed_work_and_marks_only_its_pointers(tmp_path, monkeypatch):
    notes, names, sdir = rp.build(tmp_path, monkeypatch, 60)
    rp.Rig(monkeypatch, notes, names)
    monkeypatch.setattr(ask, "FTS_COMMIT_EVERY", 10)
    real, n = FileIndex.fts_put, [0]

    def dying(self, *a, **k):
        n[0] += 1
        if n[0] == 35:
            raise KeyboardInterrupt
        return real(self, *a, **k)
    monkeypatch.setattr(FileIndex, "fts_put", dying)
    with pytest.raises(KeyboardInterrupt):
        ask.index_sync(rp.PRINCIPAL, sdir)
    c = sqlite3.connect(str(sdir / "index.sqlite"))
    assert c.execute("SELECT COUNT(*) FROM fts_map").fetchone()[0] >= 30  # committed every 10 files
    assert c.execute("SELECT COUNT(*) FROM fts_pending").fetchone()[0] >= 1
    c.close()
    monkeypatch.setattr(FileIndex, "fts_put", real)
    rp.sync(sdir)  # the next run builds the rest, from where it stopped
    c = sqlite3.connect(str(sdir / "index.sqlite"))
    assert c.execute("SELECT COUNT(*) FROM fts_map").fetchone()[0] == 65
    assert c.execute("SELECT COUNT(*) FROM fts_pending").fetchone()[0] == 0
    c.close()


def test_fts_stays_usable_for_untouched_pointers_during_an_update(tmp_path, monkeypatch, capsys):
    notes, names, sdir = rp.build(tmp_path, monkeypatch, 30)
    rig = rp.Rig(monkeypatch, notes, names)
    rp.sync(sdir)
    rp.flag(monkeypatch, True)
    target = next(notes.rglob("zorblax.md"))  # planted in p0
    target.write_text("# zorblax\nzorblax quenth shipment arrives on the ninth of June.\n")
    idx = FileIndex(rp.PRINCIPAL, sdir / "index.sqlite")  # an updater part-way: p0 rewritten, FTS pass not yet run
    from prepare_bulk import load_cache_files
    idx.update("p0", entries=load_cache_files("p0"))
    idx.close()
    reader, panel, why, fb = ask.index_panel(rp.PRINCIPAL, sdir)
    try:
        assert why is None and list(fb) == ["p0"] and fb["p0"] == "index update running for it"
        assert reader.fts_usable(ask.WORD_INDEX_VERSION)[0] is True
    finally:
        reader.close()
    _rc, out = rp.ask_it(rp.PLANTED[1][0], sdir, capsys)  # plimbus lives in p1, untouched
    assert "plimbus.md" in out and last_stage(sdir)["used"] is True
    rp.sync(sdir)  # the update finishes: nothing pending
    c = sqlite3.connect(str(sdir / "index.sqlite"))
    assert c.execute("SELECT COUNT(*) FROM fts_pending").fetchone()[0] == 0
    c.close()


def test_kick_and_after_ask_start_the_one_updater_entry(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(pb.subprocess, "Popen", lambda cmd, **k: seen.append(cmd))
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    pb.kick_index_updater(["me"])
    assert seen[0][1].endswith("ask.py") and seen[0][-1] == "--index-update-if-on"
    src = Path(ask.__file__).read_text()
    assert 'if a[0] == "--index-update":\n        return index_sync(' in src and "index_sync(principal, state_dir(principal)) if index_enabled()" in src


# (c) cost proportional to change

def old_walk_one(root, pb):
    """The filter as it was: one test per link per file."""
    base = root.resolve()
    files, linked = pb.walk_md(root)
    bases = [base] + [t for t in linked if not pb.SKIP_PARTS.intersection(x.casefold() for x in t.parts)]
    for p in files:
        rp_ = p.resolve()
        if pb.credential_suffix(p.name) or pb.credential_suffix(rp_.name):
            continue
        if not any(rp_.is_relative_to(b) for b in bases):
            continue
        if any(".bak" in n or Path(n).stem == "logins" or Path(n).stem.endswith("-secret")
               for n in (p.name.casefold(), rp_.name.casefold())):
            continue
        b = next(b for b in bases if rp_.is_relative_to(b))
        if pb.skipped_folder(p.relative_to(root).parts[:-1] + rp_.relative_to(b).parts[:-1]):
            continue
        if p.name.startswith(".") or rp_.name.startswith("."):
            continue
        if pb.path_has_secret(p.name) or pb.path_has_secret(rp_.name):
            continue
        try:
            if rp_.stat().st_size > pb.CEILING_BYTES:
                continue
        except OSError:
            continue
        yield str(p)


def touch(p, text="# note\nplain words\n"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def tangle(tmp_path):
    """Nested roots, linked folders (inside, outside, into a skipped folder, a loop, a second path to one folder), a linked
    file, shared paths, and every name the filter drops."""
    top, out = tmp_path / "top", tmp_path / "outside"
    for n in ("a.md", "b/c.md", "b/d/e.md", "b/node_modules/x.md", "b/documents/y.md", "b/.hid/z.md", ".dot.md", "keep.bak.md",
              "logins.md", "api-secret.md", "creds.pem.md", "password-hunter2xyz-notes.md", "b/f.txt", "s/documents/inner.md", "s/ok.md"):
        touch(top / n)
    (top / "huge.md").write_text("x" * (pb.CEILING_BYTES + 5))
    for n in ("o1.md", "sub/o2.md", "sub/node_modules/o3.md", ".h/o4.md"):
        touch(out / n)
    touch(tmp_path / "docs-vault" / "documents" / "v.md")
    (top / "lnk_out").symlink_to(out)                         # a linked folder outside the root
    (top / "b" / "lnk_in").symlink_to(top / "b" / "d")        # a second path to a folder inside
    (top / "loop").symlink_to(top)                            # a link loop
    (top / "to_vault").symlink_to(tmp_path / "docs-vault" / "documents")  # a link into a skipped folder
    (top / "b" / "file_link.md").symlink_to(out / "o1.md")    # a linked file
    (top / "b" / "bad_link.md").symlink_to(tmp_path / "gone.md")
    return top


def test_walk_filter_matches_the_old_one(tangle):
    roots = [tangle, tangle / "b", tangle / "b" / "d", tangle / "s", tangle / "lnk_out"]
    fi = FileIndex("t", tangle.parent / "i.sqlite")
    for solo in (True, False):
        if not solo:
            fi.begin_round(roots)  # nested roots cut from the outer walk
        for r in roots:
            want = sorted(old_walk_one(r, pb))
            assert sorted(fi._walk_one(r, pb)) == want, (solo, r)
            assert want, r  # every root of the fixture holds something
    got = sorted(fi._walk_one(tangle, pb))
    assert str(tangle / "a.md") in got and str(tangle / "lnk_out" / "o1.md") in got  # a linked outside folder is walked
    assert str(tangle / "keep.bak.md") not in got and str(tangle / "b" / "documents" / "y.md") not in got


def test_nested_root_is_cut_from_the_outer_walk_not_walked_again(tangle, monkeypatch):
    fi = FileIndex("t", tangle.parent / "i.sqlite")
    calls = []
    real = pb.walk_md
    monkeypatch.setattr(pb, "walk_md", lambda root, *a, **k: (calls.append(str(root)), real(root, *a, **k))[1])
    clean = tangle.parent / "clean"
    for n in ("a.md", "b/c.md", "b/d/e.md", "s/t.md"):
        touch(clean / n)
    fi.begin_round([clean / "b", clean, clean / "b" / "d"])  # the inner roots come first in the list
    got = [sorted(fi._walk_one(r, pb)) for r in (clean / "b", clean, clean / "b" / "d")]
    assert calls == [str(clean)]
    assert got == [sorted(old_walk_one(r, pb)) for r in (clean / "b", clean, clean / "b" / "d")]


def test_a_root_holding_a_skipped_duplicate_folder_is_walked_on_its_own(tangle):
    fi = FileIndex("t", tangle.parent / "i.sqlite")
    fi.begin_round([tangle, tangle / "b"])  # b/lnk_in repeats b/d: skipped in b's walk, but its own walk is what the old code did
    for r in (tangle, tangle / "b"):
        assert sorted(fi._walk_one(r, pb)) == sorted(old_walk_one(r, pb))


def test_walk_filter_cost_follows_files_not_links(tmp_path):
    root, out = tmp_path / "big", tmp_path / "targets"
    for i in range(3000):  # 3k links, each to a folder of its own outside the root
        touch(out / f"t{i}" / "n.md")
        (root).mkdir(exist_ok=True)
        (root / f"l{i}").symlink_to(out / f"t{i}")
    for d in range(200):
        for i in range(85):
            touch(root / "plain" / f"d{d}" / f"n{i}.md")  # 17k plain files
    fi = FileIndex("t", tmp_path / "i.sqlite")
    t0 = time.time()
    n = len(list(fi._walk_one(root, pb)))
    new = time.time() - t0
    assert n == 20000
    assert new < 30, new  # the old filter ran 20k x 3k link tests here (minutes)


def test_old_filter_is_slower_with_many_links(tmp_path):
    root, out = tmp_path / "r", tmp_path / "targets"
    root.mkdir()
    for i in range(250):
        touch(out / f"t{i}" / "n.md")
        (root / f"l{i}").symlink_to(out / f"t{i}")
    for i in range(800):
        touch(root / "plain" / f"n{i}.md")
    fi = FileIndex("t", tmp_path / "i.sqlite")
    t0 = time.time(); got = sorted(fi._walk_one(root, pb)); new = time.time() - t0
    t0 = time.time(); want = sorted(old_walk_one(root, pb)); old = time.time() - t0
    assert got == want and new * 3 < old, (new, old)


def test_pointers_sharing_files_do_not_reread_each_other(tmp_path, monkeypatch):
    d = tmp_path / "docs"
    files = [touch(d / f"n{i}.md", f"made up {i}\n") for i in range(10)]
    ent = {str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True} for f in files}
    idx = FileIndex("t", tmp_path / "i.sqlite")
    assert idx.update("p0", entries=ent, roots=[str(d)])["hashed"] == 10
    assert idx.update("p1", entries=ent, roots=[str(d)])["hashed"] == 0  # p0 holds them as they stand
    assert idx.update("p0", entries=ent, roots=[str(d)])["hashed"] == 0
    files[3].write_text("changed\n")
    ent2 = dict(ent)
    assert idx.update("p1", entries=ent2, roots=[str(d)])["hashed"] == 1  # a real change is still read


def test_vbigram_word_index_exists(tmp_path):
    idx = FileIndex("t", tmp_path / "i.sqlite")
    names = {r[0] for r in idx.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "vbigram_word" in names
    plan = " ".join(str(r) for r in idx.db.execute("EXPLAIN QUERY PLAN DELETE FROM vbigram WHERE word='x'"))
    assert "vbigram_word" in plan


# (d) the updater's walk applies the excludes connect recorded

def test_updater_never_walks_or_stats_an_excluded_folder(tmp_path, monkeypatch):
    notes, names, sdir = rp.build(tmp_path, monkeypatch, 12)
    rp.Rig(monkeypatch, notes, names)
    sub = notes / "p0" / "sub"
    for i in range(3):
        touch(sub / f"inner{i}.md", f"made up inner {i}\n")
    touch(sub / "deep" / "more.md")
    touch(notes / "p0" / "kept" / "k.md")
    cdir = ask.prepare_bulk.CACHE_DIR
    rep = json.loads((cdir / "p0-report.json").read_text())
    rep["excludes"] = ["sub/"]  # connect --exclude sub/, as the report stores it
    (cdir / "p0-report.json").write_text(json.dumps(rep))
    touched = []
    real_stat, real_scandir = os.stat, os.scandir
    monkeypatch.setattr(os, "stat", lambda p, *a, **k: (touched.append(str(p)), real_stat(p, *a, **k))[1])
    monkeypatch.setattr(os, "scandir", lambda p=".", *a, **k: (touched.append(str(p)), real_scandir(p, *a, **k))[1])
    rp.sync(sdir)
    assert any("kept" in t for t in touched)  # the walk did run
    assert not [t for t in touched if str(sub) in t], [t for t in touched if str(sub) in t]
    c = sqlite3.connect(str(sdir / "index.sqlite"))
    assert not c.execute("SELECT path FROM seen WHERE path LIKE ?", (f"{sub}/%",)).fetchall()
    assert c.execute("SELECT COUNT(*) FROM seen WHERE path LIKE ?", (f"%/kept/k.md",)).fetchone()[0] == 1
    c.close()


def test_walk_excludes_match_connects_rule(tangle):
    fi = FileIndex("t", tangle.parent / "i.sqlite")
    ex = ["b/d", "a.md", "lnk_out/sub"]
    got = set(fi._walk([str(tangle)], ex))
    want = {p for p in fi._walk([str(tangle)]) if not pb._excluded(Path(p).relative_to(tangle).as_posix(), ex)}
    assert got == want and str(tangle / "b" / "c.md") in got and str(tangle / "b" / "d" / "e.md") not in got


def test_a_root_with_an_exclude_does_not_collide_with_a_sibling_root(tmp_path):
    touch(tmp_path / "a" / "b" / "keep.md")
    touch(tmp_path / "a" / "b" / "c" / "drop.md")
    touch(tmp_path / "a" / "bc" / "other.md")  # "/a/b" + exclude "c" must not share a key with this root
    fi = FileIndex("t", tmp_path / "i.sqlite")
    fi.begin_round()
    got_b = sorted(fi._walk([str(tmp_path / "a" / "b")], ["c"]))
    got_bc = sorted(fi._walk([str(tmp_path / "a" / "bc")]))
    assert got_b == [str(tmp_path / "a" / "b" / "keep.md")]
    assert got_bc == [str(tmp_path / "a" / "bc" / "other.md")]
    fi2 = FileIndex("t2", tmp_path / "i2.sqlite")
    fi2.begin_round()
    assert sorted(fi2._walk([str(tmp_path / "a" / "bc")])) == got_bc  # and in the other order
    assert sorted(fi2._walk([str(tmp_path / "a" / "b")], ["c"])) == got_b


def test_an_exclude_added_later_drops_the_old_rows_and_they_are_not_stat_again(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    touch(root / "keep.md"); touch(root / "gen" / "g1.md"); touch(root / "gen" / "g2.md")
    idx = FileIndex("t", tmp_path / "i.sqlite")
    idx.update("p", entries={}, roots=[str(root)])
    assert idx.db.execute("SELECT COUNT(*) FROM seen WHERE path LIKE '%/gen/%'").fetchone()[0] == 2
    touched = []
    real = os.stat
    monkeypatch.setattr(os, "stat", lambda p, *a, **k: (touched.append(str(p)), real(p, *a, **k))[1])
    idx.update("p", entries={}, roots=[str(root)], excludes=["gen/"])
    assert not [t for t in touched if "/gen" in t], touched
    assert idx.db.execute("SELECT COUNT(*) FROM seen WHERE path LIKE '%/gen/%'").fetchone()[0] == 0
    assert idx.db.execute("SELECT COUNT(*) FROM seen WHERE path LIKE '%/keep.md'").fetchone()[0] == 1


def test_updater_exits_quietly_when_the_index_cannot_be_opened(tmp_path, monkeypatch):
    notes, names, sdir = rp.build(tmp_path, monkeypatch, 5)
    rp.Rig(monkeypatch, notes, names)

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(file_index, "FileIndex", locked)
    assert ask.index_sync(rp.PRINCIPAL, sdir) == 0
