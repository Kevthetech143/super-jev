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

def _fake_choice(choice, prob=None, seen=None):
    def f(state, questions):
        if seen is not None:
            seen.append((state, questions))
        pick = {"choice": choice}
        if prob is not None:
            pick["probabilities"] = {choice: prob}
        return {"answers": {"pick": pick}}
    return f


def test_judge_listwise_sends_best_passages_plus_none_and_returns_winner(tmp_path, monkeypatch):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("the real answer")
    b.write_text("a lookalike, not the answer")
    seen = []
    monkeypatch.setattr(ask, "jev_choice", _fake_choice("file_1", 0.95, seen))
    assert ask.judge_listwise("q", [str(a), str(b)]) == (str(a), 0.95)
    state, questions = seen[0]
    assert state["file_1"]["text"] == "the real answer"
    assert "none" in questions["pick"]["criteria"]
    assert "When torn, pick none" in questions["pick"]["instructions"]


def test_judge_listwise_sends_the_best_scored_passage_of_a_long_file(tmp_path, monkeypatch):
    a = tmp_path / "a.md"
    n = ask.WHOLE_FILE_CHARS // ask.CONFIRM_CHUNK + 1  # long enough to be read in passages
    a.write_text("x" * ask.CONFIRM_CHUNK * n + "the answer lives in a late passage")
    monkeypatch.setitem(ask._STAGE, "checks", {str(a): {"best_chunk": n}})
    seen = []
    monkeypatch.setattr(ask, "jev_choice", _fake_choice("file_1", None, seen))
    ask.judge_listwise("q", [str(a)])
    assert seen[0][0]["file_1"]["text"] == "the answer lives in a late passage"


def test_judge_listwise_caps_at_four_files(tmp_path, monkeypatch):
    paths = []
    for i in range(6):
        f = tmp_path / f"f{i}.md"
        f.write_text(f"text {i}")
        paths.append(str(f))
    seen = []
    monkeypatch.setattr(ask, "jev_choice", _fake_choice("none", 0.8, seen))
    assert ask.judge_listwise("q", paths) == (ask.LISTWISE_NONE, 0.8)
    assert len(seen[0][0]) == 4


