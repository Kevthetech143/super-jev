#!/usr/bin/env python3
"""Night of 2026-09-27: a note written minutes earlier could not be found by a "have we
already tried this?" ask. Its set had changed and was cooling down after a recent refresh,
so every ask skipped the whole set (new and unchanged notes alike), and the only sign was a
"preparation-required ... cooling down [STALE]" line at the bottom that agents ignore.

Now the ask routes a stale set on its last refresh (memory navigate lastGood): its files
still answer, the line says it answered from its last refresh, and it is not an error.
No network; prepare_bulk.py never runs.

    python3 -m pytest skills/super-jev/tests/test_stale_set_answers.py -q
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_stale_set", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
ah = ask.auto_heal
pytestmark = pytest.mark.real_toc  # _setup stubs the TOC read list itself (every cached file, in order)


def _setup(tmp_path, monkeypatch, changed=(), heal="cooldown"):
    """A connected set 'notes' (tried.md unchanged, log.md edited since its last refresh)
    whose memory navigate answers lastGood with its last refresh's candidates."""
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    notes = tmp_path / "notes"
    notes.mkdir()
    tried, log_md = notes / "tried.md", notes / "log.md"
    tried.write_text("# Tried\nWe tried the cache warmer; it did not help.\n")
    log_md.write_text("# Log\nOlder entries.\n")
    cache = {str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True}
             for f in (tried, log_md)}
    cdir = tmp_path / "cache"
    cdir.mkdir()
    (cdir / "notes-report.json").write_text(json.dumps(
        {"pointer": "notes", "principals": ["primary"], "roots": [str(notes)]}))
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", cdir)
    for f, text in changed:
        f(tried, log_md).write_text(text)
    stale = {"status": "preparation-required",
             "changed": [str(f(tried, log_md)) for f, _ in changed], "missing": []}
    requests = []

    def nav_out(req):
        if not req.get("lastGood"):
            return {"status": "preparation-required"}
        return {"status": "candidates", "calls": 1, "trace": [], "complete": False, "message": "ok",
                "candidates": [{"originalPath": str(tried), "score": 0.9},
                               {"originalPath": str(log_md), "score": 0.9}],
                "stale": stale}

    def fake(req):
        requests.append(req)
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            # a stale set is known from the registry's snapshot status, with no routing call
            return {"pointers": [{"pointer": "notes", **({"snapshotStatus": "preparation-required"} if changed else {})}]}
        if req["action"] == "navigate-many":
            return {"status": "ok", "results": {p: nav_out(req) for p in req["pointers"]}}
        if req["action"] == "navigate":
            return nav_out(req)
        return {"status": "error"}
    monkeypatch.setattr(ask, "memory", fake)
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: cache if ptr == "notes" else {})
    monkeypatch.setattr(ask.toc_search, "run", lambda q, corpus, hits, hooks, cache_path=None: (list(corpus), [], {}))
    monkeypatch.setattr(ah, "reconnect_now", lambda ptr, principal, timeout=None: "changed")
    heals = []
    monkeypatch.setattr(ah, "maybe_heal", lambda *a, **k: heals.append(a) or heal)
    monkeypatch.setattr(ah, "last_refresh_error", lambda *a: None)
    confirmed = []
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (confirmed.extend(ps) or
                                                      {p: 0.95 for p in ps}, set(), None, {}))
    return tried, log_md, requests, heals, confirmed


def test_a_cooling_down_set_still_answers_from_its_last_refresh(tmp_path, monkeypatch, capsys):
    new = "# Log\nOlder entries.\nTonight: we tried the cache warmer again; still no help.\n"
    tried, log_md, requests, heals, _ = _setup(tmp_path, monkeypatch,
                                               changed=[(lambda t, l: l, new)])
    rc = ask.lookup("have we already tried the cache warmer?", "primary", tmp_path / "s")
    out = capsys.readouterr().out
    assert not [r for r in requests if r["action"].startswith("navigate")]  # a set with a cache is routed by the registry
    assert str(tried) in out  # unchanged note: found
    assert str(log_md) in out  # the note edited minutes ago: found at its current text
    line = next(ln for ln in out.splitlines() if ln.startswith("[notes]"))
    assert "searched as of its last refresh" in line and "files changed since" in line
    assert "cooling down" in line and "[STALE]" in line
    assert "unresolved" not in out and rc == 0
    assert len(heals) == 1  # the bounded auto-heal still runs once; the cooldown is kept


def test_an_edited_file_a_refresh_would_hold_is_not_read(tmp_path, monkeypatch, capsys):
    secret = "# Log\ncache warmer\napi_key = sk-live-" + "a1B2c3D4e5" * 4 + "\n"
    tried, log_md, _, _, confirmed = _setup(tmp_path, monkeypatch, changed=[(lambda t, l: l, secret)])
    ask.lookup("have we already tried the cache warmer?", "primary", tmp_path / "s")
    out = capsys.readouterr().out
    assert str(tried) in out
    # never read or ranked; it is named as held (path only, never its text) so the gap is not silent
    assert str(log_md) not in confirmed and f"HELD  {log_md}  (contains a secret; not sent)" in out
    assert "a1B2c3D4e5" not in out
    ranked = lambda text: [l for l in text.splitlines() if l.lstrip()[:1].isdigit() and str(log_md) in l]
    assert not ranked(out)
    assert ranked(f" 0.91  {log_md}  [notes]\n")  # the check can fail: a ranked line is seen


