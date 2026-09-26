#!/usr/bin/env python3
"""The "none of these" check (judge_none, NONE_KEEP_FLOOR). Real failures, night-0803:
q18, a made-up question about Kelvin's CLOV verdict, kept history-recall/SKILL.md as
"possible" (cover gate cap 0.84); q14 kept an evidence-sample releases.md confirmed at
0.89. Jev picks "none" for both. Nothing here makes a live provider call.

    python3 -m pytest skills/super-jev/tests/test_none_choice.py -q
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_none_choice", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

QUESTION = "What was Kelvin's final personal verdict on pulling out of CLOV last month?"


def _lookup(tmp_path, monkeypatch, files, scores, choice):
    monkeypatch.setenv("SUPERJEV_NONE_CHOICE", "1")
    calls = []
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {
        str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True} for f in files})
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "miss"} if r["action"] == "cached" else
                        {"pointers": ["p1"]} if r["action"] == "panel" else
                        {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(p)} for p in files]})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({p: s for p, s in scores.items() if p in ps}, set(), None, {}))
    monkeypatch.setattr(ask, "judge_listwise", lambda q, pool: (None, None))
    probs = {p: 0.0 for p in map(str, files)} if choice else {}
    monkeypatch.setattr(ask, "judge_none", lambda q, pool: (calls.append(pool), (choice, probs))[1])
    sdir = tmp_path / "s"
    sdir.mkdir(exist_ok=True)
    ask.lookup(QUESTION, "me", sdir)
    rec = next((json.loads(ln) for ln in reversed((sdir / "lookups.jsonl").read_text().splitlines())
                if json.loads(ln).get("kind") == "lookup"), {})
    return [t["path"] for t in rec.get("top") or []], calls


def _notes(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("# History Recall\nRead the last N exchanges Kelvin had with any bot.\n")
    b.write_text("# CLOV\nKelvin's verdict on pulling out of CLOV last month: final, stay in.\n")
    return a, b


def test_none_drops_seen_possible_hits(tmp_path, monkeypatch):
    a, b = _notes(tmp_path)
    top, calls = _lookup(tmp_path, monkeypatch, [a, b], {str(a): 0.84, str(b): 0.7}, "none")
    assert calls and top == []


def test_none_never_drops_a_confirmed_file(tmp_path, monkeypatch):
    # night-0803 review: restart-seat-opus5 SKILL.md, right and confirmed at 0.85,
    # lost to "none" on some runs; confirmed files stay.
    a, b = _notes(tmp_path)
    top, _ = _lookup(tmp_path, monkeypatch, [a, b], {str(a): 0.7, str(b): 0.85}, "none")
    assert top == [str(b)]


def test_a_file_choice_keeps_everything(tmp_path, monkeypatch):
    a, b = _notes(tmp_path)
    top, _ = _lookup(tmp_path, monkeypatch, [a, b], {str(a): 0.7, str(b): 0.88}, str(b))
    assert set(top) == {str(a), str(b)}


def test_no_opinion_fails_open(tmp_path, monkeypatch):
    a, b = _notes(tmp_path)
    top, _ = _lookup(tmp_path, monkeypatch, [a, b], {str(a): 0.7}, None)
    assert top == [str(a)]


def test_judge_sends_the_passage_with_the_questions_words(tmp_path, monkeypatch):
    long = tmp_path / "timeline.md"
    long.write_text("# Timeline\n" + "unrelated visit notes. " * 400 + "\nCLOV verdict: stay in.\n")
    sent = {}
    import sys
    sys.path.insert(0, str(SKILL / "lib"))
    import jev_client
    monkeypatch.setattr(jev_client, "ask", lambda state, qs, **kw: (sent.update(state), {
        "answers": {"pick": {"choice": "file_1", "probabilities": {"file_1": 0.9, "none": 0.1}}}})[1])
    choice, probs = ask.judge_none(QUESTION, [str(long)])
    assert choice == str(long) and "CLOV verdict" in sent["file_1"]["text"]
    assert len(sent["file_1"]["text"]) <= ask.NONE_SNIPPET


def test_none_never_drops_a_file_jev_did_not_see(tmp_path, monkeypatch):
    a, b = _notes(tmp_path)
    monkeypatch.setattr(ask, "NONE_FILES", 1)
    top, calls = _lookup(tmp_path, monkeypatch, [a, b], {str(a): 0.8, str(b): 0.7}, "none")
    assert top == [str(b)]
