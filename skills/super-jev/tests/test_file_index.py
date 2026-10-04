"""Per-principal file index: stat-diff updater, reviewed-sha-only ingest, purge. Made-up files, no judge."""
import builtins
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from file_index import FileIndex  # noqa: E402


def _entry(p):
    return {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "description": "notes", "question": "q",
            "kind": "unknown", "status": "unknown", "as_of": "unknown", "subject": "unknown", "pass": True}


@pytest.fixture
def corpus(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    files = []
    for i in range(20):
        f = d / f"note{i}.md"
        f.write_text(f"made up note number {i}\nabout gardens\n")
        files.append(f)
    return d, files, {str(f): _entry(f) for f in files}


@pytest.fixture
def opens(monkeypatch):
    seen = []
    real = builtins.open

    def spy(file, *a, **k):
        seen.append(str(file))
        return real(file, *a, **k)
    monkeypatch.setattr(builtins, "open", spy)
    return seen


def _idx(tmp_path, name="a"):
    return FileIndex(name, db_path=tmp_path / f"{name}.sqlite")


def test_unchanged_corpus_opens_no_bodies(tmp_path, corpus, opens):
    d, files, entries = corpus
    idx = _idx(tmp_path)
    assert idx.update("p", entries, [str(d)])["hashed"] == 20
    assert idx.count(pointer="p") == 20
    opens.clear()
    r = idx.update("p", entries, [str(d)])
    assert r["hashed"] == 0 and not r["stale"]
    assert [o for o in opens if str(d) in o] == []


def test_one_edit_one_sha_and_stale(tmp_path, corpus, opens):
    d, files, entries = corpus
    idx = _idx(tmp_path)
    idx.update("p", entries, [str(d)])
    assert not idx.is_stale("p")
    files[3].write_text("edited bytes, never reviewed\n")
    opens.clear()
    r = idx.update("p", entries, [str(d)])
    assert r["hashed"] == 1 and len([o for o in opens if str(d) in o]) == 1
    assert idx.is_stale("p")
    assert idx.count(path=str(files[3])) == 0  # edited bytes are not indexed
    assert idx.count(pointer="p") == 19


def test_new_file_found(tmp_path, corpus):
    d, files, entries = corpus
    idx = _idx(tmp_path)
    idx.update("p", entries, [str(d)])
    new = d / "fresh.md"
    new.write_text("brand new\n")
    r = idx.update("p", entries)  # roots remembered
    assert r["new"] == [str(new)] and idx.is_stale("p")
    assert idx.count(path=str(new)) == 0  # unreviewed: found, not ingested
    assert idx.update("p", entries)["hashed"] == 0


def test_secret_line_never_enters(tmp_path, corpus):
    d, files, entries = corpus
    files[5].write_text("harmless\napi_key = sk-live-9fQ2xZ7pL0aBcD3eF4\n")
    entries[str(files[5])] = _entry(files[5])  # reviewed sha matches the bytes: still held
    idx = _idx(tmp_path)
    idx.update("p", entries, [str(d)])
    assert idx.count(path=str(files[5])) == 0
    assert idx.db.execute("SELECT COUNT(*) FROM toc WHERE path=?", (str(files[5]),)).fetchone()[0] == 0
    assert idx.count(pointer="p") == 19


def test_unreviewed_and_failed_never_read(tmp_path, corpus, opens):
    d, files, entries = corpus
    entries[str(files[0])]["pass"] = False
    idx = _idx(tmp_path)
    opens.clear()
    idx.update("p", entries)
    assert idx.count(path=str(files[0])) == 0
    assert not [o for o in opens if o == str(files[0])]


def test_principals_are_separate(tmp_path, corpus):
    d, files, entries = corpus
    a, b = _idx(tmp_path, "a"), _idx(tmp_path, "b")
    a.update("a-only", entries, [str(d)])
    b.update("b-own", {}, None)
    assert a.count() == 20
    assert b.count() == 0 and b.count(pointer="a-only") == 0


def test_unshare_purges(tmp_path, corpus):
    d, files, entries = corpus
    idx = _idx(tmp_path)
    idx.update("p", entries, [str(d)])
    idx.update("q", {}, None)
    idx.purge("p")
    for t in ("files", "toc", "seen"):
        assert idx.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    assert idx.db.execute("SELECT COUNT(*) FROM pointers WHERE pointer='p'").fetchone()[0] == 0
    assert idx.count(pointer="p") == 0 and not idx.is_stale("p")
