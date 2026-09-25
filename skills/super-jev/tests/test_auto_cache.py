#!/usr/bin/env python3
"""Offline tests for ask.py auto-cache (--answer), --miss un-save and approver on hits.

`ask.memory` and `ask.run_gate` are monkeypatched: no network, no live harness.

    python3 -m pytest skills/super-jev/tests/test_auto_cache.py -q
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "ask.py"
spec = importlib.util.spec_from_file_location("ask_auto", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

Q, A = "what color is the car?", "The car is blue."


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A prior lookup whose top file is fresh, a fake harness, a CLEAN gate."""
    note = tmp_path / "car.md"
    note.write_text("The car is blue.\n")
    sdir = tmp_path / "state"
    ask.log(sdir, "lookup", question=Q, top=[{"score": 0.95, "path": str(note), "pointer": "p1"}])
    calls, cache = [], {}

    def fake_memory(req):
        calls.append(req)
        act = req["action"]
        if act == "sources":
            return {"status": "ok", "sources": [{"sourceId": "one", "originalPath": str(note),
                                                  "contentSHA": world_state["sha"]}]}
        if act == "open":
            return {"status": "ok", "attemptId": "a1"}
        if act == "assist":
            assert req["references"] == [{"sourceId": "one", "startLine": 1, "endLine": 1}]  # line 1 is the answer
            return {"status": "ready", "approvalTicket": "t1",
                    "passages": [{"sourceId": "one", "reviewedText": "The car is blue."}]}
        if act == "approve":
            cache[req["principal"]] = req["answer"]
            return {"status": "saved"}
        if act == "cached":
            return ({"status": "verified-cache-hit", "answer": cache["alice"], "evidence": []}
                    if "alice" in cache else {"status": "cache-miss"})
        if act == "forget":
            return {"status": "forgotten", "pointers": ["p1"]} if cache.pop("alice", None) else {"status": "not-cached"}
        raise AssertionError(act)

    world_state = {"sha": hashlib.sha256(note.read_bytes()).hexdigest(), "note": note, "sdir": sdir,
                   "calls": calls, "cache": cache}
    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.93))
    monkeypatch.delenv("SUPERJEV_AUTO_CACHE", raising=False)
    return world_state


def test_clean_saves_marked_auto_check(world, capsys):
    assert ask.auto_approve("alice", Q, A, world["sdir"]) == 0
    assert world["cache"]["alice"] == A
    rec = ask.approver(world["sdir"], Q)
    assert rec["approved_by"] == "auto-check"
    assert rec["evidence_file"] == str(world["note"]) and rec["score"] == 0.93
    assert "approve: saved" in capsys.readouterr().out


@pytest.mark.parametrize("verdict", ["READ", "REJECT", "ERROR"])
def test_read_or_blocked_does_not_save(world, monkeypatch, capsys, verdict):
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: (verdict, 0.5))
    assert ask.auto_approve("alice", Q, A, world["sdir"]) == 1
    assert not world["cache"]
    assert f"not saved: check gate verdict {verdict}" in capsys.readouterr().out


def test_stale_file_does_not_save(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "run_gate", lambda *a: pytest.fail("gate must not run on a stale file"))
    world["note"].write_text("The car is red.\n")
    assert ask.auto_approve("alice", Q, A, world["sdir"]) == 1
    assert not world["cache"]
    assert "not saved: stale" in capsys.readouterr().out


def test_secret_held_does_not_save(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "run_gate", lambda *a: pytest.fail("gate must not run on a secret"))
    monkeypatch.setattr(ask, "has_secret", lambda text: "blue" in text)
    assert ask.auto_approve("alice", Q, A, world["sdir"]) == 1
    assert not world["cache"]
    assert "not saved: secret-held" in capsys.readouterr().out


def test_no_auto_saves_nothing(world, monkeypatch, capsys):
    monkeypatch.setenv("SUPERJEV_AUTO_CACHE", "0")
    assert ask.auto_approve("alice", Q, A, world["sdir"]) == 0
    assert not world["cache"]
    assert "auto-cache is off" in capsys.readouterr().out


def test_uncitable_file_does_not_save(world, monkeypatch, capsys):
    """If car.md's own lines cannot be cited, nothing is saved (no other file stands in)."""
    orig_memory = ask.memory

    def mismatched_memory(req):
        if req["action"] == "assist":
            return {"status": "error", "reason": "reference does not resolve to reviewed preparation"}
        return orig_memory(req)

    monkeypatch.setattr(ask, "memory", mismatched_memory)
    assert ask.auto_approve("alice", Q, A, world["sdir"]) == 1
    assert not world["cache"]
    out = capsys.readouterr().out
    assert "not saved" in out and "could be cited" in out


