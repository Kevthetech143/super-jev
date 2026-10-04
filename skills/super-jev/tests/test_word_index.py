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
