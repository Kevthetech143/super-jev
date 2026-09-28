"""Offline source retrieval controls; provider decisions are mocked."""
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
        monkeypatch.setenv("SUPERJEV_LISTWISE", "1" if listwise_enabled else "0")
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
    shown = seen[0][0]["file_1"]["text"]  # led by the file's title, cut at SUBJECT_CHARS
    assert shown == "x" * ask.SUBJECT_CHARS + ask.SUBJECT_SEP + "the answer lives in a late passage"


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


@pytest.mark.parametrize("enabled", [True, False])
def test_ordinary_retrieval_never_invokes_answer_picker(tmp_path, monkeypatch, enabled):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("equipment costs 300 credits")
    b.write_text("freight costs 20 credits")
    def forbidden(*args):
        raise AssertionError("source retrieval must retain independently relevant evidence")
    monkeypatch.setattr(ask, "judge_listwise", forbidden)
    top = _run_lookup(tmp_path, "total purchase cost including freight",
                      [{"score": .9, "originalPath": str(a)}, {"score": .7, "originalPath": str(b)}],
                      {str(a): .95, str(b): .9}, listwise_enabled=enabled)
    assert [t["path"] for t in top] == [str(a), str(b)]
    assert all(not t["possible"] for t in top)


def test_small_file_is_judged_and_shown_whole(tmp_path, monkeypatch):
    a = tmp_path / "map.md"
    a.write_text("| bot | risk |\n" + "| x | none |\n" * 400 + "| polymarket | real money |\n")
    assert len(a.read_text()) > ask.CONFIRM_CHUNK
    done, ctx = ask.confirm_start("which bot has real money risk?", str(a))
    assert done is None and len(ctx["chunks"]) == 1
    assert ask.best_passage(str(a)) == a.read_text()
