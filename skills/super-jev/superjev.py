#!/usr/bin/env python3
"""super-jev — the ONE front door for every check we run.

One ask, one door. This file is a set of thin wrappers: it never re-implements
a check. `gate` wraps a claim-gate command, `verify` wraps a report-verify
command, `sweep` and `bench` are npm scripts in the super-jev checkout.
Three more doors are named but not built yet; each one names its own wishlist
item and exits 6, so a missing tool tells you what is missing instead of
failing silently.

    python3 skills/super-jev/superjev.py <sub> ...

Subcommands: gate · verify · sweep · fetch · bench · hook · ledger · permit ·
chain · ask · status

Exit codes: whatever the wrapped door returned. Plus 5 for a refusal by this
wrapper (missing input, missing door, unroutable ask) and 6 for NOT BUILT.
`hook` uses a different, narrower contract of its own — see its section below.

It never prints TYPESAFE_API_KEY, and it never reads a secret file.

Portable by default: the `gate` and `verify` doors run a command taken from
SUPERJEV_GATE_CMD / SUPERJEV_VERIFY_CMD when set, and otherwise fall back to
a fixed fleet-local path that is almost certainly absent on a fresh clone —
in that case the door names the missing path and the env var that replaces
it, rather than failing inside a subprocess. SUPERJEV_REPO controls where
`sweep` and `bench` look for their npm scripts, and defaults to this
checkout's own root, so a fresh clone needs no env at all for those two.

`--json` on gate/verify/sweep/bench/status prints exactly one JSON object on
stdout and nothing else: {door, verdict, exit_code, summary, details,
would_run}. `hook <gate|verify>` reads a Claude Code hook payload on stdin
and maps the verdict onto the hook's own exit convention (0 allow/advisory,
2 block) instead — see cmd_hook's docstring. Every door invocation is logged
to a call ledger under this skill's own `ledger/` folder; see `ledger` and
`status`.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
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

# Subprocess timeouts. A wrapped door must never hang a hook (or a CLI call)
# forever; env overrides let a slow tool raise its own ceiling.
GATE_TIMEOUT_ENV = "SUPERJEV_GATE_TIMEOUT"
VERIFY_TIMEOUT_ENV = "SUPERJEV_VERIFY_TIMEOUT"
DEFAULT_GATE_TIMEOUT_S = 90
DEFAULT_VERIFY_TIMEOUT_S = 300
TIMEOUT_EXIT_CODE = 124  # conventional shell "command timed out" code

# How much of the transcript's tool_result history the Stop hook turns into
# evidence when the payload names none itself. Env overrides let a caller
# tune this without a code change.
HOOK_EVIDENCE_N_ENV = "SUPERJEV_HOOK_EVIDENCE_N"
HOOK_EVIDENCE_MAX_BYTES_ENV = "SUPERJEV_HOOK_EVIDENCE_MAX_BYTES"
DEFAULT_HOOK_EVIDENCE_N = 8
DEFAULT_HOOK_EVIDENCE_MAX_BYTES = 50_000

# PostToolUse verify must never guess a worktree from the payload's own cwd
# (that field describes the lead session, not necessarily the worker's
# tree). This env var is the only non-payload source honoured.
HOOK_WORKTREE_ENV = "SUPERJEV_HOOK_WORKTREE"

# Strong-flag block thresholds. A gate/verify run that comes back READ
# (exit 3, "advisory") can still carry a claim-level or draft-level flag
# strong enough that letting it pass as a silent advisory is the same bug
# that let a lied-about Stop message through: NOT_SUPPORTED even at LOW
# confidence ("the evidence does not address it", scored 0.18) is exactly
# the sentence an agent must not say out loud as fact, so it blocks on a
# score AT OR BELOW the line, not above it. OVERCLAIMS blocks on a score AT
# OR ABOVE the line — a confident overclaim. SELF_CONTRADICTORY blocks at
# or below its own fixed line (not env-configurable; only the two claim-
# shaped flags get an env override). A fabricated quote is already exit 2
# from jev.py itself and already maps to "block" via GATE_HOOK_ACTION —
# nothing here changes that path.
BLOCK_NOT_SUPPORTED_ENV = "SUPERJEV_BLOCK_NOT_SUPPORTED"
BLOCK_OVERCLAIM_ENV = "SUPERJEV_BLOCK_OVERCLAIM"
DEFAULT_BLOCK_NOT_SUPPORTED = 0.20
DEFAULT_BLOCK_OVERCLAIM = 0.80
BLOCK_SELF_CONTRADICTORY = 0.30

# jev.py's --kit reply table (_print_reply) and worker-verify's own table
# (same row shape, same function pattern) print one line per claim as
#   "  c3   NOT_SUPPORTED   0.18  <subject text...>"
# and one line per draft-level flag as
#   "  overclaim         OVERCLAIMS           0.98   -> SOFTEN IT ..."
# This regex reads either shape off the captured stdout: a key (c<N> or one
# of the four draft-level question keys), then an ALL-CAPS verdict word,
# then a confidence float. Both doors share this exact format because both
# are built on jev.py's row shapes, so one parser covers gate and verify.
_FLAG_LINE_RE = re.compile(
    r'^\s*(?P<key>c\d+|leaked_internal|time_sensitive|self_contradictory|overclaim)\s+'
    r'(?P<verdict>[A-Z][A-Z_]*)\s+(?P<score>\d+\.\d+)', re.MULTILINE)

# The red verdicts worth carrying into the ledger and checking against a
# block line. Mirrors jev.py's own RED_VERDICTS.
NOTABLE_VERDICTS = {"NOT_SUPPORTED", "CONTRADICTED", "HAS_LEAKS", "TIME_SENSITIVE",
                    "SELF_CONTRADICTORY", "OVERCLAIMS"}


def _block_not_supported_threshold():
    try:
        return float(os.environ.get(BLOCK_NOT_SUPPORTED_ENV, DEFAULT_BLOCK_NOT_SUPPORTED))
    except (TypeError, ValueError):
        return DEFAULT_BLOCK_NOT_SUPPORTED


def _block_overclaim_threshold():
    try:
        return float(os.environ.get(BLOCK_OVERCLAIM_ENV, DEFAULT_BLOCK_OVERCLAIM))
    except (TypeError, ValueError):
        return DEFAULT_BLOCK_OVERCLAIM


def _parse_strong_flags(text):
    """Every notable (red) claim/draft-level line found in a captured
    jev.py-shaped verdict table — gate (jev.py --kit reply) and verify
    (worker-verify) print the identical row format, so one parser covers
    both doors. Returns a list of {"key", "verdict", "score"} dicts, in the
    order they appear on stdout. Never raises: None/empty/unparseable text
    yields []."""
    if not text:
        return []
    flags = []
    for m in _FLAG_LINE_RE.finditer(text):
        verdict = m.group("verdict")
        if verdict not in NOTABLE_VERDICTS:
            continue
        try:
            score = float(m.group("score"))
        except ValueError:
            continue
        flags.append({"key": m.group("key"), "verdict": verdict, "score": score})
    return flags


def _hook_block_reasons(flags):
    """Which of these already-notable flags cross THIS hook's own block
    line (stricter, and in the opposite direction for NOT_SUPPORTED/
    SELF_CONTRADICTORY, than jev's own 0.80 escalate-below line — see the
    block above). Returns a list of "key VERDICT score" strings, in
    argument order, for the stderr reason and the ledger line."""
    not_supported_line = _block_not_supported_threshold()
    overclaim_line = _block_overclaim_threshold()
    reasons = []
    for f in flags:
        v, s, k = f["verdict"], f["score"], f["key"]
        blocked = (
            (v == "NOT_SUPPORTED" and s <= not_supported_line) or
            (v == "OVERCLAIMS" and s >= overclaim_line) or
            (v == "SELF_CONTRADICTORY" and s <= BLOCK_SELF_CONTRADICTORY)
        )
        if blocked:
            reasons.append(f"{k} {v} {s:.2f}")
    return reasons

# The wishlist ships in this repo, not in a private fleet path.
WISHLIST = REPO_ROOT / "docs" / "wishlist.md"
DEFAULT_REPO = REPO_ROOT


def _default_ledger_path():
    """SUPERJEV_LEDGER, if set, else this skill's own ledger/calls.jsonl."""
    override = os.environ.get("SUPERJEV_LEDGER")
    if override:
        return Path(override).expanduser()
    return SKILL_DIR / "ledger" / "calls.jsonl"


