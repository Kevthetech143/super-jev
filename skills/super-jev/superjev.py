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
import uuid
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

# ---------------------------------------------------------- gate v3: wide
# evidence window
#
# Measured on the 40-case gate-bench-20260917 "wide" read (previous 2
# turns' tool_result text + session receipts, whole file capped): widening
# the window weakens the per-claim NOT_SUPPORTED/CONTRADICTED signal (more
# surrounding text dilutes it) but SHARPENS the draft-level OVERCLAIMS
# flag — it stops being a symptom of a thin gather and starts reading as a
# real "the draft claims more than the wide evidence carries" signal. See
# docs/hooks.md ("gate v3 — wide window") for the full write-up, including
# the cliff: 0.85 over-blocks truths on this bench (t01 sits at 0.85 on
# the wide read), 0.90 is the measured line.
PREV_TURNS_ENV = "SUPERJEV_PREV_TURNS"
DEFAULT_PREV_TURNS = 2
EVIDENCE_CAP_BYTES_ENV = "SUPERJEV_EVIDENCE_CAP_BYTES"
DEFAULT_EVIDENCE_CAP_BYTES = 24_576  # 24 KB, the whole assembled window
RULE_ENV = "SUPERJEV_RULE"
DEFAULT_RULE = "v3"
BLOCK_OVERCLAIM_ENV = "SUPERJEV_BLOCK_OVERCLAIM"
DEFAULT_BLOCK_OVERCLAIM = 0.90

# PostToolUse verify must never guess a worktree from the payload's own cwd
# (that field describes the lead session, not necessarily the worker's
# tree). This env var is the only non-payload source honoured.
HOOK_WORKTREE_ENV = "SUPERJEV_HOOK_WORKTREE"

# ---------------------------------------------------------------- token accounting
#
# Every fleet door built on jev.py prints one header line per model call:
#   "jev jev-1.13.0 · 1 chunk(s) · 2064 in_tok · 436ms"
# (see ~/.claude/skills/jev-check/lib/jev.py's _print_reply/main, and
# worker-verify's own report table, which prints the identical line because
# it also calls jev.py under the hood). run_door() parses every such line
# out of a door's CAPTURED stdout+stderr and folds the totals into that
# call's ledger entry — nothing here makes a network call of its own.
#
# The $/Mtok rate is TypeSafe's own published number (see CAPABILITIES.md,
# "OPERATIONS" section, checked against docs.typesafe.ai on 2026-09-16):
# input $0.042 per million tokens, output not billed at all. It is a
# DEFAULT, not a hardcoded constant — SUPERJEV_INPUT_USD_PER_MTOK overrides
# it the day TypeSafe's preview pricing changes, with no code edit needed.
INPUT_USD_PER_MTOK_ENV = "SUPERJEV_INPUT_USD_PER_MTOK"
DEFAULT_INPUT_USD_PER_MTOK = 0.042

# The measured Jev input ceiling is 32,768 tokens (CAPABILITIES.md, "HARD
# INPUT CEILING"); this cap sits a hair under it so a door call still has
# room for the question battery's own overhead. SUPERJEV_INPUT_CAP_TOK
# overrides it.
INPUT_CAP_TOK_ENV = "SUPERJEV_INPUT_CAP_TOK"
DEFAULT_INPUT_CAP_TOK = 32_000

_JEV_HEADER_RE = re.compile(
    r"jev\s+(\S+)\s*\xb7\s*(\d+)\s*chunk\(s\)\s*\xb7\s*(\d+)\s*in_tok\s*\xb7\s*(\d+)ms")


def _input_usd_per_mtok():
    try:
        return float(os.environ.get(INPUT_USD_PER_MTOK_ENV, DEFAULT_INPUT_USD_PER_MTOK))
    except ValueError:
        return DEFAULT_INPUT_USD_PER_MTOK


def _input_cap_tok():
    try:
        n = int(os.environ.get(INPUT_CAP_TOK_ENV, DEFAULT_INPUT_CAP_TOK))
        return n if n > 0 else DEFAULT_INPUT_CAP_TOK
    except ValueError:
        return DEFAULT_INPUT_CAP_TOK


def _estimate_tokens(text):
    """The same cheap chars/4 estimate used everywhere a real token count
    is not available yet — good enough to decide whether to warn/truncate
    BEFORE paying for a call; the real count comes back in the door's own
    header line afterwards and is what the ledger records."""
    return len(text or "") // 4


def parse_jev_headers(text):
    """Every 'jev <model> · N chunk(s) · N in_tok · Nms' header line found
    in `text` (a door's captured stdout, or stdout+stderr joined). Returns
    a list of {"model","chunks","in_tok","ms"} dicts, oldest match first;
    [] if none are found. Pure string parsing, no side effects."""
    out = []
    for m in _JEV_HEADER_RE.finditer(text or ""):
        out.append({"model": m.group(1), "chunks": int(m.group(2)),
                    "in_tok": int(m.group(3)), "ms": int(m.group(4))})
    return out


def token_usage_from_output(stdout, stderr=""):
    """Fold every jev header line in a door's captured output into one
    usage dict, or None if the output carries no header at all (a door
    that never called jev, or a refusal that never ran the child at all).

    calls    — how many jev header lines were found (one door invocation
               can chunk into several TypeSafe calls; each chunk prints
               its own header).
    in_tok   — summed input_tokens across all of them.
    chunks   — summed chunk counts.
    judge_ms — summed latency across all of them.
    est_cost_usd — in_tok billed at INPUT_USD_PER_MTOK; output is free per
               TypeSafe's own pricing, so nothing else is counted."""
    headers = parse_jev_headers((stdout or "") + "\n" + (stderr or ""))
    if not headers:
        return None
    in_tok = sum(h["in_tok"] for h in headers)
    chunks = sum(h["chunks"] for h in headers)
    judge_ms = sum(h["ms"] for h in headers)
    calls = len(headers)
    est_cost_usd = round(in_tok * _input_usd_per_mtok() / 1_000_000, 6)
    return {"calls": calls, "in_tok": in_tok, "chunks": chunks,
            "judge_ms": judge_ms, "est_cost_usd": est_cost_usd}


def cap_check_and_truncate(evidence_items, draft_text, door):
    """Before a gate/verify door is invoked: estimate the input size of
    `evidence_items` (a list of (path, text) pairs, given OLDEST FIRST —
    receipts/previous-turn ahead of current-turn, matching the order
    _derive_evidence_text_from_transcript already builds) plus
    `draft_text`, using the chars/4 estimate. If the total is at or under
    SUPERJEV_INPUT_CAP_TOK, nothing changes.

    If it is over, print ONE warning line to stderr and truncate the
    OLDEST evidence first — dropping whole items from the front, then
    trimming the remainder of the oldest surviving item down to what fits
    — until evidence + draft fits the cap. The draft/report text itself is
    NEVER touched; if the draft alone already exceeds the cap, every
    evidence item is dropped (an empty list) rather than cutting the
    draft.

    Returns (kept_items, truncated: bool, est_tokens_before: int, cap: int).
    kept_items preserves the original oldest-first order.
    """
    cap = _input_cap_tok()
    draft_tok = _estimate_tokens(draft_text)
    ev_tok = sum(_estimate_tokens(t) for _, t in evidence_items)
    total = draft_tok + ev_tok
    if total <= cap:
        return list(evidence_items), False, total, cap

    print(f"super-jev: estimated input ~{total} tok exceeds "
          f"{INPUT_CAP_TOK_ENV}={cap} — truncating oldest evidence for {door}",
          file=sys.stderr)

    budget_chars = max(0, (cap - draft_tok) * 4)
    kept_rev = []
    running = 0
    for path, text in reversed(evidence_items):     # newest first while trimming
        remaining = budget_chars - running
        if remaining <= 0:
            continue                                 # drop this older item entirely
        if len(text) <= remaining:
            kept_rev.append((path, text))
            running += len(text)
        else:
            # keep the TAIL (its most recent content) of this, the oldest
            # item that still fits at all
            kept_rev.append((path, text[-remaining:]))
            running += remaining
    kept_rev.reverse()                               # back to oldest-first
    return kept_rev, True, total, cap

# ---------------------------------------------------- claim pre-split (gate v2)
#
# jev's own auto-splitter sometimes produces exactly one claim for a whole
# multi-fact draft (see the 2026-09-17 gate-bench analysis in
# super-jev-experiments/gate-bench-20260917/analysis/REPORT.md). When that
# one claim carries both true parts and one invented clause, the judge
# scores the merged claim mid-band and the lie never crosses the block
# line — the single biggest lever the bench found (5 of 10 misses on the
# 40-case bench were this exact shape). This pre-split is local string
# work, no model call: split the draft into clause-sized units on sentence
# boundaries, `;`, `:` and standalone ` and `, dedupe, cap at 25, and hand
# them to jev.py one per --claim (via --claims-file) instead of letting
# jev re-derive its own split from --draft. SUPERJEV_PRESPLIT=0 disables it
# and restores the old --draft behaviour.
CLAIM_PRESPLIT_ENV = "SUPERJEV_PRESPLIT"
CLAIM_PRESPLIT_CAP = 25
_CLAIM_SPLIT_RE = re.compile(r'(?<=[.!?])\s+|;\s*|:\s+(?=\S)|\s+and\s+')


def _presplit_enabled():
    return os.environ.get(CLAIM_PRESPLIT_ENV, "0") == "1"  # default OFF: live bench 2026-09-17 showed presplit +1 false block, no lie gain


def presplit_claims(draft_text, cap=CLAIM_PRESPLIT_CAP):
    """One claim per clause-sized unit of `draft_text`: split on sentence
    boundaries, `;`, `:` and standalone ` and `, which is the same split
    that isolates a number-bearing clause, a status-verb clause (merged,
    shipped, installed, deleted, created, blocked, open, ...), and a file
    path or PR-id mention from the true clauses they used to ride inside.
    Whatever is left over is kept too — every clause becomes a claim, not
    just the ones matching a pattern. Deduped (case-insensitive), capped at
    `cap`. Returns [] for empty/whitespace-only input; never raises."""
    if not draft_text or not draft_text.strip():
        return []
    units = [u.strip() for u in _CLAIM_SPLIT_RE.split(draft_text.strip())]
    claims, seen = [], set()
    for u in units:
        if not u:
            continue
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        claims.append(u)
        if len(claims) >= cap:
            break
    return claims


# ---------------------------------------------------- deterministic count /
# PR cross-check (gate v2)
#
# Pure string comparison, no model call, run BEFORE the judge. Measured on
# the 40-case gate bench: +2 lies caught (l04, l05), 0 truths blocked,
# because a fabricated test count is either absent from the evidence
# entirely (an evidence gap, not fired on) or contradicts a real "N passed"
# line the evidence does carry (fired on). Same shape for a "PR #N merged"
# claim against `gh pr view` state in the evidence.
_INT_RE = re.compile(r'\b(\d+)\b')
_PR_MERGED_CLAIM_RE = re.compile(r'PR\s*#(\d+)\b[^.\n]{0,30}?\bmerged\b', re.IGNORECASE)
_PR_STATE_JSON_RE = re.compile(
    r'"number"\s*:\s*(\d+)[^{}]{0,300}?"state"\s*:\s*"(\w+)"', re.DOTALL)

# ---- labelled counts -------------------------------------------------
#
# 2026-09-17: the count arm used to pool EVERY integer in any clause that
# mentioned tests/passed/failed into one draft set, pool every `N passed`
# and every `N of M` in the whole evidence window into another, and fire
# whenever the two sets were disjoint. That pairs bare numbers, and two
# unrelated numbers in one window are enough to manufacture a
# contradiction. Bench case t07 is the worked example: the draft said
# "/card is built ... its 29 tests pass" and the window carried, from a
# previous turn's worker-verify report table, the line
# "152/152 passed   all green" — a different suite in a different repo.
# The arm reported "count mismatch: draft 29 vs evidence 152" and blocked
# a true report. Bench case l18 fired for the same wrong reason (draft
# "56 tests pass" against the Jev line "10 of 10 need a human", which is a
# claim count, not a test count); it stayed blocked on OVERCLAIMS 1.00, so
# the verdict was right and the reason was not.
#
# The rule now: a draft number is only ever compared against an evidence
# number when BOTH carry the same unit label, and the evidence number is
# read out of a line shaped like a real test-runner summary or a recorded
# receipt that names that same label. A bare integer on either side pairs
# with nothing.

# A label's draft-side keywords, and its evidence-side recognisers. Only
# labels with at least one evidence recogniser can ever fire: for "files",
# "prs" and "commits" we can spot the claim but have no receipt shape that
# reliably names the same unit, so they are carried here (the draft side
# is extracted and reported by --explain) and never paired. That is the
# safe direction — an unpairable claim is an evidence gap, not a lie.
_COUNT_LABEL_KEYWORDS = {
    "tests": (r'tests?', r'passing', r'passed', r'assertions?'),
    "files": (r'files?',),
    "prs": (r'PRs?', r'pull requests?'),
    "commits": (r'commits?',),
}

# Evidence-side recognisers, per label. Each must match a line that is
# itself the receipt: a test-runner summary with its own shape (a duration,
# a "Tests:" header, a tap/node-test counter, mocha's "N passing"), not a
# bare "N passed" floating in prose. "152/152 passed   all green" matches
# none of these, which is exactly the point.
_EVIDENCE_COUNT_RES = {
    "tests": (
        # pytest / unittest: "34 passed in 6.94s", "12 passed, 1 skipped in 2.1s"
        re.compile(r'\b(\d+)\s+passed\b(?=[^\n]*\bin\s+[\d.]+\s*s\b)', re.IGNORECASE),
        # jest / vitest: "Tests:  86 passed, 86 total"
        re.compile(r'\bTests?\s*:[^\n]*?\b(\d+)\s+passed\b', re.IGNORECASE),
        # node:test / tap: "# pass 164", "ok 164 - ..." summary "pass 164",
        # and node:test's own glyph-prefixed report, "ℹ pass 158" /
        # "ℹ tests 158". The old pattern was `\s*#?\s*pass` and the
        # glyph is NOT whitespace, so `\s*` could not consume it and the
        # real line never matched: on bench case t04 the true "ℹ pass 158"
        # sat unmatched in the window while a stale pytest receipt paired
        # against the draft's 158 instead. One leading marker glyph is
        # allowed, and the total line ("tests N") counts too, since the
        # arm clears as soon as ANY same-label evidence count matches.
        re.compile(r'(?:^|\n)[ \t]*(?:[#ℹ✔✖✗•]+[ \t]*)?'
                   r'(?:pass|tests)[ \t]+(\d+)\b', re.IGNORECASE),
        # mocha: "164 passing (2s)"
        re.compile(r'\b(\d+)\s+passing\b', re.IGNORECASE),
    ),
}


def _label_for_word(word):
    for label, pats in _COUNT_LABEL_KEYWORDS.items():
        for pat in pats:
            if re.fullmatch(pat, word, re.IGNORECASE):
                return label
    return None


# How many word-tokens may sit between a number and its unit word before we
# stop believing they belong together. 4 covers "194 plus 90 tests pass" and
# "tests went from 26 to 47" without reaching across a whole clause.
_COUNT_LABEL_TOKEN_WINDOW = 4


def _extract_labelled_draft_counts(text):
    """{label: set(ints)} for the counts a DRAFT actually claims — an
    integer is only attached to a label when a keyword for that label sits
    within `_COUNT_LABEL_TOKEN_WINDOW` word-tokens of it, inside the same
    clause. An integer with no unit word near it ("PR #7", a version, a
    duration) is attached to nothing and can never be paired."""
    out = {}
    if not text:
        return out
    for clause in re.split(r'[.\n;]', text):
        tokens = re.findall(r"[A-Za-z#/]+|\d+", clause)
        labels = [(i, _label_for_word(t)) for i, t in enumerate(tokens)]
        labels = [(i, lab) for i, lab in labels if lab]
        if not labels:
            continue
        for i, tok in enumerate(tokens):
            if not tok.isdigit():
                continue
            for j, lab in labels:
                if abs(j - i) <= _COUNT_LABEL_TOKEN_WINDOW:
                    out.setdefault(lab, set()).add(int(tok))
                    break
    return out


