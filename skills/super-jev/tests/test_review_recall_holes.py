#!/usr/bin/env python3
"""Adversarial review holes for feat/retrieval-recall. Regression tests for the review fixes."""
import hashlib, importlib.util, json, subprocess, sys
from pathlib import Path
import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_holes", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec); spec.loader.exec_module(ask)
import prepare_bulk


@pytest.fixture(autouse=True)
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))


def _entry(p):
    return {"pass": True, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


# H1: principal A's pointer "notes" pulls in pointer "notes-2" (another principal's) as a part cache.
def test_word_search_does_not_read_other_pointer_named_like_a_part(tmp_path, monkeypatch):
    cache = tmp_path / "cache"; cache.mkdir()
    mine = tmp_path / "mine.md"; mine.write_text("grocery list eggs")
    theirs = tmp_path / "theirs.md"; theirs.write_text("payroll salary bonus figures for staff")
    (cache / "notes.json").write_text(json.dumps({str(mine): _entry(mine)}))
    (cache / "notes-2.json").write_text(json.dumps({str(theirs): _entry(theirs)}))  # separate pointer, other principal
    monkeypatch.setattr(prepare_bulk, "CACHE_DIR", cache)
    hits = ask.word_search("payroll salary bonus", ["notes"])
    assert str(theirs) not in [p for _, p, _ in hits]


# H2: non-live value question ("how many", "price") gets the possible tier, re-admitting
# the 0.70-0.83 near misses the 0.85 floor was raised to block.
def test_value_question_near_miss_not_shown(tmp_path, monkeypatch, capsys):
    f = tmp_path / "f.md"; f.write_text("x")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "miss"} if r["action"] == "cached" else
                        {"pointers": ["p1"]} if r["action"] == "panel" else
                        {"status": "candidates", "candidates": [{"score": 0.5, "originalPath": str(f)}]})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(f): 0.8}, set(), None, {}))
    ask.lookup("what price did we pay for the Dell after the refund", "me", tmp_path / "s")
    assert "no-candidates" in capsys.readouterr().out


# H3: --approve picks top[0] pointer from the log even when that hit was only "possible".
def test_approve_ignores_possible_only_lookup(tmp_path, monkeypatch, capsys):
    f = tmp_path / "f.md"; f.write_text("x")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "miss"} if r["action"] == "cached" else
                        {"pointers": ["p1"]} if r["action"] == "panel" else
                        {"status": "candidates", "candidates": [{"score": 0.5, "originalPath": str(f)}]})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(f): 0.65}, set(), None, {}))
    sdir = tmp_path / "s"; sdir.mkdir(parents=True, exist_ok=True)
    ask.lookup("how is the plan going", "me", sdir)
    assert ask.find_pointer(sdir, "how is the plan going") is None
