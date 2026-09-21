#!/usr/bin/env python3
"""Offline tests for ask.py, the cache-first front door over the memory harness.

No network, no live harness call, no live provider: `ask.memory` is monkeypatched
at the module level for every test, and the state directory is always an isolated
tmp_path (either passed directly to the lower-level functions or via a
monkeypatched `ask.state_dir`). `navigate` is asserted never called on a cache
hit; the harness `cached` action is asserted never confused with local bookkeeping.

    python3 -m pytest skills/super-jev/tests/test_ask.py -q
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "ask.py"

spec = importlib.util.spec_from_file_location("ask", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def test_cache_hit_prints_answer_and_never_navigates(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_memory(req):
        calls.append(req["action"])
        if req["action"] == "cached":
            return {"status": "verified-cache-hit", "answer": "Blue.",
                     "evidence": [{"sourceId": "one", "quote": "blue", "path": "/x/source.txt", "contentSHA": "s"}],
                     "freshness": {"mode": "snapshot"}, "resolution": "retrieval"}
        raise AssertionError(f"unexpected action: {req['action']}")

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("what color?", "alice", tmp_path)

    assert rc == 0
    assert "navigate" not in calls
    assert "panel" not in calls
    out = capsys.readouterr().out
    assert "CACHE HIT" in out
    assert "Blue." in out
    assert "one" in out


def test_miss_fans_out_over_panel_pointers_merged_by_score_and_logs(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}, {"pointer": "p2"}]}
        if req["action"] == "navigate" and req["pointer"] == "p1":
            return {"status": "candidates", "candidates": [{"score": 0.4, "originalPath": "/low.md"}]}
        if req["action"] == "navigate" and req["pointer"] == "p2":
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": "/high.md"}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("where is it?", "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip() and l.strip()[0].isdigit()]
    assert lines[0].strip().startswith("0.90") and "/high.md" in lines[0] and "[p2]" in lines[0]
    log_path = tmp_path / "lookups.jsonl"
    assert log_path.is_file()
    rec = json.loads(log_path.read_text().splitlines()[-1])
    assert rec["kind"] == "lookup"
    assert rec["pointers"] == 2
    assert rec["statuses"] == {"p1": "candidates", "p2": "candidates"}


def test_pointer_error_prints_status_line_and_is_not_folded_into_no_candidates(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1", "p2"]}
        if req["action"] == "navigate" and req["pointer"] == "p1":
            return {"status": "refresh-required"}
        if req["action"] == "navigate" and req["pointer"] == "p2":
            return {"status": "no-candidates", "candidates": []}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("q", "alice", tmp_path)

    assert rc == 1
    out = capsys.readouterr().out
    assert "[p1] refresh-required" in out
    assert "unresolved: 1 of 2 pointers errored" in out
    assert "no-candidates across" not in out


def test_all_pointers_empty_gives_no_candidates_hint_naming_connectors_and_add(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1"]}
        if req["action"] == "navigate":
            return {"status": "no-candidates", "candidates": []}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("q", "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert "no-candidates across 1 pointers" in out
    assert "references/connectors.md" in out
    assert "--add" in out


def test_approve_ready_search_sends_ticket_approved_answer_and_reviewedtext_quotes(tmp_path, monkeypatch):
    ask.log(tmp_path, "lookup", question="q", top=[{"score": 0.9, "path": "/a.md", "pointer": "p1"}])
    calls = []

    def fake_memory(req):
        calls.append(req)
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix-1",
                     "passages": [{"sourceId": "s1", "reviewedText": "The answer text."}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.approve("alice", "q", "The answer.", tmp_path)

    assert rc == 0
    approve_req = next(c for c in calls if c["action"] == "approve")
    assert approve_req["ticket"] == "tix-1"
    assert approve_req["approved"] is True
    assert approve_req["answer"] == "The answer."
    assert approve_req["evidence"] == [{"sourceId": "s1", "quote": "The answer text."}]


def test_approve_on_no_match_hints_add_and_exits_1(tmp_path, monkeypatch, capsys):
    ask.log(tmp_path, "lookup", question="q", top=[{"score": 0.9, "path": "/a.md", "pointer": "p1"}])

    def fake_memory(req):
        if req["action"] == "search":
            return {"status": "no-match"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.approve("alice", "q", "answer", tmp_path)

    assert rc == 1
    out = capsys.readouterr().out
    assert "--add" in out


def test_add_writes_manual_record_connects_new_pointer_never_replace_then_approves(tmp_path, monkeypatch):
    src_file = tmp_path / "src.txt"
    src_file.write_text("hello source\n")
    calls = []

    def fake_memory(req):
        calls.append(req)
        if req["action"] == "panel":
            return {"pointers": []}
        if req["action"] == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            assert req.get("replace") is not True
            return {"status": "registered"}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    question = "which pointers hold the businessfi brain"
    rc = ask.add_manual("alice", question, "The answer body.", str(src_file), tmp_path)

    assert rc == 0
    expected_pointer = f"alice-manual-{hashlib.sha1(question.encode()).hexdigest()[:10]}"
    record = tmp_path / "manual" / f"{expected_pointer}.md"
    assert record.is_file()
    text = record.read_text()
    assert f"source_path: {src_file.resolve()}" in text
    assert "source_sha256:" in text
    connects = [c for c in calls if c["action"] == "connect"]
    assert len(connects) == 2
    assert connects[0]["pointer"] == expected_pointer
    assert connects[1]["reviewed"] is True
    assert connects[1]["sources"][0]["sha256"] == "deadbeef"


def test_add_refuses_when_pointer_already_exists_for_question(tmp_path, monkeypatch, capsys):
    question = "duplicate wording"
    expected_pointer = f"alice-manual-{hashlib.sha1(question.encode()).hexdigest()[:10]}"

    def fake_memory(req):
        if req["action"] == "panel":
            return {"pointers": [expected_pointer]}
        raise AssertionError("must refuse before ever calling connect")

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", question, "answer", None, tmp_path)

    assert rc == 1
    out = capsys.readouterr().out
    assert "refused" in out
    assert expected_pointer in out


def test_cache_hit_on_manual_pointer_warns_when_source_file_changed(tmp_path, monkeypatch, capsys):
    manual_dir = tmp_path / "manual"
    manual_dir.mkdir()
    src = tmp_path / "orig.txt"
    src.write_text("version 1\n")
    recorded_hash = hashlib.sha256(src.read_bytes()).hexdigest()
    record = manual_dir / "alice-manual-abc123.md"
    record.write_text(f"# q\n\nsource_path: {src}\nsource_sha256: {recorded_hash}\n\nThe answer.\n")
    src.write_text("version 2, changed since recording\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "verified-cache-hit", "answer": "The answer.",
                     "evidence": [{"sourceId": "s", "quote": "The answer.", "path": str(record), "contentSHA": "x"}],
                     "freshness": {"mode": "snapshot"}, "resolution": "retrieval"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("q", "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert "WARNING: source changed since this answer was recorded" in out
    assert str(src) in out


def test_cache_hit_on_manual_pointer_with_unchanged_source_has_no_warning(tmp_path, monkeypatch, capsys):
    manual_dir = tmp_path / "manual"
    manual_dir.mkdir()
    src = tmp_path / "orig.txt"
    src.write_text("version 1\n")
    recorded_hash = hashlib.sha256(src.read_bytes()).hexdigest()
    record = manual_dir / "alice-manual-abc123.md"
    record.write_text(f"# q\n\nsource_path: {src}\nsource_sha256: {recorded_hash}\n\nThe answer.\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "verified-cache-hit", "answer": "The answer.",
                     "evidence": [{"sourceId": "s", "quote": "The answer.", "path": str(record), "contentSHA": "x"}],
                     "freshness": {"mode": "snapshot"}, "resolution": "retrieval"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("q", "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert "WARNING" not in out


def test_missing_principal_prints_usage_and_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("SUPERJEV_PRINCIPAL", raising=False)
    monkeypatch.setattr(sys, "argv", ["ask.py", "some question"])

    rc = ask.main()

    assert rc == 2
    out = capsys.readouterr().out
    assert "ask.py" in out


def test_missing_question_prints_usage_and_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "alice"])

    rc = ask.main()

    assert rc == 2


def test_main_routes_miss_through_state_dir_and_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "state_dir", lambda principal: tmp_path)
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "alice", "--miss", "q", "it was in the wiki"])

    rc = ask.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "miss recorded" in out
    rec = json.loads((tmp_path / "lookups.jsonl").read_text().splitlines()[-1])
    assert rec["kind"] == "miss"
    assert rec["question"] == "q"
    assert rec["actual"] == "it was in the wiki"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
