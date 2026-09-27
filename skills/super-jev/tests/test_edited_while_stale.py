#!/usr/bin/env python3
"""An edited note stays findable while its pointer waits on the refresh, and a stale
split part prints its parent's refresh command. No network.

    python3 -m pytest skills/super-jev/tests/test_edited_while_stale.py -q
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_edited", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def _cache(files):
    return {str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True} for f in files}


def _two_notes(tmp_path):
    note, other = tmp_path / "design.md", tmp_path / "other.md"
    note.write_text("# Design log\nRouting reads pointer descriptions.\n")
    other.write_text("# Groceries\nMilk, eggs, bread.\n")
    return note, other


def test_a_note_edited_since_connect_is_still_word_searched(tmp_path, monkeypatch):
    note, other = _two_notes(tmp_path)
    cache = _cache([note, other])
    note.write_text("# Design log\nRouting reads pointer descriptions.\nThe builder playbook lists release traps.\n")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: cache)
    ask._STAGE.clear()
    found = [p for _, p, _ in ask.word_search("builder playbook release traps", ["p1"])]
    assert found == [str(note)]
    assert ask._STAGE["word_changed"] == [str(note)]


def test_an_edited_note_with_a_secret_now_is_not_searched(tmp_path, monkeypatch):
    note, other = _two_notes(tmp_path)
    cache = _cache([note, other])
    note.write_text("# Design log\nbuilder playbook release traps\napi_key = sk-live-" + "a1B2c3D4e5" * 4 + "\n")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: cache)
    assert ask.word_search("builder playbook release traps", ["p1"]) == []


def test_an_edited_note_over_the_size_ceiling_is_not_searched(tmp_path, monkeypatch):
    note, other = _two_notes(tmp_path)
    cache = _cache([note, other])
    note.write_text("builder playbook release traps\n" + "x" * (ask.prepare_bulk.CEILING_BYTES + 1))
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: cache)
    assert ask.word_search("builder playbook release traps", ["p1"]) == []


def test_a_stale_split_part_prints_its_parents_refresh_command(tmp_path, monkeypatch):
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "ok"})
    (tmp_path / "cat-report.json").write_text(json.dumps({
        "pointer": "cat", "principals": ["me"], "roots": ["/r"], "names": ["SKILL.md"],
        "parts": [{"pointer": "cat-2"}, {"pointer": "cat-3"}]}))
    hint = ask.refresh_hint("cat-3", "me", "preparation-required")
    assert "--root /r" in hint and "--pointer cat" in hint and "replaying its connect recipe failed" not in hint


def test_an_entry_never_reviewed_at_a_known_version_is_not_searched(tmp_path, monkeypatch):
    note, other = _two_notes(tmp_path)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {str(note): {"pass": True}})
    assert ask.word_search("routing pointer descriptions", ["p1"]) == []
