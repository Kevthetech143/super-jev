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
    state = {"routed": str(note), "rejected": set(), "also": []}

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
            paths = [state["routed"]] + state["also"]
            return {"status": "candidates", "candidates": [{"score": 0.9 - 0.05 * i, "originalPath": p} for i, p in enumerate(paths)]}
        if act == "sources":
            return {"status": "ok", "sources": [
                {"sourceId": "s1", "originalPath": p, "contentSHA": state["sha"] if p == state["routed"] else state.setdefault("shas", {}).setdefault(p, sha(p))}
                for p in [state["routed"]] + state["also"]]}
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
        return {p: (0.1 if p in state["rejected"] else 0.9 - 0.05 * i) for i, p in enumerate(paths)}, set(), None, {}

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
    assert world["gates"] == []  # no claim check on the saving ask
    out3, n3 = ask_once(world)
    assert n3 == 0 and world["gates"] == []  # nor on a hit
    assert out3.startswith("OUTCOME: found - 1 file; saved answer, sources unchanged")
    assert f"saved answer, from {world['note']}, saved " in out3 and "CACHE HIT" in out3
    assert f"  {world['note']}  [acme]" in out3


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
    assert n == 0 and "CACHE HIT" in out and str(world["note"]) in out


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


# The weekly line
def test_trace_report_counts_searches_skipped_by_saved_answers(world, capsys):
    for _ in range(4):
        ask_once(world)  # win, win + save, then two saved answers
    capsys.readouterr()
    assert ask.trace_report(world["sdir"], 7, "ann") == 0
    assert "searches skipped by saved answers (last 7 days, principal ann): 2" in capsys.readouterr().out


# The saved thing is the file: no claim check, no answer text, a secret is still held
def test_a_repeat_win_saves_the_file_with_no_answer_text_and_no_claim_check(world):
    ask_once(world)
    out2, _ = ask_once(world)
    assert "Saved for next time" in out2 and world["gates"] == []
    saved = list(world["cache"].values())
    assert saved == [f"Saved file: {world['note']}"] and "30 days" not in saved[0]
    out3, n3 = ask_once(world)
    assert n3 == 0 and world["gates"] == []
    assert str(world["note"]) in out3 and "answer:" not in out3 and "evidence:" not in out3


def test_a_file_holding_a_secret_is_never_saved(world):
    world["note"].write_text(ACME + "password: hunter2hunter2\n")
    world["state"]["sha"] = world["sha"](world["note"])
    ask_once(world)
    out, _n = ask_once(world)
    assert "not saved: secret-held" in out and "Saved for next time" not in out
    assert not world["cache"]


# A win counts only on a COMPLETE search: a partial one records no win and does not reset the count
def _second_set_down(world, monkeypatch, down):
    real = ask.memory
    world["down"] = down

    def memory(req):
        if req["action"] == "panel" and req.get("principal") == "ann":
            return {"pointers": [{"pointer": "acme"}, {"pointer": "acme2"}]}
        if req["action"] == "navigate" and req.get("pointer") == "acme2":
            return {"status": "error", "reason": "provider failed"} if world["down"] else {"status": "no-candidates"}
        return real(req)

    monkeypatch.setattr(ask, "memory", memory)


def test_two_partial_search_wins_do_not_save(world, monkeypatch):
    _second_set_down(world, monkeypatch, True)
    for _ in range(3):
        out, _n = ask_once(world)
        assert "partial: 1 set not searched" in out and "Saved for next time" not in out
    assert not world["cache"]


def test_a_partial_search_neither_counts_nor_resets_the_count(world, monkeypatch):
    _second_set_down(world, monkeypatch, False)
    ask_once(world)  # win 1, complete
    world["down"] = True
    ask_once(world)  # partial: no win, no reset
    world["down"] = False
    out, _n = ask_once(world)  # win 2, complete
    assert "Saved for next time" in out and world["cache"]


# The saved answer keeps the whole ranked list, so a right file that ranked 2-5 is never lost
def _three_files(world, tmp_path):
    extra = []
    for name in ("acme-returns.md", "acme-policy.md"):
        f = tmp_path / name
        f.write_text(f"Acme {name} refund window notes.\n")
        extra.append(str(f))
    world["state"]["also"] = extra
    world["state"]["shas"] = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in extra}  # as at connect
    return extra


def _rank_lines(out):
    return [l.split()[1] for l in out.splitlines() if l[:5].strip().replace(".", "").isdigit()]


