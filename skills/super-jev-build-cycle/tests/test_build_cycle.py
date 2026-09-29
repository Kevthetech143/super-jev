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
        "review": ["review", "--reviewer", "rev-a", "--verdict", "SHIP looks right", "--file", review, "--claims", claims],
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


def test_start_asks_one_question_plus_your_own(env):
    run(env, "preflight")
    r = run(env, "start", "retry on timeout", "--project", "demo", "--ask", "where are timeouts retried")
    assert r.returncode == 0, r.stderr
    cs = calls(env)
    assert len(cs) == 2 and all(c[0] == "ask.py" and c[1:3] == ["--principal", "agent-1"] for c in cs)
    assert "retry on timeout in demo" in cs[0][-1] and cs[1][-1] == "where are timeouts retried"
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
    named = r.stdout.split("for:")[1]
    assert "review" in named and "prove" not in named and "start" not in named  # prove is optional now
    assert not (env["cycle"] / "summary.md").exists()


def test_close_refuses_empty_receipt(env):
    do_all(env)
    mark_all(env)
    (env["cycle"] / "08-review.md").write_text("")
    r = run(env, "close")
    assert r.returncode == 1 and "review" in r.stdout


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
    do_all(env, skip=("check-report",))
    r = run(env, "skip", "check-report", "--reason", "solo fix, no helper")
    assert r.returncode == 0
    mark_all(env)
    r = run(env, "close")
    assert r.returncode == 0, r.stdout
    assert "check-report: SKIPPED (solo fix, no helper)" in (env["cycle"] / "summary.md").read_text()


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


LONG_IDEA = ("Secret scan card_hit in prepare_bulk.py holds a whole file as card-like text when a line contains "
             "a spaced shipping tracking number; stop digit runs reading as card numbers without weakening the guard")


def test_start_long_idea_needs_a_topic_before_any_ask(env):
    run(env, "preflight")
    r = run(env, "start", LONG_IDEA)
    assert r.returncode == 2 and "--topic" in r.stderr and "at most 12 words" in r.stderr
    assert calls(env) == [] and not (env["cycle"] / "02-start.md").exists()


def test_start_asks_about_the_topic_and_keeps_the_whole_idea(env):
    run(env, "preflight")
    r = run(env, "start", LONG_IDEA, "--topic", "secret scan holding files over tracking numbers",
            "--project", "demo", "--ask", "how does card_hit work")
    assert r.returncode == 0, r.stderr
    cs = calls(env)
    assert [c[-1] for c in cs] == ["what have we already tried, decided or learned about secret scan holding "
                                   "files over tracking numbers in demo", "how does card_hit work"]
    receipt = (env["cycle"] / "02-start.md").read_text()
    assert f"- idea: {LONG_IDEA}\n" in receipt and "- topic: secret scan holding files over tracking numbers" in receipt
    assert run(env, "brief").returncode == 0  # brief still reads the whole idea back
    assert LONG_IDEA in (env["cycle"] / "05-brief.md").read_text()


@pytest.mark.parametrize("topic", ["", "   ", " ".join(["word"] * 13)])
def test_start_refuses_an_empty_or_long_topic(env, topic):
    run(env, "preflight")
    r = run(env, "start", "retry on timeout", "--topic", topic)
    assert r.returncode == 2 and ("cannot be empty" in r.stderr or "at most 12 words" in r.stderr)
    assert calls(env) == []


def test_start_multiline_idea_is_kept_on_one_line(env):
    run(env, "preflight")
    assert run(env, "start", "retry\non   timeout").returncode == 0
    assert "- idea: retry on timeout\n" in (env["cycle"] / "02-start.md").read_text()
    assert calls(env)[0][-1].endswith("about retry on timeout")


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


@pytest.mark.parametrize("step", ["start", "review", "reply-check"])
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


def test_review_needs_ship_or_fix_and_a_different_reviewer(env):
    rev = f(env, "rev.md", "SHIP\n")
    assert run(env, "review", "--reviewer", "agent-1", "--verdict", "SHIP", "--file", rev).returncode == 2
    assert run(env, "review", "--reviewer", "fresh", "--verdict", "looks fine", "--file", rev).returncode == 2
    assert run(env, "review", "--reviewer", "fresh", "--verdict", "SHIP ok", "--file", rev).returncode == 0


def test_close_refuses_while_latest_review_says_fix(env):
    do_all(env)
    rev = f(env, "rev2.md", "FIX: off by one\n")
    assert run(env, "review", "--reviewer", "fresh", "--verdict", "FIX off by one", "--file", rev).returncode == 0
    mark_all(env)
    r = run(env, "close")
    assert r.returncode == 1 and "FIX off by one" in r.stdout


