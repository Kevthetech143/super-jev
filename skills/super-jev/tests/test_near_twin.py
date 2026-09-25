#!/usr/bin/env python3
"""Unit tests for the near-twin tie-break (idea-twin): when the top 2-3 ranked
results are close in score AND same folder or similar file names, one Jev
judge call over their short snippets picks the winner. Anything else leaves
ranking unchanged. Nothing here makes a live provider call.

    python3 -m pytest skills/super-jev/tests/test_near_twin.py -q
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_near_twin", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def _judge_result(winner):
    return json.dumps({"status": "candidates",
                        "candidates": [{"sourceId": winner, "score": 0.9}]})


class FakeRun:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


# ------------------------------------------------------------ is_near_twin

def test_same_folder_is_near_twin():
    assert ask.is_near_twin("/a/notes.md", "/a/summary.md")


def test_similar_names_is_near_twin():
    assert ask.is_near_twin("/a/clov-medicare.md", "/b/clov-medicare-summary.md")


def test_different_folder_different_name_is_not_near_twin():
    assert not ask.is_near_twin("/a/crypto.md", "/b/weather.md")


# ------------------------------------------------------------ judge_near_twin

def test_judge_near_twin_reads_snippets_and_returns_winner(tmp_path, monkeypatch):
    a, b = tmp_path / "notes.md", tmp_path / "notes-summary.md"
    a.write_text("full detail about the answer")
    b.write_text("a short summary of the same note")
    monkeypatch.setattr(ask.subprocess, "run", lambda *a2, **k: FakeRun(_judge_result(str(a))))
    winner = ask.judge_near_twin("what is the answer", [(0.9, str(a), "p"), (0.87, str(b), "p")])
    assert winner == str(a)


def test_judge_near_twin_returns_none_on_error(tmp_path, monkeypatch):
    a, b = tmp_path / "notes.md", tmp_path / "notes-summary.md"
    a.write_text("full detail")
    b.write_text("a short summary")
    monkeypatch.setattr(ask.subprocess, "run", lambda *a2, **k: FakeRun("", returncode=1))
    winner = ask.judge_near_twin("q", [(0.9, str(a), "p"), (0.87, str(b), "p")])
    assert winner is None


def test_judge_near_twin_returns_none_on_timeout(tmp_path, monkeypatch):
    a, b = tmp_path / "notes.md", tmp_path / "notes-summary.md"
    a.write_text("full detail")
    b.write_text("a short summary")

    def boom(*a2, **k):
        raise subprocess.TimeoutExpired(cmd="jev", timeout=60)
    monkeypatch.setattr(ask.subprocess, "run", boom)
    assert ask.judge_near_twin("q", [(0.9, str(a), "p"), (0.87, str(b), "p")]) is None


def test_judge_near_twin_skips_a_file_with_a_secret(tmp_path, monkeypatch):
    a, b = tmp_path / "notes.md", tmp_path / "notes-summary.md"
    a.write_text("looks fine but is flagged below")
    b.write_text("a short summary")
    monkeypatch.setattr(ask, "has_secret", lambda text: text.startswith("looks fine"))
    monkeypatch.setattr(ask.subprocess, "run", lambda *a2, **k: FakeRun(_judge_result(str(b))))
    # Only one file is left to send once the secret-bearing one is dropped, so
    # no judge call is made and it returns None (not enough files to judge).
    assert ask.judge_near_twin("q", [(0.9, str(a), "p"), (0.87, str(b), "p")]) is None


# ------------------------------------------------------------ apply_near_twin_tiebreak

def test_wide_gap_leaves_ranking_unchanged(tmp_path, monkeypatch):
    a, b = tmp_path / "notes.md", tmp_path / "notes-summary.md"
    a.write_text("x")
    b.write_text("y")
    called = []
    monkeypatch.setattr(ask, "judge_near_twin", lambda q, c: called.append(1) or str(b))
    top = [(0.9, str(a), "p"), (0.5, str(b), "p")]
    out = ask.apply_near_twin_tiebreak("q", top)
    assert out == top
    assert not called


def test_not_same_folder_or_similar_name_leaves_ranking_unchanged(tmp_path, monkeypatch):
    folder_a, folder_b = tmp_path / "a", tmp_path / "b"
    folder_a.mkdir()
    folder_b.mkdir()
    a, b = folder_a / "crypto.md", folder_b / "weather.md"
    a.write_text("x")
    b.write_text("y")
    called = []
    monkeypatch.setattr(ask, "judge_near_twin", lambda q, c: called.append(1) or str(b))
    top = [(0.9, str(a), "p1"), (0.87, str(b), "p2")]
    out = ask.apply_near_twin_tiebreak("q", top)
    assert out == top
    assert not called


def test_near_twin_reorders_on_judge_winner(tmp_path, monkeypatch):
    a, b, c = tmp_path / "notes.md", tmp_path / "notes-summary.md", tmp_path / "other.md"
    for f in (a, b, c):
        f.write_text("x")
    monkeypatch.setattr(ask, "judge_near_twin", lambda q, cands: str(b))
    top = [(0.9, str(a), "p"), (0.87, str(b), "p"), (0.3, str(c), "p")]
    out = ask.apply_near_twin_tiebreak("q", top)
    assert [p for _, p, _ in out] == [str(b), str(a), str(c)]


def test_inconclusive_judge_leaves_ranking_unchanged(tmp_path, monkeypatch):
    a, b = tmp_path / "notes.md", tmp_path / "notes-summary.md"
    a.write_text("x")
    b.write_text("y")
    monkeypatch.setattr(ask, "judge_near_twin", lambda q, c: None)
    top = [(0.9, str(a), "p"), (0.87, str(b), "p")]
    out = ask.apply_near_twin_tiebreak("q", top)
    assert out == top


def test_single_result_never_calls_judge(monkeypatch):
    called = []
    monkeypatch.setattr(ask, "judge_near_twin", lambda q, c: called.append(1))
    out = ask.apply_near_twin_tiebreak("q", [(0.9, "/a/x.md", "p")])
    assert out == [(0.9, "/a/x.md", "p")]
    assert not called


# ------------------------------------------------------------ lookup() integration

def _run_lookup(tmp_path, question, candidates, scores):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "miss"} if r["action"] == "cached" else
                        {"pointers": ["p1"]} if r["action"] == "panel" else
                        {"status": "candidates", "candidates": candidates})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (dict(scores), set(), None, {}))
    sdir = tmp_path / "s"
    sdir.mkdir(parents=True, exist_ok=True)
    ask.lookup(question, "me", sdir)
    lines = (sdir / "lookups.jsonl").read_text().splitlines()
    monkeypatch.undo()
    for line in reversed(lines):
        rec = json.loads(line)
        if rec.get("kind") == "lookup" and rec.get("question") == question and rec.get("top"):
            return [t["path"] for t in rec["top"]]
    raise AssertionError("no lookup log entry found")


def test_lookup_uses_judge_to_promote_sibling_note(tmp_path, monkeypatch):
    """Near-twin siblings (same folder, close scores) -- the judge call, not the
    raw score gap, decides which comes first."""
    folder = tmp_path / "brain"
    folder.mkdir()
    main_note = folder / "clov-medicare.md"
    summary_note = folder / "clov-medicare-summary.md"
    main_note.write_text("the full note")
    summary_note.write_text("a short summary")
    monkeypatch.setattr(ask, "judge_near_twin", lambda q, cands: str(summary_note))
    top = _run_lookup(
        tmp_path, "what does the medicare note say",
        [{"score": 0.90, "originalPath": str(main_note)},
         {"score": 0.88, "originalPath": str(summary_note)}],
        {str(main_note): 0.90, str(summary_note): 0.88},
    )
    assert top[0] == str(summary_note)


def test_lookup_leaves_unrelated_files_ranking_unchanged(tmp_path, monkeypatch):
    folder_a, folder_b = tmp_path / "a", tmp_path / "b"
    folder_a.mkdir()
    folder_b.mkdir()
    crypto = folder_a / "crypto.md"
    weather = folder_b / "weather.md"
    crypto.write_text("crypto notes")
    weather.write_text("weather notes")
    called = []
    monkeypatch.setattr(ask, "judge_near_twin", lambda q, cands: called.append(1) or str(weather))
    top = _run_lookup(
        tmp_path, "what is the crypto market doing",
        [{"score": 0.94, "originalPath": str(crypto)},
         {"score": 0.40, "originalPath": str(weather)}],
        {str(crypto): 0.94, str(weather): 0.40},
    )
    assert top[0] == str(crypto)
    assert not called
