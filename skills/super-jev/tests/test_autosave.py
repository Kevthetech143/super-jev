#!/usr/bin/env python3
"""Frozen checks for auto-saved answers on repeat questions (one rule, built for any user).

Made-up users and notes (Acme-style); no fleet names, folders or principals. `ask.memory` is a
fake harness that keeps saved answers per principal, `ask.confirm` and `ask.run_gate` are stubs:
no network, no live state, no Jev call. Each test drives the public entry (`ask.lookup`, `ask._main`).

    python3 -m pytest skills/super-jev/tests/test_autosave.py -q
"""
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "ask.py"
spec = importlib.util.spec_from_file_location("ask_autosave", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

Q = "What is the Acme refund window?"
ACME = "Acme refund window: 30 days from delivery.\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """One principal (ann) with one collection (one pointer, one note). A second principal (bo) has nothing."""
    note = tmp_path / "acme-refunds.md"
    note.write_text(ACME)
    other = tmp_path / "acme-shipping.md"
    other.write_text("Acme ships in 2 days.\n")
    cache, calls = {}, []
    pointers = {"ann": ["acme"], "bo": []}
    state = {"routed": str(note), "rejected": set()}

    def sha(p):
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    def fake_memory(req):
        calls.append(req)
        act, who = req["action"], req.get("principal")
        if act == "panel":
            return {"pointers": [{"pointer": p} for p in pointers.get(who, [])]}
        if act == "cached":
            hit = cache.get((who, req["question"]))
            return ({"status": "verified-cache-hit", "answer": hit, "evidence": []} if hit
                    else {"status": "cache-miss", "checked": []})
        if act == "navigate":
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": state["routed"]}]}
        if act == "sources":
            return {"status": "ok", "sources": [{"sourceId": "s1", "originalPath": state["routed"],
                                                  "contentSHA": state["sha"]}]}
        if act == "open":
            return {"status": "ok", "attemptId": "att|" + req["question"]}
        if act == "assist":
            return {"status": "ready", "approvalTicket": "tk|" + req["attemptId"][4:],
                    "passages": [{"sourceId": "s1", "reviewedText": ACME.strip()}]}
        if act == "approve":
            cache[(who, req["ticket"][3:])] = req["answer"]
            return {"status": "saved"}
        if act == "forget":
            return ({"status": "forgotten", "pointers": ["acme"]} if cache.pop((who, req["question"]), None)
                    else {"status": "not-cached"})
        raise AssertionError(f"unexpected action {act}")

    state["sha"] = sha(note)

    def confirm(question, paths):
        return {p: (0.1 if p in state["rejected"] else 0.9) for p in paths}, set(), None, {}

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "confirm", confirm)
    gates = []

    def gate(claim, path, passage=None):
        gates.append(claim)
        return ("REJECT", 0.1) if path in state["rejected"] else ("CLEAN", 0.93)

    monkeypatch.setattr(ask, "run_gate", gate)
    monkeypatch.setattr(ask, "state_dir", lambda principal: tmp_path / "state" / principal)
    monkeypatch.delenv("SUPERJEV_AUTO_CACHE", raising=False)
    monkeypatch.delenv("SUPERJEV_SAVE_AFTER", raising=False)
    return {"gates": gates, "note": note, "other": other, "cache": cache, "calls": calls, "state": state,
            "sdir": tmp_path / "state" / "ann", "sha": sha}


def ask_once(world, question=Q, principal="ann"):
    """One ordinary ask; returns (output, number of navigate calls it made)."""
    before = sum(c["action"] == "navigate" for c in world["calls"])
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ask.lookup(question, principal, ask.state_dir(principal))
    return buf.getvalue(), sum(c["action"] == "navigate" for c in world["calls"]) - before


# 1 -- REPEAT: two wins by the same file, the third ask is answered from the saved answer
def test_repeat_question_won_twice_is_returned_saved_with_no_search(world):
    out1, n1 = ask_once(world)
    assert n1 == 1 and "Saved for next time" not in out1
    out2, n2 = ask_once(world)
    assert n2 == 1 and "Saved for next time" in out2 and world["cache"]
    assert len(world["gates"]) == 1  # the claim check ran once, on the saving ask
    out3, n3 = ask_once(world)
    assert n3 == 0 and len(world["gates"]) == 1  # a saved answer costs no check
    assert out3.startswith("OUTCOME: found - saved answer, its source unchanged")
    assert f"saved answer, from {world['note']}, saved " in out3 and "CACHE HIT" in out3
    assert "30 days" in out3


# 2 -- ONE WIN ONLY: still a full search
def test_one_win_is_not_enough(world):
    ask_once(world)
    assert not world["cache"]
    out, n = ask_once(world)  # the second ask is still a full search (it is the second win)
    assert n == 1


def test_wins_by_two_different_files_do_not_add_up(world):
    ask_once(world)
    world["state"]["routed"] = str(world["other"])
    world["state"]["sha"] = world["sha"](world["other"])
    ask_once(world)
    assert not world["cache"]


def test_the_threshold_is_a_setting_that_works_for_one_principal_one_note(world, monkeypatch):
    monkeypatch.setenv("SUPERJEV_SAVE_AFTER", "3")
    ask_once(world); ask_once(world)
    assert not world["cache"]
    ask_once(world)
    assert world["cache"]
    assert ask.save_after() == 3 and ask.SAVE_AFTER_DEFAULT == 2


def test_off_switch_saves_nothing(world, monkeypatch):
    monkeypatch.setenv("SUPERJEV_AUTO_CACHE", "0")
    for _ in range(3):
        ask_once(world)
    assert not world["cache"]


# 3 -- TWIN: the same question about a different person or date never reuses the answer
@pytest.mark.parametrize("twin", ["What is the Acme refund window for Bo?", "What is the Acme refund window on 2026-01-05?"])
def test_twin_question_does_not_reuse_the_answer(world, twin):
    ask_once(world); ask_once(world)
    assert world["cache"]
    out, n = ask_once(world, twin)
    assert n == 1 and "CACHE HIT" not in out and "Saved for next time" not in out


def test_same_words_in_other_case_spacing_and_punctuation_is_the_same_question(world):
    ask_once(world); ask_once(world)
    out, n = ask_once(world, "  what is the ACME refund   window ")
    assert n == 0 and "CACHE HIT" in out


# 4 -- CHANGED FILE: the saved answer is withheld, labelled stale, and a fresh search runs
def test_changed_file_is_withheld_stale_and_searched_fresh(world):
    ask_once(world); ask_once(world)
    world["note"].write_text("Acme refund window: 14 days from delivery.\n")
    world["state"]["sha"] = world["sha"](world["note"])
    out, n = ask_once(world)
    assert "STALE" in out and "CACHE HIT" not in out and "30 days" not in out
    assert n == 1 and str(world["note"]) in out
    ask_once(world)  # it wins its way back in on the new bytes (the stale answer is replaced)
    out, n = ask_once(world)
    assert n == 0 and "14 days" in out


# 5 -- ACCESS: an asker who cannot read the file never gets it
def test_another_principal_without_the_file_never_gets_it(world):
    ask_once(world); ask_once(world)
    assert world["cache"]
    out, n = ask_once(world, principal="bo")
    assert "CACHE HIT" not in out and str(world["note"]) not in out and "30 days" not in out
    assert n == 0  # bo has nothing connected, so nothing is even searched


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_a_file_the_asker_cannot_read_is_never_returned(world):
    ask_once(world); ask_once(world)
    world["note"].chmod(0)
    try:
        out, _ = ask_once(world)
    finally:
        world["note"].chmod(0o644)
    assert "CACHE HIT" not in out and "30 days" not in out and "STALE" in out


# 6 -- WRONG REPORT: after --miss the next ask is a full search, and the count starts over
def test_after_miss_the_next_ask_is_a_full_search(world, monkeypatch):
    ask_once(world); ask_once(world)
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "ann", "--miss", Q, "somewhere else"])
    assert ask._main() == 0 and not world["cache"]
    out, n = ask_once(world)
    assert n == 1 and "CACHE HIT" not in out
    assert not world["cache"]  # one win since the miss is not enough
    ask_once(world)
    assert world["cache"]


# 7 -- REJECTED FILE: a file the content check rejects is never saved
def test_a_file_the_content_check_rejects_is_never_saved(world):
    world["state"]["rejected"].add(str(world["note"]))
    for _ in range(4):
        out, _n = ask_once(world)
        assert "Saved for next time" not in out
    assert not world["cache"]


def test_a_file_the_save_check_refuses_is_not_saved_and_the_count_starts_over(world, monkeypatch):
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("REJECT", 0.2))
    ask_once(world)
    out, _ = ask_once(world)
    assert "not saved: check gate verdict REJECT" in out and not world["cache"]
    assert any(json.loads(l)["kind"] == "autosave-refused" for l in (world["sdir"] / "lookups.jsonl").read_text().splitlines())


# The weekly line
def test_trace_report_counts_searches_skipped_by_saved_answers(world, capsys):
    for _ in range(4):
        ask_once(world)  # win, win + save, then two saved answers
    capsys.readouterr()
    assert ask.trace_report(world["sdir"], 7, "ann") == 0
    assert "searches skipped by saved answers (last 7 days, principal ann): 2" in capsys.readouterr().out


# The saved text is the answer line, not the note's heading
NOTE = "# Acme refund policy\n\nThis note covers Acme policies.\n\nRefunds: 30 days from delivery.\n"


def test_saved_text_is_the_answer_line_not_the_heading(world):
    world["note"].write_text(NOTE)
    world["state"]["sha"] = world["sha"](world["note"])
    ask_once(world); ask_once(world)
    assert list(world["cache"].values()) == ["Refunds: 30 days from delivery."]


def test_answer_line_skips_headings_and_lines_that_only_repeat_the_question():
    q = "what is the acme refund window"
    assert ask.answer_line(NOTE, q) == "Refunds: 30 days from delivery."
    assert ask.answer_line("# Acme refund window\n", q) == ""


def test_a_refused_file_is_not_re_checked_until_it_changes(world, monkeypatch):
    monkeypatch.setenv("SUPERJEV_SAVE_AFTER", "1")
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None:
                        world["gates"].append(claim) or ("REJECT", 0.2))
    for _ in range(5):
        ask_once(world)
    assert len(world["gates"]) == 1 and not world["cache"]
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None:
                        world["gates"].append(claim) or ("CLEAN", 0.93))
    world["note"].write_text(ACME + "Late returns: 7 days.\n")
    world["state"]["sha"] = world["sha"](world["note"])
    ask_once(world)
    assert len(world["gates"]) == 2 and world["cache"]


def test_a_gate_error_is_not_remembered_and_the_next_ask_saves(world, monkeypatch):
    monkeypatch.setenv("SUPERJEV_SAVE_AFTER", "1")
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("ERROR", None))
    ask_once(world)
    assert not world["cache"] and not ask.refused_before(world["sdir"], Q, {"path": str(world["note"]), "sha": world["state"]["sha"]})
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.93))
    ask_once(world)
    assert world["cache"]


def test_a_miss_clears_a_remembered_refusal(world, monkeypatch):
    monkeypatch.setenv("SUPERJEV_SAVE_AFTER", "1")
    monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("REJECT", 0.2))
    ask_once(world)
    win = {"path": str(world["note"]), "sha": world["state"]["sha"]}
    assert ask.refused_before(world["sdir"], Q, win)
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "ann", "--miss", Q, "elsewhere"])
    ask._main()
    assert not ask.refused_before(world["sdir"], Q, win)


def test_answer_line_prefers_the_short_answer_over_a_long_intro():
    q = "What is the Acme refund window?"
    note = ("# Refunds\n\nThis long introduction explains how the Acme refund window works for every "
            "customer, and why the refund window matters to Acme.\n\nAcme refund window: 30 days from delivery.\n")
    assert ask.answer_line(note, q) == "Acme refund window: 30 days from delivery."


@pytest.mark.parametrize("question,decoy,answer", [
    ("When is trash pickup?", "Trash notes last updated 2026-09-01.", "Trash pickup is every Monday morning."),
    ("What is the Acme refund window?", "Acme refund page reviewed 2025-11-02.", "Acme refund window: thirty days from delivery."),
    ("What color is the car?", "Car bought in 2019.", "The car color is blue."),
])
def test_answer_line_prefers_more_question_words_over_a_dated_line(question, decoy, answer):
    assert ask.answer_line(f"# Notes\n\n{decoy}\n\n{answer}\n", question) == answer
    assert ask.answer_line(f"# Notes\n\n{answer}\n\n{decoy}\n", question) == answer
