#!/usr/bin/env python3
"""super-jev — the ONE front door for every check we run.

One ask, one door. This file is a set of thin wrappers: it never re-implements
a check. `gate` wraps a claim-gate command, `verify` wraps a report-verify
command, `sweep` and `bench` are npm scripts in the super-jev checkout.
Three more doors are named but not built yet; each one names its own wishlist
item and exits 6, so a missing tool tells you what is missing instead of
failing silently.

    python3 skills/super-jev/superjev.py <sub> ...

Subcommands: gate · verify · sweep · bench · permit · chain · fetch · ask · status

Exit codes: whatever the wrapped door returned. Plus 5 for a refusal by this
wrapper (missing input, missing door, unroutable ask) and 6 for NOT BUILT.

It never prints TYPESAFE_API_KEY, and it never reads a secret file.

Portable by default: the `gate` and `verify` doors run a command taken from
SUPERJEV_GATE_CMD / SUPERJEV_VERIFY_CMD when set, and otherwise fall back to
a fixed fleet-local path that is almost certainly absent on a fresh clone —
in that case the door names the missing path and the env var that replaces
it, rather than failing inside a subprocess. SUPERJEV_REPO controls where
`sweep` and `bench` look for their npm scripts, and defaults to this
checkout's own root, so a fresh clone needs no env at all for those two.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parent.parent  # skills/super-jev/superjev.py -> repo root

# Fleet-local fallbacks. Real on the machine this skill was authored on;
# almost certainly absent on a fresh clone, where SUPERJEV_GATE_CMD and
# SUPERJEV_VERIFY_CMD take over instead.
FLEET_JEV_LIB = HOME / ".claude/skills/jev-check/lib/jev.py"
FLEET_VERIFY_PY = HOME / ".claude/skills/worker-verify/verify.py"

GATE_CMD_ENV = "SUPERJEV_GATE_CMD"
VERIFY_CMD_ENV = "SUPERJEV_VERIFY_CMD"

# The wishlist ships in this repo, not in a private fleet path.
WISHLIST = REPO_ROOT / "docs" / "wishlist.md"
DEFAULT_REPO = REPO_ROOT

REFUSED = 5          # this wrapper refused: missing input or missing door
NOT_BUILT = 6        # the door is named in the wishlist and does not exist yet

# The three doors that are named but not built. item -> (title, why it is named)
UNBUILT = {
    "permit": (5, "ACTION PERMIT"),
    "chain": (4, "EVIDENCE CHAIN"),
    "fetch": (6, "FETCH LAYER"),
}
CHAIN_NOTE = "src/enhance/evidence.ts exists, no CLI"


# ---------------------------------------------------------------- plumbing

def repo_path():
    """The super-jev checkout. SUPERJEV_REPO wins, else this repo's own root."""
    return Path(os.environ.get("SUPERJEV_REPO") or DEFAULT_REPO).expanduser()


def door_cmd(env_var, fleet_path):
    """The argv prefix for a wrapped door: env override, else the fleet path.

    An env override is a full shell command (`SUPERJEV_GATE_CMD="python3
    /path/to/jev.py"`), split with shlex. Without an override this falls back
    to `sys.executable <fleet_path>`, which is only real on the machine this
    skill shipped from.
    """
    override = os.environ.get(env_var)
    if override:
        return shlex.split(override)
    return [sys.executable, str(fleet_path)]


def door_missing(env_var, fleet_path):
    """None if the door is reachable, else the line explaining why it is not.

    An env override is trusted outright — this wrapper does not resolve it
    against PATH. Without one, the fleet-local fallback path must exist on
    disk.
    """
    if os.environ.get(env_var):
        return None
    if fleet_path.exists():
        return None
    return (f"no door at {fleet_path}, and {env_var} is not set — set {env_var} "
            "to the command that runs it, or install it at that path")


def child_env():
    """The environment a wrapped door runs in.

    A copy of ours, so SSL_CERT_FILE and TYPESAFE_API_KEY pass through
    untouched. Neither is ever printed.
    """
    return dict(os.environ)


def run_door(cmd, cwd=None):
    """Run a wrapped door and give back its exit code.

    Output is not captured: the door's own table is the result, and it goes
    straight to the operator's terminal.
    """
    print("$ " + shlex.join(str(c) for c in cmd) + (f"   (in {cwd})" if cwd else ""))
    sys.stdout.flush()
    proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                          env=child_env())
    return proc.returncode


def refuse(line):
    print(f"super-jev: {line}", file=sys.stderr)
    return REFUSED


def npm_scripts(repo):
    """The script names in the checkout's package.json, or None if unreadable."""
    try:
        data = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scripts = data.get("scripts")
    return scripts if isinstance(scripts, dict) else {}


