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

# A background Agent/Task spawn's tool_response is sometimes only a launch
# acknowledgement ("Spawned successfully... now running", "Async agent
# launched... You will be notified automatically when it completes") rather
# than the sub-agent's actual final report. Running `verify` against that
# text is a category error — there is nothing to check yet — and jev's own
# OVERCLAIMS scoring has been seen to score it as a confident, unsupported
# claim and block it (see docs/ledger for the 2026-09-17 false alarm this
# fixes). Before calling the verify door, classify the text: a launch/ack
# phrase match, or text too short to plausibly be a real report AND
# carrying none of a report's own markers (COMPLETE/INCOMPLETE/verdict/a
# test count), skips the door entirely rather than running it.
HOOK_ACK_PATTERNS_ENV = "SUPERJEV_HOOK_ACK_PATTERNS"
DEFAULT_ACK_PATTERNS = [
    "spawned successfully",
    "async agent launched",
    "agent is now running",
    "will be notified",
    "resumed agent",
    "idle_notification",
]
VERIFY_MIN_CHARS_ENV = "SUPERJEV_VERIFY_MIN_CHARS"
DEFAULT_VERIFY_MIN_CHARS = 200
# Content that marks a text as a real report even when it is short: an
# explicit COMPLETE/INCOMPLETE/verdict word, or a test count ("12 tests",
# "tests: 3 passed", "test-count").
_REPORT_MARKER_RE = re.compile(
    r'\b(complete|incomplete|verdict)\b|\d+\s*(tests?|passed|failed)|test-count',
    re.IGNORECASE)

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

# READ THIS BEFORE CHANGING EITHER THRESHOLD ABOVE.
#
# The float on a jev/worker-verify row is the judge's CONFIDENCE IN ITS OWN
# VERDICT, not a measure of how well the evidence supports the claim. See
# jev.py `_row`: it reads `ans["confidence"]`, and `escalate_below` (0.80)
# means "under this line the judge is unsure, so a human must read the
# source". So "c3 NOT_SUPPORTED 0.97" reads as "I am 97% sure the evidence
# does not address this claim" — the STRONGEST unsupported-claim signal
# there is — while "c1 NOT_SUPPORTED 0.18" reads as "I barely think so, do
# not act on me".
#
# DEFAULT_BLOCK_NOT_SUPPORTED therefore blocks in the direction that is
# hardest to defend: it fires on the judge's LEAST confident
# unsupported-claim findings and fails OPEN on its most confident ones. It
# is left as-is here on purpose — this change is a precision fix, and
# making the gate stricter without a bench run would trade tonight's false
# blocks for a new, unmeasured set of them. docs/hooks.md records it as the
# top open decision. Do not "fix" the direction without re-running bench/.
NOT_SUPPORTED_DIRECTION_NOTE = (
    "the float is the judge's CONFIDENCE in its verdict, not a support score; "
    "NOT_SUPPORTED blocks at or BELOW the line, so a high-confidence "
    "unsupported claim fails open (docs/hooks.md, open decision 1)")

# ------------------------------------------------- the evidence inventory
#
# The 2026-09-17 false block. A TRUE report ("187 passed", "both checks
# pass", "hook verify now exits 0/skipped"), checked with
# --worktree/--test-cmd/--pr, came back c2 NOT_SUPPORTED 0.68, c3
# NOT_SUPPORTED 0.97, c4 CONTRADICTED 0.72, overclaim OVERCLAIMS 1.00 and
# BLOCKED, while the lead had already confirmed by hand that the tests and
# CI both passed. The judge was not wrong about its evidence. THE EVIDENCE
# WAS MISSING, in two specific and reproducible ways:
#
#   1. --test-cmd was a DIRECTORY-level pytest. worker-verify's
#      `check_test_cmd` refuses those (a bare pytest in claw4mac launches
#      the live app) and writes "NO TEST OUTPUT WAS COLLECTED" into the
#      evidence instead of a test run. So "187 passed" had literally
#      nothing to be checked against.
#   2. --pr only ever produces `gh pr view --json
#      state,isDraft,headRefName,mergedAt`. That JSON carries NO check-run
#      data at all, so "both checks pass" is unprovable BY CONSTRUCTION,
#      however green the PR actually is.
#
# With three of four claims unprovable, "does the DRAFT state something as
# tested/verified/done when the EVIDENCE shows it only inferred or
# partial?" is honestly answered OVERCLAIMS at 1.00 — and OVERCLAIMS alone
# was enough to block. That is the bug: the shim turned an EVIDENCE GAP
# into a verdict about the report. A gate that cannot tell "you lied" from
# "I could not look" must not block on the second.
EVIDENCE_MIN_CHARS_ENV = "SUPERJEV_EVIDENCE_MIN_CHARS"
DEFAULT_EVIDENCE_MIN_CHARS = 400
# worker-verify's --dry-run printer emits exactly:
#   "EVIDENCE: 7 blocks, 24196 chars (limit 100000)"
_EVIDENCE_SIZE_RE = re.compile(r'^EVIDENCE:\s+(\d+)\s+blocks?,\s+(\d+)\s+chars',
                               re.MULTILINE)
# Sentences worker-verify writes INTO the evidence when a kind of evidence
# could not be collected. Each one means "this claim has nothing to be
# judged against", never "this claim is false".
EVIDENCE_ABSENT_MARKERS = (
    "NO TEST OUTPUT WAS COLLECTED",
    "NO REPOSITORY WAS INSPECTED",
    "gh IS NOT INSTALLED",
    "(no --test-cmd given)",
    "(no --worktree given)",
)
# A claim-level row, INCLUDING the SUPPORTED ones _parse_strong_flags drops
# (it keeps only red verdicts). Needed to answer "was every claim
# supported?", which _parse_strong_flags alone can never tell us.
_CLAIM_ROW_RE = re.compile(r'^\s*(?P<key>c\d+)\s+(?P<verdict>[A-Z][A-Z_]*)\s+'
                           r'(?P<score>\d+\.\d+)', re.MULTILINE)


