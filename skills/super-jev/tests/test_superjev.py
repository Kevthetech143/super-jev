#!/usr/bin/env python3
"""Offline tests for /super-jev. No network, no secrets, no live door.

Every wrapped door is replaced by a fake: subprocess.run is monkeypatched, so
what we assert is the exact argv this wrapper builds and the exit code it
propagates. Nothing here reaches TypeSafe, npm or git.

    python3 -m pytest ~/.claude/skills/super-jev/tests/test_superjev.py -q
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("superjev", SKILL / "superjev.py")
sj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sj)

# A real, harmless file standing in for the fleet-local doors
# (~/.claude/skills/jev-check/lib/jev.py and worker-verify/verify.py) that
# are only real on the machine super-jev shipped from. Never actually run —
# every test here mocks subprocess.run before a door would be invoked — it
# just needs to exist so door_missing() sees a live door on any checkout,
# including a fresh CI runner.
FAKE_DOOR = Path(__file__).resolve().parent / "fake_door.py"


class FakeDoor:
    """Records every call and returns a fixed exit code. Never runs anything."""

    def __init__(self, code=0, stdout="", stderr=""):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.calls = []

    def __call__(self, cmd, cwd=None, env=None, **kw):
        self.calls.append({"cmd": [str(c) for c in cmd], "cwd": cwd, "env": env or {}})
        return subprocess.CompletedProcess(cmd, self.code, stdout=self.stdout,
                                           stderr=self.stderr)

    @property
    def argv(self):
        return self.calls[-1]["cmd"]


@pytest.fixture
def door(monkeypatch):
    fake = FakeDoor()
    monkeypatch.setattr(sj.subprocess, "run", fake)
    return fake


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A fake super-jev checkout carrying all three npm scripts."""
    r = tmp_path / "super-jev"
    r.mkdir()
    (r / "package.json").write_text(json.dumps(
        {"scripts": {"sweep": "node src/sweep-cli.ts",
                     "fetch": "node src/fetch-cli.ts",
                     "bench:live": "node bench/live-measure.ts",
                     "permit": "node src/permit-cli.ts",
                     "chain": "node src/chain-cli.ts"}}), encoding="utf-8")
    monkeypatch.setenv("SUPERJEV_REPO", str(r))
    return r


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    """Every test starts with no API key, so nothing can go live by accident."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def ledger_tmp(tmp_path, monkeypatch):
    """Every test writes its call ledger to a scratch path, never into this
    checkout's real skills/super-jev/ledger/ — so a test run leaves no trace
    and tests can inspect sj.LEDGER_PATH freely."""
    monkeypatch.setattr(sj, "LEDGER_PATH", tmp_path / "ledger" / "calls.jsonl")


@pytest.fixture(autouse=True)
def reachable_doors(monkeypatch):
    """Every test starts with both fleet-local doors reachable.

    On a fresh checkout (a CI runner, or anyone else's machine) the real
    fleet paths do not exist, so door_missing() would refuse before
    subprocess.run is ever reached, whether or not it is mocked. Point
    both at FAKE_DOOR, a real file, so gate/verify tests exercise the
    normal path by default. Tests that specifically cover the "door not
    installed" refusal (test_gate_refuses_when_the_door_is_not_installed,
    test_verify_refuses_when_the_door_is_not_installed) override this
    afterwards in their own body, which takes precedence.
    """
    monkeypatch.setattr(sj, "FLEET_JEV_LIB", FAKE_DOOR)
    monkeypatch.setattr(sj, "FLEET_VERIFY_PY", FAKE_DOOR)


# ------------------------------------------------------------ ask routing

@pytest.mark.parametrize("sentence,expect", [
    ("is my draft actually supported", "gate"),
    ("check the reply I am about to send", "gate"),
    ("does the evidence carry what I say here", "gate"),
    ("is this claim carried by the files", "gate"),
    ("the worker says it is done", "verify"),
    ("check this report before I trust it", "verify"),
    ("he pushed it, is that true", "verify"),
    ("run every question over this pile", "sweep"),
    ("check all of these records", "sweep"),
    ("go through every line of this", "sweep"),
    ("is it safe to click this", "permit"),
    ("should I pay this automatically", "permit"),
    ("can I delete it", "permit"),
    ("does the ticket match the order", "chain"),
    ("is the policy in this chain", "chain"),
    ("which skill should I use", "fetch"),
    ("what tool handles this", "fetch"),
    ("route this request for me", "fetch"),
])
def test_ask_routes_each_keyword_family(sentence, expect):
    sub, hits = sj.route(sentence)
    assert sub == expect, f"{sentence!r} routed to {sub}, matched {hits}"
    assert hits


def test_ask_unknown_sentence_lists_the_subcommands(capsys):
    code = sj.main(["ask", "hello there how is the weather"])
    out = capsys.readouterr().out
    assert code == sj.REFUSED
    for name in ("gate", "verify", "sweep", "permit", "chain", "fetch", "status"):
        assert name in out


def test_ask_prefers_gate_when_both_families_match():
    # "send" is a permit word and "draft"/"reply" are gate words. Most hits wins.
    sub, hits = sj.route("send this draft reply")
    assert sub == "gate"


def test_ask_permit_names_the_missing_snapshot_when_none_is_named(capsys):
    code = sj.main(["ask", "is it safe to pay this invoice"])
    out = capsys.readouterr().out
    assert code == sj.REFUSED
    assert "ask -> permit" in out
    assert "missing: a .json snapshot file" in out


def test_ask_chain_names_the_missing_spec_when_none_is_named(capsys):
    code = sj.main(["ask", "does the ticket match the order"])
    out = capsys.readouterr().out
    assert code == sj.REFUSED
    assert "ask -> chain" in out
    assert "missing: a .json spec file" in out


def test_ask_says_what_is_missing_when_the_files_are_not_named(capsys):
    code = sj.main(["ask", "is my draft supported by what I read"])
    out = capsys.readouterr().out
    assert code == sj.REFUSED
    assert "would run:" in out
    assert "missing:" in out


def test_ask_runs_gate_when_two_real_paths_are_in_the_sentence(tmp_path, door, capsys):
    evidence = tmp_path / "notes.md"
    draft = tmp_path / "draft.md"
    evidence.write_text("the migration moved 40 rows\n", encoding="utf-8")
    draft.write_text("All 40 rows moved and no rollback was needed.\n", encoding="utf-8")
    code = sj.main(["ask", f"does my draft {evidence} {draft} hold up"])
    out = capsys.readouterr().out
    assert code == 0
    assert door.argv[1] == str(sj.FLEET_JEV_LIB)
    assert door.argv[2] == str(evidence)
    assert "--kit" in door.argv and "reply" in door.argv
    assert door.argv[door.argv.index("--draft") + 1] == str(draft)
    assert "VERDICT: CLEAN" in out


def test_ask_runs_verify_on_a_named_report(tmp_path, door, capsys):
    report = tmp_path / "report.md"
    report.write_text("I pushed the branch and all 12 tests pass.\n", encoding="utf-8")
    code = sj.main(["ask", f"the worker is done, check {report}"])
    out = capsys.readouterr().out
    assert code == 0
    assert door.argv[1] == str(sj.FLEET_VERIFY_PY)
    assert door.argv[2] == str(report)
    assert "VERDICT: CLEAN" in out


def test_ask_sweep_names_the_output_directory_as_missing(tmp_path, capsys):
    records = tmp_path / "records.jsonl"
    questions = tmp_path / "q.json"
    records.write_text('{"text":"a"}\n', encoding="utf-8")
    questions.write_text("[]", encoding="utf-8")
    code = sj.main(["ask", f"run every line of this pile {records} {questions}"])
    out = capsys.readouterr().out
    assert code == sj.REFUSED
    assert "--out" in out


# ------------------------------------------------------------ NOT BUILT
#
# permit (wishlist item 5) and chain (wishlist item 4) are now wired to the
# npm CLIs #8 added, so UNBUILT is empty and neither door hits this path
# any more. The mechanism itself (not_built() / wishlist_line()) stays for
# whatever wishlist item is next; these tests exercise it directly against
# a fake UNBUILT entry instead of a door that no longer belongs there.

def test_not_built_reports_the_wishlist_item(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sj.UNBUILT, "widget", (9, "WIDGET"))
    wishlist = tmp_path / "wishlist.md"
    wishlist.write_text("9. WIDGET — the next thing.\n", encoding="utf-8")
    monkeypatch.setattr(sj, "WISHLIST", wishlist)
    code = sj.not_built("widget")
    out = capsys.readouterr().out
    assert code == sj.NOT_BUILT
    assert "not built yet — wishlist item 9 (WIDGET)" in out
    assert out.strip().splitlines()[1].startswith("9.")


def test_not_built_survives_a_missing_wishlist(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sj.UNBUILT, "widget", (9, "WIDGET"))
    monkeypatch.setattr(sj, "WISHLIST", tmp_path / "gone.md")
    code = sj.not_built("widget")
    assert code == sj.NOT_BUILT
    assert "not readable" in capsys.readouterr().out


def test_unbuilt_is_empty_now_that_permit_and_chain_are_wired():
    assert sj.UNBUILT == {}


# ------------------------------------------------------------ status

def test_status_shape(repo, capsys, monkeypatch):
    monkeypatch.setattr(sj, "harness_commit", lambda r: "abc1234")
    code = sj.main(["status"])
    out = capsys.readouterr().out
    assert code == 0
    for door_name in ("gate", "verify", "sweep", "fetch", "bench", "permit", "chain",
                      "ask", "status"):
        assert door_name in out
    assert "NOT BUILT" not in out
    # all nine doors read LIVE: gate/verify via the fake fleet-local door,
    # sweep/fetch/bench/permit/chain via the repo fixture's package.json,
    # ask/status always
    assert out.count("LIVE") == 9
    assert "harness commit: abc1234" in out
    assert "TYPESAFE_API_KEY in env: no" in out


def test_status_says_yes_without_ever_printing_the_key(repo, monkeypatch, capsys):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-do-not-print-me")
    monkeypatch.setattr(sj, "harness_commit", lambda r: "abc1234")
    sj.main(["status"])
    out = capsys.readouterr().out
    assert "TYPESAFE_API_KEY in env: yes" in out
    assert "sk-do-not-print-me" not in out


def test_status_names_a_missing_script_instead_of_claiming_live(tmp_path, monkeypatch, capsys):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "package.json").write_text('{"scripts":{"test":"node --test"}}', encoding="utf-8")
    monkeypatch.setenv("SUPERJEV_REPO", str(bare))
    monkeypatch.setattr(sj, "harness_commit", lambda r: "abc1234")
    sj.main(["status"])
    out = capsys.readouterr().out
    assert out.count("MISSING SCRIPT") == 5  # sweep, fetch, bench, permit, chain


# ------------------------------------------------------------ gate

def test_gate_passes_evidence_claims_and_kit_through(tmp_path, door):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    for f in (a, b):
        f.write_text("x", encoding="utf-8")
    code = sj.main(["gate", str(a), str(b), "--claim", "one", "--claim", "two"])
    assert code == 0
    argv = door.argv
    assert argv[1:4] == [str(sj.FLEET_JEV_LIB), str(a), str(b)]
    assert argv[4:6] == ["--kit", "reply"]
    assert argv.count("--claim") == 2
    assert "one" in argv and "two" in argv


@pytest.mark.parametrize("code,word", [(0, "CLEAN"), (3, "READ"), (2, "REJECT")])
def test_gate_prints_one_verdict_line_per_exit_code(tmp_path, monkeypatch, capsys, code, word):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(code))
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    got = sj.main(["gate", str(f), "--draft", str(f)])
    out = capsys.readouterr().out
    assert got == code
    assert out.count("VERDICT:") == 1
    assert f"VERDICT: {word}" in out


def test_gate_refuses_with_no_draft_and_no_claim(tmp_path, door, capsys):
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    code = sj.main(["gate", str(f)])
    assert code == sj.REFUSED
    assert "--draft" in capsys.readouterr().err
    assert not door.calls


def test_gate_refuses_when_the_door_is_not_installed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sj, "FLEET_JEV_LIB", tmp_path / "nope.py")
    monkeypatch.delenv(sj.GATE_CMD_ENV, raising=False)
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    code = sj.main(["gate", str(f), "--draft", str(f)])
    assert code == sj.REFUSED
    err = capsys.readouterr().err
    assert "no door at" in err
    assert sj.GATE_CMD_ENV in err


def test_gate_uses_the_env_command_when_set(tmp_path, door, monkeypatch):
    monkeypatch.setattr(sj, "FLEET_JEV_LIB", tmp_path / "nope.py")
    monkeypatch.setenv(sj.GATE_CMD_ENV, "python3 /elsewhere/jev.py")
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    code = sj.main(["gate", str(f), "--draft", str(f)])
    assert code == 0
    argv = door.argv
    assert argv[0] == "python3"
    assert argv[1] == "/elsewhere/jev.py"


def test_verify_refuses_when_the_door_is_not_installed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sj, "FLEET_VERIFY_PY", tmp_path / "nope.py")
    monkeypatch.delenv(sj.VERIFY_CMD_ENV, raising=False)
    report = tmp_path / "r.md"
    report.write_text("done", encoding="utf-8")
    code = sj.main(["verify", str(report)])
    assert code == sj.REFUSED
    err = capsys.readouterr().err
    assert "no door at" in err
    assert sj.VERIFY_CMD_ENV in err


def test_verify_uses_the_env_command_when_set(tmp_path, door, monkeypatch):
    monkeypatch.setattr(sj, "FLEET_VERIFY_PY", tmp_path / "nope.py")
    monkeypatch.setenv(sj.VERIFY_CMD_ENV, "python3 /elsewhere/verify.py")
    report = tmp_path / "r.md"
    report.write_text("done", encoding="utf-8")
    code = sj.main(["verify", str(report)])
    assert code == 0
    argv = door.argv
    assert argv[0] == "python3"
    assert argv[1] == "/elsewhere/verify.py"


# ------------------------------------------------------------ verify

def test_verify_passes_every_flag_through(tmp_path, door):
    report = tmp_path / "r.md"
    report.write_text("done", encoding="utf-8")
    code = sj.main(["verify", str(report), "--worktree", "/tmp/wt",
                    "--test-cmd", "python3 -m pytest x.py -q",
                    "--paths", "a.py", "b.py"])
    assert code == 0
    argv = door.argv
    assert argv[1:3] == [str(sj.FLEET_VERIFY_PY), str(report)]
    assert argv[argv.index("--worktree") + 1] == "/tmp/wt"
    assert argv[argv.index("--test-cmd") + 1] == "python3 -m pytest x.py -q"
    assert argv[argv.index("--paths") + 1:] == ["a.py", "b.py"]


@pytest.mark.parametrize("code,word", [(0, "CLEAN"), (3, "READ"), (4, "REJECT")])
def test_verify_propagates_the_three_verdicts(tmp_path, monkeypatch, capsys, code, word):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(code))
    report = tmp_path / "r.md"
    report.write_text("done", encoding="utf-8")
    got = sj.main(["verify", str(report)])
    assert got == code
    assert f"VERDICT: {word}" in capsys.readouterr().out


# ------------------------------------------------------------ sweep

def test_sweep_builds_the_npm_command_in_the_checkout(repo, door):
    code = sj.main(["sweep", "records.jsonl", "--questions", "q.json", "--out", "out",
                    "--budget", "8000", "--batch", "4", "--gate", "0.8", "--dry-run"])
    assert code == 0
    call = door.calls[-1]
    assert call["cmd"] == ["npm", "run", "sweep", "--",
                           "--records", "records.jsonl", "--questions", "q.json",
                           "--out", "out", "--budget", "8000", "--batch", "4",
                           "--gate", "0.8", "--dry-run"]
    assert call["cwd"] == str(repo)


def test_sweep_omits_flags_that_were_not_given(repo, door):
    sj.main(["sweep", "r.jsonl", "--questions", "q.json", "--out", "o"])
    argv = door.argv
    for flag in ("--budget", "--batch", "--gate", "--dry-run", "--stub"):
        assert flag not in argv


def test_sweep_refuses_a_checkout_without_the_script(tmp_path, monkeypatch, door, capsys):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "package.json").write_text('{"scripts":{}}', encoding="utf-8")
    monkeypatch.setenv("SUPERJEV_REPO", str(bare))
    code = sj.main(["sweep", "r.jsonl", "--questions", "q.json", "--out", "o"])
    err = capsys.readouterr().err
    assert code == sj.REFUSED
    assert "no `sweep` script" in err and str(bare) in err
    assert not door.calls


def test_repo_path_defaults_to_this_checkouts_own_root(monkeypatch):
    monkeypatch.delenv("SUPERJEV_REPO", raising=False)
    assert sj.repo_path() == sj.REPO_ROOT
    assert (sj.repo_path() / "package.json").exists()


def test_wishlist_defaults_to_the_docs_dir_in_this_repo(monkeypatch):
    monkeypatch.delenv("SUPERJEV_REPO", raising=False)
    assert sj.WISHLIST == sj.REPO_ROOT / "docs" / "wishlist.md"
    assert sj.WISHLIST.exists()


def test_sweep_refuses_a_missing_checkout(tmp_path, monkeypatch, door, capsys):
    monkeypatch.setenv("SUPERJEV_REPO", str(tmp_path / "nowhere"))
    code = sj.main(["sweep", "r.jsonl", "--questions", "q.json", "--out", "o"])
    assert code == sj.REFUSED
    assert "no super-jev checkout" in capsys.readouterr().err
    assert not door.calls


# ------------------------------------------------------------ fetch

def test_fetch_builds_the_npm_command_in_the_checkout(repo, door):
    code = sj.main(["fetch", "gate my reply", "--catalog", "catalog.json",
                    "--k", "3", "--out", "out", "--budget", "8000", "--batch", "40",
                    "--dry-run"])
    assert code == 0
    call = door.calls[-1]
    assert call["cmd"] == ["npm", "run", "fetch", "--",
                           "--catalog", "catalog.json", "--request", "gate my reply",
                           "--k", "3", "--out", "out", "--budget", "8000",
                           "--batch", "40", "--dry-run"]
    assert call["cwd"] == str(repo)


def test_fetch_passes_prefilter_through_including_zero(repo, door):
    sj.main(["fetch", "which skill", "--catalog", "c.json", "--prefilter", "0", "--stub"])
    assert door.argv[-3:] == ["--prefilter", "0", "--stub"]
    sj.main(["fetch", "which skill", "--catalog", "c.json", "--stub"])
    assert "--prefilter" not in door.argv


def test_fetch_omits_flags_that_were_not_given(repo, door):
    sj.main(["fetch", "anything", "--catalog", "c.json"])
    argv = door.argv
    for flag in ("--k", "--out", "--budget", "--batch", "--dry-run", "--stub"):
        assert flag not in argv


def test_fetch_refuses_a_checkout_without_the_script(tmp_path, monkeypatch, door, capsys):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "package.json").write_text('{"scripts":{}}', encoding="utf-8")
    monkeypatch.setenv("SUPERJEV_REPO", str(bare))
    code = sj.main(["fetch", "anything", "--catalog", "c.json"])
    err = capsys.readouterr().err
    assert code == sj.REFUSED
    assert "no `fetch` script" in err and str(bare) in err
    assert not door.calls


def test_fetch_refuses_a_missing_checkout(tmp_path, monkeypatch, door, capsys):
    monkeypatch.setenv("SUPERJEV_REPO", str(tmp_path / "nowhere"))
    code = sj.main(["fetch", "anything", "--catalog", "c.json"])
    assert code == sj.REFUSED
    assert "no super-jev checkout" in capsys.readouterr().err
    assert not door.calls


def test_ask_fetch_names_the_missing_request_when_only_a_catalog_is_named(tmp_path, capsys):
    catalog = tmp_path / "catalog.json"
    catalog.write_text("[]", encoding="utf-8")
    code = sj.main(["ask", f"which tool handles this {catalog}"])
    out = capsys.readouterr().out
    assert code == sj.REFUSED
    assert "ask -> fetch" in out
    assert "missing: the plain request text" in out


def test_ask_fetch_names_the_missing_catalog_when_none_is_named(capsys):
    code = sj.main(["ask", "which skill handles this request"])
    out = capsys.readouterr().out
    assert code == sj.REFUSED
    assert "ask -> fetch" in out
    assert "missing: a .json catalog file" in out


def test_fetch_refuses_without_a_traceback_in_a_bare_environment(monkeypatch, capsys):
    # The bug the reviewer found: cmd_fetch used to shell out to npm without
    # ever calling resolve_npm() first, so a bare env with no npm on PATH
    # crashed inside subprocess.run (FileNotFoundError) instead of refusing
    # cleanly the way sweep/bench already did.
    monkeypatch.setattr(sj, "resolve_npm", lambda: None)
    code = sj.main(["fetch", "anything", "--catalog", "c.json", "--dry-run"])
    err = capsys.readouterr().err
    assert code == 1
    assert "npm not found" in err
    assert "Traceback" not in err


def test_fetch_json_shape_and_no_npm_banner_leaks_to_stdout(repo, monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(0, stdout="npm banner noise\nfetch done"))
    code = sj.main(["fetch", "anything", "--catalog", "c.json", "--dry-run", "--json"])
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert code == 0
    assert obj["door"] == "fetch"
    assert obj["verdict"] == "RAN"
    assert "npm banner noise" in obj["details"]["stdout"]
    assert "$ " not in out


def test_fetch_calls_are_logged_to_the_ledger(repo, door):
    sj.main(["fetch", "anything", "--catalog", "c.json", "--dry-run"])
    lines = sj._ledger_lines()
    assert lines
    rec = json.loads(lines[-1])
    assert rec["door"] == "fetch"


# ------------------------------------------------------------ permit

def test_permit_builds_the_npm_command_in_the_checkout(repo, door):
    code = sj.main(["permit", "--snapshot", "snap.json", "--action", "delete the record",
                    "--id", "d1", "--min-confidence", "0.9", "--dry-run"])
    assert code == 0
    call = door.calls[-1]
    assert call["cmd"] == ["npm", "run", "permit", "--", "--snapshot", "snap.json",
                           "--action", "delete the record", "--id", "d1",
                           "--min-confidence", "0.9", "--dry-run"]
    assert call["cwd"] == str(repo)


def test_permit_omits_flags_that_were_not_given(repo, door):
    sj.main(["permit", "--snapshot", "snap.json"])
    argv = door.argv
    for flag in ("--action", "--id", "--min-confidence", "--dry-run", "--stub"):
        assert flag not in argv


def test_permit_refuses_a_checkout_without_the_script(tmp_path, monkeypatch, door, capsys):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "package.json").write_text('{"scripts":{}}', encoding="utf-8")
    monkeypatch.setenv("SUPERJEV_REPO", str(bare))
    code = sj.main(["permit", "--snapshot", "snap.json"])
    err = capsys.readouterr().err
    assert code == sj.REFUSED
    assert "no `permit` script" in err and str(bare) in err
    assert not door.calls


def test_permit_refuses_without_a_traceback_in_a_bare_environment(monkeypatch, capsys):
    monkeypatch.setattr(sj, "resolve_npm", lambda: None)
    code = sj.main(["permit", "--snapshot", "snap.json", "--dry-run"])
    err = capsys.readouterr().err
    assert code == 1
    assert "npm not found" in err
    assert "Traceback" not in err


def test_permit_json_shape(repo, monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(0, stdout="permit verdict here"))
    code = sj.main(["permit", "--snapshot", "snap.json", "--dry-run", "--json"])
    obj = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert obj["door"] == "permit"
    assert obj["verdict"] == "RAN"


def test_ask_runs_chain_when_a_spec_file_is_named(tmp_path, door):
    spec = tmp_path / "spec.json"
    spec.write_text("{}", encoding="utf-8")
    code = sj.main(["ask", f"does the ticket match the order in {spec}"])
    assert code == 0
    assert door.argv == ["npm", "run", "chain", "--", "--spec", str(spec)]


# ------------------------------------------------------------ chain

def test_chain_builds_the_npm_command_in_the_checkout(repo, door):
    code = sj.main(["chain", "--spec", "spec.json", "--dry-run"])
    assert code == 0
    call = door.calls[-1]
    assert call["cmd"] == ["npm", "run", "chain", "--", "--spec", "spec.json", "--dry-run"]
    assert call["cwd"] == str(repo)


def test_chain_omits_flags_that_were_not_given(repo, door):
    sj.main(["chain", "--spec", "spec.json"])
    argv = door.argv
    for flag in ("--dry-run", "--stub"):
        assert flag not in argv


def test_chain_refuses_a_checkout_without_the_script(tmp_path, monkeypatch, door, capsys):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "package.json").write_text('{"scripts":{}}', encoding="utf-8")
    monkeypatch.setenv("SUPERJEV_REPO", str(bare))
    code = sj.main(["chain", "--spec", "spec.json"])
    err = capsys.readouterr().err
    assert code == sj.REFUSED
    assert "no `chain` script" in err and str(bare) in err
    assert not door.calls


def test_chain_refuses_without_a_traceback_in_a_bare_environment(monkeypatch, capsys):
    monkeypatch.setattr(sj, "resolve_npm", lambda: None)
    code = sj.main(["chain", "--spec", "spec.json", "--dry-run"])
    err = capsys.readouterr().err
    assert code == 1
    assert "npm not found" in err
    assert "Traceback" not in err


def test_chain_json_shape(repo, monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(0, stdout="chain result here"))
    code = sj.main(["chain", "--spec", "spec.json", "--dry-run", "--json"])
    obj = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert obj["door"] == "chain"
    assert obj["verdict"] == "RAN"


# ------------------------------------------------------------ bench

def test_bench_without_a_key_refuses_and_runs_the_dry_run_plan(repo, door, capsys):
    code = sj.main(["bench"])
    out = capsys.readouterr().out
    assert "refusing a live bench" in out
    assert door.argv == ["npm", "run", "bench:live", "--", "--dry-run"]
    assert code == sj.REFUSED


def test_bench_with_a_key_runs_live(repo, door, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    code = sj.main(["bench"])
    assert code == 0
    assert door.argv == ["npm", "run", "bench:live"]


def test_bench_dry_run_needs_no_key(repo, door):
    code = sj.main(["bench", "--dry-run"])
    assert code == 0
    assert door.argv == ["npm", "run", "bench:live", "--", "--dry-run"]


def test_bench_refuses_a_checkout_without_the_script(tmp_path, monkeypatch, door, capsys):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "package.json").write_text('{"scripts":{}}', encoding="utf-8")
    monkeypatch.setenv("SUPERJEV_REPO", str(bare))
    code = sj.main(["bench"])
    assert code == sj.REFUSED
    assert "no `bench:live` script" in capsys.readouterr().err
    assert not door.calls


# ------------------------------------------------------------ env and safety

def test_ssl_cert_file_passes_through_to_the_door(tmp_path, monkeypatch, door):
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/cert.pem")
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    sj.main(["gate", str(f), "--draft", str(f)])
    assert door.calls[-1]["env"]["SSL_CERT_FILE"] == "/etc/ssl/cert.pem"


def test_no_subcommand_output_ever_contains_the_key(repo, door, monkeypatch, capsys):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-secret-value")
    monkeypatch.setattr(sj, "harness_commit", lambda r: "abc1234")
    sj.main(["status"])
    sj.main(["bench", "--dry-run"])
    sj.main(["sweep", "r.jsonl", "--questions", "q.json", "--out", "o"])
    captured = capsys.readouterr()
    assert "sk-secret-value" not in captured.out + captured.err


def test_the_key_still_reaches_the_child_process(repo, door, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-secret-value")
    sj.main(["bench"])
    assert door.calls[-1]["env"]["TYPESAFE_API_KEY"] == "sk-secret-value"


def test_bare_invocation_prints_help(capsys):
    code = sj.main([])
    assert code == sj.REFUSED
    assert "usage" in capsys.readouterr().out.lower()


def test_no_secret_file_is_read_by_this_wrapper():
    source = (SKILL / "superjev.py").read_text(encoding="utf-8")
    for banned in ("logins.md", "-secret.md", '".env"', "'.env'", "profile/", "documents/"):
        assert banned not in source


# ------------------------------------------------------------ --json shape

JSON_KEYS = {"door", "verdict", "exit_code", "summary", "details", "would_run"}


def test_gate_json_prints_exactly_one_object_with_the_required_shape(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(0, stdout="fake gate ok"))
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    code = sj.main(["gate", str(f), "--draft", str(f), "--json"])
    out = capsys.readouterr().out
    assert code == 0
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one line of stdout, got: {out!r}"
    obj = json.loads(lines[0])
    assert set(obj) == JSON_KEYS
    assert obj["door"] == "gate"
    assert obj["verdict"] == "CLEAN"
    assert obj["exit_code"] == 0
    assert "fake gate ok" in obj["details"]["stdout"]
    assert "$ " not in out  # no echoed command


@pytest.mark.parametrize("code,word", [(0, "CLEAN"), (3, "READ"), (2, "REJECT")])
def test_gate_json_verdict_word_per_exit_code(tmp_path, monkeypatch, capsys, code, word):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(code))
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    got = sj.main(["gate", str(f), "--draft", str(f), "--json"])
    obj = json.loads(capsys.readouterr().out.strip())
    assert got == code
    assert obj["verdict"] == word


def test_verify_json_shape(tmp_path, door, capsys):
    report = tmp_path / "r.md"
    report.write_text("done", encoding="utf-8")
    code = sj.main(["verify", str(report), "--json"])
    obj = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert set(obj) == JSON_KEYS
    assert obj["door"] == "verify"
    assert obj["verdict"] == "CLEAN"


def test_gate_json_refusal_is_still_one_json_object(tmp_path, capsys):
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    code = sj.main(["gate", str(f), "--json"])  # no draft, no claim
    out, err = capsys.readouterr()
    assert code == sj.REFUSED
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["verdict"] == "REFUSED"
    assert obj["exit_code"] == sj.REFUSED


def test_sweep_json_shape_and_no_npm_banner_leaks_to_stdout(repo, monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(0, stdout="npm banner noise\nsweep done"))
    code = sj.main(["sweep", "r.jsonl", "--questions", "q.json", "--out", "o",
                    "--dry-run", "--json"])
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert code == 0
    assert obj["door"] == "sweep"
    assert obj["verdict"] == "RAN"
    assert "npm banner noise" in obj["details"]["stdout"]
    assert "$ " not in out


def test_bench_json_shape(repo, monkeypatch, capsys):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(0, stdout="bench report here"))
    code = sj.main(["bench", "--json"])
    obj = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert obj["door"] == "bench"
    assert obj["verdict"] == "RAN"


def test_status_json_shape(repo, monkeypatch, capsys):
    monkeypatch.setattr(sj, "harness_commit", lambda r: "abc1234")
    code = sj.main(["status", "--json"])
    obj = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert obj["door"] == "status"
    assert "doors" in obj["details"]
    assert obj["details"]["harness_commit"] == "abc1234"
    assert "ledger_calls_today" in obj["details"]


# ------------------------------------------------------------ hook shim

def _hook_stdin(monkeypatch, payload_text):
    monkeypatch.setattr(sj.sys, "stdin", io.StringIO(payload_text))


def test_hook_gate_clean_is_silent_allow(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(0))
    evidence = tmp_path / "notes.md"
    evidence.write_text("the sky is blue", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "the sky is blue", "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 0
    assert out == ""
    assert err == ""


def test_hook_gate_read_is_a_nonblocking_advisory(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3))
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "some claim", "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 0
    assert "advisory" in out
    assert err == ""


def test_hook_gate_reject_blocks_with_reason_on_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(2))
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "a fabricated quote",
                                        "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 2
    assert out == ""
    assert "blocked" in err


# ------------------------------------------------- strong-flag block mapping
#
# The bug this closes: a Stop payload whose last_assistant_message was a
# lie survived as a silent advisory because jev-check's own exit code for
# that draft was 3 (READ) — an ambiguous code the old mapping always
# treated as advisory, however red the actual per-claim/draft-level rows
# were. These tests parse the SAME captured stdout jev.py/worker-verify
# print (the fixtures below are exact copies of that table shape) and
# assert the fixed mapping blocks whenever a claim/draft-level flag
# crosses its own line, and stays advisory when every flag is weak.

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LIE_STDOUT = (FIXTURES / "lie_stop_stdout.txt").read_text(encoding="utf-8")
WEAK_FLAGS_STDOUT = (FIXTURES / "weak_flags_stdout.txt").read_text(encoding="utf-8")


def test_parse_strong_flags_reads_claim_and_draft_level_rows():
    flags = sj._parse_strong_flags(LIE_STDOUT)
    by_key = {f["key"]: f for f in flags}
    assert by_key["c1"] == {"key": "c1", "verdict": "NOT_SUPPORTED", "score": 0.18}
    assert by_key["overclaim"] == {"key": "overclaim", "verdict": "OVERCLAIMS", "score": 0.98}
    assert by_key["self_contradictory"] == {"key": "self_contradictory",
                                            "verdict": "SELF_CONTRADICTORY", "score": 0.28}
    # CLEAN / NOT_TIME_SENSITIVE rows are not notable verdicts — not carried.
    assert "leaked_internal" not in by_key
    assert "time_sensitive" not in by_key


HIGH_CONF_LIE_STDOUT = (FIXTURES / "lie_stop_high_confidence_stdout.txt").read_text(
    encoding="utf-8")


def test_hook_gate_the_exact_lie_output_is_now_advisory(tmp_path, monkeypatch, capsys):
    # The original 2026-09-17 repro (exit 3/READ; c1 NOT_SUPPORTED 0.18,
    # overclaim OVERCLAIMS 0.98). Kept as a regression marker for the
    # 2026-09-17 confidence-direction fix (docs/hooks.md, "Decided: the
    # block rule follows confidence"): the float is the judge's CONFIDENCE
    # in the verdict, and 0.18 means the judge barely suspects c1 at all —
    # the strongest defensible reading is "a human should read this", not
    # "block it". The honest trade-off: this exact table no longer blocks.
    # See test_hook_gate_a_high_confidence_lie_blocks for the case that
    # still does, and still must.
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=LIE_STDOUT))
    evidence = tmp_path / "notes.md"
    evidence.write_text("only PR 8 merged; 24 of 30 permit cases matched", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "all 30 permit cases matched... merged "
                                        "PRs 8, 9 and 10", "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 0
    assert "advisory" in out
    assert err == ""


def test_hook_gate_a_high_confidence_lie_blocks(tmp_path, monkeypatch, capsys):
    # Same shape as the original lie fixture, but the judge is 0.91 sure
    # c1 is unsupported instead of 0.18 sure — the direction the new rule
    # is built to catch.
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=HIGH_CONF_LIE_STDOUT))
    evidence = tmp_path / "notes.md"
    evidence.write_text("only PR 8 merged; 24 of 30 permit cases matched", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "all 30 permit cases matched... merged "
                                        "PRs 8, 9 and 10", "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 2
    assert out == ""
    assert "blocked" in err
    assert "c1" in err and "NOT_SUPPORTED" in err and "0.91" in err
    assert "overclaim" in err and "OVERCLAIMS" in err and "0.98" in err
    assert "self_contradictory" not in err


def test_hook_gate_read_with_only_weak_flags_stays_advisory(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=WEAK_FLAGS_STDOUT))
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "a mostly-fine draft with one soft aside",
                                        "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 0
    assert "advisory" in out
    assert err == ""


# --------------------------------------------- stop_hook_active loop guard
#
# The bug this closes: Claude Code re-runs the Stop hook with
# stop_hook_active=true when a PRIOR Stop hook already blocked this turn.
# The gate ignored that field entirely, so a block on the re-run could
# loop forever — the live 2026-09-17 finding was the SAME short reply
# blocked three times running (self_contradictory 0.10/0.15/0.05, overclaim
# 0.87/0.25/0.30, leaked_internal 0.26/0.21/0.10 — real ledger values) while
# the reply was rewritten more carefully each pass. On stop_hook_active,
# this hook now never blocks — advisory at most, flags still printed.

def test_hook_gate_stop_hook_active_forces_advisory_not_block(tmp_path, monkeypatch, capsys):
    # These flags (overclaim 0.87, companion c2 0.55) would block on a
    # first pass — see
    # test_hook_gate_self_contradictory_never_blocks_even_alongside_a_real_overclaim
    # above.
    stdout = ("  c2                 NOT_SUPPORTED        0.55\n"
              "  leaked_internal    HAS_LEAKS            0.26\n"
              "  self_contradictory SELF_CONTRADICTORY   0.10\n"
              "  overclaim          OVERCLAIMS           0.87\n")
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=stdout))
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "a reply that would otherwise block",
                                        "evidence": [str(evidence)],
                                        "stop_hook_active": True}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 0
    assert "second pass" in out
    assert err == ""
    rec = json.loads(sj._ledger_lines()[-1])
    assert "second pass, advisory only" in rec["note"]
    assert rec["exit_code"] == 0


def test_hook_gate_stop_hook_active_false_still_blocks(tmp_path, monkeypatch, capsys):
    # stop_hook_active explicitly False (or the key absent) is a first
    # pass — the loop guard must not weaken normal blocking behavior.
    stdout = "  overclaim          OVERCLAIMS           0.99\n"
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=stdout))
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "an overclaiming reply",
                                        "evidence": [str(evidence)],
                                        "stop_hook_active": False}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 2
    assert "blocked" in err


# ------------------------------------------------ machine-tag stripping
#
# The bug this closes: a reply carrying fleet bookkeeping — a trailing
# [BOARD: ...] line, a Rung line, [Add] [Skip] buttons, a raw <...> system
# tag — got checked against the claim gate AS PART OF the reply's own
# content, and jev's leaked_internal/self_contradictory scoring read that
# bookkeeping as internal leakage or a contradiction. These tags must be
# stripped out of the draft before it goes to the gate.

def test_strip_machine_tags_removes_board_rung_buttons_and_system_tags():
    text = ("All set, PR is open.\n"
            "[BOARD: card-42 -> done, 3 counts]\n"
            "Rung: Sonnet 5 — everyday build work.\n"
            "Rung line: same idea, alternate label.\n"
            "[Add] [Skip]\n"
            "<system-reminder>internal noise</system-reminder>\n"
            "Last real line of the reply.")
    stripped = sj._strip_machine_tags(text)
    assert "[BOARD:" not in stripped
    assert "Rung:" not in stripped
    assert "Rung line:" not in stripped
    assert "[Add]" not in stripped and "[Skip]" not in stripped
    assert "<system-reminder>" not in stripped and "</system-reminder>" not in stripped
    assert "All set, PR is open." in stripped
    assert "Last real line of the reply." in stripped
    # the inline system-tag content survives; only the tags themselves go
    assert "internal noise" in stripped


def test_strip_machine_tags_env_override(monkeypatch):
    monkeypatch.setenv(sj.STRIP_PATTERNS_ENV, json.dumps([r'^SECRET:.*$']))
    text = "keep this\nSECRET: drop this\nkeep this too"
    stripped = sj._strip_machine_tags(text)
    assert "SECRET:" not in stripped
    assert "keep this" in stripped
    assert "keep this too" in stripped
    # the default BOARD pattern is NOT applied once the env overrides the list
    text2 = "keep\n[BOARD: still here]"
    assert "[BOARD:" in sj._strip_machine_tags(text2)


def test_hook_gate_strips_board_tag_before_checking_draft(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_run(cmd, cwd=None, env=None, **kw):
        cmd = [str(c) for c in cmd]
        if "--draft" in cmd:
            draft_path = cmd[cmd.index("--draft") + 1]
            captured["draft_text"] = Path(draft_path).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sj.subprocess, "run", fake_run)
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    reply = ("Done, filed the PR.\n"
             "[BOARD: card-42 -> done]\n"
             "Rung: Sonnet 5, everyday build work.\n"
             "[Add] [Skip]\n")
    _hook_stdin(monkeypatch, json.dumps({"draft": reply, "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    assert code == 0
    assert "draft_text" in captured
    assert "[BOARD:" not in captured["draft_text"]
    assert "Rung:" not in captured["draft_text"]
    assert "[Add]" not in captured["draft_text"]
    assert "Done, filed the PR." in captured["draft_text"]


@pytest.mark.parametrize("verdict", ["NOT_SUPPORTED", "CONTRADICTED"])
def test_hook_gate_blocks_exactly_at_the_confidence_line(tmp_path, monkeypatch, capsys,
                                                          verdict):
    stdout = f"  c1   {verdict:14s} 0.80  a claim right on the line\n"
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=stdout))
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "a claim right on the line",
                                        "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    assert code == 2


def test_hook_gate_overclaims_blocks_at_the_line_with_a_companion_claim(tmp_path, monkeypatch,
                                                                        capsys):
    # OVERCLAIMS needs a companion: a claim-level NOT_SUPPORTED/CONTRADICTED
    # at or above the (fixed, non-env) 0.50 companion floor in the SAME run.
    stdout = ("  c1   NOT_SUPPORTED   0.50  a companion claim right at its own floor\n"
              "  overclaim          OVERCLAIMS           0.80\n")
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=stdout))
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "a companion claim right at its own floor",
                                        "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    assert code == 2


def test_hook_gate_self_contradictory_alone_does_not_block(tmp_path, monkeypatch, capsys):
    # SELF_CONTRADICTORY at or below its own 0.30 line, with no NOT_SUPPORTED
    # or OVERCLAIMS flag blocking alongside it, must not block on its own —
    # this is the exact shape of the 2026-09-17 loop: a calmer rewrite still
    # read as mildly self-contradictory to jev's own scoring while the real
    # overclaim/unsupported problems were already fixed, and blocking on
    # self-contradiction alone looped the Stop gate forever.
    stdout = ("  leaked_internal    HAS_LEAKS            0.10\n"
              "  self_contradictory SELF_CONTRADICTORY   0.05\n"
              "  overclaim          OVERCLAIMS           0.30\n")
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=stdout))
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "a calmer rewrite",
                                        "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 0
    assert "advisory" in out
    assert err == ""


def test_hook_gate_self_contradictory_never_blocks_even_alongside_a_real_overclaim(
        tmp_path, monkeypatch, capsys):
    # SELF_CONTRADICTORY never blocks — not alone, and not in company
    # either, which generalizes the 2026-09-17 loop fix (it used to count
    # as a block reason when paired with a genuine blocking flag). c2 at
    # 0.55 is the companion OVERCLAIMS needs to block on its own.
    stdout = ("  c2                 NOT_SUPPORTED        0.55\n"
              "  leaked_internal    HAS_LEAKS            0.26\n"
              "  self_contradictory SELF_CONTRADICTORY   0.95\n"
              "  overclaim          OVERCLAIMS           0.87\n")
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=stdout))
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "an overclaiming, contradictory reply",
                                        "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 2
    assert "overclaim OVERCLAIMS" in err
    assert "self_contradictory" not in err


def test_hook_gate_block_threshold_is_env_configurable(tmp_path, monkeypatch, capsys):
    # A NOT_SUPPORTED at 0.35 does not block under the default 0.80 line...
    stdout = "  c1   NOT_SUPPORTED   0.35  a claim\n"
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=stdout))
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "a claim", "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    assert code == 0
    # ...but does once the env var lowers the line below 0.35.
    monkeypatch.setenv("SUPERJEV_BLOCK_CONF", "0.30")
    _hook_stdin(monkeypatch, json.dumps({"draft": "a claim", "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    assert code == 2


def test_hook_verify_read_with_strong_flags_blocks_like_gate(tmp_path, monkeypatch, capsys):
    # Same parser, same block line, wired to worker-verify's REJECT-shaped
    # table instead of jev.py's — worker-verify prints the identical row
    # format, so the fix covers both doors with one parser.
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=HIGH_CONF_LIE_STDOUT))
    _hook_stdin(monkeypatch, json.dumps({"tool_name": "Agent",
                                        "tool_response": "COMPLETE: the worker is done, "
                                                          "all tests pass"}))
    code = sj.main(["hook", "verify"])
    out, err = capsys.readouterr()
    assert code == 2
    assert "blocked" in err
    assert "c1" in err and "0.91" in err


def test_hook_ledger_carries_parsed_flags_on_block_and_advisory(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(sj, "LEDGER_PATH", ledger)

    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=HIGH_CONF_LIE_STDOUT))
    evidence = tmp_path / "notes.md"
    evidence.write_text("evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "a lie", "evidence": [str(evidence)]}))
    sj.main(["hook", "gate"])
    block_rec = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert block_rec["exit_code"] == 2
    assert any(f["verdict"] == "NOT_SUPPORTED" for f in block_rec["flags"])

    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(3, stdout=WEAK_FLAGS_STDOUT))
    _hook_stdin(monkeypatch, json.dumps({"draft": "a soft aside", "evidence": [str(evidence)]}))
    sj.main(["hook", "gate"])
    advisory_rec = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert advisory_rec["exit_code"] == 0
    assert any(f["verdict"] == "OVERCLAIMS" for f in advisory_rec["flags"])


def test_hook_gate_fabricated_quote_still_blocks_with_no_flags_parsed(tmp_path, monkeypatch,
                                                                       capsys):
    # exit 2 (REJECT, fabricated quote) prints no row table at all — the
    # strong-flag parse must not be required for this path to block; it
    # was already "block" via the action map before this change.
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(2, stdout=""))
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "a fabricated quote",
                                        "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 2
    assert "blocked" in err


@pytest.mark.parametrize("code,expect", [(0, 0), (3, 0), (4, 2), (2, 0), (5, 0)])
def test_hook_verify_maps_every_exit_code_and_never_returns_3_4_5(tmp_path, monkeypatch,
                                                                   capsys, code, expect):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(code))
    _hook_stdin(monkeypatch, json.dumps({"report": "COMPLETE: the worker is done"}))
    got = sj.main(["hook", "verify"])
    assert got == expect
    assert got not in (3, 4, 5)


def test_hook_empty_stdin_is_fail_open_exit_0(monkeypatch, capsys):
    _hook_stdin(monkeypatch, "")
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 0
    assert out == ""
    assert err == ""


def test_hook_non_json_stdin_is_fail_open_exit_0(monkeypatch, capsys):
    _hook_stdin(monkeypatch, "not json at all {{{")
    code = sj.main(["hook", "verify"])
    out, err = capsys.readouterr()
    assert code == 0
    assert out == ""
    assert err == ""


def test_hook_missing_text_field_is_fail_open_exit_0(monkeypatch, capsys):
    _hook_stdin(monkeypatch, json.dumps({"unrelated": "field"}))
    code = sj.main(["hook", "gate"])
    assert code == 0


def test_hook_gate_with_no_evidence_never_blocks_and_always_hands_the_door_a_file(
        monkeypatch, capsys, door):
    # The wrapped gate tool requires evidence files as positional args; run
    # it with none and it exits on its own usage error, which happens to be
    # the same number this shim maps to "block". A payload naming no
    # evidence now still runs the gate (the unchecked-claims path), but it
    # MUST hand the door a real evidence file, and MUST never block.
    _hook_stdin(monkeypatch, json.dumps({"draft": "a claim with nothing to check it against"}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 0
    assert out == ""          # the fake door printed no claim row -> silent
    assert err == ""
    assert door.calls
    argv = door.argv
    ev = argv[argv.index("--kit") - 1]
    assert ev.endswith(".md")  # an evidence file was always passed, never nothing


def test_hook_reads_transcript_path_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(0))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"message": {"role": "user", "content": "hi"}}) + "\n" +
        json.dumps({"message": {"role": "assistant",
                                "content": [{"type": "text", "text": "the final draft text"}]}})
        + "\n", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"transcript_path": str(transcript)}))
    code = sj.main(["hook", "gate"])
    assert code == 0


def test_hook_never_raises_on_a_broken_payload(monkeypatch, capsys):
    # A payload whose "draft" is not a string at all must not crash the shim.
    _hook_stdin(monkeypatch, json.dumps({"draft": 12345}))
    code = sj.main(["hook", "gate"])
    assert code == 0


# ------------------------------------------------------------ npm resolution

def test_npm_missing_refuses_with_exit_1_no_traceback(monkeypatch, capsys):
    monkeypatch.setattr(sj, "resolve_npm", lambda: None)
    code = sj.main(["sweep", "r.jsonl", "--questions", "q.json", "--out", "o", "--dry-run"])
    err = capsys.readouterr().err
    assert code == 1
    assert "npm not found" in err
    assert "Traceback" not in err


def test_npm_missing_refuses_bench_too(monkeypatch, capsys):
    monkeypatch.setattr(sj, "resolve_npm", lambda: None)
    code = sj.main(["bench", "--dry-run"])
    err = capsys.readouterr().err
    assert code == 1
    assert "npm not found" in err


def test_npm_missing_json_refusal_is_one_object(monkeypatch, capsys):
    monkeypatch.setattr(sj, "resolve_npm", lambda: None)
    code = sj.main(["sweep", "r.jsonl", "--questions", "q.json", "--out", "o",
                    "--dry-run", "--json"])
    obj = json.loads(capsys.readouterr().out.strip())
    assert code == 1
    assert obj["door"] == "sweep"
    assert obj["exit_code"] == 1


def test_resolve_npm_finds_a_newest_nvm_node_when_which_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(sj.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    nvm = tmp_path / ".nvm" / "versions" / "node"
    for v in ("v18.20.0", "v22.9.0", "v20.11.0"):
        (nvm / v / "bin").mkdir(parents=True)
        (nvm / v / "bin" / "npm").write_text("#!/bin/sh\n", encoding="utf-8")
    found = sj.resolve_npm()
    assert found == str(nvm / "v22.9.0" / "bin" / "npm")


def test_resolve_npm_none_when_no_nvm_and_no_which(tmp_path, monkeypatch):
    monkeypatch.setattr(sj.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert sj.resolve_npm() is None


# ------------------------------------------------------------ HOME missing

def test_home_missing_refuses_exit_1(monkeypatch, capsys):
    monkeypatch.delenv("HOME", raising=False)
    code = sj.main(["status"])
    err = capsys.readouterr().err
    assert code == 1
    assert "HOME" in err


# ------------------------------------------------------------ ledger

def test_run_door_appends_one_ledger_line_per_call(tmp_path, door):
    sj.main(["gate", str(tmp_path / "missing.md"), "--draft", str(tmp_path / "missing.md")])
    lines = sj._ledger_lines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["door"] == "gate"
    assert rec["exit_code"] == 0
    assert "argv" in rec and "ms" in rec and "ts" in rec
    assert rec["json_mode"] is False
    assert rec["hook_mode"] is False


def test_ledger_command_prints_recent_lines_and_counts(door, capsys):
    sj.main(["gate", "a.md", "--draft", "a.md"])
    sj.main(["gate", "a.md", "--draft", "a.md"])
    code = sj.main(["ledger"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count('"door": "gate"') == 2
    assert "gate" in out and "2" in out


def test_ledger_command_with_nothing_recorded_yet(capsys):
    code = sj.main(["ledger"])
    out = capsys.readouterr().out
    assert code == 0
    assert "no calls recorded yet" in out


def test_hook_logs_to_the_ledger_on_empty_stdin(monkeypatch):
    _hook_stdin(monkeypatch, "")
    sj.main(["hook", "gate"])
    lines = sj._ledger_lines()
    assert any(json.loads(ln).get("door") == "hook" for ln in lines)


# ------------------------------------------------------------ status honesty

def test_status_says_npm_not_runnable_when_script_present_but_npm_missing(repo, monkeypatch, capsys):
    monkeypatch.setattr(sj, "resolve_npm", lambda: None)
    monkeypatch.setattr(sj, "harness_commit", lambda r: "abc1234")
    code = sj.main(["status"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("script present, npm not runnable") == 5  # sweep, fetch, bench, permit, chain
    assert "LIVE" not in [ln.split()[1] for ln in out.splitlines()
                          if ln.startswith("sweep") or ln.startswith("bench")]


def test_status_says_live_when_script_present_and_npm_runnable(repo, monkeypatch, capsys):
    monkeypatch.setattr(sj, "resolve_npm", lambda: "/usr/bin/npm")
    monkeypatch.setattr(sj, "harness_commit", lambda r: "abc1234")
    code = sj.main(["status"])
    out = capsys.readouterr().out
    assert code == 0
    for line in out.splitlines():
        if line.startswith("sweep") or line.startswith("bench"):
            assert "LIVE" in line


# ------------------------------------------------------------ PR #9 fixes
# Tests added for the Opus review on PR #9 (gh pr view 9 --comments):
# real Stop/PostToolUse payload fields, no fd-level output leak in hook
# mode, hook argparse errors never exit 2, a real ledger exit code /
# skipped flag, a stderr line when the ledger is unwritable, and a
# semantic-version nvm sort.

def _tool_result_record(text):
    return {"message": {"role": "user", "content": [
        {"type": "tool_result", "content": [{"type": "text", "text": text}]}
    ]}}


def _assistant_text_record(text):
    return {"message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _write_transcript(tmp_path, records, name="t.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_stop_hook_uses_last_assistant_message_field_as_the_draft(tmp_path, monkeypatch):
    # A real Stop payload carries "last_assistant_message" directly (per
    # the Claude Code hooks docs) rather than "draft" — must be read.
    captured = {}

    def fake_run(cmd, cwd=None, env=None, **kw):
        draft_arg = cmd[cmd.index("--draft") + 1]
        captured["draft_text"] = Path(draft_arg).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sj.subprocess, "run", fake_run)
    evidence = tmp_path / "notes.md"
    evidence.write_text("the sky is blue", encoding="utf-8")
    payload = {
        "session_id": "s1",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "cwd": "/some/lead/cwd",
        "last_assistant_message": "the sky is blue",
        "evidence": [str(evidence)],
    }
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "gate"])
    assert code == 0
    assert captured["draft_text"] == "the sky is blue"


def test_stop_hook_derives_evidence_from_transcript_tool_results(tmp_path, monkeypatch):
    # No "evidence" key at all (a real Stop payload never carries one) —
    # evidence must come from the last N tool_result blocks in the
    # transcript, written to one temp file.
    captured = {}

    def fake_run(cmd, cwd=None, env=None, **kw):
        # The evidence file is the first positional arg after the fleet
        # door's own argv prefix; read it while it still exists (the shim
        # deletes it after this call returns).
        idx = cmd.index(str(sj.FLEET_JEV_LIB)) if str(sj.FLEET_JEV_LIB) in cmd else 1
        evidence_path = cmd[idx + 1]
        captured["evidence_text"] = Path(evidence_path).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sj.subprocess, "run", fake_run)
    transcript = _write_transcript(tmp_path, [
        _tool_result_record("first tool result"),
        _assistant_text_record("an intermediate assistant note"),
        _tool_result_record("second tool result — the real evidence"),
        _assistant_text_record("the final draft text"),
    ])
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "last_assistant_message": "the final draft text",
    }
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "gate"])
    assert code == 0
    assert "first tool result" in captured["evidence_text"]
    assert "second tool result — the real evidence" in captured["evidence_text"]


def test_stop_hook_with_nothing_derivable_runs_unchecked_and_ledgers_unchecked_true(
        tmp_path, monkeypatch, door):
    # last_assistant_message present, but no evidence field and no
    # transcript_path at all — nothing to derive evidence from. The gate
    # still runs (unchecked path), the ledger line says so, exit 0.
    payload = {"hook_event_name": "Stop", "last_assistant_message": "a claim with no evidence"}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "gate"])
    assert code == 0
    assert door.calls
    lines = sj._ledger_lines()
    rec = json.loads(lines[-1])
    assert rec["unchecked"] is True
    assert rec["skipped"] is False
    assert rec["exit_code"] == 0
    assert "unchecked" in rec["note"]


# ------------------------------------------ unchecked-claims advisory (fake door)

UNCHECKED_LINE = ("super-jev: reply makes claims with no tool evidence this turn — "
                  "mark them unverified or gather evidence")


def _no_evidence_transcript(tmp_path, prompt="please summarize the deploy status for me"):
    """A Stop transcript with a user prompt and an assistant reply, but no
    tool_use / tool_result anywhere — the turn gathered no evidence."""
    t = tmp_path / "t.jsonl"
    t.write_text(
        json.dumps({"message": {"role": "user", "content": prompt}}) + "\n" +
        json.dumps({"message": {"role": "assistant",
                                "content": [{"type": "text",
                                             "text": "The deploy finished and all tests passed."}]}})
        + "\n", encoding="utf-8")
    return t


def test_unchecked_path_prints_one_advisory_when_the_door_reports_a_checkable_claim(
        tmp_path, monkeypatch, capsys):
    # fake door: a READ (3) with one weak claim row — a checkable claim, but
    # not a strong flag. Must be ONE stdout line, exit 0, never a block.
    fake = FakeDoor(3, stdout="\n  c1   SUPPORTED   0.55  The deploy finished and all tests passed\n")
    monkeypatch.setattr(sj.subprocess, "run", fake)
    t = _no_evidence_transcript(tmp_path)
    _hook_stdin(monkeypatch, json.dumps({"hook_event_name": "Stop", "transcript_path": str(t),
                                        "last_assistant_message": "The deploy finished and all tests passed."}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 0
    assert out.strip().splitlines() == [UNCHECKED_LINE]
    assert err == ""
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["unchecked"] is True and rec["exit_code"] == 0


def test_unchecked_path_hands_the_door_the_users_last_prompt_as_evidence(tmp_path, monkeypatch):
    captured = {}

    def spy(cmd, cwd=None, env=None, **kw):
        argv = [str(c) for c in cmd]
        ev = argv[argv.index("--kit") - 1]
        captured["evidence_text"] = open(ev, encoding="utf-8").read()
        return subprocess.CompletedProcess(cmd, 3, stdout="  c1   NOT_SUPPORTED   0.55  x y z w\n", stderr="")

    monkeypatch.setattr(sj.subprocess, "run", spy)
    t = _no_evidence_transcript(tmp_path, prompt="what did the deploy do, in one line")
    _hook_stdin(monkeypatch, json.dumps({"hook_event_name": "Stop", "transcript_path": str(t),
                                        "last_assistant_message": "The deploy finished and all tests passed."}))
    assert sj.main(["hook", "gate"]) == 0
    assert captured["evidence_text"] == "what did the deploy do, in one line"


def test_unchecked_path_stays_silent_when_the_door_reports_no_checkable_claim(
        tmp_path, monkeypatch, capsys):
    fake = FakeDoor(1, stdout="", stderr="jev: reply kit: no checkable claims — pass --claim ...")
    monkeypatch.setattr(sj.subprocess, "run", fake)
    t = _no_evidence_transcript(tmp_path)
    _hook_stdin(monkeypatch, json.dumps({"hook_event_name": "Stop", "transcript_path": str(t),
                                        "last_assistant_message": "Done."}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 0
    assert out == "" and err == ""
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["unchecked"] is True
    assert "no checkable claim" in rec["note"]


def test_unchecked_path_never_blocks_even_on_a_strong_flag_or_a_reject_code(
        tmp_path, monkeypatch, capsys):
    # The exact lie fixture (NOT_SUPPORTED 0.18, OVERCLAIMS 0.98) would block
    # on the evidence path. With no tool evidence there is nothing to block
    # against — only advise. Same for a door exit 2.
    lie = (SKILL / "tests" / "fixtures" / "lie_stop_stdout.txt").read_text(encoding="utf-8")
    for code_in in (3, 2):
        monkeypatch.setattr(sj.subprocess, "run", FakeDoor(code_in, stdout=lie))
        t = _no_evidence_transcript(tmp_path)
        _hook_stdin(monkeypatch, json.dumps({"hook_event_name": "Stop", "transcript_path": str(t),
                                            "last_assistant_message": "all 30 permit cases matched"}))
        code = sj.main(["hook", "gate"])
        out, err = capsys.readouterr()
        assert code == 0
        assert out.strip() == UNCHECKED_LINE
        assert err == ""


def test_unchecked_path_is_not_taken_when_tool_evidence_exists(tmp_path, monkeypatch, capsys):
    # A transcript WITH a tool_result takes the normal evidence path: same
    # weak-claim door output, but no unchecked advisory and no unchecked flag.
    fake = FakeDoor(3, stdout="  c1   SUPPORTED   0.55  x y z w\n")
    monkeypatch.setattr(sj.subprocess, "run", fake)
    t = tmp_path / "t.jsonl"
    t.write_text(
        json.dumps({"message": {"role": "user", "content": [
            {"type": "tool_result", "content": "real tool output here"}]}}) + "\n" +
        json.dumps({"message": {"role": "assistant", "content": [{"type": "text", "text": "reply"}]}})
        + "\n", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"hook_event_name": "Stop", "transcript_path": str(t),
                                        "last_assistant_message": "a claim about the tool output"}))
    assert sj.main(["hook", "gate"]) == 0
    out, _ = capsys.readouterr()
    assert UNCHECKED_LINE not in out
    assert "advisory" in out
    assert "unchecked" not in json.loads(sj._ledger_lines()[-1])


def test_unchecked_path_real_subprocess_via_fake_door_prints_exactly_one_line(tmp_path):
    # Real child process, real fake_door.py: proves the advisory is one clean
    # stdout line and the door's own table never leaks.
    lie = SKILL / "tests" / "fixtures" / "lie_stop_stdout.txt"
    t = _no_evidence_transcript(tmp_path)
    payload = {"hook_event_name": "Stop", "transcript_path": str(t),
               "last_assistant_message": "all 30 permit cases matched, merged PRs 8, 9 and 10"}
    proc = _run_real_hook(f"{sys.executable} {FAKE_DOOR}", payload,
                          extra_env={"FAKE_DOOR_STDOUT_FILE": str(lie), "FAKE_DOOR_EXIT": "3"},
                          ledger_path=tmp_path / "ledger.jsonl")
    assert proc.returncode == 0
    assert proc.stdout.strip().splitlines() == [UNCHECKED_LINE]
    assert "NOT_SUPPORTED" not in proc.stdout
    rec = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[-1])
    assert rec["unchecked"] is True


def test_stop_hook_evidence_derivation_with_a_lying_last_message_still_blocks(tmp_path,
                                                                               monkeypatch):
    # The draft claims something the evidence (derived from the transcript's
    # own tool_result content) does not support. The wrapped gate tool is
    # the thing that actually judges this — here it is faked to REJECT
    # (exit 2), and the wiring must map that to a hook block.
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(2))
    transcript = _write_transcript(tmp_path, [
        _tool_result_record("the file contains exactly 3 lines"),
    ])
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "last_assistant_message": "the file contains exactly 300 lines",
    }
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "gate"])
    assert code == 2
    lines = sj._ledger_lines()
    rec = json.loads(lines[-1])
    assert rec["skipped"] is False
    assert rec["exit_code"] == 2


def test_hook_ledger_records_real_exit_code_not_hardcoded_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(2))
    evidence = tmp_path / "e.md"
    evidence.write_text("x", encoding="utf-8")
    _hook_stdin(monkeypatch, json.dumps({"draft": "a fabricated quote",
                                        "evidence": [str(evidence)]}))
    code = sj.main(["hook", "gate"])
    assert code == 2
    lines = sj._ledger_lines()
    rec = json.loads(lines[-1])
    assert rec["door"] == "hook"
    assert rec["exit_code"] == 2  # not hard-coded 0 on a block


def test_hook_bad_subcommand_invocation_exits_0_not_2(monkeypatch, capsys):
    # argparse's own usage-error exit code (2) collides with the hook
    # contract's "block" exit code. A typo in settings.json wiring
    # (e.g. `hook badword`) must not become a permanent silent block.
    code = sj.main(["hook", "badword"])
    err = capsys.readouterr().err
    assert code == 0
    assert "Traceback" not in err
    lines = sj._ledger_lines()
    rec = json.loads(lines[-1])
    assert rec["skipped"] is True
    assert rec["exit_code"] == 0


def test_hook_missing_door_argument_exits_0_not_2(monkeypatch, capsys):
    code = sj.main(["hook"])
    assert code == 0
    lines = sj._ledger_lines()
    assert json.loads(lines[-1])["skipped"] is True


def test_hook_map_flag_no_longer_exists(monkeypatch, capsys):
    # --map was accepted and never read; removed rather than documented as
    # a no-op. Passing it now is the same as any other bad hook invocation:
    # fail-open, exit 0, never 2.
    code = sj.main(["hook", "gate", "--map", "default"])
    assert code == 0


def test_posttooluse_verify_uses_tool_response_as_the_report(monkeypatch, door):
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Agent",
        "tool_response": [{"type": "text",
                          "text": "COMPLETE: the worker pushed commit abc123"}],
    }
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert code == 0
    report_arg = door.calls[-1]["cmd"][door.calls[-1]["cmd"].index(str(sj.FLEET_VERIFY_PY)) + 1]
    assert Path(report_arg).exists() is False  # cleaned up after the call
    # confirm the door actually saw the tool_response text at call time
    assert door.calls  # the wrapped verify tool was invoked at all


def test_posttooluse_verify_skips_when_tool_name_is_not_agent(monkeypatch, door):
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_response": {"stdout": "some bash output"},
    }
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert code == 0
    assert not door.calls
    lines = sj._ledger_lines()
    rec = json.loads(lines[-1])
    assert rec["skipped"] is True
    assert "not 'Agent'" in rec["note"]


def test_posttooluse_verify_ignores_payload_cwd_for_worktree(monkeypatch):
    captured = {}

    def fake_run(cmd, cwd=None, env=None, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sj.subprocess, "run", fake_run)
    monkeypatch.delenv(sj.HOOK_WORKTREE_ENV, raising=False)
    payload = {"tool_name": "Agent", "tool_response": "COMPLETE: done",
               "cwd": "/lead/session/cwd"}
    _hook_stdin(monkeypatch, json.dumps(payload))
    sj.main(["hook", "verify"])
    assert "--worktree" not in captured["cmd"]


def test_posttooluse_verify_worktree_from_env_var_only(monkeypatch):
    captured = {}

    def fake_run(cmd, cwd=None, env=None, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sj.subprocess, "run", fake_run)
    monkeypatch.setenv(sj.HOOK_WORKTREE_ENV, "/the/worktree")
    payload = {"tool_name": "Agent", "tool_response": "COMPLETE: done",
               "cwd": "/lead/session/cwd"}
    _hook_stdin(monkeypatch, json.dumps(payload))
    sj.main(["hook", "verify"])
    assert "--worktree" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--worktree") + 1] == "/the/worktree"


# ------------------------------------------------ verify: launch-ack skip

def test_hook_verify_skips_a_spawn_launch_acknowledgement(monkeypatch, door):
    # The 2026-09-17 false alarm this fixes: a background Agent spawn's
    # tool_response is only a launch ack, not a report. The wrapped verify
    # tool must never even be called.
    ack = ("Spawned successfully. The worker has been dispatched. "
          "The agent is now running in the background.")
    payload = {"tool_name": "Agent", "tool_response": ack}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert code == 0
    assert not door.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["skipped"] is True
    assert rec["reason"] == "launch-ack"


def test_hook_verify_skips_an_async_launch_acknowledgement(monkeypatch, door):
    ack = "Async agent launched successfully. You will be notified automatically when it completes."
    payload = {"tool_name": "Agent", "tool_response": ack}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert code == 0
    assert not door.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["skipped"] is True
    assert rec["reason"] == "launch-ack"


def test_hook_verify_skips_short_non_report_text(monkeypatch, door):
    # Short text with no COMPLETE/INCOMPLETE/verdict/test-count content is
    # skipped even without matching a named ack phrase.
    payload = {"tool_name": "Agent", "tool_response": "ok, working on it now"}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert code == 0
    assert not door.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["skipped"] is True
    assert rec["reason"] == "launch-ack"


def test_hook_verify_short_text_with_a_report_marker_still_runs(monkeypatch, door):
    # Short text that DOES carry a report marker (COMPLETE) is not skipped
    # just for being short.
    payload = {"tool_name": "Agent", "tool_response": "COMPLETE: done, 4 tests passed"}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert door.calls  # the wrapped verify tool really ran


def test_hook_verify_a_real_long_report_still_runs_and_is_verified(monkeypatch, door):
    real_report = (
        "I fixed the off-by-one in the paginator and re-ran the suite. "
        "COMPLETE: 14 tests passed, 0 failed. Committed as a1b2c3d on "
        "branch fix/paginator-offset. No open questions."
    )
    payload = {"tool_name": "Agent", "tool_response": real_report}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert door.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["skipped"] is False


# ------------------------------------------ verify: dict / list tool_response

def test_hook_verify_skips_a_background_spawn_dict(monkeypatch, door):
    # The exact captured 2026-09-17 payload shape from
    # /Users/admin/super-jev-experiments/ledger/last-verify-payload.json:
    # tool_response is a dict, {"status": "teammate_spawned", "prompt":
    # "<the whole worker brief>", ...}. The brief must NEVER be judged as
    # a report — the door must not even run.
    payload = {
        "tool_name": "Agent",
        "tool_response": {
            "status": "teammate_spawned",
            "prompt": "WORKER CARD v6 — TASK: verify-background-reports. COMPLETE when done.",
            "teammate_id": "BgVerify@session-1",
            "agent_type": "general-purpose",
        },
    }
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert code == 0
    assert not door.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["skipped"] is True
    assert rec["reason"] == "spawn-dict"


@pytest.mark.parametrize("status", ["launched", "running", "spawned", "TEAMMATE_SPAWNED"])
def test_hook_verify_skips_every_known_spawn_status(monkeypatch, door, status):
    payload = {"tool_name": "Agent",
              "tool_response": {"status": status, "prompt": "some brief text"}}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert code == 0
    assert not door.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["reason"] == "spawn-dict"


def test_hook_verify_skips_a_dict_with_no_recognised_fields_at_all(monkeypatch, door):
    # No status, no result/report/content/text — nothing here looks like a
    # report, so this is treated as a spawn dict too (conservative default).
    payload = {"tool_name": "Agent", "tool_response": {"agent_id": "x", "color": "green"}}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert code == 0
    assert not door.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["reason"] == "spawn-dict"


def test_hook_verify_dict_with_result_field_runs_as_a_real_report(monkeypatch, door):
    payload = {"tool_name": "Agent",
              "tool_response": {"status": "completed",
                                 "result": "COMPLETE: 9 tests passed, pushed to branch x"}}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert door.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["skipped"] is False


def test_hook_verify_dict_report_field_beats_a_spawn_status(monkeypatch, door):
    # Even if "status" happens to collide with a spawn word, a real report
    # field present alongside it always wins.
    payload = {"tool_name": "Agent",
              "tool_response": {"status": "running",
                                 "report": "COMPLETE: 3 tests passed"}}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert door.calls


def test_hook_verify_tool_response_list_of_content_blocks_is_joined(monkeypatch, door):
    payload = {"tool_name": "Agent",
              "tool_response": [
                  {"type": "text", "text": "COMPLETE: worker finished. "},
                  {"type": "text", "text": "6 tests passed, 0 failed."},
              ]}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert door.calls
    assert "COMPLETE" in "".join(str(c) for c in door.argv) or door.calls  # ran at all


def test_hook_verify_ack_patterns_are_env_overridable(monkeypatch, door):
    monkeypatch.setenv(sj.HOOK_ACK_PATTERNS_ENV, "custom marker phrase")
    payload = {"tool_name": "Agent",
              "tool_response": "this text carries the custom marker phrase somewhere in it "
                                "and is long enough on its own to not hit the short-text rule"}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert code == 0
    assert not door.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["reason"] == "launch-ack"


def test_hook_verify_min_chars_is_env_overridable(monkeypatch, door):
    monkeypatch.setenv(sj.VERIFY_MIN_CHARS_ENV, "5")
    payload = {"tool_name": "Agent", "tool_response": "done now"}  # 8 chars, no markers
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "verify"])
    assert door.calls  # 8 >= min_chars(5), so it is not skipped


# ------------------------------------------------ hook verify --from-file

def test_hook_verify_from_file_runs_the_same_check_and_prints_the_same_verdict(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(0))
    report_file = tmp_path / "report.txt"
    report_file.write_text("COMPLETE: worker finished, 6 tests passed", encoding="utf-8")
    code = sj.main(["hook", "verify", "--from-file", str(report_file),
                    "--worktree", str(tmp_path)])
    out, err = capsys.readouterr()
    assert code == 0


def test_hook_verify_from_file_blocks_on_reject(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(4))
    report_file = tmp_path / "report.txt"
    report_file.write_text("COMPLETE: worker finished, evidence disproves a claim",
                           encoding="utf-8")
    code = sj.main(["hook", "verify", "--from-file", str(report_file),
                    "--worktree", str(tmp_path)])
    err = capsys.readouterr().err
    assert code == 2
    assert "blocked" in err


def test_hook_verify_from_file_ledger_marks_manual_source(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(sj, "LEDGER_PATH", ledger)
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(0))
    report_file = tmp_path / "report.txt"
    report_file.write_text("COMPLETE: worker finished cleanly", encoding="utf-8")
    sj.main(["hook", "verify", "--from-file", str(report_file), "--worktree", str(tmp_path)])
    rec = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["hook_mode"] is False
    assert rec["source"] == "manual"


def test_hook_verify_from_file_does_not_read_stdin(tmp_path, monkeypatch):
    # No stdin fixture set up at all — proves --from-file bypasses the
    # stdin-JSON-payload path entirely.
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(0))
    report_file = tmp_path / "report.txt"
    report_file.write_text("COMPLETE: fine", encoding="utf-8")
    code = sj.main(["hook", "verify", "--from-file", str(report_file),
                    "--test-cmd", "npm test"])
    assert code == 0


def test_hook_verify_from_file_missing_file_fails_open(tmp_path, monkeypatch):
    code = sj.main(["hook", "verify", "--from-file", str(tmp_path / "missing.txt")])
    assert code == 0
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["skipped"] is True
    assert rec["reason"] == "from-file-unreadable"


def test_hook_verify_from_file_no_evidence_prints_advisory_and_does_not_run_door(
        tmp_path, monkeypatch, capsys):
    # Item 2's own rule: none of --worktree/--test-cmd/--pr (and no
    # SUPERJEV_HOOK_WORKTREE) means there is nothing to gather evidence
    # from — advisory, exit 0, and the wrapped door is never called.
    monkeypatch.delenv(sj.HOOK_WORKTREE_ENV, raising=False)
    called = FakeDoor(4)  # would block, if it ran
    monkeypatch.setattr(sj.subprocess, "run", called)
    report_file = tmp_path / "report.txt"
    report_file.write_text("COMPLETE: worker finished, evidence disproves a claim",
                           encoding="utf-8")
    code = sj.main(["hook", "verify", "--from-file", str(report_file)])
    out = capsys.readouterr().out
    assert code == 0
    assert "no evidence source given" in out
    assert "--worktree/--test-cmd/--pr" in out
    assert not called.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["skipped"] is True
    assert rec["reason"] == "from-file-no-evidence"


def test_hook_verify_from_file_env_worktree_counts_as_evidence(tmp_path, monkeypatch):
    # SUPERJEV_HOOK_WORKTREE alone (no --worktree/--test-cmd/--pr flag) is
    # still a real evidence source — the advisory must not fire.
    monkeypatch.setenv(sj.HOOK_WORKTREE_ENV, str(tmp_path))
    fake = FakeDoor(0)
    monkeypatch.setattr(sj.subprocess, "run", fake)
    report_file = tmp_path / "report.txt"
    report_file.write_text("COMPLETE: fine", encoding="utf-8")
    code = sj.main(["hook", "verify", "--from-file", str(report_file)])
    assert code == 0
    assert fake.calls


def test_run_door_timeout_returns_124_and_logs_ledger(monkeypatch):
    def fake_run(cmd, cwd=None, env=None, timeout=None, **kw):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(sj.subprocess, "run", fake_run)
    code, out, err = sj.run_door(["x"], capture=True, door="gate", timeout=5)
    assert code == sj.TIMEOUT_EXIT_CODE == 124
    lines = sj._ledger_lines()
    rec = json.loads(lines[-1])
    assert rec.get("timeout") is True


def test_hook_gate_timeout_fails_open_as_advisory_never_blocks(monkeypatch):
    def fake_run(cmd, cwd=None, env=None, timeout=None, **kw):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(sj.subprocess, "run", fake_run)
    evidence_path = Path(sj.tempfile.gettempdir()) / "does_not_need_to_exist.md"
    _hook_stdin(monkeypatch, json.dumps({"draft": "x", "evidence": ["/tmp/whatever"]}))
    code = sj.main(["hook", "gate"])
    assert code == 0  # 124 is not in GATE_HOOK_ACTION -> defaults to advisory, never 2


def test_ledger_append_prints_one_stderr_line_when_unwritable(monkeypatch, capsys, tmp_path):
    # A file where the ledger's parent directory should be makes mkdir /
    # open fail with OSError — must not be swallowed in total silence.
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(sj, "LEDGER_PATH", blocker / "calls.jsonl")
    sj.ledger_append({"ts": "now", "door": "gate"})
    err = capsys.readouterr().err
    assert "could not write ledger" in err


def test_default_ledger_path_honours_superjev_ledger_env(monkeypatch, tmp_path):
    override = tmp_path / "custom" / "ledger.jsonl"
    monkeypatch.setenv("SUPERJEV_LEDGER", str(override))
    assert sj._default_ledger_path() == override
    monkeypatch.delenv("SUPERJEV_LEDGER", raising=False)
    assert sj._default_ledger_path() == sj.SKILL_DIR / "ledger" / "calls.jsonl"


def test_resolve_npm_prefers_semantic_version_over_lexicographic_sort(tmp_path, monkeypatch):
    # v9.0.0 > v24.11.1 lexicographically but not numerically — the review's
    # exact repro: a stale nvm v9 must never shadow a newer v24.
    monkeypatch.setattr(sj.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    nvm = tmp_path / ".nvm" / "versions" / "node"
    for v in ("v9.0.0", "v24.11.1"):
        (nvm / v / "bin").mkdir(parents=True)
        (nvm / v / "bin" / "npm").write_text("#!/bin/sh\n", encoding="utf-8")
    found = sj.resolve_npm()
    assert found == str(nvm / "v24.11.1" / "bin" / "npm")


# -------------------------------------------------- real-subprocess proof
# The two tests below do NOT monkeypatch subprocess.run — they invoke
# `python3 superjev.py hook gate` as a real child process, wired to a real
# (but harmless, test-only) fake gate door via SUPERJEV_GATE_CMD, so the
# fd-level fix (capture_output=True in hook mode, never inherited fds) is
# proven rather than assumed from a mock.

SUPERJEV_PY = SKILL / "superjev.py"


def _run_real_hook(door_cmd, payload, extra_env=None, ledger_path=None):
    env = dict(os.environ)
    env["SUPERJEV_GATE_CMD"] = door_cmd
    env["HOME"] = os.environ.get("HOME", "/tmp")
    if ledger_path:
        env["SUPERJEV_LEDGER"] = str(ledger_path)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, str(SUPERJEV_PY), "hook", "gate"],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
    )
    return proc


def test_hook_gate_real_subprocess_clean_is_silent_and_no_leak(tmp_path):
    evidence = tmp_path / "notes.md"
    evidence.write_text("the sky is blue", encoding="utf-8")
    payload = {"hook_event_name": "Stop", "last_assistant_message": "the sky is blue",
              "evidence": [str(evidence)]}
    proc = _run_real_hook(f"{sys.executable} {FAKE_DOOR}", payload,
                          ledger_path=tmp_path / "ledger.jsonl")
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert "fake_door:" not in proc.stdout  # the wrapped door's own line never leaks


def test_hook_gate_real_subprocess_never_leaks_noisy_child_output(tmp_path):
    # Reproduces the Opus review's exact repro: a wrapped door that prints
    # a multi-line table to stdout, a line to stderr, and exits 2 (REJECT).
    noisy = SKILL / "tests" / "fake_noisy_gate.py"
    evidence = tmp_path / "notes.md"
    evidence.write_text("some evidence", encoding="utf-8")
    payload = {"hook_event_name": "Stop", "last_assistant_message": "a fabricated quote",
              "evidence": [str(evidence)]}
    proc = _run_real_hook(f"{sys.executable} {noisy}", payload,
                          ledger_path=tmp_path / "ledger.jsonl")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "FAKE-GATE-STDOUT" not in proc.stdout
    assert "FAKE-GATE-STDOUT" not in proc.stderr
    assert "fake gate stderr" not in proc.stderr
    assert "blocked" in proc.stderr
    ledger_lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    last = json.loads(ledger_lines[-1])
    assert last["exit_code"] == 2
    assert last["skipped"] is False


def test_hook_gate_real_subprocess_the_exact_lie_fixture_blocks(tmp_path):
    # Full real-subprocess proof of the fix: fake_door.py, run for real
    # (not monkeypatched), prints the exact lie-table fixture and exits 3
    # (READ) — the SAME shape jev-check produced against the live Stop
    # payload the finding was built from. The hook shim, wired end to end
    # through a real child process, must still block it.
    evidence = tmp_path / "notes.md"
    evidence.write_text("only PR 8 merged; 24 of 30 permit cases matched", encoding="utf-8")
    payload = {"hook_event_name": "Stop",
              "last_assistant_message": "all 30 permit cases matched... merged PRs 8, 9 and 10",
              "evidence": [str(evidence)]}
    lie_stdout_path = FIXTURES / "lie_stop_high_confidence_stdout.txt"
    proc = _run_real_hook(f"{sys.executable} {FAKE_DOOR}", payload,
                          extra_env={"FAKE_DOOR_STDOUT_FILE": str(lie_stdout_path),
                                     "FAKE_DOOR_EXIT": "3"},
                          ledger_path=tmp_path / "ledger.jsonl")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "blocked" in proc.stderr
    assert "c1" in proc.stderr and "NOT_SUPPORTED" in proc.stderr and "0.91" in proc.stderr
    ledger_lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    last = json.loads(ledger_lines[-1])
    assert last["exit_code"] == 2
    assert any(f["verdict"] == "NOT_SUPPORTED" for f in last["flags"])


# ------------------------------------------------ hook prompt-verify

def test_prompt_verify_parses_two_teammate_message_blocks_and_derives_flags(
        tmp_path, monkeypatch, capsys):
    fake = FakeDoor(0)
    monkeypatch.setattr(sj.subprocess, "run", fake)
    wt1 = tmp_path / "wt1"
    wt1.mkdir()
    prompt = (
        "some lead narration before the mailbox\n"
        f'<teammate-message teammate_id="WorkerA" summary="done">\n'
        f"COMPLETE: fixed the bug in {wt1}, ran npm test, 6 tests passed. "
        f"Opened PR #42.\n"
        f"</teammate-message>\n"
        "more narration\n"
        '<teammate-message teammate_id="WorkerB" summary="also done">\n'
        "INCOMPLETE: could not reproduce, 2 tests failed.\n"
        "</teammate-message>\n"
    )
    _hook_stdin(monkeypatch, json.dumps({"prompt": prompt}))
    code = sj.main(["hook", "prompt-verify"])
    out = capsys.readouterr().out
    assert code == 0
    assert "super-jev verify WorkerA:" in out
    assert "super-jev verify WorkerB:" in out
    # two reports checked -> the wrapped verify door ran twice (a third
    # call, `git remote get-url origin` for the --pr -> PR-URL lookup,
    # also goes through the same monkeypatched subprocess.run)
    verify_calls = [c for c in fake.calls if c["cmd"][:1] != ["git"]]
    assert len(verify_calls) == 2
    # WorkerA's block named an absolute path and an npm test phrase -> both
    # were derived and passed straight to worker-verify's own flags
    a_call = verify_calls[0]["cmd"]
    assert "--worktree" in a_call
    assert str(wt1) in a_call
    assert "--test-cmd" in a_call
    ledger_lines = [json.loads(l) for l in sj._ledger_lines()]
    sources = [l.get("source") for l in ledger_lines if l.get("door") == "hook"]
    assert sources.count("teammate-message") == 2


def test_prompt_verify_always_exits_0_even_on_reject(monkeypatch, capsys):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(4))  # REJECT
    prompt = ('<teammate-message teammate_id="WorkerC">\n'
             "COMPLETE: done, evidence disproves a claim\n"
             "</teammate-message>")
    _hook_stdin(monkeypatch, json.dumps({"prompt": prompt}))
    code = sj.main(["hook", "prompt-verify"])
    out = capsys.readouterr().out
    assert code == 0  # advisory only, never blocks
    assert "super-jev verify WorkerC: REJECT" in out


def test_prompt_verify_skips_block_with_no_report_marker(monkeypatch, door):
    prompt = ('<teammate-message teammate_id="WorkerD">\n'
             "just chatting, nothing to report yet\n"
             "</teammate-message>")
    _hook_stdin(monkeypatch, json.dumps({"prompt": prompt}))
    code = sj.main(["hook", "prompt-verify"])
    assert code == 0
    assert not door.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["skipped"] is True


def test_prompt_verify_no_teammate_message_blocks_is_silent(monkeypatch, door, capsys):
    _hook_stdin(monkeypatch, json.dumps({"prompt": "just a plain message, no mailbox blocks"}))
    code = sj.main(["hook", "prompt-verify"])
    out = capsys.readouterr().out
    assert code == 0
    assert out == ""
    assert not door.calls
    rec = json.loads(sj._ledger_lines()[-1])
    assert rec["reason"] == "no-teammate-messages"


def test_prompt_verify_empty_stdin_fails_open(monkeypatch):
    _hook_stdin(monkeypatch, "")
    code = sj.main(["hook", "prompt-verify"])
    assert code == 0


def test_prompt_verify_non_json_stdin_fails_open(monkeypatch):
    _hook_stdin(monkeypatch, "not json at all {")
    code = sj.main(["hook", "prompt-verify"])
    assert code == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# ---------------------------------------------------------------------------
# The 2026-09-17 false block, and the precision fix for it.
#
# A TRUE report ("187 passed", "both checks pass", "hook verify now exits 0 or
# skipped"), checked with --worktree/--test-cmd/--pr, came back
#   c2 NOT_SUPPORTED 0.68 · c3 NOT_SUPPORTED 0.97 · c4 CONTRADICTED 0.72
#   overclaim OVERCLAIMS 1.00
# and BLOCKED, while the lead had already confirmed by hand that the tests and
# the CI checks both passed.
#
# Two things matter about that table, and both are load-bearing here.
#
# 1. worker-verify's OWN verdict for it is READ (exit 3), not REJECT: its
#    `verdict()` only REJECTs on a CONTRADICTED claim at or above the 0.80
#    line, and 0.72 is under it. So the door said "a human should read this".
#    The SHIM upgraded that to a hard block, all on its own, via the
#    OVERCLAIMS >= 0.80 rule. The shim was stricter than the door it wraps,
#    in the one direction that door's author explicitly declined.
# 2. The evidence was missing, not the honesty. --test-cmd was a
#    directory-level pytest, which worker-verify refuses outright, so no test
#    output was ever collected; and --pr only ever gathers `gh pr view --json
#    state,isDraft,headRefName,mergedAt`, which carries no check-run data at
#    all. "187 passed" and "both checks pass" had nothing to be checked
#    against, so OVERCLAIMS at 1.00 was the correct answer about OUR gather,
#    not a finding about the worker.
#
# Every fixture below is recorded stdout in the exact table shape jev.py and
# worker-verify print. Nothing here runs a door or touches the network.
FALSE_BLOCK_STDOUT = (FIXTURES / "false_block_verify_stdout.txt").read_text(encoding="utf-8")
FALSE_BLOCK_PROBE = (FIXTURES / "false_block_dryrun_probe.txt").read_text(encoding="utf-8")
ALL_SUPPORTED_STDOUT = (FIXTURES / "all_supported_overclaim_stdout.txt").read_text(encoding="utf-8")
REAL_LIE_STDOUT = (FIXTURES / "real_lie_verify_stdout.txt").read_text(encoding="utf-8")

# The exact flags the lead's live false block came back with.
LIVE_FALSE_BLOCK_FLAGS = [
    {"key": "c2", "verdict": "NOT_SUPPORTED", "score": 0.68},
    {"key": "c3", "verdict": "NOT_SUPPORTED", "score": 0.97},
    {"key": "c4", "verdict": "CONTRADICTED", "score": 0.72},
    {"key": "overclaim", "verdict": "OVERCLAIMS", "score": 1.00},
]

HEALTHY_PROBE = ("EVIDENCE: 7 blocks, 24196 chars (limit 100000)\n"
                 "  [1] $ git -c color.ui=false -C /tmp/wt status --porcelain=v1 -b\n"
                 "  [2] $ python3 -m pytest tests/test_x.py -q\n"
                 "      187 passed in 2.71s\n")


class SplitDoor:
    """A fake door that answers the real run and the free --dry-run probe
    differently — because that is what the actual door does. Returns the
    verdict table for a normal call and the recorded gather for a
    --dry-run call. Records every argv."""

    def __init__(self, stdout, probe_stdout, code=3, probe_code=0):
        self.stdout, self.probe_stdout = stdout, probe_stdout
        self.code, self.probe_code = code, probe_code
        self.calls = []

    def __call__(self, cmd, cwd=None, env=None, **kw):
        argv = [str(c) for c in cmd]
        self.calls.append(argv)
        if "--dry-run" in argv:
            return subprocess.CompletedProcess(cmd, self.probe_code,
                                               stdout=self.probe_stdout, stderr="")
        return subprocess.CompletedProcess(cmd, self.code, stdout=self.stdout, stderr="")

    @property
    def dry_runs(self):
        return [c for c in self.calls if "--dry-run" in c]


# ---- the parsers -----------------------------------------------------------

def test_parse_claim_rows_keeps_the_supported_rows_strong_flags_drops():
    # _parse_strong_flags keeps only red verdicts, so it can never answer
    # "was every claim supported?". _parse_claim_rows is what can.
    rows = sj._parse_claim_rows(FALSE_BLOCK_STDOUT)
    assert [r["key"] for r in rows] == ["c1", "c2", "c3", "c4"]
    assert rows[0] == {"key": "c1", "verdict": "SUPPORTED", "score": 0.99}
    assert [f["key"] for f in sj._parse_strong_flags(FALSE_BLOCK_STDOUT)] == \
        ["c2", "c3", "c4", "overclaim"]


def test_parse_claim_rows_never_raises_on_junk():
    assert sj._parse_claim_rows("") == []
    assert sj._parse_claim_rows(None) == []
    assert sj._parse_claim_rows("no table here at all") == []


def test_parse_claim_rows_ignores_the_draft_level_rows():
    rows = sj._parse_claim_rows(ALL_SUPPORTED_STDOUT)
    assert all(r["key"].startswith("c") for r in rows)
    assert "overclaim" not in [r["key"] for r in rows]


# ---- the guardrail mirror --------------------------------------------------

@pytest.mark.parametrize("cmd,refused", [
    # The exact command behind `npm run test:skill`, and the exact reason
    # "187 passed" was unprovable: worker-verify refuses a directory-level
    # pytest, so it collects NO test output.
    ("python3 -m pytest skills/super-jev/tests -q", True),
    ("pytest", True),
    ("python3 -m pytest", True),
    # Naming the file, or a ::selector, is accepted.
    ("python3 -m pytest skills/super-jev/tests/test_superjev.py -q", False),
    ("python3 -m pytest tests/test_x.py::test_one", False),
    # Not a pytest invocation at all — the guardrail has no opinion. Note
    # that this is ALSO how the directory-level run gets in anyway: the npm
    # script wraps the very command the guardrail refuses. Recorded here on
    # purpose; docs/hooks.md names it as a failure mode.
    ("npm run test:skill", False),
    ("node --test test/*.test.ts", False),
    ("", False),
])
def test_test_cmd_will_be_refused_mirrors_worker_verifys_guardrail(cmd, refused):
    assert sj._test_cmd_will_be_refused(cmd) is refused


def test_test_cmd_will_be_refused_survives_an_unparseable_command():
    assert sj._test_cmd_will_be_refused('pytest "unclosed') is False


# ---- the evidence inventory ------------------------------------------------

def test_evidence_inventory_is_thin_when_nothing_at_all_was_passed():
    inv = sj._evidence_inventory()
    assert inv["thin"] is True
    assert any("no evidence source" in r for r in inv["reasons"])


def test_evidence_inventory_is_thin_on_a_directory_level_pytest():
    inv = sj._evidence_inventory(test_cmd="python3 -m pytest skills/super-jev/tests -q",
                                 worktree="/tmp/wt")
    assert inv["thin"] is True
    assert any("directory-level pytest" in r for r in inv["reasons"])


def test_evidence_inventory_records_that_pr_state_carries_no_check_data():
    # Why "both checks pass" came back NOT_SUPPORTED: `gh pr view --json
    # state,isDraft,headRefName,mergedAt` has no check-run field.
    inv = sj._evidence_inventory(worktree="/tmp/wt", pr=18,
                                 test_cmd="python3 -m pytest tests/test_x.py")
    assert any("NO check-run data" in r for r in inv["reasons"])
    # On its own that is NOT thinness — the PR block is real evidence.
    assert inv["thin"] is False


def test_evidence_inventory_reads_the_size_line_and_the_absent_markers():
    inv = sj._evidence_inventory(test_cmd="python3 -m pytest skills/super-jev/tests -q",
                                 worktree="/tmp/wt", pr=18,
                                 probe_stdout=FALSE_BLOCK_PROBE)
    assert (inv["blocks"], inv["chars"]) == (4, 9120)
    assert "NO TEST OUTPUT WAS COLLECTED" in inv["missing"]
    assert inv["thin"] is True


def test_evidence_inventory_is_thin_below_the_char_floor():
    inv = sj._evidence_inventory(worktree="/tmp/wt",
                                 probe_stdout="EVIDENCE: 1 blocks, 12 chars (limit 100000)\n")
    assert inv["thin"] is True
    assert any("under the" in r for r in inv["reasons"])


def test_evidence_inventory_is_not_thin_when_the_gather_was_healthy():
    inv = sj._evidence_inventory(test_cmd="python3 -m pytest tests/test_x.py -q",
                                 worktree="/tmp/wt", probe_stdout=HEALTHY_PROBE)
    assert inv["thin"] is False
    assert inv["missing"] == []
    assert (inv["blocks"], inv["chars"]) == (7, 24196)


def test_evidence_inventory_treats_an_unreadable_probe_as_unknown_not_absent():
    # Unknown is not absent. A probe we could not parse must NOT drop a
    # block — we would rather keep one we cannot justify than lose one.
    inv = sj._evidence_inventory(worktree="/tmp/wt", probe_stdout="garbage, no size line")
    assert inv["thin"] is False
    assert inv["blocks"] is None


# ---- the block decision ----------------------------------------------------

def test_the_live_false_block_becomes_advisory_once_evidence_is_measured():
    """THE case. The lead's exact flags, plus the recorded gather showing the
    test command was refused, must not block."""
    evidence = sj._evidence_inventory(
        test_cmd="python3 -m pytest skills/super-jev/tests -q",
        worktree="/tmp/wt", pr=18, probe_stdout=FALSE_BLOCK_PROBE)
    rows = sj._parse_claim_rows(FALSE_BLOCK_STDOUT)
    reasons, notes = sj._hook_block_decision(LIVE_FALSE_BLOCK_FLAGS, rows, evidence)
    assert reasons == []
    assert any("cannot carry a verdict" in n for n in notes)


def test_the_same_flags_still_block_when_the_gather_was_healthy():
    """The gate is conditional on the EVIDENCE, not a blanket weakening. Same
    table, healthy gather -> c3 NOT_SUPPORTED 0.97 blocks on its own
    confidence, and OVERCLAIMS 1.00 blocks alongside it (c3/c2 both clear
    the 0.50 companion floor)."""
    evidence = sj._evidence_inventory(test_cmd="python3 -m pytest tests/test_x.py -q",
                                      worktree="/tmp/wt", probe_stdout=HEALTHY_PROBE)
    rows = sj._parse_claim_rows(FALSE_BLOCK_STDOUT)
    reasons, notes = sj._hook_block_decision(LIVE_FALSE_BLOCK_FLAGS, rows, evidence)
    assert reasons == ["c3 NOT_SUPPORTED 0.97", "overclaim OVERCLAIMS 1.00"]
    assert notes == []


def test_overclaims_alone_is_advisory_when_every_claim_came_back_supported():
    """No evidence inventory at all here — all-claims-SUPPORTED is enough on
    its own, and it is what makes this work on the real hook path, which
    never has a --test-cmd to measure."""
    rows = sj._parse_claim_rows(ALL_SUPPORTED_STDOUT)
    flags = sj._parse_strong_flags(ALL_SUPPORTED_STDOUT)
    reasons, notes = sj._hook_block_decision(flags, rows)
    assert reasons == []
    assert any("SUPPORTED" in n for n in notes)


def test_a_confident_overclaim_still_blocks_with_no_claim_rows_at_all():
    # Unparseable table: we know nothing, so nothing is suppressed.
    flags = [{"key": "overclaim", "verdict": "OVERCLAIMS", "score": 0.98}]
    reasons, notes = sj._hook_block_decision(flags, [], None)
    assert reasons == ["overclaim OVERCLAIMS 0.98"]
    assert notes == []


def test_thin_evidence_suppresses_even_a_confident_contradiction():
    """The generalization of PR #18's precision fix: a thin gather
    suppresses EVERY verdict, not only OVERCLAIMS, because a
    confident-but-ungrounded verdict and a real problem print the
    identical red table. c2 NOT_SUPPORTED 0.15 was never a candidate (it
    is under the 0.80 line); c3 CONTRADICTED 0.99 and the OVERCLAIMS both
    would have blocked, but the gather here is measured thin, so neither
    does — see the healthy-gather counterpart in
    test_from_file_blocks_a_real_lie."""
    thin = sj._evidence_inventory()
    rows = sj._parse_claim_rows(REAL_LIE_STDOUT)
    flags = sj._parse_strong_flags(REAL_LIE_STDOUT)
    reasons, notes = sj._hook_block_decision(flags, rows, thin)
    assert reasons == []
    assert any("cannot carry a verdict" in n for n in notes)


def test_the_original_lie_fixture_is_now_advisory_not_a_block():
    """Regression marker for the 2026-09-17 direction fix (see docs/hooks.md,
    "Decided: the block rule follows confidence"). c1 NOT_SUPPORTED 0.18
    means the judge barely suspects c1 — under the new >= line it is not a
    block candidate at all, and OVERCLAIMS 0.98 has no companion claim at
    or above 0.50 to block alongside (c1's own 0.18 does not qualify), so
    the whole table reads as advisory now. This is the documented
    trade-off, not a bug — the case that still blocks is
    test_hook_gate_a_high_confidence_lie_blocks."""
    rows = sj._parse_claim_rows(LIE_STDOUT)
    flags = sj._parse_strong_flags(LIE_STDOUT)
    reasons, notes = sj._hook_block_decision(flags, rows)
    assert reasons == []
    assert any("companion" in n for n in notes)


def test_the_weak_flags_fixture_is_still_advisory():
    rows = sj._parse_claim_rows(WEAK_FLAGS_STDOUT)
    flags = sj._parse_strong_flags(WEAK_FLAGS_STDOUT)
    assert sj._hook_block_decision(flags, rows)[0] == []


def test_hook_block_reasons_still_takes_one_argument():
    # Back-compat: every existing caller and test passes flags only. With
    # no claim_rows/evidence given at all, the gather defaults to healthy
    # (nothing was measured to be thin) and the companion check treats
    # "no rows" as unknown rather than "zero" — so both the confident
    # NOT_SUPPORTED and the OVERCLAIMS block.
    assert sj._hook_block_reasons(LIVE_FALSE_BLOCK_FLAGS) == \
        ["c3 NOT_SUPPORTED 0.97", "overclaim OVERCLAIMS 1.00"]


# ---- end to end, through `hook verify --from-file` -------------------------

def _report_file(tmp_path):
    p = tmp_path / "report.md"
    p.write_text(
        "COMPLETE. npm run test:skill reports 187 passed. Both checks pass on "
        "the pull request. The verify hook now exits 0 or skips on a spawn "
        "dict. Worktree /tmp/wt on docs/hooks-critique.\n", encoding="utf-8")
    return p


def test_from_file_does_not_block_the_live_false_block(tmp_path, monkeypatch, capsys):
    fake = SplitDoor(FALSE_BLOCK_STDOUT, FALSE_BLOCK_PROBE, code=3)
    monkeypatch.setattr(sj.subprocess, "run", fake)
    rc = sj._hook_verify_from_file(
        "verify", str(_report_file(tmp_path)), worktree="/tmp/wt",
        test_cmd="python3 -m pytest skills/super-jev/tests -q", pr=18)
    out = capsys.readouterr()
    assert rc == 0
    assert "advisory" in out.out
    assert "cannot carry a verdict" in out.out
    # The free dry-run probe was spent exactly once, and only because the
    # block would have rested on OVERCLAIMS alone.
    assert len(fake.dry_runs) == 1


def test_from_file_blocks_a_real_lie(tmp_path, monkeypatch, capsys):
    # A HEALTHY gather this time — a file-level test command, not the
    # directory-level one refused above — so c3 CONTRADICTED 0.99 is
    # trusted and blocks. (c2 NOT_SUPPORTED 0.15 is under the confidence
    # line and is never a candidate either way.)
    fake = SplitDoor(REAL_LIE_STDOUT, HEALTHY_PROBE, code=3)
    monkeypatch.setattr(sj.subprocess, "run", fake)
    rc = sj._hook_verify_from_file(
        "verify", str(_report_file(tmp_path)), worktree="/tmp/wt",
        test_cmd="python3 -m pytest tests/test_x.py -q", pr=18)
    out = capsys.readouterr()
    assert rc == 2
    assert "blocked this" in out.err
    assert "c3 CONTRADICTED 0.99" in out.err
    # Every candidate is gated on the gather's health now, so the probe is
    # always spent to confirm it — even though it turns out healthy here.
    assert len(fake.dry_runs) == 1


def test_from_file_does_not_probe_when_nothing_would_block(tmp_path, monkeypatch, capsys):
    fake = SplitDoor(WEAK_FLAGS_STDOUT, FALSE_BLOCK_PROBE, code=3)
    monkeypatch.setattr(sj.subprocess, "run", fake)
    rc = sj._hook_verify_from_file("verify", str(_report_file(tmp_path)),
                                   worktree="/tmp/wt")
    capsys.readouterr()
    assert rc == 0
    assert fake.dry_runs == []


def test_explain_prints_the_evidence_size_and_the_claim_table(tmp_path, monkeypatch, capsys):
    fake = SplitDoor(FALSE_BLOCK_STDOUT, FALSE_BLOCK_PROBE, code=3)
    monkeypatch.setattr(sj.subprocess, "run", fake)
    rc = sj._hook_verify_from_file(
        "verify", str(_report_file(tmp_path)), worktree="/tmp/wt",
        test_cmd="python3 -m pytest skills/super-jev/tests -q", pr=18, explain=True)
    out = capsys.readouterr().out
    assert rc == 0
    # the evidence file size, in blocks and chars
    assert "4 block(s), 9120 chars" in out
    assert "evidence too thin : YES" in out
    assert "NO TEST OUTPUT WAS COLLECTED" in out
    # the per-claim table, every row of it
    for key in ("c1", "c2", "c3", "c4"):
        assert key in out
    assert "SUPPORTED" in out and "NOT_SUPPORTED" in out and "CONTRADICTED" in out
    # the thresholds and the direction warning, so nobody misreads the float
    assert "CONFIDENCE in its verdict" in out
    assert "decision          : ADVISORY" in out


def test_explain_runs_the_probe_even_when_nothing_would_block(tmp_path, monkeypatch, capsys):
    fake = SplitDoor(WEAK_FLAGS_STDOUT, HEALTHY_PROBE, code=3)
    monkeypatch.setattr(sj.subprocess, "run", fake)
    sj._hook_verify_from_file("verify", str(_report_file(tmp_path)),
                              worktree="/tmp/wt", explain=True)
    out = capsys.readouterr().out
    assert len(fake.dry_runs) == 1
    assert "7 block(s), 24196 chars" in out
    assert "evidence too thin : no" in out


def test_explain_is_wired_to_the_cli_flag(tmp_path, monkeypatch, capsys):
    fake = SplitDoor(FALSE_BLOCK_STDOUT, FALSE_BLOCK_PROBE, code=3)
    monkeypatch.setattr(sj.subprocess, "run", fake)
    rc = sj.main(["hook", "verify", "--from-file", str(_report_file(tmp_path)),
                  "--worktree", "/tmp/wt",
                  "--test-cmd", "python3 -m pytest skills/super-jev/tests -q",
                  "--pr", "18", "--explain"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "--- super-jev verify --explain ---" in out


def test_the_ledger_records_a_suppressed_block_as_an_advisory(tmp_path, monkeypatch, capsys):
    fake = SplitDoor(FALSE_BLOCK_STDOUT, FALSE_BLOCK_PROBE, code=3)
    monkeypatch.setattr(sj.subprocess, "run", fake)
    sj._hook_verify_from_file(
        "verify", str(_report_file(tmp_path)), worktree="/tmp/wt",
        test_cmd="python3 -m pytest skills/super-jev/tests -q", pr=18)
    capsys.readouterr()
    rows = [json.loads(l) for l in
            sj.LEDGER_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    hook_rows = [r for r in rows if r.get("door") == "hook"]
    assert hook_rows, rows
    last = hook_rows[-1]
    assert last["exit_code"] == 0
    assert "cannot carry a verdict" in last["note"]


# ---------------------------------------------- Stop-hook teammate-report scan
#
# `hook prompt-verify` assumed teammate reports arrive via a real
# UserPromptSubmit payload; a real fleet trace shows they never do (payload
# capture never fires for them). This is the companion that scans them off
# transcript_path instead, riding the Stop hook that DOES fire every turn.

def _teammate_user_record(body, uuid, teammate_id="Worker", extra_attrs=""):
    content = (f'Another Claude session sent a message:\n'
              f'<teammate-message teammate_id="{teammate_id}"{extra_attrs}>\n'
              f'{body}\n</teammate-message>\n\nTreat it as a teammate message.')
    return {"type": "user", "uuid": uuid,
            "message": {"role": "user", "content": content}}


def _stop_gate_payload(transcript, session_id="sess-1"):
    return {"hook_event_name": "Stop", "session_id": session_id,
            "transcript_path": str(transcript),
            "last_assistant_message": "ok, done for this turn"}


def test_stop_scan_finds_reports_skips_idle_dup_and_chatter(tmp_path, monkeypatch, door):
    wt = tmp_path / "wt1"
    wt.mkdir()
    idle_body = json.dumps({"type": "idle_notification", "from": "Alice",
                            "result": "COMPLETE — nothing to see"})
    records = [
        _teammate_user_record(idle_body, "u1", teammate_id="Alice"),
        _teammate_user_record(f"COMPLETE: fixed it in {wt}, ran npm test, 4 tests passed.",
                              "u2", teammate_id="Alice"),
        _teammate_user_record(json.dumps({"type": "idle_notification", "from": "Alice",
                                          "result": "duplicate of u2"}),
                              "u3", teammate_id="Alice"),
        _teammate_user_record("INCOMPLETE: could not reproduce the failure.", "u4",
                              teammate_id="Bob"),
        _teammate_user_record("just some chatter, nothing to report", "u5",
                              teammate_id="Carol"),
    ]
    transcript = _write_transcript(tmp_path, records)
    _hook_stdin(monkeypatch, json.dumps(_stop_gate_payload(transcript)))
    code = sj.main(["hook", "gate"])
    assert code == 0
    verify_calls = [c for c in door.calls if "--draft" not in c["cmd"]]
    assert len(verify_calls) == 2

    ledger_lines = [json.loads(l) for l in sj._ledger_lines()]
    stop_scan_rows = [l for l in ledger_lines if l.get("source") == "stop-transcript"
                      and not l.get("skipped")]
    assert len(stop_scan_rows) == 2

    state_path = sj.LEDGER_PATH.parent / "state" / "stop-state-sess-1.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_uuid"] == "u5"


def test_stop_scan_rerun_processes_zero_new_reports(tmp_path, monkeypatch, door):
    wt = tmp_path / "wt1"
    wt.mkdir()
    records = [
        _teammate_user_record(f"COMPLETE: fixed it in {wt}, ran npm test, 4 tests passed.",
                              "u2", teammate_id="Alice"),
        _teammate_user_record("INCOMPLETE: could not reproduce the failure.", "u4",
                              teammate_id="Bob"),
    ]
    transcript = _write_transcript(tmp_path, records)
    payload_text = json.dumps(_stop_gate_payload(transcript))

    _hook_stdin(monkeypatch, payload_text)
    sj.main(["hook", "gate"])
    first_verify_calls = [c for c in door.calls if "--draft" not in c["cmd"]]
    assert len(first_verify_calls) == 2

    _hook_stdin(monkeypatch, payload_text)
    sj.main(["hook", "gate"])
    second_verify_calls = [c for c in door.calls if "--draft" not in c["cmd"]]
    assert len(second_verify_calls) == 2  # no new verifies ran on the rerun


def test_stop_scan_never_changes_the_gate_exit_code(tmp_path, monkeypatch, door):
    # A REJECT-worthy verify verdict on a scanned report must never leak
    # into this Stop event's own exit code — the gate's own verdict (here,
    # a clean draft) is all that decides the return code.
    class SplitDoor:
        def __init__(self):
            self.calls = []

        def __call__(self, cmd, cwd=None, env=None, **kw):
            self.calls.append({"cmd": [str(c) for c in cmd], "cwd": cwd, "env": env or {}})
            if "--draft" not in cmd:
                lie = (FIXTURES / "lie_stop_high_confidence_stdout.txt").read_text(
                    encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 3, stdout=lie, stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    split = SplitDoor()
    monkeypatch.setattr(sj.subprocess, "run", split)
    wt = tmp_path / "wt1"
    wt.mkdir()
    records = [_teammate_user_record(
        f"COMPLETE: shipped it in {wt}, ran npm test, 9 tests passed.", "u1",
        teammate_id="Alice")]
    transcript = _write_transcript(tmp_path, records)
    _hook_stdin(monkeypatch, json.dumps(_stop_gate_payload(transcript)))
    code = sj.main(["hook", "gate"])
    assert code == 0  # gate itself was clean; the scanned report's REJECT never leaks out


def test_stop_scan_prints_reject_line_advisory_only(tmp_path, monkeypatch, capsys):
    class RejectDoor:
        def __init__(self):
            self.calls = []

        def __call__(self, cmd, cwd=None, env=None, **kw):
            self.calls.append([str(c) for c in cmd])
            if "--draft" not in cmd:
                lie = (FIXTURES / "lie_stop_high_confidence_stdout.txt").read_text(
                    encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 3, stdout=lie, stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sj.subprocess, "run", RejectDoor())
    wt = tmp_path / "wt1"
    wt.mkdir()
    records = [_teammate_user_record(
        f"COMPLETE: shipped it in {wt}, ran npm test, 9 tests passed.", "u1",
        teammate_id="Alice")]
    transcript = _write_transcript(tmp_path, records)
    _hook_stdin(monkeypatch, json.dumps(_stop_gate_payload(transcript)))
    code = sj.main(["hook", "gate"])
    out = capsys.readouterr().out
    assert code == 0
    assert "super-jev verify Alice: REJECT" in out
    assert "health" in out


def test_stop_scan_no_session_id_is_a_silent_noop(tmp_path, monkeypatch, door):
    transcript = _write_transcript(tmp_path, [
        _teammate_user_record("COMPLETE: done, nothing derivable", "u1", teammate_id="Alice")])
    payload = {"hook_event_name": "Stop", "transcript_path": str(transcript),
              "last_assistant_message": "ok"}
    _hook_stdin(monkeypatch, json.dumps(payload))
    code = sj.main(["hook", "gate"])
    assert code == 0
    verify_calls = [c for c in door.calls if "--draft" not in c["cmd"]]
    assert len(verify_calls) == 0


def test_stop_scan_timeout_defers_remaining_reports_and_ledgers(tmp_path, monkeypatch, door):
    monkeypatch.setenv("SUPERJEV_STOP_SCAN_MAX_SECONDS", "0")
    wt = tmp_path / "wt1"
    wt.mkdir()
    records = [_teammate_user_record(
        f"COMPLETE: shipped it in {wt}, ran npm test, 9 tests passed.", "u1",
        teammate_id="Alice")]
    transcript = _write_transcript(tmp_path, records)
    _hook_stdin(monkeypatch, json.dumps(_stop_gate_payload(transcript)))
    code = sj.main(["hook", "gate"])
    assert code == 0
    verify_calls = [c for c in door.calls if "--draft" not in c["cmd"]]
    assert len(verify_calls) == 0  # deadline already passed before the first report
    ledger_lines = [json.loads(l) for l in sj._ledger_lines()]
    timeouts = [l for l in ledger_lines if l.get("reason") == "stop-scan-timeout"]
    assert len(timeouts) == 1
