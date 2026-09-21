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
import time
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


def test_hit_from_healthy_pointer_prints_before_a_sibling_pointer_error(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1", "p2"]}
        if req["action"] == "navigate" and req["pointer"] == "p1":
            return {"status": "refresh-required"}
        if req["action"] == "navigate" and req["pointer"] == "p2":
            return {"status": "candidates", "candidates": [{"score": 0.8, "originalPath": "/hit.md"}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("q", "alice", tmp_path)

    assert rc == 1
    out = capsys.readouterr().out
    assert "/hit.md" in out
    assert "[p2]" in out
    assert "[p1] refresh-required" in out
    assert "unresolved: 1 of 2 pointers errored" in out
    # the hit must appear before both the error line and the unresolved summary
    hit_pos = out.index("/hit.md")
    error_pos = out.index("[p1] refresh-required")
    unresolved_pos = out.index("unresolved:")
    assert hit_pos < error_pos < unresolved_pos


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
            return {"status": "registered",
                     "sources": [{"id": "file:abc123", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    question = "which pointers hold the example agent brain"
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
    assist_calls = [c for c in calls if c["action"] == "assist"]
    assert assist_calls == []


def test_add_falls_back_to_assist_on_no_match_then_approves_with_returned_ticket(tmp_path, monkeypatch):
    src_file = tmp_path / "src.txt"
    src_file.write_text("hello source\n")
    question = "which pointers hold the shared brain now"
    expected_pointer = f"alice-manual-{hashlib.sha1(question.encode()).hexdigest()[:10]}"
    calls = []

    def fake_memory(req):
        calls.append(req)
        if req["action"] == "panel":
            return {"pointers": []}
        if req["action"] == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            return {"status": "registered",
                     "sources": [{"id": "file:src-id", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "no-match", "attemptId": "attempt-1"}
        if req["action"] == "assist":
            return {"status": "ready", "approvalTicket": "assist-tix",
                     "passages": [{"sourceId": "file:src-id", "reviewedText": "The manual answer text."}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", question, "The manual answer text.", str(src_file), tmp_path)

    assert rc == 0
    record = tmp_path / "manual" / f"{expected_pointer}.md"
    expected_last_line = len(record.read_text().splitlines())
    assist_calls = [c for c in calls if c["action"] == "assist"]
    assert len(assist_calls) == 1
    assert assist_calls[0]["attemptId"] == "attempt-1"
    assert assist_calls[0]["principal"] == "alice"
    assert assist_calls[0]["references"] == [{"sourceId": "file:src-id", "startLine": 1, "endLine": expected_last_line}]
    approve_calls = [c for c in calls if c["action"] == "approve"]
    assert len(approve_calls) == 1
    assert approve_calls[0]["ticket"] == "assist-tix"
    assert approve_calls[0]["evidence"] == [{"sourceId": "file:src-id", "quote": "The manual answer text."}]


def test_add_prints_config_hint_and_exits_1_when_assist_disabled(tmp_path, monkeypatch, capsys):
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
            return {"status": "registered",
                     "sources": [{"id": "file:src-id", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "no-match", "attemptId": "attempt-1"}
        if req["action"] == "assist":
            return {"status": "error", "reason": "agent assist is disabled"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    question = "which pointers hold the shared brain when disabled"
    rc = ask.add_manual("alice", question, "The manual answer text.", str(src_file), tmp_path)

    assert rc == 1
    out = capsys.readouterr().out
    assert "allowAgentAssist" in out
    approve_calls = [c for c in calls if c["action"] == "approve"]
    assert approve_calls == []


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


def test_add_manual_default_labels_in_header_and_connect_description(tmp_path, monkeypatch):
    """No --subject/--kind/--status/--as-of given: the record header and the connect
    description both carry the defaults (record/active/today/derived subject)."""
    calls = []

    def fake_memory(req):
        calls.append(req)
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
    question = "what is pending for the widget project rollout"
    rc = ask.add_manual("alice", question, "The answer body.", None, tmp_path)

    assert rc == 0
    pointer = ask.manual_pointer_name("alice", question)
    record = tmp_path / "manual" / f"{pointer}.md"
    text = record.read_text()
    assert "kind: record" in text
    assert "status: active" in text
    assert f"as_of: {time.strftime('%Y-%m-%d')}" in text
    assert "subject: " in text
    assert "project: alice" in text

    connects = [c for c in calls if c["action"] == "connect"]
    desc = connects[0]["sources"][0]["description"]
    assert "[kind: record; status: active; as_of:" in desc
    assert "subject:" in desc


def test_add_manual_explicit_labels_override_defaults(tmp_path, monkeypatch):
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
    question = "where is the typesafe api key kept"
    rc = ask.add_manual("alice", question, "answer", None, tmp_path,
                         kind="pointer", status="done", as_of="2026-01-15", subject="typesafe api key")

    assert rc == 0
    pointer = ask.manual_pointer_name("alice", question)
    text = (tmp_path / "manual" / f"{pointer}.md").read_text()
    assert "kind: pointer" in text
    assert "status: done" in text
    assert "as_of: 2026-01-15" in text
    assert "subject: typesafe api key" in text


def test_do_add_invalid_kind_is_usage_error_exit_2(tmp_path, monkeypatch):
    monkeypatch.setattr(ask, "memory", lambda req: (_ for _ in ()).throw(AssertionError("must not call memory")))
    rc = ask.do_add("alice", ["q", "answer", "--kind", "not-a-kind"], tmp_path)
    assert rc == 2


def test_do_add_invalid_status_is_usage_error_exit_2(tmp_path, monkeypatch):
    monkeypatch.setattr(ask, "memory", lambda req: (_ for _ in ()).throw(AssertionError("must not call memory")))
    rc = ask.do_add("alice", ["q", "answer", "--status", "bogus"], tmp_path)
    assert rc == 2


def test_do_add_invalid_as_of_is_usage_error_exit_2(tmp_path, monkeypatch):
    monkeypatch.setattr(ask, "memory", lambda req: (_ for _ in ()).throw(AssertionError("must not call memory")))
    rc = ask.do_add("alice", ["q", "answer", "--as-of", "not-a-date"], tmp_path)
    assert rc == 2


def test_do_add_parses_flags_in_any_order_and_builds_answer_from_remaining_words(tmp_path, monkeypatch):
    captured = {}

    def fake_add_manual(principal, question, answer, source, sdir, **kw):
        captured.update(kw); captured["principal"] = principal; captured["question"] = question
        captured["answer"] = answer; captured["source"] = source
        return 0

    monkeypatch.setattr(ask, "add_manual", fake_add_manual)
    rc = ask.do_add("alice", ["the question", "the", "--subject", "widgets", "answer", "--kind", "note",
                               "words", "--status", "closed"], tmp_path)

    assert rc == 0
    assert captured["question"] == "the question"
    assert captured["answer"] == "the answer words"
    assert captured["subject"] == "widgets"
    assert captured["kind"] == "note"
    assert captured["status"] == "closed"


def test_replace_entry_removes_old_pointer_and_record_then_adds_fresh(tmp_path, monkeypatch):
    question = "duplicate wording to replace"
    pointer = ask.manual_pointer_name("alice", question)
    manual_dir = tmp_path / "manual"
    manual_dir.mkdir(parents=True)
    old_record = manual_dir / f"{pointer}.md"
    old_record.write_text("# old\n\nstale body\n")
    calls = []

    def fake_memory(req):
        calls.append(req)
        if req["action"] == "panel":
            return {"pointers": [pointer]}
        if req["action"] == "remove":
            return {"status": "removed"}
        if req["action"] == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            return {"status": "registered",
                     "sources": [{"id": "file:abc", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "fresh text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", question, "fresh answer", None, tmp_path, replace=True)

    assert rc == 0
    remove_calls = [c for c in calls if c["action"] == "remove"]
    assert len(remove_calls) == 1
    assert remove_calls[0]["pointer"] == pointer
    new_text = old_record.read_text()
    assert "stale body" not in new_text
    assert "fresh answer" in new_text
    # `remove` only drops principal access; the harness keeps the dataset registered
    # under that pointer name, so the reconnect after an explicit remove still needs
    # replace:true, exactly like a stale-source reconnect elsewhere in this codebase.
    connect_calls = [c for c in calls if c["action"] == "connect"]
    assert connect_calls and all(c.get("replace") is True for c in connect_calls)


def test_replace_entry_sets_replace_true_even_when_panel_no_longer_lists_the_pointer(tmp_path, monkeypatch):
    """Live-discovered case: a pointer removed in an earlier run no longer shows up in
    `panel`, but the harness's dataset registry still has it under that pointer name --
    a connect without replace:true still fails as already-connected. --replace-entry
    must send replace:true regardless of what panel currently reports."""
    question = "duplicate wording, already removed from panel"
    pointer = ask.manual_pointer_name("alice", question)
    calls = []

    def fake_memory(req):
        calls.append(req)
        if req["action"] == "panel":
            return {"pointers": []}  # panel no longer lists it
        if req["action"] == "connect" and not req.get("reviewed"):
            if not req.get("replace"):
                return {"status": "preparation-required", "reason": "already-connected"}
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            if not req.get("replace"):
                return {"status": "preparation-required", "reason": "already-connected"}
            return {"status": "registered",
                     "sources": [{"id": "file:abc", "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix",
                     "passages": [{"sourceId": "s", "reviewedText": "fresh text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        if req["action"] == "remove":
            raise AssertionError("panel never listed it; remove should not be called")
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", question, "fresh answer", None, tmp_path, replace=True)

    assert rc == 0
    remove_calls = [c for c in calls if c["action"] == "remove"]
    assert remove_calls == []


def test_add_without_replace_flag_still_refuses_when_pointer_exists(tmp_path, monkeypatch, capsys):
    question = "duplicate wording no replace"
    pointer = ask.manual_pointer_name("alice", question)

    def fake_memory(req):
        if req["action"] == "panel":
            return {"pointers": [pointer]}
        raise AssertionError("must refuse before ever calling connect or remove")

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.add_manual("alice", question, "answer", None, tmp_path)

    assert rc == 1
    out = capsys.readouterr().out
    assert "refused" in out
    assert "--replace-entry" in out


def test_second_manual_entry_leaves_first_pointer_untouched(tmp_path, monkeypatch):
    """Adding a second manual entry must never replace or remove another pointer."""
    registered_pointers = []
    calls = []

    def fake_memory(req):
        calls.append(json.loads(json.dumps(req)))  # req is mutated in place by add_manual; snapshot it
        if req["action"] == "panel":
            return {"pointers": list(registered_pointers)}
        if req["action"] == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required",
                     "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if req["action"] == "connect" and req.get("reviewed"):
            assert req.get("replace") is not True
            registered_pointers.append(req["pointer"])
            return {"status": "registered",
                     "sources": [{"id": "file:" + req["pointer"], "originalPath": req["sources"][0]["path"]}]}
        if req["action"] == "search":
            return {"status": "ready", "approvalTicket": "tix-" + req["pointer"],
                     "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if req["action"] == "approve":
            return {"status": "saved"}
        if req["action"] == "remove":
            raise AssertionError("must never remove another pointer while adding a new one")
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc1 = ask.add_manual("alice", "first manual question", "answer one", None, tmp_path)
    rc2 = ask.add_manual("alice", "second manual question", "answer two", None, tmp_path)

    assert rc1 == 0
    assert rc2 == 0
    assert len(registered_pointers) == 2
    assert registered_pointers[0] != registered_pointers[1]
    remove_calls = [c for c in calls if c["action"] == "remove"]
    assert remove_calls == []
    connects = [c for c in calls if c["action"] == "connect" and c.get("reviewed")]
    assert connects[0]["pointer"] == registered_pointers[0]
    assert connects[1]["pointer"] == registered_pointers[1]
    # The second add's connect call never references the first pointer's name.
    assert registered_pointers[0] not in json.dumps(connects[1])


def test_cache_hit_on_manual_pointer_with_changed_source_is_invalidated_not_served(tmp_path, monkeypatch, capsys):
    # Staleness is keyed off the manual record this principal+question would have
    # written (ask.manual_record_path), not off evidence.path -- the harness only
    # ever returns its own internal prepared-copy path there, never the caller's file.
    question = "q"
    record = ask.manual_record_path(tmp_path, "alice", question)
    record.parent.mkdir(parents=True)
    src = tmp_path / "orig.txt"
    src.write_text("version 1\n")
    recorded_hash = hashlib.sha256(src.read_bytes()).hexdigest()
    record.write_text(f"# {question}\n\nsource_path: {src}\nsource_sha256: {recorded_hash}\n\nThe answer.\n")
    src.write_text("version 2, changed since recording\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "verified-cache-hit", "answer": "The answer.",
                     "evidence": [{"sourceId": "s", "quote": "The answer.",
                                  "path": "/registry/.prepared-xyz/0.txt", "contentSHA": "x"}],
                     "freshness": {"mode": "snapshot"}, "resolution": "retrieval"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup(question, "alice", tmp_path)

    assert rc == 1
    out = capsys.readouterr().out
    assert "STALE: source changed since this answer was recorded" in out
    assert str(src) in out
    assert "CACHE HIT" not in out
    assert "The answer." not in out


def test_cache_hit_on_manual_pointer_with_unchanged_source_serves_the_answer(tmp_path, monkeypatch, capsys):
    question = "q"
    record = ask.manual_record_path(tmp_path, "alice", question)
    record.parent.mkdir(parents=True)
    src = tmp_path / "orig.txt"
    src.write_text("version 1\n")
    recorded_hash = hashlib.sha256(src.read_bytes()).hexdigest()
    record.write_text(f"# {question}\n\nsource_path: {src}\nsource_sha256: {recorded_hash}\n\nThe answer.\n")

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "verified-cache-hit", "answer": "The answer.",
                     "evidence": [{"sourceId": "s", "quote": "The answer.",
                                  "path": "/registry/.prepared-xyz/0.txt", "contentSHA": "x"}],
                     "freshness": {"mode": "snapshot"}, "resolution": "retrieval"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup(question, "alice", tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert "STALE" not in out
    assert "CACHE HIT" in out
    assert "The answer." in out


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
