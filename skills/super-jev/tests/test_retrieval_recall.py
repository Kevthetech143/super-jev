"""Offline source retrieval controls; provider decisions are mocked."""
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


def test_long_file_reads_matching_chunks_within_budget(tmp_path, monkeypatch):
    chunks = ["intro " * 580] + ["filler " * 500] * 5 + ["the shareholder meeting is in June " * 100] + ["filler " * 500]
    f = tmp_path / "long.md"
    f.write_text("".join(c[:ask.CONFIRM_CHUNK].ljust(ask.CONFIRM_CHUNK) for c in chunks))
    sent = []

    def fake_run(cmd, input, **kw):
        sent.append(json.loads(input))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"status": "no-candidates", "candidates": []}), "")
    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    score, partial, err, note = ask.confirm_one("when is the shareholder meeting", str(f))
    leaves = sent[0]["catalog"]["nodes"][1:]
    ids = [n["sourceId"] for n in leaves]
    assert "6" in ids and len(ids) < len(chunks)
    assert sum(len(n["description"]) for n in leaves) <= ask.READ_CHARS
    assert score is None and partial is False and err is None and note is None


def test_long_file_failing_the_check_is_not_kept(tmp_path, monkeypatch, capsys):
    f = tmp_path / "CLOV.md"
    f.write_text("x" * ask.CONFIRM_CHUNK * 6)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.97, "originalPath": str(f)}]))
    monkeypatch.setattr(ask.subprocess, "run", lambda cmd, input, **kw: subprocess.CompletedProcess(
        cmd, 0, json.dumps({"status": "no-candidates", "candidates": []}), ""))
    ask.lookup("whens the CLOV annual shareholder meeting", "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert "OUTCOME: not-found" in out
    assert not any(l.strip().endswith("[p1]") or "CLOV.md  [" in l for l in out.splitlines())
    assert "no answer confirmed. Closest:" in out and "CLOV.md" in out




def test_only_file_meeting_evidence_threshold_is_returned(tmp_path, monkeypatch, capsys):
    a, b = _files(tmp_path, 2)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.99, "originalPath": str(a)}, {"score": 0.5, "originalPath": str(b)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(b): 0.9}, set(), None, {}))
    ask.lookup("q?", "me", tmp_path / "s")
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip() and not l.startswith("OUTCOME:")]
    assert lines[0].startswith(" 0.90") and "possible" not in lines[0]
    assert not any(str(a) in line for line in lines)


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


def test_unrouted_lexical_match_enters_ordinary_content_check(tmp_path, monkeypatch, capsys):
    toll, other = tmp_path / "toll.md", tmp_path / "other.md"
    toll.write_text("E-ZPass vehicle account balance owed: $42.")
    other.write_text("vehicle notes")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([toll, other]))
    monkeypatch.setattr(ask, "memory", _memory([]))
    checked = []
    monkeypatch.setattr(ask, "confirm", lambda q, ps: checked.extend(ps) or ({str(toll): 0.9}, set(), None, {}))
    assert ask.lookup("what about the vehicle account toll", "me", tmp_path / "s") == 0
    out = capsys.readouterr().out
    assert checked == [str(toll)]
    assert "OUTCOME: found" in out and str(toll) in out


def test_fallback_that_reads_nothing_still_says_not_in_files(tmp_path, monkeypatch, capsys):
    toll = tmp_path / "toll.md"
    toll.write_text("E-ZPass vehicle account balance owed: $42.")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([toll]))
    monkeypatch.setattr(ask, "memory", _memory([]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {}))
    ask.lookup("vehicle account balance owed", "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert "OUTCOME: not-found" in out and str(toll) not in out


def test_fallback_skips_files_the_content_check_already_rejected(tmp_path, monkeypatch):
    toll = tmp_path / "toll.md"
    toll.write_text("E-ZPass vehicle account balance owed: $42.")
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([toll]))
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.9, "originalPath": str(toll)}]))
    calls = []
    monkeypatch.setattr(ask, "confirm", lambda q, ps: calls.append(list(ps)) or ({}, set(), None, {}))
    ask.lookup("vehicle account balance owed", "me", tmp_path / "s")
    assert calls == [[str(toll)]]


