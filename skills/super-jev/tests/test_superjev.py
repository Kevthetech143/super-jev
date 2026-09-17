#!/usr/bin/env python3
"""Offline tests for /super-jev. No network, no secrets, no live door.

Every wrapped door is replaced by a fake: subprocess.run is monkeypatched, so
what we assert is the exact argv this wrapper builds and the exit code it
propagates. Nothing here reaches TypeSafe, npm or git.

    python3 -m pytest ~/.claude/skills/super-jev/tests/test_superjev.py -q
"""
import importlib.util
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


class FakeDoor:
    """Records every call and returns a fixed exit code. Never runs anything."""

    def __init__(self, code=0):
        self.code = code
        self.calls = []

    def __call__(self, cmd, cwd=None, env=None, **kw):
        self.calls.append({"cmd": [str(c) for c in cmd], "cwd": cwd, "env": env or {}})
        return subprocess.CompletedProcess(cmd, self.code, stdout="", stderr="")

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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
