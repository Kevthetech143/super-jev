#!/usr/bin/env python3
"""Frozen check: an answer whose cited file lives in a split part (<base>-N) is saved; a changed file still refuses.

Made-up user and notes; `ask.memory`, `ask.run_gate` are fakes (no network, no live state).

    python3 -m pytest skills/super-jev/tests/test_save_split_pointers.py -q
"""
import hashlib
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "ask.py"
spec = importlib.util.spec_from_file_location("ask_split_save", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

Q = "what is the acme refund window"
A = "Acme refund window: 30 days from delivery."


@pytest.fixture
def world(tmp_path, monkeypatch):
    note = tmp_path / "acme-refunds.md"
    note.write_text(A + "\n")
    sha = lambda: hashlib.sha256(note.read_bytes()).hexdigest()
    st = {"sha": sha(), "cache": {}}
    owner = "acme-notes-3"  # the file lives in part 3; the lookup names the base

    def memory(req):
        act = req["action"]
        if act == "panel":
            return {"pointers": [{"pointer": p} for p in ("acme-notes", "acme-notes-2", "acme-notes-3")]}
        if act == "sources":
            if req["pointer"] != owner:
                return {"status": "ok", "sources": []}
            return {"status": "ok", "sources": [{"sourceId": "s3", "originalPath": str(note), "contentSHA": st["sha"]}]}
        if act == "open":
            assert req["pointer"] == owner
            return {"status": "ok", "attemptId": "att|" + req["question"]}
        if act == "assist":
            return {"status": "ready", "approvalTicket": "tk|" + req["attemptId"][4:],
                    "passages": [{"sourceId": "s3", "reviewedText": A}]}
        if act == "approve":
            st["cache"][req["ticket"][3:]] = req["answer"]
            return {"status": "saved"}
        if act == "cached":
            return {"status": "cache-miss", "checked": []}
        raise AssertionError(f"unexpected action {act}")

    monkeypatch.setattr(ask, "memory", memory)
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.93))
    monkeypatch.delenv("SUPERJEV_AUTO_CACHE", raising=False)
    return {"note": note, "st": st, "sdir": tmp_path / "state" / "zed", "top": {"path": str(note), "pointer": "acme-notes"}}


def test_file_in_part_three_is_saved_under_the_base_name(world):
    rc = ask.save_answer("zed", Q, A, world["sdir"], top=world["top"])
    assert rc == 0 and world["st"]["cache"]


def test_changed_file_in_a_part_still_refuses(world):
    world["note"].write_text("Acme refund window: 14 days from delivery.\n")  # sha no longer matches the connected one
    rc = ask.save_answer("zed", Q, A, world["sdir"], top=world["top"])
    assert rc != 0 and not world["st"]["cache"]
