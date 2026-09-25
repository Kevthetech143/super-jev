#!/usr/bin/env python3
"""Unit tests for the listwise reorder + promote gate ("Version C" in
superjev-tests/listwise-2026-09-25/result.md): one Jev call over every file
that reached POSSIBLE_FLOOR, asking which single one best answers the
question. The winner reorders to #1 and is promoted to CONFIRM_FLOOR only if
its own winning probability is itself >= LISTWISE_PROMOTE_FLOOR (0.9). It
never demotes another file, and a failed/timed-out/off call keeps today's
order. Nothing here makes a live provider call.

    python3 -m pytest skills/super-jev/tests/test_listwise.py -q
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_listwise", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def _judge_result(winner, score):
    return json.dumps({"status": "candidates",
                        "candidates": [{"sourceId": winner, "score": score}]})


class FakeRun:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


def _run_lookup(tmp_path, question, candidates, scores, listwise_winner_prob=None, listwise_enabled=None):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "miss"} if r["action"] == "cached" else
                        {"pointers": ["p1"]} if r["action"] == "panel" else
                        {"status": "candidates", "candidates": candidates})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (dict(scores), set(), None, {}))
    if listwise_winner_prob is not None:
        winner, prob = listwise_winner_prob
        monkeypatch.setattr(ask, "judge_listwise", lambda q, pool: (winner, prob))
    if listwise_enabled is not None:
        monkeypatch.setattr(ask, "listwise_enabled", lambda: listwise_enabled)
    sdir = tmp_path / "s"
    sdir.mkdir(parents=True, exist_ok=True)
    ask.lookup(question, "me", sdir)
    lines = (sdir / "lookups.jsonl").read_text().splitlines()
    monkeypatch.undo()
    for line in reversed(lines):
        rec = json.loads(line)
        if rec.get("kind") == "lookup" and rec.get("question") == question and rec.get("top"):
            return rec["top"]
    raise AssertionError("no lookup log entry found")


# ------------------------------------------------------------ judge_listwise

def test_judge_listwise_reads_snippets_and_returns_winner_and_prob(tmp_path, monkeypatch):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("the real answer")
    b.write_text("a lookalike, not the answer")
    monkeypatch.setattr(ask.subprocess, "run", lambda *a2, **k: FakeRun(_judge_result(str(a), 0.95)))
    winner, prob = ask.judge_listwise("q", [str(a), str(b)])
    assert winner == str(a)
    assert prob == 0.95


def test_judge_listwise_returns_none_on_error(tmp_path, monkeypatch):
    a = tmp_path / "a.md"
    a.write_text("x")
    monkeypatch.setattr(ask.subprocess, "run", lambda *a2, **k: FakeRun("", returncode=1))
    assert ask.judge_listwise("q", [str(a)]) == (None, None)


def test_judge_listwise_returns_none_on_timeout(tmp_path, monkeypatch):
    a = tmp_path / "a.md"
    a.write_text("x")

    def boom(*a2, **k):
        raise subprocess.TimeoutExpired(cmd="jev", timeout=60)
    monkeypatch.setattr(ask.subprocess, "run", boom)
    assert ask.judge_listwise("q", [str(a)]) == (None, None)


# ------------------------------------------------------------ lookup() integration

def test_winner_gets_promoted_and_moved_to_top(tmp_path):
    """A file below CONFIRM_FLOOR that wins the listwise call with >= 0.9
    gets promoted to CONFIRM_FLOOR and moves to #1."""
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("routed higher but not the real answer")
    b.write_text("the real answer, routed lower")
    top = _run_lookup(
        tmp_path, "what is the answer",
        [{"score": 0.90, "originalPath": str(a)},
         {"score": 0.70, "originalPath": str(b)}],
        {str(a): 0.70, str(b): 0.70},
        listwise_winner_prob=(str(b), 0.93),
    )
    assert top[0]["path"] == str(b)
    assert top[0]["score"] >= ask.CONFIRM_FLOOR


def test_winner_under_promote_floor_is_not_confirmed(tmp_path):
    """A winner scoring below LISTWISE_PROMOTE_FLOOR (0.9) reorders to #1 but
    is not promoted past CONFIRM_FLOOR -- it stays wherever its own content
    score puts it (never demoted below what it already had)."""
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("routed higher")
    b.write_text("routed lower, wins listwise weakly")
    top = _run_lookup(
        tmp_path, "what is the answer",
        [{"score": 0.90, "originalPath": str(a)},
         {"score": 0.70, "originalPath": str(b)}],
        {str(a): 0.70, str(b): 0.70},
        listwise_winner_prob=(str(b), 0.75),
    )
    assert top[0]["path"] == str(b)
    assert top[0]["score"] < ask.CONFIRM_FLOOR


def test_failed_call_keeps_todays_order(tmp_path):
    """judge_listwise returning (None, None) -- the call failed, timed out, or
    picked none -- leaves ranking and scores exactly as today."""
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("x")
    b.write_text("y")
    top = _run_lookup(
        tmp_path, "what is the answer",
        [{"score": 0.90, "originalPath": str(a)},
         {"score": 0.70, "originalPath": str(b)}],
        {str(a): 0.90, str(b): 0.70},
        listwise_winner_prob=(None, None),
    )
    assert [t["path"] for t in top] == [str(a), str(b)]


def test_off_switch_skips_the_call_entirely(tmp_path, monkeypatch):
    """SUPERJEV_LISTWISE=0 (here via listwise_enabled() patched False) never
    calls judge_listwise and leaves ranking unchanged."""
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("x")
    b.write_text("y")
    called = []
    monkeypatch.setattr(ask, "judge_listwise", lambda q, pool: called.append(1) or (str(b), 0.99))
    top = _run_lookup(
        tmp_path, "what is the answer",
        [{"score": 0.90, "originalPath": str(a)},
         {"score": 0.70, "originalPath": str(b)}],
        {str(a): 0.90, str(b): 0.70},
        listwise_enabled=False,
    )
    assert [t["path"] for t in top] == [str(a), str(b)]
    assert not called
