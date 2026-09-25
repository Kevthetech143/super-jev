#!/usr/bin/env python3
"""Offline tests for the pick trail (--used / --flush-picks / --pending-picks).

`ask.memory` and `ask.run_gate` are monkeypatched (fake checker): no Jev, no network.

    python3 -m pytest skills/super-jev/tests/test_pick_trail.py -q
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "ask.py"
spec = importlib.util.spec_from_file_location("ask_picks", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

Q = "what color is the car?"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """One trace listing two files; a fake harness whose search tops whichever file is fresh."""
    a, b = tmp_path / "index.md", tmp_path / "car.md"
    a.write_text("index\n"); b.write_text("The car is blue.\n")
    sdir = tmp_path / "state"
    ask.write_trace(sdir, kind="trace", lookup_id="L1", question=Q,
                    final_ranked=[{"score": 0.9, "path": str(a), "pointer": "p1"},
                                  {"score": 0.8, "path": str(b), "pointer": "p1"}])
    cache = {}

    def fake_memory(req):
        act = req["action"]
        if act == "sources":
            return {"status": "ok", "sources": [{"sourceId": "b", "originalPath": str(b),
                                                  "contentSHA": hashlib.sha256(b.read_bytes()).hexdigest()}]}
        if act == "search":
            return {"status": "ready", "approvalTicket": "t", "passages": [{"sourceId": "b", "reviewedText": "blue"}]}
        if act == "approve":
            cache[Q] = req["answer"]
            return {"status": "saved"}
        raise AssertionError(act)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "run_gate", lambda claim, path: ("CLEAN", 0.93))
    monkeypatch.setenv("SUPERJEV_PICK_BATCH", "5")
    monkeypatch.delenv("SUPERJEV_AUTO_CACHE", raising=False)
    return {"sdir": sdir, "b": b, "cache": cache}


def traces(sdir, kind):
    return [r for r in map(json.loads, (sdir / "traces.jsonl").read_text().splitlines()) if r["kind"] == kind]


def test_used_rank_queues_pick_and_logs_rank(world):
    assert ask.record_pick("alice", world["sdir"], "last", rank=2, answer="The car is blue.") == 0
    [pick] = ask.load_picks(world["sdir"])
    assert pick["file"] == str(world["b"]) and pick["rank"] == 2 and pick["answer"] == "The car is blue."
    [t] = traces(world["sdir"], "pick")
    assert t["lookup_id"] == "L1" and t["rank"] == 2
    assert not world["cache"]  # below batch size: nothing checked yet


def test_used_file_and_trace_id(world):
    assert ask.record_pick("alice", world["sdir"], "L1", file="car.md") == 0
    assert ask.load_picks(world["sdir"])[0]["rank"] == 2


def test_fifth_pick_flushes_and_saves_clean(world, capsys):
    for _ in range(5):
        ask.record_pick("alice", world["sdir"], "last", rank=2, answer="The car is blue.")
    assert world["cache"][Q] == "The car is blue."
    assert ask.approver(world["sdir"], Q)["approved_by"] == "agent-pick+check"
    assert ask.load_picks(world["sdir"]) == []
    assert any(o["result"] == "right" for o in traces(world["sdir"], "outcome"))


@pytest.mark.parametrize("verdict", ["REJECT", "TIME_SENSITIVE"])
def test_refused_verdict_stays_listed_under_dropped_section(world, monkeypatch, verdict, capsys):
    """A refused pick used to vanish from --pending-picks with no lasting trace
    beyond lookups.jsonl (businessfi report, 2026-09-25: a "fair" TIME_SENSITIVE
    drop looked identical to a silent bug). It now stays in the file tagged
    "dropped": why, listed under --pending-picks' "Dropped (not saved)"
    section, until a human clears it with an explicit --confirm-pick or
    --drop-pick."""
    monkeypatch.setattr(ask, "run_gate", lambda c, p: (verdict, 0.9))
    ask.record_pick("alice", world["sdir"], "last", rank=2, answer="x")
    ask.flush_picks("alice", world["sdir"])
    picks = ask.load_picks(world["sdir"])
    assert not world["cache"]
    [pick] = picks
    assert pick["dropped"] == f"check gate verdict {verdict} (only CLEAN saves)"
    assert "why" not in pick
    capsys.readouterr()
    ask.pending_picks(world["sdir"])
    out = capsys.readouterr().out
    assert "Dropped (not saved):" in out
    assert f"check gate verdict {verdict}" in out.split("Dropped (not saved):")[1]
    assert "--drop-pick" in out
    # not cleared just by being shown once -- only an explicit ack clears it
    ask.pending_picks(world["sdir"])
    assert ask.load_picks(world["sdir"]) == [pick]
    assert ask.settle_pick("alice", world["sdir"], pick["id"], drop=True) == 0
    assert ask.load_picks(world["sdir"]) == []


def test_dropped_pick_is_not_re_sent_to_the_gate_on_a_second_flush(world, monkeypatch):
    """The skip guard in _flush_picks used to check only "why" in pick, but a
    dropped pick carries "dropped" instead -- so it looked unchecked again and
    was re-sent to the gate (and re-counted toward the batch) on every later
    flush. Fixed: a dropped pick is skipped exactly like an unsure one."""
    calls = []
    monkeypatch.setattr(ask, "run_gate", lambda c, p: (calls.append(1) or "TIME_SENSITIVE", 0.9))
    ask.record_pick("alice", world["sdir"], "last", rank=2, answer="x")
    ask.flush_picks("alice", world["sdir"])
    assert len(calls) == 1
    [pick] = ask.load_picks(world["sdir"])
    assert pick["dropped"]
    ask.flush_picks("alice", world["sdir"])
    ask.flush_picks("alice", world["sdir"])
    assert len(calls) == 1  # never re-sent to the gate
    assert ask.load_picks(world["sdir"]) == [pick]


def test_stale_file_stays_listed_under_dropped_section(world, monkeypatch):
    monkeypatch.setattr(ask, "run_gate", lambda *a: pytest.fail("gate must not run on a stale file"))
    ask.record_pick("alice", world["sdir"], "last", rank=1, answer="x")  # index.md: not in the pointer's sources
    ask.flush_picks("alice", world["sdir"])
    picks = ask.load_picks(world["sdir"])
    assert not world["cache"]
    [pick] = picks
    assert pick["dropped"].startswith("stale:")
    assert ask.settle_pick("alice", world["sdir"], pick["id"], drop=True) == 0


def test_unsure_stays_listed_then_confirm_or_drop(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "run_gate", lambda c, p: ("READ", 0.5))
    ask.record_pick("alice", world["sdir"], "last", rank=2, answer="The car is blue.")
    ask.record_pick("alice", world["sdir"], "last", rank=1)  # no answer text
    ask.flush_picks("alice", world["sdir"])
    picks = ask.load_picks(world["sdir"])
    assert len(picks) == 2 and all("why" in p for p in picks)
    capsys.readouterr()
    ask.pending_picks(world["sdir"])
    out = capsys.readouterr().out
    assert "check gate verdict READ" in out and "--confirm-pick" in out
    assert ask.settle_pick("alice", world["sdir"], picks[1]["id"], drop=True) == 0
    assert ask.settle_pick("alice", world["sdir"], picks[0]["id"]) == 0
    assert world["cache"][Q] == "The car is blue." and ask.load_picks(world["sdir"]) == []


def test_checked_unsure_picks_do_not_count_toward_batch(world, monkeypatch):
    monkeypatch.setattr(ask, "run_gate", lambda c, p: ("READ", 0.5))
    monkeypatch.setenv("SUPERJEV_PICK_BATCH", "2")
    calls = []
    monkeypatch.setattr(ask, "flush_picks", lambda *a: calls.append(1) or 0)
    ask.save_picks(world["sdir"], [{"id": "x", "why": "unsure"}])
    ask.record_pick("alice", world["sdir"], "last", rank=2, answer="a")
    assert calls == []


def test_bad_rank_and_unknown_trace(world, capsys):
    assert ask.record_pick("alice", world["sdir"], "last", rank=9) == 1
    assert ask.record_pick("alice", world["sdir"], "nope", rank=1) == 1
    assert ask.load_picks(world["sdir"]) == []


def _approve_world(tmp_path, monkeypatch, search_passages, answer="The car is blue.", assisted="The car is blue."):
    """--approve --file car.md with a fake harness; returns the evidence list it sent."""
    car = tmp_path / "car.md"
    car.write_text("# Car\nSeparate play: earnings overnight.\nThe car is blue.\n")
    sdir = tmp_path / "state"
    ask.log(sdir, "lookup", question=Q, top=[{"score": 0.92, "path": str(car), "pointer": "p1"}])
    sent = {}

    def fake_memory(req):
        act = req["action"]
        if act == "sources":
            return {"status": "ok", "sources": [{"sourceId": "car", "originalPath": str(car)}]}
        if act == "search":
            return {"status": "ready", "approvalTicket": "t", "attemptId": "a1", "passages": search_passages}
        if act == "assist":
            [ref] = req["references"]
            assert ref == {"sourceId": "car", "startLine": 3, "endLine": 3}
            return {"status": "ready", "approvalTicket": "t2",
                    "passages": [{"sourceId": "car", "reviewedText": assisted}]}
        if act == "approve":
            sent["evidence"] = req["evidence"]
            return {"status": "saved"}
        raise AssertionError(act)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.approve("alice", Q, answer, sdir, file=str(car))
    return rc, sent.get("evidence")


def test_approve_file_cites_its_own_lines_when_search_tops_another_file(tmp_path, monkeypatch):
    """businessfi retest 2026-09-25: a confirmed #1 README was refused because search's
    passages all came from other files; approve now cites the file's own reviewed lines."""
    rc, evidence = _approve_world(tmp_path, monkeypatch,
                                  [{"sourceId": "other", "reviewedText": "The car is red."}])
    assert rc == 0 and evidence == [{"sourceId": "car", "quote": "The car is blue."}]


def test_approve_quotes_the_passage_that_supports_the_answer_first(tmp_path, monkeypatch):
    rc, evidence = _approve_world(tmp_path, monkeypatch, [
        {"sourceId": "car", "reviewedText": "Separate play: earnings overnight."},
        {"sourceId": "car", "reviewedText": "The car is blue."}])
    assert rc == 0 and evidence[0]["quote"] == "The car is blue."


def test_approve_refuses_when_no_line_matches_the_answer(tmp_path, monkeypatch):
    rc, evidence = _approve_world(tmp_path, monkeypatch, [{"sourceId": "other", "reviewedText": "x"}],
                                  answer="Purple zeppelin")
    assert rc == 1 and evidence is None


def test_approve_refuses_when_assisted_text_no_longer_supports_the_answer(tmp_path, monkeypatch):
    rc, evidence = _approve_world(tmp_path, monkeypatch, [{"sourceId": "other", "reviewedText": "x"}],
                                  assisted="Changed since connect.")
    assert rc == 1 and evidence is None
