"""build_cycle.py against fake ask.py / dispatch.py (no Jev calls, no network)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "build_cycle.py"
STEPS = ["preflight", "start", "target", "cause", "brief", "check-report", "prove", "review", "reply-check",
         "learn"]

FAKE = '''import json, os, sys
if "--preflight" in sys.argv:  # an older Super Jev has no --preflight unless FAKE_CORE holds its JSON answer
    if os.environ.get("FAKE_CORE"):
        print(os.environ["FAKE_CORE"])
        sys.exit(1 if json.loads(os.environ["FAKE_CORE"])["problems"] else 0)
    print("usage: ...")
    sys.exit(2)
if "--status" in sys.argv:  # free status: never logged as a Super Jev use
    print("Connections for x (registered snapshots, not a freshness guarantee):")
    for row in os.environ.get("FAKE_STATUS", "notes: ready").split(";"):
        print("  " + row)
    sys.exit(int(os.environ.get("FAKE_STATUS_EXIT", "0")))
with open(os.environ["FAKE_SJ_LOG"], "a") as f:
    f.write(json.dumps([os.path.basename(__file__)] + sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_WEAK"):
    print(" 0.62  /notes/other.md  [notes]  (possible: word-search match, answer not confirmed)")
else:
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
    (skills / "super-jev" / "prepare-cache").mkdir()
    proj = tmp_path / "project"
    (proj / "src").mkdir(parents=True)
    (skills / "super-jev" / "prepare-cache" / "notes-report.json").write_text(
        json.dumps({"pointer": "notes", "roots": [str(tmp_path)],
                    "approved": [str(proj / "src" / "a.py"), str(proj / "readme.md")]}))
    (skills / "super-jev-build-cycle").mkdir()
    shutil.copy(TOOL, skills / "super-jev-build-cycle" / "build_cycle.py")
    e = {k: v for k, v in os.environ.items() if k != "SUPERJEV_PRINCIPAL"}
    e["FAKE_SJ_LOG"] = str(tmp_path / "calls.jsonl")
    return {"tool": skills / "super-jev-build-cycle" / "build_cycle.py", "env": e, "tmp": tmp_path,
            "cycle": tmp_path / "cycle", "log": tmp_path / "calls.jsonl", "proj": proj}


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
    note = env["proj"] / "lesson.md"
    note.write_text("retries need a cap\n")
    steps = {
        "preflight": ["preflight", "--project-dir", str(env["proj"] / "src")],
        "learn": ["learn", "--note", str(note), "--fact", "how many retries", "three"],
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
    run(env, "preflight", principal=None, extra_env={"SUPERJEV_PRINCIPAL": "agent-env"})
    r = run(env, "start", "x", principal=None, extra_env={"SUPERJEV_PRINCIPAL": "agent-env"})
    assert r.returncode == 0, r.stderr
    assert all(c[c.index("--principal") + 1] == "agent-env" for c in calls(env))


def test_start_runs_four_asks_from_sibling_folder(env):
    run(env, "preflight")
    r = run(env, "start", "retry on timeout", "--project", "demo")
    assert r.returncode == 0, r.stderr
    cs = calls(env)
    assert len(cs) == 4 and all(c[0] == "ask.py" and c[1:3] == ["--principal", "agent-1"] for c in cs)
    assert all("retry on timeout in demo" in c[-1] for c in cs)
    receipt = (env["cycle"] / "02-start.md").read_text()
    assert "0.91  /notes/design.md" in receipt and "- idea: retry on timeout" in receipt


def test_super_jev_missing_is_named(env):
    shutil.rmtree(env["tool"].parent.parent / "super-jev")
    r = run(env, "preflight")
    assert r.returncode == 2 and "Super Jev not found" in r.stderr


def test_brief_prints_start_hits(env):
    run(env, "preflight")
    run(env, "start", "retry on timeout")
    r = run(env, "brief")
    assert r.returncode == 0
    assert "BRIEFING" in r.stdout and "/notes/design.md" in r.stdout
    assert (env["cycle"] / "05-brief.md").exists()


def test_check_report_runs_verify_and_claims(env):
    report = f(env, "report.md", "done\n")
    claims = f(env, "claims.txt", "a\nb\n")
    assert run(env, "check-report", "--report", report, "--claims", claims).returncode == 0
    cs = calls(env)
    assert cs[0][:2] == ["dispatch.py", "verify"] and cs[0][2] == report
    assert cs[1][0] == "ask.py" and "--claims-file" in cs[1]


def test_prove_needs_existing_output(env):
    r = run(env, "prove", "--cmd", "pytest", "--output", str(env["tmp"] / "nope.txt"))
    assert r.returncode == 2 and not (env["cycle"] / "07-prove.md").exists()


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
    (env["cycle"] / "07-prove.md").write_text("")
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


# --- preflight, new ground, learn, unskippable steps ---

def test_preflight_ready_writes_receipt_and_costs_nothing(env):
    r = run(env, "preflight", "--project-dir", str(env["proj"]))
    assert r.returncode == 0, r.stderr
    assert "preflight READY" in r.stdout and "project folder connected" in r.stdout
    assert (env["cycle"] / "01-preflight.md").exists()
    assert calls(env) == []  # status is free, never a logged use


def test_preflight_stale_connection_is_a_named_warning(env):
    r = run(env, "preflight", extra_env={"FAKE_STATUS": "notes: ready;old-notes: stale (preparation-required)"})
    assert r.returncode == 0 and "WARNING old-notes is stale" in r.stdout


def test_preflight_broken_connection_writes_no_receipt(env):
    r = run(env, "preflight", extra_env={"FAKE_STATUS": "notes: ready;bad-notes: error"})
    assert r.returncode == 2 and "bad-notes is not ready" in r.stderr
    assert not (env["cycle"] / "01-preflight.md").exists()


def test_preflight_unconnected_project_is_refused_with_fix(env):
    other = env["tmp"] / "elsewhere"
    other.mkdir()
    r = run(env, "preflight", "--project-dir", str(other))
    assert r.returncode == 2 and "not on Super Jev's shelves" in r.stderr and "super-jev-connect" in r.stderr


def test_preflight_status_failure_is_not_ready(env):
    r = run(env, "preflight", extra_env={"FAKE_STATUS": "", "FAKE_STATUS_EXIT": "1"})
    assert r.returncode == 2 and "status unavailable" in r.stderr


def test_preflight_skill_search_runs_dispatch(env):
    r = run(env, "preflight", "--skill", "turns meeting notes into tasks")
    assert r.returncode == 0, r.stderr
    cs = calls(env)
    assert len(cs) == 1 and cs[0][:2] == ["dispatch.py", "skills"]


def test_start_requires_preflight(env):
    r = run(env, "start", "x")
    assert r.returncode == 2 and "preflight first" in r.stderr and calls(env) == []
    assert run(env, "skip", "preflight", "--reason", "offline box").returncode == 0
    assert run(env, "start", "x").returncode == 0


def test_start_flags_new_ground_when_only_weak_hits(env):
    run(env, "preflight")
    r = run(env, "start", "quantum widget", extra_env={"FAKE_WEAK": "1"})
    assert r.returncode == 0 and "NEW GROUND" in r.stdout
    assert "- new ground: YES" in (env["cycle"] / "02-start.md").read_text()


def test_start_known_topic_is_not_new_ground(env):
    run(env, "preflight")
    r = run(env, "start", "retry on timeout")
    assert "NEW GROUND" not in r.stdout
    assert "new ground" not in (env["cycle"] / "02-start.md").read_text()


@pytest.mark.parametrize("step", ["start", "check-report", "reply-check"])
def test_unskippable_steps(env, step):
    r = run(env, "skip", step, "--reason", "busy")
    assert r.returncode == 2 and "cannot be skipped" in r.stderr


def test_learn_needs_something_to_teach(env):
    assert run(env, "learn").returncode == 2


def test_learn_refuses_note_outside_connected_folders(env):
    stray = env["tmp"] / "stray.md"
    stray.write_text("x\n")
    r = run(env, "learn", "--note", str(stray))
    assert r.returncode == 2 and "not on Super Jev's shelves" in r.stderr


def test_learn_fact_calls_add(env):
    r = run(env, "learn", "--fact", "how many retries", "three", "--source", str(env["proj"]))
    assert r.returncode == 0, r.stderr
    c = calls(env)[0]
    assert c[0] == "ask.py" and "--add" in c and c[c.index("--add") + 1:c.index("--add") + 3] == ["how many retries", "three"]


def test_close_requires_learn(env):
    do_all(env, skip=("learn",))
    mark_all(env)
    r = run(env, "close")
    assert r.returncode == 1 and "learn" in r.stdout


def test_preflight_root_alone_is_not_connected(env):
    """A connection rooted above the folder but listing no file inside it does not count."""
    lone = env["tmp"] / "lone"
    lone.mkdir()
    r = run(env, "preflight", "--project-dir", str(lone))
    assert r.returncode == 2 and "not on Super Jev's shelves" in r.stderr


def core(verdict="READY", problems=(), warnings=(), **extra):
    return json.dumps(dict(verdict=verdict, problems=list(problems), warnings=list(warnings), folders=[
        {"folder": "/p", "connected": 2, "connectable": 3}], **extra))


def test_preflight_uses_super_jevs_own_preflight_when_present(env):
    r = run(env, "preflight", extra_env={"FAKE_CORE": core("READY WITH WARNINGS", warnings=["2 of 3 connected"])})
    assert r.returncode == 0, r.stderr
    assert "Super Jev's own preflight" in r.stdout and "2 of 3 connectable files connected" in r.stdout
    assert "Super Jev preflight (json)" in (env["cycle"] / "01-preflight.md").read_text()


def test_core_not_ready_blocks_with_its_problems(env):
    r = run(env, "preflight", extra_env={"FAKE_CORE": core("NOT READY", problems=["bad is not ready: error"])})
    assert r.returncode == 2 and "bad is not ready" in r.stderr
    assert not (env["cycle"] / "01-preflight.md").exists()


def test_core_skill_search_is_a_marked_use(env):
    r = run(env, "preflight", "--skill", "posts things",
            extra_env={"FAKE_CORE": core(existing_skills=["x-poster"])})
    assert r.returncode == 0 and "x-poster" in r.stdout
    uses = (env["cycle"] / "uses.jsonl").read_text().splitlines()
    assert len(uses) == 1 and "existing skill for: posts things" in uses[0]


def _repo_with_change(env):
    """A git repo whose origin/main is the first commit, plus one committed and one uncommitted change."""
    repo = env["tmp"] / "repo"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo)] + list(a), check=True, capture_output=True)
    g("init", "-q"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n"); g("add", "."); g("commit", "-qm", "base")
    g("update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "a.py").write_text("x = 2\n"); g("commit", "-qam", "change")
    (repo / "b.py").write_text("y = 3\n"); g("add", "b.py")
    return repo


def test_reply_check_with_worktree_checks_claims_against_the_diff(env):
    repo = _repo_with_change(env)
    claims = f(env, "claims.txt", "x is now 2\ny is 3\n")
    r = run(env, "reply-check", "--claims", claims, "--worktree", str(repo))
    assert r.returncode == 0, r.stderr
    c = calls(env)[-1]
    assert c[:2] == ["dispatch.py", "check"] and c.count("--claim") == 2 and "ask.py" not in c[0]
    patch = Path(c[2]).read_text()
    assert "x = 2" in patch and "y = 3" in patch  # committed and uncommitted changes both reach Super Jev


def test_evidence_files_are_checked_directly(env):
    ev = f(env, "notes.md", "the answer is 42\n")
    claims = f(env, "claims.txt", "the answer is 42\n")
    report = f(env, "report.md", "done\n")
    assert run(env, "check-report", "--report", report, "--claims", claims, "--evidence", ev).returncode == 0
    c = calls(env)[-1]
    assert c[:3] == ["dispatch.py", "check", ev] and c[-2:] == ["--claim", "the answer is 42"]


def test_worktree_without_changes_is_refused_not_silently_skipped(env):
    repo = env["tmp"] / "clean"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo)] + list(a), check=True, capture_output=True)
    g("init", "-q"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (repo / "a.py").write_text("x\n"); g("add", "."); g("commit", "-qm", "base")
    g("update-ref", "refs/remotes/origin/main", "HEAD")
    r = run(env, "reply-check", "--claims", f(env, "c.txt", "x\n"), "--worktree", str(repo))
    assert r.returncode != 0 and "no changes" in (r.stdout + r.stderr)
