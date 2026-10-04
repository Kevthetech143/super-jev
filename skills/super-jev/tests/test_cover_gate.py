#!/usr/bin/env python3
"""Source content controls ranking; word overlap and filenames do not override it.
Person-scoped discovery and admission still apply. No live calls.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_cover_gate", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

QUESTION = "What was Marvin's final personal verdict on pulling out of CLOV last month?"


def _files(tmp_path):
    recall = tmp_path / "history-recall" / "SKILL.md"
    recall.parent.mkdir()
    recall.write_text("# History Recall\nRead the last N exchanges Marvin had with any bot. "
                      "Use it to catch up on what Marvin and the bot discussed last.\n")
    clov = tmp_path / "clov-notes.md"
    clov.write_text("# CLOV\nMarvin's verdict on pulling out of CLOV last month: final, stay in.\n")
    others = [tmp_path / f"u{i}.md" for i in range(10)]
    for i, f in enumerate(others):
        f.write_text(f"Marvin note {i} about groceries, rent and the last month.\n")
    return recall, clov, others


def _lookup(tmp_path, routed, scores, cached):
    mp = pytest.MonkeyPatch()
    mp.setattr(ask, "load_cache_files", lambda ptr: {
        str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True} for f in cached})
    mp.setattr(ask, "memory", lambda r: {"status": "miss"} if r["action"] == "cached" else
               {"pointers": ["p1"]} if r["action"] == "panel" else
               {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(p)} for p in routed]})
    mp.setattr(ask, "confirm", lambda q, ps: ({p: s for p, s in scores.items() if p in ps}, set(), None, {}))
    mp.setattr(ask, "judge_listwise", lambda q, pool: (None, None))
    sdir = tmp_path / "s"
    sdir.mkdir(exist_ok=True)
    try:
        ask.lookup(QUESTION, "me", sdir)
    finally:
        mp.undo()
    for line in reversed((sdir / "lookups.jsonl").read_text().splitlines()):
        rec = json.loads(line)
        if rec.get("kind") == "lookup" and rec.get("top"):
            return rec["top"]
    return []


def test_semantically_selected_file_is_not_vetoed_by_lexical_coverage(tmp_path):
    # Semantic decisions are mocked; this tests removal of the lexical veto,
    # not whether the provider classifies this particular text correctly.
    recall, clov, others = _files(tmp_path)
    top = _lookup(tmp_path, [recall], {str(recall): 0.87}, [recall, clov, *others])
    assert str(recall) in [t["path"] for t in top]


def test_file_holding_the_questions_words_still_confirms(tmp_path):
    recall, clov, others = _files(tmp_path)
    top = _lookup(tmp_path, [clov], {str(clov): 0.9}, [recall, clov, *others])
    assert top[0]["path"] == str(clov) and top[0]["score"] >= ask.CONFIRM_FLOOR and not top[0]["possible"]


def test_routed_file_outside_word_index_can_supply_evidence(tmp_path):
    recall, clov, others = _files(tmp_path)
    # the pick (what routing used to supply) is in the set's file list but shares no words with the question
    top = _lookup(tmp_path, [recall], {str(recall): 0.87}, [recall, clov, *others])
    assert top[0]["path"] == str(recall) and top[0]["score"] >= ask.CONFIRM_FLOOR