def test_a_lookup_starts_the_new_file_scan_for_every_visible_set(tmp_path, monkeypatch, capsys):
    # A note written into a connected folder after its connect never marks its set stale, so the
    # lookup looks for new files itself (background, at most once per interval; see auto_heal.scan).
    _setup(tmp_path, monkeypatch)
    scans = []
    monkeypatch.setattr(ah, "maybe_scan", lambda principal, ptrs: scans.append((principal, list(ptrs))) or "started")
    ask.lookup("have we already tried the cache warmer?", "primary", tmp_path / "s")
    assert scans == []  # SUPERJEV_NEW_FILE_SCAN=0 (tests, or a user opting out)
    monkeypatch.setenv("SUPERJEV_NEW_FILE_SCAN", "1")
    ask.lookup("have we already tried the cache warmer?", "primary", tmp_path / "s")
    assert scans == [("primary", ["notes"])]
    monkeypatch.setenv("SUPERJEV_REPLAY", "1")
    ask.lookup("have we already tried the cache warmer?", "primary", tmp_path / "s")
    assert len(scans) == 1  # a replay never starts paid work
    capsys.readouterr()


def _claim(monkeypatch, proof):
    """A --claim ask whose claim judge finds `proof` supports it; returns the saved verdicts."""
    claim = "We tried the cache warmer."
    monkeypatch.setitem(ask._CLAIM, "text", claim)
    monkeypatch.setattr(ask, "claim_cache_get", lambda *a: None)
    rec = {"verdict": "supported", "prob": 0.97, "line": "we tried the cache warmer", "lineno": 2}

    def judge(question, pool):
        ask._STAGE["claim_files"] = {str(proof): rec}
        return str(proof), 0.97
    monkeypatch.setattr(ask, "judge_listwise", judge)
    saved = []
    monkeypatch.setattr(ask, "claim_cache_put", lambda sdir, text, word, path, r: saved.append(path))
    return claim, saved


def test_a_claim_proved_by_an_edited_file_is_never_saved(tmp_path, monkeypatch, capsys):
    new = "# Log\nTonight: we tried the cache warmer again.\n"
    tried, log_md, _, _, _ = _setup(tmp_path, monkeypatch, changed=[(lambda t, l: l, new)])
    claim, saved = _claim(monkeypatch, log_md)
    # Routing alone brings it in: word search (which marks its own edited files) finds nothing.
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [])
    ask.lookup(claim, "primary", tmp_path / "s")
    assert "TRUE" in capsys.readouterr().out
    assert str(log_md) in ask._STAGE["stale_changed"] and saved == []


def test_a_claim_proved_by_an_unchanged_file_of_a_stale_set_is_saved(tmp_path, monkeypatch, capsys):
    tried, log_md, _, _, _ = _setup(tmp_path, monkeypatch, changed=[(lambda t, l: l, "# Log\nnew\n")])
    claim, saved = _claim(monkeypatch, tried)
    ask.lookup(claim, "primary", tmp_path / "s")
    assert "TRUE" in capsys.readouterr().out
    assert saved == [str(tried)]


def test_an_edited_file_its_last_review_failed_is_not_read(tmp_path, monkeypatch, capsys):
    new = "# Log\nTonight: we tried the cache warmer again.\n"
    tried, log_md, _, _, confirmed = _setup(tmp_path, monkeypatch, changed=[(lambda t, l: l, new)])
    # A refresh reviewed the new bytes, failed them, and never reconnected.
    failed = {**ask.load_cache_files("notes"),
              str(log_md): {"sha256": hashlib.sha256(log_md.read_bytes()).hexdigest(), "pass": False}}
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: failed if ptr == "notes" else {})
    ask.lookup("have we already tried the cache warmer?", "primary", tmp_path / "s")
    out = capsys.readouterr().out
    assert str(tried) in out
    assert str(log_md) not in confirmed and str(log_md) not in out


def test_an_older_runtime_without_last_good_still_reports_the_stale_set(tmp_path, monkeypatch, capsys):
    tried, _, _, _, _ = _setup(tmp_path, monkeypatch, changed=[(lambda t, l: l, "# Log\nnew\n")])
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})  # no prepare-cache: routed by navigate
    monkeypatch.setattr(ask, "word_search", lambda *a, **k: [(0.9, str(tried), "notes")])  # the word search still reads its index
    monkeypatch.setattr(ask.toc_search, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no toc")))  # its list is read
    real = ask.memory
    monkeypatch.setattr(ask, "memory", lambda req: real({k: v for k, v in req.items() if k != "lastGood"}))
    ask.lookup("have we already tried the cache warmer?", "primary", tmp_path / "s")
    out = capsys.readouterr().out
    assert "[notes] preparation-required" in out and out.startswith("OUTCOME: found")
    assert "partial: 1 set not searched" in out


def test_a_refresh_landing_mid_routing_is_asked_again_not_an_error(tmp_path, monkeypatch, capsys):
    tried, _, requests, _, _ = _setup(tmp_path, monkeypatch, changed=[(lambda t, l: l, "# Log\nnew\n")])
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: {})  # no prepare-cache: routed by navigate
    real, calls = ask.memory, []

    def racing(req):
        if req["action"].startswith("navigate"):
            calls.append(req["action"])
            if len(calls) == 1:  # the refresh re-registered the set during this call
                return {"status": "ok", "results": {p: {"status": "pointer-changed"} for p in req["pointers"]}} \
                    if req["action"] == "navigate-many" else {"status": "pointer-changed"}
            return {"status": "candidates", "calls": 1, "trace": [], "complete": False, "message": "ok",
                    "candidates": [{"originalPath": str(tried), "score": 0.9}]}
        return real(req)
    monkeypatch.setattr(ask, "memory", racing)
    rc = ask.lookup("have we already tried the cache warmer?", "primary", tmp_path / "s")
    out = capsys.readouterr().out
    assert len(calls) == 2 and str(tried) in out
    assert "pointer-changed" not in out and "unresolved" not in out and rc == 0