def need_script(repo, name):
    """Refuse with a clear line unless the checkout really has this script."""
    if not repo.is_dir():
        return refuse(f"no super-jev checkout at {repo} — set SUPERJEV_REPO to one")
    scripts = npm_scripts(repo)
    if scripts is None:
        return refuse(f"no readable package.json in {repo} — SUPERJEV_REPO is not a "
                      "super-jev checkout")
    if name not in scripts:
        return refuse(f"no `{name}` script in {repo}/package.json — set SUPERJEV_REPO "
                      f"to a checkout that has it (`git -C {repo} branch -a` shows which "
                      "branches carry it)")
    return None


def wishlist_line(item):
    """The wishlist's own sentence for an item, read from the file."""
    try:
        text = WISHLIST.read_text(encoding="utf-8")
    except OSError:
        return f"(wishlist not readable at {WISHLIST})"
    for line in text.splitlines():
        if line.strip().startswith(f"{item}."):
            return line.strip()
    return f"(no item {item} line in {WISHLIST})"


def not_built(sub):
    """Print the NOT BUILT refusal for one door and return exit 6."""
    item, title = UNBUILT[sub]
    note = f"; {CHAIN_NOTE}" if sub == "chain" else ""
    print(f"{sub}: not built yet — wishlist item {item} ({title}){note}")
    print(wishlist_line(item))
    print(f"wishlist: {WISHLIST}")
    return NOT_BUILT


# ---------------------------------------------------------------- gate

GATE_VERDICT = {
    0: "CLEAN — every claim is carried by the evidence at or above 0.80. Send it.",
    3: "READ — a claim is red or under 0.80. A human reads the source before you send.",
    2: "REJECT — a quoted span is not in the evidence. The citation is fabricated.",
}


def cmd_gate(a):
    bad = door_missing(GATE_CMD_ENV, FLEET_JEV_LIB)
    if bad is not None:
        return refuse(bad)
    if not a.draft and not a.claim:
        return refuse("gate needs --draft <file> or one or more --claim \"<text>\"")
    cmd = [*door_cmd(GATE_CMD_ENV, FLEET_JEV_LIB), *a.evidence, "--kit", "reply"]
    if a.draft:
        cmd += ["--draft", a.draft]
    for claim in a.claim or []:
        cmd += ["--claim", claim]
    code = run_door(cmd)
    print(f"\nVERDICT: {GATE_VERDICT.get(code, f'ERROR — jev-check exited {code}')}")
    return code


# ---------------------------------------------------------------- verify

VERIFY_VERDICT = {
    0: "CLEAN — every claim in the report is carried by the evidence.",
    3: "READ — a human must read the flagged claims before accepting this report.",
    4: "REJECT — the evidence DISPROVES a claim in the report.",
    2: "NO CHECKABLE CLAIMS — the report carries nothing the evidence can test.",
    5: "BAD USAGE — worker-verify refused the arguments.",
}


def cmd_verify(a):
    bad = door_missing(VERIFY_CMD_ENV, FLEET_VERIFY_PY)
    if bad is not None:
        return refuse(bad)
    cmd = [*door_cmd(VERIFY_CMD_ENV, FLEET_VERIFY_PY), a.report]
    if a.worktree:
        cmd += ["--worktree", a.worktree]
    if a.test_cmd:
        cmd += ["--test-cmd", a.test_cmd]
    if a.paths:
        cmd += ["--paths", *a.paths]
    if a.dry_run:
        cmd += ["--dry-run"]
    code = run_door(cmd)
    print(f"\nVERDICT: {VERIFY_VERDICT.get(code, f'ERROR — worker-verify exited {code}')}")
    return code


# ---------------------------------------------------------------- sweep

def cmd_sweep(a):
    repo = repo_path()
    bad = need_script(repo, "sweep")
    if bad is not None:
        return bad
    cmd = ["npm", "run", "sweep", "--", "--records", a.records,
           "--questions", a.questions, "--out", a.out]
    if a.budget is not None:
        cmd += ["--budget", str(a.budget)]
    if a.batch is not None:
        cmd += ["--batch", str(a.batch)]
    if a.gate is not None:
        cmd += ["--gate", str(a.gate)]
    if a.dry_run:
        cmd += ["--dry-run"]
    if a.stub:
        cmd += ["--stub"]
    if not a.dry_run and not a.stub and not os.environ.get("TYPESAFE_API_KEY"):
        print("super-jev: a live sweep needs TYPESAFE_API_KEY. Re-run with --dry-run "
              "or --stub to stay offline.", file=sys.stderr)
    return run_door(cmd, cwd=repo)


# ---------------------------------------------------------------- bench

