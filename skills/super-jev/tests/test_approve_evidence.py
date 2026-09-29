#!/usr/bin/env python3
"""Offline tests for --approve's evidence (a person's pick of a listed file, saved at once).

`ask.memory` and `ask.run_gate` are monkeypatched (fake checker): no Jev, no network.

    python3 -m pytest skills/super-jev/tests/test_approve_evidence.py -q
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "ask.py"
spec = importlib.util.spec_from_file_location("ask_approve", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

Q = "what color is the car?"


def _approve_world(tmp_path, monkeypatch, search_passages, answer="The car is blue.", assisted=("The car is blue.",)):
    """--approve --file car.md with a fake harness; returns the evidence list it sent."""
    car = tmp_path / "car.md"
    car.write_text("# Car\nSeparate play: earnings overnight.\nThe car is blue.\n")
    sdir = tmp_path / "state"
    ask.log(sdir, "lookup", question=Q, top=[{"score": 0.92, "path": str(car), "pointer": "p1"}])
    sent = {}

    def fake_memory(req):
        act = req["action"]
        if act == "sources":
            return {"status": "ok", "sources": [{"sourceId": "car", "originalPath": str(car),
                                                  "contentSHA": ask.live_sha(str(car))}]}
        if act == "search":  # memory's own ranking: must never decide the save
            return {"status": "ready", "approvalTicket": "t", "attemptId": "a1", "passages": search_passages}
        if act == "cached":
            return {"status": "cache-miss"}
        if act == "open":
            return {"status": "ok", "attemptId": "a1"}
        if act == "assist":
            assert req["references"] == [{"sourceId": "car", "startLine": n, "endLine": n} for n in (1, 3)]
            return {"status": "ready", "approvalTicket": "t2",
                    "passages": [{"sourceId": "car", "reviewedText": t} for t in assisted]}
        if act == "approve":
            sent["evidence"] = req["evidence"]
            return {"status": "saved"}
        raise AssertionError(act)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "run_gate", lambda c, p, passage=None: ("CLEAN", 0.93))
    rc = ask.approve("alice", Q, answer, sdir, file=str(car))
    return rc, sent.get("evidence")


def test_approve_file_cites_its_own_lines_when_search_tops_another_file(tmp_path, monkeypatch):
    """Regression (2026-09-25): a confirmed #1 README was refused because search's
    passages all came from other files; approve now cites the file's own reviewed lines."""
    rc, evidence = _approve_world(tmp_path, monkeypatch,
                                  [{"sourceId": "other", "reviewedText": "The car is red."}])
    assert rc == 0 and evidence == [{"sourceId": "car", "quote": "The car is blue."}]


def test_approve_quotes_the_passage_that_supports_the_answer_first(tmp_path, monkeypatch):
    rc, evidence = _approve_world(tmp_path, monkeypatch, [], assisted=(
        "Separate play: earnings overnight.", "The car is blue."))
    assert rc == 0 and evidence[0]["quote"] == "The car is blue."


def test_approve_refuses_when_no_line_matches_the_answer(tmp_path, monkeypatch):
    rc, evidence = _approve_world(tmp_path, monkeypatch, [{"sourceId": "other", "reviewedText": "x"}],
                                  answer="Purple zeppelin")
    assert rc == 1 and evidence is None


def test_approve_refuses_when_assisted_text_no_longer_supports_the_answer(tmp_path, monkeypatch):
    rc, evidence = _approve_world(tmp_path, monkeypatch, [{"sourceId": "other", "reviewedText": "x"}],
                                  assisted=("Changed since connect.",))
    assert rc == 1 and evidence is None