def test_review_brief_carries_goal_and_diff(env):
    repo = _repo_with_change(env)
    run(env, "preflight"); run(env, "start", "make x two")
    r = run(env, "review-brief", "--worktree", str(repo), "--test-cmd", "pytest -q")
    assert r.returncode == 0, r.stderr
    brief = (env["cycle"] / "review-brief.md").read_text()
    assert "make x two" in brief and "x = 2" in brief and "pytest -q" in brief and "SHIP or FIX" in brief


# ---- Earn the Button (onboard) ----
def ob(env, *args):
    return run(env, "onboard", *args)


def cases(env, name, tag):
    rows = [{"kind": k, "q": f"{tag}-{k}"} for k in ("supported", "absent", "invalid")]
    return f(env, name, "\n".join(json.dumps(r) for r in rows) + "\n")


def results(env, name, n, passing):
    """A result file in the try248/eval60 text format: n lines, the first `passing` of them pass."""
    lines = [f"q{i:02d} tuned health rank 1 exit 0 5s | OUTCOME: found" if i < passing
             else f"q{i:02d} tuned health MISS exit 0 5s | x rank 1 not-found OK" for i in range(n)]
    return f(env, name, "\n".join(lines) + "\n")


def ids10(env):
    return f(env, "cases10-ids.jsonl", "".join(json.dumps({"id": f"q{i:02d}"}) + "\n" for i in range(10)))


def git_wt(env):
    wt = env["tmp"] / "wt"
    subprocess.run(["git", "init", "-q", "-b", "feat", str(wt)], check=True)
    return wt


def onboard_upto(env, upto):
    """Run onboarding steps in order up to and including `upto`; returns the last result."""
    contract = f(env, "contract.md", "# contract\n")
    dev, held = cases(env, "dev.jsonl", "dev"), cases(env, "held.jsonl", "held")
    wt = git_wt(env)
    tl, card = f(env, "timeline.md", "row: X works\n"), f(env, "card.md", "NOW: X works\n")
    do_review = lambda: (run(env, "review", "--reviewer", "rev", "--verdict", "SHIP ok",
                             "--file", f(env, "ans.md", "SHIP: reviewed branch feat")))
    steps = [("need", ["--who", "Kelvin asked twice", "--how-often", "about weekly now", "--fails", "csv files return not found"]),
             ("simplest", ["--answer", "no existing rule reads csv so widen nothing"]),
             ("promise", ["--line", "accepts csv; returns rows; failure is reported as not found",
                          "--approved", "Kelvin approved: 2026-09-29", "--contract", contract]),
             ("frozen", ["--dev", dev, "--heldout", held]),
             ("build", ["--worktree", str(wt), "--branch", "feat", "--deletes", "none: new path only"]),
             ("prove", lambda: ["--cases10-file", results(env, "c10.txt", 10, 10), "--cases10-ids", ids10(env),
                                "--eval60-before-file", results(env, "e0.txt", 60, 50),
                                "--eval60-file", results(env, "e1.txt", 60, 51), "--test-cmd", f"test -f {dev}"]),
             ("record", ["--timeline-row", "X works", "--timeline-file", tl,
                         "--card-now", "NOW: X works", "--card-file", card])]
    r = None
    for name, args in steps:
        if name == "build":
            do_review()
        r = ob(env, name, *(args() if callable(args) else args))
        if name == upto:
            return r
        assert r.returncode == 0, (name, r.stderr)
    return r


def test_onboard_full_path_is_earned(env):
    assert onboard_upto(env, "record").returncode == 0
    r = ob(env, "check")
    assert r.returncode == 0 and "EARNED" in r.stdout


def test_onboard_no_evidence_stops(env):
    r = ob(env, "need", "--who", "nobody", "--how-often", "none", "--fails", "unknown")
    assert r.returncode == 2 and "stop" in r.stderr
    assert not (env["cycle"] / "onboard-1-need.md").exists()


def test_onboard_steps_go_in_order(env):
    r = ob(env, "simplest", "--answer", "widen the csv rule instead of a new path")
    assert r.returncode == 2 and "'need' first" in r.stderr


def test_onboard_promise_needs_approval_and_all_three_parts(env):
    onboard_upto(env, "simplest")
    c = f(env, "c2.md", "x\n")
    r = ob(env, "promise", "--line", "accepts csv; returns rows; failure is reported", "--contract", c)
    assert r.returncode == 2 and "--approved" in r.stderr
    r = ob(env, "promise", "--line", "accepts csv", "--approved", "ok", "--contract", c)
    assert r.returncode == 2 and "failure" in r.stderr