def cmd_bench(a):
    repo = repo_path()
    bad = need_script(repo, "bench:live")
    if bad is not None:
        return bad
    live = not (a.dry_run or a.stub)
    if live and not os.environ.get("TYPESAFE_API_KEY"):
        print("super-jev: refusing a live bench — TYPESAFE_API_KEY is not set in the "
              "environment. Printing the dry-run plan instead; no network, no cost.")
        code = run_door(["npm", "run", "bench:live", "--", "--dry-run"], cwd=repo)
        return code if code else REFUSED
    flag = ["--dry-run"] if a.dry_run else (["--stub"] if a.stub else [])
    return run_door(["npm", "run", "bench:live", "--", *flag] if flag
                    else ["npm", "run", "bench:live"], cwd=repo)


# ---------------------------------------------------------------- ask

# One plain sentence in, one subcommand out. No model call: the router is a
# keyword table, so it is free, instant and readable.
ROUTES = [
    ("gate", ["draft", "reply", "say", "said", "says", "claim", "claims"]),
    ("verify", ["report", "worker", "done", "pushed"]),
    ("sweep", ["pile", "records", "every line", "all of these"]),
    ("permit", ["click", "pay", "send", "delete", "safe"]),
    ("chain", ["ticket", "order", "policy", "chain"]),
    ("fetch", ["which skill", "tool", "route"]),
]


def route(sentence):
    """(subcommand, matched words) for one sentence, or (None, []).

    Most keywords win, ties broken by the order above — the order the doors
    are listed in, so `gate` beats `permit` on "send this draft".
    """
    low = sentence.lower()
    best, best_hits = None, []
    for sub, words in ROUTES:
        hits = [w for w in words
                if (w in low if " " in w
                    else re.search(rf"\b{re.escape(w)}\b", low))]
        if len(hits) > len(best_hits):
            best, best_hits = sub, hits
    return best, best_hits


def existing_paths(sentence):
    """Every token in the sentence that is a real path on disk, in order."""
    found = []
    for token in re.split(r"\s+", sentence.strip()):
        token = token.strip(".,;:!?\"'()[]")
        if token and Path(token).expanduser().exists():
            found.append(str(Path(token).expanduser()))
    return found


def cmd_ask(a):
    sentence = a.sentence
    sub, hits = route(sentence)
    if sub is None:
        print("super-jev: I cannot route that sentence. The doors are:")
        for name, words in ROUTES:
            print(f"  {name:<7} say any of: {', '.join(words)}")
        print("  status  which doors are live")
        return REFUSED
    print(f"ask -> {sub}   (matched: {', '.join(hits)})")

    if sub in UNBUILT:
        return not_built(sub)

    paths = existing_paths(sentence)
    if sub == "gate":
        if len(paths) < 2:
            print(f"would run: superjev.py gate <evidence files...> --draft <your draft>")
            print("missing: name the evidence files you read and your draft file in the "
                  "sentence, or call `gate` directly. "
                  f"Found {len(paths)} real path(s): {paths or 'none'}")
            return REFUSED
        evidence, draft = paths[:-1], paths[-1]
        print(f"would run: superjev.py gate {shlex.join(evidence)} --draft {draft}")
        return cmd_gate(argparse.Namespace(evidence=evidence, draft=draft, claim=None))
    if sub == "verify":
        if not paths:
            print("would run: superjev.py verify <report file> [--worktree P] [--test-cmd C]")
            print("missing: the worker's report as a file. Name its path in the sentence.")
            return REFUSED
        print(f"would run: superjev.py verify {paths[0]}")
        return cmd_verify(argparse.Namespace(report=paths[0], worktree=None,
                                            test_cmd="", paths=[], dry_run=False))
    # sweep
    records = next((p for p in paths if p.endswith(".jsonl")), None)
    questions = next((p for p in paths if p.endswith(".json")), None)
    print("would run: superjev.py sweep <records.jsonl> --questions <q.json> --out <dir>")
    if not records or not questions:
        print("missing: " + ", ".join(
            x for x in [None if records else "a .jsonl records file",
                        None if questions else "a .json questions file"] if x)
            + f". Found: {paths or 'no real paths'}")
        return REFUSED
    print("missing: --out <dir>. A sweep writes files and never overwrites, so the "
          "output directory is yours to name. Call `sweep` directly with it.")
    return REFUSED


# ---------------------------------------------------------------- status