def test_low_gate_score_does_not_save(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.79))
    assert ask.auto_approve("alice", Q, A, world["sdir"]) == 1
    assert not world["cache"]
    assert "not saved: check gate score 0.79 is below the 0.80 auto-save floor" in capsys.readouterr().out


def test_find_top_skips_malformed_lines(tmp_path):
    sdir = tmp_path / "state"
    sdir.mkdir(parents=True)
    lookups = sdir / "lookups.jsonl"
    good = json.dumps({"kind": "lookup", "question": Q, "top": [{"score": 0.9, "path": "/x", "pointer": "p1"}]})
    lookups.write_text("not json at all\n" + good + "\n{truncated\n")
    assert ask.find_top(sdir, Q) == {"score": 0.9, "path": "/x", "pointer": "p1"}


def test_miss_removes_saved_answer(world, capsys):
    ask.auto_approve("alice", Q, A, world["sdir"])
    assert ask.miss("alice", Q, "it was in the garage log", world["sdir"]) == 0
    assert not world["cache"]
    assert "un-saved" in capsys.readouterr().out and "auto-check" in (world["sdir"] / "approvals.jsonl").read_text()
    assert ask.memory({"action": "cached", "principal": "alice", "question": Q})["status"] == "cache-miss"


def test_hit_shows_approver_auto_and_human(world, capsys):
    ask.auto_approve("alice", Q, A, world["sdir"])
    capsys.readouterr()
    assert ask.lookup(Q, "alice", world["sdir"]) == 0
    assert "approved_by: auto-check" in capsys.readouterr().out
    ask.record_approver(world["sdir"], Q, "human")
    ask.lookup(Q, "alice", world["sdir"])
    assert "approved_by: human" in capsys.readouterr().out


@pytest.mark.parametrize("side, want", [("TIME_SENSITIVE", "TIME_SENSITIVE"),
                                        ("NOT_TIME_SENSITIVE", "CLEAN")])
def test_run_gate_blocks_a_time_sensitive_answer(monkeypatch, side, want):
    """check --claim leaves time_sensitive advisory, but a dated fact (price,
    breakeven) must never auto-cache: run_gate turns that CLEAN into a refusal."""
    import subprocess
    out = f"  c1   SUPPORTED      1.00  x\n\n  time_sensitive     {side:20s} 0.90\n"
    body = json.dumps({"verdict": "CLEAN", "details": {"stdout": out}})
    monkeypatch.setattr(ask.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, body, ""))
    assert ask.run_gate("Q A", "/x.md") == (want, 1.0)


def test_run_gate_ignores_a_low_confidence_time_sensitive_label(monkeypatch):
    """Regression for the health-fitness test #7 bug (2026-09-25): jev's reply
    table prints whichever label won the choice even at low confidence, so a
    near coin-flip TIME_SENSITIVE call (0.05-0.10) must not block a correct,
    fully-supported answer the way a confident one (>=0.80) does."""
    import subprocess
    out = "  c1   SUPPORTED      1.00  x\n\n  time_sensitive     TIME_SENSITIVE       0.05\n"
    body = json.dumps({"verdict": "CLEAN", "details": {"stdout": out}})
    monkeypatch.setattr(ask.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, body, ""))
    assert ask.run_gate("Q A", "/x.md") == ("CLEAN", 1.0)


def test_file_ask_ranked_saves_even_when_search_would_not_match(world, monkeypatch, capsys):
    """businessfi geico 2026-09-25: ask() ranked the file #1 but memory's description-only
    search said no-match. The save cites the file itself and never asks search."""
    orig, gated = ask.memory, {}

    def no_search(req):
        if req["action"] == "search":
            return {"status": "no-match", "attemptId": "s1"}
        return orig(req)

    monkeypatch.setattr(ask, "memory", no_search)
    monkeypatch.setattr(ask, "run_gate", lambda c, p, passage=None: gated.update(p=passage) or ("CLEAN", 0.93))
    assert ask.auto_approve("alice", Q, A, world["sdir"]) == 0
    assert world["cache"]["alice"] == A
    assert gated["p"] == "The car is blue."  # the gate saw the passage that gets saved
    assert not any(c["action"] == "search" for c in world["calls"])


def test_passage_that_does_not_support_the_answer_is_refused(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "run_gate", lambda c, p, passage=None:
                        ("REJECT", 0.1) if passage else ("CLEAN", 0.93))
    assert ask.auto_approve("alice", Q, A, world["sdir"]) == 1
    assert not world["cache"]
    assert "not saved: check gate verdict REJECT" in capsys.readouterr().out