# One JSONL line per door invocation. SUPERJEV_LEDGER overrides the path;
# tests always override sj.LEDGER_PATH directly so a test run never writes
# into a real checkout.
LEDGER_PATH = _default_ledger_path()

REFUSED = 5          # this wrapper refused: missing input or missing door
NOT_BUILT = 6        # the door is named in the wishlist and does not exist yet

# Doors named in the wishlist but not yet built. item -> (title, why it is
# named). Empty now that permit (item 5) and chain (item 4) are wired to the
# npm CLIs #8 added — kept as a dict, not deleted, so a future wishlist item
# has somewhere to register instead of needing a new mechanism.
UNBUILT = {}
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


def _semver_key(name):
    """(major, minor, patch) parsed off an nvm version dir name like
    'v24.11.1', for a real numeric sort instead of a lexicographic one
    ('v9.0.0' > 'v24.11.1' as strings). Anything unparseable sorts below
    every real version rather than crashing the comparison."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", name)
    if not m:
        return (-1, -1, -1)
    return tuple(int(g) for g in m.groups())


def resolve_npm():
    """Absolute path to a runnable `npm`, or None. Never raises, never depends
    on cwd.

    A bare/non-interactive shell (a hook's shell, `env -i ...`) usually does
    not carry the nvm PATH edit an interactive shell profile adds. If `npm`
    is not already on PATH, look under ~/.nvm/versions/node/*/bin, newest
    version first by semantic version (not name string), and prepend the
    winning bin dir to PATH so the door that actually shells out to npm
    inherits it too.
    """
    found = shutil.which("npm")
    if found:
        return found
    home = os.environ.get("HOME")
    if not home:
        return None
    nvm_dir = Path(home) / ".nvm" / "versions" / "node"
    try:
        candidates = sorted((d for d in nvm_dir.iterdir() if d.is_dir()),
                            key=lambda d: _semver_key(d.name), reverse=True)
    except OSError:
        return None
    for d in candidates:
        npm_bin = d / "bin" / "npm"
        if npm_bin.exists():
            os.environ["PATH"] = f"{d / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"
            return str(npm_bin)
    return None


def _npm_missing_refusal(json_mode, door):
    msg = ("npm not found on PATH and no node under ~/.nvm/versions/node — "
           "install Node.js or put npm on PATH")
    if json_mode:
        emit_json(door, "REFUSED", 1, msg, {}, [])
    else:
        print(f"super-jev: {msg}", file=sys.stderr)
    return 1


def ledger_append(entry):
    """Append one JSONL line to the call ledger. Never raises — a ledger
    problem must never break a door — but an unwritable ledger is not
    swallowed in total silence: one line goes to stderr so a broken ledger
    path is discoverable instead of invisibly dropping every record."""
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"super-jev: could not write ledger at {LEDGER_PATH}: {exc}",
              file=sys.stderr)


def _ledger_lines():
    try:
        text = LEDGER_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln for ln in text.splitlines() if ln.strip()]


def _ledger_count_today():
    today = datetime.now(timezone.utc).date().isoformat()
    n = 0
    for line in _ledger_lines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if str(rec.get("ts", "")).startswith(today):
            n += 1
    return n


def run_door(cmd, cwd=None, capture=False, door=None, json_mode=False, hook_mode=False,
            timeout=None):
    """Run a wrapped door and give back its exit code.

    Without `capture`, output is not captured: the door's own table is the
    result, and it goes straight to the operator's terminal (unchanged
    behaviour). With `capture=True` (used by --json and, always, by `hook`
    — a hook must never let a child's raw stdout escape onto the hook's own
    stdout via inherited fd 1), nothing is echoed and the door's
    stdout/stderr are returned instead of printed, so a JSON or hook caller
    gets exactly its own single line of output.

    `timeout` (seconds) bounds the subprocess. A timeout never raises out of
    this function: it is logged to the ledger and reported back as exit
    code 124 (the conventional shell "timed out" code), so every caller —
    including `hook`, which must fail open rather than hang a session —
    sees an ordinary exit code instead of an uncaught exception.

    Every call — captured, timed out, or not — is appended to the call
    ledger.
    """
    if not capture:
        print("$ " + shlex.join(str(c) for c in cmd) + (f"   (in {cwd})" if cwd else ""))
        sys.stdout.flush()
    t0 = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                              env=child_env(), capture_output=capture,
                              text=True if capture else False, timeout=timeout)
        returncode = proc.returncode
        out = proc.stdout if capture else ""
        err = proc.stderr if capture else ""
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = TIMEOUT_EXIT_CODE
        out = ""
        err = f"super-jev: {door or (cmd[0] if cmd else '?')} timed out after {timeout}s"
        if not capture:
            print(err, file=sys.stderr)
    ms = int((time.monotonic() - t0) * 1000)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "door": door or (str(cmd[0]) if cmd else "?"),
        "argv": [str(c) for c in cmd],
        "exit_code": returncode,
        "ms": ms,
        "json_mode": json_mode,
        "hook_mode": hook_mode,
    }
    if timed_out:
        entry["timeout"] = True
    ledger_append(entry)
    if capture:
        return returncode, out or "", err or ""
    return returncode


def refuse(line):
    print(f"super-jev: {line}", file=sys.stderr)
    return REFUSED


def emit_json(door, verdict, exit_code, summary, details, would_run):
    """The one JSON object --json prints on stdout. Nothing else goes to
    stdout in that mode."""
    print(json.dumps({
        "door": door,
        "verdict": verdict,
        "exit_code": exit_code,
        "summary": summary,
        "details": details if details is not None else {},
        "would_run": [str(c) for c in (would_run or [])],
    }))


def door_refuse(json_mode, door, summary, exit_code=REFUSED, would_run=None):
    """A refusal, shaped for whichever mode the caller is in. Identical to
    the plain refuse() when json_mode is False — same message, same exit
    code — so every existing (non-json) caller is unaffected."""
    if json_mode:
        emit_json(door, "REFUSED", exit_code, summary, {}, would_run)
        return exit_code
    return refuse(summary)


def npm_scripts(repo):
    """The script names in the checkout's package.json, or None if unreadable."""
    try:
        data = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scripts = data.get("scripts")
    return scripts if isinstance(scripts, dict) else {}


def need_script(repo, name, json_mode=False, door=None):
    """Refuse with a clear line unless the checkout really has this script."""
    if not repo.is_dir():
        return door_refuse(json_mode, door,
                           f"no super-jev checkout at {repo} — set SUPERJEV_REPO to one")
    scripts = npm_scripts(repo)
    if scripts is None:
        return door_refuse(json_mode, door,
                           f"no readable package.json in {repo} — SUPERJEV_REPO is not a "
                           "super-jev checkout")
    if name not in scripts:
        return door_refuse(json_mode, door,
                           f"no `{name}` script in {repo}/package.json — set SUPERJEV_REPO "
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


def not_built(sub, json_mode=False):
    """Print the NOT BUILT refusal for one door and return exit 6."""
    item, title = UNBUILT[sub]
    note = f"; {CHAIN_NOTE}" if sub == "chain" else ""
    line1 = f"{sub}: not built yet — wishlist item {item} ({title}){note}"
    line2 = wishlist_line(item)
    if json_mode:
        emit_json(sub, "NOT_BUILT", NOT_BUILT, line1,
                  {"wishlist_line": line2, "wishlist_path": str(WISHLIST)}, [])
        return NOT_BUILT
    print(line1)
    print(line2)
    print(f"wishlist: {WISHLIST}")
    return NOT_BUILT


# ---------------------------------------------------------------- gate

GATE_VERDICT = {
    0: "CLEAN — every claim is carried by the evidence at or above 0.80. Send it.",
    3: "READ — a claim is red or under 0.80. A human reads the source before you send.",
    2: "REJECT — a quoted span is not in the evidence. The citation is fabricated.",
}
GATE_VERDICT_WORD = {0: "CLEAN", 3: "READ", 2: "REJECT"}


def _gate_timeout():
    try:
        return float(os.environ.get(GATE_TIMEOUT_ENV, DEFAULT_GATE_TIMEOUT_S))
    except ValueError:
        return DEFAULT_GATE_TIMEOUT_S


def cmd_gate(a):
    json_mode = getattr(a, "json", False)
    hook_mode = getattr(a, "hook_mode", False)
    bad = door_missing(GATE_CMD_ENV, FLEET_JEV_LIB)
    if bad is not None:
        return door_refuse(json_mode, "gate", bad)
    if not a.draft and not a.claim:
        return door_refuse(json_mode, "gate",
                           "gate needs --draft <file> or one or more --claim \"<text>\"")
    cmd = [*door_cmd(GATE_CMD_ENV, FLEET_JEV_LIB), *a.evidence, "--kit", "reply"]
    if a.draft:
        cmd += ["--draft", a.draft]
    for claim in a.claim or []:
        cmd += ["--claim", claim]
    timeout = _gate_timeout()
    # capture_output whenever this isn't a plain terminal call — --json needs
    # exactly one object on stdout, and hook mode must never let the child's
    # raw stdout/stderr escape onto fd 1/2, which the child would otherwise
    # inherit straight from this process regardless of contextlib redirects.
    if json_mode:
        code, out, err = run_door(cmd, capture=True, door="gate", json_mode=True,
                                  hook_mode=hook_mode, timeout=timeout)
        emit_json("gate", GATE_VERDICT_WORD.get(code, "ERROR"), code,
                  GATE_VERDICT.get(code, f"ERROR — jev-check exited {code}"),
                  {"stdout": out, "stderr": err}, cmd)
        return code
    if hook_mode:
        # Captured and NOT printed: cmd_hook builds its own one-line
        # advisory/block message from the exit code, plus a strong-flag
        # scan of the captured stdout (see _parse_strong_flags /
        # _hook_block_reasons). Printing the VERDICT line here would leak
        # straight onto the hook's real stdout, breaking the "silent
        # allow" contract, so cmd_hook gets (code, out, err) back instead
        # of a bare code — the only caller of this branch.
        code, out, err = run_door(cmd, capture=True, door="gate", json_mode=False,
                                  hook_mode=True, timeout=timeout)
        return code, out, err
    code = run_door(cmd, door="gate", hook_mode=hook_mode, timeout=timeout)
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
VERIFY_VERDICT_WORD = {0: "CLEAN", 3: "READ", 4: "REJECT", 2: "NO_CHECKABLE_CLAIMS",
                       5: "BAD_USAGE"}


def _verify_timeout():
    try:
        return float(os.environ.get(VERIFY_TIMEOUT_ENV, DEFAULT_VERIFY_TIMEOUT_S))
    except ValueError:
        return DEFAULT_VERIFY_TIMEOUT_S


def cmd_verify(a):
    json_mode = getattr(a, "json", False)
    hook_mode = getattr(a, "hook_mode", False)
    bad = door_missing(VERIFY_CMD_ENV, FLEET_VERIFY_PY)
    if bad is not None:
        return door_refuse(json_mode, "verify", bad)
    cmd = [*door_cmd(VERIFY_CMD_ENV, FLEET_VERIFY_PY), a.report]
    if a.worktree:
        cmd += ["--worktree", a.worktree]
    if a.test_cmd:
        cmd += ["--test-cmd", a.test_cmd]
    if a.paths:
        cmd += ["--paths", *a.paths]
    if a.dry_run:
        cmd += ["--dry-run"]
    timeout = _verify_timeout()
    if json_mode:
        code, out, err = run_door(cmd, capture=True, door="verify", json_mode=True,
                                  hook_mode=hook_mode, timeout=timeout)
        emit_json("verify", VERIFY_VERDICT_WORD.get(code, "ERROR"), code,
                  VERIFY_VERDICT.get(code, f"ERROR — worker-verify exited {code}"),
                  {"stdout": out, "stderr": err}, cmd)
        return code
    if hook_mode:
        # Same rationale as cmd_gate's hook_mode branch: captured, never
        # printed, and returned as (code, out, err) so cmd_hook can run
        # the same strong-flag scan over worker-verify's table.
        code, out, err = run_door(cmd, capture=True, door="verify", json_mode=False,
                                  hook_mode=True, timeout=timeout)
        return code, out, err
    code = run_door(cmd, door="verify", hook_mode=hook_mode, timeout=timeout)
    print(f"\nVERDICT: {VERIFY_VERDICT.get(code, f'ERROR — worker-verify exited {code}')}")
    return code


# ---------------------------------------------------------------- sweep

def cmd_sweep(a):
    json_mode = getattr(a, "json", False)
    npm_path = resolve_npm()
    if npm_path is None:
        return _npm_missing_refusal(json_mode, "sweep")
    repo = repo_path()
    bad = need_script(repo, "sweep", json_mode=json_mode, door="sweep")
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
    key_warning = None
    if not a.dry_run and not a.stub and not os.environ.get("TYPESAFE_API_KEY"):
        key_warning = ("a live sweep needs TYPESAFE_API_KEY. Re-run with --dry-run "
                       "or --stub to stay offline.")
        if not json_mode:
            print(f"super-jev: {key_warning}", file=sys.stderr)
    if json_mode:
        code, out, err = run_door(cmd, cwd=repo, capture=True, door="sweep", json_mode=True)
        details = {"stdout": out, "stderr": err}
        if key_warning:
            details["note"] = key_warning
        emit_json("sweep", "RAN" if code == 0 else "ERROR", code,
                  f"sweep exited {code}", details, cmd)
        return code
    return run_door(cmd, cwd=repo, door="sweep")


# ---------------------------------------------------------------- fetch

def cmd_fetch(a):
    json_mode = getattr(a, "json", False)
    npm_path = resolve_npm()
    if npm_path is None:
        return _npm_missing_refusal(json_mode, "fetch")
    repo = repo_path()
    bad = need_script(repo, "fetch", json_mode=json_mode, door="fetch")
    if bad is not None:
        return bad
    cmd = ["npm", "run", "fetch", "--", "--catalog", a.catalog, "--request", a.request]
    if a.k is not None:
        cmd += ["--k", str(a.k)]
    if a.out:
        cmd += ["--out", a.out]
    if a.budget is not None:
        cmd += ["--budget", str(a.budget)]
    if a.batch is not None:
        cmd += ["--batch", str(a.batch)]
    if a.dry_run:
        cmd += ["--dry-run"]
    if a.stub:
        cmd += ["--stub"]
    key_warning = None
    if not a.dry_run and not a.stub and not os.environ.get("TYPESAFE_API_KEY"):
        key_warning = ("a live fetch needs TYPESAFE_API_KEY. Re-run with --dry-run "
                       "or --stub to stay offline.")
        if not json_mode:
            print(f"super-jev: {key_warning}", file=sys.stderr)
    if json_mode:
        code, out, err = run_door(cmd, cwd=repo, capture=True, door="fetch", json_mode=True)
        details = {"stdout": out, "stderr": err}
        if key_warning:
            details["note"] = key_warning
        emit_json("fetch", "RAN" if code == 0 else "ERROR", code,
                  f"fetch exited {code}", details, cmd)
        return code
    return run_door(cmd, cwd=repo, door="fetch")


# ---------------------------------------------------------------- permit

def cmd_permit(a):
    json_mode = getattr(a, "json", False)
    npm_path = resolve_npm()
    if npm_path is None:
        return _npm_missing_refusal(json_mode, "permit")
    repo = repo_path()
    bad = need_script(repo, "permit", json_mode=json_mode, door="permit")
    if bad is not None:
        return bad
    cmd = ["npm", "run", "permit", "--", "--snapshot", a.snapshot]
    if a.action:
        cmd += ["--action", a.action]
    if a.id:
        cmd += ["--id", a.id]
    if a.min_confidence is not None:
        cmd += ["--min-confidence", str(a.min_confidence)]
    if a.dry_run:
        cmd += ["--dry-run"]
    if a.stub:
        cmd += ["--stub"]
    key_warning = None
    if not a.dry_run and not a.stub and not os.environ.get("TYPESAFE_API_KEY"):
        key_warning = ("a live permit needs TYPESAFE_API_KEY. Re-run with --dry-run "
                       "or --stub to stay offline.")
        if not json_mode:
            print(f"super-jev: {key_warning}", file=sys.stderr)
    if json_mode:
        code, out, err = run_door(cmd, cwd=repo, capture=True, door="permit", json_mode=True)
        details = {"stdout": out, "stderr": err}
        if key_warning:
            details["note"] = key_warning
        emit_json("permit", "RAN" if code == 0 else "ERROR", code,
                  f"permit exited {code}", details, cmd)
        return code
    return run_door(cmd, cwd=repo, door="permit")


# ---------------------------------------------------------------- chain

def cmd_chain(a):
    json_mode = getattr(a, "json", False)
    npm_path = resolve_npm()
    if npm_path is None:
        return _npm_missing_refusal(json_mode, "chain")
    repo = repo_path()
    bad = need_script(repo, "chain", json_mode=json_mode, door="chain")
    if bad is not None:
        return bad
    cmd = ["npm", "run", "chain", "--", "--spec", a.spec]
    if a.dry_run:
        cmd += ["--dry-run"]
    if a.stub:
        cmd += ["--stub"]
    key_warning = None
    if not a.dry_run and not a.stub and not os.environ.get("TYPESAFE_API_KEY"):
        key_warning = ("a live chain needs TYPESAFE_API_KEY. Re-run with --dry-run "
                       "or --stub to stay offline.")
        if not json_mode:
            print(f"super-jev: {key_warning}", file=sys.stderr)
    if json_mode:
        code, out, err = run_door(cmd, cwd=repo, capture=True, door="chain", json_mode=True)
        details = {"stdout": out, "stderr": err}
        if key_warning:
            details["note"] = key_warning
        emit_json("chain", "RAN" if code == 0 else "ERROR", code,
                  f"chain exited {code}", details, cmd)
        return code
    return run_door(cmd, cwd=repo, door="chain")


# ---------------------------------------------------------------- bench

def cmd_bench(a):
    json_mode = getattr(a, "json", False)
    npm_path = resolve_npm()
    if npm_path is None:
        return _npm_missing_refusal(json_mode, "bench")
    repo = repo_path()
    bad = need_script(repo, "bench:live", json_mode=json_mode, door="bench")
    if bad is not None:
        return bad
    live = not (a.dry_run or a.stub)
    if live and not os.environ.get("TYPESAFE_API_KEY"):
        msg = ("refusing a live bench — TYPESAFE_API_KEY is not set in the "
               "environment. Printing the dry-run plan instead; no network, no cost.")
        plan_cmd = ["npm", "run", "bench:live", "--", "--dry-run"]
        if json_mode:
            code, out, err = run_door(plan_cmd, cwd=repo, capture=True, door="bench",
                                      json_mode=True)
            emit_json("bench", "REFUSED", code if code else REFUSED, msg,
                      {"stdout": out, "stderr": err}, plan_cmd)
            return code if code else REFUSED
        print(f"super-jev: {msg}")
        code = run_door(plan_cmd, cwd=repo, door="bench")
        return code if code else REFUSED
    flag = ["--dry-run"] if a.dry_run else (["--stub"] if a.stub else [])
    cmd = ["npm", "run", "bench:live", "--", *flag] if flag else ["npm", "run", "bench:live"]
    if json_mode:
        code, out, err = run_door(cmd, cwd=repo, capture=True, door="bench", json_mode=True)
        emit_json("bench", "RAN" if code == 0 else "ERROR", code,
                  f"bench exited {code}", {"stdout": out, "stderr": err}, cmd)
        return code
    return run_door(cmd, cwd=repo, door="bench")


# ---------------------------------------------------------------- hook

# Hook exit action per verdict exit code. Anything not listed here maps to
# "advisory" — the hook contract never blocks (exit 2) on an ambiguous code,
# and never propagates 3/4/5 straight through.
GATE_HOOK_ACTION = {0: "allow", 3: "advisory", 2: "block"}
VERIFY_HOOK_ACTION = {0: "allow", 3: "advisory", 4: "block", 2: "advisory", 5: "advisory"}


def _hook_text(payload, keys):
    """The first non-empty string field in `keys`, else the last assistant
    message pulled from a Claude Code transcript at payload['transcript_path'],
    else None."""
    for k in keys:
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v
    tp = payload.get("transcript_path")
    if isinstance(tp, str) and tp:
        return _last_assistant_text(tp)
    return None


def _extract_text_blocks(value):
    """Best-effort plain text out of an Anthropic-shaped content value: a
    string, a list of {"type": "text", "text": ...} blocks, or a dict
    carrying a "text"/"content"/"output"/"result" field. None if nothing
    usable is found. Never raises on an odd shape — just returns None."""
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, list):
        parts = [b.get("text", "") for b in value
                if isinstance(b, dict) and b.get("type") == "text"]
        joined = "\n".join(p for p in parts if p)
        return joined if joined.strip() else None
    if isinstance(value, dict):
        for k in ("text", "content", "output", "result"):
            got = _extract_text_blocks(value.get(k))
            if got:
                return got
    return None


def _hook_report_text(payload):
    """The PostToolUse verify text: the direct 'report'/'text'/'message'
    string fields (for a caller building its own smaller payload), else
    payload['tool_response'] (the real field a PostToolUse payload carries
    — the sub-agent's final report, for a Task/Agent tool call), else the
    transcript fallback."""
    for k in ("report", "text", "message"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v
    got = _extract_text_blocks(payload.get("tool_response"))
    if got:
        return got
    tp = payload.get("transcript_path")
    if isinstance(tp, str) and tp:
        return _last_assistant_text(tp)
    return None


def _read_transcript_records(transcript_path):
    """Every parseable JSON object in a Claude Code transcript JSONL file,
    in file order. [] on any read/parse problem — best-effort, never
    raises."""
    try:
        path = Path(transcript_path).expanduser()
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _hook_evidence_n():
    try:
        n = int(os.environ.get(HOOK_EVIDENCE_N_ENV, DEFAULT_HOOK_EVIDENCE_N))
        return n if n > 0 else DEFAULT_HOOK_EVIDENCE_N
    except ValueError:
        return DEFAULT_HOOK_EVIDENCE_N


def _hook_evidence_max_bytes():
    try:
        n = int(os.environ.get(HOOK_EVIDENCE_MAX_BYTES_ENV, DEFAULT_HOOK_EVIDENCE_MAX_BYTES))
        return n if n > 0 else DEFAULT_HOOK_EVIDENCE_MAX_BYTES
    except ValueError:
        return DEFAULT_HOOK_EVIDENCE_MAX_BYTES


def _derive_evidence_text_from_transcript(transcript_path, n=None, max_bytes=None):
    """The evidence text a Stop-hook gate run uses when the payload names no
    'evidence' itself: the tool_result content of the last `n` tool calls
    found anywhere in the transcript (a real Stop payload's transcript_path
    JSONL does not mark turn boundaries in a machine-obvious way, so this is
    a best-effort "most recent tool calls" reading, not strictly scoped to
    the current turn only), joined in chronological order and capped at
    `max_bytes` total (the most recent bytes are kept, since the latest
    tool calls are the most likely to back the latest draft).

    Each transcript line that looks like {"message": {"content": [...]}}
    is scanned for {"type": "tool_result", "content": ...} blocks; each
    block's text is pulled out with _extract_text_blocks. Returns None if
    no tool_result content is found anywhere, or the transcript cannot be
    read at all.
    """
    n = n if n is not None else _hook_evidence_n()
    max_bytes = max_bytes if max_bytes is not None else _hook_evidence_max_bytes()
    results = []
    for rec in _read_transcript_records(transcript_path):
        msg = rec.get("message") if isinstance(rec, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                text = _extract_text_blocks(block.get("content"))
                if text:
                    results.append(text)
    if not results:
        return None
    tail = results[-n:]
    joined = "\n\n---\n\n".join(tail)
    if len(joined) > max_bytes:
        joined = joined[-max_bytes:]
    return joined if joined.strip() else None


def _last_assistant_text(transcript_path):
    """The most recent assistant message text in a Claude Code transcript
    JSONL file, or None. Best-effort: any read/parse problem just yields
    None, which the caller treats as fail-open."""
    try:
        path = Path(transcript_path).expanduser()
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"]
            joined = "\n".join(p for p in parts if p)
            if joined.strip():
                return joined
    return None


def _hook_evidence_paths(payload):
    """payload['evidence'], a list of path strings, if present, else [].
    This is the back-compat path for a caller that builds its own smaller
    payload with an explicit evidence list; a real Claude Code Stop payload
    never carries this key — see _derive_evidence_text_from_transcript for
    the path that actually fires against a real payload."""
    ev = payload.get("evidence")
    if isinstance(ev, list):
        return [str(p) for p in ev if isinstance(p, (str, os.PathLike))]
    return []


def _hook_log(note, exit_code=0, skipped=False, flags=None):
    """One ledger line for a hook decision. `exit_code` is the real code
    this hook invocation is about to return (never hard-coded to 0) —
    2 for a block, 0 for everything else, including a fail-open skip.
    `skipped=True` marks a run where the wrapped door never executed at
    all (bad/empty input, no derivable evidence, a caught exception, a
    timeout, or a bad hook invocation); `skipped=False` means gate/verify
    actually ran and this is its allow/advisory/block outcome. `flags`, if
    given, is the list of {"key","verdict","score"} dicts this run's
    captured stdout carried (see _parse_strong_flags) — every block AND
    every advisory line carries whatever was parsed, even an empty list,
    so the ledger always shows what was actually checked."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "door": "hook",
        "argv": [],
        "exit_code": exit_code,
        "ms": 0,
        "json_mode": False,
        "hook_mode": True,
        "skipped": skipped,
        "note": note,
    }
    if flags is not None:
        entry["flags"] = flags
    ledger_append(entry)


def cmd_hook(a):
    """Read a Claude Code hook payload on stdin and map the verdict to hook
    exit semantics. This subcommand's own contract, not the plain wrapper's:

        exit 0  = allow (CLEAN) — silent on stdout
        exit 0  = advisory (READ, or any other non-blocking verdict, or a
                  fail-open skip) — at most one line on stdout, never blocks
        exit 2  = block (REJECT) — one line reason on stderr, per Claude
                  Code's own PreToolUse/Stop convention for "block"

    Never exits 3/4/5 — those are `gate`/`verify`'s own exit codes and do
    not mean anything to a hook runner. Empty or non-JSON stdin, a payload
    with no usable text, no derivable evidence, a subprocess timeout, or
    any unexpected exception during the run all fail open (exit 0) rather
    than blocking or crashing — a hook must never wedge the user's session
    over a bad or unexpected payload. Every one of these fail-open paths
    writes a ledger line (via _hook_log, skipped=True) with the reason;
    there is no silent skip.

    Payload fields this reads — verified against the real Claude Code
    Stop and PostToolUse payload shapes, not assumed:

      gate (wired to Stop):
        "last_assistant_message" — the real Stop-only field Claude Code
          hands a hook so it does not have to re-parse a possibly-stale
          transcript for the current turn's text; this is the DRAFT.
        else "draft" | "text" | "prompt" (string) — back-compat for a
          caller that builds its own smaller payload instead of
          forwarding the real one.
        else the last assistant message read out of the transcript at
          "transcript_path" (a real Stop payload always carries this too).
        "evidence" (list of path strings), if present — back-compat only;
          a real Stop payload never carries this key.
        else, when "transcript_path" is present and readable: EVIDENCE is
          derived from the transcript itself — the tool_result content of
          the last N tool calls found in it (N = SUPERJEV_HOOK_EVIDENCE_N,
          default 8; total size capped by
          SUPERJEV_HOOK_EVIDENCE_MAX_BYTES, default 50000), written to one
          temp file and passed as the sole evidence path.
        If neither an explicit "evidence" list nor a readable
        "transcript_path" with derivable tool_result content is available,
        there is nothing to check the draft against — fail open (exit 0,
        logged, skipped=True) rather than running the wrapped tool with no
        evidence, which would exit on its own usage error and get
        misread as a block.

      verify (wired to PostToolUse, matcher "Agent"):
        "tool_name" — if present and not "Agent", this hook event is not a
          sub-agent report; fail open (skipped=True) rather than verifying
          something that is not a Task/Agent result. Absent entirely (a
          caller's own smaller payload), verify proceeds as before.
        "tool_response" — the REPORT: a real PostToolUse payload's tool
          result for the Agent/Task tool call, text extracted best-effort
          from a string, a list of {"type":"text"} blocks, or a dict.
        else "report" | "text" | "message" (string) — back-compat.
        else the transcript_path fallback, same as gate.
        "worktree" (string), if present in the payload, else
          SUPERJEV_HOOK_WORKTREE from the environment, else none — this is
          deliberately never derived from the payload's own "cwd" (that
          describes the lead session, not necessarily the worker's tree).
    """
    door = getattr(a, "door", "?")
    try:
        raw = sys.stdin.read()
    except Exception:
        _hook_log("could not read stdin — fail-open", skipped=True)
        return 0

    if not raw or not raw.strip():
        _hook_log("empty stdin — fail-open", skipped=True)
        return 0

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload is not a JSON object")
    except (ValueError, TypeError):
        _hook_log("non-JSON stdin — fail-open", skipped=True)
        return 0

    evidence_tmp_path = None
    try:
        if door == "gate":
            text = _hook_text(payload, ["last_assistant_message", "draft", "text", "prompt"])
            if text is None:
                _hook_log("gate: no usable text field in payload or transcript — "
                          "fail-open", skipped=True)
                return 0

            evidence = _hook_evidence_paths(payload)
            evidence_source = "payload"
            if not evidence:
                tp = payload.get("transcript_path")
                derived = None
                if isinstance(tp, str) and tp:
                    derived = _derive_evidence_text_from_transcript(tp)
                if derived:
                    tmp_ev = tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                                          delete=False, encoding="utf-8")
                    evidence_tmp_path = tmp_ev.name
                    tmp_ev.write(derived)
                    tmp_ev.close()
                    evidence = [evidence_tmp_path]
                    evidence_source = "transcript tool_result derivation"
                else:
                    # Neither an explicit evidence list nor a readable
                    # transcript with any tool_result content — nothing to
                    # check the draft against. Running the wrapped tool
                    # anyway would exit on its own usage error (the SAME
                    # number this shim uses for "block"), and get
                    # misreported as a REJECT. Fail open instead.
                    reason = ("gate: no evidence in payload and none derivable from "
                             f"transcript_path ({tp!r}) — nothing to check, fail-open")
                    _hook_log(reason, skipped=True)
                    return 0

            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                              encoding="utf-8")
            tmp_path = tmp.name
            try:
                tmp.write(text)
                tmp.close()
                ns = argparse.Namespace(evidence=evidence, draft=tmp_path, claim=None,
                                        json=False, hook_mode=True)
                code, door_out, door_err = cmd_gate(ns)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        else:
            tool_name = payload.get("tool_name")
            if tool_name is not None and tool_name != "Agent":
                _hook_log(f"verify: tool_name={tool_name!r} is not 'Agent' — this "
                         "PostToolUse event is not a sub-agent report, fail-open",
                         skipped=True)
                return 0

            text = _hook_report_text(payload)
            if text is None:
                _hook_log("verify: no usable report text (tool_response/report/text/"
                         "message/transcript) — fail-open", skipped=True)
                return 0

            worktree = payload.get("worktree") or os.environ.get(HOOK_WORKTREE_ENV)

            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                              encoding="utf-8")
            tmp_path = tmp.name
            try:
                tmp.write(text)
                tmp.close()
                ns = argparse.Namespace(report=tmp_path, worktree=worktree,
                                        test_cmd="", paths=[], dry_run=False,
                                        json=False, hook_mode=True)
                code, door_out, door_err = cmd_verify(ns)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        # Strong-flag override: a claim or draft-level flag red enough to
        # cross THIS hook's own block line (see _hook_block_reasons) turns
        # an "allow"/"advisory" base action into "block" — a READ (exit 3)
        # whose captured table carries e.g. "c1 NOT_SUPPORTED 0.18" must
        # not pass as a silent advisory just because jev's own 0.80
        # escalate-below line did not also flag it. A fabricated quote
        # (exit 2/4) is already "block" via the action map below and is
        # unaffected — flags is still parsed and logged for it, but there
        # is no weaker action to upgrade.
        flags = _parse_strong_flags(door_out)
        block_reasons = _hook_block_reasons(flags)

        action_map = GATE_HOOK_ACTION if door == "gate" else VERIFY_HOOK_ACTION
        action = action_map.get(code, "advisory")
        if block_reasons:
            action = "block"
        if action == "allow":
            _hook_log(f"{door}: allow (exit {code})", exit_code=0, flags=flags)
            return 0
        if action == "block":
            reason = f"super-jev {door} blocked this (exit {code})"
            if block_reasons:
                reason += ": " + "; ".join(block_reasons)
            print(reason, file=sys.stderr)
            _hook_log(f"{door}: block (exit {code})" +
                     (f" — strong flags: {'; '.join(block_reasons)}" if block_reasons else ""),
                     exit_code=2, flags=flags)
            return 2
        advisory = f"super-jev {door} advisory (exit {code})"
        print(advisory)
        _hook_log(f"{door}: advisory (exit {code})", exit_code=0, flags=flags)
        return 0
    except Exception as exc:  # fail-open: never wedge the session
        _hook_log(f"{door}: unexpected error ({exc.__class__.__name__}) — fail-open",
                 skipped=True)
        return 0
    finally:
        if evidence_tmp_path:
            try:
                os.unlink(evidence_tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------- ledger

def cmd_ledger(a):
    lines = _ledger_lines()
    if not lines:
        print(f"ledger: no calls recorded yet at {LEDGER_PATH}")
        return 0
    n = a.n if a.n and a.n > 0 else 20
    counts = {}
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        counts[rec.get("door", "?")] = counts.get(rec.get("door", "?"), 0) + 1
    print(f"ledger: {LEDGER_PATH}  ({len(lines)} call(s) total)\n")
    for line in lines[-n:]:
        print(line)
    print("\nper-door counts:")
    for name in sorted(counts):
        print(f"  {name:<10} {counts[name]}")
    return 0


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
    if sub == "fetch":
        catalog = next((p for p in paths if p.endswith(".json")), None)
        print('would run: superjev.py fetch "<request>" --catalog <catalog.json>')
        if not catalog:
            print("missing: a .json catalog file. Name its path in the sentence, or "
                  "call `fetch` directly.")
            return REFUSED
        print(f"missing: the plain request text. Call `fetch` directly with "
              f"--catalog {catalog} and your request as the positional argument.")
        return REFUSED
    if sub == "permit":
        snapshot = next((p for p in paths if p.endswith(".json")), None)
        if not snapshot:
            print('would run: superjev.py permit --snapshot <snapshot.json>')
            print("missing: a .json snapshot file naming the action. Name its path in "
                  "the sentence, or call `permit` directly.")
            return REFUSED
        print(f"would run: superjev.py permit --snapshot {snapshot}")
        return cmd_permit(argparse.Namespace(snapshot=snapshot, action="", id="",
                                             min_confidence=None, dry_run=False,
                                             stub=False, json=False))
    if sub == "chain":
        spec = next((p for p in paths if p.endswith(".json")), None)
        if not spec:
            print("would run: superjev.py chain --spec <spec.json>")
            print("missing: a .json spec file. Name its path in the sentence, or "
                  "call `chain` directly.")
            return REFUSED
        print(f"would run: superjev.py chain --spec {spec}")
        return cmd_chain(argparse.Namespace(spec=spec, dry_run=False, stub=False, json=False))
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
    json_mode = getattr(a, "json", False)
    repo = repo_path()
    scripts = npm_scripts(repo) or {}
    live_gate = door_missing(GATE_CMD_ENV, FLEET_JEV_LIB) is None
    live_verify = door_missing(VERIFY_CMD_ENV, FLEET_VERIFY_PY) is None
    npm_ok = resolve_npm() is not None
    gate_what = (f"${GATE_CMD_ENV}" if os.environ.get(GATE_CMD_ENV)
                else f"claim-gate ({FLEET_JEV_LIB})")
    verify_what = (f"${VERIFY_CMD_ENV}" if os.environ.get(VERIFY_CMD_ENV)
                  else f"report-verify ({FLEET_VERIFY_PY})")

    def script_state(name):
        # Honest three-way read, not just "is the script name present":
        # a script that npm cannot actually run is not LIVE.
        if name not in scripts:
            return "MISSING SCRIPT"
        if not npm_ok:
            return "script present, npm not runnable"
        return "LIVE"

    rows = [
        ("gate", "LIVE" if live_gate else "MISSING DOOR", gate_what),
        ("verify", "LIVE" if live_verify else "MISSING DOOR", verify_what),
        ("sweep", script_state("sweep"), "npm run sweep"),
        ("fetch", script_state("fetch"), "npm run fetch"),
        ("bench", script_state("bench:live"), "npm run bench:live"),
        ("permit", script_state("permit"), "npm run permit"),
        ("chain", script_state("chain"), "npm run chain"),
        ("ask", "LIVE", "keyword router, no model call"),
        ("status", "LIVE", "this"),
    ]
    ledger_today = _ledger_count_today()

    if json_mode:
        emit_json("status", "OK", 0, "door states as read off disk", {
            "doors": [{"door": n, "state": s, "wraps": w} for n, s, w in rows],
            "harness_repo": str(repo),
            "harness_commit": harness_commit(repo),
            "typesafe_key_present": bool(os.environ.get("TYPESAFE_API_KEY")),
            "ledger_path": str(LEDGER_PATH),
            "ledger_calls_today": ledger_today,
        }, [])
        return 0

    print("super-jev — one front door for every check\n")
    print(f"{'door':<8} {'state':<28} what it wraps")
    print("-" * 85)
    for name, state, what in rows:
        print(f"{name:<8} {state:<28} {what}")
    print(f"\nharness repo: {repo}")
    print(f"harness commit: {harness_commit(repo)}")
    print(f"TYPESAFE_API_KEY in env: {'yes' if os.environ.get('TYPESAFE_API_KEY') else 'no'}"
          " (never printed)")
    print(f"ledger: {LEDGER_PATH} ({ledger_today} call(s) today)")
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

def _add_json_flag(sp):
    sp.add_argument("--json", action="store_true",
                    help="print one JSON object on stdout, nothing else")


def build_parser():
    p = argparse.ArgumentParser(prog="superjev",
                                description="One front door for every check we run.")
    subs = p.add_subparsers(dest="sub")

    g = subs.add_parser("gate", help="check a draft against the evidence it came from")
    g.add_argument("evidence", nargs="+", help="the evidence files you actually read")
    g.add_argument("--draft", default="", help="your draft reply, as a file")
    g.add_argument("--claim", action="append", help="one claim; repeat per claim")
    _add_json_flag(g)
    g.set_defaults(func=cmd_gate)

    v = subs.add_parser("verify", help="check a worker's report against machine evidence")
    v.add_argument("report", help="the worker's final report, as a file")
    v.add_argument("--worktree", help="the repo or folder the work happened in")
    v.add_argument("--test-cmd", default="", help="the test command, exact path")
    v.add_argument("--paths", nargs="*", default=[], help="paths the report claims")
    v.add_argument("--dry-run", action="store_true", help="collect evidence, no judging")
    _add_json_flag(v)
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
    _add_json_flag(s)
    s.set_defaults(func=cmd_sweep)

    b = subs.add_parser("bench", help="the live bench that measures the harness")
    b.add_argument("--dry-run", action="store_true", help="plan and cost only, no network")
    b.add_argument("--stub", action="store_true", help="offline stub run")
    _add_json_flag(b)
    b.set_defaults(func=cmd_bench)

    hk = subs.add_parser("hook",
                         help="Claude Code hook shim: read a hook payload on stdin, map "
                              "the verdict onto the hook's own exit convention")
    hk.add_argument("door", choices=["gate", "verify"],
                    help="which check to run against the hook payload")
    hk.set_defaults(func=cmd_hook)

    lg = subs.add_parser("ledger", help="the call ledger: recent calls and per-door counts")
    lg.add_argument("-n", type=int, default=20, dest="n",
                    help="how many recent lines to print (default 20)")
    lg.set_defaults(func=cmd_ledger)

    pm = subs.add_parser("permit", help="is this one action safe to run automatically?")
    pm.add_argument("--snapshot", required=True,
                    help="JSON: action/target/reversible/reversibilityNotes/policyLines")
    pm.add_argument("--action", default="", help="the action; overrides snapshot.action")
    pm.add_argument("--id", default="", dest="id", help="label for this decision")
    pm.add_argument("--min-confidence", type=float, dest="min_confidence",
                    help="escalation threshold, 0..1")
    pm.add_argument("--dry-run", action="store_true", help="print the request, no network")
    pm.add_argument("--stub", action="store_true", help="offline stub, no key, no network")
    _add_json_flag(pm)
    pm.set_defaults(func=cmd_permit)

    ch = subs.add_parser("chain", help="evidence completeness before the final question")
    ch.add_argument("--spec", required=True, help="role spec, JSON")
    ch.add_argument("--dry-run", action="store_true",
                    help="in-code completeness check only, no network")
    ch.add_argument("--stub", action="store_true", help="offline stub, no key, no network")
    _add_json_flag(ch)
    ch.set_defaults(func=cmd_chain)

    ft = subs.add_parser("fetch", help="score a catalog against a request, top ids only")
    ft.add_argument("request", help="the plain request to score the catalog against")
    ft.add_argument("--catalog", required=True, help="catalog.json, an array of {id,text}")
    ft.add_argument("--k", type=int, help="how many top ids to return")
    ft.add_argument("--out", help="output directory; a rerun overwrites, never fails")
    ft.add_argument("--budget", type=int, help="maxInputTokens per call")
    ft.add_argument("--batch", type=int, help="max records per call")
    ft.add_argument("--dry-run", action="store_true", help="print the plan, no network")
    ft.add_argument("--stub", action="store_true", help="offline stub, no key, no network")
    _add_json_flag(ft)
    ft.set_defaults(func=cmd_fetch)

    ak = subs.add_parser("ask", help="one plain sentence; it picks the door")
    ak.add_argument("sentence", help="one plain sentence")
    ak.set_defaults(func=cmd_ask)

    st = subs.add_parser("status", help="which doors are live, which are not built")
    _add_json_flag(st)
    st.set_defaults(func=cmd_status)
    return p


def main(argv=None):
    if not os.environ.get("HOME"):
        print("super-jev: HOME is not set in the environment — refusing rather than "
              "guessing paths", file=sys.stderr)
        return 1
    argv = sys.argv[1:] if argv is None else list(argv)
    # `hook` has its own exit contract (0 allow/advisory, 2 block) — the
    # SAME number argparse uses for its own usage errors (a bad --door
    # choice, or none at all). A typo in a settings.json hook wiring must
    # not become a permanent silent block on every hook event, so an
    # argparse failure under `hook` is remapped to the hook contract's own
    # fail-open (exit 0, advisory, ledger-logged) instead of propagating
    # argparse's exit 2. Every other subcommand's usage errors are
    # untouched — they are not bound by the hook contract.
    is_hook_argv = bool(argv) and argv[0] == "hook"
    p = build_parser()
    try:
        a = p.parse_args(argv)
    except SystemExit as exc:
        if is_hook_argv:
            note = f"hook: bad invocation (argv={argv!r}) — advisory, never blocks"
            print(f"super-jev hook: {note}")
            _hook_log(note, exit_code=0, skipped=True)
            return 0
        raise
    if not getattr(a, "func", None):
        p.print_help()
        return REFUSED
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
