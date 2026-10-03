#!/usr/bin/env python3
"""Frozen contract tests (2026-09-28): every ordinary ask starts with exactly one OUTCOME line,
computed from the whole search state, and the exit code matches it.

    OUTCOME: found | not-found | not-supported | needs-setup | error
    exit codes: found 0, not-found 1, not-supported 2, error 3, needs-setup 4

Also proves a note over 12,000 characters is supported: read in sections, found by a passage
past character 12,000. No network, no real key, prepare_bulk.py never runs.

    python3 -m pytest skills/super-jev/tests/test_outcome_line.py -q
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_outcome", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
ah = ask.auto_heal

OK = "/notes/warranty.md"


@pytest.fixture(autouse=True)
def stage():
    ask._STAGE.clear()
    yield
    ask._STAGE.clear()


def _setup(tmp_path, monkeypatch, pointers, navigate, scores=None):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", tmp_path / "cache")

    def fake(req):
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": p} for p in pointers]}
        if req["action"] == "recipe":
            return {"status": "no-recipe"}
        return navigate(req["pointer"])
    monkeypatch.setattr(ask, "memory", fake)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "batch_jev", lambda: False)
    monkeypatch.setattr(ah, "reconnect_now", lambda ptr, principal, timeout=None: "no-report")
    monkeypatch.setattr(ah, "reconnect_recipe", lambda ptr, principal, memory=None: "no-recipe")
    monkeypatch.setattr(ah, "maybe_heal", lambda *a, **k: "no-report")
    monkeypatch.setattr(ah, "maybe_scan", lambda *a, **k: None)
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (dict(scores or {}), set(), None, {}))


def _ask(tmp_path, question="find the note about Acme's warranty period"):
    rc = ask.lookup(question, "primary", tmp_path / "s")
    return rc, _read().splitlines()


_cap = {}


def _read():
    return _cap["capsys"].readouterr().out


@pytest.fixture(autouse=True)
def _grab(capsys):
    _cap["capsys"] = capsys


def _outcomes(lines):
    return [ln for ln in lines if ln.startswith("OUTCOME:")]


def _cands(*_):
    return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": OK}]}


def test_found(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, ["notes"], _cands, {OK: 0.95})
    rc, lines = _ask(tmp_path)
    assert rc == 0
    assert lines[0].startswith("OUTCOME: found") and "partial" not in lines[0]
    assert len(_outcomes(lines)) == 1
    assert any(OK in ln for ln in lines[1:])  # per-file result lines stay as they are


def test_found_but_a_pointer_errored_names_what_was_not_searched(tmp_path, monkeypatch):
    nav = lambda p: {"status": "error", "reason": "provider failed"} if p == "broken" else _cands()
    _setup(tmp_path, monkeypatch, ["broken", "notes"], nav, {OK: 0.95})
    rc, lines = _ask(tmp_path)
    assert rc == 0
    assert lines[0].startswith("OUTCOME: found") and "partial: 1 set not searched" in lines[0]
    assert any(OK in ln for ln in lines[1:]) and any(ln.startswith("[broken]") for ln in lines)
    assert not any(ln.startswith("unresolved:") for ln in lines)  # replaced by the OUTCOME line


def test_found_from_a_stale_set_is_searched_from_the_older_catalog(tmp_path, monkeypatch):
    stale = {"status": "preparation-required", "changed": ["/x.md"], "missing": []}
    nav = lambda p: {**_cands(), "stale": stale} if p == "old" else _cands()
    monkeypatch.setattr(ask, "edited_readable", lambda *a, **k: True)
    _setup(tmp_path, monkeypatch, ["old", "notes"], nav, {OK: 0.95})
    rc, lines = _ask(tmp_path)
    assert rc == 0 and lines[0].startswith("OUTCOME: found") and "partial" not in lines[0]
    assert "served from older catalog: 1 set" in lines  # searched from its last refresh, not left out


def test_not_found_is_a_complete_search_with_one_next_command(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, ["a", "b"], lambda p: {"status": "no-candidates"})
    rc, lines = _ask(tmp_path)
    assert rc == 1
    assert lines[0].startswith("OUTCOME: not-found") and "next:" in lines[0] and "python3" in lines[0]
    assert len(_outcomes(lines)) == 1
    text = "\n".join(lines)
    assert "no-candidates across" not in text  # the old verdict line is gone; the separate voice line stays last
    assert lines[-1] == ask.VOICE_LINE


def test_needs_setup_when_no_candidates_and_a_set_is_stale_and_it_never_goes_quiet(tmp_path, monkeypatch):
    nav = lambda p: {"status": "preparation-required"} if p == "bench" else {"status": "no-candidates"}
    _setup(tmp_path, monkeypatch, ["bench", "notes"], nav)
    for _ in range(2):  # the old "stale-quiet" drop hid it on the second ask
        rc, lines = _ask(tmp_path)
        assert rc == 4
        assert lines[0].startswith("OUTCOME: needs-setup") and "next:" in lines[0]
        assert any(ln.startswith("[bench]") for ln in lines)
    assert "stale-quiet" not in (tmp_path / "s" / "lookups.jsonl").read_text()


def test_needs_setup_when_nothing_is_connected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [], lambda p: {"status": "no-candidates"})
    rc, lines = _ask(tmp_path)
    assert rc == 4 and lines[0].startswith("OUTCOME: needs-setup") and "prepare_bulk.py" in lines[0]


@pytest.mark.parametrize("question", ["", "   ", "x" * (ask.MAX_QUESTION + 1)], ids=["empty", "blank", "too-long"])
def test_not_supported_for_an_empty_or_over_long_question(tmp_path, monkeypatch, question):
    _setup(tmp_path, monkeypatch, ["notes"], _cands, {OK: 0.95})
    rc, lines = _ask(tmp_path, question)
    assert rc == 2 and lines[0].startswith("OUTCOME: not-supported") and len(_outcomes(lines)) == 1


def test_error_when_every_pointer_failed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, ["a", "b"], lambda p: {"status": "error", "reason": "provider failed"})
    rc, lines = _ask(tmp_path)
    assert rc == 3 and lines[0].startswith("OUTCOME: error") and "next:" in lines[0]
    assert len(_outcomes(lines)) == 1 and not any(ln.startswith("unresolved:") for ln in lines)


def test_saved_answer_hit_also_starts_with_the_outcome_line(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, ["notes"], _cands, {OK: 0.95})
    hit = {"status": "verified-cache-hit", "answer": "24 months", "evidence": []}
    real = ask.memory
    monkeypatch.setattr(ask, "memory", lambda req: hit if req["action"] == "cached" else real(req))
    rc, lines = _ask(tmp_path)
    assert rc == 0 and lines[0].startswith("OUTCOME: found") and len(_outcomes(lines)) == 1


def test_exit_codes_are_one_documented_table():
    assert ask.OUTCOME_EXIT == {"found": 0, "not-found": 1, "not-supported": 2, "error": 3, "needs-setup": 4}
    for doc in (SKILL / "SKILL.md", SKILL.parent / "super-jev-connect" / "SKILL.md"):
        text = doc.read_text()
        assert "OUTCOME:" in text and "needs-setup" in text and "not-supported" in text


# --- contract v2: a note over 12,000 characters is supported, read in sections ---------------

def test_a_note_over_12000_chars_is_found_by_a_passage_past_char_12000(tmp_path, monkeypatch):
    needle = "Acme's warranty period is 24 months from delivery."
    filler = "".join(f"Acme shipping detail number {i} is logged here.\n" for i in range(400))
    text = "# Acme notes\n\n" + filler + "\n## Warranty\n\n" + needle + "\n" + filler[:3000]
    assert text.index(needle) > 12000 and 15000 < len(text.encode()) < 250000
    f = tmp_path / "acme.md"
    f.write_text(text)
    parts = ask.split_passages(text)
    idx = next(i for i, p in enumerate(parts) if needle in p)
    assert sum(len(p) for p in parts[:idx]) > 12000 - len(parts[idx])
    sent = []

    def fake_run(cmd, input, **kw):
        sent.append(json.loads(input))
        return subprocess.CompletedProcess(cmd, 0, json.dumps(
            {"status": "candidates", "candidates": [{"score": 0.93, "sourceId": str(idx)}]}), "")
    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    score, _, err, _ = ask.confirm_one("what is Acme's warranty period", str(f))
    assert err is None and score == 0.93
    assert any(needle in leaf["description"] for leaf in sent[0]["catalog"]["nodes"][1:])
    assert needle in ask.best_passage(str(f))
    assert ask._STAGE["checks"][str(f)]["best_line"] >= 1 + text[:text.index(needle)].count("\n")


# --- review round 1 fixes ---------------------------------------------------------------------

def test_found_with_a_failed_content_check_says_the_files_are_unconfirmed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, ["notes"], _cands)
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), "judge unreachable", {OK: ask.INCONCLUSIVE}))
    rc, lines = _ask(tmp_path)
    assert rc == 0  # table: found is 0 (candidates exist); the line says they are unconfirmed
    assert lines[0].startswith("OUTCOME: found") and "unconfirmed: content check failed" in lines[0]
    assert any(OK in ln and "inconclusive" in ln for ln in lines[1:])
    assert len(_outcomes(lines)) == 1


def test_a_200kb_note_is_found_by_a_passage_near_its_end(tmp_path, monkeypatch):
    needle = "Acme's warranty period is 24 months from delivery."
    filler = "".join(f"Acme shipping detail number {i} is logged here.\n" for i in range(4000))
    text = "# Acme notes\n\n" + filler + "\n## Warranty\n\n" + needle + "\n"
    assert 190_000 < len(text.encode()) < 250_000 and text.index(needle) > len(text) - 200
    f = tmp_path / "big.md"
    f.write_text(text)
    parts = ask.split_passages(text)
    idx = next(i for i, p in enumerate(parts) if needle in p)
    sent = []

    def fake_run(cmd, input, **kw):
        sent.append(json.loads(input))
        return subprocess.CompletedProcess(cmd, 0, json.dumps(
            {"status": "candidates", "candidates": [{"score": 0.93, "sourceId": str(idx)}]}), "")
    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    score, _, err, _ = ask.confirm_one("what is Acme's warranty period", str(f))
    assert err is None and score == 0.93
    assert any(needle in leaf["description"] for call in sent for leaf in call["catalog"]["nodes"][1:])
    assert needle in ask.best_passage(str(f))


def test_outcome_file_count_is_ranked_files_only_skills_on_their_own_label(tmp_path, monkeypatch):
    """Frozen 2026-09-29: the OUTCOME file count is the ranked files only; skill suggestions never add to it."""
    _setup(tmp_path, monkeypatch, ["notes"], _cands, {OK: 0.95})
    monkeypatch.setenv("SUPERJEV_SKILLS", "1")
    monkeypatch.setattr(ask, "skill_catalog", lambda q: [("a", "/s/a/SKILL.md"), ("b", "/s/b/SKILL.md")])
    rc, lines = _ask(tmp_path, "skill search step by step")
    assert rc == 0
    assert lines[0].startswith("OUTCOME: found - 1 file; 2 skill suggestions"), lines[0]
    assert len(_outcomes(lines)) == 1
