#!/usr/bin/env python3
"""The lazy per-file word index: edited files re-indexed, held text never indexed, bad or old
index rebuilt with the same answer. Made-up files, no network.

    python3 -m pytest skills/super-jev/tests/test_word_index.py -q
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_windex", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

Q = "when is the orchard harvest"


def _setup(tmp_path, monkeypatch):
    cdir = tmp_path / "prepare-cache"
    cdir.mkdir()
    notes = tmp_path / "notes"
    notes.mkdir()
    (cdir / "p1-report.json").write_text(json.dumps({"pointer": "p1", "principals": ["me"], "roots": [str(notes)]}))
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", cdir)
    a, b = notes / "orchard.md", notes / "other.md"
    a.write_text("# Orchard\nThe orchard harvest is in October.\n")
    b.write_text("# Groceries\nMilk and eggs.\n")
    cache = {str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True} for f in (a, b)}
    (cdir / "p1.json").write_text(json.dumps(cache))
    return a, b, tmp_path / "idx.json"


def _search(idx):
    return ask.word_search(Q, ["p1"], index_path=idx, reads={})


def _sha(f):
    return hashlib.sha256(f.read_bytes()).hexdigest()


def test_indexed_and_warm_same_answer(tmp_path, monkeypatch):
    a, b, idx = _setup(tmp_path, monkeypatch)
    cold = _search(idx)
    assert cold and cold[0][1] == str(a)
    assert json.loads(idx.read_text())["files"][str(a)]["sha"] == _sha(a)
    assert _search(idx) == cold == ask.word_search(Q, ["p1"])  # warm == cold == no index


def test_edited_file_new_text_old_entry_gone(tmp_path, monkeypatch):
    a, b, idx = _setup(tmp_path, monkeypatch)
    _search(idx)
    old = _sha(a)
    a.write_text("# Orchard\nThe orchard harvest moved to November, bring kayak.\n")
    assert _search(idx)[0][1] == str(a)
    files = json.loads(idx.read_text())["files"]
    assert files[str(a)]["sha"] == _sha(a) != old
    assert "kayak" in files[str(a)]["whole"]
    assert old not in json.dumps(files)


def test_inserted_secret_held_not_indexed(tmp_path, monkeypatch):
    a, b, idx = _setup(tmp_path, monkeypatch)
    _search(idx)
    a.write_text("# Orchard\nThe orchard harvest is in October.\npassword: hunter2hunter2\napi_key = sk-live-abcdefghijklmnop1234567890\n")
    assert ask.clean_text(a.read_text(), str(a)) is None  # the gate does hold this text
    assert all(p != str(a) for _, p, _ in _search(idx))
    files = json.loads(idx.read_text())["files"]
    assert str(a) not in files and "hunter2hunter2" not in idx.read_text()


def test_corrupt_or_old_version_rebuilt(tmp_path, monkeypatch):
    a, b, idx = _setup(tmp_path, monkeypatch)
    want = _search(idx)
    idx.write_text("{not json")
    assert _search(idx) == want
    assert json.loads(idx.read_text())["version"] == ask.WORD_INDEX_VERSION
    saved = json.loads(idx.read_text())
    saved["version"] = "0.0"
    saved["files"][str(a)]["whole"] = {"zzz": 99}
    idx.write_text(json.dumps(saved))
    assert _search(idx) == want
    assert "zzz" not in json.loads(idx.read_text())["files"][str(a)]["whole"]
    saved = json.loads(idx.read_text())
    saved["files"][str(a)]["passages"] = "bad"
    idx.write_text(json.dumps(saved))
    assert _search(idx) == want


def test_prune_drops_left_and_deleted_files():
    widx = {"/a": {"sha": "1"}, "/b": {"sha": "2"}, "/gone": {"sha": "3"}}
    assert ask._prune_word_index(widx, {"/a", "/b"}) is True
    assert set(widx) == {"/a", "/b"}
    assert ask._prune_word_index(widx, {"/a", "/b"}) is False


def test_prune_saved_file_roundtrip(tmp_path, monkeypatch):
    a, b, idx = _setup(tmp_path, monkeypatch)
    _search(idx)
    saved = json.loads(idx.read_text())
    saved["files"]["/deleted.md"] = saved["files"][str(a)]
    idx.write_text(json.dumps(saved))
    widx = ask._load_word_index(idx)
    ask._prune_word_index(widx, {str(a), str(b)})
    ask._save_word_index(idx, widx)
    assert "/deleted.md" not in json.loads(idx.read_text())["files"]
    assert str(a) in json.loads(idx.read_text())["files"]


def test_version_covers_stopwords(monkeypatch):
    base = ask._word_index_version()
    assert base == ask.WORD_INDEX_VERSION
    monkeypatch.setattr(ask, "QUERY_STOPWORDS", ask.QUERY_STOPWORDS | {"zzword"})
    assert ask._word_index_version() != base
    monkeypatch.undo()
    monkeypatch.setattr(ask, "WORD_RE", ask.re.compile(r"\w+"))
    assert ask._word_index_version() != base


def test_secret_file_stores_no_words(tmp_path, monkeypatch):
    a, b, idx = _setup(tmp_path, monkeypatch)
    token = "AKIA" + "IOSFODNN7" + "EXAMPLE"  # fake key-shaped token, split so scanners pass over this file
    a.write_text(f"# Orchard\nThe orchard harvest is in October.\nkey {token}\n")
    cdir = ask.prepare_bulk.CACHE_DIR
    (cdir / "p1.json").write_text(json.dumps({str(f): {"sha256": _sha(f), "pass": True} for f in (a, b)}))
    assert ask.has_secret(a.read_text())
    assert all(str(a) != r[1] for r in _search(idx))  # still never offered
    raw = idx.read_text()
    assert token not in raw and token.lower() not in raw
    assert json.loads(raw)["files"][str(a)]["secret"] is True


def test_secret_file_stores_no_pointer_words(tmp_path, monkeypatch):
    token = "AKIA" + "IOSFODNN7" + "EXAMPLE"  # fake key-shaped token, split so scanners pass over this file
    held, fine = tmp_path / "held.md", tmp_path / "fine.md"
    held.write_text(f"# Orchard\nkey {token}\n")
    fine.write_text("# Notes\nThe orchard harvest is in October.\n")
    assert ask.has_secret(held.read_text())
    rows = [{"path": str(f), "originalPath": str(f)} for f in (held, fine)]
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "ok", "sources": rows})
    ask.save_pointer_words(tmp_path, "me", {"p": "g1"}, ["p"])
    raw = (tmp_path / ask.POINTER_WORDS_FILE).read_text()
    assert token.lower() not in raw.lower()
    assert "harvest" in json.loads(raw)["p"]["words"].split()


def test_old_pointer_words_are_cleaned_on_first_load(tmp_path):
    token = "AKIA" + "IOSFODNN7" + "EXAMPLE"  # fake key-shaped token, split so scanners pass over this file
    path = tmp_path / ask.POINTER_WORDS_FILE
    path.write_text(json.dumps({
        "old": {"generation": "g1", "words": token.lower(), "version": ask.WORDS_VERSION - 1},
        "new": {"generation": "g1", "words": "harvest", "version": ask.WORDS_VERSION}}))
    known, missing = ask.pointer_words(tmp_path, {"old": "g1", "new": "g1"})
    assert missing == ["old"] and known == {"new": "harvest"}
    assert token.lower() not in path.read_text().lower()  # purged without re-reading any file
    assert json.loads(path.read_text()) == {"new": {"generation": "g1", "words": "harvest", "version": ask.WORDS_VERSION}}
    assert ask.pointer_words(tmp_path, {"old": "g1", "new": "g1"}) == ({"new": "harvest"}, ["old"])  # one purge


def test_word_index_purges_held_words_on_load_without_reindexing(tmp_path, monkeypatch):
    token = "AKIA" + "IOSFODNN7" + "EXAMPLE"
    idx = tmp_path / "idx.json"
    good = {"sha": "s2", "heading": "Fine", "whole": {"harvest": 1}, "passages": [[{"harvest": 1}, 1, ["harv est"]]], "secret": False}
    old_held = {"sha": "s1", "heading": "Orchard", "whole": {token.lower(): 1}, "passages": [[{token.lower(): 1}, 1, []]], "secret": True}
    idx.write_text(json.dumps({"version": ask.WORD_INDEX_VERSION, "files": {"/held": old_held, "/fine": good}}))
    monkeypatch.setattr(ask, "_index_item", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-indexed")))
    files = ask._load_word_index(idx)
    assert files["/fine"] == good  # untouched
    assert files["/held"]["secret"] is True and files["/held"]["whole"] == {} and ask._valid_item(files["/held"], "s1")
    assert token.lower() not in idx.read_text().lower()  # the cleaned file was saved
    assert ask._load_word_index(idx) == files


def test_word_index_version_is_not_bumped_by_this_release():
    # same stamp as 1.0.131 (tokenizer 3, stamp ".2."), so upgrading does not re-index every file
    assert ask.WORD_INDEX_VERSION.startswith("3.2.")
