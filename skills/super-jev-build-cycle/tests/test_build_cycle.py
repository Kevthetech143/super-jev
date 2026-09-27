"""build_cycle.py against fake ask.py / dispatch.py (no Jev calls, no network)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "build_cycle.py"
STEPS = ["start", "target", "cause", "brief", "check-report", "prove", "review", "reply-check"]

FAKE = '''import json, os, sys
with open(os.environ["FAKE_SJ_LOG"], "a") as f:
    f.write(json.dumps([os.path.basename(__file__)] + sys.argv[1:]) + "\\n")
print(" 0.91  /notes/design.md  [notes]")
print("TRUE  (fake verdict)")
'''


@pytest.fixture
def env(tmp_path):
    """A skills folder laid out like any agent's: <skills>/super-jev + <skills>/super-jev-build-cycle."""
    skills = tmp_path / "some-agent" / "skills"
    (skills / "super-jev").mkdir(parents=True)
    for name in ("ask.py", "dispatch.py"):
        (skills / "super-jev" / name).write_text(FAKE)
    (skills / "super-jev-build-cycle").mkdir()
    shutil.copy(TOOL, skills / "super-jev-build-cycle" / "build_cycle.py")
    e = {k: v for k, v in os.environ.items() if k != "SUPERJEV_PRINCIPAL"}
    e["FAKE_SJ_LOG"] = str(tmp_path / "calls.jsonl")
    return {"tool": skills / "super-jev-build-cycle" / "build_cycle.py", "env": e, "tmp": tmp_path,
            "cycle": tmp_path / "cycle", "log": tmp_path / "calls.jsonl"}


def run(env, *args, principal="agent-1", extra_env=None):
    cmd = [sys.executable, str(env["tool"]), "--dir", str(env["cycle"])]
    if principal:
        cmd += ["--principal", principal]
    e = dict(env["env"], **(extra_env or {}))
    return subprocess.run(cmd + list(args), capture_output=True, text=True, env=e, cwd=env["tmp"])


def calls(env):
    if not env["log"].exists():
        return []
    return [json.loads(l) for l in env["log"].read_text().splitlines()]


def f(env, name, text):
    p = env["tmp"] / name
    p.write_text(text)
    return str(p)


def do_all(env, skip=()):
    report = f(env, "report.md", "worker says tests pass\n")
    claims = f(env, "claims.txt", "the fix is in ask.py\nthe tests pass\n")
    out = f(env, "new.txt", "all green\n")
    review = f(env, "review.md", "APPROVE\n")
    steps = {
        "start": ["start", "retry on timeout"],
        "target": ["target", "--goal", "asks time out", "--evidence", "lookup id abc123", "--ask"],
        "cause": ["cause", "--cause", "no retry", "--ask", "where are timeouts handled", "--trace", "last"],
        "brief": ["brief"],
        "check-report": ["check-report", "--report", report, "--claims", claims],
        "prove": ["prove", "--cmd", "pytest tests/test_x.py", "--output", out],
        "review": ["review", "--reviewer", "rev-a", "--verdict", "approve", "--file", review, "--claims", claims],
        "reply-check": ["reply-check", "--claims", claims],
    }
    for s in STEPS:
        if s in skip:
            continue
        r = run(env, *steps[s])
        assert r.returncode == 0, (s, r.stdout, r.stderr)


def mark_all(env, verdict="helped"):
    for line in (env["cycle"] / "uses.jsonl").read_text().splitlines():
        uid = json.loads(line)["id"]
        assert run(env, "mark", uid, verdict).returncode == 0


def test_principal_required_never_guessed(env):
    r = run(env, "start", "x", principal=None)
    assert r.returncode == 2 and "--principal" in r.stderr
    assert calls(env) == []
    r = run(env, "start", "x", principal="bad name")
    assert r.returncode == 2 and calls(env) == []


def test_principal_from_env(env):
    r = run(env, "start", "x", principal=None, extra_env={"SUPERJEV_PRINCIPAL": "agent-env"})
    assert r.returncode == 0, r.stderr
    assert all(c[c.index("--principal") + 1] == "agent-env" for c in calls(env))