@pytest.mark.parametrize("q,value", [("how much is in my roth right now", True),
                                     ("whats my tesla covered call breakeven", True),
                                     ("should I sell puts on SNAP before earnings?", False),
                                     ("when do we roll the LUMN call", False)])
def test_label_by_question_kind(q, value):
    assert ask.confirm_label(q) == ask.SOURCE_LABEL


def test_value_question_has_no_possible_tier(tmp_path, monkeypatch, capsys):
    (a,) = _files(tmp_path, 1)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.9, "originalPath": str(a)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {}))
    ask.lookup("how much is in my roth ira right now", "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert str(a) not in out and "OUTCOME: not-found" in out


def test_ordinary_lookup_checks_routed_and_word_matched_candidates(tmp_path, monkeypatch, capsys):
    routed, fb = tmp_path / "routed.md", tmp_path / "feedback.md"
    routed.write_text("unrelated")
    fb.write_text("verify information before using it: read the source")
    # the TOC read list covers the set's cached files: the routed-style pick (routed.md) and the word match
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: _cache([routed, fb]))
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.9, "originalPath": str(routed)}]))
    checked = []
    monkeypatch.setattr(ask, "confirm", lambda q, ps: checked.extend(ps) or ({str(fb): 0.9}, set(), None, {}))
    ask.lookup("how should I verify information before using it", "me", tmp_path / "s")
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert checked == [str(routed), str(fb)]
    assert lines[0].startswith("OUTCOME: found")
    assert any(str(fb) in line for line in lines) and not any(str(routed) in line for line in lines)


def test_exact_ties_have_stable_path_order(tmp_path, monkeypatch, capsys):
    reuse = tmp_path / "global" / "reuse" / "sendmessage-revives-teammate.md"
    local = tmp_path / "brain" / "notebook" / "sendmessage-revives-teammate.md"
    other = tmp_path / "brain" / "notes.md"
    for f in (reuse, local, other):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.9, "originalPath": str(p)} for p in (other, reuse, local)]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(p): 0.9 for p in ps}, set(), None, {}))
    ask.lookup("does sendmessage revive a stopped teammate?", "me", tmp_path / "s")
    rows = [l.split()[1] for l in capsys.readouterr().out.splitlines() if l.strip() and not l.startswith("OUTCOME:")]
    assert rows == sorted([str(local), str(reuse), str(other)], reverse=True)


def test_content_score_wins_over_routing_when_not_a_true_tie(tmp_path, monkeypatch, capsys):
    lower_content, higher_content = _files(tmp_path, 2)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.97, "originalPath": str(lower_content)},
                                                 {"score": 0.5, "originalPath": str(higher_content)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(lower_content): 0.88, str(higher_content): 0.9}, set(), None, {}))
    ask.lookup("q?", "me", tmp_path / "s")
    rows = [l.split()[1] for l in capsys.readouterr().out.splitlines() if l.strip() and not l.startswith("OUTCOME:")]
    assert rows == [str(higher_content), str(lower_content)]


def test_routing_score_breaks_true_content_tie(tmp_path, monkeypatch, capsys):
    right, sibling = _files(tmp_path, 2)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.97, "originalPath": str(right)},
                                                 {"score": 0.5, "originalPath": str(sibling)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(right): 0.9, str(sibling): 0.9}, set(), None, {}))
    ask.lookup("q?", "me", tmp_path / "s")
    rows = [l.split()[1] for l in capsys.readouterr().out.splitlines() if l.strip() and not l.startswith("OUTCOME:")]
    assert rows == [str(right), str(sibling)]


