#!/usr/bin/env python3
"""Batched Jev calls: routing for every pointer in one navigate-many request, every
file's content check in one navigation-cli run, and a claim check whose evidence is
over Jev's input ceiling split into parts instead of failing.

    python3 -m pytest skills/super-jev/tests/test_batch_jev.py -q
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
sys.path.insert(0, str(SKILL / "lib"))
import jev_client  # noqa: E402


@pytest.fixture(autouse=True)
def batched(monkeypatch):
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1")
    monkeypatch.setattr(ask.time, "sleep", lambda s: None)


def test_routing_asks_every_pointer_in_one_request_and_retries_only_the_overloaded(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_memory(req):
        calls.append(req)
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}, {"pointer": "p2"}, {"pointer": "p3"}]}
        if req["action"] == "navigate-many" and req["pointers"] == ["p1", "p2", "p3"]:
            return {"status": "ok", "results": {
                "p1": {"status": "candidates", "candidates": [{"score": 0.4, "originalPath": "/low.md"}]},
                "p2": {"status": "error", "reason": "Navigation provider failed: Jev HTTP 529"},
                "p3": {"status": "no-candidates", "candidates": []}}}
        if req["action"] == "navigate-many" and req["pointers"] == ["p2"]:
            return {"status": "ok", "results": {
                "p2": {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": "/high.md"}]}}}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "confirm", lambda q, paths: ({}, set(paths), None, {}))
    assert ask.lookup("where is it?", "alice", tmp_path) == 0
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()[:1].isdigit()]
    assert "/high.md" in lines[0] and "/low.md" in lines[1]
    assert [c["action"] for c in calls].count("navigate-many") == 2
    assert not any(c["action"] == "navigate" for c in calls)


def test_routing_falls_back_to_one_call_per_pointer_on_an_older_runtime(tmp_path, monkeypatch, capsys):
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        if req["action"] == "navigate-many":
            return {"status": "error", "reason": "Unknown action; use --describe."}
        if req["action"] == "navigate":
            return {"status": "candidates", "candidates": [{"score": 0.9, "originalPath": "/a.md"}]}
        raise AssertionError(req)

    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "confirm", lambda q, paths: ({}, set(paths), None, {}))
    assert ask.lookup("where is it?", "alice", tmp_path) == 0
    assert "/a.md" in capsys.readouterr().out


def test_content_checks_ride_one_run_and_one_file_error_stays_its_own(tmp_path, monkeypatch):
    files = []
    for name in ("a", "b", "c"):
        f = tmp_path / f"{name}.md"
        f.write_text(f"{name} notes: the gate code is 4411.\n")
        files.append(str(f))
    runs = []

    def fake_run(cmd, input, **kw):
        body = json.loads(input)
        runs.append(body)
        rows = [{"status": "candidates", "candidates": [{"score": 0.95, "sourceId": "0"}]},
                {"status": "error", "reason": "Navigation provider timed out"},
                {"status": "no-candidates", "candidates": []}]
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"results": rows, "calls": 1}), "")

    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    scores, _, error, notes = ask.confirm("what is the gate code", files)
    assert len(runs) == 1 and len(runs[0]["batch"]) == 3
    assert scores == {files[0]: 0.95}
    assert error == "Navigation provider timed out" and notes == {files[1]: ask.INCONCLUSIVE}


def test_a_failed_batched_run_falls_back_to_one_check_per_file(tmp_path, monkeypatch):
    files = []
    for name in ("a", "b"):
        f = tmp_path / f"{name}.md"
        f.write_text(f"{name} notes: the gate code is 4411.\n")
        files.append(str(f))
    runs = []

    def fake_run(cmd, input, **kw):
        body = json.loads(input)
        runs.append(body)
        if "batch" in body:
            return subprocess.CompletedProcess(cmd, 2, "", "Navigation input is too large")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(
            {"status": "candidates", "candidates": [{"score": 0.95, "sourceId": "0"}]}), "")

    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    scores, _, error, notes = ask.confirm("what is the gate code", files)
    assert len(runs) == 3 and "batch" in runs[0]
    assert scores == {files[0]: 0.95, files[1]: 0.95} and error is None and notes == {}


def test_number_dense_evidence_under_the_old_char_cap_is_split_not_sent_over_the_ceiling(monkeypatch):
    # Tens of thousands of characters of IDs and amounts: under the old 110k-character
    # guard, but dense enough that one call would run over Jev's token ceiling.
    rows = "\n".join(f"{i:06d} 2026-09-{i % 28 + 1:02d} {i * 37 % 100000:>8} ACCT-{i * 7919 % 10**8:08d}"
                     for i in range(2400))
    assert 60_000 < len(rows) < 110_000
    sent = []

    def fake_transport(url, body, headers, timeout):
        req = json.loads(body)
        longest = max(jev_client.estimate_tokens(q) for q in req["questions"].values())
        assert jev_client.estimate_tokens(req["state"]) + longest <= jev_client.MAX_INPUT_TOKENS
        sent.append(req)
        hit = "000042" in req["state"]
        answers = {k: {"choice": "CLEAN" if k == "leaked_internal" else "CONSISTENT" if k == "self_contradictory"
                       else "NOT_TIME_SENSITIVE" if k == "time_sensitive" else "HONEST" if k == "overclaim"
                       else ("SUPPORTED" if hit else "NOT_SUPPORTED"), "confidence": 0.95}
                   for k in req["questions"]}
        return {"answers": answers, "model": "fake", "usage": {"input_tokens": 1}}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(jev_client, "transport", fake_transport)
    rows_out, meta, code = jev_client.check([("/ledger.md", rows)], ["Row 000042 is in the ledger file."])
    assert len(sent) >= 2 and meta["chunks"] == len(sent)
    assert rows_out[0]["verdict"] == "SUPPORTED" and code == 0


def test_batched_and_single_checks_give_the_same_file_to_score_mapping(tmp_path, monkeypatch):
    # q15 shape: two near-tied files on the same topic. The fake judge scores each
    # payload from its own passage text, so a leak or a shifted row would show.
    files = []
    for name, body in (("nysc-membership", "NYSC membership: plan Passport, joined 2024.\n"),
                       ("nysc-incident-2025-09-19", "NYSC incident 2025-09-19: slipped in the locker room.\n"),
                       ("gym-notes", "Gym notes: leg day routine.\n" * 2000)):
        f = tmp_path / f"{name}.md"
        f.write_text(body)
        files.append(str(f))

    def judge(payload):
        text = "".join(n["description"] for n in payload["catalog"]["nodes"][1:])
        return {"status": "candidates", "candidates": [{"score": 0.6 + (sum(map(ord, text)) % 39) / 100, "sourceId": "0"}]}

    def fake_run(cmd, input, **kw):
        body = json.loads(input)
        out = {"results": [judge(p) for p in body["batch"]], "calls": 1} if "batch" in body else judge(body)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setattr(ask.subprocess, "run", fake_run)
    batched = ask.confirm("what is my NYSC membership plan", files)[0]
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    single = ask.confirm("what is my NYSC membership plan", files)[0]
    assert batched == single and len(set(batched.values())) == len(batched) == 3