def test_a_hit_returns_all_ranked_files_from_the_saving_search_in_order(world, tmp_path):
    extra = _three_files(world, tmp_path)
    live, _ = ask_once(world)
    ranked = _rank_lines(live)
    assert ranked == [str(world["note"])] + extra
    ask_once(world)
    hit, n = ask_once(world)
    assert n == 0 and "CACHE HIT" in hit and _rank_lines(hit) == ranked


def test_a_change_to_the_third_file_makes_the_saved_answer_stale_and_searches_live(world, tmp_path):
    extra = _three_files(world, tmp_path)
    ask_once(world); ask_once(world)
    Path(extra[1]).write_text("Acme policy: changed.\n")
    out, n = ask_once(world)
    assert "STALE" in out and "CACHE HIT" not in out and n == 1
    assert _rank_lines(out) == [str(world["note"])] + extra  # the live search shows every file again


def test_outcome_count_on_a_hit_is_the_number_of_saved_files(world, tmp_path):
    _three_files(world, tmp_path)
    ask_once(world); ask_once(world)
    hit, _ = ask_once(world)
    assert hit.startswith("OUTCOME: found - 3 files; saved answer, sources unchanged")


def test_an_inconclusive_rank_two_file_records_no_win(world, tmp_path, monkeypatch):
    extra = _three_files(world, tmp_path)
    monkeypatch.setattr(ask, "confirm", lambda q, paths: ({p: 0.9 - 0.05 * i for i, p in enumerate(paths)}, set(), None,
                                                          {extra[0]: ask.INCONCLUSIVE}))
    for _ in range(3):
        out, _n = ask_once(world)
        assert "Saved for next time" not in out
    assert not world["cache"]
    assert not any(json.loads(l).get("win") for l in (world["sdir"] / "lookups.jsonl").read_text().splitlines())


def test_a_file_that_cannot_be_hashed_records_no_win(world, tmp_path):
    ghost = str(tmp_path / "gone.md")
    assert ask.win_of([(0.9, str(world["note"]), "acme"), (0.8, ghost, "acme")], {}, {}) is None


def test_a_principal_that_lost_the_pointer_of_the_third_file_gets_no_hit(world, tmp_path):
    _three_files(world, tmp_path)
    ask_once(world); ask_once(world)
    path = world["sdir"] / "approvals.jsonl"
    rec = json.loads(path.read_text().splitlines()[-1])
    rec["files"][2]["pointer"] = "acme-old"
    path.write_text(json.dumps(rec) + "\n")
    out, n = ask_once(world)
    assert "STALE" in out and "acme-old" in out and "CACHE HIT" not in out and n == 1


def test_a_hit_prints_the_skill_suggestions_and_the_leans_none_note_like_live(world, monkeypatch):
    monkeypatch.setenv("SUPERJEV_SKILLS", "1")
    monkeypatch.setattr(ask, "skill_catalog", lambda q: [("acme-refunds", "/skills/acme-refunds/SKILL.md")])
    q = "Which skill covers the Acme refund window?"
    live, _ = ask_once(world, q)
    assert "skill  /skills/acme-refunds/SKILL.md" in live
    ask_once(world, q)
    hit, n = ask_once(world, q)
    assert n == 0 and "skill  /skills/acme-refunds/SKILL.md  [skills: acme-refunds]" in hit
    assert hit.startswith("OUTCOME: found - 1 file; 1 skill suggestion; saved answer")
    win = ask.win_of([(0.9, str(world["note"]), "acme")], {}, {}, [("s", "/p")], True)
    assert win["skills"] == [{"name": "s", "path": "/p"}] and win["leans_none"] is True


def test_an_older_single_file_save_prints_saved_not_a_score(world):
    ask_once(world); ask_once(world)
    path = world["sdir"] / "approvals.jsonl"
    rec = json.loads(path.read_text().splitlines()[-1])
    for k in ("files", "skills", "leans_none"):
        rec.pop(k, None)
    path.write_text(json.dumps(rec) + "\n")
    out, _ = ask_once(world)
    assert f"saved  {world['note']}  [acme]" in out and "0.00" not in out


# -- a judge whose lines are not measured never saves or approves; wins are counted per judge
def _use_judge(monkeypatch, name):
    import judge_profile
    monkeypatch.setattr(judge_profile, "PROFILE", judge_profile.load(name))


def _lines(path):
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_each_lookup_records_which_judge_answered(world):
    ask_once(world)
    look = [r for r in _lines(world["sdir"] / "lookups.jsonl") if r["kind"] == "lookup"]
    assert look and all(r["judge"] == "typesafe-jev" for r in look)