@pytest.mark.parametrize("q,route,kept", [("should I invest in PLUG?", 0.99, False),
                                          ("should I invest in PLUG?", 0.7, False),
                                          ("when is the PLUG meeting", 0.99, False),
                                          ("should I sell, how much is it right now", 0.99, False)])
def test_strong_route_does_not_rescue_opinion_misses(tmp_path, monkeypatch, capsys, q, route, kept):
    (a,) = _files(tmp_path, 1)
    monkeypatch.setattr(ask, "memory", _memory([{"score": route, "originalPath": str(a)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {}))
    ask.lookup(q, "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert (f"{ask.SOURCE_FLOOR:5.2f}  {a}  [p1]  (possible:" in out) is kept
    assert ("OUTCOME: not-found" in out) is not kept


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
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {}))
    assert ask.lookup("what is owed on the vehicle account", "me", tmp_path / "s") == 1  # not-found
    assert "OUTCOME: not-found" in capsys.readouterr().out


def _big_pending(tmp_path):
    f = tmp_path / "pending.md"
    f.write_text("".join(("## Section %d\n" % i + "item " * 700)[:ask.CONFIRM_CHUNK - 1] + "\n"
                         for i in range(24)))
    return f


@pytest.mark.parametrize("route,judge,kept", [(0.96, 0.57, False), (0.7, 0.57, False),
                                              (0.96, 0.3, False)])
def test_big_rejected_file_is_not_rescued(tmp_path, monkeypatch, capsys, route, judge, kept):
    f = _big_pending(tmp_path)
    monkeypatch.setattr(ask, "memory", _memory([{"score": route, "originalPath": str(f)}]))
    monkeypatch.setattr(ask.subprocess, "run", lambda cmd, input, **kw: subprocess.CompletedProcess(
        cmd, 0, json.dumps({"status": "no-candidates", "candidates": []}), ""))
    ask.lookup("what is on the health-fitness pending to-do list", "me", tmp_path / "s")
    out = capsys.readouterr().out
    assert (str(f) in out) is kept
    if kept:
        assert "start at section: Section 5" in out


def test_big_file_not_kept_for_live_value_ask(tmp_path, monkeypatch, capsys):
    f = _big_pending(tmp_path)
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.97, "originalPath": str(f)}]))
    monkeypatch.setattr(ask.subprocess, "run", lambda cmd, input, **kw: subprocess.CompletedProcess(
        cmd, 0, json.dumps({"status": "no-candidates", "candidates": []}), ""))
    ask.lookup("what is the balance right now", "me", tmp_path / "s")
    assert str(f) not in capsys.readouterr().out


@pytest.mark.parametrize("q,label", [
    ("what is on the health-fitness pending to-do list", "ANSWER_LABEL"),
    ("what's on my todo list", "ANSWER_LABEL"),
    ("what car do I have", "CONFIRM_LABEL"),
    ("what is the list price", "CONFIRM_LABEL"),
    ("what is the phone number for Riverside ENT on Marvin's referral options list", "CONFIRM_LABEL")])
def test_list_questions_get_same_criterion(q, label):
    assert ask.confirm_label(q) == ask.SOURCE_LABEL


def test_file_length_does_not_rescue_rejected_content(tmp_path, monkeypatch, capsys):
    big, small = _big_pending(tmp_path), tmp_path / "small.md"
    small.write_text("to-do: call the clinic")
    monkeypatch.setattr(ask, "memory", _memory([{"score": 0.98, "originalPath": str(big)},
                                                {"score": 0.3, "originalPath": str(small)}]))

    def fake_run(cmd, input, **kw):
        big_file = len(json.loads(input)["catalog"]["nodes"]) > 2
        sc = {"score": 0.57, "sourceId": "2"} if big_file else {"score": 0.7, "sourceId": "0"}
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"status": "candidates", "candidates": [sc]}), "")
    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    ask.lookup("what is on the pending to-do list", "me", tmp_path / "s")
    lines = [l for l in capsys.readouterr().out.splitlines() if "(possible:" in l]
    assert lines == []
