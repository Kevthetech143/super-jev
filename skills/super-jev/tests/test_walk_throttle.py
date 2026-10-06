"""The updater walks the roots at most once per SCAN_SECS per principal; a connect/refresh kick is throttled too."""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_index_read_path as rp  # noqa: E402  (its corpus builder and stub engine)

import ask  # noqa: E402
import prepare_bulk as pb  # noqa: E402
from file_index import FileIndex  # noqa: E402


def count_walks(monkeypatch):
    n = [0]
    real = FileIndex._walk_one

    def counting(self, *a, **k):
        n[0] += 1
        return real(self, *a, **k)
    monkeypatch.setattr(FileIndex, "_walk_one", counting)
    return n


def test_second_run_within_scan_secs_does_not_walk_and_a_later_one_does(tmp_path, monkeypatch):
    notes, names, sdir = rp.build(tmp_path, monkeypatch, 12)
    rp.Rig(monkeypatch, notes, names)
    n = count_walks(monkeypatch)
    rp.sync(sdir)
    first = n[0]
    assert first > 0
    rp.sync(sdir)
    assert n[0] == first  # inside SCAN_SECS: no root walk
    stamp = sdir / ask.INDEX_WALK_STAMP
    old = time.time() - ask.auto_heal.SCAN_SECS - 5
    os.utime(stamp, (old, old))
    rp.sync(sdir)
    assert n[0] > first  # after SCAN_SECS: it walks again


def test_kick_is_throttled_and_skipped_in_a_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    seen = []
    monkeypatch.setattr(pb.subprocess, "Popen", lambda cmd, **k: seen.append(cmd))
    pb.kick_index_updater(["me"])
    assert len(seen) == 1
    pb.kick_index_updater(["me"])  # stamped just now
    assert len(seen) == 1
    stamp = tmp_path / "state" / "me" / ask.INDEX_STAMP
    old = time.time() - ask.INDEX_SPAWN_EVERY_SECS - 5
    os.utime(stamp, (old, old))
    pb.kick_index_updater(["me"])
    assert len(seen) == 2
    monkeypatch.setenv("SUPERJEV_REPLAY", "1")
    os.utime(stamp, (old, old))
    pb.kick_index_updater(["me"])
    assert len(seen) == 2


def test_refreshed_file_is_indexed_without_a_walk(tmp_path, monkeypatch):
    notes, names, sdir = rp.build(tmp_path, monkeypatch, 12)
    rp.Rig(monkeypatch, notes, names)
    rp.sync(sdir)
    target = next(notes.rglob("zorblax.md"))
    target.write_text("# zorblax\nzorblax quenth changed by a refresh.\n")
    cache = ask.prepare_bulk.CACHE_DIR / "p0.json"  # a refresh rewrites the cache entry with the new bytes
    entries = json.loads(cache.read_text())
    entries[str(target)]["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    cache.write_text(json.dumps(entries))
    n = count_walks(monkeypatch)
    rp.sync(sdir)
    assert n[0] == 0  # throttled: no walk
    idx = FileIndex(rp.PRINCIPAL, sdir / "index.sqlite")
    row = idx.db.execute("SELECT sha256 FROM files WHERE path=?", (str(target),)).fetchone()
    idx.close()
    assert row and row[0] == entries[str(target)]["sha256"]


def test_compact_returns_the_freelist_and_leaves_the_rows(tmp_path):
    idx = FileIndex(rp.PRINCIPAL, tmp_path / "index.sqlite")
    idx.db.execute("CREATE TABLE junk(x)")
    idx.db.executemany("INSERT INTO junk VALUES(?)", [("y" * 2000,)] * 2000)
    idx.db.execute("INSERT INTO pointers(pointer) VALUES('keep')")
    idx.db.commit()
    idx.db.execute("DROP TABLE junk")
    idx.db.commit()
    free = lambda: idx.db.execute("PRAGMA freelist_count").fetchone()[0]  # noqa: E731
    assert free() > 100
    idx.compact()  # first time: auto_vacuum was off, so one rebuild
    assert free() == 0 and idx.db.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    idx.db.execute("CREATE TABLE junk(x)")
    idx.db.executemany("INSERT INTO junk VALUES(?)", [("y" * 2000,)] * 2000)
    idx.db.commit()
    idx.db.execute("DROP TABLE junk")
    idx.db.commit()
    assert free() > 100
    idx.compact()  # then incremental
    assert free() == 0
    assert idx.db.execute("SELECT pointer FROM pointers").fetchall() == [("keep",)]
    idx.close()