def test_onboard_frozen_needs_all_kinds_and_separate_files(env):
    onboard_upto(env, "promise")
    bad = f(env, "bad.jsonl", json.dumps({"kind": "supported"}) + "\n")
    good = cases(env, "g.jsonl", "g")
    assert ob(env, "frozen", "--dev", bad, "--heldout", good).returncode == 2
    assert ob(env, "frozen", "--dev", good, "--heldout", good).returncode == 2


def test_onboard_edit_after_freeze_fails_check(env):
    onboard_upto(env, "record")
    dev = env["tmp"] / "dev.jsonl"
    dev.write_text(dev.read_text() + json.dumps({"kind": "supported", "q": "sneaky"}) + "\n")
    r = ob(env, "check")
    assert r.returncode == 1 and "changed" in r.stdout


def test_onboard_build_needs_ship_review_and_feature_branch(env):
    onboard_upto(env, "frozen")
    wt = git_wt(env)
    args = ["--worktree", str(wt), "--branch", "feat", "--deletes", "none: new path only"]
    r = ob(env, "build", *args)
    assert r.returncode == 2 and "review" in r.stderr
    run(env, "review", "--reviewer", "rev", "--verdict", "SHIP ok", "--file", f(env, "a.md", "SHIP on branch feat"))
    assert ob(env, "build", "--worktree", str(wt), "--branch", "main", "--deletes", "none: new path only").returncode == 2
    assert ob(env, "build", *args).returncode == 0


def test_onboard_prove_counts_result_files_and_runs_the_frozen_test(env):
    onboard_upto(env, "build")
    dev = str(env["tmp"] / "dev.jsonl")
    c10, e0, e1 = (results(env, n, k, p) for n, k, p in (("c10.txt", 10, 10), ("e0.txt", 60, 50), ("e1.txt", 60, 50)))
    ids = ids10(env)
    t = ["--cases10-ids", ids, "--test-cmd", f"test -f {dev}"]
    assert ob(env, "prove", "--cases10-file", results(env, "c9.txt", 10, 9), "--eval60-before-file", e0,
              "--eval60-file", e1, *t).returncode == 2
    assert ob(env, "prove", "--cases10-file", c10, "--eval60-before-file", e0,
              "--eval60-file", results(env, "e49.txt", 60, 49), *t).returncode == 2
    assert ob(env, "prove", "--cases10-file", results(env, "short.txt", 9, 9), "--eval60-before-file", e0,
              "--eval60-file", e1, *t).returncode == 2  # a file with 9 lines is not the 10 questions
    assert ob(env, "prove", "--cases10-file", c10, "--eval60-before-file", e0, "--eval60-file", e1,
              "--cases10-ids", ids, "--test-cmd", "true").returncode == 2  # test does not use the frozen file
    assert ob(env, "prove", "--cases10-file", c10, "--eval60-before-file", e0, "--eval60-file", e1,
              "--cases10-ids", ids, "--test-cmd", f"test -f {dev} && false").returncode == 2
    assert ob(env, "prove", "--cases10-file", c10, "--eval60-before-file", e0, "--eval60-file", e1, *t).returncode == 0
    pf = (env["cycle"] / "onboard-6-prove.md").read_text()
    assert "cases10 file sha256:" in pf and "eval60 file sha256:" in pf


def test_pass_rule_is_anchored_to_the_status_field_not_text_after_the_bar(env):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bc", TOOL)
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    ok = ["q01 rank 1 exit 0 5s | x", "q01 tuned health rank 3 exit 0 | x", "q20 not-found OK exit 4 | y",
          "q21 absent-OK exit 0 | y"]
    bad = ["q01 MISS exit 0 5s | rank 1", "q02 tuned health MISS exit 0 | not-found OK", "q03 exit 0 | rank 1",
           "q04 rank 6 exit 0", "q05 MISS exit rank 1", "q06 a b c rank 1"]
    assert all(bc.PASS_RE.match(l) for l in ok) and not any(bc.PASS_RE.match(l) for l in bad)