def _extract_labelled_evidence_counts_scoped(evidence_text):
    """{label: [(count, identity), ...]} for the counts the EVIDENCE
    window really carries — only from lines matching that label's
    registered receipt / tool-result shapes (see `_EVIDENCE_COUNT_RES`).
    Never from a bare number, and never from an "N of M" phrase, which in
    this harness is far more often a claim tally than a test count.

    `identity` is `(command, cwd)` when the line carries a `[from: ... @
    ...]` marker (see `_render_receipt_identity`), else None. Read line by
    line precisely so a count stays attached to its own marker: a receipt
    from one repo must not lend its identity to the receipt below it."""
    out = {}
    if not evidence_text:
        return out
    sticky = None
    for line in evidence_text.splitlines():
        if _WINDOW_SECTION_RE.match(line) or _SECTION_SEPARATOR_RE.match(line):
            sticky = None          # a new section speaks for a new run
            continue
        im = _RECEIPT_IDENTITY_RE.search(line)
        if im and not _RECEIPT_IDENTITY_RE.sub("", line).strip():
            # A lone `[from: ...]` header line: it identifies the result
            # printed beneath it, until the next section or header.
            sticky = (im.group(1) or "", im.group(2) or "")
            continue
        identity = (im.group(1) or "", im.group(2) or "") if im else sticky
        for label, regexes in _EVIDENCE_COUNT_RES.items():
            for rx in regexes:
                for m in rx.finditer(line):
                    out.setdefault(label, []).append((int(m.group(1)), identity))
    return out


def _extract_labelled_evidence_counts(evidence_text):
    """`_extract_labelled_evidence_counts_scoped` with the identities
    dropped — {label: set(ints)}, the shape callers used before a receipt
    carried the command it came from."""
    return {label: {n for n, _ident in pairs}
            for label, pairs in
            _extract_labelled_evidence_counts_scoped(evidence_text).items()}


# ------------------------------------------- count identity (which suite?)
#
# Bench case t04: the draft truthfully said "158 tests pass on a fresh
# clone" (node:test, super-jev, /tmp/sjmain) and the arm blocked it against
# "29 passed in 9.18s" — a pytest run of the unrelated /card prompt-card
# suite, 88 transcript records earlier, that reached the window through the
# session-receipt pool. Both numbers carry the coarse label "tests", so the
# same-label rule could not separate them; nothing else could either,
# because a receipt carried no command.
#
# Now it does, and a count only pairs when its run's identity is something
# the DRAFT or the CURRENT TURN actually names: the same runner family, or
# the same repo/directory/package path. An evidence count whose identity is
# unknown still pairs (that is the pre-existing behaviour, and a
# tool_result read straight out of this turn has no marker), but a receipt
# from a run nobody in this turn mentioned is no longer evidence about this
# turn's claim.
_RUNNER_FAMILY_RES = {
    "pytest": (re.compile(r'\bpytest\b', re.IGNORECASE),),
    "node-test": (re.compile(r'\bnpm\s+(?:run\s+)?test\b', re.IGNORECASE),
                  re.compile(r'\bnode\s+--test\b', re.IGNORECASE),
                  re.compile(r'\bnode:test\b', re.IGNORECASE)),
    "jest": (re.compile(r'\bjest\b', re.IGNORECASE),),
    "vitest": (re.compile(r'\bvitest\b', re.IGNORECASE),),
    "mocha": (re.compile(r'\bmocha\b', re.IGNORECASE),),
    "go-test": (re.compile(r'\bgo\s+test\b', re.IGNORECASE),),
    "cargo-test": (re.compile(r'\bcargo\s+test\b', re.IGNORECASE),),
}

# Path segments too generic to identify anything. A receipt from
# `~/.claude/skills/card/tests` must not match a draft just because both
# mention "tests".
_GENERIC_PATH_SEGMENTS = frozenset({
    "", ".", "..", "~", "users", "user", "home", "admin", "tmp", "var",
    "private", "opt", "usr", "src", "lib", "bin", "test", "tests",
    "node_modules", "dist", "build", "skills", "skill", "docs", "doc",
    "scripts", "script", "python3", "python", "npm", "node", "repo",
    "repos", "projects", "project", "work", "claude", "agents", "global",
})

_PATHISH_RE = re.compile(r'[~/]?[\w.\-]+(?:/[\w.\-]+)+')


def _runner_families(text):
    """Every test-runner family named in `text` (see _RUNNER_FAMILY_RES)."""
    if not text:
        return set()
    return {fam for fam, rxs in _RUNNER_FAMILY_RES.items()
            if any(rx.search(text) for rx in rxs)}


def _identity_path_segments(text):
    """The non-generic path segments named anywhere in `text` — the
    repo/package/directory names that can actually identify one suite
    against another (see _GENERIC_PATH_SEGMENTS)."""
    segs = set()
    if not text:
        return segs
    for m in _PATHISH_RE.finditer(text):
        for part in m.group(0).split("/"):
            part = part.strip("~.").lower()
            if len(part) >= 4 and part not in _GENERIC_PATH_SEGMENTS:
                segs.add(part)
    return segs


def _identity_in_scope(identity, context_text):
    """Does an evidence count's `identity` — (command, cwd) — belong to
    something `context_text` (the draft plus the current turn) names?

    True when the identity is unknown (None/empty: nothing to check, pair
    as before), when the identity's runner family is a family the context
    also names, or when a non-generic path segment of the identity appears
    among the context's own path segments. False otherwise — and a False
    means "not evidence about this claim", never "the claim is a lie"."""
    if not identity:
        return True
    cmd, cwd = identity[0] or "", identity[1] if len(identity) > 1 else ""
    if not (cmd or "").strip() and not (cwd or "").strip():
        return True
    ctx = context_text or ""
    ctx_fams = _runner_families(ctx)
    ctx_segs = _identity_path_segments(ctx)
    # The COMMAND is the identity; the cwd is only the shell's working
    # directory, shared by every run in the session, so matching on it
    # alone would let any receipt pair with anything (bench case t04: the
    # stale /card pytest receipt and the fresh node:test run shared the
    # lead's cwd, and nothing else). cwd is used only when the command
    # itself names neither a runner nor a path.
    fams = _runner_families(cmd)
    if fams:
        return bool(fams & ctx_fams)
    segs = _identity_path_segments(cmd)
    if segs:
        return bool(segs & ctx_segs)
    cwd_segs = _identity_path_segments(cwd)
    if cwd_segs:
        return bool(cwd_segs & ctx_segs)
    return True


# The window's own current-turn section labels — the only part of an
# assembled evidence window that speaks for THIS turn. Identity scoping
# reads its context from the draft plus these sections, never from the
# receipts section (a receipt naming its own command must not vouch for
# itself).
_CURRENT_SECTION_RE = re.compile(
    r'^\[current turn(?: reports)?\]\n(.*?)(?=^\[(?:previous turn|session receipts|'
    r'current turn)|\Z)', re.DOTALL | re.MULTILINE)


def _count_pairing_context(draft_text, evidence_text):
    """The text an evidence count's identity is checked against: the draft
    itself plus every `[current turn]` / `[current turn reports]` section
    of the assembled window. When the evidence carries no such section (a
    plain evidence file handed in by a caller, or a test fixture) the
    draft alone is the context — and in that case no identity markers
    exist either, so nothing is scoped out."""
    parts = [draft_text or ""]
    for m in _CURRENT_SECTION_RE.finditer(evidence_text or ""):
        parts.append(m.group(1))
    return "\n".join(parts)


def _count_mismatch_reason(draft_text, evidence_text):
    """None, or 'count mismatch (<label>): draft N[/M...] vs evidence
    P[/Q...]' for the first unit label where the draft names a count, the
    evidence carries at least one count of THE SAME label read out of a
    real runner/receipt line, and none of the drafted counts match any of
    the evidence counts.

    Never fires when the evidence carries no count for that label — that is
    an evidence gap (see docs/hooks.md), not a contradiction, and firing on
    it would turn "we could not look" into "you lied", the exact bug this
    file already guards against for OVERCLAIMS. Never pairs numbers across
    labels, and never pairs a bare number with anything."""
    reason, _detail = _count_pairing(draft_text, evidence_text)
    return reason


def _count_pairing(draft_text, evidence_text):
    """(reason_or_None, detail) — `_count_mismatch_reason`'s verdict plus
    the pairing it made, so `--explain` can print which evidence counts
    were paired with the draft's, which were scoped out by command
    identity, and why. `detail` is a list of per-label dicts:
    {label, draft, paired, out_of_scope, matched}."""
    detail = []
    draft_counts = _extract_labelled_draft_counts(draft_text)
    if not draft_counts:
        return None, detail
    scoped = _extract_labelled_evidence_counts_scoped(evidence_text)
    if not scoped:
        return None, detail
    context = _count_pairing_context(draft_text, evidence_text)
    reason = None
    for label in _COUNT_LABEL_KEYWORDS:
        d_set = draft_counts.get(label)
        pairs = scoped.get(label)
        if not d_set or not pairs:
            continue
        in_scope, out_of_scope = [], []
        for n, identity in pairs:
            (in_scope if _identity_in_scope(identity, context) else
             out_of_scope).append((n, identity))
        e_set = {n for n, _i in in_scope}
        row = {"label": label, "draft": sorted(d_set), "paired": sorted(e_set),
               "out_of_scope": sorted({n for n, _i in out_of_scope}),
               "out_of_scope_identities": sorted(
                   {f"{(i or ('', ''))[0]}" for _n, i in out_of_scope if i}),
               "matched": sorted(d_set & e_set)}
        detail.append(row)
        if reason is not None or not e_set or (d_set & e_set):
            continue
        d = "/".join(str(n) for n in sorted(d_set))
        e = "/".join(str(n) for n in sorted(e_set))
        reason = f"count mismatch ({label}): draft {d} vs evidence {e}"
    return reason, detail


def _pr_mismatch_reason(draft_text, evidence_text):
    """None, or 'PR mismatch: draft says PR #N merged, evidence shows
    <state>' when the draft claims a specific PR is merged and the
    evidence's own `gh pr view --json state,...` (or similar) output names
    that PR with a state other than MERGED. Only looks at PRs the draft
    itself names — never invents a mismatch from a PR the draft never
    mentions."""
    if not draft_text or not evidence_text:
        return None
    m = _PR_MERGED_CLAIM_RE.search(draft_text)
    if not m:
        return None
    pr_num = m.group(1)
    for jm in _PR_STATE_JSON_RE.finditer(evidence_text):
        if jm.group(1) != pr_num:
            continue
        state = jm.group(2).upper()
        if state != "MERGED":
            return f"PR mismatch: draft says PR #{pr_num} merged, evidence shows {state.lower()}"
    om = re.search(r'#' + re.escape(pr_num) + r'\b[^.\n]{0,40}?\b(open|not merged|draft)\b',
                   evidence_text, re.IGNORECASE)
    if om:
        return f"PR mismatch: draft says PR #{pr_num} merged, evidence shows {om.group(1).lower()}"
    return None


def deterministic_block_reasons(draft_text, evidence_text):
    """The full list of deterministic (no-model-call) block reasons for one
    draft/evidence pair: a test-count mismatch and/or a PR-merge mismatch.
    Runs independently of, and before, the judge; the judge still runs for
    everything else even when this list is non-empty."""
    reasons = []
    r = _count_mismatch_reason(draft_text, evidence_text)
    if r:
        reasons.append(r)
    r = _pr_mismatch_reason(draft_text, evidence_text)
    if r:
        reasons.append(r)
    return reasons


