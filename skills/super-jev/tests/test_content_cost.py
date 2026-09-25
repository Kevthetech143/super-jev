#!/usr/bin/env python3
"""Guards for the 2026-09-25 full-eval cause B (right file read but scored under the
floor because its answer spread over passages) and for the routing word prefilter.

    python3 -m pytest skills/super-jev/tests/test_content_cost.py -q
"""
import importlib.util
import json
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_content_cost", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def test_spread_answer_orders_files_within_a_tier():
    """q60: best 0.79, none 0.02 ranks above a 0.83 single passage, still possible."""
    assert 0.83 < ask.file_score(0.79, 0.02, 3) < ask.CONFIRM_FLOOR
    assert ask.file_score(0.9, 0.02, 3) > 0.9


def test_blend_never_lifts_a_file_into_a_tier():
    """q22 (absent): NYSC INDEX best 0.54, none 0.34 was lifted to possible 0.60."""
    assert ask.file_score(0.54, 0.34, 6) == 0.54


def test_live_value_ask_gets_no_spread_credit():
    assert ask.file_score(0.7, 0.10, 4, live=True) == 0.7


def test_single_passage_and_off_topic_files_keep_their_score():
    assert ask.file_score(0.7, 0.3, 1) == 0.7
    assert ask.file_score(0.7, None, 3) == 0.7
    # under the possible floor: no credit
    assert ask.file_score(0.2, 0.75, 4) == 0.2


def _lookup(tmp_path, question, words, sources_ok=True):
    """Two pointers with generations. Returns (navigated pointers, sources calls)."""
    navigated, listed = [], []
    for name, text in words.items():
        (tmp_path / f"{name}.txt").write_text(text)

    def memory(r):
        if r["action"] == "cached":
            return {"status": "miss"}
        if r["action"] == "panel":
            return {"pointers": [{"pointer": n, "generation": "g1"} for n in words]}
        if r["action"] == "sources":
            listed.append(r["pointer"])
            if not sources_ok:
                return {"status": "error"}
            return {"status": "ok", "sources": [{"path": str(tmp_path / f"{r['pointer']}.txt"),
                                                 "originalPath": f"/x/{r['pointer']}.md", "description": ""}]}
        navigated.append(r["pointer"])
        return {"status": "no-candidates", "candidates": []}

    mp = pytest.MonkeyPatch()
    mp.setattr(ask, "memory", memory)
    mp.setattr(ask, "load_cache_files", lambda ptr: {})
    mp.setattr(ask, "word_search", lambda *a, **k: [])
    sdir = tmp_path / "s"
    sdir.mkdir(exist_ok=True)
    ask.lookup(question, "me", sdir)
    mp.undo()
    return navigated, listed


def test_pointer_without_any_question_word_is_not_routed(tmp_path):
    words = {"knee": "Referral code M22.2X1 for the knee.", "taxes": "Schedule C totals for 2024."}
    # First ask: words unknown yet, so every pointer is routed while they are learned.
    navigated, listed = _lookup(tmp_path, "knee referral code", words)
    assert sorted(navigated) == ["knee", "taxes"] and sorted(listed) == ["knee", "taxes"]
    # Next ask: the taxes pointer holds none of the words and costs no routing call.
    navigated, listed = _lookup(tmp_path, "knee referral code", words)
    assert navigated == ["knee"] and listed == []


def test_synonym_keeps_the_pointer(tmp_path):
    """"my doctor" must still route a pointer whose notes only say physician."""
    words = {"care": "Primary physician Ruiz, rebook line.", "taxes": "Schedule C totals."}
    _lookup(tmp_path, "doctor appointment", words)
    navigated, _ = _lookup(tmp_path, "doctor appointment", words)
    assert navigated == ["care"]


def test_one_word_question_never_skips(tmp_path):
    words = {"knee": "Referral code.", "taxes": "Schedule C totals."}
    _lookup(tmp_path, "knee", words)
    navigated, _ = _lookup(tmp_path, "knee", words)
    assert sorted(navigated) == ["knee", "taxes"]


def test_pointer_whose_files_cannot_be_listed_is_always_routed(tmp_path):
    words = {"knee": "Referral code for the knee.", "taxes": "Schedule C totals."}
    _lookup(tmp_path, "knee referral code", words, sources_ok=False)
    navigated, listed = _lookup(tmp_path, "knee referral code", words, sources_ok=False)
    assert sorted(navigated) == ["knee", "taxes"] and listed == []
    saved = json.loads((tmp_path / "s" / ask.POINTER_WORDS_FILE).read_text())
    assert saved["taxes"] == {"generation": "g1", "words": None}
