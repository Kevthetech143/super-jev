#!/usr/bin/env python3
"""The outcome and the partial notice say what was actually searched (2026-10-02).

1. A stale set served from its older catalog is searched: a "served from older catalog" note,
   never "partial: N sets not searched", and the lookup is not logged as partial.
2. Files skipped at setup leave a searched not-found a not-found, with a note naming the count
   and the --status command. needs-setup stays for a set that was really not searched.
3. A TypeSafe network error is retried once, in routing and in the content check.

Offline: memory and the content check are stubs, the notes are made up.

    python3 -m pytest skills/super-jev/tests/test_honest_outcome.py -q
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_honest", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
ah = ask.auto_heal

OK = "/notes/warranty.md"
NETWORK = {"status": "error", "reason": "Navigation provider failed: could not reach TypeSafe (network)"}


@pytest.fixture(autouse=True)
def stage():
    ask._STAGE.clear()
    yield
    ask._STAGE.clear()


def _cands():
    return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": OK}]}


def _setup(tmp_path, monkeypatch, pointers, navigate, scores=None):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", tmp_path / "cache")
    calls = []

    def fake(req):
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": p} for p in pointers]}
        if req["action"] == "recipe":
            return {"status": "no-recipe"}
        calls.append(req["pointer"])
        return navigate(req["pointer"], calls.count(req["pointer"]))
    monkeypatch.setattr(ask, "memory", fake)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})
    monkeypatch.setattr(ask, "batch_jev", lambda: False)
    monkeypatch.setattr(ask, "skipped_for_question", lambda *a, **k: [])
    monkeypatch.setattr(ah, "reconnect_now", lambda ptr, principal, timeout=None: "no-report")
    monkeypatch.setattr(ah, "reconnect_recipe", lambda ptr, principal, memory=None: "no-recipe")
    monkeypatch.setattr(ah, "maybe_heal", lambda *a, **k: "no-report")
    monkeypatch.setattr(ah, "maybe_scan", lambda *a, **k: None)
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (dict(scores or {}), set(), None, {}))
    return calls


def _ask(tmp_path, capsys):
    rc = ask.lookup("find the note about Acme's warranty period", "primary", tmp_path / "s")
    return rc, capsys.readouterr().out.splitlines()


# --- 1. a stale set served from its older catalog is searched ---------------------------------


def test_stale_set_served_from_older_catalog_is_searched_not_partial(tmp_path, monkeypatch, capsys):
    stale = {"status": "preparation-required", "changed": [], "missing": []}
    _setup(tmp_path, monkeypatch, ["old", "notes"],
           lambda p, n: {**_cands(), "stale": stale} if p == "old" else _cands(), {OK: 0.95})
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 0 and lines[0].startswith("OUTCOME: found") and "partial" not in lines[0]
    assert "served from older catalog: 1 set" in lines
    rec = json.loads((tmp_path / "s" / "lookups.jsonl").read_text().splitlines()[-1])
    assert "partial" not in rec


def test_stale_set_with_no_hit_is_a_plain_not_found(tmp_path, monkeypatch, capsys):
    stale = {"status": "preparation-required", "changed": [], "missing": []}
    _setup(tmp_path, monkeypatch, ["old"], lambda p, n: {"status": "no-candidates", "stale": stale})
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 1 and lines[0].startswith("OUTCOME: not-found")
    assert "served from older catalog: 1 set" in lines


def test_a_set_truly_not_searched_is_still_partial(tmp_path, monkeypatch, capsys):
    nav = lambda p, n: {"status": "error", "reason": "provider failed"} if p == "broken" else _cands()
    _setup(tmp_path, monkeypatch, ["broken", "notes"], nav, {OK: 0.95})
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 0 and "partial: 1 set not searched" in lines[0]
    assert not any(ln.startswith("served from older catalog") for ln in lines)


# --- 2. skipped files do not turn a searched not-found into needs-setup -----------------------


def test_skipped_files_leave_a_searched_not_found_with_a_note(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch, ["a", "b"], lambda p, n: {"status": "no-candidates"})
    monkeypatch.setattr(ask, "skipped_for_question", lambda *a, **k: [("x/a.pdf", "scanned image", "run ocr")] * 3)
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 1
    assert lines[0].startswith("OUTCOME: not-found - searched 2 sets")
    assert "3 files skipped at setup" in lines[0] and "--status" in lines[0]
    assert "needs-setup" not in lines[0]


def test_an_unprepared_set_is_still_needs_setup_even_with_skipped_files(tmp_path, monkeypatch, capsys):
    nav = lambda p, n: {"status": "preparation-required"} if p == "bench" else {"status": "no-candidates"}
    _setup(tmp_path, monkeypatch, ["bench", "notes"], nav)
    monkeypatch.setattr(ask, "skipped_for_question", lambda *a, **k: [("x/a.pdf", "scanned image", "run ocr")])
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 4 and lines[0].startswith("OUTCOME: needs-setup")


# --- 3. one retry on a TypeSafe network error --------------------------------------------------


def test_routing_retries_a_network_error_once_and_searches_the_set(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch, ["notes"], lambda p, n: NETWORK if n == 1 else _cands(), {OK: 0.95})
    rc, lines = _ask(tmp_path, capsys)
    assert calls == ["notes", "notes"]
    assert rc == 0 and lines[0].startswith("OUTCOME: found") and "partial" not in lines[0]


def test_routing_network_error_twice_counts_the_set_as_failed(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch, ["notes"], lambda p, n: NETWORK)
    rc, lines = _ask(tmp_path, capsys)
    assert calls == ["notes", "notes"]  # one retry, not a loop
    assert rc == 3 and lines[0].startswith("OUTCOME: error")


def test_routing_does_not_retry_a_key_error(tmp_path, monkeypatch, capsys):
    calls = _setup(tmp_path, monkeypatch, ["notes"], lambda p, n: {"status": "error", "reason": "key refused"})
    _ask(tmp_path, capsys)
    assert calls == ["notes"]


def test_content_check_retries_a_network_error_once(monkeypatch):
    monkeypatch.setattr(ask, "batch_jev", lambda: False)
    seen = []

    def one(question, path):
        seen.append(path)
        if seen.count(path) == 1:
            return None, False, "could not reach TypeSafe (network)", None
        return 0.9, False, None, None
    monkeypatch.setattr(ask, "confirm_one", one)
    scores, partial, error, notes = ask.confirm("q", [OK])
    assert seen == [OK, OK] and scores == {OK: 0.9} and error is None and notes == {}


def test_content_check_network_error_twice_stays_an_error(monkeypatch):
    monkeypatch.setattr(ask, "batch_jev", lambda: False)
    seen = []

    def one(question, path):
        seen.append(path)
        return None, False, "could not reach TypeSafe (network)", None
    monkeypatch.setattr(ask, "confirm_one", one)
    scores, _, error, _ = ask.confirm("q", [OK])
    assert seen == [OK, OK] and scores == {} and "network" in error


def test_a_file_held_for_a_secret_at_query_time_stays_needs_setup(tmp_path, monkeypatch, capsys):
    # picked for THIS question and never read: not a setup-time skip
    _setup(tmp_path, monkeypatch, ["a"], lambda p, n: {"status": "no-candidates"})
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {OK: ask.HELD_SECRET}))
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [(1.0, OK, "a")])
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 4 and lines[0].startswith("OUTCOME: needs-setup") and "1 file held" in lines[0]


def _edited_note(tmp_path, text="# Acme warranty period\nthe warranty period for Acme is 3 years\n"):
    note = tmp_path / "acme-warranty.md"
    note.write_text(text)
    return note


def test_an_edited_file_a_refresh_will_review_is_named_not_silently_dropped(tmp_path, monkeypatch, capsys):
    note = _edited_note(tmp_path)
    stale = {"status": "preparation-required", "changed": [str(note)], "missing": []}
    nav = lambda p, n: {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(note)}], "stale": stale}
    _setup(tmp_path, monkeypatch, ["old"], nav)
    # no recorded hash: it was never reviewed at a known version, so it is not read until a refresh does
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {str(note): {"pass": True}})
    monkeypatch.setattr(ask, "refresh_would_admit", lambda path, ptr: True)
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 1 and lines[0].startswith("OUTCOME: not-found")
    assert "1 edited file not read" in lines[0] and "--status" in lines[0]
    assert ask._RESULT["left_out"] == [{"what": ask.EDITED_WHAT, "count": 1, "where": str(tmp_path), "way_in": ask.EDITED_FIX}]


def test_an_edited_file_a_refresh_would_hold_again_gets_no_refresh_advice(tmp_path, monkeypatch, capsys):
    note = _edited_note(tmp_path)
    _setup(tmp_path, monkeypatch, ["old"], lambda p, n: {"status": "no-candidates"})
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {str(note): {"pass": True, "sha256": "0" * 64}})
    monkeypatch.setattr(ask, "refresh_would_admit", lambda path, ptr: False)
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 1
    assert ask._RESULT["left_out"] == [{"what": ask.EDITED_STUCK_WHAT, "count": 1, "where": str(tmp_path),
                                        "way_in": ask.EDITED_STUCK_FIX}]
    assert "refresh" not in ask._RESULT["left_out"][0]["way_in"]


def test_a_file_only_word_search_would_skip_is_counted_once_like_any_other(tmp_path, monkeypatch, capsys):
    # routing finds nothing, so only word search meets the edited note; it is counted once, by its set
    note = _edited_note(tmp_path)
    stale = {"status": "preparation-required", "changed": [str(note)], "missing": []}
    _setup(tmp_path, monkeypatch, ["old"], lambda p, n: {"status": "no-candidates", "stale": stale})
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {str(note): {"pass": True, "description": "Acme warranty"}})
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 1 and "1 edited file not read" in lines[0]
    assert [r["count"] for r in ask._RESULT["left_out"]] == [1]


def test_an_edited_file_now_holding_a_secret_is_held_and_needs_setup(tmp_path, monkeypatch, capsys):
    note = _edited_note(tmp_path, "# Acme warranty period\nthe period is 3 years\npassword: hunter2abcXYZ\n")
    assert ask.has_secret(note.read_text())  # the scanner the content check uses
    stale = {"status": "preparation-required", "changed": [str(note)], "missing": []}
    nav = lambda p, n: {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(note)}], "stale": stale}
    _setup(tmp_path, monkeypatch, ["old"], nav)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {str(note): {"pass": True, "sha256": "0" * 64}})
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 4 and lines[0].startswith("OUTCOME: needs-setup") and "1 file held" in lines[0]
    assert "0 sets" not in lines[0] and "edited file" not in lines[0]
    assert ask._RESULT["next"] == "include" and ask._RESULT["left_out"][0]["way_in"] == ask.SECRET_FIX


def test_held_only_does_not_say_zero_sets_stale(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch, ["notes"], lambda p, n: _cands())
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({}, set(), None, {OK: ask.HELD_SECRET}))
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 4 and "stale or unprepared" not in lines[0] and "1 file held" in lines[0]


def test_files_no_search_would_open_are_not_counted(tmp_path, monkeypatch, capsys):
    # another person's note and scratch test material are never read, so never "edited, not read"
    root = tmp_path / "agents" / "global" / "documents"
    (root / "nora").mkdir(parents=True)
    (root / "self").mkdir()
    (root / "nora" / "PROFILE.md").write_text("Relation: mother\n")
    (root / "self" / "PROFILE.md").write_text("Relation: self\n")
    other = root / "self" / "acme.md"
    other.write_text("changed text\n")
    scratch = tmp_path / "ops" / "sj-bench-x" / "new.md"
    scratch.parent.mkdir(parents=True)
    scratch.write_text("scratch\n")
    sha = lambda t: __import__("hashlib").sha256(t.encode()).hexdigest()
    files = {str(root / "nora" / "PROFILE.md"): {"pass": True, "sha256": sha("Relation: mother\n")},
             str(root / "self" / "PROFILE.md"): {"pass": True, "sha256": sha("Relation: self\n")},
             str(other): {"pass": True}, str(scratch): {"pass": True}}
    stale = {"status": "preparation-required", "changed": [str(other), str(scratch)], "missing": []}
    _setup(tmp_path, monkeypatch, ["old"], lambda p, n: {"status": "no-candidates", "stale": stale})
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: files)
    rc = ask.lookup("what is my mom's acme warranty period", "primary", tmp_path / "s")
    lines = capsys.readouterr().out.splitlines()
    assert rc == 1 and "edited file" not in lines[0]
    monkeypatch.setattr(ask, "refresh_would_admit", lambda path, ptr: True)
    assert ask.edited_held(["old"])["refresh"] == [str(other)]  # the person filter is what leaves it out above


def test_edited_files_show_in_the_needs_setup_reason_too(tmp_path, monkeypatch, capsys):
    note = _edited_note(tmp_path)
    nav = lambda p, n: {"status": "preparation-required"} if p == "bench" else {"status": "no-candidates"}
    _setup(tmp_path, monkeypatch, ["bench", "notes"], nav)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {str(note): {"pass": True}})
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 4 and "1 set stale or unprepared" in lines[0] and "1 edited file not read" in lines[0]
