#!/usr/bin/env python3
"""Tests for the evidence-guard blocklist + redactor Python mirror in
superjev.py, over the SAME fixture test/enhance/evidence-guard.test.ts runs
(test/enhance/fixtures/evidence-guard-cases.json), so the TS and Python
sides of the guard cannot silently drift apart.

    python3 -m pytest skills/super-jev/tests/test_evidence_guard.py -q
"""
import importlib.util
import json
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL.parent.parent
spec = importlib.util.spec_from_file_location("superjev", SKILL / "superjev.py")
sj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sj)

FIXTURE_PATH = REPO_ROOT / "test" / "enhance" / "fixtures" / "evidence-guard-cases.json"
fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_present():
    assert FIXTURE_PATH.exists(), "shared TS/Python evidence-guard fixture is missing"
    assert fixture["blockedPaths"]


def test_blocked_paths():
    for c in fixture["blockedPaths"]:
        assert sj.is_blocked_path(c["path"]) == c["blocked"], c


def test_allow_override():
    for c in fixture["allowOverride"]:
        assert sj.is_blocked_path(c["path"], c["allow"]) == c["blocked"], c


def test_redactions():
    for c in fixture["redactions"]:
        out, count, _by_kind = sj.redact_counted(c["text"])
        assert out == c["expect"], c
        assert count == c["count"], c


def test_redactions_with_emails():
    for c in fixture["redactionsWithEmails"]:
        out, count, _by_kind = sj.redact_counted(c["text"], redact_emails=True)
        assert out == c["expect"], c
        assert count == c["count"], c


def test_blocked_path_fact_is_unprovable_not_missing():
    fact = sj.blocked_path_fact("/Users/example/agents/global/profile/logins.md")
    assert fact["ok"] is False
    assert fact["exists"] is None
    assert fact["blocked"] is True
    assert fact["cmd"] == f"({sj.SKIPPED_FACT}: /Users/example/agents/global/profile/logins.md)"


def test_guard_tally_counts_across_a_call():
    tally = sj.GuardTally()
    tally.check_path("/Users/example/agents/global/profile/logins.md")
    tally.check_path("/Users/example/super-jev/README.md")
    tally.redact("key is sk-abcdefghijklmnopqrstuvwxyz123456 in the log")
    tally.redact("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    assert tally.paths_skipped == 1
    assert tally.redactions == 2
    assert tally.summary() == "guard: 1 path(s) skipped, 2 redaction(s)"


# ---------------------------------------------- wiring: fallback verify gatherer

DUMMY_40_CHAR_TOKEN = "d" * 40  # dummy key-shaped token, never a real secret


def test_gather_local_evidence_skips_blocked_paths_and_redacts_test_output(tmp_path, monkeypatch):
    """A tmp tree with dummy logins.md / .env / x-secret.md, each holding a
    dummy 40-char token: the fallback verify gatherer must never include
    their lines, only report them as skipped."""
    worktree = tmp_path
    (worktree / "logins.md").write_text(f"password=hunter2 token={DUMMY_40_CHAR_TOKEN}\n", encoding="utf-8")
    (worktree / ".env").write_text(f"API_TOKEN={DUMMY_40_CHAR_TOKEN}\n", encoding="utf-8")
    (worktree / "x-secret.md").write_text(f"secret={DUMMY_40_CHAR_TOKEN}\n", encoding="utf-8")
    (worktree / "notes.md").write_text("line one\nline two\n", encoding="utf-8")

    report_text = "See logins.md, .env, x-secret.md and notes.md for details."
    named_paths = ["logins.md", ".env", "x-secret.md", "notes.md"]

    def fake_git_out(args, cwd):
        if args[:2] == ["rev-parse", "--abbrev-ref"] and args[-1] == "HEAD":
            return True, "main\n"
        if "ls-files" in args:
            return False, ""
        return False, ""

    monkeypatch.setattr(sj, "_git_out", fake_git_out)
    monkeypatch.setattr(sj, "_light_atoms", lambda text: (named_paths, [], []))
    monkeypatch.setattr(sj, "_gh_pr_evidence", lambda *a, **k: None)

    guard = sj.GuardTally()
    evidence = sj._gather_local_evidence(report_text, str(worktree), "", guard=guard)

    lengths = evidence.get("lengths", [])
    blocked_paths = {l["path"] for l in lengths if l.get("blocked")}
    assert blocked_paths == {"logins.md", ".env", "x-secret.md"}, lengths
    for l in lengths:
        if l.get("blocked"):
            assert l["ok"] is False and l["exists"] is None  # unprovable, never FALSE

    kept = [l for l in lengths if l.get("path") == "notes.md"]
    assert kept and kept[0]["exists"] is True and kept[0]["lines"] == 2

    # the dummy token never appears anywhere in the gathered evidence
    assert DUMMY_40_CHAR_TOKEN not in json.dumps(evidence)
    assert guard.paths_skipped == 3


def test_gather_local_evidence_redacts_test_command_output(tmp_path, monkeypatch):
    monkeypatch.setattr(sj, "_light_atoms", lambda text: ([], [], []))
    monkeypatch.setattr(sj, "_gh_pr_evidence", lambda *a, **k: None)
    monkeypatch.setattr(sj, "check_test_cmd_for_fallback", lambda cmd, worktree: None)

    class FakeProc:
        stdout = "leaked key sk-abcdefghijklmnopqrstuvwxyz123456 in test output\n"
        stderr = ""

    monkeypatch.setattr(sj.subprocess, "run", lambda *a, **k: FakeProc())

    guard = sj.GuardTally()
    evidence = sj._gather_local_evidence("report", None, "echo hi", guard=guard)
    assert "[REDACTED:openai-key]" in evidence["tests"]["output"]
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in evidence["tests"]["output"]
    assert guard.redactions >= 1


# ---------------------------------------------------- wiring: gate window builder

def test_compose_window_with_facts_redacts_the_whole_window(monkeypatch):
    monkeypatch.setattr(sj, "_derived_facts_enabled", lambda: False)
    window_text = "some evidence with a key sk-abcdefghijklmnopqrstuvwxyz123456 in it"
    text, _facts, meta = sj.compose_window_with_facts(window_text, "draft")
    assert "[REDACTED:openai-key]" in text
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in text
    assert meta["guard"]["redactions"] >= 1


def test_compose_window_with_facts_preserves_a_git_sha(monkeypatch):
    monkeypatch.setattr(sj, "_derived_facts_enabled", lambda: False)
    window_text = "commit 523e007fdeadbeef1234567890abcdef1234567 fixed it"
    text, _facts, meta = sj.compose_window_with_facts(window_text, "draft")
    assert "523e007fdeadbeef1234567890abcdef1234567" in text
    assert meta["guard"]["redactions"] == 0