def _evidence_min_chars():
    try:
        return int(os.environ.get(EVIDENCE_MIN_CHARS_ENV, DEFAULT_EVIDENCE_MIN_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_EVIDENCE_MIN_CHARS


def _parse_claim_rows(text):
    """Every c<N> row in a captured verdict table, SUPPORTED ones included.
    Returns [{"key", "verdict", "score"}] in table order; never raises."""
    if not text:
        return []
    rows = []
    for m in _CLAIM_ROW_RE.finditer(text):
        try:
            score = float(m.group("score"))
        except ValueError:
            continue
        rows.append({"key": m.group("key"), "verdict": m.group("verdict"),
                     "score": score})
    return rows


def _test_cmd_will_be_refused(cmd):
    """True when worker-verify's own `check_test_cmd` guardrail will refuse
    this --test-cmd, so we KNOW before spending a model call that no test
    output will reach the evidence. A deliberate mirror of verify.py's rule
    (that file is fleet-local and is not edited from here): a pytest
    invocation naming no .py file and no ::test selector is a
    directory-level run, and is refused.

    This is the check that catches tonight's false block with no probe and
    no network: `python3 -m pytest skills/super-jev/tests -q` is refused,
    so "187 passed" was never provable from that evidence."""
    if not cmd:
        return False
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return False
    if not toks:
        return False
    is_pytest = toks[0] == "pytest" or ("pytest" in toks and toks[0] in ("python", "python3"))
    if not is_pytest:
        return False
    return not any(t.endswith(".py") or "::" in t for t in toks[1:])


def _evidence_inventory(test_cmd="", worktree=None, pr=None, probe_stdout=""):
    """What evidence this run could ACTUALLY have gathered, and whether that
    is too thin to carry a verdict about the report.

    `probe_stdout` is the captured stdout of a free `--dry-run` gather (no
    model call) when one was run. Returns a dict that the ledger, --explain
    and the block gate all read:

      blocks/chars  — from worker-verify's own "EVIDENCE: N blocks, M chars"
                      line, or None when no probe ran or it printed none
      missing       — the absent-evidence sentences found in the probe
      thin          — True when we have POSITIVE reason to believe the
                      evidence cannot carry a judgement about the report
      reasons       — plain-words explanation, for --explain and the ledger

    `thin` is deliberately conservative. It is True only on positive
    evidence of a gap: no evidence source was passed at all, the --test-cmd
    will be refused, the probe reported a gathered size under the floor, or
    the probe printed an absent-evidence sentence. An unparseable or failed
    probe leaves `thin` False — unknown is not the same as absent, and we
    would rather keep a block we cannot justify than drop one we should
    have kept."""
    inv = {"blocks": None, "chars": None, "missing": [], "thin": False,
           "reasons": [], "test_cmd": test_cmd or "",
           "worktree": worktree or None, "pr": pr}
    if not (worktree or test_cmd or pr):
        inv["thin"] = True
        inv["reasons"].append("no evidence source was passed (no worktree, "
                              "no test command, no PR)")
    if test_cmd and _test_cmd_will_be_refused(test_cmd):
        inv["thin"] = True
        inv["reasons"].append(
            f"--test-cmd {test_cmd!r} is a directory-level pytest, which "
            "worker-verify refuses, so NO test output reaches the evidence "
            "and any test-count claim is unprovable")
    if pr:
        # Not a thinness trigger on its own — the PR block IS real evidence
        # about state. It is recorded so --explain can say plainly why a
        # "checks pass" claim came back unsupported.
        inv["reasons"].append(
            "--pr gathers `gh pr view --json state,isDraft,headRefName,"
            "mergedAt` only, which carries NO check-run data, so a claim "
            "about CI checks passing is unprovable by construction")
    probe = probe_stdout or ""
    m = _EVIDENCE_SIZE_RE.search(probe)
    if m:
        inv["blocks"], inv["chars"] = int(m.group(1)), int(m.group(2))
        if inv["chars"] < _evidence_min_chars() or inv["blocks"] == 0:
            inv["thin"] = True
            inv["reasons"].append(
                f"the gather produced {inv['blocks']} block(s) / "
                f"{inv['chars']} chars, under the {_evidence_min_chars()}-char floor")
    for marker in EVIDENCE_ABSENT_MARKERS:
        if marker in probe:
            inv["missing"].append(marker)
    if inv["missing"]:
        inv["thin"] = True
        inv["reasons"].append("the gather reported absent evidence: "
                              + "; ".join(inv["missing"]))
    return inv

# Machine tags a reply carries for fleet bookkeeping (board-bus footer,
# rung-line, Add/Skip buttons, raw system tags) are not part of the reply's
# own content, but jev's leaked_internal/self_contradictory scoring has been
# seen reading them as internal leakage or a contradiction (see the
# 2026-09-17 loop where the SAME short reply blocked three times running
# while the lead rewrote it more carefully each time). Stripped out of the
# draft before it goes to the gate. Env-overridable as a JSON list of regex
# strings so a fleet with a different tag vocabulary is not stuck patching
# this file.
STRIP_PATTERNS_ENV = "SUPERJEV_STRIP_PATTERNS"
DEFAULT_STRIP_PATTERNS = [
    r'^\[BOARD:.*\]\s*$',
    r'^Rung(?:\s+line)?:.*$',
    r'^\[Add\]\s*\[Skip\].*$',
    r'</?[a-zA-Z][^>\n]*>',
]

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


def _hook_block_decision(flags, claim_rows=None, evidence=None):
    """The full block decision: (reasons, notes).

    `reasons` are the "key VERDICT score" strings for the flags that cross
    THIS hook's block line — empty means do not block. `notes` are
    plain-words lines explaining any flag that WOULD have blocked and was
    suppressed, for stderr, --explain and the ledger.

    Directions, because they are not symmetric (see the long comment on
    the thresholds): NOT_SUPPORTED and SELF_CONTRADICTORY block at or
    BELOW their line, OVERCLAIMS blocks at or ABOVE its line.

    Two suppressions, both of them fixes for real 2026-09-17 incidents:

    SELF_CONTRADICTORY never blocks on its own. It counts only when the
    SAME run also carries a blocking NOT_SUPPORTED or OVERCLAIMS. A calmer
    rewrite of a reply still reads as mildly self-contradictory to jev's
    scoring (hedging language does), so blocking on it alone is what
    looped the Stop gate: the same short reply blocked three times running
    on self_contradictory 0.10/0.15/0.05 while overclaim fell 0.87 ->
    0.25 -> 0.30.

    OVERCLAIMS never blocks ON ITS OWN when the run gives us no standing
    to act on it — that is, when EVERY claim-level row came back
    SUPPORTED, or when the evidence inventory says the evidence is absent
    or too thin to judge against. "The draft claims more than the
    evidence carries" is the expected, correct answer when the evidence
    carries nothing, so on its own it is a statement about OUR gather, not
    about the worker's honesty. It still prints as an advisory and still
    lands in the ledger. A blocking NOT_SUPPORTED in the same run is
    untouched: OVERCLAIMS alongside a real unsupported-claim finding
    blocks exactly as before, and nothing here weakens a fabricated quote
    (already exit 2/4 and "block" via the action map)."""
    not_supported_line = _block_not_supported_threshold()
    overclaim_line = _block_overclaim_threshold()
    notes = []
    has_not_supported_block = any(
        f["verdict"] == "NOT_SUPPORTED" and f["score"] <= not_supported_line
        for f in flags)
    has_overclaim_block = any(
        f["verdict"] == "OVERCLAIMS" and f["score"] >= overclaim_line
        for f in flags)

    # The OVERCLAIMS-alone gate.
    overclaim_suppressed = False
    if has_overclaim_block and not has_not_supported_block:
        rows = [r for r in (claim_rows or []) if r["key"].startswith("c")]
        all_supported = bool(rows) and all(r["verdict"] == "SUPPORTED" for r in rows)
        thin = bool(evidence) and evidence.get("thin") is True
        if all_supported:
            overclaim_suppressed = True
            notes.append(
                f"OVERCLAIMS was the only blocking flag and all {len(rows)} "
                "claim(s) came back SUPPORTED — advisory, not a block")
        elif thin:
            overclaim_suppressed = True
            # Kept to one line on purpose: this string goes into stderr, the
            # ledger and the session's own context. The full list of gaps is
            # what --explain is for.
            first = (evidence.get("reasons") or ["no evidence was gathered"])[0]
            headline = first.split(",")[0].split(" so ")[0].strip()
            notes.append(
                "OVERCLAIMS was the only blocking flag and the evidence "
                f"cannot carry a verdict ({headline}) — advisory, not a "
                "block; run with --explain for the full gather")
        if overclaim_suppressed:
            has_overclaim_block = False

    reasons = []
    for f in flags:
        v, s, k = f["verdict"], f["score"], f["key"]
        if v == "NOT_SUPPORTED" and s <= not_supported_line:
            blocked = True
        elif v == "OVERCLAIMS" and s >= overclaim_line:
            blocked = not overclaim_suppressed
        elif v == "SELF_CONTRADICTORY" and s <= BLOCK_SELF_CONTRADICTORY:
            blocked = has_not_supported_block or has_overclaim_block
        else:
            blocked = False
        if blocked:
            reasons.append(f"{k} {v} {s:.2f}")
    return reasons, notes


def _hook_block_reasons(flags, claim_rows=None, evidence=None):
    """Just the block reasons from _hook_block_decision. Kept as the name
    every caller and test already uses."""
    reasons, _notes = _hook_block_decision(flags, claim_rows, evidence)
    return reasons


def _strip_patterns():
    """The compiled-pattern source strings to strip from a draft before it
    goes to the gate: SUPERJEV_STRIP_PATTERNS (a JSON list of regex
    strings), if set and parseable, else DEFAULT_STRIP_PATTERNS. Never
    raises: a bad env value falls back to the default list."""
    raw = os.environ.get(STRIP_PATTERNS_ENV)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and parsed:
                return [str(p) for p in parsed]
        except (ValueError, TypeError):
            pass
    return DEFAULT_STRIP_PATTERNS


def _strip_machine_tags(text):
    """Remove fleet machine tags — a trailing `[BOARD: ...]` line, a
    `Rung:`/`Rung line:` line, `[Add] [Skip]` button lines, and any
    `<...>` system tag — from a reply before it is checked against the
    claim gate. These are bookkeeping the fleet's own tooling reads, not
    content the reply's author is claiming, and jev's leaked_internal /
    self_contradictory scoring has misread them as internal leakage or a
    contradiction. Line-level patterns that consume a whole line drop
    that line entirely rather than leaving a blank line behind. Never
    raises: an unparseable pattern in the list is skipped, not fatal."""
    if not text:
        return text
    patterns = []
    for p in _strip_patterns():
        try:
            patterns.append(re.compile(p))
        except re.error:
            continue
    if not patterns:
        return text
    out = []
    for line in text.split("\n"):
        stripped = line
        for pat in patterns:
            stripped = pat.sub("", stripped)
        if line.strip() and not stripped.strip():
            # The whole line was a machine tag — drop it, not just blank it.
            continue
        out.append(stripped)
    return "\n".join(out)

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
    if getattr(a, "prefilter", None) is not None:
        cmd += ["--prefilter", str(a.prefilter)]
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


# A background Agent/Task spawn's PostToolUse tool_response is a DICT, not a
# string or a content-block list — {"status": "teammate_spawned", "prompt":
# "<the whole worker brief>", ...}. The 2026-09-17 live finding this fixes:
# the shim used to stringify that whole dict, and jev's OVERCLAIMS scoring
# judged the worker's own BRIEF (sitting under "prompt") as if it were the
# worker's finished report. It never is — the real report arrives later,
# inside a USER turn, as a `<teammate-message>` block in the Claude Code
# teammate mailbox (see `hook prompt-verify`), which PostToolUse never sees
# at all. So a dict tool_response is read narrowly: only result/report/
# content/text ever count as report text; "prompt" is never read as one.
SPAWN_DICT_STATUSES = frozenset({
    "teammate_spawned", "launched", "running", "spawned",
})
_DICT_REPORT_KEYS = ("result", "report", "content", "text")


def _report_from_dict_tool_response(d):
    """(text, is_spawn_dict) for a dict-shaped tool_response.

    `text` is the value of the first of result/report/content/text that
    carries usable text (each resolved with _extract_text_blocks, so a
    nested list-of-blocks or dict under one of those keys still works),
    else None. `is_spawn_dict` is True only when `text` is None AND
    either "status" names a known spawn/launch state (SPAWN_DICT_STATUSES,
    case-insensitive) or none of the four report keys were present at all
    — the shape a background spawn's launch acknowledgement actually has.
    A dict that names none of those keys AND carries no recognised status
    is still treated as a spawn dict (conservative: there is nothing here
    that looks like a report, so there is nothing safe to check)."""
    for k in _DICT_REPORT_KEYS:
        if k in d:
            got = _extract_text_blocks(d.get(k))
            if got:
                return got, False
    status = d.get("status")
    known_status = isinstance(status, str) and status.strip().lower() in SPAWN_DICT_STATUSES
    has_any_key = any(k in d for k in _DICT_REPORT_KEYS)
    return None, (known_status or not has_any_key)


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


def _last_user_prompt(transcript_path):
    """The most recent user message text in a Claude Code transcript JSONL
    file, or None. Best-effort, same shape rules as _last_assistant_text.
    Used by the unchecked-claims path: when a turn produced no tool_result
    evidence, the user's own prompt is the only text the gate can weigh the
    reply against."""
    for rec in reversed(_read_transcript_records(transcript_path)):
        msg = rec.get("message") if isinstance(rec, dict) else None
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            continue  # a tool_result carrier, not a human prompt
        text = _extract_text_blocks(content)
        if text:
            return text
    return None


def _door_reports_checkable_claim(code, stdout, stderr):
    """Did the wrapped gate door find at least one claim to check? jev.py's
    reply kit exits on "no checkable claims" (a usage-style error, the
    sentence on stderr, no claim table) when the draft holds nothing four
    words or longer; otherwise it prints one "cN VERDICT score" row per
    claim. A timeout or a missing door (REFUSED/124) is not a report of a
    claim either."""
    if code in (REFUSED, NOT_BUILT, TIMEOUT_EXIT_CODE):
        return False
    both = (stdout or "") + "\n" + (stderr or "")
    if "no checkable claims" in both:
        return False
    if re.search(r"^\s*c\d+\s+[A-Z][A-Z_]*\s+\d+\.\d+", stdout or "", re.MULTILINE):
        return True
    return False


UNCHECKED_ADVISORY = ("super-jev: reply makes claims with no tool evidence this turn — "
                      "mark them unverified or gather evidence")


def _ack_patterns():
    """The launch/ack phrase list verify's pre-check matches against,
    lowercased. SUPERJEV_HOOK_ACK_PATTERNS (comma-separated), if set,
    REPLACES the built-in list entirely; otherwise DEFAULT_ACK_PATTERNS."""
    override = os.environ.get(HOOK_ACK_PATTERNS_ENV)
    if override:
        return [p.strip().lower() for p in override.split(",") if p.strip()]
    return DEFAULT_ACK_PATTERNS


def _verify_min_chars():
    try:
        n = int(os.environ.get(VERIFY_MIN_CHARS_ENV, DEFAULT_VERIFY_MIN_CHARS))
        return n if n > 0 else DEFAULT_VERIFY_MIN_CHARS
    except ValueError:
        return DEFAULT_VERIFY_MIN_CHARS


def _is_launch_ack(text):
    """(is_ack, reason) for one tool_response text. True when this looks
    like an Agent/Task launch acknowledgement rather than a worker's final
    report: it contains one of the ack/launch phrases (see
    _ack_patterns()), OR it is shorter than SUPERJEV_VERIFY_MIN_CHARS
    (default 200) and carries none of a real report's own markers
    (COMPLETE/INCOMPLETE/verdict/a test count — see _REPORT_MARKER_RE).
    `text` is assumed non-empty (callers only reach this after
    _hook_report_text already returned something)."""
    low = text.lower()
    for pat in _ack_patterns():
        if pat in low:
            return True, f"matches ack pattern {pat!r}"
    min_chars = _verify_min_chars()
    if len(text) < min_chars and not _REPORT_MARKER_RE.search(text):
        return True, f"shorter than {min_chars} chars with no report markers"
    return False, ""


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


def _hook_log(note, exit_code=0, skipped=False, flags=None, unchecked=False, reason=None,
              hook_mode=True, source=None):
    """One ledger line for a hook decision. `exit_code` is the real code
    this hook invocation is about to return (never hard-coded to 0) —
    2 for a block, 0 for everything else, including a fail-open skip.
    `skipped=True` marks a run where the wrapped door never executed at
    all (bad/empty input, no derivable evidence, a caught exception, a
    timeout, a bad hook invocation, or a launch-ack the pre-check caught);
    `skipped=False` means gate/verify actually ran and this is its
    allow/advisory/block outcome. `flags`, if given, is the list of
    {"key","verdict","score"} dicts this run's captured stdout carried
    (see _parse_strong_flags) — every block AND every advisory line
    carries whatever was parsed, even an empty list, so the ledger always
    shows what was actually checked. `reason`, if given, is a short
    machine-matchable tag (e.g. "launch-ack") distinct from the free-text
    `note`. `hook_mode` is False and `source` is "manual" for a `hook
    verify --from-file` run — same ledger shape, but this call did not
    come from a real Claude Code hook firing."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "door": "hook",
        "argv": [],
        "exit_code": exit_code,
        "ms": 0,
        "json_mode": False,
        "hook_mode": hook_mode,
        "skipped": skipped,
        "note": note,
    }
    if flags is not None:
        entry["flags"] = flags
    if unchecked:
        entry["unchecked"] = True
    if reason is not None:
        entry["reason"] = reason
    if source is not None:
        entry["source"] = source
    ledger_append(entry)


def _pr_url_from_worktree(worktree, pr_num):
    """https://github.com/<org>/<repo>/pull/<pr_num>, built from `git -C
    worktree remote get-url origin`, or None if there is no worktree, no
    PR number, no origin remote, or the remote is not a recognisable
    GitHub URL. worker-verify's own atom extraction only turns a FULL
    GitHub pull URL into a `gh pr view` evidence block (verify.py's
    _PR_URL regex; a bare PR number in the text is not enough) — this is
    the only thing that makes a --pr number produce real PR evidence."""
    if not worktree or not pr_num:
        return None
    try:
        proc = subprocess.run(["git", "-C", str(worktree), "remote", "get-url", "origin"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    m = re.search(r'github\.com[:/]+([\w.\-]+)/([\w.\-]+?)(?:\.git)?/?$', proc.stdout.strip())
    if not m:
        return None
    return f"https://github.com/{m.group(1)}/{m.group(2)}/pull/{pr_num}"


def _evidence_probe(report_path, worktree=None, test_cmd=""):
    """A FREE gather: worker-verify's own `--dry-run`, which assembles every
    evidence block and prints "EVIDENCE: N blocks, M chars" plus each
    block, and never calls the model. Returns its captured stdout, or ""
    when the probe could not run.

    It is only ever run when the answer changes something — a block that
    would rest on OVERCLAIMS alone, or an explicit --explain — so the
    common path still pays for exactly one gather."""
    try:
        ns = argparse.Namespace(report=report_path, worktree=worktree,
                                test_cmd=test_cmd or "", paths=[], dry_run=True,
                                json=False, hook_mode=True)
        result = cmd_verify(ns)
    except Exception:
        return ""
    if isinstance(result, tuple) and len(result) == 3:
        return result[1] or ""
    return ""


def _print_explain(door, code, action, flags, claim_rows, evidence, notes,
                   block_reasons):
    """The `--explain` report: what evidence was gathered, how big it was,
    every claim row, and which rule decided. Written so a human can see
    WHY without reading this file."""
    ev = evidence or {}
    print(f"\n--- super-jev {door} --explain ---")
    size = ("not measured (no dry-run probe was needed)"
            if ev.get("chars") is None else
            f"{ev['blocks']} block(s), {ev['chars']} chars")
    print(f"  evidence gathered : {size}")
    print(f"  worktree          : {ev.get('worktree') or '(none)'}")
    print(f"  test command      : {ev.get('test_cmd') or '(none)'}")
    print(f"  pull request      : {ev.get('pr') if ev.get('pr') else '(none)'}")
    print(f"  evidence too thin : {'YES' if ev.get('thin') else 'no'}")
    for r in ev.get("reasons") or []:
        print(f"      - {r}")
    for m in ev.get("missing") or []:
        print(f"      - MISSING: {m}")
    if claim_rows:
        print("\n  claim                verdict          confidence")
        for row in claim_rows:
            print(f"      {row['key']:<17s} {row['verdict']:<16s} {row['score']:.2f}")
    else:
        print("\n  claim rows        : none found in the door's output")
    other = [f for f in (flags or []) if not f["key"].startswith("c")]
    if other:
        print("\n  draft-level flags")
        for f in other:
            print(f"      {f['key']:<17s} {f['verdict']:<16s} {f['score']:.2f}")
    print(f"\n  thresholds        : NOT_SUPPORTED blocks at or below "
          f"{_block_not_supported_threshold():.2f}, OVERCLAIMS at or above "
          f"{_block_overclaim_threshold():.2f}")
    print(f"  note              : {NOT_SUPPORTED_DIRECTION_NOTE}")
    print(f"\n  door exit         : {code}")
    print(f"  decision          : {action.upper()}")
    if block_reasons:
        print(f"  blocking on       : {'; '.join(block_reasons)}")
    for n in notes or []:
        print(f"  suppressed        : {n}")
    print("--- end --explain ---\n")


NO_EVIDENCE_ADVISORY = ("super-jev hook verify --from-file: no evidence source given; "
                        "run with --worktree/--test-cmd/--pr")


def _hook_verify_from_file(door, path, worktree=None, test_cmd="", pr=None,
                           explain=False):
    """`hook verify --from-file <report.txt>`: run the exact same verify
    check `hook verify` runs off a real PostToolUse payload, but against a
    report file that arrived out of band — a worker's final report message
    the lead is holding, not a hook firing. Prints the same one-line
    allow/advisory/block verdict `hook` prints, and writes the same shape
    of ledger line, except hook_mode=False and source="manual" so it is
    distinguishable from a real hook invocation. Never runs the launch-ack
    pre-check — a caller passing --from-file has already decided this file
    is a report worth checking.

    `worktree`/`test_cmd`/`pr` let this manual door gather its OWN
    evidence, the same way a real PostToolUse hook derives it from the
    payload — passed straight through to worker-verify's own --worktree/
    --test-cmd flags (verify.py's real argparse; see FLEET_VERIFY_PY).
    `pr`, a PR number, has no equivalent verify.py flag — instead it is
    turned into a full GitHub pull URL via `_pr_url_from_worktree` (which
    needs `worktree` to resolve the origin remote) and appended to the
    report text, so worker-verify's own atom extraction picks it up and
    runs `gh pr view` on it exactly as if the report had named the URL
    itself. `worktree` falls back to SUPERJEV_HOOK_WORKTREE when not
    passed explicitly. If, after that fallback, NONE of worktree/test_cmd/
    pr resolve to anything, there is no evidence source at all — this
    prints an advisory and returns 0 WITHOUT ever calling the verify door,
    rather than running it blind and letting an unrelated old default
    verdict stand in for "nothing was checked"."""
    if door != "verify":
        msg = "super-jev hook: --from-file is only supported for `hook verify`"
        print(msg, file=sys.stderr)
        _hook_log(f"{door}: --from-file refused — only verify supports it", exit_code=0,
                 skipped=True, reason="from-file-wrong-door", hook_mode=False, source="manual")
        return 0
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        print(f"super-jev hook: could not read --from-file {path!r}: {exc}", file=sys.stderr)
        _hook_log(f"verify: --from-file {path!r} unreadable ({exc}) — fail-open",
                 exit_code=0, skipped=True, reason="from-file-unreadable",
                 hook_mode=False, source="manual")
        return 0
    if not text.strip():
        _hook_log(f"verify: --from-file {path!r} is empty — fail-open", exit_code=0,
                 skipped=True, reason="from-file-empty", hook_mode=False, source="manual")
        return 0

    resolved_worktree = worktree or os.environ.get(HOOK_WORKTREE_ENV)
    if not resolved_worktree and not test_cmd and not pr:
        print(NO_EVIDENCE_ADVISORY)
        _hook_log(f"verify: --from-file {path!r} — no evidence source given (no "
                 "--worktree/--test-cmd/--pr, and SUPERJEV_HOOK_WORKTREE is not set) — "
                 "advisory, door not run", exit_code=0, skipped=True,
                 reason="from-file-no-evidence", hook_mode=False, source="manual")
        return 0

    pr_url = _pr_url_from_worktree(resolved_worktree, pr) if pr else None
    if pr_url:
        text += f"\n\nRelated pull request: {pr_url}"
    elif pr:
        text += f"\n\nRelated pull request: PR #{pr}"

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
    tmp_path = tmp.name
    try:
        tmp.write(text)
        tmp.close()
        ns = argparse.Namespace(report=tmp_path, worktree=resolved_worktree,
                                test_cmd=test_cmd or "", paths=[], dry_run=False, json=False,
                                hook_mode=True)
        code, door_out, door_err = cmd_verify(ns)

        flags = _parse_strong_flags(door_out)
        claim_rows = _parse_claim_rows(door_out)
        evidence = _evidence_inventory(test_cmd=test_cmd, worktree=resolved_worktree,
                                       pr=pr)
        block_reasons, notes = _hook_block_decision(flags, claim_rows, evidence)

        # Would this block rest on OVERCLAIMS alone? If so, the answer turns
        # on whether we actually gathered anything to judge against — so
        # spend the free `--dry-run` gather and decide on real numbers
        # rather than on what we hoped the flags meant. Also run it for
        # --explain, where the whole point is showing the human the size.
        overclaim_only = (
            any(f["verdict"] == "OVERCLAIMS"
                and f["score"] >= _block_overclaim_threshold() for f in flags)
            and not any(f["verdict"] == "NOT_SUPPORTED"
                        and f["score"] <= _block_not_supported_threshold()
                        for f in flags))
        if explain or overclaim_only:
            probe = _evidence_probe(tmp_path, resolved_worktree, test_cmd)
            if probe:
                evidence = _evidence_inventory(test_cmd=test_cmd,
                                               worktree=resolved_worktree, pr=pr,
                                               probe_stdout=probe)
                block_reasons, notes = _hook_block_decision(flags, claim_rows, evidence)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    action = VERIFY_HOOK_ACTION.get(code, "advisory")
    if block_reasons:
        action = "block"
    elif notes:
        # A suppressed block is never a silent allow. It is an advisory, so
        # the session sees the flag and the reason it did not block.
        action = "advisory"
    if explain:
        _print_explain("verify", code, action, flags, claim_rows, evidence, notes,
                       block_reasons)
    note_tail = (" — " + "; ".join(notes)) if notes else ""
    if action == "allow":
        _hook_log(f"verify: allow (exit {code}) [from-file {path!r}]", exit_code=0,
                 flags=flags, hook_mode=False, source="manual")
        return 0
    if action == "block":
        reason = f"super-jev verify blocked this (exit {code})"
        if block_reasons:
            reason += ": " + "; ".join(block_reasons)
        print(reason, file=sys.stderr)
        _hook_log(f"verify: block (exit {code}) [from-file {path!r}]" +
                 (f" — strong flags: {'; '.join(block_reasons)}" if block_reasons else ""),
                 exit_code=2, flags=flags, hook_mode=False, source="manual")
        return 2
    advisory = f"super-jev verify advisory (exit {code}){note_tail}"
    print(advisory)
    _hook_log(f"verify: advisory (exit {code}) [from-file {path!r}]{note_tail}",
             exit_code=0, flags=flags, hook_mode=False, source="manual")
    return 0


# ---------------------------------------------------------- prompt-verify
#
# A background Agent/Task spawn's PostToolUse event never carries the
# worker's real report — see _report_from_dict_tool_response above. The
# worker's actual final report lands LATER, inside the USER turn, as a
# `<teammate-message teammate_id="X" ...>...</teammate-message>` block —
# Claude Code's own teammate mailbox rendering. `hook prompt-verify` is the
# door that actually sees that: wired to UserPromptSubmit, it reads every
# teammate-message block out of the next user turn and verifies each one
# that looks like a real report.
_TEAMMATE_MSG_RE = re.compile(r'<teammate-message\b([^>]*)>(.*?)</teammate-message>',
                              re.DOTALL)
_TEAMMATE_ID_RE = re.compile(r'teammate_id="([^"]*)"')
# What makes a teammate-message block worth checking: a COMPLETE/INCOMPLETE
# word, a "PR #N"/"pull/N" mention, or a test count ("6 tests", "4 passed").
_REPORT_TRIGGER_RE = re.compile(
    r'\b(?:COMPLETE|INCOMPLETE)\b|PR\s*#?\d+|\bpull/\d+\b|'
    r'\d+\s*(?:tests?|test\s+cases?|passed|failed)', re.IGNORECASE)
_ABS_PATH_RE = re.compile(r'(/(?:[\w.\-]+/)+[\w.\-]*)')
_PR_NUM_RE = re.compile(r'PR\s*#(\d+)|\bpull/(\d+)\b', re.IGNORECASE)
_TEST_PHRASE_RE = re.compile(
    r'\b(npm(?:\s+run)?\s+test\S*|pytest\S*|python3?\s+-m\s+pytest\S*)', re.IGNORECASE)


def _derive_evidence_from_report_text(text):
    """{"worktree", "pr", "test_cmd"} auto-derived from a report's own
    text: any absolute path mentioned (-> worktree — the first one found,
    good enough for the fleet's one-worktree-per-task convention), any
    "PR #N" / "pull/N" mention (-> pr, an int), and — ONLY when a worktree
    was found, per the brief — any npm test/pytest phrase (-> test_cmd).
    Any of the three can come back None/""/empty; that is not an error,
    it just means this report's text did not mention that kind of
    evidence."""
    worktree = None
    m = _ABS_PATH_RE.search(text)
    if m:
        worktree = m.group(1).rstrip("/.,;:)")
    pr = None
    m = _PR_NUM_RE.search(text)
    if m:
        pr = int(m.group(1) or m.group(2))
    test_cmd = ""
    if worktree:
        m = _TEST_PHRASE_RE.search(text)
        if m:
            test_cmd = m.group(1)
    return {"worktree": worktree, "pr": pr, "test_cmd": test_cmd}


def cmd_hook_prompt_verify(a):
    """`hook prompt-verify`: reads a UserPromptSubmit payload on stdin,
    finds every `<teammate-message ...>...</teammate-message>` block in
    payload["prompt"], and for each one that carries a report marker
    (COMPLETE/INCOMPLETE/PR/test-count — see _REPORT_TRIGGER_RE) runs
    `verify` against it with evidence auto-derived from the report's own
    text (see _derive_evidence_from_report_text). A `pr` number is turned
    into a real `gh pr view` evidence block the same way --from-file does
    it: via _pr_url_from_worktree, appended to the report text before
    worker-verify's own atom extraction ever sees it.

    ALWAYS exits 0 — advisory only. A UserPromptSubmit hook that blocks
    eats the user's own next message along with it, not just a check
    result, so this must never return a non-zero exit under any
    circumstance, including an unexpected exception.

    Prints one line per report checked, to stdout:
        "super-jev verify <teammate_id>: CLEAN|READ|REJECT — <flags>"
    and writes one ledger line per report, source="teammate-message". A
    payload with no "prompt" text, unparseable JSON, or a prompt with no
    teammate-message blocks (or none carrying a report marker) prints
    nothing and logs one skipped ledger line instead."""
    try:
        try:
            raw = sys.stdin.read()
        except Exception:
            _hook_log("prompt-verify: could not read stdin — fail-open", skipped=True)
            return 0
        if not raw or not raw.strip():
            _hook_log("prompt-verify: empty stdin — fail-open", skipped=True)
            return 0
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("payload is not a JSON object")
        except (ValueError, TypeError):
            _hook_log("prompt-verify: non-JSON stdin — fail-open", skipped=True)
            return 0
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            _hook_log("prompt-verify: no usable 'prompt' field — fail-open", skipped=True)
            return 0
        blocks = _TEAMMATE_MSG_RE.findall(prompt)
        if not blocks:
            _hook_log("prompt-verify: no <teammate-message> blocks in prompt — "
                     "nothing to check", skipped=True, reason="no-teammate-messages")
            return 0

        any_checked = False
        for attrs, body in blocks:
            idm = _TEAMMATE_ID_RE.search(attrs)
            teammate_id = idm.group(1) if idm else "unknown"
            body = body.strip()
            if not body or not _REPORT_TRIGGER_RE.search(body):
                _hook_log(f"prompt-verify: {teammate_id} — no report marker "
                         "(COMPLETE/INCOMPLETE/PR/test-count) in block, skipped",
                         skipped=True, reason="no-report-marker", source="teammate-message")
                continue

            derived = _derive_evidence_from_report_text(body)
            report_text = body
            pr_url = (_pr_url_from_worktree(derived["worktree"], derived["pr"])
                     if derived["pr"] else None)
            if pr_url:
                report_text += f"\n\nRelated pull request: {pr_url}"
            elif derived["pr"]:
                report_text += f"\n\nRelated pull request: PR #{derived['pr']}"

            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                              encoding="utf-8")
            tmp_path = tmp.name
            try:
                tmp.write(report_text)
                tmp.close()
                ns = argparse.Namespace(report=tmp_path, worktree=derived["worktree"],
                                        test_cmd=derived["test_cmd"] or "", paths=[],
                                        dry_run=False, json=False, hook_mode=True)
                code, door_out, door_err = cmd_verify(ns)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            flags = _parse_strong_flags(door_out)
            block_reasons = _hook_block_reasons(flags)
            label = {0: "CLEAN", 3: "READ", 4: "REJECT"}.get(code, "READ")
            if block_reasons and label != "REJECT":
                label = "REJECT"
            flag_str = ("; ".join(f"{f['key']} {f['verdict']} {f['score']:.2f}"
                                  for f in flags) or "no flags")
            used = ", ".join(f"--{k} {v}" for k, v in
                             (("worktree", derived["worktree"]),
                              ("test-cmd", derived["test_cmd"]),
                              ("pr", derived["pr"])) if v) or "no evidence derived"
            print(f"super-jev verify {teammate_id}: {label} — {flag_str} ({used})")
            _hook_log(f"prompt-verify: {teammate_id} — {label} (exit {code}) [{used}]",
                     exit_code=0, skipped=False, flags=flags, hook_mode=True,
                     source="teammate-message")
            any_checked = True
        if not any_checked:
            _hook_log("prompt-verify: no teammate-message block carried a report marker",
                     skipped=True, reason="no-checkable-reports")
        return 0
    except Exception as exc:  # advisory-only contract: never raise, never block
        _hook_log(f"prompt-verify: unexpected error ({exc.__class__.__name__}) — fail-open",
                 skipped=True)
        return 0


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
    if door == "prompt-verify":
        return cmd_hook_prompt_verify(a)
    from_file = getattr(a, "from_file", None)
    if from_file:
        return _hook_verify_from_file(door, from_file, worktree=getattr(a, "worktree", None),
                                      test_cmd=getattr(a, "test_cmd", "") or "",
                                      pr=getattr(a, "pr", None),
                                      explain=bool(getattr(a, "explain", False)))
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

    def _hook_unchecked(tp, evidence, tmp_ev_prompt_found):
        """The no-tool-evidence path: run the gate against the user's last
        prompt, advise once if the door reports a checkable claim, stay
        silent if it reports none, log unchecked=True, exit 0 always."""
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
        checkable = _door_reports_checkable_claim(code, door_out, door_err)
        source = "last user prompt" if tmp_ev_prompt_found else "no prompt found"
        if checkable:
            print(UNCHECKED_ADVISORY)
            _hook_log(f"gate: unchecked — no tool evidence derivable from transcript_path "
                      f"({tp!r}); gate ran against {source}, checkable claim reported "
                      f"(exit {code}), advisory printed", exit_code=0,
                      flags=_parse_strong_flags(door_out), unchecked=True)
        else:
            _hook_log(f"gate: unchecked — no tool evidence derivable from transcript_path "
                      f"({tp!r}); gate ran against {source}, no checkable claim "
                      f"(exit {code}), silent", exit_code=0, unchecked=True)
        return 0

    try:
        if door == "gate":
            text = _hook_text(payload, ["last_assistant_message", "draft", "text", "prompt"])
            if text is None:
                _hook_log("gate: no usable text field in payload or transcript — "
                          "fail-open", skipped=True)
                return 0
            text = _strip_machine_tags(text)

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
                    # transcript with any tool_result content. The reply
                    # is then, by construction, unverifiable this turn —
                    # which used to mean silence, so a reply full of
                    # confident claims was never checked at all. Now the
                    # gate still runs, with the user's last prompt as the
                    # only evidence (a real usage error from the wrapped
                    # tool with NO evidence file would share the "block"
                    # exit number, so a file is always written, even an
                    # empty one). The door's job here is only to say
                    # whether the reply carries a checkable claim: if it
                    # does, ONE advisory line; if it does not, silence.
                    # Never a block on this path — exit 0 always.
                    prompt = _last_user_prompt(tp) if isinstance(tp, str) and tp else None
                    tmp_ev = tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                                          delete=False, encoding="utf-8")
                    evidence_tmp_path = tmp_ev.name
                    tmp_ev.write(prompt or "(no tool evidence and no user prompt found "
                                           "in the transcript this turn)")
                    tmp_ev.close()
                    evidence = [evidence_tmp_path]
                    evidence_source = "unchecked: last user prompt"
                    return _hook_unchecked(tp, evidence, tmp_ev_prompt_found=prompt is not None)

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

            tool_response = payload.get("tool_response")
            if isinstance(tool_response, dict):
                dict_text, is_spawn = _report_from_dict_tool_response(tool_response)
                if dict_text is not None:
                    text = dict_text
                elif is_spawn:
                    status = tool_response.get("status")
                    _hook_log(
                        "verify: skipped — tool_response is a spawn/launch dict"
                        + (f" (status={status!r})" if status else "") +
                        "; the worker's own report is not here yet, it arrives later "
                        "in a <teammate-message> block (see `hook prompt-verify`)",
                        exit_code=0, skipped=True, reason="spawn-dict")
                    return 0
                else:
                    text = _hook_report_text(payload)
            else:
                text = _hook_report_text(payload)
            if text is None:
                _hook_log("verify: no usable report text (tool_response/report/text/"
                         "message/transcript) — fail-open", skipped=True)
                return 0

            is_ack, ack_reason = _is_launch_ack(text)
            if is_ack:
                _hook_log(f"verify: skipped — {ack_reason}", exit_code=0, skipped=True,
                         reason="launch-ack")
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
        # claim_rows carries the SUPPORTED rows too, which _parse_strong_flags
        # drops — without them the OVERCLAIMS-alone gate could never see
        # that every claim was in fact supported. No evidence inventory is
        # passed on this path: a real hook payload gives us no --test-cmd
        # and no --pr (see the hardcoded test_cmd="" above), so there is no
        # gather to measure. That also means a hook-driven verify can NEVER
        # prove a test-count claim — docs/hooks.md says so in plain words.
        flags = _parse_strong_flags(door_out)
        claim_rows = _parse_claim_rows(door_out)
        block_reasons, block_notes = _hook_block_decision(flags, claim_rows)

        action_map = GATE_HOOK_ACTION if door == "gate" else VERIFY_HOOK_ACTION
        action = action_map.get(code, "advisory")
        if block_reasons:
            action = "block"

        # stop_hook_active=true is Claude Code's own signal that this Stop
        # event is a RE-RUN — a previous hook already blocked once this
        # turn and the transcript was already re-run because of it. A gate
        # that can still say "block" on a re-run has no way to ever let
        # the turn end: the 2026-09-17 loop was exactly this, the same
        # short reply blocked three times running while the lead rewrote
        # it more carefully each pass. On a re-run this hook never blocks
        # again — advisory at most, with the flags still printed so the
        # session can see why the gate is unhappy, and the ledger line
        # says plainly this was a fail-open, not a real allow.
        if door == "gate" and payload.get("stop_hook_active") is True and action == "block":
            action = "block-forced-advisory"
        if action == "allow":
            _hook_log(f"{door}: allow (exit {code})", exit_code=0, flags=flags)
            return 0
        if action == "block-forced-advisory":
            reason_bits = "; ".join(block_reasons) if block_reasons else f"exit {code}"
            advisory = f"super-jev gate: second pass (stop_hook_active) — advisory only, would have blocked on: {reason_bits}"
            print(advisory)
            _hook_log(f"gate: second pass, advisory only (exit {code}) — would have "
                     f"blocked on: {reason_bits}", exit_code=0, flags=flags)
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
        note_tail = (" — " + "; ".join(block_notes)) if block_notes else ""
        advisory = f"super-jev {door} advisory (exit {code}){note_tail}"
        print(advisory)
        _hook_log(f"{door}: advisory (exit {code}){note_tail}", exit_code=0, flags=flags)
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
    hk.add_argument("door", choices=["gate", "verify", "prompt-verify"],
                    help="which check to run against the hook payload; prompt-verify "
                         "reads a UserPromptSubmit payload and checks every "
                         "<teammate-message> block in it")
    hk.add_argument("--from-file", dest="from_file", default=None,
                    help="verify only: run the same check against a report file that "
                         "arrived out of band, instead of a hook payload on stdin")
    hk.add_argument("--worktree", default=None,
                    help="verify --from-file only: the repo the work happened in, "
                         "passed straight to worker-verify's own --worktree")
    hk.add_argument("--test-cmd", dest="test_cmd", default="",
                    help="verify --from-file only: the test command, passed straight "
                         "to worker-verify's own --test-cmd")
    hk.add_argument("--explain", action="store_true",
                    help="verify --from-file only: print how much evidence was "
                         "gathered, the per-claim table and which rule decided, "
                         "so a human can see why it blocked or did not")
    hk.add_argument("--pr", type=int, default=None,
                    help="verify --from-file only: a PR number, turned into a full "
                         "GitHub pull URL (via --worktree's origin remote) and "
                         "appended to the report so worker-verify runs `gh pr view` on it")
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
    ft.add_argument("--prefilter", type=int,
                    help="keep only the top N records by local narrowing before the "
                         "one provider call (harness default 8; 0 disables)")
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
