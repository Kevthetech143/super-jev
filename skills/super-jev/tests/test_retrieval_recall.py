#!/usr/bin/env python3
"""Offline tests for the retrieval-recall fixes from businessfi's stress test 2
(2026-09-23): long files are judged on chosen passages instead of kept unread, a
top-routed file scoring just under the floor is kept as "possible", and a local
word search backs up routing when nothing survives.

No network and no real key.

    python3 -m pytest skills/super-jev/tests/test_retrieval_recall.py -q
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_recall", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


@pytest.fixture(autouse=True)
def no_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})


def _memory(cands):
    def fake(req):
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        return {"status": "candidates", "candidates": cands} if cands else {"status": "no-candidates"}
    return fake


def _files(tmp_path, n):
    out = []
    for i in range(n):
        out.append(tmp_path / f"f{i}.md")
        out[-1].write_text(f"file {i}")
    return out


# 1. long files: chunk 0 plus the chunks sharing the question's words, never a pass-through
def test_long_file_reads_first_chunk_plus_matching_chunks(tmp_path, monkeypatch):
    chunks = ["intro " * 580] + ["filler " * 500] * 5 + ["the shareholder meeting is in June " * 100] + ["filler " * 500]
    f = tmp_path / "long.md"
    f.write_text("".join(c[:ask.CONFIRM_CHUNK].ljust(ask.CONFIRM_CHUNK) for c in chunks))
    sent = []

    def fake_run(cmd, input, **kw):
        sent.append(json.loads(input))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"status": "candidates", "candidates": [{"score": 0.0}]}), "")
    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    score, partial, err, note = ask.confirm_one("when is the shareholder meeting", str(f))
    ids = [n["sourceId"] for n in sent[0]["catalog"]["nodes"][1:]]
    assert ids[0] == "0" and "6" in ids and len(ids) == ask.CONFIRM_CHUNKS_PER_FILE
    assert score is None and partial is False and err is None and note is None


def test_long_file_failing_the_check_is_not_kept(tmp_path, monkeypatch, capsys):
    f = tmp_path / "CLOV.md"
    f.write_text("x" * ask.CONFIRM_CHUNK * 6)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.97, "originalPath": str(f)}]))
    monkeypatch.setattr(ask.subprocess, "run", lambda cmd, input, **kw: subprocess.CompletedProcess(
        cmd, 0, json.dumps({"status": "no-candidates", "candidates": []}), ""))
    ask.lookup("whens the CLOV annual shareholder meeting", "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert "CLOV.md" not in out and "no-candidates" in out


# 2. the possible tier: only the top routed files, only between the two floors
@pytest.mark.parametrize("rank,score,shown", [(0, 0.72, True), (1, 0.61, True), (2, 0.8, True),
                                              (0, 0.55, False)])
def test_possible_tier(tmp_path, monkeypatch, capsys, rank, score, shown):
    files = _files(tmp_path, 3)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.9 - i / 10, "originalPath": str(p)} for i, p in enumerate(files)]))
    target = str(files[rank])
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({target: score} if score >= ask.POSSIBLE_FLOOR else {}, set(), None, {}))
    ask.lookup("how is it doing?", "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert (f"{score:5.2f}  {target}  [p1]  (possible:" in out) is shown
    assert ("no-candidates" in out) is not shown


def test_confirmed_file_ranks_above_a_possible_one(tmp_path, monkeypatch, capsys):
    a, b = _files(tmp_path, 2)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.99, "originalPath": str(a)}, {"score": 0.5, "originalPath": str(b)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(a): 0.7, str(b): 0.9}, set(), None, {}))
    ask.lookup("q?", "me", tmp_path / "s")
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert lines[0].startswith(" 0.90") and "possible" not in lines[0]
    assert lines[1].startswith(" 0.70") and "(possible:" in lines[1]


# 3. word-search fallback
def _cache(files, **over):
    return {str(p): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "pass": True,
                     "description": over.get(p.name, ""), "question": ""} for p in files}


def test_word_search_tolerates_typos_and_skips_changed_or_unpassed_files(tmp_path, monkeypatch):
    toll, parking, changed, failed = (tmp_path / n for n in ("toll.md", "parking.md", "changed.md", "failed.md"))
    toll.write_text("E-ZPass vehicle account balance owed: $42.")
    parking.write_text("Parking ticket paid.")
    changed.write_text("vehicle account balance")
    failed.write_text("vehicle account balance")
    cache = _cache([toll, parking, changed, failed])
    changed.write_text("vehicle account balance, edited after review")
    cache[str(failed)]["pass"] = False
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: cache)
    found = ask.word_search("how much do i owe on my vehicel acount", ["p1"])
    assert [p for _, p, _ in found] == [str(toll)]


def test_word_search_needs_the_question_words(tmp_path, monkeypatch):
    f = tmp_path / "garden.md"
    f.write_text("Tomatoes planted in May.")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([f]))
    assert ask.word_search("how do i change a bike tire", ["p1"]) == []
    assert ask.word_search("what is it", ["p1"]) == []


def test_fallback_runs_the_content_check_and_marks_matches_possible(tmp_path, monkeypatch, capsys):
    toll, other = tmp_path / "toll.md", tmp_path / "other.md"
    toll.write_text("E-ZPass vehicle account balance owed: $42.")
    other.write_text("vehicle notes")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([toll, other]))
    monkeypatch.setattr(ask, "memory", _memory([]))
    checked = []
    monkeypatch.setattr(ask, "confirm", lambda q, ps: checked.extend(ps) or ({str(toll): 0.7}, set(), None, {}))
    assert ask.lookup("what about the vehicle account toll", "me", tmp_path / "s") == 0
    out = capsys.readouterr().out
    assert str(toll) in checked
    assert f" 0.70  {toll}  [p1]  (possible: word-search match" in out and str(other) not in out


def test_fallback_that_reads_nothing_still_says_not_in_files(tmp_path, monkeypatch, capsys):
    toll = tmp_path / "toll.md"
    toll.write_text("E-ZPass vehicle account balance owed: $42.")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([toll]))
    monkeypatch.setattr(ask, "memory", _memory([]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {}))
    ask.lookup("vehicle account balance owed", "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert "no-candidates" in out and str(toll) not in out


def test_fallback_skips_files_the_content_check_already_rejected(tmp_path, monkeypatch):
    toll = tmp_path / "toll.md"
    toll.write_text("E-ZPass vehicle account balance owed: $42.")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([toll]))
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.9, "originalPath": str(toll)}]))
    calls = []
    monkeypatch.setattr(ask, "confirm", lambda q, ps: calls.append(list(ps)) or ({}, set(), None, {}))
    ask.lookup("vehicle account balance owed", "me", tmp_path / "s")
    assert calls == [[str(toll)]]


# 4. round 2: open questions ask "does it answer", value questions get no possible tier,
# the word search always runs, and ties go to the file named for the question
@pytest.mark.parametrize("q,value", [("how much is in my roth right now", True),
                                     ("whats my tesla covered call breakeven", True),
                                     ("should I sell puts on SNAP before earnings?", False),
                                     ("when do we roll the LUMN call", False)])
def test_label_by_question_kind(q, value):
    assert ask.is_value_question(q) is value
    assert (ask.confirm_label(q) == ask.CONFIRM_LABEL) is value


def test_value_question_has_no_possible_tier(tmp_path, monkeypatch, capsys):
    (a,) = _files(tmp_path, 1)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.9, "originalPath": str(a)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(a): 0.82}, set(), None, {}))
    ask.lookup("how much is in my roth ira right now", "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert str(a) not in out and "no-candidates" in out


def test_word_search_runs_even_when_routing_has_hits(tmp_path, monkeypatch, capsys):
    routed, fb = tmp_path / "routed.md", tmp_path / "feedback.md"
    routed.write_text("unrelated")
    fb.write_text("verify information before using it: read the source")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([fb]))
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.9, "originalPath": str(routed)}]))
    checked = []
    monkeypatch.setattr(ask, "confirm", lambda q, ps: checked.extend(ps) or ({str(fb): 0.9, str(routed): 0.7}, set(), None, {}))
    ask.lookup("how should I verify information before using it", "me", tmp_path / "s")
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert checked == [str(routed), str(fb)]
    assert lines[0] == f" 0.90  {fb}  [p1]" and "(possible:" in lines[1]


def test_tie_prefers_named_file_then_brain_copy_over_reuse(tmp_path, monkeypatch, capsys):
    reuse = tmp_path / "global" / "reuse" / "sendmessage-revives-teammate.md"
    local = tmp_path / "brain" / "notebook" / "sendmessage-revives-teammate.md"
    other = tmp_path / "brain" / "notes.md"
    for f in (reuse, local, other):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.9, "originalPath": str(p)} for p in (other, reuse, local)]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(p): 0.9 for p in ps}, set(), None, {}))
    ask.lookup("does sendmessage revive a stopped teammate?", "me", tmp_path / "s")
    rows = [l.split()[1] for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert rows == [str(local), str(reuse), str(other)]


# 5. round 3: routing counts in the final order, strong routes survive opinion asks,
# and the word search sees headings and a few synonyms
def test_routing_score_breaks_near_ties(tmp_path, monkeypatch, capsys):
    right, sibling = _files(tmp_path, 2)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.97, "originalPath": str(right)},
                                                 {"score": 0.5, "originalPath": str(sibling)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(right): 0.88, str(sibling): 0.9}, set(), None, {}))
    ask.lookup("q?", "me", tmp_path / "s")
    rows = [l.split()[1] for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert rows == [str(right), str(sibling)]


@pytest.mark.parametrize("q,route,kept", [("should I invest in PLUG?", 0.99, True),
                                          ("should I invest in PLUG?", 0.7, False),
                                          ("when is the PLUG meeting", 0.99, False),
                                          ("should I sell, how much is it right now", 0.99, False)])
def test_strong_route_kept_as_possible_on_opinion_asks(tmp_path, monkeypatch, capsys, q, route, kept):
    (a,) = _files(tmp_path, 1)
    monkeypatch.setattr(ask, "memory", _memory([{"score": route, "originalPath": str(a)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {}))
    ask.lookup(q, "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert (f"{ask.POSSIBLE_FLOOR:5.2f}  {a}  [p1]  (possible:" in out) is kept
    assert ("no-candidates" in out) is not kept


def test_strong_route_not_kept_when_the_check_did_not_read_it(tmp_path, monkeypatch, capsys):
    (a,) = _files(tmp_path, 1)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.99, "originalPath": str(a)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {str(a): ask.HELD_SECRET}))
    ask.lookup("should I invest in PLUG?", "me", tmp_path / "s")
    assert "(possible:" not in capsys.readouterr().out


def test_word_search_uses_heading_and_synonyms(tmp_path, monkeypatch):
    wl, other = tmp_path / "watchlist.md", tmp_path / "other.md"
    wl.write_text("# Watchlist\nTickers: CLOV, SNAP.")
    other.write_text("# Notes\nGroceries.")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([wl, other]))
    assert [p for _, p, _ in ask.word_search("time to rebalance?", ["p1"])] == [str(wl)]


def test_value_question_owed_gets_no_possible_tier(tmp_path, monkeypatch, capsys):
    toll = tmp_path / "toll.md"
    toll.write_text("E-ZPass vehicle account balance owed: $42.")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([toll]))
    monkeypatch.setattr(ask, "memory", _memory([]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(toll): 0.7}, set(), None, {}))
    assert ask.lookup("what is owed on the vehicle account", "me", tmp_path / "s") == 0
    assert "no-candidates" in capsys.readouterr().out
