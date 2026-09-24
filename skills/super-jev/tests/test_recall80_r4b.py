#!/usr/bin/env python3
"""Offline tests for the recall80-r4b candidate fix to ask.py:

1. confirm_label() now uses ANSWER_LABEL (not the strict exact-value
   CONFIRM_LABEL) for which/where questions, and for "what ... say/cover/mean"
   style casual asks, as long as they are not value questions -- targets the
   content-check rejections in the recall80 bench (router.md, the tools
   audit, agentic-economy, among others). Plain "what is/are X" fact lookups
   ("what car do I have", "what time does it depart") are deliberately left
   out of the open-question treatment: they ask for one exact value just as
   much as an explicit VALUE_RE hit, and widening to all "what" questions
   reintroduced the near-miss false hits test_hardening_round3/4 guard.
2. lookup()'s possible-tier gate now only excludes VALUE_RE questions when
   they are NOT "how many" / non-live "how much" (is_howcount_question).
   worth/owe/owed/price/cost/total and any live figure (is_live_value_question)
   stay gated -- those are the near-miss shapes test_retrieval_recall and
   test_review_recall_holes guard. "how many"/"how much" get the possible
   tier back -- targets wheel-radar (0.67) and EXPLORE (0.80).
3. LIVE_RE must still gate the near:nvda breakeven-style question -- no
   possible tier, no false hit, for a genuine live-value ask.

No network and no real key.

    python3 -m pytest skills/super-jev/tests/test_recall80_r4b.py -q
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(SKILL))
ask = load("ask_r4b", SKILL / "ask.py")


# 1. confirm_label(): casual which/where/what-say-cover-mean questions get the
#    answer label, not the strict exact-value label, as long as they are not
#    value questions.
@pytest.mark.parametrize("question", [
    "what does router.md say about routing",
    "which section covers the tools audit",
    "where is the agentic-economy plan discussed",
    "what swap path services did we discover",
])
def test_casual_wh_questions_get_answer_label(question):
    assert ask.confirm_label(question) is ask.ANSWER_LABEL


# ... but a wh-question that is also a value question (VALUE_RE) still gets
# the strict exact-value label.
@pytest.mark.parametrize("question", [
    "what is the total cost of the trip",
    "what is the current balance",
])
def test_wh_value_questions_still_get_exact_value_label(question):
    assert ask.confirm_label(question) is ask.CONFIRM_LABEL


# a plain "what is/are X" fact lookup is NOT treated as open -- it wants one
# exact value just like an explicit VALUE_RE hit (regression guard for
# test_hardening_round3/4's near-miss calibration).
@pytest.mark.parametrize("question", [
    "What car do I have?",
    "What time does TP201 depart?",
])
def test_plain_what_fact_lookup_keeps_exact_value_label(question):
    assert ask.confirm_label(question) is ask.CONFIRM_LABEL


# how/should/why/when questions keep working exactly as before (regression guard).
def test_existing_open_questions_unchanged():
    assert ask.confirm_label("should I renew the lease") is ask.ANSWER_LABEL
    assert ask.confirm_label("how do I file the claim") is ask.ANSWER_LABEL


# 2. is_live_value_question() distinguishes live-money asks from other
#    VALUE_RE-flagged questions like "how many" or a non-live "how much".
def test_how_many_is_a_value_question_but_not_live():
    q = "how many wheel positions are open on the radar"
    assert ask.is_value_question(q)
    assert not ask.is_live_value_question(q)


def test_how_much_about_a_document_is_not_live():
    q = "how much detail does EXPLORE.md give on the rollout plan"
    assert ask.is_value_question(q)
    assert not ask.is_live_value_question(q)


# 3. LIVE_RE must still gate a genuine live-money ask (the nvda breakeven
# near-miss the task calls out).
def test_nvda_breakeven_is_live_and_gated():
    q = "what is the breakeven on nvda right now"
    assert ask.is_live_value_question(q)


def test_nvda_negative_bench_wording_is_live_and_gated():
    # exact wording of the negative case from run-r80-r2a/run.py
    q = "NVDA wheel campaign breakeven and premiums collected"
    assert ask.is_live_value_question(q)


def test_explore_bench_wording_is_not_live():
    # exact wording of the EXPLORE (0.80) miss from run-r80-r3a/misses.json
    q = "how much cheaper and faster is this"
    assert ask.is_value_question(q)
    assert not ask.is_live_value_question(q)


def test_wheel_radar_bench_wording_is_not_live():
    # exact wording of the wheel-radar (0.67) miss from run-r80-r3a/misses.json
    q = "How many put opportunities show up this morning?"
    assert ask.is_value_question(q)
    assert not ask.is_live_value_question(q)


def _lookup_possible_gate(question, score):
    """Mirrors lookup()'s possible-tier gate condition exactly: given a single
    checked file scored between POSSIBLE_FLOOR and CONFIRM_FLOOR, does the
    possible tier get populated?"""
    allowed = not ask.is_value_question(question) or (
        ask.is_howcount_question(question) and not ask.is_live_value_question(question))
    if not allowed:
        return {}
    scores = {"f.md": score}
    return {p: ask.POSSIBLE_NOTE for p in ["f.md"]
            if ask.POSSIBLE_FLOOR <= scores.get(p, 0) < ask.CONFIRM_FLOOR}


def test_possible_tier_kept_for_how_many(monkeypatch):
    possible = _lookup_possible_gate("how many items are on the radar", 0.67)
    assert "f.md" in possible


def test_possible_tier_kept_for_explore_style_how_much(monkeypatch):
    possible = _lookup_possible_gate("how much does EXPLORE cover the rollout", 0.80)
    assert "f.md" in possible


def test_possible_tier_dropped_for_live_breakeven(monkeypatch):
    possible = _lookup_possible_gate("what is the breakeven on nvda right now", 0.80)
    assert possible == {}


# non-live value words other than how-many/how-much (worth/owe/owed/price/cost/
# total) stay gated too -- these are the near-miss shapes
# test_retrieval_recall.test_value_question_owed_gets_no_possible_tier and
# test_review_recall_holes.test_value_question_near_miss_not_shown guard.
def test_possible_tier_dropped_for_owed():
    possible = _lookup_possible_gate("what is owed on the vehicle account", 0.70)
    assert possible == {}


def test_possible_tier_dropped_for_price():
    possible = _lookup_possible_gate("what price did we pay for the Dell after the refund", 0.80)
    assert possible == {}


# End-to-end sanity: confirm_one still respects the label change without
# breaking the score/threshold contract used elsewhere.
def test_confirm_one_uses_answer_label_for_casual_question(tmp_path, monkeypatch):
    f = tmp_path / "router.md"
    f.write_text("Router.md routes each question to the best-matching pointer.")
    calls = []

    def fake_run(cmd, input, **kw):
        calls.append(input)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(
            {"status": "candidates", "candidates": [{"score": 0.9}]}), "")
    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    score = ask.confirm_one("what does router.md say about routing", str(f))[0]
    assert score >= ask.CONFIRM_FLOOR
    label = json.loads(calls[0])["catalog"]["nodes"][1]["label"]
    assert "answers the question" in label