def cmd_status(a):
    repo = repo_path()
    scripts = npm_scripts(repo) or {}
    live_gate = door_missing(GATE_CMD_ENV, FLEET_JEV_LIB) is None
    live_verify = door_missing(VERIFY_CMD_ENV, FLEET_VERIFY_PY) is None
    gate_what = (f"${GATE_CMD_ENV}" if os.environ.get(GATE_CMD_ENV)
                else f"claim-gate ({FLEET_JEV_LIB})")
    verify_what = (f"${VERIFY_CMD_ENV}" if os.environ.get(VERIFY_CMD_ENV)
                  else f"report-verify ({FLEET_VERIFY_PY})")
    rows = [
        ("gate", "LIVE" if live_gate else "MISSING DOOR", gate_what),
        ("verify", "LIVE" if live_verify else "MISSING DOOR", verify_what),
        ("sweep", "LIVE" if "sweep" in scripts else "MISSING SCRIPT", "npm run sweep"),
        ("bench", "LIVE" if "bench:live" in scripts else "MISSING SCRIPT", "npm run bench:live"),
        ("permit", "NOT BUILT", "wishlist item 5 (ACTION PERMIT)"),
        ("chain", "NOT BUILT", "wishlist item 4 (EVIDENCE CHAIN)"),
        ("fetch", "NOT BUILT", "wishlist item 6 (FETCH LAYER)"),
        ("ask", "LIVE", "keyword router, no model call"),
        ("status", "LIVE", "this"),
    ]
    print("super-jev — one front door for every check\n")
    print(f"{'door':<8} {'state':<15} what it wraps")
    print("-" * 72)
    for name, state, what in rows:
        print(f"{name:<8} {state:<15} {what}")
    print(f"\nharness repo: {repo}")
    print(f"harness commit: {harness_commit(repo)}")
    print(f"TYPESAFE_API_KEY in env: {'yes' if os.environ.get('TYPESAFE_API_KEY') else 'no'}"
          " (never printed)")
    return 0


def harness_commit(repo):
    if not (repo / ".git").exists() and not repo.is_dir():
        return f"unknown — no checkout at {repo}"
    try:
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, env=child_env())
    except OSError:
        return "unknown — git is not runnable here"
    out = (proc.stdout or "").strip()
    return out if proc.returncode == 0 and out else f"unknown — git could not read {repo}"


# ---------------------------------------------------------------- cli

def build_parser():
    p = argparse.ArgumentParser(prog="superjev",
                                description="One front door for every check we run.")
    subs = p.add_subparsers(dest="sub")

    g = subs.add_parser("gate", help="check a draft against the evidence it came from")
    g.add_argument("evidence", nargs="+", help="the evidence files you actually read")
    g.add_argument("--draft", default="", help="your draft reply, as a file")
    g.add_argument("--claim", action="append", help="one claim; repeat per claim")
    g.set_defaults(func=cmd_gate)

    v = subs.add_parser("verify", help="check a worker's report against machine evidence")
    v.add_argument("report", help="the worker's final report, as a file")
    v.add_argument("--worktree", help="the repo or folder the work happened in")
    v.add_argument("--test-cmd", default="", help="the test command, exact path")
    v.add_argument("--paths", nargs="*", default=[], help="paths the report claims")
    v.add_argument("--dry-run", action="store_true", help="collect evidence, no judging")
    v.set_defaults(func=cmd_verify)

    s = subs.add_parser("sweep", help="run every question over a pile bigger than one call")
    s.add_argument("records", help="records.jsonl, one record per line")
    s.add_argument("--questions", required=True, help="questions.json")
    s.add_argument("--out", required=True, help="output directory, created not overwritten")
    s.add_argument("--budget", type=int, help="maxInputTokens per call")
    s.add_argument("--batch", type=int, help="max records per call")
    s.add_argument("--gate", help="accept gate, 0..1")
    s.add_argument("--dry-run", action="store_true", help="print the plan, no network")
    s.add_argument("--stub", action="store_true", help="offline stub, no key, no network")
    s.set_defaults(func=cmd_sweep)

    b = subs.add_parser("bench", help="the live bench that measures the harness")
    b.add_argument("--dry-run", action="store_true", help="plan and cost only, no network")
    b.add_argument("--stub", action="store_true", help="offline stub run")
    b.set_defaults(func=cmd_bench)

    pm = subs.add_parser("permit", help="NOT BUILT — wishlist item 5")
    pm.add_argument("snapshot", nargs="?", help="the screen or context snapshot")
    pm.add_argument("--action", default="", help="the action you intend to take")
    pm.set_defaults(func=lambda a: not_built("permit"))

    ch = subs.add_parser("chain", help="NOT BUILT — wishlist item 4")
    ch.add_argument("spec", nargs="?", help="required-source spec, JSON")
    ch.add_argument("docs", nargs="*", help="the documents in the chain")
    ch.set_defaults(func=lambda a: not_built("chain"))

    ft = subs.add_parser("fetch", help="NOT BUILT — wishlist item 6")
    ft.add_argument("request", nargs="?", help="the request to score the catalog against")
    ft.set_defaults(func=lambda a: not_built("fetch"))

    ak = subs.add_parser("ask", help="one plain sentence; it picks the door")
    ak.add_argument("sentence", help="one plain sentence")
    ak.set_defaults(func=cmd_ask)

    st = subs.add_parser("status", help="which doors are live, which are not built")
    st.set_defaults(func=cmd_status)
    return p


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, "func", None):
        p.print_help()
        return REFUSED
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
