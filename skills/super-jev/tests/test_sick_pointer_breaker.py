#!/usr/bin/env python3
"""Sick-pointer circuit breaker: per-pointer failure/latency tracking, benching
after N consecutive failures, one backoff retry on a 529/overloaded navigate,
and partial-success lookups that keep the failure visible without failing the
whole call. All offline: `ask.memory` is monkeypatched, state dir is tmp_path.

    python3 -m pytest skills/super-jev/tests/test_sick_pointer_breaker.py -q
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "ask.py"

spec = importlib.util.spec_from_file_location("ask_sick_pointer", SCRIPT)
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


@pytest.fixture(autouse=True)
def no_content_check(monkeypatch):
    monkeypatch.setattr(ask, "confirm", lambda question, paths: ({}, set(paths), None, {}))


def test_pointer_benched_after_threshold_consecutive_failures():
    health = {}
    for _ in range(ask.BENCH_FAIL_THRESHOLD):
        ask.record_pointer_outcome(health, "flaky", ok=False, elapsed=1.0)
    benched, remaining, fails = ask.pointer_benched(health, "flaky")
    assert benched is True
    assert fails == ask.BENCH_FAIL_THRESHOLD
    assert remaining > 0


def test_pointer_not_benched_below_threshold():
    health = {}
    for _ in range(ask.BENCH_FAIL_THRESHOLD - 1):
        ask.record_pointer_outcome(health, "flaky", ok=False, elapsed=1.0)
    benched, _, _ = ask.pointer_benched(health, "flaky")
    assert benched is False


def test_a_success_resets_the_failure_streak():
    health = {}
    for _ in range(ask.BENCH_FAIL_THRESHOLD):
        ask.record_pointer_outcome(health, "flaky", ok=False, elapsed=1.0)
    ask.record_pointer_outcome(health, "flaky", ok=True, elapsed=1.0)
    benched, _, fails = ask.pointer_benched(health, "flaky")
    assert benched is False
    assert fails == 0


def test_benched_pointer_expires_after_cooldown(monkeypatch):
    health = {}
    for _ in range(ask.BENCH_FAIL_THRESHOLD):
        ask.record_pointer_outcome(health, "flaky", ok=False, elapsed=1.0)
    # Push the last failure outside the cooldown window.
    health["flaky"]["last_fail_ts"] = time.time() - ask.BENCH_COOLDOWN_SECS - 1
    benched, remaining, _ = ask.pointer_benched(health, "flaky")
    assert benched is False
    assert remaining == 0


def test_lookup_benches_a_pointer_with_a_prior_failure_streak_and_reports_it(tmp_path, monkeypatch, capsys):
    sdir = tmp_path / "state"
    health = {}
    for _ in range(ask.BENCH_FAIL_THRESHOLD):
        ask.record_pointer_outcome(health, "sick", ok=False, elapsed=1.0)
    ask.save_pointer_health(sdir, health)

    calls = []

    def fake_memory(req):
        calls.append((req["action"], req.get("pointer")))
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["sick", "healthy"]}
        if req["action"] == "navigate" and req["pointer"] == "healthy":
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": "/ok.md"}]}
        raise AssertionError(f"navigate should never be called on a benched pointer: {req}")

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("q", "alice", sdir)

    assert ("navigate", "sick") not in calls
    assert rc == 0
    out = capsys.readouterr().out
    assert "[sick] benched" in out
    assert "/ok.md" in out


def test_overloaded_navigate_gets_one_backoff_retry_then_succeeds(tmp_path, monkeypatch, capsys):
    sdir = tmp_path / "state"
    attempts = {"n": 0}

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1"]}
        if req["action"] == "navigate":
            attempts["n"] += 1
            if attempts["n"] == 1:
                return {"status": "error", "reason": "Jev HTTP 529 overloaded"}
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": "/hit.md"}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "OVERLOAD_BACKOFF_SECS", 0)
    rc = ask.lookup("q", "alice", sdir)

    assert attempts["n"] == 2
    assert rc == 0
    out = capsys.readouterr().out
    assert "/hit.md" in out
    assert "unresolved" not in out


def test_overloaded_navigate_still_counts_as_a_failure_if_retry_also_fails(tmp_path, monkeypatch):
    sdir = tmp_path / "state"

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["p1"]}
        if req["action"] == "navigate":
            return {"status": "error", "reason": "Jev HTTP 529 overloaded"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "OVERLOAD_BACKOFF_SECS", 0)
    rc = ask.lookup("q", "alice", sdir)

    assert rc == 1
    health = ask.load_pointer_health(sdir)
    assert health["p1"]["fails"] == 1


def test_partial_pointer_failure_still_returns_healthy_results_rc0(tmp_path, monkeypatch, capsys):
    sdir = tmp_path / "state"

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["broken", "healthy"]}
        if req["action"] == "navigate" and req["pointer"] == "broken":
            return {"status": "error", "reason": "Navigation provider failed"}
        if req["action"] == "navigate" and req["pointer"] == "healthy":
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": "/ok.md"}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("q", "alice", sdir)

    assert rc == 0
    out = capsys.readouterr().out
    assert "/ok.md" in out
    assert "[broken]" in out
    assert "unresolved: 1 of 2 pointers errored" in out


def test_all_pointers_failing_still_exits_1_with_no_healthy_result(tmp_path, monkeypatch, capsys):
    sdir = tmp_path / "state"

    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": ["broken"]}
        if req["action"] == "navigate":
            return {"status": "error", "reason": "Navigation provider failed"}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    rc = ask.lookup("q", "alice", sdir)

    assert rc == 1
    out = capsys.readouterr().out
    assert "unresolved: 1 of 1 pointers errored" in out
