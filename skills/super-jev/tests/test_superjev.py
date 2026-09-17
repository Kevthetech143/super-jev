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
    """A fake super-jev checkout carrying both npm scripts."""
    r = tmp_path / "super-jev"
    r.mkdir()
    (r / "package.json").write_text(json.dumps(
        {"scripts": {"sweep": "node src/sweep-cli.ts",
                     "bench:live": "node bench/live-measure.ts"}}), encoding="utf-8")
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


def test_ask_routed_to_an_unbuilt_door_exits_6(capsys):
    code = sj.main(["ask", "is it safe to pay this invoice"])
    out = capsys.readouterr().out
    assert code == sj.NOT_BUILT
    assert "ask -> permit" in out
    assert "wishlist item 5" in out


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

@pytest.mark.parametrize("argv,item,title", [
    (["permit", "snap.png", "--action", "click pay"], 5, "ACTION PERMIT"),
    (["chain", "spec.json", "ticket.md"], 4, "EVIDENCE CHAIN"),
    (["fetch", "which skill posts to x"], 6, "FETCH LAYER"),
])
def test_unbuilt_doors_exit_6_and_name_their_wishlist_item(argv, item, title, capsys):
    code = sj.main(argv)
    out = capsys.readouterr().out
    assert code == 6
    assert f"not built yet — wishlist item {item} ({title})" in out
    assert out.strip().splitlines()[1].startswith(f"{item}.")


def test_chain_names_the_module_that_already_exists(capsys):
    sj.main(["chain", "spec.json"])
    assert "src/enhance/evidence.ts exists, no CLI" in capsys.readouterr().out


def test_unbuilt_door_survives_a_missing_wishlist(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sj, "WISHLIST", tmp_path / "gone.md")
    code = sj.main(["fetch", "which tool"])
    assert code == 6
    assert "not readable" in capsys.readouterr().out


# ------------------------------------------------------------ status

def test_status_shape(repo, capsys, monkeypatch):
    monkeypatch.setattr(sj, "harness_commit", lambda r: "abc1234")
    code = sj.main(["status"])
    out = capsys.readouterr().out
    assert code == 0
    for door_name in ("gate", "verify", "sweep", "bench", "permit", "chain",
                      "fetch", "ask", "status"):
        assert door_name in out
    assert out.count("NOT BUILT") == 3
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
    assert out.count("MISSING SCRIPT") == 2


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


@pytest.mark.parametrize("code,expect", [(0, 0), (3, 0), (4, 2), (2, 0), (5, 0)])
def test_hook_verify_maps_every_exit_code_and_never_returns_3_4_5(tmp_path, monkeypatch,
                                                                   capsys, code, expect):
    monkeypatch.setattr(sj.subprocess, "run", FakeDoor(code))
    _hook_stdin(monkeypatch, json.dumps({"report": "the worker is done"}))
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


def test_hook_gate_with_no_evidence_fails_open_instead_of_misreporting_a_block(
        monkeypatch, capsys, door):
    # The wrapped gate tool requires evidence files as positional args; run
    # it with none and it exits on its own usage error, which happens to be
    # the same number this shim maps to "block". A payload naming no
    # evidence must fail open rather than misreport that as a REJECT.
    _hook_stdin(monkeypatch, json.dumps({"draft": "a claim with nothing to check it against"}))
    code = sj.main(["hook", "gate"])
    out, err = capsys.readouterr()
    assert code == 0
    assert out == ""
    assert err == ""
    assert not door.calls  # the wrapped tool was never even invoked


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
    assert out.count("script present, npm not runnable") == 2
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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