def test_uncalibrated_judge_never_autosaves_and_says_why(world, monkeypatch):
    _use_judge(monkeypatch, "laya")
    ask_once(world)
    out, _ = ask_once(world)
    ask_once(world)
    assert not world["cache"] and "Saved for next time" not in out
    assert "uncalibrated" in out and "laya" in out


def test_uncalibrated_judge_never_approves(world, monkeypatch, capsys):
    ask_once(world)
    _use_judge(monkeypatch, "laya")
    assert ask.approve("ann", Q, "30 days", world["sdir"]) == 1
    assert "uncalibrated" in capsys.readouterr().out and not world["cache"]


def test_save_answer_itself_refuses_when_uncalibrated(world, monkeypatch, capsys):
    ask_once(world)
    _use_judge(monkeypatch, "laya")
    assert ask.save_answer("ann", Q, "30 days", world["sdir"], approved_by="x") == 1
    assert "uncalibrated" in capsys.readouterr().out and not world["cache"]


def test_wins_by_another_judge_do_not_count_and_the_saved_record_names_the_judge(world, monkeypatch):
    _use_judge(monkeypatch, "laya")
    ask_once(world); ask_once(world)            # two wins, by the uncalibrated judge
    _use_judge(monkeypatch, "typesafe-jev")
    ask_once(world)                             # its first win: not enough
    assert not world["cache"]
    ask_once(world)                             # its second: saved
    assert world["cache"]
    rec = _lines(world["sdir"] / "approvals.jsonl")[-1]
    assert rec["judge"] == "typesafe-jev"


# SAVE BY HAND, NO ANSWER TEXT: --approve Q [--rank N] saves the last ranked list, like a repeat win
def approve_list(monkeypatch, *args, question=Q):
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "ann", "--approve", question, *args])
    return ask._main()


def test_approve_with_no_answer_saves_the_ranked_list_with_the_chosen_file_first(world, tmp_path, monkeypatch):
    extra = _three_files(world, tmp_path)
    ask_once(world)
    assert approve_list(monkeypatch, "--rank", "2") == 0
    assert world["gates"] == [] and list(world["cache"].values()) == [f"Saved file: {extra[0]}"]
    rec = ask.saved_record(world["sdir"], Q)
    assert rec["approved_by"] == "principal:ann" and [f["path"] for f in rec["files"]] == [extra[0], str(world["note"]), extra[1]]
    hit, n = ask_once(world)
    assert n == 0 and "CACHE HIT" in hit and _rank_lines(hit) == [extra[0], str(world["note"]), extra[1]]


def test_approve_with_no_answer_and_no_rank_saves_the_list_as_ranked(world, tmp_path, monkeypatch):
    extra = _three_files(world, tmp_path)
    live, _ = ask_once(world)
    assert approve_list(monkeypatch) == 0
    hit, n = ask_once(world)
    assert n == 0 and _rank_lines(hit) == _rank_lines(live)


def test_approve_with_no_answer_still_runs_the_secret_scan(world, tmp_path, monkeypatch):
    extra = _three_files(world, tmp_path)
    ask_once(world)
    Path(extra[1]).write_text("password: hunter2hunter2\n")
    assert approve_list(monkeypatch) == 1 and not world["cache"]


def test_approve_with_no_answer_still_refuses_a_file_changed_since_connect(world, monkeypatch):
    ask_once(world)
    world["note"].write_text(ACME + "Edited after connect.\n")
    assert approve_list(monkeypatch) == 1 and not world["cache"]


def test_approve_with_no_answer_needs_an_earlier_search(world, monkeypatch):
    assert approve_list(monkeypatch) == 1 and not world["cache"]


def test_approve_with_no_answer_refuses_a_list_holding_a_possible_tier_file(world, tmp_path, monkeypatch, capsys):
    _three_files(world, tmp_path)
    ask_once(world)
    log = world["sdir"] / "lookups.jsonl"
    recs = _lines(log)
    recs[-1]["top"][1]["possible"] = True
    log.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert approve_list(monkeypatch) == 1 and not world["cache"]
    assert "pick one file" not in capsys.readouterr().out


def test_approve_with_no_answer_refuses_a_listed_file_other_than_the_first_changed_since_connect(world, tmp_path, monkeypatch):
    extra = _three_files(world, tmp_path)
    ask_once(world)
    Path(extra[1]).write_text(Path(extra[1]).read_text() + "Edited after connect.\n")
    assert approve_list(monkeypatch) == 1 and not world["cache"]


def test_approve_with_an_empty_answer_takes_the_no_text_path(world, monkeypatch):
    ask_once(world)
    assert approve_list(monkeypatch, "  ") == 0
    assert world["gates"] == [] and list(world["cache"].values()) == [f"Saved file: {world['note']}"]