def test_onboard_result_ids_must_be_distinct_and_the_frozen_ten(env):
    onboard_upto(env, "build")
    dev = str(env["tmp"] / "dev.jsonl")
    e0, e1, ids = results(env, "e0.txt", 60, 50), results(env, "e1.txt", 60, 50), ids10(env)
    dup = f(env, "dup.txt", "\n".join("q00 rank 1 exit 0 | x" for _ in range(10)) + "\n")
    other = f(env, "other.txt", "\n".join(f"q{i + 50:02d} rank 1 exit 0 | x" for i in range(10)) + "\n")
    for bad in (dup, other):
        r = ob(env, "prove", "--cases10-file", bad, "--eval60-before-file", e0, "--eval60-file", e1,
               "--cases10-ids", ids, "--test-cmd", f"test -f {dev}")
        assert r.returncode == 2 and "ids" in r.stderr, bad


def test_onboard_eval60_files_must_differ_and_after_must_be_newer_than_build(env):
    import time
    onboard_upto(env, "build")
    dev = str(env["tmp"] / "dev.jsonl")
    c10, ids, e = results(env, "c10.txt", 10, 10), ids10(env), results(env, "e0.txt", 60, 50)
    args = ["--cases10-file", c10, "--cases10-ids", ids, "--test-cmd", f"test -f {dev}"]
    r = ob(env, "prove", *args, "--eval60-before-file", e, "--eval60-file", e)
    assert r.returncode == 2 and "different files" in r.stderr
    e1 = results(env, "e1.txt", 60, 50)
    old = time.time() - 3600
    os.utime(e1, (old, old))
    r = ob(env, "prove", *args, "--eval60-before-file", e, "--eval60-file", e1)
    assert r.returncode == 2 and "older than the build" in r.stderr


def test_onboard_review_verdict_comes_from_the_answer_file_and_must_follow_frozen(env):
    import time
    onboard_upto(env, "frozen")
    wt = git_wt(env)
    args = ["build", "--worktree", str(wt), "--branch", "feat", "--deletes", "none: new path only"]
    run(env, "review", "--reviewer", "rev", "--verdict", "SHIP typed", "--file", f(env, "fx.md", "FIX branch feat: broken"))
    r = ob(env, *args)
    assert r.returncode == 2 and "must start with SHIP" in r.stderr
    run(env, "review", "--reviewer", "rev", "--verdict", "SHIP ok", "--file", f(env, "sh.md", "SHIP branch feat"))
    old = time.time() - 3600
    os.utime(env["cycle"] / "onboard-4-frozen.md", (old + 7200, old + 7200))
    r = ob(env, *args)
    assert r.returncode == 2 and "older than the frozen step" in r.stderr
    os.utime(env["cycle"] / "onboard-4-frozen.md", (old, old))
    assert ob(env, *args).returncode == 0


def test_onboard_check_recounts_prove_files_and_reapplies_approval_and_branch_rules(env):
    import hashlib
    onboard_upto(env, "record")
    cyc = env["cycle"]
    orig = {n: (cyc / n).read_text() for n in ("onboard-6-prove.md", "onboard-3-promise.md", "onboard-5-build.md")}
    e1 = env["tmp"] / "e1.txt"
    e1.write_text("\n".join(f"q{i:02d} rank 1 | x" for i in range(30)) + "\n"
                  + "\n".join(f"q{i + 30:02d} MISS | x" for i in range(30)) + "\n")  # lower than before, 60 lines
    sha = hashlib.sha256(e1.read_bytes()).hexdigest()
    old_line = [l for l in orig["onboard-6-prove.md"].splitlines() if l.startswith("- eval60 file sha256:")][0]
    (cyc / "onboard-6-prove.md").write_text(orig["onboard-6-prove.md"].replace(old_line, f"- eval60 file sha256: {sha}"))
    r = ob(env, "check")
    assert r.returncode == 1 and "eval60 went down" in r.stdout
    (cyc / "onboard-6-prove.md").write_text(orig["onboard-6-prove.md"])
    (cyc / "onboard-3-promise.md").write_text(orig["onboard-3-promise.md"].replace("Kelvin approved: 2026-09-29", "sure"))
    assert "Kelvin approved" in ob(env, "check").stdout
    (cyc / "onboard-3-promise.md").write_text(orig["onboard-3-promise.md"])
    (cyc / "onboard-5-build.md").write_text(orig["onboard-5-build.md"].replace("- branch: feat", "- branch: main"))
    assert "build branch is main" in ob(env, "check").stdout


def test_onboard_approved_date_cannot_be_in_the_future(env):
    onboard_upto(env, "simplest")
    c = f(env, "c9.md", "x\n")
    r = ob(env, "promise", "--line", "accepts csv; returns rows; failure is reported", "--contract", c,
           "--approved", "Kelvin approved: 2999-01-01")
    assert r.returncode == 2 and "future" in r.stderr