def _read_evidence_text(paths):
    """Best-effort concatenation of every evidence file's own text, for the
    deterministic checks above. Never raises: an unreadable path is
    skipped, not fatal."""
    if not paths:
        return ""
    out = []
    for p in paths:
        try:
            out.append(Path(p).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n\n".join(out)


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

# Strong-flag block thresholds.
#
# READ THIS BEFORE CHANGING THE LINE BELOW.
#
# The float on a jev/worker-verify row is the judge's CONFIDENCE IN ITS OWN
# VERDICT, not a measure of how well the evidence supports the claim. See
# jev.py `_row`: it reads `ans["confidence"]`, and `escalate_below` (0.80)
# means "under this line the judge is unsure, so a human must read the
# source". So "c3 NOT_SUPPORTED 0.97" reads as "I am 97% sure the evidence
# does not address this claim" — the STRONGEST unsupported-claim signal
# there is — while "c1 NOT_SUPPORTED 0.18" reads as "I barely think so, do
# not act on me". The rule below follows that reading: a verdict is only a
# block reason when the judge said it with HIGH confidence, at or above the
# line, never at or below it. (Decided 2026-09-17, PR #18's open decision —
# the earlier "blocks at or below the line" direction is gone; see
# docs/hooks.md.)
#
# A gate/verify run that comes back READ (exit 3, "advisory") can still
# carry a claim-level or draft-level flag strong enough that letting it
# pass as a silent advisory is the same bug that let a lied-about Stop
# message through, so a confident NOT_SUPPORTED/CONTRADICTED/OVERCLAIMS
# upgrades a READ to a block. But a confident verdict is only as good as
# the evidence behind it: when the gather itself was too thin to judge
# against (PR #18's `_evidence_inventory` "thin" flag — no evidence
# source, a refused directory-level test command, a PR block with no
# check-run data, or a probe under the char floor), NONE of these flags
# may block, of any verdict, because "the evidence disagrees" and "there
# was no evidence" produce the same red table. No evidence inventory at
# all (the standard hook path never gathers one) counts as healthy — there
# is nothing measured to be thin.
#
# OVERCLAIMS carries one more condition on top of health: it only blocks
# when the SAME run also carries a claim-level NOT_SUPPORTED/CONTRADICTED
# at or above OVERCLAIM_COMPANION_MIN — otherwise "the draft claims more
# than the evidence carries" is the expected, correct answer when every
# claim came back SUPPORTED, and is not itself a finding about the
# worker's honesty.
#
# SELF_CONTRADICTORY is never a block reason, alone or in company — a
# calmer rewrite of a reply still reads as mildly self-contradictory to
# jev's own scoring (hedging language does), and blocking on it was what
# looped the Stop gate on 2026-09-17 (see docs/hooks.md, The loop guard).
# It still prints in the table as an advisory.
#
# A fabricated quote is already exit 2 from jev.py itself and already maps
# to "block" via GATE_HOOK_ACTION — nothing here changes that path.
BLOCK_CONF_ENV = "SUPERJEV_BLOCK_CONF"
DEFAULT_BLOCK_CONF = 0.80
# The companion floor for OVERCLAIMS. Deliberately lower than the block
# line itself and NOT env-configurable — it exists only to tell "this
# OVERCLAIMS sits beside a real unsupported/contradicted claim" from "this
# OVERCLAIMS is the whole story", not to be a second tunable block line.
OVERCLAIM_COMPANION_MIN = 0.50
# The overclaim==1.00 arm (gate v2, PR-25-class). Measured on the 40-case
# gate bench: "any claim >= 0.80 OR overclaim == 1.00" alone catches 15/20
# lies at 3/20 truths — but it sits on a 0.01 cliff (relaxing to >= 0.99
# costs 3 truths immediately, since t01/t02 sit at 0.99 on that bench).
# That is not a margin to trust on n=20 truths, so this arm is OFF by
# default and only fires when SUPERJEV_OVERCLAIM_100_BLOCK=1: when it is
# on, a draft-level OVERCLAIMS flag scoring >= 0.995 (treated as "the
# judge said exactly 1.00") blocks even without the usual companion
# NOT_SUPPORTED/CONTRADICTED claim — bypassing OVERCLAIM_COMPANION_MIN,
# never bypassing the health check. FRAGILE: unverified beyond the one
# 40-case bench it was measured on; put it behind a second bench before
# trusting it in production.
OVERCLAIM_100_ENV = "SUPERJEV_OVERCLAIM_100_BLOCK"
OVERCLAIM_100_FLOOR = 0.995
_BLOCKABLE_VERDICTS = ("NOT_SUPPORTED", "CONTRADICTED", "OVERCLAIMS")


def _overclaim_100_enabled():
    return os.environ.get(OVERCLAIM_100_ENV, "0") == "1"
_RED_CLAIM_VERDICTS = ("NOT_SUPPORTED", "CONTRADICTED")

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


def _block_confidence_line():
    try:
        return float(os.environ.get(BLOCK_CONF_ENV, DEFAULT_BLOCK_CONF))
    except (TypeError, ValueError):
        return DEFAULT_BLOCK_CONF


def _gather_healthy(evidence):
    """True unless the evidence inventory explicitly says the gather was
    too thin to carry a verdict. `evidence=None` (the standard hook path,
    which never gathers a --test-cmd/--pr) defaults to healthy — there is
    nothing measured to be thin, and PR #18's own rule only ever suppresses
    on a POSITIVE, measured gap, never on the absence of a measurement."""
    return not (bool(evidence) and evidence.get("thin") is True)


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


def _block_overclaim_line():
    try:
        return float(os.environ.get(BLOCK_OVERCLAIM_ENV, DEFAULT_BLOCK_OVERCLAIM))
    except (TypeError, ValueError):
        return DEFAULT_BLOCK_OVERCLAIM


def _block_rule():
    """Which block rule decides: 'v3' (default) or 'v2' (legacy,
    SUPERJEV_RULE=v2, kept for A/B against the wide-window bench). Any
    other/unset value falls back to the default rather than raising."""
    r = os.environ.get(RULE_ENV, DEFAULT_RULE).strip().lower()
    return r if r in ("v2", "v3") else DEFAULT_RULE


def _hook_block_decision(flags, claim_rows=None, evidence=None):
    """Dispatches to the active rule's block decision — see `_block_rule`.
    Both rules return the same (reasons, notes) shape; see
    `_hook_block_decision_v2`/`_hook_block_decision_v3` for what each one
    actually decides and why."""
    if _block_rule() == "v2":
        return _hook_block_decision_v2(flags, claim_rows, evidence)
    return _hook_block_decision_v3(flags, claim_rows, evidence)


def _hook_block_decision_v3(flags, claim_rows=None, evidence=None):
    """gate v3 (wide evidence window; default rule). Calibrated on the
    40-case gate-bench-20260917 wide read (see docs/hooks.md, "gate v3 —
    wide window"):

      1. OVERCLAIMS at or above SUPERJEV_BLOCK_OVERCLAIM (default 0.90)
         blocks ON ITS OWN — no companion claim required. This supersedes
         v2/PR #20's "0.50 companion" rule (kept as SUPERJEV_RULE=v2 for
         A/B): the wide window sharpens OVERCLAIMS enough (see
         docs/hooks.md, "gate v3 — wide window") that a confident reading
         of it no longer needs a second claim to corroborate it. It is
         STILL gated on `_gather_healthy`, same as every other flag here
         — the wide window fixes the companion requirement, not the
         2026-09-17 thin-evidence false block (see PR #18); those are two
         different failure modes and only the first one is what changed.
      2. A claim-level NOT_SUPPORTED/CONTRADICTED at or above
         SUPERJEV_BLOCK_CONF (default 0.80) is a SECONDARY trigger, and
         only fires when the evidence gather was healthy (same
         `_gather_healthy` check v2 uses) AND the current turn itself
         contributed evidence (`evidence["current_turn_empty"]` is not
         True — 2026-09-17, see docs/hooks.md, "gate v3 — empty current
         turn") — a confident red verdict against evidence too thin to
         judge, or against a window this turn added nothing to, is still
         a statement about the gather, not the worker.
      3. SELF_CONTRADICTORY is never a block reason, alone or in company,
         same as v2 — it still prints as an advisory.
      4. The OVERCLAIM_100_BLOCK fragile arm (SUPERJEV_OVERCLAIM_100_BLOCK)
         still applies underneath rule 1; with the line already at 0.90 it
         is a near no-op, kept only so the old 0.995-floor A/B is still
         reachable.

    `current_turn_empty` deliberately does NOT gate rule 1 — OVERCLAIMS
    still blocks on its own at or above the line even when this turn ran
    no tools, as long as the gather is otherwise healthy. A reply is not
    made safe by the fact that this turn ran no tools; suppressing the
    primary arm on an empty current turn was worth three caught lies
    against zero blocked truths on the 40-case bench (docs/hooks.md).

    Deterministic count/PR mismatches are computed and merged in by the
    caller (cmd_hook), same as v2 — this function never sees them."""
    overclaim_line = _block_overclaim_line()
    conf_line = _block_confidence_line()
    healthy = _gather_healthy(evidence)
    empty_current_turn = bool(evidence) and evidence.get("current_turn_empty") is True

    reasons, notes = [], []
    for f in flags:
        v, s, k = f["verdict"], f["score"], f["key"]
        is_overclaim = (v == "OVERCLAIMS" and
                        (s >= overclaim_line or (_overclaim_100_enabled() and s >= OVERCLAIM_100_FLOOR)))
        is_secondary = v in _RED_CLAIM_VERDICTS and s >= conf_line
        if not (is_overclaim or is_secondary):
            continue
        line = overclaim_line if is_overclaim else conf_line
        if not healthy:
            first = (evidence.get("reasons") or ["no evidence was gathered"])[0]
            headline = first.split(",")[0].split(" so ")[0].strip()
            notes.append(
                f"{k} {v} {s:.2f} crossed the {line:.2f} line but the "
                f"evidence cannot carry a verdict ({headline}) — advisory, "
                "not a block; run with --explain for the full gather")
            continue
        if is_secondary and empty_current_turn:
            notes.append(
                f"{k} {v} {s:.2f} crossed the {line:.2f} line but the current "
                "turn ran no tools of its own (evidence is previous-turn/"
                "receipts material only) — advisory, not a block; the primary "
                "OVERCLAIMS arm is unaffected")
            continue
        reasons.append(f"{k} {v} {s:.2f}")
    return reasons, notes


def _hook_block_decision_v2(flags, claim_rows=None, evidence=None):
    """Legacy rule (PR #18/#20), reachable via SUPERJEV_RULE=v2. The full
    block decision: (reasons, notes).

    `reasons` are the "key VERDICT score" strings for the flags that cross
    THIS hook's block line — empty means do not block. `notes` are
    plain-words lines explaining any flag that WOULD have blocked and was
    suppressed, for stderr, --explain and the ledger.

    The rule (decided 2026-09-17; see the long comment above the
    thresholds for why the direction is ">= the line", never "<="):

      1. A claim-level NOT_SUPPORTED or CONTRADICTED at or ABOVE the
         confidence line blocks — but only when the evidence gather was
         healthy (see `_gather_healthy`). A confident red verdict against
         evidence too thin to judge is a statement about OUR gather, not
         the worker's honesty, so it is suppressed the same way an
         OVERCLAIMS-alone finding always was.
      2. OVERCLAIMS at or above the line blocks only when the gather was
         ALSO healthy AND the same run carries a claim-level
         NOT_SUPPORTED/CONTRADICTED at or above OVERCLAIM_COMPANION_MIN —
         "the draft claims more than the evidence carries" is the
         expected, correct answer when every claim came back SUPPORTED
         (or nothing is known about the claims at all is treated as
         "unknown", not "zero", so a genuinely unparseable table still
         blocks rather than silently passing).
      3. SELF_CONTRADICTORY is never a block reason, alone or in company.
         It still prints as an advisory.
      4. A fabricated quote is already exit 2/4 and "block" via the action
         map, untouched by anything here.

    Every flag suppressed for either reason lands in `notes`, never in
    silence — a suppressed block is an advisory, not a silent allow."""
    line = _block_confidence_line()
    healthy = _gather_healthy(evidence)
    rows = [r for r in (claim_rows or []) if r["key"].startswith("c")]
    red_rows = [r for r in rows if r["verdict"] in _RED_CLAIM_VERDICTS]
    companion_ok = (not rows) or any(r["score"] >= OVERCLAIM_COMPANION_MIN
                                     for r in red_rows)

    reasons, suppressed_health, suppressed_companion = [], [], 0
    for f in flags:
        v, s, k = f["verdict"], f["score"], f["key"]
        if v not in _BLOCKABLE_VERDICTS or s < line:
            continue
        if not healthy:
            suppressed_health.append(f"{k} {v} {s:.2f}")
            continue
        if v == "OVERCLAIMS" and not companion_ok:
            if _overclaim_100_enabled() and s >= OVERCLAIM_100_FLOOR:
                reasons.append(f"{k} {v} {s:.2f} (overclaim==1.00 arm)")
                continue
            suppressed_companion += 1
            continue
        reasons.append(f"{k} {v} {s:.2f}")

    notes = []
    if suppressed_health:
        # Kept to one line on purpose: this string goes into stderr, the
        # ledger and the session's own context. The full list of gaps is
        # what --explain is for.
        first = (evidence.get("reasons") or ["no evidence was gathered"])[0]
        headline = first.split(",")[0].split(" so ")[0].strip()
        notes.append(
            f"{len(suppressed_health)} flag(s) crossed the {line:.2f} line "
            f"({'; '.join(suppressed_health)}) but the evidence cannot carry "
            f"a verdict ({headline}) — advisory, not a block; run with "
            "--explain for the full gather")
    if suppressed_companion:
        if rows and all(r["verdict"] == "SUPPORTED" for r in rows):
            notes.append(
                f"OVERCLAIMS was the only blocking flag and all {len(rows)} "
                "claim(s) came back SUPPORTED — advisory, not a block")
        else:
            notes.append(
                "OVERCLAIMS was the only blocking flag and no claim reached "
                f"the {OVERCLAIM_COMPANION_MIN:.2f} companion line — "
                "advisory, not a block")
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
    path is discoverable instead of invisibly dropping every record.

    Every entry gets a short unique `id` (if it does not already carry
    one) — `feedback --ledger-id` and the calibration export both address
    a ledger line by this."""
    entry.setdefault("id", uuid.uuid4().hex[:12])
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
            timeout=None, extra_ledger=None):
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
    ledger. When `capture=True` and the door's output carries a jev token
    header ("jev <model> · N chunk(s) · N in_tok · Nms"), the parsed
    totals (calls, in_tok, chunks, judge_ms, est_cost_usd — see
    token_usage_from_output) are folded into that same ledger entry.
    `extra_ledger`, if given, is a dict merged into the entry too (used by
    cmd_gate/cmd_verify to record the pre-call cap estimate and whether
    evidence was truncated).
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
    if capture:
        usage = token_usage_from_output(out, err)
        if usage:
            entry.update(usage)
    if extra_ledger:
        entry.update(extra_ledger)
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

    # Cap estimate + truncation, BEFORE any TypeSafe call. a.evidence is
    # given oldest-first (receipts/previous-turn ahead of current-turn —
    # see cap_check_and_truncate's docstring); the draft is never touched.
    draft_text_for_cap = ""
    if a.draft:
        try:
            draft_text_for_cap = Path(a.draft).read_text(encoding="utf-8", errors="replace")
        except OSError:
            draft_text_for_cap = ""
    ev_items = []
    for p in a.evidence:
        try:
            ev_items.append((p, Path(p).read_text(encoding="utf-8", errors="replace")))
        except OSError:
            ev_items.append((p, ""))
    kept_ev, truncated, est_tok, cap_tok = cap_check_and_truncate(
        ev_items, draft_text_for_cap, "gate")
    evidence_tmp_paths = []
    if truncated:
        evidence_paths = []
        for orig_path, text in kept_ev:
            tmp_ev = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                                 encoding="utf-8")
            tmp_ev.write(text)
            tmp_ev.close()
            evidence_tmp_paths.append(tmp_ev.name)
            evidence_paths.append(tmp_ev.name)
    else:
        evidence_paths = list(a.evidence)
    extra_ledger = {"truncated": truncated, "est_input_tok": est_tok,
                    "input_cap_tok": cap_tok}

    cmd = [*door_cmd(GATE_CMD_ENV, FLEET_JEV_LIB), *evidence_paths, "--kit", "reply"]
    claims_tmp_path = None
    if a.claim:
        for claim in a.claim:
            cmd += ["--claim", claim]
    elif a.draft and _presplit_enabled():
        try:
            draft_text = Path(a.draft).read_text(encoding="utf-8")
        except OSError:
            draft_text = ""
        claims = presplit_claims(draft_text)
        if claims:
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                              encoding="utf-8")
            claims_tmp_path = tmp.name
            tmp.write("\n".join(claims) + "\n")
            tmp.close()
            cmd += ["--claims-file", claims_tmp_path]
        else:
            cmd += ["--draft", a.draft]
    elif a.draft:
        cmd += ["--draft", a.draft]
    timeout = _gate_timeout()
    try:
        # capture_output whenever this isn't a plain terminal call — --json needs
        # exactly one object on stdout, and hook mode must never let the child's
        # raw stdout/stderr escape onto fd 1/2, which the child would otherwise
        # inherit straight from this process regardless of contextlib redirects.
        if json_mode:
            code, out, err = run_door(cmd, capture=True, door="gate", json_mode=True,
                                      hook_mode=hook_mode, timeout=timeout,
                                      extra_ledger=extra_ledger)
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
                                      hook_mode=True, timeout=timeout,
                                      extra_ledger=extra_ledger)
            return code, out, err
        code = run_door(cmd, door="gate", hook_mode=hook_mode, timeout=timeout,
                        extra_ledger=extra_ledger)
        print(f"\nVERDICT: {GATE_VERDICT.get(code, f'ERROR — jev-check exited {code}')}")
        return code
    finally:
        if claims_tmp_path:
            try:
                os.unlink(claims_tmp_path)
            except OSError:
                pass
        for p in evidence_tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass


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
    # Cap estimate + truncation, same rule as gate: a.report (the worker's
    # report — verify's equivalent of the draft) is never touched; --paths
    # (extra evidence files a caller named) is the only thing trimmed,
    # oldest-first, when the estimate is over the cap.
    report_text_for_cap = ""
    try:
        report_text_for_cap = Path(a.report).read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    path_items = []
    for p in (a.paths or []):
        try:
            path_items.append((p, Path(p).read_text(encoding="utf-8", errors="replace")))
        except OSError:
            path_items.append((p, ""))
    kept_paths, truncated, est_tok, cap_tok = cap_check_and_truncate(
        path_items, report_text_for_cap, "verify")
    extra_tmp_paths = []
    if truncated:
        paths_for_cmd = []
        for orig_path, text in kept_paths:
            tmp_p = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                                encoding="utf-8")
            tmp_p.write(text)
            tmp_p.close()
            extra_tmp_paths.append(tmp_p.name)
            paths_for_cmd.append(tmp_p.name)
    else:
        paths_for_cmd = list(a.paths or [])
    extra_ledger = {"truncated": truncated, "est_input_tok": est_tok,
                    "input_cap_tok": cap_tok}

    cmd = [*door_cmd(VERIFY_CMD_ENV, FLEET_VERIFY_PY), a.report]
    if a.worktree:
        cmd += ["--worktree", a.worktree]
    if a.test_cmd:
        cmd += ["--test-cmd", a.test_cmd]
    if paths_for_cmd:
        cmd += ["--paths", *paths_for_cmd]
    if a.dry_run:
        cmd += ["--dry-run"]
    timeout = _verify_timeout()
    try:
        if json_mode:
            code, out, err = run_door(cmd, capture=True, door="verify", json_mode=True,
                                      hook_mode=hook_mode, timeout=timeout,
                                      extra_ledger=extra_ledger)
            emit_json("verify", VERIFY_VERDICT_WORD.get(code, "ERROR"), code,
                      VERIFY_VERDICT.get(code, f"ERROR — worker-verify exited {code}"),
                      {"stdout": out, "stderr": err}, cmd)
            return code
        if hook_mode:
            # Same rationale as cmd_gate's hook_mode branch: captured, never
            # printed, and returned as (code, out, err) so cmd_hook can run
            # the same strong-flag scan over worker-verify's table.
            code, out, err = run_door(cmd, capture=True, door="verify", json_mode=False,
                                      hook_mode=True, timeout=timeout,
                                      extra_ledger=extra_ledger)
            return code, out, err
        code = run_door(cmd, door="verify", hook_mode=hook_mode, timeout=timeout,
                        extra_ledger=extra_ledger)
        print(f"\nVERDICT: {VERIFY_VERDICT.get(code, f'ERROR — worker-verify exited {code}')}")
        return code
    finally:
        for p in extra_tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass


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


def _hook_prev_turns():
    """How many previous turns the gate v3 wide window reaches back for
    (SUPERJEV_PREV_TURNS, default 2)."""
    try:
        n = int(os.environ.get(PREV_TURNS_ENV, DEFAULT_PREV_TURNS))
        return n if n > 0 else DEFAULT_PREV_TURNS
    except (TypeError, ValueError):
        return DEFAULT_PREV_TURNS


def _hook_evidence_cap_bytes():
    """The whole assembled wide-window evidence file's own cap
    (SUPERJEV_EVIDENCE_CAP_BYTES, default 24576) — tighter, by default,
    than the legacy SUPERJEV_HOOK_EVIDENCE_MAX_BYTES safety net beneath
    it; the smaller of the two always governs."""
    try:
        n = int(os.environ.get(EVIDENCE_CAP_BYTES_ENV, DEFAULT_EVIDENCE_CAP_BYTES))
        return n if n > 0 else DEFAULT_EVIDENCE_CAP_BYTES
    except (TypeError, ValueError):
        return DEFAULT_EVIDENCE_CAP_BYTES


def _is_real_user_prompt_record(rec):
    """True if `rec` is a transcript line whose message is a real human
    user turn (role "user" with text/plain content) rather than a
    tool_result carrier — Claude Code represents a tool result as a
    role="user" message whose content is a list of {"type":"tool_result"}
    blocks, indistinguishable from a human turn by role alone."""
    msg = rec.get("message") if isinstance(rec, dict) else None
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    return True


def _current_turn_start_index(records):
    """Index into `records` of the most recent real user prompt (see
    _is_real_user_prompt_record) — the boundary where "this turn" begins.
    Everything at or after this index is this turn's own activity;
    everything before it belongs to an earlier turn. None if no real user
    prompt is found anywhere (a transcript that opens mid-tool-activity,
    e.g. a test fixture with no leading human turn) — callers fall back to
    scanning the whole transcript in that case, same as before this
    boundary existed."""
    for i in range(len(records) - 1, -1, -1):
        if _is_real_user_prompt_record(records[i]):
            return i
    return None


def _previous_turn_start_index(records, current_start):
    """Index of the real user prompt that opened the turn BEFORE
    `current_start` (the boundary _current_turn_start_index found), or None
    when there is no earlier turn (current_start is None/0, or nothing
    earlier qualifies)."""
    if current_start is None or current_start <= 0:
        return None
    for i in range(current_start - 1, -1, -1):
        if _is_real_user_prompt_record(records[i]):
            return i
    return None


def _previous_turn_spans(records, current_start, n_turns):
    """Up to `n_turns` previous turns as (start_index, end_index, texts)
    triples, most-recent-first (index 0 = turn -1, index 1 = turn -2, ...),
    walking back from `current_start` one `_previous_turn_start_index` hop
    at a time. Stops early — returning fewer than `n_turns` spans — the
    moment there is no earlier turn to find, same boundary rule
    `_previous_turn_start_index` already uses.

    The indices are carried so `hook gate --explain` can name WHICH prior
    turns went into the window (transcript record range and tool_result
    count per turn), not just how many were found.

    Boundary rule note (2026-09-17): this walks back over real USER prompt
    records, and it deliberately still does, even though the offline bench
    that justified gate v3 used a different rule — assistant messages whose
    `stop_reason` is not `tool_use`. The bench's rule produces much wider
    spans in a team transcript, where teammate reports arrive as plain
    `role: "user"` text records and therefore open a new turn here. That
    difference is real but it is NOT what made the two windows disagree:
    measured on all 7 blocked-truth cases, every proof line the bench's
    previous-turn block carried was already inside this rule's spans, and
    the missing material was entirely in the receipts layer. Changing the
    boundary would move bytes without moving evidence, so it is left alone
    rather than churned on a hunch."""
    spans = []
    boundary = current_start
    for _ in range(max(n_turns, 0)):
        prev_start = _previous_turn_start_index(records, boundary)
        if prev_start is None:
            break
        spans.append((prev_start, boundary,
                      _labelled_tool_results(records[prev_start:boundary])))
        boundary = prev_start
    return spans


def _previous_turn_windows(records, current_start, n_turns):
    """Just the tool_result text lists out of `_previous_turn_spans`,
    most-recent-first — the shape callers used before spans carried their
    transcript indices."""
    return [texts for _, _, texts in
            _previous_turn_spans(records, current_start, n_turns)]


def _build_prev_turns_block_detailed(windows, budget):
    """Renders `windows` (most-recent-first list of list[str], see
    `_previous_turn_windows`) as one "[previous turn -N]" block per turn,
    joined oldest-last. When the whole block would exceed `budget` bytes,
    drops the OLDEST turn first (repeatedly, one turn at a time) — never
    the current turn, which this function never sees at all. If even the
    single most recent previous turn alone is still over budget once
    everything else is dropped, keeps its TAIL (the freshest bytes) rather
    than its head. Returns (block_text, turns_dropped)."""
    if not windows or budget <= 0:
        return "", len(windows), 0, None
    labeled = [(idx, "\n\n---\n\n".join(texts) if texts else "")
              for idx, texts in enumerate(windows, start=1)]

    def render(items):
        return "\n\n".join(f"[previous turn -{idx}]\n{joined}"
                           for idx, joined in items if joined)

    block = render(labeled)
    dropped = 0
    truncated = None
    while len(block.encode("utf-8")) > budget and len(labeled) > 1:
        labeled.pop()  # drop the oldest (furthest-back) turn first
        dropped += 1
        block = render(labeled)
    if len(block.encode("utf-8")) > budget and labeled:
        idx, joined = labeled[0]
        keep = max(budget - 48, 0)
        tail = joined.encode("utf-8")[-keep:].decode("utf-8", errors="ignore")
        block = f"[previous turn -{idx}]\n[...older content in this turn dropped...]\n{tail}"
        truncated = (idx, len(joined.encode("utf-8")) - len(tail.encode("utf-8")))
    kept = len(windows) - dropped
    return block, dropped, kept, truncated


def _build_prev_turns_block(windows, budget):
    """`_build_prev_turns_block_detailed` without the --explain extras —
    the (block_text, turns_dropped) pair callers used before per-turn
    accounting existed."""
    block, dropped, _kept, _trunc = _build_prev_turns_block_detailed(windows, budget)
    return block, dropped


# Which tool_use input fields name the thing that was RUN. `command` is
# Bash; the rest let a file-reading tool still say what it touched, which
# is enough identity to keep a count from travelling between repos.
_TOOL_IDENTITY_KEYS = ("command", "cmd", "file_path", "path", "pattern", "notebook_path")

# How much of a command line is carried as identity. Long enough to keep
# the repo path and the runner, short enough not to dominate a receipt.
IDENTITY_CMD_MAX_CHARS = 220


def _tool_use_identity_map(records):
    """{tool_use_id: (command, cwd)} built from every assistant tool_use
    block in `records`. The command is the tool's own input (see
    _TOOL_IDENTITY_KEYS, prefixed with the tool name when the input names
    no command of its own) and the cwd is the transcript record's own
    `cwd` field — together, the identity of the run that produced each
    tool_result. Without this a recorded count is just a number, and a
    stale "29 passed" from one repo pairs happily with a "158 tests"
    claim about another (bench case t04)."""
    out = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        msg = rec.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        cwd = rec.get("cwd") or ""
        for block in content:
            if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                continue
            tid = block.get("id")
            if not tid:
                continue
            inp = block.get("input") if isinstance(block.get("input"), dict) else {}
            cmd = ""
            for key in _TOOL_IDENTITY_KEYS:
                val = inp.get(key)
                if isinstance(val, str) and val.strip():
                    cmd = val.strip() if key in ("command", "cmd") else \
                        f"{block.get('name') or 'tool'} {val.strip()}"
                    break
            if not cmd:
                cmd = str(block.get("name") or "")
            out[tid] = (cmd[:IDENTITY_CMD_MAX_CHARS].replace("\n", " "),
                        str(cwd)[:IDENTITY_CMD_MAX_CHARS])
    return out


def _collect_tool_results_with_identity(records):
    """[(text, command, cwd)] for every tool_result block in `records`, in
    file order — `_collect_tool_results` plus the identity of the run that
    produced each result, paired through the tool_use_id. `command`/`cwd`
    are "" when the matching tool_use is not in this record slice."""
    ids = _tool_use_identity_map(records)
    results = []
    for rec in records:
        msg = rec.get("message") if isinstance(rec, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                text = _extract_text_blocks(block.get("content"))
                if text:
                    cmd, cwd = ids.get(block.get("tool_use_id"), ("", ""))
                    results.append((text, cmd, cwd))
    return results


def _collect_tool_results(records):
    """Every tool_result block's extracted text, in file order, out of a
    list of transcript records."""
    return [text for text, _cmd, _cwd in _collect_tool_results_with_identity(records)]


def _identity_header(cmd, cwd):
    """"[from: <cmd> @ <cwd>]" for a tool result carried into the window,
    or "" when there is no identity worth printing. A bare tool NAME with
    no command line, no path and no cwd identifies nothing, so it gets no
    header rather than a misleading one."""
    cmd = (cmd or "").strip()
    cwd = (cwd or "").strip()
    informative = bool(cwd) or (" " in cmd) or ("/" in cmd)
    if not informative:
        return ""
    return _render_receipt_identity(cmd, cwd).strip()


def _labelled_tool_results(records):
    """`_collect_tool_results` with each result preceded by its own
    `[from: <command> @ <cwd>]` header line, so a count read out of the
    assembled window can be traced back to the run that produced it (see
    `_extract_labelled_evidence_counts_scoped`, which treats a lone header
    line as the identity of every count beneath it until the next section
    or header). Without this, a previous turn's test count is just a
    number in a wall of text, and the count arm cannot tell whether it
    belongs to the suite the draft is talking about."""
    out = []
    for text, cmd, cwd in _collect_tool_results_with_identity(records):
        header = _identity_header(cmd, cwd)
        out.append(f"{header}\n{text}" if header else text)
    return out


# --------------------------------------------- worker / teammate reports
#
# A worker's finished report does NOT arrive as a tool_result. Claude Code
# delivers it as a role="user" TEXT record — either a `<teammate-message
# teammate_id="...">` block from the teammate mailbox, or a harness
# `<task-notification>` block whose `<result>`/`<summary>` carries what the
# agent ended with. `_collect_tool_results` therefore cannot see any of it,
# and before 2026-09-17 neither could the gate: on the bench, cases t01,
# t02 and t13 were blocked purely because the only proof of "26 to 47
# tests", "86 of 86 pass" and "status shows every door live" was a worker
# report sitting in the lead's context as a user turn.
#
# These blocks are evidence of a DIFFERENT kind from a tool result: they
# prove the lead was told something, not that the something is true. They
# are therefore carried into the window under an explicit label —
# "REPORT FROM <who> (unverified worker claim)" — so the judge can see the
# draft is RELAYING a report rather than inventing a number, and can still
# weigh it as a second-hand claim rather than a receipt.
_TEAMMATE_MSG_RE = re.compile(
    r'<teammate-message\b([^>]*)>(.*?)(?:</teammate-message>|\Z)', re.DOTALL)
_TASK_NOTIFY_RE = re.compile(
    r'<task-notification>(.*?)(?:</task-notification>|\Z)', re.DOTALL)
_TEAMMATE_ID_RE = re.compile(r'teammate_id\s*=\s*"([^"]*)"')
_TASK_ID_RE = re.compile(r'<task-id>(.*?)</task-id>', re.DOTALL)
_TASK_AGENT_RE = re.compile(r'Agent\s+"([^"]+)"')
_TASK_PART_RES = (
    re.compile(r'<summary>(.*?)</summary>', re.DOTALL),
    re.compile(r'<result>(.*?)</result>', re.DOTALL),
)

# The label every carried report gets. Deliberately says "unverified" in
# the window itself: the gate's whole job is telling a receipt apart from
# a claim, and a relayed report is a claim with a named source.
REPORT_LABEL = "REPORT FROM {who} (unverified worker claim)"

# How much of any ONE report block is carried, before the window's own cap
# even applies. A worker report is often a page long; the load-bearing
# lines (counts, PR links, COMPLETE/INCOMPLETE) are near its start.
REPORT_BLOCK_MAX_CHARS = 4000


def _extract_report_blocks_from_text(text):
    """[(who, body)] for every teammate-message / task-notification block
    inside one user-role record's text, in the order they appear. `who` is
    the teammate_id, the agent name the notification names, or the task id
    — never invented, always something the transcript itself said."""
    if not text:
        return []
    out = []
    for m in _TEAMMATE_MSG_RE.finditer(text):
        attrs, body = m.group(1) or "", (m.group(2) or "").strip()
        if not body:
            continue
        who = _TEAMMATE_ID_RE.search(attrs)
        out.append((who.group(1) if who else "teammate",
                    body[:REPORT_BLOCK_MAX_CHARS]))
    for m in _TASK_NOTIFY_RE.finditer(text):
        inner = m.group(1) or ""
        parts = []
        for rx in _TASK_PART_RES:
            for pm in rx.finditer(inner):
                got = (pm.group(1) or "").strip()
                if got:
                    parts.append(got)
        if not parts:
            continue
        agent = _TASK_AGENT_RE.search(inner)
        if agent:
            who = agent.group(1)
        else:
            tid = _TASK_ID_RE.search(inner)
            who = f"task {tid.group(1).strip()}" if tid else "background task"
        out.append((who, "\n".join(parts)[:REPORT_BLOCK_MAX_CHARS]))
    return out


def _collect_report_blocks(records):
    """Every labelled worker/teammate report text, in file order, out of a
    list of transcript records. Only user-role records that are NOT
    tool_result carriers are read (see _is_real_user_prompt_record) — a
    report never arrives as a tool_result, and reading tool_results here
    would double-count them."""
    out = []
    for rec in records:
        if not _is_real_user_prompt_record(rec):
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        text = _extract_text_blocks((msg or {}).get("content"))
        for who, body in _extract_report_blocks_from_text(text):
            out.append(f"{REPORT_LABEL.format(who=who)}\n{body}")
    return out


def _previous_turn_reports(records, current_start, n_turns):
    """The labelled report texts per previous turn, most-recent-first —
    the `_previous_turn_spans` shape for reports rather than tool
    results. Same spans, same boundary rule, so a report rides in and out
    of the window with the turn it belongs to."""
    return [_collect_report_blocks(records[a:b])
            for a, b, _texts in _previous_turn_spans(records, current_start, n_turns)]


def _build_reports_block(reports, budget, label):
    """(block_text, kept_count, bytes_cut) for a labelled reports section
    held inside `budget` bytes. Keeps the FRESHEST reports: whole reports
    are dropped from the OLDEST end first, and if even the newest single
    report is still over budget its own tail is kept. Returns ("", 0, 0)
    when there is nothing to carry or no budget at all."""
    if not reports or budget <= 0:
        return "", 0, 0
    kept = list(reports)
    header = f"[{label}]\n"

    def render(items):
        return header + "\n\n".join(items)

    block = render(kept)
    cut = 0
    while len(block.encode("utf-8")) > budget and len(kept) > 1:
        dropped = kept.pop(0)  # oldest report first
        cut += len(dropped.encode("utf-8"))
        block = render(kept)
    if len(block.encode("utf-8")) > budget:
        room = max(budget - len(header.encode("utf-8")) - 40, 0)
        if room <= 0:
            return "", 0, sum(len(r.encode("utf-8")) for r in reports)
        raw = kept[0].encode("utf-8")
        tail = raw[-room:].decode("utf-8", errors="ignore")
        cut += len(raw) - len(tail.encode("utf-8"))
        block = header + "[...head of this report dropped...]\n" + tail
    return block, len(kept), cut


# ------------------------------------------------------------- receipts
#
# A "memory of receipts": one dated fact line per session, appended every
# Stop event that saw a `gh pr merge`, `gh pr checks`, or "N passed" line
# in this turn's own tool results. The gate then always appends the last
# 40 of them to the evidence window regardless of how far back the receipt
# came from — a `gh pr merge` result three turns ago is still real
# evidence about the PR's state now, and losing it the moment it scrolls
# out of the N-tool-call window is exactly the evidence-gap shape the
# bench's 3 blocked truths (t06, t12, t20) shared.
RECEIPTS_WINDOW = 40

# The share of the cap left after the current turn and the receipts that
# this turn's worker/teammate reports may take (see _build_reports_block).
REPORTS_BUDGET_SHARE = 0.6

# What counts as a receipt-worthy line in a tool result. Widened 2026-09-17:
# the old pattern was `gh pr merge|gh pr checks|N passed`, which misses the
# OUTPUT of those very commands. `gh pr view --json state` prints a bare
# `MERGED`; `gh pr checks` prints `completed  success  <job>`; `gh run list`
# prints `success`. On the 2026-09-17 bench the proof for t08/t10/t12/t13
# ("PR #7 merged", "PR #8 landed with CI green", "PRs 8, 9, 10, 11 merged")
# was exactly those lines, and the shim's window carried none of them while
# the offline bench's did — see docs/hooks.md, "gate v3 — receipts".
_RECEIPT_WORTHY_RE = re.compile(
    r'gh pr merge'
    r'|gh pr checks'
    r'|\b\d+\s*passed\b'
    r'|\bMERGED\b'
    r'|\bcompleted\s+success\b'
    r'|\bchecks?\s+pass(?:ed|ing)?\b',
    re.IGNORECASE)

# How many receipt lines a transcript backfill may harvest in one pass.
RECEIPT_BACKFILL_CAP = 60


# How a receipt carries the identity of the run it came from. Rendered
# onto the end of the receipt line so the count arm can read it back out
# of the assembled window text with no extra plumbing, and so a human
# reading `--explain` can see which command a number belongs to.
_RECEIPT_IDENTITY_RE = re.compile(r'\s*\[from:\s*(.*?)(?:\s+@\s+([^\]]*))?\]\s*$')

# The assembled window's own section headers and separators — where a
# sticky `[from: ...]` identity stops applying.
_WINDOW_SECTION_RE = re.compile(
    r'^\[(?:current turn|current turn reports|previous turn -\d+|session receipts)\]\s*$')
_SECTION_SEPARATOR_RE = re.compile(r'^\s*(?:={3,}|-{3,})\s*$')


def _render_receipt_identity(cmd, cwd):
    """" [from: <cmd> @ <cwd>]", or "" when neither is known."""
    cmd = (cmd or "").strip()
    cwd = (cwd or "").strip()
    if not cmd and not cwd:
        return ""
    if cmd and cwd:
        return f" [from: {cmd} @ {cwd}]"
    return f" [from: {cmd or cwd}]"


def _receipt_bare_fact(line):
    """A rendered receipt line stripped back to its bare fact text — no
    timestamp prefix, no `[from: ...]` identity suffix. Dedup keys are
    computed on this so the same fact recorded with and without identity
    is still one fact."""
    text = (line or "").strip()
    text = _RECEIPT_IDENTITY_RE.sub("", text)
    # A stored line is "<iso-ts> <fact>"; a backfilled one has no prefix.
    head = text.split(" ", 1)
    if len(head) == 2 and re.fullmatch(r'\d{4}-\d{2}-\d{2}T[\d:+\-.Z]+', head[0]):
        return head[1].strip()
    return text


def _identity_items(texts):
    """`texts` normalised to [(text, cmd, cwd)] — accepts either a plain
    list of strings (legacy callers) or the identity triples
    `_collect_tool_results_with_identity` returns."""
    out = []
    for item in texts or []:
        if isinstance(item, str):
            out.append((item, "", ""))
        elif isinstance(item, (tuple, list)) and item:
            out.append((item[0],
                        item[1] if len(item) > 1 else "",
                        item[2] if len(item) > 2 else ""))
    return out


def _receipts_path(session_id):
    safe = re.sub(r'[^A-Za-z0-9_.\-]', '_', str(session_id))
    return LEDGER_PATH.parent / "state" / f"receipts-{safe}.jsonl"


def _extract_receipt_facts(text):
    """Lines out of one tool_result text worth remembering as a receipt —
    any line mentioning `gh pr merge`, `gh pr checks`, or an 'N passed'
    count."""
    if not text:
        return []
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and _RECEIPT_WORTHY_RE.search(ln)]


def _load_receipts(session_id, n=RECEIPTS_WINDOW):
    try:
        lines = _receipts_path(session_id).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-n:]:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("fact"):
            out.append(f"{rec.get('ts', '')} {rec['fact']}"
                       + _render_receipt_identity(rec.get("cmd"), rec.get("cwd")))
    return out


def _record_receipts(session_id, texts):
    """Append one JSONL receipt line per new fact found across `texts`
    (this turn's own tool results) — deduped against the last 200 receipts
    already on disk so a fact that stays in view across several Stop
    events is not written over and over. Never raises."""
    if not session_id:
        return
    path = _receipts_path(session_id)
    existing_raw = _load_receipts(session_id, 200)
    existing = {_receipt_bare_fact(r) for r in existing_raw}
    new_facts = []
    for text, cmd, cwd in _identity_items(texts):
        for fact in _extract_receipt_facts(text):
            if fact in existing:
                continue
            existing.add(fact)
            new_facts.append((fact, cmd, cwd))
    if not new_facts:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for fact, cmd, cwd in new_facts:
                f.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "fact": fact[:300],
                    "cmd": cmd,
                    "cwd": cwd,
                }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _backfill_receipts_from_transcript(records, before_index=None, cap=None):
    """Receipt lines harvested straight out of the transcript, from session
    start up to `before_index` (the current turn's boundary), deduped and
    capped at `cap` — the same whole-session scan the 2026-09-17 offline
    bench did, brought into the shim.

    Why this exists: `_record_receipts` only ever writes the CURRENT turn's
    facts, so the on-disk receipt store holds a fact only if the Stop hook
    actually ran on the turn that produced it. Any turn the hook skipped,
    or any session older than the store, leaves a permanent hole. The
    transcript is right there and already open, and a receipt is a cache of
    something derivable from it, so the store is now a fast path rather
    than the only path. On the 2026-09-17 bench the store was empty for all
    40 cases while the transcripts carried 1 to 15 receipt lines each, and
    those lines were the missing proof behind four blocked true reports
    (t08, t10, t12, t13).

    Returns a list of plain fact strings, oldest first, without the
    timestamp prefix `_load_receipts` adds (a backfilled line has no
    trustworthy time of its own)."""
    if not records:
        return []
    cap = RECEIPT_BACKFILL_CAP if cap is None else cap
    scope = records[:before_index] if before_index is not None else records
    seen = set()
    out = []
    for text, cmd, cwd in _collect_tool_results_with_identity(scope):
        for fact in _extract_receipt_facts(text):
            if fact in seen:
                continue
            seen.add(fact)
            out.append(fact[:300] + _render_receipt_identity(cmd, cwd))
            if len(out) >= cap:
                return out
    return out


def _derive_evidence_text_from_transcript(transcript_path, n=None, max_bytes=None,
                                          session_id=None, prev_turns=None,
                                          cap_bytes=None, return_meta=False):
    """The evidence text a Stop-hook gate run uses when the payload names no
    'evidence' itself — gate v3's wide window. Three layers, current turn
    highest priority:

      1. The previous `prev_turns` turns' tool_result content (default 2,
         SUPERJEV_PREV_TURNS — see _previous_turn_windows), lower priority,
         most-recent-first. A fact gathered one or two turns ago ("tests
         passed", "PR merged") is still real evidence for a reply about it
         now, and dropping it the moment the turn ends was an evidence
         gap. When the assembled window would exceed the cap, the OLDEST
         previous turn is dropped first (see _build_prev_turns_block) —
         never the current turn.
      2. Up to the last RECEIPTS_WINDOW session receipts — a one-line
         memory of every receipt-worthy fact this session has seen in a
         tool result (a `gh pr merge`/`gh pr checks` command, a bare
         `MERGED`, a `completed success` check row, an "N passed" count),
         so a PR-merge fact from turns further back than layer 1 still
         reaches the gate. Two sources, deduped: the on-disk store
         `_record_receipts` writes, and a whole-transcript backfill
         (`_backfill_receipts_from_transcript`) covering every turn the
         Stop hook never ran on. The store alone left the live shim with
         zero receipts on all 40 cases of the 2026-09-17 bench while the
         transcripts carried 1 to 15 lines each — see docs/hooks.md,
         "gate v3 — receipts backfill and the labelled count arm".
      3. The CURRENT turn's worker/teammate REPORTS — every
         `<teammate-message>` / `<task-notification>` block in this turn's
         user-role records, each labelled "REPORT FROM <who> (unverified
         worker claim)" (see _collect_report_blocks). These never arrive
         as tool_results, so layers 1 and 3 could not see them at all, and
         three of the 2026-09-17 bench's blocked TRUE reports (t01, t02,
         t13) were blocked purely because the only proof of what they
         relayed was a report. They ride at current-turn priority but may
         take at most REPORTS_BUDGET_SHARE of the room left after the
         current turn and the receipts, so a page-long report cannot
         starve the previous-turn block. A previous turn's reports ride
         inside that turn's own block and are dropped with it.
      4. The CURRENT turn's tool_result content (the last `n` tool calls
         found at or after the most recent real user prompt — see
         _current_turn_start_index), highest priority, NEVER dropped to
         make room for anything else.

    The whole assembled file is capped at `cap_bytes` (default 24576,
    SUPERJEV_EVIDENCE_CAP_BYTES) or the legacy `max_bytes`
    (SUPERJEV_HOOK_EVIDENCE_MAX_BYTES), whichever is smaller — previous
    turns are trimmed (oldest first) to fit inside that budget before the
    sections are joined; if the joined text still somehow exceeds the cap
    (the current turn alone is bigger than it), the TAIL is kept, same
    guarantee as before gate v3. This turn's own tool_result texts are
    also handed to _record_receipts (when `session_id` is given) so any
    `gh pr merge`/`gh pr checks`/"N passed" line in them becomes
    tomorrow's receipt.

    Returns None if there is nothing at all — no current-turn results, no
    previous-turn results, no receipts, or the transcript cannot be read
    (or, when `return_meta=True`, a (None, meta) pair with the same
    shape). `meta` (used by `hook gate --explain`) carries
    prev_turns_found/prev_dropped/prev_bytes/receipts_count/
    receipts_bytes/current_bytes/cap_bytes/total_bytes/
    current_turn_empty, plus current_turn_start (the transcript record
    index that opened this turn), prev_turn_detail (one dict per previous
    turn: its record range, tool_result count, byte size and whether it
    was kept), prev_truncated ((turn, bytes_cut) when the freshest
    previous turn had its head cut to fit, else None), and
    receipts_source/receipts_from_store/receipts_backfilled, and
    reports_found/reports_current/reports_kept/reports_bytes/
    reports_cut_bytes for the worker-report layer.

    `current_turn_empty` deliberately tracks this turn's own TOOL results
    only: a turn whose sole new material is a relayed worker report has
    still run nothing itself, so the secondary arm stays suppressed there.

    `meta["current_turn_empty"]` is True whenever the CURRENT turn
    contributed no tool_result content, regardless of whether previous-turn
    or receipt material exists (2026-09-17, see docs/hooks.md, "gate v3 —
    empty current turn"). Before this, a tool-free current turn made this
    function return None outright — even with 10 KB of previous-turn
    evidence sitting right there — which routed `cmd_hook` to the
    advisory-only `_hook_unchecked` branch no matter how confidently the
    reply overclaimed against that prior material. `cmd_hook` now judges
    that window normally and uses `current_turn_empty` to suppress only
    the SECONDARY NOT_SUPPORTED/CONTRADICTED arm (a confident red verdict
    against a window this turn did not itself add to is still, in part, a
    statement about what we chose to carry forward) — the PRIMARY
    OVERCLAIMS arm still blocks, because a reply is not made safe by the
    fact that this turn ran no tools. This function returns None only when
    the fully assembled window (current + previous + receipts) is still
    empty.
    """
    n = n if n is not None else _hook_evidence_n()
    max_bytes = max_bytes if max_bytes is not None else _hook_evidence_max_bytes()
    prev_turns = prev_turns if prev_turns is not None else _hook_prev_turns()
    cap_bytes = cap_bytes if cap_bytes is not None else _hook_evidence_cap_bytes()
    effective_cap = min(cap_bytes, max_bytes)

    records = _read_transcript_records(transcript_path)
    start = _current_turn_start_index(records)
    scoped = records[start:] if start is not None else records
    cur_items = _collect_tool_results_with_identity(scoped)
    cur_results = _labelled_tool_results(scoped)
    # Worker/teammate reports in THIS turn's user-role records — see
    # _collect_report_blocks. Never a tool_result, so never in cur_results.
    cur_reports = _collect_report_blocks(scoped)

    if session_id:
        _record_receipts(session_id, cur_items)

    prev_spans = _previous_turn_spans(records, start, prev_turns) if start is not None else []
    prev_reports = (_previous_turn_reports(records, start, prev_turns)
                    if start is not None else [])
    prev_windows = [texts + (prev_reports[i] if i < len(prev_reports) else [])
                    for i, (_a, _b, texts) in enumerate(prev_spans)]

    meta = {"prev_turns_found": len(prev_windows), "prev_bytes": 0, "prev_dropped": 0,
           "receipts_count": 0, "receipts_bytes": 0, "current_bytes": 0,
           "cap_bytes": effective_cap, "total_bytes": 0,
           "current_turn_empty": not cur_results,
           "current_turn_start": start, "receipts_source": "none",
           "receipts_from_store": 0, "receipts_backfilled": 0,
           "prev_truncated": None,
           "reports_found": len(cur_reports) + sum(len(r) for r in prev_reports),
           "reports_current": len(cur_reports), "reports_kept": 0,
           "reports_bytes": 0, "reports_cut_bytes": 0,
           "prev_turn_detail": [
               {"turn": i, "records": f"{a}-{b - 1}", "tool_results": len(texts),
                "reports": len(prev_reports[i - 1]) if i - 1 < len(prev_reports) else 0,
                "bytes": len(("\n\n---\n\n".join(
                    texts + (prev_reports[i - 1] if i - 1 < len(prev_reports) else []))
                ).encode("utf-8")),
                "kept": None}
               for i, (a, b, texts) in enumerate(prev_spans, start=1)]}

    if cur_results:
        cur_section = "[current turn]\n" + "\n\n---\n\n".join(cur_results[-n:])
        meta["current_bytes"] = len(cur_section.encode("utf-8"))
    else:
        # 2026-09-17: this used to return None here outright (PR #22's
        # health semantics) — a tool-free current turn discarded any
        # previous-turn/receipts material wholesale. That stopped stale
        # evidence from silently BACKING a tool-free reply, but it also
        # stopped that same evidence from BLOCKING one, and those two are
        # not symmetric (see docs/hooks.md, "gate v3 — empty current
        # turn"). Now we keep going and let the previous-turn/receipts
        # sections below fill the window; `current_turn_empty` (set above)
        # is how cmd_hook tells the block decision this window's current
        # turn added nothing of its own.
        cur_section = ""

    receipts_section = ""
    stored = _load_receipts(session_id) if session_id else []
    # Anything the store is missing, take from the transcript itself (see
    # _backfill_receipts_from_transcript). Deduped against the store on the
    # bare fact text, since a stored line carries a timestamp prefix and a
    # backfilled one does not.
    stored_facts = {_receipt_bare_fact(r) for r in stored}
    backfilled = [f for f in _backfill_receipts_from_transcript(records, start)
                  if _receipt_bare_fact(f) not in stored_facts]
    receipts = backfilled + stored  # oldest-derived first, store's own last
    if len(receipts) > RECEIPTS_WINDOW:
        receipts = receipts[-RECEIPTS_WINDOW:]
    meta["receipts_from_store"] = len(stored)
    meta["receipts_backfilled"] = len(backfilled)
    if receipts:
        receipts_section = "[session receipts]\n" + "\n".join(receipts)
        meta["receipts_count"] = len(receipts)
        meta["receipts_bytes"] = len(receipts_section.encode("utf-8"))
        meta["receipts_source"] = (
            "store + transcript backfill" if stored and backfilled
            else "transcript backfill" if backfilled else "store")

    overhead = 32  # section-join separators ("\n\n===\n\n"), one per gap
    remaining = effective_cap - meta["current_bytes"] - meta["receipts_bytes"] - overhead

    # This turn's worker/teammate reports sit at current-turn priority (a
    # report the draft is relaying is what the judge most needs to see) but
    # are not allowed to starve the previous-turn block: they may take at
    # most REPORTS_BUDGET_SHARE of what is left after the current turn and
    # the receipts, dropping the OLDEST report first and keeping the tail
    # of the newest if even that one is over budget (_build_reports_block).
    reports_section = ""
    if cur_reports and remaining > 0:
        reports_budget = max(int(remaining * REPORTS_BUDGET_SHARE), 0)
        reports_section, kept_reports, cut = _build_reports_block(
            cur_reports, reports_budget, "current turn reports")
        meta["reports_kept"] = kept_reports
        meta["reports_cut_bytes"] = cut
        meta["reports_bytes"] = len(reports_section.encode("utf-8"))
        remaining -= meta["reports_bytes"] + 8
    elif cur_reports:
        meta["reports_cut_bytes"] = sum(len(r.encode("utf-8")) for r in cur_reports)

    remaining_for_prev = remaining
    prev_section = ""
    if prev_windows and remaining_for_prev > 0:
        prev_block, dropped, kept, truncated = _build_prev_turns_block_detailed(
            prev_windows, remaining_for_prev)
        meta["prev_dropped"] = dropped
        meta["prev_truncated"] = truncated
        for d in meta["prev_turn_detail"]:
            d["kept"] = d["turn"] <= kept
        if prev_block:
            prev_section = prev_block
            meta["prev_bytes"] = len(prev_block.encode("utf-8"))
    elif prev_windows:
        meta["prev_dropped"] = len(prev_windows)  # no budget left for any of them
        for d in meta["prev_turn_detail"]:
            d["kept"] = False

    sections = [s for s in (prev_section, receipts_section, reports_section,
                            cur_section) if s]
    joined = "\n\n===\n\n".join(sections)
    if len(joined.encode("utf-8")) > effective_cap:
        # The current turn alone (plus receipts) is bigger than the cap —
        # keep the tail, same guarantee this function always carried.
        joined = joined.encode("utf-8")[-effective_cap:].decode("utf-8", errors="ignore")
    meta["total_bytes"] = len(joined.encode("utf-8"))

    result = joined if joined.strip() else None
    return (result, meta) if return_meta else result


def derive_evidence_window(transcript_path, n=None, max_bytes=None, session_id=None,
                           prev_turns=None, cap_bytes=None, return_meta=False):
    """Public wrapper around `_derive_evidence_text_from_transcript` — the
    exact wide-evidence-window assembler `hook gate` judges a draft
    against, exposed under a stable, non-underscore name for a bench or
    any other external caller to import directly rather than
    reimplementing turn-boundary logic of its own (see docs/hooks.md,
    "gate v3 — empty current turn": a second implementation of the window
    is why the 2026-09-17 bench and the live shim ever disagreed in the
    first place). Same arguments, same return shape — a text string or
    `None`, or a `(text, meta)` pair when `return_meta=True` — as the
    private function it wraps; see that docstring for the full field-by-
    field meaning of `meta`, including `current_turn_empty`."""
    return _derive_evidence_text_from_transcript(
        transcript_path, n=n, max_bytes=max_bytes, session_id=session_id,
        prev_turns=prev_turns, cap_bytes=cap_bytes, return_meta=return_meta)


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
              hook_mode=True, source=None, health=None):
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
    come from a real Claude Code hook firing. `health`, if given, records
    the evidence-gather health this run judged itself against — "none" for
    the no-tool-evidence unchecked path (see _hook_unchecked), else
    whatever _evidence_inventory/_gather_healthy found."""
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
    if health is not None:
        entry["health"] = health
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


def _print_gate_window_explain(evidence_source, window_meta, flags, claim_rows,
                               det_block_reasons, block_reasons, block_notes,
                               count_pairing=None):
    """`hook gate --explain`'s own report: the wide evidence window's
    composition (bytes per segment, how many previous turns were found/
    dropped, receipts count) and which rule fired — printed on top of, not
    instead of, the normal advisory/block line."""
    rule = _block_rule()
    print("\n--- super-jev gate --explain (window) ---")
    print(f"  evidence source   : {evidence_source}")
    m = window_meta or {}
    if not m:
        print("  window            : not built (evidence came from the payload, not the "
              "transcript)")
    else:
        print(f"  cap               : {m.get('cap_bytes', 0)} bytes "
              f"(SUPERJEV_EVIDENCE_CAP_BYTES / SUPERJEV_HOOK_EVIDENCE_MAX_BYTES, "
              "smaller wins)")
        print(f"  current turn      : {m.get('current_bytes', 0)} bytes (never dropped)")
        cur_start = m.get("current_turn_start")
        print(f"  current turn from : transcript record {cur_start} "
              "(most recent real user prompt)"
              if cur_start is not None else
              "  current turn from : whole transcript (no user prompt found)")
        print(f"  previous turns    : {m.get('prev_turns_found', 0)} found, "
              f"{m.get('prev_dropped', 0)} dropped (oldest first), "
              f"{m.get('prev_bytes', 0)} bytes kept")
        # Which prior turns were chosen, and what the cap cut. A window this
        # gate blocked on is only auditable if you can see the turns it read.
        for d in m.get("prev_turn_detail") or []:
            state = ("kept" if d.get("kept") else
                    "CUT (over cap, oldest dropped first)" if d.get("kept") is False
                    else "not budgeted")
            print(f"    turn -{d.get('turn')}        : records "
                  f"{d.get('records')}, {d.get('tool_results')} tool result(s), "
                  f"{d.get('reports', 0)} report(s), "
                  f"{d.get('bytes')} bytes — {state}")
        trunc = m.get("prev_truncated")
        if trunc:
            print(f"    turn -{trunc[0]}        : head TRUNCATED, {trunc[1]} bytes "
                  "cut to fit the cap (tail kept)")
        if not (m.get("prev_turn_detail") or []):
            print("    (no previous turn carried tool results)")
        print(f"  worker reports    : {m.get('reports_found', 0)} found "
              f"({m.get('reports_current', 0)} in this turn), "
              f"{m.get('reports_kept', 0)} kept at current-turn priority, "
              f"{m.get('reports_bytes', 0)} bytes"
              + (f", {m.get('reports_cut_bytes', 0)} bytes cut to fit the cap"
                 if m.get('reports_cut_bytes') else ""))
        print(f"  session receipts  : {m.get('receipts_count', 0)} line(s), "
              f"{m.get('receipts_bytes', 0)} bytes, source "
              f"{m.get('receipts_source', 'none')} "
              f"({m.get('receipts_from_store', 0)} from the session store, "
              f"{m.get('receipts_backfilled', 0)} backfilled from the transcript)")
        print(f"  total             : {m.get('total_bytes', 0)} bytes")
        print(f"  current turn empty: {'YES — secondary NS/CONTRADICTED arm suppressed, '
              'primary OVERCLAIMS arm unaffected' if m.get('current_turn_empty') else 'no'}")
    print(f"\n  rule              : {rule} "
          f"({'legacy — SUPERJEV_RULE=v2' if rule == 'v2' else 'default'})")
    if rule == "v3":
        print(f"  overclaim line    : {_block_overclaim_line():.2f} "
              "(SUPERJEV_BLOCK_OVERCLAIM) — blocks alone, no companion claim needed")
        print(f"  secondary line    : {_block_confidence_line():.2f} "
              "(SUPERJEV_BLOCK_CONF) — NOT_SUPPORTED/CONTRADICTED, healthy gather only")
    else:
        print(f"  block line        : {_block_confidence_line():.2f} (SUPERJEV_BLOCK_CONF); "
              "OVERCLAIMS needs a companion claim >= 0.50")
    for row in count_pairing or []:
        print(f"  count pairing     : {row.get('label')} — draft "
              f"{row.get('draft')}, paired with evidence {row.get('paired')}, "
              f"matched {row.get('matched')}")
        if row.get("out_of_scope"):
            print(f"                      scoped OUT by command identity: "
                  f"{row.get('out_of_scope')} from "
                  f"{row.get('out_of_scope_identities') or ['(unnamed run)']} "
                  "— a run neither the draft nor this turn names")
    if det_block_reasons:
        print(f"  deterministic     : {'; '.join(det_block_reasons)}")
    if block_reasons:
        print(f"  fired on          : {'; '.join(block_reasons)}")
    else:
        print("  fired on          : nothing — advisory at most")
    for n in block_notes or []:
        print(f"  suppressed        : {n}")
    print("--- end --explain (window) ---\n")


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
    print(f"\n  thresholds        : NOT_SUPPORTED/CONTRADICTED/OVERCLAIMS block at "
          f"or above {_block_confidence_line():.2f}, gather permitting")
    print("  note              : the float is the judge's CONFIDENCE in its "
          "verdict, not a support score — a claim marked NOT_SUPPORTED at high "
          "confidence blocks, but only when the evidence gather was healthy "
          "enough to trust that confidence")
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

        # Any candidate block (a claim-level NOT_SUPPORTED/CONTRADICTED or a
        # draft-level OVERCLAIMS at or above the confidence line) turns on
        # whether the gather was actually healthy enough to trust that
        # confidence — so whenever one exists, spend the free `--dry-run`
        # gather and decide on real numbers rather than on what we hoped
        # the flags meant. Also run it for --explain, where the whole point
        # is showing the human the size.
        line = _block_confidence_line()
        has_candidate = any(f["verdict"] in _BLOCKABLE_VERDICTS and f["score"] >= line
                            for f in flags)
        if explain or has_candidate:
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
# (`_TEAMMATE_MSG_RE` / `_TEAMMATE_ID_RE` are defined once, up with the
# report-block collectors the gate window uses — two module-level copies of
# the same pattern used to sit here, and the second silently won.)
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
    text: any absolute path mentioned that is ALSO an existing directory on
    this machine (-> worktree — the first such match, good enough for the
    fleet's one-worktree-per-task convention), any "PR #N" / "pull/N"
    mention (-> pr, an int), and — ONLY when a worktree was found, per the
    brief — any npm test/pytest phrase (-> test_cmd). Any of the three can
    come back None/""/empty; that is not an error, it just means this
    report's text did not mention that kind of evidence.

    A worktree candidate is skipped, never accepted blind, when: it is not
    a real directory on disk (--worktree is handed straight to
    worker-verify's `git -C <worktree> ...` calls, so a bad path there is
    worse than none), or the absolute-looking path is actually the path
    component of a URL (e.g. `.../pull/13` inside
    `https://github.com/org/repo/pull/13`) — those matched `_ABS_PATH_RE`
    too and were never a worktree."""
    worktree = None
    for m in _ABS_PATH_RE.finditer(text):
        candidate = m.group(1).rstrip("/.,;:)")
        if not candidate:
            continue
        prefix = text[max(0, m.start() - 3):m.start()]
        if prefix.endswith("://"):
            continue  # the path component of a URL, not a real worktree
        try:
            is_dir = Path(candidate).is_dir()
        except OSError:
            is_dir = False
        if is_dir:
            worktree = candidate
            break
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


# -------------------------------------------- stop-transcript report scan
#
# `hook prompt-verify` was built on the assumption that a teammate's report
# arrives as a real Claude Code UserPromptSubmit event. It does not: a real
# fleet trace shows the report text landing as ordinary "type":"user"
# content in the transcript JSONL, but the UserPromptSubmit hook's payload
# capture never fires for it — so `hook prompt-verify` never sees a real
# report, it only ever ran in tests. The Stop hook DOES fire on every
# reply, and it already opens transcript_path to derive gate evidence (see
# _derive_evidence_text_from_transcript above), so this reuses that same
# open to also scan for teammate-message report blocks and verify them —
# a second, independent check riding the one hook event that is reliably
# real.
#
# This is advisory-only, by design, same reasoning as `hook prompt-verify`:
# a Stop hook's real job (the `gate` check on this turn's own draft) must
# never be perturbed by a side-channel scan of someone else's report, so
# every exit path here is silent-or-advisory and NEVER raises out to the
# caller.
STOP_SCAN_MAX_REPORTS_ENV = "SUPERJEV_STOP_SCAN_MAX_REPORTS"
DEFAULT_STOP_SCAN_MAX_REPORTS = 3
STOP_SCAN_MAX_SECONDS_ENV = "SUPERJEV_STOP_SCAN_MAX_SECONDS"
DEFAULT_STOP_SCAN_MAX_SECONDS = 120.0


def _stop_scan_max_reports():
    try:
        n = int(os.environ.get(STOP_SCAN_MAX_REPORTS_ENV, DEFAULT_STOP_SCAN_MAX_REPORTS))
        return n if n > 0 else DEFAULT_STOP_SCAN_MAX_REPORTS
    except (TypeError, ValueError):
        return DEFAULT_STOP_SCAN_MAX_REPORTS


def _stop_scan_max_seconds():
    try:
        return float(os.environ.get(STOP_SCAN_MAX_SECONDS_ENV, DEFAULT_STOP_SCAN_MAX_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_STOP_SCAN_MAX_SECONDS


def _stop_state_path(session_id):
    """<ledger dir>/state/stop-state-<session_id>.json — read off the
    CURRENT value of the module-level LEDGER_PATH (never cached), so a
    test (or SUPERJEV_LEDGER) that redirects the ledger also redirects
    this state file, the same way _default_ledger_path's own callers
    expect."""
    safe = re.sub(r'[^A-Za-z0-9_.\-]', '_', str(session_id))
    return LEDGER_PATH.parent / "state" / f"stop-state-{safe}.json"


def _load_stop_state(session_id):
    try:
        obj = json.loads(_stop_state_path(session_id).read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_stop_state(session_id, state):
    """Never raises — a state file that cannot be written just means the
    next Stop event rescans from the same place, which is a duplicate
    verify at worst, not a crash."""
    path = _stop_state_path(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def _is_idle_notification_dup(body):
    """True when a teammate-message body is a JSON-wrapped
    {"type":"idle_notification", ...} envelope — the fleet's own duplicate
    of a report that already landed as plain text moments earlier (an
    idle-notification echo carries the same "result" text inside its own
    JSON, not a second, distinct report). Not a report block in its own
    right, so the Stop scan skips it rather than double-verifying the same
    content."""
    try:
        obj = json.loads(body)
    except ValueError:
        return False
    return isinstance(obj, dict) and obj.get("type") == "idle_notification"


def _find_new_teammate_reports(transcript_path, last_uuid, max_reports):
    """Every NEW <teammate-message> report block in `transcript_path` —
    "new" meaning found in a transcript record AFTER the one whose uuid is
    `last_uuid` (or from the start of the file when `last_uuid` is falsy or
    not found, e.g. a rotated/pruned transcript). A "report block" is one
    that is not an idle_notification duplicate (see
    _is_idle_notification_dup) and carries a report marker (see
    _REPORT_TRIGGER_RE) — chatter and launch acks are skipped. Capped at
    `max_reports`.

    Returns (reports, new_last_uuid): `reports` is a list of
    {"teammate_id", "body", "uuid"} dicts, file order; `new_last_uuid` is
    the uuid of the LAST record actually examined (whether or not it
    carried a report), so a caller that persists it verbatim resumes
    exactly where this scan stopped — including a stop mid-file because
    the report cap was hit. Never raises: any read/parse problem behaves
    like an empty transcript (`([], last_uuid)`)."""
    try:
        records = _read_transcript_records(transcript_path)
    except Exception:
        return [], last_uuid
    if not records:
        return [], last_uuid
    start_idx = 0
    if last_uuid:
        for i, rec in enumerate(records):
            if rec.get("uuid") == last_uuid:
                start_idx = i + 1
                break
    reports = []
    new_last_uuid = last_uuid
    for rec in records[start_idx:]:
        rec_uuid = rec.get("uuid")
        if rec.get("type") == "user":
            msg = rec.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            text = content if isinstance(content, str) else None
            if text and "<teammate-message" in text:
                for attrs, body in _TEAMMATE_MSG_RE.findall(text):
                    body = body.strip()
                    if not body:
                        continue
                    if _is_idle_notification_dup(body):
                        continue
                    if not _REPORT_TRIGGER_RE.search(body):
                        continue
                    idm = _TEAMMATE_ID_RE.search(attrs)
                    teammate_id = idm.group(1) if idm else "unknown"
                    reports.append({"teammate_id": teammate_id, "body": body,
                                    "uuid": rec_uuid})
        if rec_uuid:
            new_last_uuid = rec_uuid
        if len(reports) >= max_reports:
            break
    return reports[:max_reports], new_last_uuid


def _stop_scan_verify_one(r):
    """Run `verify` against one report found by _find_new_teammate_reports
    and print/ledger its verdict — same evidence auto-derivation as
    `hook prompt-verify`, plus the PR #20 0.80-confidence block rule
    (_hook_block_decision, with a --dry-run evidence probe spent only when
    a flag actually crosses the line, same as `hook verify --from-file`),
    but ADVISORY ONLY: a REJECT/block-worthy verdict here still never
    raises and the caller never turns it into this Stop event's own exit
    code. Never raises — any failure prints/logs an advisory 'error' line
    instead."""
    teammate_id, body = r["teammate_id"], r["body"]
    try:
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

            flags = _parse_strong_flags(door_out)
            claim_rows = _parse_claim_rows(door_out)
            evidence = _evidence_inventory(test_cmd=derived["test_cmd"] or "",
                                           worktree=derived["worktree"], pr=derived["pr"])
            line = _block_confidence_line()
            has_candidate = any(f["verdict"] in _BLOCKABLE_VERDICTS and f["score"] >= line
                                for f in flags)
            if has_candidate:
                probe = _evidence_probe(tmp_path, derived["worktree"], derived["test_cmd"] or "")
                if probe:
                    evidence = _evidence_inventory(test_cmd=derived["test_cmd"] or "",
                                                   worktree=derived["worktree"],
                                                   pr=derived["pr"], probe_stdout=probe)
            block_reasons, notes = _hook_block_decision(flags, claim_rows, evidence)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        label = {0: "CLEAN", 3: "READ", 4: "REJECT"}.get(code, "READ")
        if block_reasons and label != "REJECT":
            label = "REJECT"
        flag_str = ("; ".join(f"{f['key']} {f['verdict']} {f['score']:.2f}" for f in flags)
                   or "no flags")
        used = ", ".join(f"--{k} {v}" for k, v in
                         (("worktree", derived["worktree"]),
                          ("test-cmd", derived["test_cmd"]),
                          ("pr", derived["pr"])) if v) or "no evidence derived"
        health = "thin" if evidence.get("thin") else "ok"
        print(f"super-jev verify {teammate_id}: {label} — {flag_str} — {used} — health {health}")
        note_tail = (" — " + "; ".join(notes)) if notes else ""
        _hook_log(f"stop-scan: {teammate_id} — {label} (exit {code}) [{used}] "
                 f"health={health}{note_tail}", exit_code=0, skipped=False, flags=flags,
                 hook_mode=True, source="stop-transcript")
    except Exception as exc:
        print(f"super-jev verify {teammate_id}: ERROR — {exc.__class__.__name__} (advisory)")
        _hook_log(f"stop-scan: {teammate_id} — error ({exc.__class__.__name__}), "
                 "advisory-only", skipped=True, reason="stop-scan-error",
                 source="stop-transcript")


def _hook_stop_scan_teammate_reports(payload):
    """The Stop-hook companion to `hook prompt-verify`: scans
    payload["transcript_path"] for teammate-message report blocks this
    session (payload["session_id"]) has not already processed (tracked in
    a per-session state file, see _stop_state_path), and runs
    _stop_scan_verify_one on up to _stop_scan_max_reports() of them,
    within a _stop_scan_max_seconds() total time budget.

    ALWAYS a no-op on the caller's own exit code/behaviour — this changes
    nothing about the real `gate` verdict this Stop event is about; it
    only prints extra advisory lines and appends ledger rows with
    source="stop-transcript". No session_id or no readable transcript_path
    is a silent no-op (there is no state to key off, or nothing to scan).
    A time-budget overrun logs one advisory ledger line and stops taking
    new reports; the state file then advances only past what was actually
    attempted, so the remainder is retried on the NEXT Stop event rather
    than silently dropped. Never raises."""
    try:
        session_id = payload.get("session_id")
        transcript_path = payload.get("transcript_path")
        if not session_id or not isinstance(transcript_path, str) or not transcript_path:
            return
        state = _load_stop_state(session_id)
        if not state:
            # First Stop event this session has ever run the scan on: there
            # is no prior state file, so the naive read of
            # state.get("last_uuid") comes back None, and
            # _find_new_teammate_reports(..., None, ...) treats that as
            # "scan from the top of the file" — meaning a fresh session
            # attached to an already-long transcript would re-verify every
            # teammate report ever seen in it on its very first Stop. The
            # start point must be the transcript's CURRENT end instead:
            # record the last uuid now, process zero reports this call, and
            # only anything appended AFTER this point counts as "new" on
            # the next Stop event.
            records = _read_transcript_records(transcript_path)
            start_uuid = records[-1].get("uuid") if records else None
            _save_stop_state(session_id, {"last_uuid": start_uuid})
            return
        last_uuid = state.get("last_uuid")
        reports, scan_last_uuid = _find_new_teammate_reports(
            transcript_path, last_uuid, _stop_scan_max_reports())
        if not reports:
            if scan_last_uuid != last_uuid:
                _save_stop_state(session_id, {"last_uuid": scan_last_uuid})
            return
        deadline = time.monotonic() + _stop_scan_max_seconds()
        last_processed_uuid = last_uuid
        timed_out = False
        for r in reports:
            if time.monotonic() > deadline:
                timed_out = True
                _hook_log("stop-scan: time budget exceeded — remaining report(s) "
                         "deferred to the next Stop event", skipped=True,
                         reason="stop-scan-timeout", source="stop-transcript")
                break
            _stop_scan_verify_one(r)
            last_processed_uuid = r["uuid"]
        final_uuid = last_processed_uuid if timed_out else scan_last_uuid
        if final_uuid != last_uuid:
            _save_stop_state(session_id, {"last_uuid": final_uuid})
    except Exception as exc:  # advisory-only contract: never raise, never block
        _hook_log(f"stop-scan: unexpected error ({exc.__class__.__name__}) — fail-open",
                 skipped=True, reason="stop-scan-error", source="stop-transcript")


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
          the last N tool calls found in THIS TURN (records at or after
          the most recent real user prompt — see
          _current_turn_start_index; a tool call from an earlier turn is
          never counted as this turn's evidence) (N =
          SUPERJEV_HOOK_EVIDENCE_N, default 8; total size capped by
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
                      flags=_parse_strong_flags(door_out), unchecked=True, health="none")
        else:
            _hook_log(f"gate: unchecked — no tool evidence derivable from transcript_path "
                      f"({tp!r}); gate ran against {source}, no checkable claim "
                      f"(exit {code}), silent", exit_code=0, unchecked=True, health="none")
        # This IS the 2026-09-16 bug's own shape (no tool evidence
        # derivable, silently routed unchecked) — so this path in
        # particular always checks the running share, and prints the
        # notice even on the otherwise-silent branch above, since silent
        # unchecked runs are exactly what let 9 of 40 go unnoticed.
        notice = _running_unchecked_notice()
        if notice:
            print(notice)
        return 0

    try:
        # The teammate-report scan runs off transcript_path/session_id
        # alone, independent of whatever this Stop event's own gate
        # verdict turns out to be (allow, block, or the no-evidence
        # "unchecked" path below, which returns early) — so it fires here,
        # before any of `gate`'s own early returns, rather than being
        # threaded through every one of them. See
        # _hook_stop_scan_teammate_reports's docstring: always a no-op on
        # this call's own exit code.
        if door == "gate":
            _hook_stop_scan_teammate_reports(payload)
        if door == "gate":
            text = _hook_text(payload, ["last_assistant_message", "draft", "text", "prompt"])
            if text is None:
                _hook_log("gate: no usable text field in payload or transcript — "
                          "fail-open", skipped=True)
                return 0
            text = _strip_machine_tags(text)

            evidence = _hook_evidence_paths(payload)
            evidence_source = "payload"
            window_meta = None
            if not evidence:
                tp = payload.get("transcript_path")
                derived = None
                if isinstance(tp, str) and tp:
                    derived, window_meta = _derive_evidence_text_from_transcript(
                        tp, session_id=payload.get("session_id"), return_meta=True)
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
            _save_last_draft_evidence("gate", text, _read_evidence_text(evidence))
            # Deterministic count/PR cross-check — pure string work, no
            # model call, runs regardless of what the judge above said.
            # See deterministic_block_reasons's own docstring: fires only
            # on a real contradiction (a drafted count/PR state the
            # evidence itself disagrees with), never on an evidence gap.
            det_reason, count_pairing = _count_pairing(
                text, _read_evidence_text(evidence))
            det_block_reasons = [r for r in
                                 (det_reason,
                                  _pr_mismatch_reason(text, _read_evidence_text(evidence)))
                                 if r]
        else:
            det_block_reasons = []
            count_pairing = []
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
            # worker-verify's own captured table is the closest thing to
            # "the evidence" verify judged this report against — it has no
            # separate evidence FILE list of its own the way gate does.
            _save_last_draft_evidence("verify", text, door_out)

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
        # Only the gate door ever derives a wide window from the transcript
        # (see window_meta above; verify has no equivalent), so only gate
        # can carry a current_turn_empty signal into the block decision —
        # the secondary NOT_SUPPORTED/CONTRADICTED arm is suppressed on an
        # empty current turn, the primary OVERCLAIMS arm is not (see
        # _hook_block_decision_v3).
        gather_health = None
        if door == "gate" and window_meta and window_meta.get("current_turn_empty"):
            gather_health = {"current_turn_empty": True,
                             "reasons": ["the current turn ran no tools of its own; this "
                                        "window is previous-turn/receipts evidence only"]}
        block_reasons, block_notes = _hook_block_decision(flags, claim_rows, gather_health)
        # Deterministic reasons are never suppressed by the gather-health
        # check the judge-driven flags above go through — arithmetic on
        # text that WAS in the evidence window carries no "the gather was
        # too thin" failure mode the way a model's confidence score does.
        block_reasons = det_block_reasons + block_reasons

        if door == "gate" and getattr(a, "explain", False):
            _print_gate_window_explain(evidence_source, window_meta, flags, claim_rows,
                                       det_block_reasons, block_reasons, block_notes,
                                       count_pairing=count_pairing)

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
        def _print_ledger_notice_if_gate():
            # So an in-session bug shaped like the 2026-09-16 one (a hook
            # silently routing replies down the unchecked path) shows up
            # THIS turn instead of waiting for someone to read the ledger
            # later. verify's own hook run is scoped out — this only
            # watches the Stop-hook gate, matching the origin incident.
            if door != "gate":
                return
            notice = _running_unchecked_notice()
            if notice:
                print(notice)

        if action == "allow":
            _hook_log(f"{door}: allow (exit {code})", exit_code=0, flags=flags)
            _print_ledger_notice_if_gate()
            return 0
        if action == "block-forced-advisory":
            reason_bits = "; ".join(block_reasons) if block_reasons else f"exit {code}"
            advisory = f"super-jev gate: second pass (stop_hook_active) — advisory only, would have blocked on: {reason_bits}"
            print(advisory)
            _hook_log(f"gate: second pass, advisory only (exit {code}) — would have "
                     f"blocked on: {reason_bits}", exit_code=0, flags=flags)
            _print_ledger_notice_if_gate()
            return 0
        if action == "block":
            reason = f"super-jev {door} blocked this (exit {code})"
            if block_reasons:
                reason += ": " + "; ".join(block_reasons)
            print(reason, file=sys.stderr)
            _hook_log(f"{door}: block (exit {code})" +
                     (f" — strong flags: {'; '.join(block_reasons)}" if block_reasons else ""),
                     exit_code=2, flags=flags)
            _print_ledger_notice_if_gate()
            return 2
        note_tail = (" — " + "; ".join(block_notes)) if block_notes else ""
        advisory = f"super-jev {door} advisory (exit {code}){note_tail}"
        print(advisory)
        _hook_log(f"{door}: advisory (exit {code}){note_tail}", exit_code=0, flags=flags)
        _print_ledger_notice_if_gate()
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

def _token_totals(lines, today_only=False):
    """{"overall": {...}, "by_door": {door: {...}}} — calls/in_tok/
    est_cost_usd summed across every ledger line that carries token usage
    (an "in_tok" key — see token_usage_from_output). `today_only` restricts
    to lines whose ts starts with today's UTC date, same rule
    _ledger_count_today uses. "session" totals (the ask() spec's other
    axis) are simply this with today_only=False — the whole ledger file
    IS this session's own call record, there being no separate session id
    threaded through the call ledger."""
    today = datetime.now(timezone.utc).date().isoformat()
    overall = {"calls": 0, "in_tok": 0, "est_cost_usd": 0.0}
    by_door = {}
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if "in_tok" not in rec:
            continue
        if today_only and not str(rec.get("ts", "")).startswith(today):
            continue
        door = rec.get("door", "?")
        d = by_door.setdefault(door, {"calls": 0, "in_tok": 0, "est_cost_usd": 0.0})
        calls = rec.get("calls", 1)
        in_tok = rec.get("in_tok", 0)
        cost = rec.get("est_cost_usd", 0.0)
        d["calls"] += calls
        d["in_tok"] += in_tok
        d["est_cost_usd"] += cost
        overall["calls"] += calls
        overall["in_tok"] += in_tok
        overall["est_cost_usd"] += cost
    for d in list(by_door.values()) + [overall]:
        d["est_cost_usd"] = round(d["est_cost_usd"], 6)
    return {"overall": overall, "by_door": by_door}


def _print_token_totals(label, totals):
    o = totals["overall"]
    print(f"{label}: {o['calls']} call(s), {o['in_tok']} in_tok, "
          f"est ${o['est_cost_usd']:.6f}")
    for door in sorted(totals["by_door"]):
        d = totals["by_door"][door]
        print(f"  {door:<10} {d['calls']:>4} call(s)  {d['in_tok']:>8} in_tok  "
              f"est ${d['est_cost_usd']:.6f}")


# ---------------------------------------------------------- ledger health
#
# 2026-09-16: a hook bug silently routed 9 of 40 replies down the
# "unchecked" (advisory, exit 0) path — no evidence was derivable, gate
# ran against the last prompt instead of the real transcript, and nobody
# noticed because the ledger line for each one looked like any other
# advisory exit 0. The ledger already recorded every one of them; this is
# the read side that turns that into something a human (or a WARN line)
# actually sees.

DEFAULT_LEDGER_WINDOW = 50
DEFAULT_STOP_NOTICE_WINDOW = 20
DEFAULT_UNCHECKED_WARN_PCT = 25.0
UNCHECKED_WARN_ENV = "SUPERJEV_UNCHECKED_WARN"
# A bucket with only a handful of runs is noise, not signal — one
# unchecked run out of one is a 100% share but tells you nothing. Require
# at least this many runs in a bucket before its share can trip a WARN.
MIN_RUNS_FOR_WARN = 5

_NOTE_DOOR_RE = re.compile(r'^([a-z][a-z-]*):\s')


def _unchecked_warn_pct():
    """SUPERJEV_UNCHECKED_WARN, parsed as a float percent (e.g. "25" for
    25%); DEFAULT_UNCHECKED_WARN_PCT if unset or unparsable."""
    raw = os.environ.get(UNCHECKED_WARN_ENV)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_UNCHECKED_WARN_PCT


def _ledger_records(lines=None):
    """The ledger's raw JSONL lines (or a caller-supplied list of raw
    lines, for tests with a synthetic ledger), parsed to dicts. A line
    that is not valid JSON is dropped rather than raising — a single
    corrupt line must never take the whole health read down."""
    if lines is None:
        lines = _ledger_lines()
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _ledger_line_subdoor(entry):
    """Which door a hook-run ledger line is actually about. Every real
    Stop/PostToolUse hook firing shares entry['door'] == 'hook' (see
    _hook_log) — the door it was protecting (gate, verify, prompt-verify,
    stop-scan) instead rides in the free-text note's own "<door>: ..."
    prefix, which every _hook_log call site that knows its door writes
    (see cmd_hook's "allow (exit ...)"/"block (exit ...)"/"advisory
    (exit ...)" notes and cmd_hook_prompt_verify's "prompt-verify: ..." /
    "stop-scan: ..." notes). A hook call that failed before it even
    parsed its input (bad/empty/non-JSON stdin) has no such prefix; those
    land under the bare "hook" bucket."""
    note = entry.get("note", "") or ""
    m = _NOTE_DOOR_RE.match(note)
    return m.group(1) if m else "hook"


def _ledger_line_verdict(entry):
    """block | unchecked | advisory | allow — read off exit_code/skipped/
    unchecked, the same three-way split every _hook_log call site already
    encodes, just named for reporting."""
    if entry.get("exit_code") == 2:
        return "block"
    if entry.get("skipped") or entry.get("unchecked"):
        return "unchecked"
    note = entry.get("note", "") or ""
    if "advisory" in note:
        return "advisory"
    return "allow"


def _ledger_skip_reason(entry):
    """A short, machine-matchable tag for why an unchecked/skipped line
    was unchecked/skipped — entry['reason'] when a call site set one
    (most skip paths do), else a tag squeezed out of the free-text note
    (the no-tool-evidence "unchecked" path — the exact shape of the
    2026-09-16 bug — never sets reason=, so it needs this), else
    "other"."""
    reason = entry.get("reason")
    if reason:
        return reason
    note = (entry.get("note") or "").lower()
    if "no tool evidence derivable" in note:
        return "no-tool-evidence"
    if "stdin" in note:
        return "bad-stdin"
    if "no usable text field" in note:
        return "no-usable-text"
    if "no usable report text" in note:
        return "no-usable-report-text"
    if "not 'agent'" in note:
        return "not-agent-tool"
    if "unexpected error" in note:
        return "unexpected-error"
    return "other"


def _door_health_bucket(entries):
    """{"runs","blocked","advisory","unchecked","allow",
    "unchecked_share_pct","top_skip_reason"} for one list of hook-run
    ledger entries (either one door's slice or the whole window)."""
    bucket = {"runs": len(entries), "blocked": 0, "advisory": 0,
             "unchecked": 0, "allow": 0}
    verdict_to_key = {"block": "blocked", "advisory": "advisory",
                      "unchecked": "unchecked", "allow": "allow"}
    skip_reasons = {}
    for e in entries:
        v = _ledger_line_verdict(e)
        bucket[verdict_to_key[v]] += 1
        if v == "unchecked":
            r = _ledger_skip_reason(e)
            skip_reasons[r] = skip_reasons.get(r, 0) + 1
    bucket["unchecked_share_pct"] = (round(100.0 * bucket["unchecked"] / bucket["runs"], 1)
                                     if bucket["runs"] else 0.0)
    bucket["top_skip_reason"] = (max(skip_reasons, key=skip_reasons.get)
                                 if skip_reasons else None)
    return bucket


def ledger_health(window=DEFAULT_LEDGER_WINDOW, records=None):
    """Ledger health over the last `window` hook-run ledger lines
    (chronological, whole ledger — not per-door windows, so a
    lightly-used door just gets fewer of its own rows). Only lines with
    entry['door'] == 'hook' count — those are the one-line-per-hook-run
    records this is built to watch (see the module note above); direct
    CLI calls to gate/verify/sweep/... carry no skipped/unchecked
    concept and would only dilute the unchecked share.

    Returns {"window", "threshold_pct", "overall": bucket,
    "doors": {door: bucket}}, each bucket shaped by _door_health_bucket.
    `records`, if given, is a pre-parsed list of ledger dicts (tests pass
    a synthetic ledger this way instead of touching LEDGER_PATH)."""
    if records is None:
        records = _ledger_records()
    hook_lines = [e for e in records if e.get("door") == "hook"]
    windowed = hook_lines[-window:] if window else hook_lines
    by_door = {}
    for e in windowed:
        by_door.setdefault(_ledger_line_subdoor(e), []).append(e)
    return {
        "window": window,
        "threshold_pct": _unchecked_warn_pct(),
        "overall": _door_health_bucket(windowed),
        "doors": {name: _door_health_bucket(es) for name, es in by_door.items()},
    }


def _health_warnings(health):
    """[(label, bucket), ...] for every bucket (overall plus each door)
    whose unchecked share is at or above threshold_pct AND has at least
    MIN_RUNS_FOR_WARN runs (a 1-run 100% share is noise, not signal) —
    the empty-ledger / all-healthy / too-few-runs case gives back []."""
    out = []
    threshold = health["threshold_pct"]
    if (health["overall"]["runs"] >= MIN_RUNS_FOR_WARN
            and health["overall"]["unchecked_share_pct"] >= threshold):
        out.append(("overall", health["overall"]))
    for name in sorted(health["doors"]):
        b = health["doors"][name]
        if b["runs"] >= MIN_RUNS_FOR_WARN and b["unchecked_share_pct"] >= threshold:
            out.append((name, b))
    return out


def _print_ledger_health(health, heading="ledger health"):
    o = health["overall"]
    print(f"{heading} (last {health['window']} hook run(s), warn at "
          f"{health['threshold_pct']:g}% unchecked)")
    if not o["runs"]:
        print("  no hook runs recorded yet")
        return
    print(f"  {'door':<14} {'runs':>5} {'blocked':>8} {'advisory':>9} "
          f"{'unchecked':>10} {'unchecked%':>11}")
    print(f"  {'overall':<14} {o['runs']:>5} {o['blocked']:>8} {o['advisory']:>9} "
          f"{o['unchecked']:>10} {o['unchecked_share_pct']:>10.1f}%")
    for name in sorted(health["doors"]):
        d = health["doors"][name]
        print(f"  {name:<14} {d['runs']:>5} {d['blocked']:>8} {d['advisory']:>9} "
              f"{d['unchecked']:>10} {d['unchecked_share_pct']:>10.1f}%")
    for label, bucket in _health_warnings(health):
        print(f"  WARN: {label} unchecked share {bucket['unchecked_share_pct']:.1f}% "
              f"({bucket['unchecked']}/{bucket['runs']}) exceeds "
              f"{health['threshold_pct']:g}% — most common skip reason: "
              f"{bucket['top_skip_reason'] or 'n/a'}")


def cmd_ledger_health(a):
    window = a.window if getattr(a, "window", None) and a.window > 0 else DEFAULT_LEDGER_WINDOW
    health = ledger_health(window=window)
    warnings = _health_warnings(health)
    if getattr(a, "json", False):
        print(json.dumps({**health, "warn": bool(warnings)}))
    else:
        _print_ledger_health(health)
    return 4 if warnings else 0


def _running_unchecked_notice(window=DEFAULT_STOP_NOTICE_WINDOW):
    """One line, or None, for the Stop hook to print alongside its own
    verdict when the running unchecked share over the last `window` hook
    runs is at/over threshold — so an in-session bug like 2026-09-16's
    shows up the same turn instead of waiting for someone to read the
    ledger later. Scoped to hook-run lines only, same as ledger_health."""
    health = ledger_health(window=window)
    warnings = _health_warnings(health)
    if not warnings:
        return None
    label, bucket = warnings[0]
    return (f"[super-jev] ledger health: {label} unchecked share "
           f"{bucket['unchecked_share_pct']:.1f}% ({bucket['unchecked']}/{bucket['runs']} "
           f"of last {window}) — most common skip reason: "
           f"{bucket['top_skip_reason'] or 'n/a'}. Run `superjev.py ledger health` to see more.")


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
    print()
    _print_token_totals("token totals, today", _token_totals(lines, today_only=True))
    print()
    _print_token_totals("token totals, session (whole ledger)",
                        _token_totals(lines, today_only=False))
    return 0


# ---------------------------------------------------------------- feedback + calibration
#
# Kelvin's second ask: "every time I confirm a block was right or wrong,
# that should go into the calibration set automatically." This is the
# write side of the learning loop — `feedback` turns a human verdict on
# the LAST gate/verify hook decision into one calibration/cases.jsonl
# line; `calibration export` turns the running case log into the exact
# drafts/ evidence/ cases.json layout gate-bench-20260917's own
# run_bench.sh/summarize.py already consume, unchanged.

_HOOK_NOTE_DOOR_RE = re.compile(r"^(gate|verify):\s*(\S[\w -]*)")


def _parse_hook_note(note):
    """(door, verdict) out of a _hook_log note like "gate: allow (exit 0)"
    or "verify: block (exit 2) — strong flags: ...". verdict is the first
    word after the door prefix, uppercased (ALLOW/BLOCK/ADVISORY/...).
    (None, None) if `note` does not start with "gate:" or "verify:"."""
    m = _HOOK_NOTE_DOOR_RE.match(note or "")
    if not m:
        return None, None
    return m.group(1), m.group(2).strip().split()[0].upper()


def _last_dir():
    d = LEDGER_PATH.parent / "last"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_last_draft_evidence(door, draft_text, evidence_text):
    """Overwrite <ledger dir>/last/<door>-draft.md and -evidence.md with
    this hook run's draft/report and evidence — the pair `feedback` reads
    when the human confirms the very next block/allow was right or wrong.
    Never raises: a failure here must not break the hook it rides in."""
    try:
        d = _last_dir()
        (d / f"{door}-draft.md").write_text(draft_text or "", encoding="utf-8")
        (d / f"{door}-evidence.md").write_text(evidence_text or "", encoding="utf-8")
    except OSError as exc:
        print(f"super-jev: could not write last/{door}-* under {LEDGER_PATH.parent}: {exc}",
              file=sys.stderr)


def _calibration_dir():
    d = LEDGER_PATH.parent / "calibration"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _last_hook_ledger_entry(ledger_id=None):
    """The most recent ledger line that is a gate/verify hook decision
    (door == "hook", note starting "gate:"/"verify:"), or — when
    `ledger_id` is given — the ledger line with that exact id regardless
    of shape. None if nothing matches."""
    lines = _ledger_lines()
    if ledger_id:
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("id") == ledger_id:
                return rec
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("door") != "hook":
            continue
        door, verdict = _parse_hook_note(rec.get("note", ""))
        if door is None:
            continue
        rec["_door"] = door
        rec["_verdict"] = verdict
        return rec
    return None


def cmd_feedback(a):
    """`feedback <right|wrong> [--note ...] [--ledger-id ID]` — append one
    calibration case built from the LAST gate/verify hook decision (or the
    ledger line named by --ledger-id) plus the draft+evidence this repo
    just saved for that door under last/. Exits REFUSED if there is no
    matching ledger line or the last/ files are missing (never crashes on
    a cold ledger — cases.jsonl just gets nothing appended)."""
    entry = _last_hook_ledger_entry(ledger_id=getattr(a, "ledger_id", None))
    if entry is None:
        which = (f" for --ledger-id {a.ledger_id}" if getattr(a, "ledger_id", None) else "")
        return refuse(f"feedback: no gate/verify hook ledger line found{which}")
    door = entry.get("_door")
    verdict = entry.get("_verdict")
    if door is None:
        door, verdict = _parse_hook_note(entry.get("note", ""))
    if door is None:
        return refuse(f"feedback: ledger line {entry.get('id')} is not a gate/verify "
                      "hook decision — pass --ledger-id for one that is")

    last_dir = _last_dir()
    try:
        draft_text = (last_dir / f"{door}-draft.md").read_text(encoding="utf-8")
    except OSError:
        draft_text = ""
    try:
        evidence_text = (last_dir / f"{door}-evidence.md").read_text(encoding="utf-8")
    except OSError:
        evidence_text = ""

    case_id = entry.get("id") or uuid.uuid4().hex[:12]
    cal_dir = _calibration_dir()
    ev_dir = cal_dir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    ev_stable_path = ev_dir / f"{case_id}.md"
    try:
        ev_stable_path.write_text(evidence_text, encoding="utf-8")
    except OSError:
        pass

    case = {
        "id": case_id,
        "ts": entry.get("ts"),
        "door": door,
        "verdict": verdict,
        "exit_code": entry.get("exit_code"),
        "flags": entry.get("flags", []),
        "draft": draft_text,
        "evidence_path": str(ev_stable_path),
        "human": a.value,
        "note": getattr(a, "note", "") or "",
    }
    try:
        with open(cal_dir / "cases.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    except OSError as exc:
        return refuse(f"feedback: could not write calibration/cases.jsonl: {exc}")

    print(f"feedback: recorded human={a.value} for {door} ledger id={case_id} "
          f"(verdict {verdict}, exit {entry.get('exit_code')})")
    return 0


def _calibration_kind(verdict, human):
    """"lie" if the block/allow verdict, cross-checked against the human's
    right/wrong call, means the draft actually WAS a lie; "truth"
    otherwise. Table: block+right -> lie (a lie correctly caught);
    allow/advisory+wrong -> lie (a lie that slipped through); block+wrong
    -> truth (a true claim wrongly blocked); allow/advisory+right ->
    truth (a true claim correctly allowed)."""
    is_block = verdict == "BLOCK"
    return "lie" if is_block == (human == "right") else "truth"


def cmd_calibration(a):
    if a.action == "summary":
        return cmd_calibration_summary(a)
    if a.action == "export":
        return cmd_calibration_export(a)
    return refuse(f"calibration: unknown action {a.action!r} — use summary or export")


def cmd_calibration_summary(a):
    path = _calibration_dir() / "cases.jsonl"
    try:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        lines = []
    counts = {"right_block": 0, "wrong_block": 0, "right_allow": 0, "wrong_allow": 0}
    for line in lines:
        try:
            case = json.loads(line)
        except ValueError:
            continue
        is_block = case.get("verdict") == "BLOCK"
        human = case.get("human")
        key = ("right_block" if (is_block and human == "right") else
               "wrong_block" if (is_block and human == "wrong") else
               "right_allow" if (not is_block and human == "right") else
               "wrong_allow")
        counts[key] += 1
    print(f"calibration summary: {len(lines)} case(s) at {path}\n")
    print(f"  right block : {counts['right_block']}")
    print(f"  wrong block : {counts['wrong_block']}")
    print(f"  right allow : {counts['right_allow']}")
    print(f"  wrong allow : {counts['wrong_allow']}")
    return 0


def cmd_calibration_export(a):
    out_dir = Path(a.dir).expanduser()
    (out_dir / "drafts").mkdir(parents=True, exist_ok=True)
    (out_dir / "evidence").mkdir(parents=True, exist_ok=True)
    src = _calibration_dir() / "cases.jsonl"
    try:
        lines = [l for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        lines = []
    cases_out = []
    for line in lines:
        try:
            case = json.loads(line)
        except ValueError:
            continue
        cid = case.get("id") or f"case{len(cases_out) + 1}"
        verdict = case.get("verdict")
        human = case.get("human")
        kind = _calibration_kind(verdict, human)
        draft_text = case.get("draft") or ""
        ev_text = ""
        ev_path = case.get("evidence_path")
        if ev_path:
            try:
                ev_text = Path(ev_path).read_text(encoding="utf-8")
            except OSError:
                ev_text = ""
        (out_dir / "drafts" / f"{cid}.md").write_text(draft_text, encoding="utf-8")
        (out_dir / "evidence" / f"{cid}.md").write_text(ev_text, encoding="utf-8")
        cases_out.append({
            "id": cid,
            "kind": kind,
            "flavor": case.get("door"),
            "draft": draft_text,
            "evidence_note": case.get("note") or
                            f"human={human}; door={case.get('door')}; verdict={verdict}",
        })
    (out_dir / "cases.json").write_text(json.dumps(cases_out, indent=2), encoding="utf-8")
    print(f"calibration export: {len(cases_out)} case(s) -> {out_dir}")
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
    today_totals = _token_totals(_ledger_lines(), today_only=True)
    window = a.window if getattr(a, "window", None) and a.window > 0 else DEFAULT_LEDGER_WINDOW
    health = ledger_health(window=window)
    health_warnings = _health_warnings(health)

    if json_mode:
        emit_json("status", "OK", 0, "door states as read off disk", {
            "doors": [{"door": n, "state": s, "wraps": w} for n, s, w in rows],
            "harness_repo": str(repo),
            "harness_commit": harness_commit(repo),
            "typesafe_key_present": bool(os.environ.get("TYPESAFE_API_KEY")),
            "ledger_path": str(LEDGER_PATH),
            "ledger_calls_today": ledger_today,
            "token_totals_today": today_totals,
            "ledger_health": health,
            "ledger_health_warn": bool(health_warnings),
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
    _print_token_totals("token totals, today", today_totals)
    print()
    _print_ledger_health(health)
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
                    help="verify --from-file, or gate (stdin): print how much evidence "
                         "was gathered (for gate: the wide window's composition — bytes "
                         "per segment, previous turns found/dropped, receipts count), "
                         "the per-claim table and which rule decided, "
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
    lg_subs = lg.add_subparsers(dest="ledger_action")
    lg_health = lg_subs.add_parser("health",
                                   help="unchecked/skipped share per door over recent hook "
                                        "runs, for scripts — exit 0 ok, exit 4 warn")
    lg_health.add_argument("--window", type=int, default=DEFAULT_LEDGER_WINDOW,
                           help=f"how many recent hook runs to look at "
                                f"(default {DEFAULT_LEDGER_WINDOW})")
    _add_json_flag(lg_health)
    lg_health.set_defaults(func=cmd_ledger_health)

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
    st.add_argument("--window", type=int, default=DEFAULT_LEDGER_WINDOW,
                    help=f"ledger health: how many recent hook runs to look at "
                         f"(default {DEFAULT_LEDGER_WINDOW})")
    _add_json_flag(st)
    st.set_defaults(func=cmd_status)

    fb = subs.add_parser("feedback",
                         help="record whether the LAST gate/verify hook decision was "
                              "right or wrong — feeds the calibration set")
    fb.add_argument("value", choices=["right", "wrong"],
                    help="was the block (or allow) the right call?")
    fb.add_argument("--note", default="", help="why, in your own words")
    fb.add_argument("--ledger-id", dest="ledger_id", default=None,
                    help="give feedback on a specific ledger line's id instead of "
                         "the most recent gate/verify hook decision")
    fb.set_defaults(func=cmd_feedback)

    cal = subs.add_parser("calibration", help="the calibration set built from `feedback`")
    cal_subs = cal.add_subparsers(dest="action")
    cal_sum = cal_subs.add_parser("summary",
                                  help="counts of right/wrong blocks and right/wrong allows")
    cal_sum.set_defaults(func=cmd_calibration, action="summary")
    cal_exp = cal_subs.add_parser("export",
                                  help="write drafts/ evidence/ cases.json for run_bench.sh")
    cal_exp.add_argument("dir", help="output directory, created if missing")
    cal_exp.set_defaults(func=cmd_calibration, action="export")
    cal.set_defaults(func=cmd_calibration, action=None)
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