def test_judge_listwise_returns_no_opinion_on_error(tmp_path, monkeypatch):
    a = tmp_path / "a.md"
    a.write_text("x")

    def boom(*a2, **k):
        raise RuntimeError("TypeSafe returned HTTP 500")
    monkeypatch.setattr(ask, "jev_choice", boom)
    assert ask.judge_listwise("q", [str(a)]) == (None, None)
    monkeypatch.setattr(ask, "jev_choice", _fake_choice("file_9"))
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
    was inconclusive -- leaves ranking and scores exactly as today."""
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


def test_hub_winner_never_promotes_past_a_confirmed_source_note(tmp_path):
    """A folder README outranked a CONFIRMED real source note beside it, because prefer_sources() correctly moved
    the README below the note, but listwise then picked the README (Jev's
    single-best-answer judgment landed on the index, not the note) and moved
    it right back to #1 with no hub exemption -- unlike the near-twin
    tiebreak, which already sits hub files out of its re-judging. A hub/copy
    winner must never promote past a non-hub file already ranked ahead of it."""
    readme = tmp_path / "esteban/medical/README.md"
    panel = tmp_path / "esteban/medical/2026-04-09-panel.md"
    readme.parent.mkdir(parents=True)
    readme.write_text("index of esteban's medical folder")
    panel.write_text("April 2026 blood panel results")
    top = _run_lookup(
        tmp_path, "what were Esteban's April 2026 blood panel results",
        [{"score": 0.9, "originalPath": str(readme)},
         {"score": 0.8, "originalPath": str(panel)}],
        {str(readme): 0.98, str(panel): 0.91},
        listwise_winner_prob=(str(readme), 0.95),
    )
    assert top[0]["path"] == str(panel)


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


def _run_lookup_top_and_trace(tmp_path, question, candidates, scores, pick):
    top = None
    try:
        top = _run_lookup(tmp_path, question, candidates, scores, listwise_winner_prob=pick)
    except AssertionError:
        pass  # nothing kept: no lookup log entry with a top list
    return top or []


def test_none_pick_drops_possible_lookalike_for_made_up_question(tmp_path):
    """A made-up CLOV verdict question returned history-recall/SKILL.md as
    "possible". Jev picking none must drop it
    and report not found."""
    skill = tmp_path / "history-recall/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Recall what Kelvin said last time about a topic.")
    top = _run_lookup_top_and_trace(
        tmp_path, "What was Kelvin's final verdict on the CLOV earnings call last week?",
        [{"score": 0.8, "originalPath": str(skill)}], {str(skill): 0.83}, (ask.LISTWISE_NONE, 0.95))
    assert top == []


def test_none_pick_drops_possible_wrong_file(tmp_path):
    """"restart-seat-opus5 steps" returned loop-job-opus5 as possible, a wrong file. Jev picking none must drop it."""
    loop = tmp_path / "loop-job-opus5/SKILL.md"
    loop.parent.mkdir(parents=True)
    loop.write_text("Run a loop job on Opus 5.")
    top = _run_lookup_top_and_trace(
        tmp_path, "What are the steps in the restart-seat-opus5 skill?",
        [{"score": 0.8, "originalPath": str(loop)}], {str(loop): 0.80}, (ask.LISTWISE_NONE, 0.95))
    assert top == []


def test_none_pick_keeps_confirmed_file_but_not_as_confirmed(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("x")
    b.write_text("y")
    sdir = tmp_path / "s"
    top = _run_lookup(tmp_path, "what is the answer",
                      [{"score": 0.9, "originalPath": str(a)}, {"score": 0.7, "originalPath": str(b)}],
                      {str(a): 0.95, str(b): 0.70}, listwise_winner_prob=(ask.LISTWISE_NONE, 0.95))
    assert [t["path"] for t in top] == [str(a)]
    assert top[0]["possible"] is True
    assert sdir.exists()


def _two_confirmed(tmp_path, prob):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("x")
    b.write_text("y")
    top = _run_lookup(tmp_path, "what is the answer",
                      [{"score": 0.9, "originalPath": str(a)}, {"score": 0.7, "originalPath": str(b)}],
                      {str(a): 0.95, str(b): 0.90}, listwise_winner_prob=(str(a), prob))
    return [(t["path"], t["possible"]) for t in top], str(a), str(b)


def test_strong_pick_demotes_other_confirmed(tmp_path):
    got, a, b = _two_confirmed(tmp_path, 0.95)
    assert got == [(a, False), (b, True)]


def test_weak_pick_keeps_other_confirmed(tmp_path):
    got, a, b = _two_confirmed(tmp_path, 0.71)
    assert got == [(a, False), (b, False)]


def test_blocked_pick_keeps_confirmed(tmp_path):
    """A blocked hub pick must not demote the right confirmed file."""
    readme = tmp_path / "docs/README.md"
    note = tmp_path / "docs/cli.md"
    readme.parent.mkdir(parents=True)
    readme.write_text("index of docs")
    note.write_text("cli usage")
    top = _run_lookup(tmp_path, "how do I use the cli",
                      [{"score": 0.9, "originalPath": str(readme)}, {"score": 0.8, "originalPath": str(note)}],
                      {str(readme): 0.98, str(note): 0.96}, listwise_winner_prob=(str(readme), 0.95))
    assert (top[0]["path"], top[0]["possible"]) == (str(note), False)


def test_weak_none_keeps_files_and_prints_hint(tmp_path, capsys):
    """A weak none once dropped the right possible file. A weak none keeps every file as it was and tells the agent Jev leans none."""
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("x")
    b.write_text("y")
    top = _run_lookup(tmp_path, "what is the answer",
                      [{"score": 0.9, "originalPath": str(a)}, {"score": 0.7, "originalPath": str(b)}],
                      {str(a): 0.95, str(b): 0.70}, listwise_winner_prob=(ask.LISTWISE_NONE, 0.87))
    assert [(t["path"], t["possible"]) for t in top] == [(str(a), False), (str(b), True)]
    assert ask.LEANS_NONE_NOTE in capsys.readouterr().out


def test_small_file_is_judged_and_shown_whole(tmp_path, monkeypatch):
    """A small table/list file is one passage: split, Jev's confidence spread across
    the pieces and the file missed the bar though its answer was in it."""
    a = tmp_path / "map.md"
    a.write_text("| bot | risk |\n" + "| x | none |\n" * 400 + "| polymarket | real money |\n")
    assert len(a.read_text()) > ask.CONFIRM_CHUNK
    done, ctx = ask.confirm_start("which bot has real money risk?", str(a))
    assert done is None and len(ctx["chunks"]) == 1
    assert ask.best_passage(str(a)) == a.read_text()