def test_onboard_typed_or_nonfinite_scores_are_no_longer_accepted(env):
    onboard_upto(env, "build")
    r = ob(env, "prove", "--cases10", "10/10", "--eval60-before", "nan", "--eval60-after", "inf", "--test-cmd", "true")
    assert r.returncode == 2 and not (env["cycle"] / "onboard-6-prove.md").exists()


def test_onboard_cannot_redo_an_earlier_step_after_a_later_one(env):
    onboard_upto(env, "build")
    dev, held = str(env["tmp"] / "dev.jsonl"), str(env["tmp"] / "held.jsonl")
    sha = (env["cycle"] / "onboard-4-frozen.md").read_text()
    r = ob(env, "frozen", "--dev", dev, "--heldout", held)
    assert r.returncode == 2 and "cannot be redone" in r.stderr
    assert (env["cycle"] / "onboard-4-frozen.md").read_text() == sha


def test_onboard_approved_needs_kelvin_approved_and_a_date_or_quote(env):
    onboard_upto(env, "simplest")
    c = f(env, "c4.md", "x\n")
    line = ["promise", "--line", "accepts csv; returns rows; failure is reported", "--contract", c]
    for bad in ("yes", "2026-09-29", "Kelvin approved", "Kelvin approved: 2026-13-45", "Kelvin approved: sure"):
        assert ob(env, *line, "--approved", bad).returncode == 2, bad
    assert ob(env, *line, "--approved", 'Kelvin approved: "go ahead"').returncode == 0


def test_onboard_promise_must_be_a_whole_line_in_the_contract(env):
    onboard_upto(env, "record")
    c = env["tmp"] / "contract.md"
    entry = [l for l in c.read_text().splitlines() if l.startswith("- accepts")][0]
    c.write_text("# contract\n" + entry + " and more words\n")  # the line is only a substring now
    r = ob(env, "check")
    assert r.returncode == 1 and "contract" in r.stdout


def test_onboard_evidence_needs_three_real_words(env):
    for bad in ("x", "- - -", "n/a", "two words", "x x x"):
        r = ob(env, "need", "--who", bad, "--how-often", "about once weekly", "--fails", "csv returns not found")
        assert r.returncode == 2, bad
    onboard_upto(env, "promise")
    r = ob(env, "frozen", "--dev", cases(env, "d5.jsonl", "d5"), "--heldout", cases(env, "h5.jsonl", "h5"))
    assert r.returncode == 0
    wt = git_wt(env)
    run(env, "review", "--reviewer", "rev", "--verdict", "SHIP ok", "--file", f(env, "a5.md", "SHIP feat"))
    for bad in ("none", "n/a", "x x x"):
        assert ob(env, "build", "--worktree", str(wt), "--branch", "feat", "--deletes", bad).returncode == 2, bad


def test_onboard_review_must_be_newer_than_need_and_name_the_branch(env):
    import time
    onboard_upto(env, "frozen")
    wt = git_wt(env)
    args = ["build", "--worktree", str(wt), "--branch", "feat", "--deletes", "none: new path only"]
    run(env, "review", "--reviewer", "rev", "--verdict", "SHIP ok", "--file", f(env, "nb.md", "SHIP, looks fine"))
    r = ob(env, *args)
    assert r.returncode == 2 and "must name the branch" in r.stderr
    run(env, "review", "--reviewer", "rev", "--verdict", "SHIP ok", "--file", f(env, "nb2.md", "SHIP branch feat"))
    old = time.time() - 3600
    assert ob(env, *args).returncode == 0


def test_onboard_check_rereads_timeline_and_card_files(env):
    onboard_upto(env, "record")
    assert ob(env, "check").returncode == 0
    (env["tmp"] / "card.md").write_text("NOW: something else\n")
    r = ob(env, "check")
    assert r.returncode == 1 and "no longer in the card file" in r.stdout


def test_onboard_record_text_must_be_in_the_files(env):
    onboard_upto(env, "prove")
    tl, card = f(env, "t2.md", "nothing\n"), f(env, "c3.md", "NOW: y\n")
    r = ob(env, "record", "--timeline-row", "X works", "--timeline-file", tl, "--card-now", "NOW: y",
           "--card-file", card)
    assert r.returncode == 2 and "timeline" in r.stderr


def test_close_refuses_unearned_onboarding(env):
    onboard_upto(env, "need")
    r = run(env, "close")
    assert r.returncode == 1 and "onboarding not earned" in r.stdout