def test_start_runs_four_asks_from_sibling_folder(env):
    r = run(env, "start", "retry on timeout", "--project", "demo")
    assert r.returncode == 0, r.stderr
    cs = calls(env)
    assert len(cs) == 4 and all(c[0] == "ask.py" and c[1:3] == ["--principal", "agent-1"] for c in cs)
    assert all("retry on timeout in demo" in c[-1] for c in cs)
    receipt = (env["cycle"] / "01-start.md").read_text()
    assert "0.91  /notes/design.md" in receipt and "- idea: retry on timeout" in receipt


def test_super_jev_missing_is_named(env):
    shutil.rmtree(env["tool"].parent.parent / "super-jev")
    r = run(env, "start", "x")
    assert r.returncode == 2 and "Super Jev not found" in r.stderr


def test_brief_prints_start_hits(env):
    run(env, "start", "retry on timeout")
    r = run(env, "brief")
    assert r.returncode == 0
    assert "BRIEFING" in r.stdout and "/notes/design.md" in r.stdout
    assert (env["cycle"] / "04-brief.md").exists()


def test_check_report_runs_verify_and_claims(env):
    report = f(env, "report.md", "done\n")
    claims = f(env, "claims.txt", "a\nb\n")
    assert run(env, "check-report", "--report", report, "--claims", claims).returncode == 0
    cs = calls(env)
    assert cs[0][:2] == ["dispatch.py", "verify"] and cs[0][2] == report
    assert cs[1][0] == "ask.py" and "--claims-file" in cs[1]


def test_prove_needs_existing_output(env):
    r = run(env, "prove", "--cmd", "pytest", "--output", str(env["tmp"] / "nope.txt"))
    assert r.returncode == 2 and not (env["cycle"] / "06-prove.md").exists()


def test_close_refuses_missing_and_names_them(env):
    do_all(env, skip=("prove", "review"))
    mark_all(env)
    r = run(env, "close")
    assert r.returncode == 1
    assert "prove" in r.stdout and "review" in r.stdout and "start" not in r.stdout.split("for:")[1]
    assert not (env["cycle"] / "summary.md").exists()


def test_close_refuses_empty_receipt(env):
    do_all(env)
    mark_all(env)
    (env["cycle"] / "06-prove.md").write_text("")
    r = run(env, "close")
    assert r.returncode == 1 and "prove" in r.stdout


def test_close_refuses_unmarked_uses(env):
    do_all(env)
    r = run(env, "close")
    assert r.returncode == 1 and "unmarked" in r.stdout and "u1" in r.stdout


def test_close_passes_with_all_and_logs_each_use(env):
    do_all(env)
    mark_all(env, "neutral")
    log = env["tmp"] / "elsewhere" / "uses.log"
    r = run(env, "close", "--log", str(log))
    assert r.returncode == 0, r.stdout
    n_uses = len((env["cycle"] / "uses.jsonl").read_text().splitlines())
    lines = log.read_text().splitlines()
    assert len(lines) == n_uses and all("\tneutral\t" in l for l in lines)
    assert "Super Jev uses:" in (env["cycle"] / "summary.md").read_text()


def test_close_default_log_in_cycle_dir(env):
    do_all(env)
    mark_all(env)
    assert run(env, "close").returncode == 0
    assert (env["cycle"] / "super-jev-uses.log").read_text().count("\thelped\t") >= 4


def test_skip_is_recorded_and_shown_at_close(env):
    do_all(env, skip=("review",))
    r = run(env, "skip", "review", "--reason", "solo fix, no reviewer free")
    assert r.returncode == 0
    mark_all(env)
    r = run(env, "close")
    assert r.returncode == 0, r.stdout
    assert "review: SKIPPED (solo fix, no reviewer free)" in (env["cycle"] / "summary.md").read_text()


def test_skip_needs_known_step_and_reason(env):
    assert run(env, "skip", "nosuch", "--reason", "x").returncode == 2
    assert run(env, "skip", "review", "--reason", " ").returncode == 2


def test_mark_rejects_unknown_use(env):
    assert run(env, "mark", "u9", "helped").returncode == 2
