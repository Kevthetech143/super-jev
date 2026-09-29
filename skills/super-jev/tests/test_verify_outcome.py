#!/usr/bin/env python3
"""Offline tests for the verify (work check) outcome contract. No network, no key, no Jev.

The door is faked; what is asserted is the one VERDICT line (first line of the
reply) and the exit code the wrapper derives from what the door printed.

    python3 -m pytest skills/super-jev/tests/test_verify_outcome.py -q
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
FAKE_DOOR = Path(__file__).resolve().parent / "fake_door.py"
spec = importlib.util.spec_from_file_location("superjev_vo", SKILL / "superjev.py")
sj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sj)

FACT_ROW = ("DETERMINISTIC PRE-RULES (settled without any model call):\n"
            "  c1   CONTRADICTED_BY_FACT 1.00  the file it names does not exist\n")
NO_FACT = ("DETERMINISTIC PRE-RULES (settled without any model call):\n"
           "  (none - no derived fact flatly contradicts any claim)\n")
CLEAN_TABLE = ("  c1   SUPPORTED            0.95  tests pass\n"
               "EVIDENCE HEALTH: healthy\n")


class Door:
    def __init__(self, code=0, stdout=""):
        self.code, self.stdout, self.calls = code, stdout, []

    def __call__(self, cmd, **kw):
        self.calls.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, self.code, stdout=self.stdout, stderr="")


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(sj, "LEDGER_PATH", tmp_path / "ledger" / "calls.jsonl")
    monkeypatch.setattr(sj, "CATCH_LEDGER_PATH", tmp_path / "ledger" / "catches.jsonl")
    monkeypatch.setattr(sj, "FLEET_VERIFY_PY", FAKE_DOOR)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


@pytest.fixture
def report(tmp_path):
    f = tmp_path / "r.md"
    f.write_text("COMPLETE. Edited a.py, tests pass.", encoding="utf-8")
    return str(f)


def run(monkeypatch, capsys, report, code, stdout, *extra):
    monkeypatch.setattr(sj.subprocess, "run", Door(code, stdout))
    got = sj.main(["verify", report, *extra])
    return got, capsys.readouterr()


def first_line(out):
    return out.strip().splitlines()[0]


def test_clean_is_first_line_and_only_verdict(monkeypatch, capsys, report):
    got, cap = run(monkeypatch, capsys, report, 0, CLEAN_TABLE)
    assert got == 0
    assert first_line(cap.out).startswith("VERDICT: CLEAN")
    assert cap.out.count("VERDICT:") == 1
    assert "SUPPORTED" in cap.out  # the door's table is still shown, below the verdict


def test_dry_run_with_fact_contradiction_is_reject_not_clean(monkeypatch, capsys, report):
    got, cap = run(monkeypatch, capsys, report, 0, FACT_ROW, "--dry-run")
    assert got == 4
    assert first_line(cap.out).startswith("VERDICT: REJECT")
    assert "CLEAN" not in cap.out


def test_dry_run_without_contradiction_gives_no_verdict(monkeypatch, capsys, report):
    got, cap = run(monkeypatch, capsys, report, 0, NO_FACT, "--dry-run")
    assert got == 0
    assert first_line(cap.out).startswith("VERDICT: DRY RUN - NO VERDICT")
    assert "CLEAN" not in cap.out


def test_real_run_exit_0_with_fact_row_is_reject(monkeypatch, capsys, report):
    got, cap = run(monkeypatch, capsys, report, 0, FACT_ROW + CLEAN_TABLE)
    assert got == 4
    assert first_line(cap.out).startswith("VERDICT: REJECT")


@pytest.mark.parametrize("door_code", [0, 3, 4])
def test_refused_worktree_is_its_own_error_outcome(monkeypatch, capsys, report, door_code):
    out = "worktree refused: outside-allowlist-root\n" + CLEAN_TABLE
    got, cap = run(monkeypatch, capsys, report, door_code, out, "--worktree", "/etc")
    assert got == sj.WORKTREE_REFUSED
    assert got not in (0, 2, 3, 4, sj.REFUSED, sj.NOT_BUILT)
    line = first_line(cap.out)
    assert line.startswith("VERDICT: WORKTREE REFUSED")
    assert "outside-allowlist-root" in line
    assert "CLEAN" not in line


def test_refused_evidence_downgrades_clean_to_read(monkeypatch, capsys, report):
    out = (CLEAN_TABLE + "EVIDENCE INVENTORY BY SOURCE:\n"
           "  TEST_OUTPUT  NOT GATHERED (refused: a directory-level pytest)\n")
    got, cap = run(monkeypatch, capsys, report, 0, out)
    assert got == 3
    assert first_line(cap.out).startswith("VERDICT: READ")


def test_json_mode_matches_the_text_outcome(monkeypatch, capsys, report):
    got, cap = run(monkeypatch, capsys, report, 0, FACT_ROW, "--dry-run", "--json")
    obj = json.loads(cap.out)
    assert got == 4 and obj["exit_code"] == 4 and obj["verdict"] == "REJECT"
    got, cap = run(monkeypatch, capsys, report, 0, "worktree refused: not-a-directory\n",
                   "--worktree", "/nope", "--json")
    obj = json.loads(cap.out)
    assert got == sj.WORKTREE_REFUSED and obj["exit_code"] == got
    assert obj["verdict"] == "WORKTREE_REFUSED"


def test_missing_door_is_needs_setup_exit_6(monkeypatch, capsys, tmp_path, report):
    monkeypatch.setattr(sj, "FLEET_VERIFY_PY", tmp_path / "nope.py")
    monkeypatch.delenv(sj.VERIFY_CMD_ENV, raising=False)
    got, cap = run(monkeypatch, capsys, report, 0, CLEAN_TABLE)
    assert got == sj.NOT_BUILT == 6
    assert first_line(cap.out).startswith("VERDICT: NEEDS SETUP")
    assert "no door at" in cap.err


def test_missing_door_in_hook_mode_returns_the_three_tuple(monkeypatch, tmp_path, report):
    monkeypatch.setattr(sj, "FLEET_VERIFY_PY", tmp_path / "nope.py")
    monkeypatch.delenv(sj.VERIFY_CMD_ENV, raising=False)
    ns = sj.argparse.Namespace(report=report, worktree=None, test_cmd="", paths=[],
                               dry_run=False, json=False, hook_mode=True)
    code, out, err = sj.cmd_verify(ns)
    assert code == sj.NOT_BUILT and out == "" and "no door at" in err


def test_the_duplicate_fallback_gatherer_is_gone():
    for name in ("_derived_facts_fallback", "_gather_local_evidence", "_light_atoms",
                 "_gh_pr_evidence", "check_test_cmd_for_fallback", "DERIVE_FACTS_CLI"):
        assert not hasattr(sj, name), name
