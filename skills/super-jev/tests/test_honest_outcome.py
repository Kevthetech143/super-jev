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


def test_an_edited_file_a_refresh_would_not_admit_is_named_not_silently_dropped(tmp_path, monkeypatch, capsys):
    edited = tmp_path / "edited.md"
    edited.write_text("# Warranty\nchanged since the last refresh\n")
    stale = {"status": "preparation-required", "changed": [str(edited)], "missing": []}
    nav = lambda p, n: {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": str(edited)}], "stale": stale}
    _setup(tmp_path, monkeypatch, ["old"], nav)
    monkeypatch.setattr(ask, "edited_readable", lambda *a, **k: False)
    rc, lines = _ask(tmp_path, capsys)
    assert rc == 1 and lines[0].startswith("OUTCOME: not-found")
    assert "1 edited file not read until refreshed" in lines[0] and "--status" in lines[0]
    assert ask._RESULT["left_out"] == [{"what": ask.EDITED_WHAT, "count": 1, "where": str(tmp_path), "way_in": ask.EDITED_FIX}]
