#!/usr/bin/env python3
"""Offline tests for live decision traces, outcome linking, the trace report,
and Jev's voice line -- all in ask.py.

No network, no live harness, no live provider: `ask.memory` is monkeypatched
at the module level, `ask.confirm`/`ask.word_search` are monkeypatched to skip
the real content-check subprocess and local file search, and the state
directory is always an isolated tmp_path.

    python3 -m pytest skills/super-jev/tests/test_traces_and_voice.py -q
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "ask.py"

spec = importlib.util.spec_from_file_location("ask", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def _no_candidates_memory(req):
    if req["action"] == "cached":
        return {"status": "cache-miss", "checked": []}
    if req["action"] == "panel":
        return {"pointers": ["p1"]}
    if req["action"] == "navigate":
        return {"status": "no-candidates", "candidates": []}
    raise AssertionError(req)


def read_jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------- voice line

def test_miss_lookup_prints_voice_line_as_the_last_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", _no_candidates_memory)
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    rc = ask.lookup("where is the deed?", "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert out[-1] == ask.VOICE_LINE


def test_only_errors_lookup_prints_voice_line(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1"]}
        if req["action"] == "navigate":
            return {"status": "preparation-required", "reason": "scope-change"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    rc = ask.lookup("q", "alice", tmp_path)

    assert rc == 1
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert out[-1] == ask.VOICE_LINE


def test_nothing_connected_prints_voice_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", lambda req: (
        {"status": "cache-miss", "checked": []} if req["action"] == "cached" else
        {"pointers": []} if req["action"] == "panel" else
        (_ for _ in ()).throw(AssertionError(req))
    ))
    rc = ask.lookup("q", "alice", tmp_path)

    assert rc == 1
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert out[-1] == ask.VOICE_LINE


def test_hit_lookup_prints_no_voice_line(tmp_path, monkeypatch, capsys):
    note = tmp_path / "hit.md"
    note.write_text("The car is blue.\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1"]}
        if req["action"] == "navigate":
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(note)}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    monkeypatch.setattr(ask, "confirm", lambda q, paths: ({str(note): 0.95}, set(), None, {}))
    rc = ask.lookup("what color is the car?", "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert ask.VOICE_LINE not in out


def test_cache_hit_prints_no_voice_line_and_writes_no_trace(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "verified-cache-hit", "answer": "Blue.",
                     "evidence": [], "freshness": {"mode": "snapshot"}, "resolution": "retrieval"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("what color?", "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert ask.VOICE_LINE not in out
    assert not (tmp_path / "traces.jsonl").is_file()


# -------------------------------------------------------------------- traces

def test_live_miss_lookup_writes_a_trace_line_with_tier_none(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", _no_candidates_memory)
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    ask.lookup("where is the deed?", "alice", tmp_path)
    capsys.readouterr()

    recs = read_jsonl(tmp_path / "traces.jsonl")
    traces = [r for r in recs if r.get("kind") == "trace"]
    assert len(traces) == 1
    tr = traces[0]
    assert tr["question"] == "where is the deed?"
    assert tr["tier"] == "none"
    assert tr["final_ranked"] == []
    assert "p1" in tr["routing"]
    assert "total_secs" in tr["timings"]
    assert tr["lookup_id"]


def test_live_hit_lookup_writes_a_trace_line_with_tier_confirmed_and_ranked_list(tmp_path, monkeypatch):
    note = tmp_path / "hit.md"
    note.write_text("The car is blue.\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1"]}
        if req["action"] == "navigate":
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(note)}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    monkeypatch.setattr(ask, "confirm", lambda q, paths: ({str(note): 0.95}, set(), None, {}))
    ask.lookup("what color is the car?", "alice", tmp_path)

    recs = read_jsonl(tmp_path / "traces.jsonl")
    traces = [r for r in recs if r.get("kind") == "trace"]
    assert len(traces) == 1
    assert traces[0]["tier"] == "confirmed"
    assert traces[0]["final_ranked"][0]["path"] == str(note)
    assert traces[0]["content_check"][str(note)]["label"] == "confirmed"


def test_trace_redacts_secret_shaped_fields_and_truncates_long_ones(tmp_path):
    ask.write_trace(tmp_path, kind="trace", lookup_id="abc123", question="what is my api-key?",
                    errors=["password: hunter2 leaked here"], notes="x" * 900)

    rec = read_jsonl(tmp_path / "traces.jsonl")[0]
    assert rec["errors"] == ["[redacted]"]
    assert rec["notes"].endswith("...[truncated]")
    assert len(rec["notes"]) < 900


def test_trace_rotation_caps_file_and_keeps_one_old_generation(tmp_path):
    path = tmp_path / "traces.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * (ask.TRACE_CAP_BYTES + 1))

    ask.write_trace(tmp_path, kind="trace", lookup_id="new1", question="q")

    old = tmp_path / "traces.jsonl.1"
    assert old.is_file()
    assert old.stat().st_size == ask.TRACE_CAP_BYTES + 1
    recs = read_jsonl(path)
    assert len(recs) == 1
    assert recs[0]["lookup_id"] == "new1"


def test_rotation_overwrites_a_prior_old_generation_keeping_only_one(tmp_path):
    path = tmp_path / "traces.jsonl"
    old = tmp_path / "traces.jsonl.1"
    old.write_text('{"kind": "ancient"}\n')
    path.write_bytes(b"x" * (ask.TRACE_CAP_BYTES + 1))

    ask.write_trace(tmp_path, kind="trace", lookup_id="new2", question="q")

    assert "ancient" not in old.read_text()
    assert path.stat().st_size < ask.TRACE_CAP_BYTES


# ---------------------------------------------------------------- outcomes

def test_approve_marks_the_last_lookup_right_with_evidence_file(tmp_path, monkeypatch):
    ask.write_trace(tmp_path, kind="trace", lookup_id="lid1", question="q",
                    final_ranked=[{"score": 0.9, "path": "/a.md", "pointer": "p1"}], tier="confirmed")
    ask.log(tmp_path, "lookup", question="q", top=[{"score": 0.9, "path": "/a.md", "pointer": "p1", "possible": False}])

    def fake_memory(req):
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.approve("alice", "q", "answer", tmp_path)

    assert rc == 0
    outcomes = [r for r in read_jsonl(tmp_path / "traces.jsonl") if r.get("kind") == "outcome"]
    assert len(outcomes) == 1
    assert outcomes[0]["lookup_id"] == "lid1"
    assert outcomes[0]["result"] == "right"
    assert outcomes[0]["file"] == "/a.md"


def test_miss_marks_the_last_lookup_wrong_with_the_actual_path(tmp_path, monkeypatch):
    ask.write_trace(tmp_path, kind="trace", lookup_id="lid2", question="q2", final_ranked=[], tier="none")
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "not-cached"})

    rc = ask.miss("alice", "q2", "/real/answer.md", tmp_path)

    assert rc == 0
    outcomes = [r for r in read_jsonl(tmp_path / "traces.jsonl") if r.get("kind") == "outcome"]
    assert len(outcomes) == 1
    assert outcomes[0]["lookup_id"] == "lid2"
    assert outcomes[0]["result"] == "wrong"
    assert outcomes[0]["file"] == "/real/answer.md"


def test_add_after_a_miss_marks_the_lookup_wrong_added(tmp_path, monkeypatch):
    ask.write_trace(tmp_path, kind="trace", lookup_id="lid3", question="q3", final_ranked=[], tier="none")
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "not-cached"})
    ask.miss("alice", "q3", "/real/answer.md", tmp_path)

    def fake_memory(req):
        if req["action"] == "panel":
            return {"pointers": []}
        if req["action"] == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            return {"status": "registered",
                     "sources": [{"id": "file:abc", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", "q3", "answer text", None, tmp_path)

    assert rc == 0
    outcomes = [r for r in read_jsonl(tmp_path / "traces.jsonl") if r.get("kind") == "outcome"]
    assert outcomes[-1]["lookup_id"] == "lid3"
    assert outcomes[-1]["result"] == "wrong-added"


def test_approve_with_no_prior_lookup_writes_no_outcome(tmp_path, monkeypatch):
    ask.log(tmp_path, "lookup", question="q4", top=[{"score": 0.9, "path": "/a.md", "pointer": "p1", "possible": False}])

    def fake_memory(req):
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.approve("alice", "q4", "answer", tmp_path)

    assert rc == 0
    assert not (tmp_path / "traces.jsonl").is_file()


# ------------------------------------------------------------------- report

def test_trace_report_counts_right_wrong_unlabeled_and_lists_top_wrong(tmp_path, capsys):
    ask.write_trace(tmp_path, kind="trace", lookup_id="r1", question="right one",
                    final_ranked=[{"score": 0.9, "path": "/a.md", "pointer": "p"}], tier="confirmed")
    ask.write_outcome(tmp_path, "r1", "right one", "right", file="/a.md")

    ask.write_trace(tmp_path, kind="trace", lookup_id="w1", question="wrong one",
                    final_ranked=[{"score": 0.3, "path": "/b.md", "pointer": "p"}], tier="possible")
    ask.write_outcome(tmp_path, "w1", "wrong one", "wrong", file="/real.md")

    ask.write_trace(tmp_path, kind="trace", lookup_id="w2", question="wrong one",
                    final_ranked=[{"score": 0.2, "path": "/c.md", "pointer": "p"}], tier="none")
    ask.write_outcome(tmp_path, "w2", "wrong one", "wrong-added", file="/real.md")

    ask.write_trace(tmp_path, kind="trace", lookup_id="u1", question="unlabeled one",
                    final_ranked=[], tier="none")

    rc = ask.trace_report(tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert "right: 1  wrong: 2  unlabeled: 1" in out
    assert "(2) wrong one" in out
    assert "/b.md" in out  # a still-wrong trace's ranked list for that question


def test_trace_report_days_filter_excludes_old_entries(tmp_path, capsys):
    old_ts = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - 30 * 86400))
    (tmp_path / "traces.jsonl").write_text(json.dumps(
        {"ts": old_ts, "kind": "trace", "lookup_id": "old1", "question": "ancient", "final_ranked": [], "tier": "none"}
    ) + "\n")
    ask.write_trace(tmp_path, kind="trace", lookup_id="new1", question="recent", final_ranked=[], tier="none")

    rc = ask.trace_report(tmp_path, days=7)

    assert rc == 0
    out = capsys.readouterr().out
    assert "right: 0  wrong: 0  unlabeled: 1" in out


def test_trace_report_reads_the_rotated_old_generation_too(tmp_path, capsys):
    (tmp_path / "traces.jsonl.1").write_text(json.dumps(
        {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": "trace", "lookup_id": "prev1",
         "question": "from before rotation", "final_ranked": [], "tier": "none"}
    ) + "\n")
    ask.write_trace(tmp_path, kind="trace", lookup_id="new2", question="after rotation",
                    final_ranked=[], tier="none")

    rc = ask.trace_report(tmp_path)

    assert rc == 0
    assert "unlabeled: 2" in capsys.readouterr().out
