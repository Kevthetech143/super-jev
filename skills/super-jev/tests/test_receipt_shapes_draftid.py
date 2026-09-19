#!/usr/bin/env python3
"""Offline tests for family 11's draft-id send lookback.

A Gmail `send_message` called with only `draftId` genuinely sends an
existing draft but names no recipient and no thread in its own input, so
the message-tool branch of `_facts_receipt_shapes` was emitting no "sent"
line at all for it. These tests build synthetic transcripts with real MCP
schema shapes (recipient arrays, camelCase `draftId`) and check the family
looks back at the draft call that built the id.

Run:
    python3 -m pytest skills/super-jev/tests/test_receipt_shapes_draftid.py -q
"""
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("superjev", SKILL / "superjev.py")
sj = importlib.util.module_from_spec(spec)
sys.modules["superjev"] = sj
spec.loader.exec_module(sj)


def _user(text="do the thing"):
    return {"message": {"role": "user",
                        "content": [{"type": "text", "text": text}]}}


def _use(tid, name, inp, cwd="/work"):
    return {"cwd": cwd, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _result(tid, text="ok", is_error=False):
    block = {"type": "tool_result", "tool_use_id": tid,
             "content": [{"type": "text", "text": text}]}
    if is_error:
        block["is_error"] = True
    return {"message": {"role": "user", "content": [block]}}


def _facts(records, prev_turns=2):
    start = sj._current_turn_start_index(records)
    return sj._facts_receipt_shapes(records, start, prev_turns)


def _sent_facts(records):
    return [f for f in _facts(records)
            if f.startswith("RECEIPT SHAPE (sent):")]


CREATE = "mcp__claude_ai_Gmail__create_draft"
UPDATE = "mcp__claude_ai_Gmail__update_draft"
SEND = "mcp__claude_ai_Gmail__send_message"

DRAFT_RESULT = '{"id": "r7abc123", "message": {"id": "m9"}}'
SEND_RESULT = '{"id": "m9", "labelIds": ["SENT"]}'


def test_send_by_draft_id_names_the_create_draft_recipients():
    # Real schema: `to` is an array of address strings, and the
    # `create_draft` result carries the minted draft id while the send
    # names only that id.
    records = [_user(),
               _use("d1", CREATE,
                    {"to": ["kevin@example.com"], "subject": "hi",
                     "body": "draft body"}),
               _result("d1", DRAFT_RESULT),
               _use("s1", SEND, {"draftId": "r7abc123"}),
               _result("s1", SEND_RESULT)]
    sent = _sent_facts(records)
    assert len(sent) == 1
    assert f"a {SEND} send naming kevin@example.com" in sent[0]
    # The draft call itself is never a send.
    assert CREATE not in sent[0]


def test_send_by_draft_id_with_no_draft_call_names_the_draft():
    # No create/update in the window: the judge still sees that a send
    # happened, but no recipient is credited.
    records = [_user(),
               _use("s1", SEND, {"draftId": "r7abc123"}),
               _result("s1", SEND_RESULT)]
    sent = _sent_facts(records)
    assert len(sent) == 1
    assert (f"a {SEND} send of draft r7abc123 (recipient not in window)"
            in sent[0])


def test_create_draft_alone_still_names_no_sent_line():
    # The blocklist is unchanged: building a draft sends nothing, even
    # though the same draft call feeds the lookback once a send follows.
    records = [_user(),
               _use("d1", CREATE,
                    {"to": ["kevin@example.com"], "subject": "hi",
                     "body": "draft body"}),
               _result("d1", DRAFT_RESULT)]
    assert _sent_facts(records) == []


def test_update_draft_recipients_win_over_the_create_draft():
    # The lookback takes the latest draft builder first: an `update_draft`
    # that changed the recipients (matched on the id in its own input)
    # wins over the earlier `create_draft` (matched on its result).
    records = [_user(),
               _use("d1", CREATE,
                    {"to": ["old@example.com"], "subject": "hi",
                     "body": "draft body"}),
               _result("d1", DRAFT_RESULT),
               _use("d2", UPDATE,
                    {"draftId": "r7abc123", "to": ["new@example.com"]}),
               _result("d2", "draft updated"),
               _use("s1", SEND, {"draftId": "r7abc123"}),
               _result("s1", SEND_RESULT)]
    sent = _sent_facts(records)
    assert len(sent) == 1
    assert f"a {SEND} send naming new@example.com" in sent[0]
    assert "old@example.com" not in sent[0]
