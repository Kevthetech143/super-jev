#!/usr/bin/env python3
"""Frozen tests for the saved-answer promise (docs/GETTING-STARTED.md, step 7), one per promise item.

`ask.memory` is a fake harness and `ask.run_gate` a stub: no network, no live state.
Every test drives the public CLI entry (`ask._main`) or `ask.lookup`.

    python3 -m pytest skills/super-jev/tests/test_saved_answers.py -q
"""
import hashlib
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "ask.py"
spec = importlib.util.spec_from_file_location("ask_saved", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

Q, A = "What color is the car?", "The car is blue."


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A prior lookup whose top file is fresh, a fake harness that stores saved answers
    under the exact question string it is sent, and a CLEAN gate."""
    note = tmp_path / "car.md"
    note.write_text("The car is blue.\n")
    sdir = tmp_path / "state"
    sha = hashlib.sha256(note.read_bytes()).hexdigest()
    cache, calls, pointers = {}, [], []

    def fake_memory(req):
        calls.append(req)
        act = req["action"]
        if act == "panel":
            return {"pointers": [{"pointer": p} for p in pointers]}
        if act == "sources":
            return {"status": "ok", "sources": [{"sourceId": "one", "originalPath": str(note), "contentSHA": sha}]}
        if act == "open":
            return {"status": "ok", "attemptId": "att|" + req["question"]}
        if act == "assist":
            return {"status": "ready", "approvalTicket": "tk|" + req["attemptId"][4:],
                    "passages": [{"sourceId": "one", "reviewedText": "The car is blue."}]}
        if act == "search":
            return {"status": "ready", "approvalTicket": "tk|" + req["question"],
                    "passages": [{"sourceId": "s", "reviewedText": "answer text"}]}
        if act == "connect" and not req.get("reviewed"):
            return {"status": "preparation-required",
                    "sources": [{"path": req["sources"][0]["path"], "sha256": "deadbeef"}]}
        if act == "connect":
            pointers.append(req["pointer"])
            return {"status": "registered",
                    "sources": [{"id": "file:abc", "originalPath": req["sources"][0]["path"]}]}
        if act == "approve":
            cache[req["ticket"][3:]] = {"answer": req["answer"], "evidence": req["evidence"]}
            return {"status": "saved"}
        if act == "cached":
            hit = cache.get(req["question"])
            return ({"status": "verified-cache-hit", "answer": hit["answer"], "evidence": hit["evidence"]}
                    if hit else {"status": "cache-miss"})
        if act == "forget":
            return ({"status": "forgotten", "pointers": ["p1"]}
                    if cache.pop(req["question"], None) else {"status": "not-cached"})
        raise AssertionError(f"unexpected action {act}")

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.93))
    monkeypatch.setattr(ask, "state_dir", lambda principal: sdir)
    monkeypatch.setattr(ask, "confirm", lambda question, paths: ({p: .9 for p in paths}, set(), None, {}))
    monkeypatch.delenv("SUPERJEV_AUTO_CACHE", raising=False)
    ask.log(sdir, "lookup", question=Q, top=[{"score": 0.95, "path": str(note), "pointer": "p1"}])
    ask.write_trace(sdir, kind="trace", lookup_id="L1", question=Q,
                    final_ranked=[{"path": str(note), "pointer": "p1"}])
    return {"note": note, "sdir": sdir, "cache": cache, "calls": calls, "pointers": pointers, "sha": sha}


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "alice", *argv])
    return ask._main()


def save_via_approve(monkeypatch, question=Q):
    assert run(monkeypatch, "--approve", question, A) == 0


# 1 -- ONE WAY IN ----------------------------------------------------------

SAVE_PATHS = {
    "approve": lambda note: ["--approve", Q, A],
    "approve-rank": lambda note: ["--approve", Q, A, "--rank", "1"],
    "add-with-source": lambda note: ["--add", Q, A, "--source", str(note)],
}


@pytest.mark.parametrize("path", sorted(SAVE_PATHS))
def test_every_save_path_refuses_what_the_claim_check_refuses(world, monkeypatch, path):
    monkeypatch.setattr(ask, "run_gate", lambda claim, p, passage=None: ("REJECT", 0.2))
    run(monkeypatch, *SAVE_PATHS[path](world["note"]))
    assert not world["cache"]


@pytest.mark.parametrize("path", sorted(SAVE_PATHS))
def test_every_save_path_needs_the_current_threshold(world, monkeypatch, path):
    monkeypatch.setattr(ask, "run_gate", lambda claim, p, passage=None: ("CLEAN", 0.79))
    run(monkeypatch, *SAVE_PATHS[path](world["note"]))
    assert not world["cache"]


@pytest.mark.parametrize("path", sorted(SAVE_PATHS))
def test_every_save_path_saves_a_clean_answer(world, monkeypatch, path):
    assert run(monkeypatch, *SAVE_PATHS[path](world["note"])) == 0
    assert len(world["cache"]) == 1


@pytest.mark.parametrize("path", ["approve", "add-with-source"])
def test_every_save_path_runs_the_secret_scan(world, monkeypatch, path):
    monkeypatch.setattr(ask, "has_secret", lambda text: "blue" in text)
    monkeypatch.setattr(ask, "payload_has_secret", lambda req: False)
    run(monkeypatch, *SAVE_PATHS[path](world["note"]))
    assert not world["cache"]


def test_add_without_a_file_still_passes_the_secret_scan(world, monkeypatch):
    monkeypatch.setattr(ask, "has_secret", lambda text: "hunter2" in text)
    monkeypatch.setattr(ask, "payload_has_secret", lambda req: False)
    assert run(monkeypatch, "--add", "what is the wifi code today", "the code is hunter2") != 0
    assert not world["cache"]


def test_add_without_a_file_is_marked_no_source_file(world, monkeypatch, capsys):
    monkeypatch.setattr(ask, "run_gate", lambda *a, **k: pytest.fail("no file, so no file check"))
    q = "what is the office wifi name"
    assert run(monkeypatch, "--add", q, "The wifi name is Harbor.") == 0
    assert world["cache"]
    capsys.readouterr()
    assert ask.lookup(q, "alice", world["sdir"]) == 0
    assert "no source file" in capsys.readouterr().out


# 2 -- LIFETIME --------------------------------------------------------------

def age_records(sdir, days):
    path = sdir / "approvals.jsonl"
    old = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - days * 86400))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("".join(json.dumps({**r, "ts": old}) + "\n" for r in rows))


def test_saved_answer_has_no_24_hour_clock(world, monkeypatch, capsys):
    save_via_approve(monkeypatch)
    age_records(world["sdir"], 400)
    capsys.readouterr()
    assert ask.lookup(Q, "alice", world["sdir"]) == 0
    assert A in capsys.readouterr().out


def test_no_86400_second_setting_is_left_in_ask(world):
    assert "86400" not in re.sub(r"days \* 86400", "", SCRIPT.read_text())


def test_saved_answer_ends_when_its_source_file_changes(world, monkeypatch, capsys):
    save_via_approve(monkeypatch)
    world["note"].write_text("The car is red now.\n")
    capsys.readouterr()
    ask.lookup(Q, "alice", world["sdir"])
    out = capsys.readouterr().out
    assert "STALE" in out and A not in out


def test_missing_source_path_is_refused_loudly(world, monkeypatch, capsys):
    rc = run(monkeypatch, "--add", "what is in the file", "It says blue.", "--source", str(world["note"]) + ".nope")
    assert rc == 2
    assert "does not exist" in capsys.readouterr().out
    assert not [c for c in world["calls"] if c["action"] == "connect"]


# 3 -- ONE WAY OUT ------------------------------------------------------------

def test_miss_removes_the_saved_answer(world, monkeypatch, capsys):
    save_via_approve(monkeypatch)
    assert run(monkeypatch, "--miss", Q, "somewhere else") == 0
    assert not world["cache"]
    assert "un-saved" in capsys.readouterr().out


def test_miss_with_nothing_saved_exits_non_zero_and_says_so(world, monkeypatch, capsys):
    rc = run(monkeypatch, "--miss", "a question never asked or saved", "x.md")
    assert rc != 0
    assert "no saved answer" in capsys.readouterr().out


def test_miss_is_labelled_wrong_in_the_help_text():
    assert re.search(r"--miss[^\n]*\n?[^\n]*wrong", ask.__doc__, re.I)


# 4 -- MATCHING ------------------------------------------------------------------

@pytest.mark.parametrize("asked", ["what color is the car", "  WHAT   color is\tthe car?? ", "what color is the car ?!"])
def test_same_question_after_case_space_and_trailing_punctuation_hits(world, monkeypatch, capsys, asked):
    save_via_approve(monkeypatch)
    capsys.readouterr()
    assert ask.lookup(asked, "alice", world["sdir"]) == 0
    assert "CACHE HIT" in capsys.readouterr().out


@pytest.mark.parametrize("asked", ["what colour is the car", "what is the color of the car", "what color is the car and truck"])
def test_nothing_fuzzier_than_that_matches(world, monkeypatch, capsys, asked):
    save_via_approve(monkeypatch)
    capsys.readouterr()
    ask.lookup(asked, "alice", world["sdir"])
    assert "CACHE HIT" not in capsys.readouterr().out


def test_miss_removes_the_answer_under_any_spelling_of_the_question(world, monkeypatch):
    save_via_approve(monkeypatch)
    assert run(monkeypatch, "--miss", "  what COLOR is the car", "x") == 0
    assert not world["cache"]


# 5 -- HONEST REPLY ---------------------------------------------------------------

def test_hit_says_saved_answer_from_file_and_date(world, monkeypatch, capsys):
    save_via_approve(monkeypatch)
    capsys.readouterr()
    assert ask.lookup(Q, "alice", world["sdir"]) == 0
    out = capsys.readouterr().out
    assert f"saved answer, from {world['note']}, saved {time.strftime('%Y-%m-%d')}" in out


def test_changed_source_says_so_and_falls_through_to_live_search(world, monkeypatch, capsys):
    save_via_approve(monkeypatch)
    world["note"].write_text("The car is red now.\n")
    capsys.readouterr()
    ask.lookup(Q, "alice", world["sdir"])
    out = capsys.readouterr().out
    assert "source changed" in out and "Searching live" in out
    assert "CACHE HIT" not in out


def test_manual_note_whose_source_changed_is_stale_and_not_ranked(world, monkeypatch, capsys):
    """A saved --add note recorded before its source file was redrawn must not rank as current."""
    other = "what is the weekly range for the fund"
    assert run(monkeypatch, "--add", other, "The range is 3 to 5.", "--source", str(world["note"])) == 0
    name = world["pointers"][0]
    world["note"].write_text("The car is red now, and the range moved.\n")
    seen = []
    orig = ask.memory

    def spy(req):
        seen.append(req)
        return orig(req)

    monkeypatch.setattr(ask, "memory", spy)
    capsys.readouterr()
    ask.lookup("show me the range of the fund each week", "alice", world["sdir"])
    out = capsys.readouterr().out
    assert "STALE" in out
    assert not [r for r in seen if r.get("pointer") == name and r["action"] in ("navigate", "navigate-many", "search")]
    assert not [r for r in seen if name in (r.get("pointers") or [])]


# Regression: an answer approved before this redesign still hits ----------------------

def test_existing_approved_answer_still_hits(world, monkeypatch, capsys):
    """A pre-redesign save: stored under the caller's exact wording, with the old approvals line
    (no source hash, no file date rules)."""
    world["cache"][Q] = {"answer": A, "evidence": [{"sourceId": "one", "quote": "The car is blue.",
                                                    "path": "/x/car.md", "contentSHA": "s"}]}
    ask.record_approver(world["sdir"], Q, "principal:alice", pointer="p1")
    assert ask.lookup(Q, "alice", world["sdir"]) == 0
    out = capsys.readouterr().out
    assert "CACHE HIT" in out and A in out and "approved_by: principal:alice" in out
