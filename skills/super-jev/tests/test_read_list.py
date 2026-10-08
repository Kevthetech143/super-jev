"""The content check reads the files most likely to answer, not only what routing ranks first.

Frozen shape of a real miss: routing filled every read slot with unrelated skill files and
never offered the note whose text holds the answer. The judge is faked; no paid calls."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_read_list", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

QUESTION = "clinic phone number"


def _setup(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    skills = []
    for i in range(6):
        p = tmp_path / "skills" / f"s{i}" / "skill.md"
        p.parent.mkdir(parents=True)
        p.write_text(f"# Skill {i}\nHow to run task {i}.\n")
        skills.append(p)
    note = tmp_path / "medical" / "timeline.md"
    note.parent.mkdir()
    note.write_text("# Timeline\nThe clinic phone number is 212-555-0100.\n")
    cache = {str(p): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "pass": True,
                      "description": "", "question": ""} for p in [*skills, note]}
    cands = [{"score": 0.9 - i * 0.1, "originalPath": str(p)} for i, p in enumerate(skills)]
    read = []

    def confirm(q, paths):
        read.extend(paths)
        return {p: (0.95 if p == str(note) else 0.0) for p in paths}, set(), None, {}

    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: cache)
    monkeypatch.setattr(ask, "pointer_words", lambda *a: ({}, []))
    monkeypatch.setattr(ask, "batch_jev", lambda: False)
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "miss"} if r["action"] == "cached" else
                        {"pointers": [{"pointer": "p1"}]} if r["action"] == "panel" else
                        {"status": "candidates", "candidates": cands})
    monkeypatch.setattr(ask, "confirm", confirm)
    return note, skills, read


def test_note_holding_the_answer_is_read_when_routing_offers_only_unrelated_files(tmp_path, monkeypatch, capsys):
    note, skills, read = _setup(tmp_path, monkeypatch)
    ask.lookup(QUESTION, "me", tmp_path / "state")
    assert str(note) in read
    assert str(note) in capsys.readouterr().out


def test_routed_files_are_still_read(tmp_path, monkeypatch, capsys):
    note, skills, read = _setup(tmp_path, monkeypatch)
    ask.lookup(QUESTION, "me", tmp_path / "state")
    assert [str(p) for p in skills[:ask.zoom.KEEP_FILES]] == read[:ask.zoom.KEEP_FILES]
    assert len(read) <= ask.READ_MAX
