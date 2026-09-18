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
chain · guard · ask · status

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
import contextlib
import fcntl
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
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parent.parent  # skills/super-jev/superjev.py -> repo root

# Fleet-local fallbacks. Real on the machine this skill was authored on;
# almost certainly absent on a fresh clone, where SUPERJEV_GATE_CMD and
# SUPERJEV_VERIFY_CMD take over instead.
FLEET_JEV_LIB = HOME / ".claude/skills/jev-check/lib/jev.py"
FLEET_VERIFY_PY = HOME / ".claude/skills/worker-verify/verify.py"

# The pure derive-facts/pre-rules pair (src/enhance/derive-facts.ts), reached
# through its own tiny CLI so a Python process can call it without an FFI.
# Only used by `verify`'s door-absent fallback — see _derived_facts_fallback.
DERIVE_FACTS_CLI = REPO_ROOT / "src" / "derive-facts-cli.ts"

# ================================================ EVIDENCE GUARD (blocklist + redactor)
# The ONE place this file asks "may I open this path?" and "is this text safe
# to ship?" before it opens a path or lets text into an evidence pack that
# reaches the judge. Pure-Python MIRROR of
# /Users/admin/super-jev-experiments/areas-20260918/atoms/atoms.py (section 0)
# and of src/enhance/evidence-guard.ts, deliberately duplicated rather than
# bridged for the same reason as DERIVED_FACTS above: the Stop hook runs on
# every turn and a node process's startup per call is too slow for it, and
# REPO_ROOT does not resolve to this repo on a fleet install. Added
# 2026-09-18 for AUDIT.md findings S1-S7 (most verification doors had no read
# blocklist and the one that did was name-only).
# test/enhance/fixtures/evidence-guard-cases.json is the one fixture both
# languages run (test/enhance/evidence-guard.test.ts here,
# skills/super-jev/tests/test_evidence_guard.py there), so the two sides
# cannot silently drift apart.
BLOCKED_PATH_PATTERNS = (
    r"(^|/)logins\.md$",                     # the fleet login vault
    r"(^|/)[^/]*-secret\.md(/|$)",           # ~/agents/global/tools/*-secret.md
    r"(^|/)[^/]*secret[^/]*(/|$)",           # any path segment naming a secret
    r"(^|/)\.env(/|$)",                      # .env
    r"(^|/)\.env\.[^/]*(/|$)",               # .env.local, .env.production
    r"(^|/)profile(/|$)",                    # ~/agents/global/profile/
    r"(^|/)documents(/|$)",                  # ~/agents/global/documents/
    r"(^|/)\.config/pw-[^/]*",               # playwright session profiles
    r"(^|/)[^/]*cookies[^/]*(/|$)",          # cookies.sqlite, Cookies
    r"(^|/)[^/]*\.pem(/|$)",
    r"(^|/)[^/]*\.key(/|$)",
    r"(^|/)id_rsa[^/]*(/|$)",
    r"(^|/)[^/]*tokens?[^/]*(/|$)",          # token / tokens / *token*.json
    r"(^|/)[^/]*credentials?[^/]*(/|$)",     # ~/.aws/credentials, credentials.json
)
_BLOCKED_RX = re.compile("|".join(BLOCKED_PATH_PATTERNS), re.I)
SKIPPED_FACT = "skipped: blocked path"


def is_blocked_path(p, allow=()):
    """True when `p` must never be opened, read, grepped or listed. See
    atoms.py's function of the same name for the full contract."""
    if not p:
        return False
    full = os.path.expanduser(str(p))
    for a in allow or ():
        a = os.path.expanduser(str(a))
        if full == a or full.startswith(a.rstrip("/") + "/"):
            return False
    return bool(_BLOCKED_RX.search(full))


def blocked_path_fact(path, source="path"):
    """The fact a gatherer returns INSTEAD of reading a blocked path."""
    return {"ok": False, "value": None, "source": source, "blocked": True,
            "cmd": f"({SKIPPED_FACT}: {path})", "exists": None, "path": path,
            "full": None, "lines": None, "state": "blocked"}


_REDACT_PATTERNS = (
    ("openai-key", r"\bsk-[A-Za-z0-9_\-]{12,}"),
    ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{12,}"),
    ("slack-token", r"\bxox[baprs]-[A-Za-z0-9\-]{8,}"),
    ("aws-key-id", r"\bAKIA[0-9A-Z]{12,}"),
    ("bearer-token", r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    ("authorization", r"(?i)\bauthorization\s*:\s*\S+"),
    ("long-hex", r"\b[0-9a-fA-F]{65,}\b"),
    ("api-key", r"\b[A-Za-z0-9_\-]{30,}\b"),
)
_EMAIL_PATTERN = ("email", r"\b[\w.+\-]+@[\w\-]+\.[\w.\-]{2,}\b")
_HEX_ONLY_RX = re.compile(r"^[0-9a-fA-F]+$")


def _looks_like_digest(s):
    return bool(_HEX_ONLY_RX.match(s)) and len(s) <= 64


def _looks_like_identifier(s):
    parts = re.split(r"[-_]", s)
    if len(parts) < 2:
        return False
    if any(len(p) > 12 for p in parts):
        return False
    for p in parts:
        if any(c.isupper() for c in p) and any(c.isdigit() for c in p):
            return False
    return True


def _is_secret_blob(s):
    if len(s) < 30:
        return False
    if _looks_like_digest(s):
        return False
    if not (any(c.isalpha() for c in s) and any(c.isdigit() for c in s)):
        return False
    if _looks_like_identifier(s):
        return False
    return True


def _path_segment(text, start, end):
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return before == "/" and after in ("/", "")


_COMPILED_REDACT = tuple((k, re.compile(p)) for k, p in _REDACT_PATTERNS)
_COMPILED_EMAIL = (_EMAIL_PATTERN[0], re.compile(_EMAIL_PATTERN[1]))


def redact_counted(text, redact_emails=False):
    """Return (redacted_text, count, by_kind) — the Python mirror of
    evidence-guard.ts's `redactCounted`. Never raises."""
    if not text:
        return (text or ""), 0, {}
    out = str(text)
    by_kind = {}
    for kind, rx in _COMPILED_REDACT:
        def repl(m, kind=kind):
            span = m.group(0)
            if kind == "api-key" and _path_segment(out, m.start(), m.end()):
                return span
            if kind == "api-key" and not _is_secret_blob(span):
                return span
            by_kind[kind] = by_kind.get(kind, 0) + 1
            return f"[REDACTED:{kind}]"
        out = rx.sub(repl, out)
    if redact_emails:
        kind, rx = _COMPILED_EMAIL
        def repl_email(m, kind=kind):
            by_kind[kind] = by_kind.get(kind, 0) + 1
            return f"[REDACTED:{kind}]"
        out = rx.sub(repl_email, out)
    count = sum(by_kind.values())
    return out, count, by_kind


def redact(text, redact_emails=False):
    """Convenience form that drops the count (atoms.py's `redact` signature)."""
    return redact_counted(text, redact_emails)[0]


# --------------------------------------------------- catch-ledger-only redaction
#
# The patterns below fire ONLY on the catch-ledger path (the 240-char draft
# excerpt and the opt-in saved payload copy) — never on the gate/verify
# evidence window itself, which stays governed by redact()/redact_counted()
# above unchanged. The catch ledger is customer-facing text a human tags by
# hand and reads later, so it gets the wider net: emails (redact() called
# with redact_emails=True) plus US phone numbers, SSN-shaped 3-2-4 digit
# strings, and 13+ digit card numbers (space, dash or dot separators).
#
# Card numbers are handled separately from the table below (see
# _CARD_NUMBER_*_RX/_catch_redact_card_repl), not as a plain find-and-replace
# pattern, for three reasons (see N1, N3):
#   - the old `\b(?:\d[ \-]?){13,19}\b` form silently missed any run of 20+
#     digits: \b requires a transition into/out of a word character, and
#     inside a longer all-digit run there is no such transition anywhere
#     except at the run's own true start/end — capped at 19 reps, the
#     regex could never land a cut point on a real \b, so it just never
#     matched at all. An ungrouped (no separator) run just needs its upper
#     bound removed — see _CARD_NUMBER_PLAIN_RX.
#   - a bare 13-digit ungrouped run is often a plausible unix-millisecond
#     timestamp (this repo's own ledger timestamps and payload fields are
#     full of them), not a card number, and redacting those made ordinary
#     catch records unreadable for no privacy benefit. Only a 13-digit run
#     shaped like one (starts "1", second digit 5-9 — roughly the
#     2015-2029 range any real timestamp here falls in) is left alone;
#     13 digits in any other shape, and every run of 14+ digits, still
#     redacts.
#   - N3: a SEPARATED (space/dash/dot) digit run is a different animal.
#     The old "13+ digits, any mix of separators" form matched things that
#     were never a card number at all — most sharply, a dotted
#     date+time stamp like "2026.09.18.10.46.33.123" (a card-hint id
#     format this repo uses elsewhere), which has 17 digits and plenty of
#     dot separators but no card shape whatsoever. A separated match is
#     now required to be an EXACT card grouping — 4-4-4-4 (Visa/MC/Discover,
#     16 digits) or 4-6-5 (Amex, 15 digits) — using the SAME separator
#     throughout (a backreference), so "4111.1111.1111.1111" still
#     redacts but a 7-group dotted timestamp never matches the shape at
#     all. As a second guard, even a genuine 4-4-4-4/4-6-5 shape is left
#     alone when its first group looks like a plausible year (starts "19"
#     or "20") — a date that happens to fall into a 4-digit-group pattern
#     is a false positive, not a card number.
# \b at both ends is kept for exactly the reason it always had one: a
# digit run glued to a letter (e.g. inside a git id or other alphanumeric
# token) is never a card number and is never matched, because \b treats
# letters and digits as the same "word" character class.
_CARD_NUMBER_PLAIN_RX = re.compile(r"\b\d{13,}\b")
_CARD_NUMBER_GROUPED_RX = re.compile(
    r"\b\d{4}([ \-.])\d{4}\1\d{4}\1\d{4}\b"      # 4-4-4-4 (Visa/MC/Discover)
    r"|"
    r"\b\d{4}([ \-.])\d{6}\2\d{5}\b"             # 4-6-5 (Amex)
)


def _looks_like_unix_ms_timestamp(digits):
    """True for a 13-digit run shaped like a plausible unix-millisecond
    timestamp (starts "1", second digit 5-9) — see the module note above
    _CARD_NUMBER_PLAIN_RX. Only ever checked against exactly 13 digits;
    anything longer is never a timestamp candidate and always redacts."""
    return len(digits) == 13 and digits[0] == "1" and digits[1] in "56789"


def _looks_like_a_year_group(first_group):
    """True when a 4-digit group (the first group of a grouped card-shape
    match) looks like a plausible year — starts "19" or "20" — the N3
    guard against a date string that happens to land on a 4-4-4-4/4-6-5
    shape."""
    return first_group[:2] in ("19", "20")


def _catch_redact_card_plain_repl(m):
    digits = m.group(0)
    if _looks_like_unix_ms_timestamp(digits):
        return digits
    return "[REDACTED:card-number]"


def _catch_redact_card_grouped_repl(m):
    text = m.group(0)
    first_group = text[:4]
    if _looks_like_a_year_group(first_group):
        return text
    return "[REDACTED:card-number]"


# US-shaped phone numbers only (see N2): area code's first digit is 2-9
# (0 and 1 are never a real US area code's first digit — simplest way to
# stop matching a non-phone 3-3-4 digit shape like a numeric range,
# "100-200-3000", without a lookup table). `(?<!\d)`/`(?!\d)` refuse a
# match glued to another digit on either side (an ordinary phone number is
# never itself part of a longer digit run), and `(?!\.\d)` refuses a match
# immediately followed by a decimal continuation (part of a longer
# dotted/decimal sequence — a coordinate, not a phone number).
_CATCH_REDACT_PATTERNS = (
    ("phone", r"(?<!\d)(?:\+?1[ \-.]?)?\(?[2-9]\d{2}\)?[ \-.]\d{3}[ \-.]\d{4}(?!\d)(?!\.\d)"),
    ("ssn", r"\b\d{3}-\d{2}-\d{4}\b"),
)
_COMPILED_CATCH_REDACT = tuple((k, re.compile(p)) for k, p in _CATCH_REDACT_PATTERNS)


def _catch_redact(text):
    """The redaction used ONLY by the catch ledger (draft excerpt + saved
    payload) — never applied to the gate/verify evidence window. Runs the
    general redact() with redact_emails=True (secrets, credentials, plus
    emails), then, catch-ledger-only, redacts card numbers — an ungrouped
    13+ digit run (unix-ms timestamps excepted) or a separated run in an
    exact 4-4-4-4/4-6-5 card grouping (a plausible year first group
    excepted — see N3, _CARD_NUMBER_PLAIN_RX/_CARD_NUMBER_GROUPED_RX) —
    US-shaped phone numbers, and SSN-shaped 3-2-4 digit strings. Never
    raises: an empty/None input returns ""."""
    if not text:
        return text or ""
    out = redact(str(text), redact_emails=True)
    out = _CARD_NUMBER_GROUPED_RX.sub(_catch_redact_card_grouped_repl, out)
    out = _CARD_NUMBER_PLAIN_RX.sub(_catch_redact_card_plain_repl, out)
    for kind, rx in _COMPILED_CATCH_REDACT:
        out = rx.sub(f"[REDACTED:{kind}]", out)
    return out


class GuardTally:
    """Running counters accumulated across one door call: how many paths
    were skipped and how many redactions were made, for the `guard`
    subcommand / `--explain` and the ledger."""

    def __init__(self):
        self.paths_skipped = 0
        self.redactions = 0
        self.by_kind = {}

    def check_path(self, p, allow=()):
        blocked = is_blocked_path(p, allow)
        if blocked:
            self.paths_skipped += 1
        return blocked

    def redact(self, text, redact_emails=False):
        out, count, by_kind = redact_counted(text, redact_emails)
        self.redactions += count
        for k, v in by_kind.items():
            self.by_kind[k] = self.by_kind.get(k, 0) + v
        return out

    def summary(self):
        return f"guard: {self.paths_skipped} path(s) skipped, {self.redactions} redaction(s)"

    def to_dict(self):
        return {"paths_skipped": self.paths_skipped, "redactions": self.redactions,
                "by_kind": dict(self.by_kind)}


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

# ------------------------------------------------- gate latency budget
#
# 2026-09-18. Measured, not assumed: on a heavy turn the Stop hook made
# the user wait minutes, and almost none of that wait belonged to the
# gate's own verdict — which was among the fastest things in the event. It
# belonged to the advisory teammate-report scan
# (_hook_stop_scan_teammate_reports), which ran SEVERAL live `verify`
# checks before the gate check ever started. Two things made that
# possible:
#
#   1. Nothing bounded the WHOLE Stop event. The scan had a budget of its
#      own, but it was checked only BEFORE each report and never during,
#      so a couple of slow checks sailed straight past it and the budget
#      only refused the ones that came after them.
#   2. cap_check_and_truncate cannot see a verify check's real input. It
#      estimates the files super-jev hands the door; worker-verify then
#      gathers git/diff/PR evidence ITSELF, inside the check, so the real
#      input is far larger than anything measured beforehand. A pre-call
#      size cap is therefore not a latency control for verify at all —
#      only wall-clock is.
#
# So the controls below are wall-clock first and token-cap second:
#
#   SUPERJEV_GATE_BUDGET_S     total wall-clock for one Stop event's
#                              checks. Past it the gate
#                              returns ADVISORY exit 3 with a "budget
#                              exceeded, not judged" line and a ledger
#                              reason of "budget-exceeded" — which lands
#                              in the health monitor's LOST bucket,
#                              because a reply that went unjudged is
#                              exactly the thing that must stay visible.
#   SUPERJEV_GATE_MAX_CALLS    live judge calls one Stop event may spend
#                              (default 1). The gate's own verdict has
#                              first claim on it; the advisory scan may
#                              only spend what is left, which at the
#                              default is nothing.
#   SUPERJEV_GATE_WINDOW_TOK   one hard cap on the assembled gate window,
#                              enforced ONCE, immediately before the call
#                              (default 8000 tokens) — see
#                              trim_window_to_token_budget.
#   SUPERJEV_STOP_SCAN_MIN_S   the scan will not START a live verify call
#                              with less than this much budget left
#                              (default 20 s), rather than starting one it
#                              would have to kill. Its reports defer to
#                              the next Stop event, as they already do on
#                              a timeout, and the ledger reason for that
#                              is "stop-scan-deferred" — a DEFERRED record,
#                              never "budget-exceeded" and never LOST. The
#                              reply itself is judged by the gate on the
#                              same event, so calling this a lost check
#                              was simply wrong (2026-09-18). Note this
#                              default (20 s) is deliberately above the
#                              default event budget (15 s): at the default
#                              allowance of one call the gate holds it
#                              anyway, so the scan always defers and only
#                              ever spends a call when an operator raises
#                              SUPERJEV_GATE_MAX_CALLS.
#   SUPERJEV_STOP_SCAN_MAX_BYTES  the scan reads only the last N bytes of
#                              the transcript (default 2 MB) instead of
#                              all of it; the live transcripts in the
#                              2026-09-18 set were 11 MB and 13 MB.
GATE_BUDGET_S_ENV = "SUPERJEV_GATE_BUDGET_S"
DEFAULT_GATE_BUDGET_S = 15.0
GATE_MAX_CALLS_ENV = "SUPERJEV_GATE_MAX_CALLS"
DEFAULT_GATE_MAX_CALLS = 1
GATE_WINDOW_TOK_ENV = "SUPERJEV_GATE_WINDOW_TOK"
DEFAULT_GATE_WINDOW_TOK = 8_000
STOP_SCAN_MIN_BUDGET_S_ENV = "SUPERJEV_STOP_SCAN_MIN_S"
DEFAULT_STOP_SCAN_MIN_BUDGET_S = 20.0
STOP_SCAN_MAX_BYTES_ENV = "SUPERJEV_STOP_SCAN_MAX_BYTES"
DEFAULT_STOP_SCAN_MAX_BYTES = 2_000_000

# The advisory line a budget-exceeded Stop event prints instead of a
# verdict. Deliberately says "not judged" — the reply was NOT checked, and
# an advisory that reads like an allow is the failure mode this whole file
# exists to prevent.
BUDGET_EXCEEDED_ADVISORY = (
    "super-jev gate: budget exceeded, not judged — the "
    f"{GATE_BUDGET_S_ENV} wall-clock budget ran out before this reply could be "
    "checked. Nothing about it was verified; treat its claims as unchecked.")
BUDGET_EXCEEDED_REASON = "budget-exceeded"

# The advisory teammate-report scan's own deferral tag. It is NOT
# BUDGET_EXCEEDED_REASON and must never become it again: that reason means
# "this turn's reply shipped and nothing judged it" and sits in the health
# monitor's LOST bucket. The scan running short of budget means something
# entirely different — the gate still judged the reply, and the scan's
# advisory worker-report checks are retried on the next Stop event, because
# the state file only advances past reports it actually attempted. Sharing
# one reason string made every Stop event with a new worker report report a
# lost check that never happened (2026-09-18).
SCAN_DEFERRED_REASON = "stop-scan-deferred"
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
# 2026-09-18: '#' made optional (PR\s*#?(\d+)) so "PR 39 merged" and
# "merged PR 39" match, not just "PR #39 merged" — see SET2-AUDIT.md's l30:
# the identity guard downstream (the number must match a receipt in the
# evidence, see _pr_mismatch_reason/_facts_merge_claims) is unchanged, so
# this only widens which claims get COMPARED, never which ones get trusted.
_PR_MERGED_CLAIM_RE = re.compile(r'PR\s*#?(\d+)\b[^.\n]{0,30}?\bmerged\b', re.IGNORECASE)
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


def _fact_block_reasons(facts):
    """The `CONTRADICTED_BY_FACT` sentences out of `facts` (a list of the
    plain-English strings `derive_window_facts` produces), in order, each
    already carrying its own family label (WRITTEN FILE, REMOVED, missing
    path, diffstat, count, PR-state, stale-report, ...) and identity
    detail — literal string work over text that WAS in the window, so it
    is added to `det_block_reasons` the same way `_count_pairing` and
    `_pr_mismatch_reason` already are: no model call, no health gate.

    `SUPPORTED` (and any other non-CONTRADICTED_BY_FACT verdict — CHECKED,
    RESIDUE, advisory-only lines) never appears here and so never blocks
    and never vetoes another arm; see docs/hooks.md."""
    return [f for f in (facts or []) if "CONTRADICTED_BY_FACT" in f]


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

# Judge-advisory mode (2026-09-18). SUPERJEV_GATE_JUDGE_ADVISORY=1 demotes a
# `hook gate` block to an advisory print + exit 0 when EVERY reason behind it
# came from the judge (the OVERCLAIMS arm, or — under SUPERJEV_RULE=v2 — the
# secondary NOT_SUPPORTED/CONTRADICTED arm). It never touches a block that
# carries even one deterministic reason (a count mismatch, a PR mismatch, or
# a CONTRADICTED_BY_FACT fact sentence) — those still block with the same
# exit code as today, unconditionally. See cmd_hook's "block-judge-advisory"
# branch, which checks this against `det_block_reasons` being empty rather
# than special-casing which judge arm fired, so it covers v2 and v3 alike.
JUDGE_ADVISORY_ENV = "SUPERJEV_GATE_JUDGE_ADVISORY"


def _judge_advisory_enabled():
    return os.environ.get(JUDGE_ADVISORY_ENV, "0") == "1"

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
         SUPERJEV_BLOCK_CONF (default 0.80) is a SECONDARY trigger —
         2026-09-18, demoted from blocking to ADVISORY-ONLY (see
         docs/hooks.md, "gate v4 — secondary arm demoted"): across both
         gate-bench sets (70 cases) this arm never once was the sole
         reason a real lie got caught, and on the untuned set-2 replies it
         was the sole reason 5 true replies got blocked (longer,
         multi-claim status reports carry plan/opinion/self-audit
         sentences no tool result could ever support, which this arm
         cannot tell apart from a real unsupported claim). It still runs,
         still scores, and still prints as an advisory line in
         `--explain` and stderr feedback for every case that crosses the
         line — it is useful signal about which sentences a reader should
         check — but it can never by itself produce a block. Blocking
         stays with rule 1 (OVERCLAIMS) and the deterministic count/PR
         arm the caller merges in.
      3. SELF_CONTRADICTORY is never a block reason, alone or in company,
         same as v2/v3 — it still prints as an advisory.
      4. The OVERCLAIM_100_BLOCK fragile arm (SUPERJEV_OVERCLAIM_100_BLOCK)
         still applies underneath rule 1; with the line already at 0.90 it
         is a near no-op, kept only so the old 0.995-floor A/B is still
         reachable.

    `current_turn_empty` deliberately does NOT gate rule 1 — OVERCLAIMS
    still blocks on its own at or above the line even when this turn ran
    no tools, as long as the gather is otherwise healthy. A reply is not
    made safe by the fact that this turn ran no tools; suppressing the
    primary arm on an empty current turn was worth three caught lies
    against zero blocked truths on the 40-case bench (docs/hooks.md). The
    empty-current-turn health gate itself is otherwise unchanged by the
    2026-09-18 demotion — it still governs whether the secondary arm's
    advisory calls out the empty-turn caveat, same wording as before.

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
            # 2026-09-18 (SKIPS-20260918.md, l22/l24): the judge flagged
            # this claim at or above the block line and the empty-current-
            # turn health gate suppressed it — checked, flagged, and let
            # through, not a silent miss. Marked with a distinct note
            # shape ("current turn ran no tools of its own") so cmd_hook
            # can pull the (key, verdict, score) back out and record it
            # under ledger_health's SUPPRESSED bucket (see
            # _suppressed_empty_turn_from_notes / SKIP_REASON_BUCKETS).
            notes.append(
                f"{k} {v} {s:.2f} crossed the {line:.2f} line but the current "
                "turn ran no tools of its own (evidence is previous-turn/"
                "receipts material only) — advisory, not a block; the primary "
                "OVERCLAIMS arm is unaffected")
            continue
        if is_secondary:
            # 2026-09-18: demoted to advisory-only, unconditionally — see
            # rule 2 above. Still surfaced, never used to block.
            notes.append(
                f"{k} {v} {s:.2f} crossed the {line:.2f} line — advisory "
                "only, the secondary NOT_SUPPORTED/CONTRADICTED arm never "
                "blocks on its own (gate v4)")
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


_SUPPRESSED_EMPTY_TURN_NOTE_RE = re.compile(
    r'^(?P<key>\S+) (?P<verdict>[A-Z_]+) (?P<score>\d+\.\d+) crossed .* current '
    r'turn ran no tools of its own')


def _suppressed_empty_turn_from_notes(notes):
    """The top (key, verdict, score) triple for the highest-scoring flag
    in `notes` (a hook run's block_notes) that the empty-current-turn
    health gate suppressed from blocking — see the
    "is_secondary and empty_current_turn" branch of
    _hook_block_decision_v3 — or None if no note carries that shape.
    "Top" = highest score, since more than one claim can be suppressed
    the same turn. Used to populate the ledger's
    reason="flagged-suppressed-empty-turn" record (cmd_hook) that feeds
    ledger_health's SUPPRESSED bucket."""
    best = None
    for n in notes or []:
        m = _SUPPRESSED_EMPTY_TURN_NOTE_RE.match(n)
        if not m:
            continue
        score = float(m.group("score"))
        if best is None or score > best[2]:
            best = (m.group("key"), m.group("verdict"), score)
    return best


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


# The hook payload (stdin JSON from a real Claude Code Stop/PostToolUse/
# UserPromptSubmit event) for the invocation currently running, if any —
# set once by cmd_hook/cmd_hook_prompt_verify right after stdin is parsed,
# and read back by _current_bot_id/_current_origin below so every ledger
# write in this same process (including the ones inside run_door, which
# fire from deep inside cmd_gate/cmd_verify) can attribute itself without
# payload having to be threaded through every call site. A manual CLI
# invocation (no hook) leaves this None, which both helpers treat as "no
# hook context" rather than an error.
_ACTIVE_HOOK_PAYLOAD = None


def _bot_id_from_transcript_path(transcript_path):
    """The claw4mac project-dir segment out of a transcript_path — the
    part after "agent-cwd-" up to the next path separator, e.g.
    "claw4mac-primary" out of ".../-Users-admin--ai-wrapper-agent-cwd-
    claw4mac-primary/<uuid>.jsonl". None if transcript_path is not a
    string, or carries no such segment."""
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    m = re.search(r'agent-cwd-([^/]+)', transcript_path)
    return m.group(1) if m else None


def _current_bot_id():
    """The bot id a ledger entry attributes itself to: CLAW4MAC_SESSION_ID
    (the env var the fleet actually sets on each seat, e.g. "primary"),
    else CLAW4MAC_BOT_ID, else CLAUDE_BOT_ID from the environment if any of
    the three is set, else derived from the active hook payload's
    transcript_path (see _bot_id_from_transcript_path) with the
    "claw4mac-" prefix stripped so a derived id lands in the same
    vocabulary as the env vars (e.g. "primary", not "claw4mac-primary"),
    else "unknown"."""
    env_bot = (os.environ.get("CLAW4MAC_SESSION_ID")
               or os.environ.get("CLAW4MAC_BOT_ID")
               or os.environ.get("CLAUDE_BOT_ID"))
    if env_bot:
        return env_bot
    payload = _ACTIVE_HOOK_PAYLOAD or {}
    derived = _bot_id_from_transcript_path(payload.get("transcript_path"))
    if derived and derived.startswith("claw4mac-"):
        derived = derived[len("claw4mac-"):]
    return derived or "unknown"


def _current_origin():
    """"bench" when SUPERJEV_BENCH=1 in the environment, or the active hook
    payload's session_id starts with "bench-"; "live" otherwise (including
    every ordinary hook firing from a real Claude Code session, and every
    manual CLI invocation with no bench markers)."""
    if os.environ.get("SUPERJEV_BENCH") == "1":
        return "bench"
    payload = _ACTIVE_HOOK_PAYLOAD or {}
    session_id = payload.get("session_id")
    if isinstance(session_id, str) and session_id.startswith("bench-"):
        return "bench"
    return "live"


def ledger_append(entry):
    """Append one JSONL line to the call ledger. Never raises — a ledger
    problem must never break a door — but an unwritable ledger is not
    swallowed in total silence: one line goes to stderr so a broken ledger
    path is discoverable instead of invisibly dropping every record.

    Every entry gets a short unique `id` (if it does not already carry
    one) — `feedback --ledger-id` and the calibration export both address
    a ledger line by this. Every entry also gets `bot` and `origin` (if
    not already carrying them) — see _current_bot_id/_current_origin."""
    entry.setdefault("id", uuid.uuid4().hex[:12])
    entry.setdefault("bot", _current_bot_id())
    entry.setdefault("origin", _current_origin())
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


# --------------------------------------------------------------- catch ledger
#
# The catch ledger is a second, separate JSONL file from the call ledger
# above. The call ledger is "what ran, how long, what exit code" — every
# door invocation. The catch ledger is narrower and purpose-built: one
# record per gate/verify hook DECISION (allow/block/advisory/unchecked),
# small enough that Kelvin can tag each one fair/false/miss by hand and
# read the three-number scoreboard `catch report` prints. See docs/hooks.md,
# "The catch ledger".

def _default_catch_ledger_path():
    """SUPERJEV_CATCH_LEDGER, if set, else alongside the call ledger, as
    catches.jsonl — same directory SUPERJEV_LEDGER resolves to (or this
    skill's own ledger/ dir when neither is set)."""
    override = os.environ.get("SUPERJEV_CATCH_LEDGER")
    if override:
        return Path(override).expanduser()
    return LEDGER_PATH.parent / "catches.jsonl"


CATCH_LEDGER_PATH = _default_catch_ledger_path()


def catch_ledger_append(entry):
    """Append one JSONL line to the catch ledger. Never raises — same
    contract as ledger_append. Critically, this is only ever called AFTER
    the real gate/verify decision (exit code, block/allow/advisory) is
    already final and returned by the caller: a broken or unwritable catch
    ledger path must never change what a hook does, only whether this
    optional record of it gets written. A write failure prints one line to
    stderr rather than raising or failing silently — this catches any
    Exception, not just OSError (a bad entry that json.dumps chokes on, a
    permissions error, anything at all), because this call sits strictly
    after a real decision has already been returned and must never
    propagate. Gets `bot` and `origin` the same way ledger_append does, if
    not already carrying them."""
    entry.setdefault("id", uuid.uuid4().hex[:10])
    entry.setdefault("bot", _current_bot_id())
    entry.setdefault("origin", _current_origin())
    try:
        CATCH_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CATCH_LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"super-jev: could not write catch ledger at {CATCH_LEDGER_PATH}: {exc}",
              file=sys.stderr)
    return entry["id"]


def _catch_excerpt(text):
    """The catch ledger's excerpt: the input is first sliced to 4096 chars
    (bounding how much text _catch_redact ever has to scan), THEN redacted
    through _catch_redact — secrets/credentials, emails, US phone numbers,
    SSN-shaped digit strings, and card numbers (an ungrouped 13+ digit run,
    or a grouped 4-4-4-4/4-6-5 run using one consistent separator, each
    with its own narrow exception — see _catch_redact and docs/hooks.md) —
    and finally sliced to the 240 chars actually kept. No secret,
    credential, email, phone number, SSN-shaped string or card number ever
    lands in the catch ledger, because this excerpt is the only piece of
    the original text the ledger keeps at all."""
    if not text:
        return ""
    return _catch_redact(str(text)[:4096])[:240]


def catch_log(door, decision, reasons=None, draft_text=None, window_bytes=None,
              start_time=None, payload=None):
    """One record for the catch ledger — called once per gate/verify hook
    decision. `decision` is one of "block", "allow", "advisory", "unchecked",
    "advisory-forced" (the stop_hook_active re-run failsafe), or
    "advisory-judge" (the SUPERJEV_GATE_JUDGE_ADVISORY=1 failsafe — see
    docs/hooks.md). `reasons` is the same strings --explain shows for
    this run (block_reasons/block_notes), never the raw evidence. `ms` is
    left as None (not guessed) when `start_time` was not captured.

    Wrapped in try/except on purpose: a bug in this function must never
    surface as a change to the hook's own return value, because every call
    site here runs strictly after that value is already decided.
    `payload`, if given and SUPERJEV_CATCH_KEEP_PAYLOAD=1, is saved
    (redacted) alongside this record's id for later bench-case export —
    see _catch_save_payload."""
    try:
        ms = None
        if start_time is not None:
            ms = int((time.monotonic() - start_time) * 1000)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "door": door,
            "decision": decision,
            "reasons": list(reasons or []),
            "draft_excerpt": _catch_excerpt(draft_text),
            "window_bytes": window_bytes,
            "ms": ms,
            "tag": None,
            "note": None,
        }
        catch_id = catch_ledger_append(entry)
        if payload is not None:
            _catch_save_payload(catch_id, payload)
        return catch_id
    except Exception:
        return None


def _catch_save_payload(catch_id, payload):
    """Opt-in only (SUPERJEV_CATCH_KEEP_PAYLOAD=1): the redacted hook
    payload for THIS decision, saved under <catch ledger dir>/payloads/
    <id>.json. Off by default because the catch ledger's whole point is
    that it never writes the full window; this is the explicit exception a
    human turned on, meant to make later bench-case export possible for a
    tagged false stop or miss. Never raises, never required for the ledger
    line above to succeed — a payload save failure is silent by design
    (the ledger line is the record that matters; the payload is a bonus)."""
    if os.environ.get("SUPERJEV_CATCH_KEEP_PAYLOAD") != "1":
        return None
    try:
        out_dir = CATCH_LEDGER_PATH.parent / "payloads"
        out_dir.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        redacted = _catch_redact(raw)
        out_path = out_dir / f"{catch_id}.json"
        out_path.write_text(redacted, encoding="utf-8")
        return str(out_path)
    except Exception:
        return None


def _catch_cases_path():
    """SUPERJEV_CATCH_CASES, if set, else <catch ledger dir>/catch-cases.json
    — ONE JSON array file that `catch tag` keeps in sync with the catch
    ledger's own tags (see _upsert_catch_case). NOT the old per-id
    bench-case-file shape (that never fit this data — see
    _build_catch_case's docstring for why), and NOT the old default
    directory name (bench-cases/); the default is a single file,
    catch-cases.json, next to the catch ledger."""
    override = os.environ.get("SUPERJEV_CATCH_CASES")
    if override:
        return Path(override).expanduser()
    return CATCH_LEDGER_PATH.parent / "catch-cases.json"


def _catch_lock_path():
    return CATCH_LEDGER_PATH.parent / (CATCH_LEDGER_PATH.name + ".lock")


class _CatchLockRefused(Exception):
    """Internal signal only: _catch_lock could not even open its lock file
    (see below). Caught by _cmd_catch_tag and turned into a normal
    `return REFUSED`, same as every other catch-tag refusal — never lets
    an OSError (or this exception) reach the caller as a traceback."""


@contextlib.contextmanager
def _catch_lock():
    """Exclusive lock held across ONE `catch tag` command's whole
    read-modify-write — both the catch-ledger rewrite (_rewrite_catch_records)
    and the catch-cases array upsert (_upsert_catch_case) — so two `catch
    tag` commands racing on different ids never silently drop each other's
    write (see B3). A sibling `.lock` file, never the ledger file itself,
    so holding this open never interferes with the ledger's own
    os.replace-based atomic rewrite. fcntl.flock blocks (waits) rather than
    failing, so a second `catch tag` simply waits its turn instead of
    losing its write; the lock is released (and the fd closed) even if the
    body raises.

    mkdir/open here can raise OSError — a read-only or missing catch-ledger
    directory, most commonly — and that must be a clean one-line refusal,
    not a traceback. Caught and printed here, then re-raised as
    _CatchLockRefused so _cmd_catch_tag can turn it into an ordinary
    `return REFUSED` (exit 5), the same shape every other catch-tag
    refusal already uses."""
    lock_path = _catch_lock_path()
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(lock_path, "a+")
    except OSError as exc:
        print(f"super-jev: catch tag: could not open lock file {lock_path}: {exc}",
              file=sys.stderr)
        raise _CatchLockRefused() from exc
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fd.close()


def _catch_payload_path(rec_id):
    return CATCH_LEDGER_PATH.parent / "payloads" / f"{rec_id}.json"


def _load_full_payload_draft(rec_id):
    """If SUPERJEV_CATCH_KEEP_PAYLOAD was on when this record was made, the
    saved (redacted) hook payload may carry the full draft/report text
    under one of a few keys a real gate/verify hook payload uses. Returns
    None (never raises) when there is no payload copy, it cannot be read
    or parsed, or none of the known keys carried a non-empty string — the
    caller then falls back to the catch ledger's own 240-char excerpt,
    which is all that was ever kept in that case."""
    payload_path = _catch_payload_path(rec_id)
    if not payload_path.exists():
        return None
    try:
        data = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("last_assistant_message", "draft", "report", "text", "prompt"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _build_catch_case(record):
    """Build ONE "catch case" dict (not written anywhere yet — see
    _upsert_catch_case) for a catch-ledger record tagged "false" (a block
    that was wrong — the draft was actually true) or "miss" (an allow that
    let a lie through).

    This is deliberately NOT a "bench case" in the gate-bench sense, and
    is never claimed to be one. A catch record has no transcript anchor —
    no source_offset/source_idx/transcript_path/bot — so it can never
    satisfy replay_gate_bench.py or replay_fact_block_sweep.py, which both
    read one JSON array of cases shaped that way. Use
    skills/super-jev/tests/replay_catch_cases.py instead, which reads
    THIS file's own shape and, for any case that carries a payload_path,
    can re-run the original gate/verify decision offline through
    SUPERJEV_GATE_CMD/SUPERJEV_VERIFY_CMD.

    `draft` here is the FULL draft/report text when a payload copy exists
    (SUPERJEV_CATCH_KEEP_PAYLOAD was on at decision time — see
    _load_full_payload_draft) — otherwise it falls back to the catch
    ledger's own 240-char redacted excerpt, which is all that was ever
    kept. Either way this is the same catch-ledger redaction (see
    _catch_redact), never the raw original text."""
    tag = record.get("tag")
    # false = a block was wrong, i.e. the blocked draft was actually TRUE.
    # miss = an allow let a lie through, i.e. the allowed draft was a LIE.
    kind = "truth" if tag == "false" else "lie"
    rec_id = record.get("id", "")
    payload_path = _catch_payload_path(rec_id)
    full_draft = _load_full_payload_draft(rec_id)
    draft = _catch_redact(full_draft) if full_draft is not None else record.get(
        "draft_excerpt", "")
    return {
        "id": rec_id,
        "ts": record.get("ts"),
        "door": record.get("door"),
        "kind": kind,
        "draft": draft,
        "payload_path": str(payload_path) if payload_path.exists() else None,
        "reasons": record.get("reasons") or [],
        "note": record.get("note"),
    }


def _catch_cases_write(cases):
    """Atomic overwrite of the whole catch-cases array file. Must be
    called with the catch lock already held (see _catch_lock) whenever the
    caller also touched the catch ledger in the same command, so the two
    files never observe a partial update from a concurrent `catch tag`.

    Sorted by `ts` (ISO 8601, so this is also chronological string order;
    a missing/unparseable ts sorts first via "") before writing — N1: a
    re-tag removes and re-appends a case (see _upsert_catch_case), which
    without this would silently move it to the end of the file, reordering
    every case that already existed just because one of them got
    re-tagged."""
    cases = sorted(cases, key=lambda c: c.get("ts") or "")
    cases_path = _catch_cases_path()
    try:
        cases_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cases_path.with_suffix(cases_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        os.replace(tmp_path, cases_path)
        return str(cases_path)
    except OSError as exc:
        print(f"super-jev: could not write catch cases at {cases_path}: {exc}",
              file=sys.stderr)
        return None


def _upsert_catch_case(rec_id, new_case):
    """At most one catch case per record id (see B2): replaces any
    existing case for `rec_id` with `new_case`, or — when `new_case` is
    None — removes that id's case without adding one (a re-tag to "fair"
    withdraws a false/miss case that had been written for the same id).
    Reads the cases file fresh, so this must be called with the catch lock
    already held: an unlocked read-modify-write here is exactly B3's
    race."""
    cases_path = _catch_cases_path()
    try:
        existing = json.loads(cases_path.read_text(encoding="utf-8"))
        if not isinstance(existing, list):
            existing = []
    except (OSError, ValueError):
        existing = []
    existing = [c for c in existing if c.get("id") != rec_id]
    if new_case is not None:
        existing.append(new_case)
    return _catch_cases_write(existing)


def _catch_lines():
    try:
        text = CATCH_LEDGER_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln for ln in text.splitlines() if ln.strip()]


def _catch_records():
    out = []
    for line in _catch_lines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _parse_since(spec):
    """"24h" / "7d" / "30m" -> a timedelta, or None if unparseable (caller
    then applies no time filter rather than guessing)."""
    if not spec:
        return None
    m = re.match(r"^(\d+)\s*([mhd])$", str(spec).strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    return timedelta(days=n)


def _catch_ts(rec):
    try:
        return datetime.fromisoformat(str(rec.get("ts", "")).replace("Z", "+00:00"))
    except ValueError:
        return None


def _catch_filter_since(records, since_spec):
    """Returns (kept, undated_count). `since_spec` of None/"" means no
    filter at all — every record is kept and undated_count is 0. When a
    real window is applied, a record with a missing or unparseable `ts` is
    excluded from that window (never silently included, as if it were
    always "recent") and counted separately in undated_count instead — the
    caller reports that count on its own line rather than folding it into
    either side of the window.

    Callers must validate `since_spec` themselves before calling this (see
    `_parse_since`) — an unparseable-but-non-empty spec is a caller error,
    reported as exit 2, not silently treated as "no filter" here."""
    delta = _parse_since(since_spec)
    if delta is None:
        return records, 0
    cutoff = datetime.now(timezone.utc) - delta
    out = []
    undated = 0
    for rec in records:
        ts = _catch_ts(rec)
        if ts is None:
            undated += 1
            continue
        if ts >= cutoff:
            out.append(rec)
    return out, undated


def _rewrite_catch_records(records):
    """Rewrite the whole catch ledger file from a list of records — used
    only by `catch tag`, which mutates one existing line in place. Never
    raises; on failure prints to stderr and leaves the file as it was."""
    try:
        CATCH_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CATCH_LEDGER_PATH.with_suffix(CATCH_LEDGER_PATH.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp_path, CATCH_LEDGER_PATH)
        return True
    except OSError as exc:
        print(f"super-jev: could not update catch ledger at {CATCH_LEDGER_PATH}: {exc}",
              file=sys.stderr)
        return False


def cmd_catch(a):
    action = getattr(a, "catch_action", None)
    if action == "list":
        return _cmd_catch_list(a)
    if action == "tag":
        return _cmd_catch_tag(a)
    if action == "report":
        return _cmd_catch_report(a)
    return refuse("catch: no action — use list, tag or report")


def _catch_refuse3(line):
    """Same shape as refuse(), but exit 3 — the shared exit code for every
    `catch` refusal that is NOT a plain usage error and NOT the generic
    REFUSED (5): `catch tag`'s tag-vs-decision contradiction refusal
    (N6 — a tag that contradicts the record it names, e.g. "false" on a
    record whose decision was "allow"), and `catch list`'s/`catch
    report`'s shared --since validation refusal (N4 — an unparseable
    --since value). Both used to exit differently (2 for --since, 3 for
    the tag contradiction) even though neither is an argparse usage error
    (argparse already accepted the arguments fine in both cases) and
    neither is the generic "missing input/door" refusal(5) — exit 2 is
    argparse's own usage-error convention, so overloading it here made a
    caller unable to tell "bad flag" apart from "refused for a domain
    reason" by exit code alone. N4 moved --since onto this same code so
    both refusal families now share one, unambiguous, non-usage-error
    exit."""
    print(f"super-jev: {line}", file=sys.stderr)
    return 3


def _cmd_catch_list(a):
    since = getattr(a, "since", None)
    if since and _parse_since(since) is None:
        return _catch_refuse3(f"catch list: --since {since!r} is not a valid duration "
                              "(e.g. 24h, 7d, 30m) — refusing rather than silently "
                              "showing all time")
    records = _catch_records()
    records, undated = _catch_filter_since(records, since)
    if getattr(a, "untagged", False):
        records = [r for r in records if r.get("tag") is None]
    bot_filter = getattr(a, "bot", None)
    if bot_filter:
        records = [r for r in records if r.get("bot") == bot_filter]
    if not records:
        print("catch list: no records")
    else:
        # Width sized to the longest bot id actually being printed this call
        # (floored at len("unknown")) rather than a fixed pad — a fixed pad
        # narrower than a real seat name (e.g. "contentcreator") let that
        # row's bot field run into the reason column with no gap.
        bot_width = max([len(str(rec.get("bot") or "unknown")) for rec in records]
                        + [len("unknown")])
        for rec in records:
            reasons = rec.get("reasons") or []
            first_reason = reasons[0] if reasons else ""
            tag = rec.get("tag") or "-"
            bot = rec.get("bot") or "unknown"
            print(f"{rec.get('id','?')}  {rec.get('ts','?')}  {rec.get('door','?'):8s}  "
                  f"{rec.get('decision','?'):10s}  tag={tag:6s}  bot={bot:<{bot_width}s}  {first_reason}")
    if since and undated:
        print(f"catch list: {undated} undated record(s) excluded from the --since window")
    return 0


# Which recorded `decision` a given tag value is allowed to land on — a
# tag that contradicts the record it names is refused rather than silently
# accepted (see _cmd_catch_tag): "false"/"fair" only make sense against a
# real or forced block (the thing being judged right or wrong IS a block),
# "miss" only against a decision that let the reply through unblocked.
_TAG_ALLOWED_DECISIONS = {
    "fair": ("block", "advisory-forced", "advisory-judge"),
    "false": ("block", "advisory-forced", "advisory-judge"),
    "miss": ("allow", "advisory", "unchecked"),
}


def _cmd_catch_tag(a):
    catch_id = a.id
    value = a.value
    note = getattr(a, "note", "") or ""
    if value not in ("fair", "false", "miss"):
        return refuse(f"catch tag: {value!r} — use fair, false or miss")
    # The whole read-modify-write — ledger AND catch-cases array — runs
    # under one lock (see B3): records are re-read fresh here, inside the
    # lock, not reused from some earlier read, so two `catch tag` commands
    # racing on different ids never clobber each other's write.
    try:
        return _cmd_catch_tag_locked(catch_id, value, note)
    except _CatchLockRefused:
        return REFUSED


def _cmd_catch_tag_locked(catch_id, value, note):
    with _catch_lock():
        records = _catch_records()
        matched = None
        for rec in records:
            if rec.get("id") == catch_id:
                matched = rec
                break
        if matched is None:
            return refuse(f"catch tag: no catch-ledger record with id {catch_id!r}")
        decision = matched.get("decision")
        allowed = _TAG_ALLOWED_DECISIONS[value]
        if decision not in allowed:
            return _catch_refuse3(
                f"catch tag: {value!r} does not fit a {decision!r} record ({catch_id}) — "
                f"{value!r} only fits: {'/'.join(allowed)}")
        matched["tag"] = value
        matched["note"] = note
        if not _rewrite_catch_records(records):
            return refuse(f"catch tag: could not persist the tag for {catch_id!r}")
        case_path = None
        if value in ("false", "miss"):
            # At most one catch case per record id (B2): a re-tag from
            # false to miss (or vice versa) replaces the earlier case for
            # this id rather than appending a duplicate.
            case_path = _upsert_catch_case(catch_id, _build_catch_case(matched))
        else:
            # Tagging fair withdraws any case an earlier false/miss tag on
            # this same id had written (B2) — a case whose tag no longer
            # says "wrong" or "missed" has no business staying in the
            # catch-cases file.
            _upsert_catch_case(catch_id, None)
    print(f"catch tag: {catch_id} -> {value}" +
          (f" (catch case: {case_path})" if case_path else ""))
    return 0


def _cmd_catch_report(a):
    since = getattr(a, "since", None)
    if since and _parse_since(since) is None:
        return _catch_refuse3(f"catch report: --since {since!r} is not a valid duration "
                              "(e.g. 24h, 7d, 30m) — refusing rather than silently "
                              "showing all time")
    records = _catch_records()
    records, undated = _catch_filter_since(records, since)
    bot_filter = getattr(a, "bot", None)
    if bot_filter:
        records = [r for r in records if r.get("bot") == bot_filter]
    fair = sum(1 for r in records if r.get("tag") == "fair")
    false = sum(1 for r in records if r.get("tag") == "false")
    miss = sum(1 for r in records if r.get("tag") == "miss")
    untagged = sum(1 for r in records if r.get("tag") is None)
    # "advisory-forced" is a would-have-blocked stop_hook_active second pass
    # (see docs/hooks.md) — reported here as its own line, "blocks
    # suppressed", never folded into "false stops" or "misses" by ITSELF,
    # because being demoted to advisory is neither: nothing was judged
    # right or wrong yet, a real block was just held back by the retry.
    # Once a human tags that same record fair or false, though, it also
    # lands on that tag's own line above (see N5) — the two lines are
    # answering different questions ("was a block held back?" vs "was the
    # underlying call right?") and a record can honestly answer both, so
    # this is not double-counting a single question, but the raw sum of
    # the lines above can still look larger than the record count without
    # this footnote explaining why.
    suppressed = sum(1 for r in records if r.get("decision") == "advisory-forced")
    suppressed_and_tagged = sum(1 for r in records if r.get("decision") == "advisory-forced"
                                and r.get("tag") in ("fair", "false"))
    # "advisory-judge" is the SUPERJEV_GATE_JUDGE_ADVISORY=1 twin of
    # "advisory-forced": a real block whose only reasons came from the judge
    # (OVERCLAIMS, or under SUPERJEV_RULE=v2 the secondary NOT_SUPPORTED/
    # CONTRADICTED arm) got demoted to advisory rather than a stop_hook_active
    # re-run. Reported on its own line rather than folded into "blocks
    # suppressed" so the scoreboard can tell the two failsafes apart — one
    # fires on a loop re-run, this one fires on every matching turn.
    judge_advisories = sum(1 for r in records if r.get("decision") == "advisory-judge")
    print(f"fair catches: {fair}")
    print(f"false stops: {false}")
    print(f"misses: {miss}")
    print(f"untagged: {untagged}")
    print(f"blocks suppressed: {suppressed}")
    print(f"judge advisories: {judge_advisories}")
    if suppressed_and_tagged:
        print(f"  ({suppressed_and_tagged} of the above blocks-suppressed record(s) is "
              "also tagged fair/false and counted on that line too — suppressed and "
              "fair/false answer different questions, see docs/hooks.md)")
    if since:
        print(f"undated: {undated}")
    if not bot_filter:
        by_bot = Counter(r.get("bot") or "unknown" for r in records)
        if by_bot:
            print("by bot:")
            for bot, n in sorted(by_bot.items(), key=lambda kv: (-kv[1], kv[0])):
                print(f"  {bot}: {n}")
    return 0


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


def _gate_budget_s():
    """Wall-clock seconds one Stop event's checks may take in total
    (SUPERJEV_GATE_BUDGET_S, default 15). Zero or negative means no
    budget at all — the pre-2026-09-18 behaviour, kept reachable so a
    bench run can measure the unbounded path on purpose."""
    try:
        v = float(os.environ.get(GATE_BUDGET_S_ENV, DEFAULT_GATE_BUDGET_S))
    except (TypeError, ValueError):
        return DEFAULT_GATE_BUDGET_S
    return v


def _gate_max_calls():
    """Live judge calls one Stop event may spend (SUPERJEV_GATE_MAX_CALLS,
    default 1). The gate's own verdict claims it first."""
    try:
        n = int(os.environ.get(GATE_MAX_CALLS_ENV, DEFAULT_GATE_MAX_CALLS))
    except (TypeError, ValueError):
        return DEFAULT_GATE_MAX_CALLS
    return n if n >= 0 else DEFAULT_GATE_MAX_CALLS


class StopBudget:
    """One Stop event's wall-clock and call allowance, shared by the gate
    verdict and the advisory teammate-report scan.

    Two separate limits, because they fail differently. `calls` is what
    keeps a single Stop event to ONE judge call: the gate claims it, and
    the advisory scan then finds none left and defers its reports to the
    next Stop (which is what it already does on a timeout, so nothing is
    dropped). `deadline` is what keeps the event bounded even when a
    single check runs long — the input size of a verify check is not
    knowable before it is made (see the header note), so wall-clock is the
    only honest control.

    `remaining()` is seconds left, or None when no budget is configured.
    `expired()` is True once that hits zero. `claim_call()` takes one call
    off the allowance and returns whether it was there to take."""

    def __init__(self, budget_s=None, max_calls=None):
        self.budget_s = _gate_budget_s() if budget_s is None else budget_s
        self.calls_left = _gate_max_calls() if max_calls is None else max_calls
        self.started = time.monotonic()
        self.deadline = (self.started + self.budget_s) if self.budget_s > 0 else None

    def remaining(self):
        if self.deadline is None:
            return None
        return self.deadline - time.monotonic()

    def elapsed(self):
        return time.monotonic() - self.started

    def expired(self):
        rem = self.remaining()
        return rem is not None and rem <= 0

    def claim_call(self, reserve=0):
        """Take one call off the allowance; False when there is none to
        take. `reserve` is how many calls the caller must leave behind for
        someone else — the advisory scan passes 1, because the gate's own
        verdict has first claim on this event and must never find the
        allowance already spent by a side-channel check."""
        if self.calls_left is None:
            return True
        if self.calls_left - reserve <= 0:
            return False
        self.calls_left -= 1
        return True

    def timeout_for(self, default_timeout):
        """`default_timeout`, clamped to what is left of the budget — a
        child call must never be allowed to outlive the event it is part
        of. Returns the plain default when no budget is configured."""
        rem = self.remaining()
        if rem is None:
            return default_timeout
        return max(min(default_timeout, rem), 0.0)


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
    # An explicit `timeout` on the namespace is the Stop hook's own
    # wall-clock budget (see StopBudget.timeout_for) — a child call must
    # never outlive the event it is part of, so the smaller wins.
    timeout = getattr(a, "timeout", None)
    timeout = _gate_timeout() if timeout is None else min(timeout, _gate_timeout())
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


# ------------------------------------------------- derived-facts fallback
#
# worker-verify (FLEET_VERIFY_PY) is a fleet-local install; on a fresh clone
# with no SUPERJEV_VERIFY_CMD it is simply absent, and `verify` used to just
# refuse. That is honest but throws away the one part of the pattern that
# needs no external tool at all: reading git/test/gh output in CODE instead
# of by eye. This fallback runs ONLY when the real door is unreachable. It
# gathers a small, best-effort evidence set itself (never as thorough as
# worker-verify's own atom extraction), hands it to the pure
# src/enhance/derive-facts.ts pair over its CLI, and prints the DERIVED FACTS
# block plus the pre-rule verdicts it settles for free. It never calls a
# judge — there is no evidence gathered here worth paying for a model call
# over — so it can say CONTRADICTED_BY_FACT with confidence 1.00, but it can
# never say CLEAN. A report with no settled contradiction is READ: unverified,
# not vouched for.
_PATH_RE = re.compile(r'(?<![\w@:])((?:~/|\./|/)?(?:[\w.\-]+/){1,8}[\w.\-]+)')
_HASH_RE = re.compile(r'\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)([0-9a-f]{7,40})\b')
_BRANCH_RE = re.compile(r'\b((?:feat|feature|fix|proto|chore|docs|bench|test|refactor|perf|'
                        r'release|hotfix|wip|exp)/[\w.\-/]+)')


def _light_atoms(text):
    """A cut-down version of worker-verify's atom extraction: just enough to
    find candidate paths, commit hashes and branch names in a report, for a
    fallback that has no jev.py to lean on. Not a replacement for the real
    thing — see worker-verify's extract_atoms for the thorough version."""
    paths, hashes, branches = [], [], []
    for m in _PATH_RE.finditer(text):
        p = m.group(1).rstrip('.,;:!?)')
        if p and p not in paths:
            paths.append(p)
    for m in _HASH_RE.finditer(text):
        h = m.group(1)
        if h not in hashes:
            hashes.append(h)
    for m in _BRANCH_RE.finditer(text):
        b = m.group(1).rstrip('.,;:!?)')
        if b not in branches:
            branches.append(b)
    # A branch is not also a path.
    paths = [p for p in paths if p not in branches]
    return paths[:20], hashes[:20], branches[:20]


def _git_out(args, cwd):
    try:
        p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                           text=True, timeout=20)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


# A PR number named in the report, the fallback's only cue that there is any
# PR evidence worth gathering at all — "PR #N" or a `/pull/N` URL segment
# (see _PR_NUM_RE further down for the sibling used by the hook's --pr /
# --from-file path; this one is deliberately self-contained so this early
# section of the file has no forward dependency on it).
_PR_NUM_FALLBACK_RE = re.compile(r'PR\s*#(\d+)|\bpull/(\d+)\b', re.IGNORECASE)
_GH_PR_TIMEOUT = 10


def _run_gh(args, cwd, commands_log):
    """One `gh` call for the verify fallback's PR evidence: never
    interactive (GH_PROMPT_DISABLED=1, stdin closed), a 10s timeout, and
    never raises. Logged into `commands_log` regardless of outcome, so
    `--explain` can print exactly what ran even when a call failed or timed
    out. Returns (ok, stdout)."""
    cmd = ["gh", *args]
    commands_log.append(" ".join(cmd))
    env = dict(os.environ)
    env["GH_PROMPT_DISABLED"] = "1"
    try:
        p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                           text=True, timeout=_GH_PR_TIMEOUT, env=env,
                           stdin=subprocess.DEVNULL)
        return p.returncode == 0, (p.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def _gh_pr_evidence(report_text, worktree, commands_log):
    """Best-effort PR state + checks off `gh`, for the verify fallback's
    derived-facts pass — the fallback's only source of PR truth, since it
    has no worker-verify door to run `gh pr view` for it. Returns one
    `evidence.prs[]` entry (the shape src/enhance/derive-facts.ts's
    `Evidence.prs` already expects) or None. Runs both `gh pr view N --json
    state,mergedAt,headRefName,baseRefName,statusCheckRollup` and `gh pr
    checks N` — the checks command is the primary source for the checks
    list (it is the command the report's claim is actually judged against),
    `statusCheckRollup` from the view call is only a fallback for when the
    checks command itself returns nothing parseable. Every call is appended
    to `commands_log` whether or not it succeeded. Never raises and never
    guesses: no `gh` on PATH, no PR number named in the report, a timeout,
    or unparseable JSON all fall through to None (no fact, no rule) rather
    than fabricating a state."""
    if not shutil.which("gh"):
        return None
    m = _PR_NUM_FALLBACK_RE.search(report_text or "")
    if not m:
        return None
    pr_num = int(m.group(1) or m.group(2))
    cwd = Path(worktree).expanduser() if worktree else None

    state = None
    rollup_checks = []
    ok, out = _run_gh(
        ["pr", "view", str(pr_num), "--json",
         "state,mergedAt,headRefName,baseRefName,statusCheckRollup"],
        cwd, commands_log)
    if ok and out.strip():
        try:
            data = json.loads(out)
        except ValueError:
            data = None
        if isinstance(data, dict):
            state = {
                "state": data.get("state") or "UNKNOWN",
                "isDraft": bool(data.get("isDraft", False)),
                "headRefName": data.get("headRefName"),
                "mergedAt": data.get("mergedAt"),
            }
            for c in (data.get("statusCheckRollup") or []):
                if not isinstance(c, dict):
                    continue
                name = c.get("name") or c.get("context") or "check"
                st = c.get("conclusion") or c.get("state") or "UNKNOWN"
                rollup_checks.append({"name": name, "state": st})

    checks = []
    ok2, out2 = _run_gh(["pr", "checks", str(pr_num)], cwd, commands_log)
    if ok2 and out2.strip():
        # Plain `gh pr checks` output is one check per line, tab-separated
        # (name, state, elapsed, url).
        for line in out2.splitlines():
            parts = [p for p in line.split("\t") if p != ""]
            if len(parts) >= 2:
                checks.append({"name": parts[0].strip(), "state": parts[1].strip()})
    if not checks:
        checks = rollup_checks

    if state is None and not checks:
        return None
    entry = {"number": pr_num}
    if state is not None:
        entry["state"] = state
    if checks:
        entry["checks"] = checks
    return entry


def _gather_local_evidence(report_text, worktree, test_cmd, commands_log=None, guard=None):
    """Best-effort git/test/PR evidence, gathered read-only, in the shape
    src/enhance/derive-facts.ts's `Evidence` type expects. Never raises;
    a block this cannot gather is simply left out, same contract as
    worker-verify's own "not gathered" blocks. `commands_log`, if passed,
    collects every `gh` command this run attempted (see _gh_pr_evidence),
    for `--explain`. `guard`, if passed, is a GuardTally: every path this
    gatherer would open is checked against is_blocked_path first (a
    blocked path is never opened; it gets blocked_path_fact instead, so
    it reads UNPROVABLE, never FALSE), and the test command's captured
    output is redacted before it enters the evidence pack — this is the
    evidence-guard.ts / atoms.py blocklist+redactor's Python mirror, wired
    into the one gatherer worker-verify's door-absent fallback uses."""
    evidence = {}
    guard = guard if guard is not None else GuardTally()
    paths, hashes, branches = _light_atoms(report_text)

    if worktree:
        wt = Path(worktree).expanduser()
        ok, branch_out = _git_out(["rev-parse", "--abbrev-ref", "HEAD"], wt)
        if ok:
            branch = branch_out.strip()
            ok2, _up = _git_out(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], wt)
            ahead = behind = 0
            if ok2:
                ok3, counts = _git_out(["rev-list", "--left-right", "--count", "HEAD...@{u}"], wt)
                if ok3:
                    parts = counts.split()
                    if len(parts) == 2:
                        try:
                            ahead, behind = int(parts[0]), int(parts[1])
                        except ValueError:
                            pass
            evidence["pushState"] = {"branch": branch, "hasUpstream": ok2,
                                     "ahead": ahead, "behind": behind}

        if paths:
            tracked = []
            lengths = []
            for p in paths:
                full = p if os.path.isabs(p) else (wt / p)
                full = Path(full)
                if guard.check_path(str(full)) or guard.check_path(p):
                    lengths.append(blocked_path_fact(p))
                    continue
                exists = full.exists() and full.is_file()
                ok_tr, _ = _git_out(["ls-files", "--error-unmatch", p], wt)
                tracked.append({"path": p, "existsOnDisk": full.exists(), "tracked": ok_tr})
                if exists:
                    try:
                        with open(full, "r", encoding="utf-8", errors="replace") as fh:
                            n = sum(1 for _ in fh)
                    except OSError:
                        n = None
                    lengths.append({"path": p, "exists": True, "lines": n})
                else:
                    lengths.append({"path": p, "exists": False})
            evidence["tracked"] = tracked
            evidence["lengths"] = lengths

        ok_d, diff_out = _git_out(["diff", "--shortstat", "origin/main...HEAD"], wt)
        if ok_d and diff_out.strip():
            m = re.search(r'(\d+) files? changed(?:, (\d+) insertions?\(\+\))?'
                          r'(?:, (\d+) deletions?\(-\))?', diff_out)
            if m:
                evidence["diffstat"] = {
                    "base": "origin/main", "head": "HEAD",
                    "filesChanged": int(m.group(1) or 0),
                    "insertions": int(m.group(2) or 0),
                    "deletions": int(m.group(3) or 0),
                }

        if hashes:
            commits = []
            for h in hashes:
                ok_c, _ = _git_out(["cat-file", "-e", h], wt)
                commits.append({"hash": h, "branch": "HEAD", "inLog": ok_c})
            evidence["commits"] = commits

        if branches:
            branch_facts = []
            for b in branches:
                ok_b, listing = _git_out(["branch", "-a", "--list", b], wt)
                branch_facts.append({"name": b, "exists": bool(ok_b and listing.strip())})
            evidence["branches"] = branch_facts

    if test_cmd:
        bad = check_test_cmd_for_fallback(test_cmd, worktree)
        if bad is None:
            try:
                p = subprocess.run(shlex.split(test_cmd), cwd=worktree or None,
                                   capture_output=True, text=True, timeout=300)
                out = (p.stdout or "") + (p.stderr or "")
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                out = f"TEST COMMAND FAILED TO RUN: {exc}"
            evidence["tests"] = {"command": test_cmd, "output": guard.redact(out)}
        else:
            evidence["tests"] = {"command": test_cmd, "output": f"REFUSED: {bad}"}

    # PR state/checks off `gh` — gated on a PR number actually being named
    # in the report AND `gh` being on PATH; neither present means no fact,
    # no rule, same "silence over a guess" contract as every block above.
    pr_entry = _gh_pr_evidence(report_text, worktree, commands_log if commands_log is not None else [])
    if pr_entry is not None:
        evidence["prs"] = [pr_entry]

    return evidence


def check_test_cmd_for_fallback(cmd, worktree):
    """The same directory-level-pytest refusal worker-verify enforces
    (see verify.py's check_test_cmd), kept small here because this fallback
    has no import of verify.py to reuse — the whole point of this path is
    that verify.py is ABSENT. Returns a refusal string, or None if fine."""
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return f"not parseable as a shell command: {cmd}"
    if not toks:
        return "empty test command"
    is_pytest = toks[0] == "pytest" or ("pytest" in toks and toks[0] in ("python", "python3"))
    if not is_pytest:
        return None
    named = [t for t in toks[1:] if t.endswith(".py") or "::" in t]
    if named:
        return None
    return ("a directory-level pytest can launch an app or hit a live system; "
            "name the test file instead")


def _resolve_node():
    return shutil.which("node")


def _derived_facts_fallback(report_text, worktree, test_cmd, explain=False):
    """Run the derive-facts/pre-rules pair through its CLI over locally
    gathered evidence, and return (block_text, verdicts, code) — or None if
    this fallback itself cannot run (no node, no CLI file, or nothing at all
    to gather). `code` is 4 (REJECT) when a pre-rule settles at least one
    claim as CONTRADICTED_BY_FACT, else 3 (READ) — this path never returns 0
    (CLEAN); it has no judge, so an unsettled claim stays unverified, never
    vouched for. `explain=True` appends the list of `gh` commands this run
    attempted (see _gh_pr_evidence) to the block, empty when none ran
    (no PR named in the report, or no `gh` on PATH)."""
    node = _resolve_node()
    if not node or not DERIVE_FACTS_CLI.exists():
        return None
    if not worktree and not test_cmd:
        return None
    gh_commands = []
    guard = GuardTally()
    evidence = _gather_local_evidence(report_text, worktree, test_cmd, commands_log=gh_commands, guard=guard)
    if not evidence:
        return None
    claims = presplit_claims(report_text) or [report_text.strip()]
    payload = json.dumps({"evidence": evidence, "claims": claims})
    try:
        p = subprocess.run([node, str(DERIVE_FACTS_CLI)], input=payload,
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        result = json.loads(p.stdout)
    except ValueError:
        return None
    formatted = result.get("formatted", "")
    verdicts = result.get("verdicts", [])
    lines = ["DERIVED FACTS (this evidence was gathered by super-jev's own "
            "fallback path, not worker-verify — see docs/playbook.md):", formatted, ""]
    if verdicts:
        lines.append("PRE-RULE VERDICTS (settled in code, before any judge call):")
        for v in verdicts:
            lines.append(f"  CONTRADICTED_BY_FACT 1.00 — {v['claim']!r}: {v['reason']}")
    else:
        lines.append("PRE-RULE VERDICTS: none of the facts above contradict a claim in "
                     "this report.")
    if explain:
        lines.append("")
        if gh_commands:
            lines.append("--explain: gh commands run —")
            for c in gh_commands:
                lines.append(f"  {c}")
        else:
            lines.append("--explain: no gh commands run (no PR named in the report, "
                         "or gh is not on PATH).")
        lines.append(f"--explain: {guard.summary()} (evidence-guard blocklist+redactor).")
    lines.append("")
    lines.append("No judge is reachable in this fallback (worker-verify is not installed "
                "and SUPERJEV_VERIFY_CMD is not set), so nothing here can be called CLEAN. "
                "Any claim not listed above as CONTRADICTED_BY_FACT is UNVERIFIED — read it "
                "yourself.")
    code = 4 if verdicts else 3
    return "\n".join(lines), verdicts, code, guard


def cmd_verify(a):
    json_mode = getattr(a, "json", False)
    hook_mode = getattr(a, "hook_mode", False)
    bad = door_missing(VERIFY_CMD_ENV, FLEET_VERIFY_PY)
    if bad is not None:
        # No door installed and no override. Before refusing outright, try
        # the offline derived-facts/pre-rules fallback (src/enhance/
        # derive-facts.ts via its CLI) — it needs no external door and no
        # TypeSafe key, only git/test output this process gathers itself.
        # See _derived_facts_fallback's docstring for exactly what it can
        # and cannot settle.
        report_text = ""
        try:
            report_text = Path(a.report).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        explain = bool(getattr(a, "explain", False))
        fallback = _derived_facts_fallback(report_text, a.worktree, a.test_cmd,
                                           explain=explain) if report_text else None
        if fallback is None:
            return door_refuse(json_mode, "verify", bad)
        block, verdicts, code, guard = fallback
        summary = VERIFY_VERDICT.get(code, f"exit {code}")
        ledger_append({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "door": "verify", "argv": ["<fallback:derive-facts>"],
            "exit_code": code, "ms": 0, "json_mode": json_mode, "hook_mode": hook_mode,
            "fallback": "derive-facts", "guard": guard.to_dict(),
        })
        if json_mode:
            emit_json("verify", VERIFY_VERDICT_WORD.get(code, "ERROR"), code, summary,
                      {"stdout": block, "stderr": "", "fallback": "derive-facts"}, [])
            return code
        if hook_mode:
            return code, block, ""
        print(block)
        print(f"\nVERDICT: {summary}")
        return code
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
    # Same contract as cmd_gate's: an explicit namespace `timeout` is the
    # Stop hook's remaining wall-clock budget, and the smaller wins.
    timeout = getattr(a, "timeout", None)
    timeout = _verify_timeout() if timeout is None else min(timeout, _verify_timeout())
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


# ---------------------------------------------------------------- guard

def cmd_guard(a):
    """`super-jev guard` — run the evidence-guard blocklist+redactor over
    --paths (checked and, if readable, redacted) and/or --text (redacted),
    entirely locally, no model call, no ledger call other than its own
    counts. Exit 0 always: this door only reports, it never blocks by
    itself — the callers wired into it (verify's fallback gatherer, the
    Stop-hook gate window builder) are what actually skip a path or refuse
    to send text."""
    json_mode = getattr(a, "json", False)
    guard = GuardTally()
    path_reports = []
    for p in (a.paths or []):
        blocked = guard.check_path(p, allow=a.allow_path or [])
        entry = {"path": p, "blocked": blocked}
        if blocked:
            entry["fact"] = blocked_path_fact(p)
        else:
            try:
                raw = Path(p).read_text(encoding="utf-8", errors="replace")
                entry["redacted"] = guard.redact(raw, redact_emails=a.redact_emails)
            except OSError as exc:
                entry["error"] = str(exc)
        path_reports.append(entry)
    text_result = None
    if a.text is not None:
        text_result = guard.redact(a.text, redact_emails=a.redact_emails)
    summary = guard.summary()
    ledger_append({
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "door": "guard", "argv": ["guard"] + list(a.paths or []),
        "exit_code": 0, "ms": 0, "json_mode": json_mode, "hook_mode": False,
        "guard": guard.to_dict(),
    })
    if json_mode:
        emit_json("guard", "RAN", 0, summary,
                  {"paths": path_reports, "text": text_result, "guard": guard.to_dict()}, [])
        return 0
    print(summary)
    for entry in path_reports:
        if entry["blocked"]:
            print(f"  BLOCKED  {entry['path']}  ({SKIPPED_FACT})")
        elif "error" in entry:
            print(f"  ERROR    {entry['path']}  ({entry['error']})")
        else:
            print(f"  OK       {entry['path']}")
    if text_result is not None:
        print("\n--- redacted text ---")
        print(text_result)
    return 0


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


def _read_transcript_records(transcript_path, max_bytes=None):
    """Every parseable JSON object in a Claude Code transcript JSONL file,
    in file order. [] on any read/parse problem — best-effort, never
    raises.

    `max_bytes` reads only the LAST `max_bytes` of the file instead of all
    of it, dropping the first (probably partial) line of that slice. The
    Stop-hook teammate-report scan passes it because the live transcripts
    in the 2026-09-18 set were 11 MB and 13 MB and the scan only ever
    wants the recent tail; the gate window builder deliberately does not,
    because it resolves the current turn's start by walking back from the
    end and must not be handed a truncated view of a turn."""
    try:
        path = Path(transcript_path).expanduser()
        if max_bytes and max_bytes > 0 and path.stat().st_size > max_bytes:
            with path.open("rb") as fh:
                fh.seek(-max_bytes, os.SEEK_END)
                raw = fh.read()
            text = raw.decode("utf-8", errors="ignore")
            lines = text.splitlines()[1:]  # first line is probably a fragment
        else:
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


# ---------------------------------------------------- derived facts at the
# head of the gate window (gate v3)
#
# The gate ships raw command output into the window and asks a language
# judge to infer state from a column dump. The 2026-09-17 gate analysis
# (super-jev-experiments/gate-bench-20260917/analysis/LAST-MISSES.md) found
# that every one of the remaining misses was that same defect: in
# l09 the refutation was a post-write listing line still showing a card the
# draft said it had deleted; in l17 it was the cadence expression
# `untagged -> role-default (... ~ every 152t)` sitting on the last line of
# the window while the draft said "rides every turn"; in t12 it was a
# mismatch table whose four rows were all STRICTER than expected, which is
# what makes the draft's universal "every destructive action refuses" true
# rather than false. Judges reliably catch "evidence says X, draft says
# not-X"; they are weak on reading a column dump as authoritative state and
# on noticing the ABSENCE of a receipt.
#
# So: state the answer as a sentence, in code, before the judge sees
# anything. Everything below is literal string and integer work over text
# ALREADY in the window plus the draft — no model call, no extra evidence,
# no threshold or rule change. Facts go first under DERIVED FACTS, the raw
# window follows unchanged as BACKING, and the evidence cap applies AFTER
# the facts: a fact is never dropped to make room for raw text (the raw
# window's tail is trimmed instead, same priority order the window builder
# itself uses).
#
# This is a pure-Python MIRROR of the `windowFacts` half of
# src/enhance/derive-facts.ts, deliberately duplicated rather than bridged:
# the Stop hook runs on every turn, `node src/derive-facts-cli.ts` pays a
# whole node process's startup per call, and REPO_ROOT does not resolve to
# this repo at all on a fleet install (the skill lives at
# ~/.claude/skills/super-jev/, whose parent is not a checkout), so the
# bridge is both slower and not reliably present on the one path that needs
# it. test/enhance/derive-facts.test.ts and
# skills/super-jev/tests/test_superjev.py assert the SAME fact sentences
# over the same four fixtures, which is what keeps the two sides honest.
DERIVED_FACTS_ENV = "SUPERJEV_DERIVED_FACTS"
DERIVED_FACTS_CAP = 24            # at most this many fact sentences
DERIVED_FACTS_HEADER = (
    "DERIVED FACTS (computed in code from this window's own text plus the "
    "draft — no model call. Each line is a literal reading of the raw "
    "evidence below; prefer it over re-reading the column dump yourself):")
DERIVED_FACTS_BACKING_HEADER = (
    "BACKING (the raw evidence window, unchanged — every fact above was "
    "read out of it):")

# A `::`-qualified identifier — card-style, e.g. `agent_role::fable_awareness`.
# Deliberately NOT extended to file paths: "the file is still in a listing"
# does not refute "I removed the debug block from that file", and a fact that
# reads as a refutation when it is not is worse than no fact at all.
_FACT_QUAL_ID_RE = re.compile(r'\b([A-Za-z][\w.\-]*(?:::[\w.\-]+)+)')
# card.py's own receipt prefixes are stable: WROTE / REMOVED / REFUSED / PROVED.
_FACT_REMOVAL_RECEIPT_RE = re.compile(r'^\s*(REMOVED|DELETED|DROPPED)\b\s*:?\s*(.*)$')
_FACT_DRAFT_REMOVAL_RE = re.compile(
    r'\b(?:deleted|deleting|removed|removing|dropped|dropping|'
    r'(?:is|are|was|were)\s+gone|got\s+rid\s+of)\b', re.IGNORECASE)
# `bus-4 (250,000 tok ~ every 152t)` / `untagged -> role-default (... every 152t)`
_FACT_CADENCE_RE = re.compile(
    r'(?:bus-\d+|untagged\s*->\s*[\w.\-]+)\s*\([^)\n]*\)')
_FACT_EVERY_NT_RE = re.compile(r'every\s+([\d,]+)\s*t\b', re.IGNORECASE)
_FACT_DRAFT_CADENCE_RE = re.compile(
    r'\bevery\s+(?:turn|prompt|message|call)\b|\bevery\s+[\d,]+\s*(?:t\b|turns?\b)',
    re.IGNORECASE)
_FACT_UNIVERSAL_RE = re.compile(r'\b(?:every|all|each)\b', re.IGNORECASE)
# "26/30 match", "26 of 30 match", "10 of 10 need a human"
_FACT_N_OF_M_RE = re.compile(
    r'\b(\d+)\s*(?:/|\s+of\s+)\s*(\d+)\s+([A-Za-z][\w]*(?:[ _-][a-z][\w]*){0,2})')
# ('safe-05', 'safe_to_auto', 'needs_approval')
_FACT_TUPLE_ROW_RE = re.compile(
    r"\(\s*'([^']{1,60})'\s*,\s*'([^']{1,40})'\s*,\s*'([^']{1,40})'\s*\)")
# How strict an outcome label is, for reading a mismatch row as stricter or
# more permissive than expected. Unknown labels rank None and are not read.
_FACT_STRICTNESS = {
    "safe_to_auto": 0, "safe": 0, "auto": 0, "allow": 0, "pass": 0,
    "needs_approval": 1, "approval": 1, "ask": 1, "confirm": 1,
    "refuse": 2, "refused": 2, "block": 2, "blocked": 2, "deny": 2,
}
_FACT_MERGE_RECEIPT_RE = re.compile(
    r'^\s*MERGED\b|\bgh\s+pr\s+merge\s+\d+|"mergedAt"\s*:\s*"[^"]+"', re.IGNORECASE)
_FACT_PR_NUM_RES = (
    re.compile(r'\bgh\s+pr\s+merge\s+(\d+)'),
    re.compile(r'\bgh\s+pr\s+(?:view|checks)\s+(\d+)'),
    re.compile(r'\(#(\d+)\)'),
    re.compile(r'#(\d+)\b'),
    re.compile(r'"number"\s*:\s*(\d+)'),
)
# 2026-09-18: '#' made optional in all three, and the third pattern's
# "is merged" requirement loosened to a bare "merged" so "#39 merged"
# matches the same as "#39 is merged" — see SET2-AUDIT.md's l30: "PR 39
# merged" / "merged PR 39" / "#39 merged" now each match one of the three.
# The identity guard is untouched: `_facts_merge_claims` (below) still only
# ever compares a claimed PR number against a receipt actually found in the
# window, never trusts a claim on its own.
_FACT_DRAFT_MERGE_RES = (
    re.compile(r'\bPR\s*#?(\d+)\b[^.\n]{0,40}?\bmerged\b', re.IGNORECASE),
    re.compile(r'\bmerged\b[^.\n]{0,40}?\bPR\s*#?(\d+)', re.IGNORECASE),
    re.compile(r'#(\d+)\b[^.\n]{0,20}?\b(?:is\s+)?merged\b', re.IGNORECASE),
)

# Family 5 (2026-09-18, V4-RESIDUE.md change 2) — a REPORT FROM block
# stating PR #N is not merged / open / pending, matched two ways so word
# order does not matter ("PR #27 is not merged" / "not merged: PR #27").
_FACT_REPORT_NOT_MERGED_RES = (
    re.compile(r'\bPR\s*#?(\d+)\b[^.\n]{0,40}?\b(?:is\s+)?'
              r'(?:not\s+merged|open|pending)\b', re.IGNORECASE),
    re.compile(r'\b(?:not\s+merged|open|pending)\b[^.\n]{0,40}?'
              r'\bPR\s*#?(\d+)\b', re.IGNORECASE),
)
_REPORT_MARKER_LINE_RE = re.compile(r'^REPORT FROM .+ \(unverified worker claim\)\s*$')

# Family 6 (2026-09-18, SET3-AUDIT2.md section 5 #1) — written-file identity.
# "I wrote/saved/created/updated file X" is the single highest-value claim
# type SET3-AUDIT2 found with no free check: ten of thirty set-3 drafts open
# with this sentence, and the window already carries the receipt (a Write/
# Edit tool result, or a `cat >`/redirect in the current turn's own bash
# history) — the gate just never compared the identifier. Scoped to files
# named as written IN THE CURRENT TURN ONLY, per the audit's own caveat
# (l45): a file written in an OLDER turn is real, but stating it as "the
# only file written in this window" when a newer write also happened would
# misread a session, not just this turn.
_FACT_FILE_TOKEN_RE = re.compile(
    r'\b[\w][\w.\-]{0,80}\.(?:md|txt|log|json)\b', re.IGNORECASE)
_FACT_WRITE_RECEIPT_RES = (
    re.compile(r'\[from:\s*(?:Write|Edit)\s+(\S+)', re.IGNORECASE),
    re.compile(r'File created successfully at:\s*(\S+?)(?:\s*\(|\s*$)', re.IGNORECASE),
    re.compile(r'The file\s+(\S+?)\s+has been updated successfully', re.IGNORECASE),
    re.compile(r'\bsaved to\s+(\S+)', re.IGNORECASE),
    re.compile(r'>\s*(\S+\.(?:md|txt|log|json))\b', re.IGNORECASE),
)
_FACT_DRAFT_WRITE_VERB_RE = re.compile(
    r'\b(?:written|wrote|writes|saved|save|created?|updated?)\b', re.IGNORECASE)
# Sentence-only split (never on standalone " and "), because a drafted list
# of written files ("written to A, B, and C") must stay one unit — splitting
# on " and " would silently drop the claim about C (SET3-AUDIT2.md l45).
_FACT_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+')


def _facts_written_file_claims(window_text, draft_text):
    """Family 6 — set-difference the file names the draft claims were
    WRITTEN this turn against the files the window's own current-turn
    receipts show were actually written. Free check, no model call: any
    draft-named file absent from that set is a contradiction; a match is
    stated too, so the fact reads the same whether the draft is honest or
    not. Silent when the current turn holds no write receipt at all (an
    older-turn write is not "the only file written in this window" — see
    the docstring above the regexes), or when the draft names nothing that
    looks like a written file."""
    written, seen_paths = [], set()
    in_current = False
    for raw in (window_text or "").splitlines():
        stripped = raw.strip()
        if stripped == "[current turn]":
            in_current = True
            continue
        if _WINDOW_SECTION_RE.match(stripped):
            in_current = False
            continue
        if not in_current:
            continue
        for rx in _FACT_WRITE_RECEIPT_RES:
            m = rx.search(raw)
            if not m:
                continue
            path = m.group(1).strip('\'"()<>')
            base = os.path.basename(path)
            if base and base not in seen_paths:
                seen_paths.add(base)
                written.append(base)
            break
    if not written:
        return []

    facts = []
    named = set()
    for sentence in _FACT_SENTENCE_SPLIT_RE.split(draft_text or ""):
        if not _FACT_DRAFT_WRITE_VERB_RE.search(sentence):
            continue
        for m in _FACT_FILE_TOKEN_RE.finditer(sentence):
            base = m.group(0)
            if base in named:
                continue
            named.add(base)
            if base in written:
                facts.append(f"WRITTEN FILE: the draft names {base}; that file "
                             f"was written in this turn — SUPPORTED.")
            else:
                only = written[0] if len(written) == 1 else ", ".join(written)
                facts.append(
                    f"WRITTEN FILE: the draft names {base}; the only file "
                    f"written in this window is {only} — CONTRADICTED_BY_FACT.")
    return facts


# Family 7 (2026-09-18, SET3-AUDIT2.md section 5 #3) — file read-back. The
# harder structural hole: a fact INSIDE a document this fleet reads is
# invisible to the gate today, because a Read/cat receipt returns content
# but nothing pins a value from it against a value the draft later restates
# "from" or "in" that file. Deliberately narrow to numeric-shaped values
# (a price, a score, a level, a percentage) — the shape SET3-AUDIT2 measured
# — never a bare word, so this stays a literal presence check, not a guess.
_FACT_READBACK_HEADER_RES = (
    re.compile(r'\[from:\s*Read\s+(\S+)', re.IGNORECASE),
    re.compile(r'\[from:[^\]\n]*\b(?:cat|head|tail)\b[^\]\n]*?(\S+\.(?:md|txt|log|json))', re.IGNORECASE),
)
# Two orders, both tight — the value has to sit right next to the file
# reference, with at most a short linking verb between them, or an
# unrelated number sharing a sentence with an unrelated file mention would
# false-fire (t22, set 2: "PR #13 conflicts with it in package.json" is not
# a claim that 13 comes FROM package.json, and must stay silent).
_FACT_READBACK_FILE_THEN_VALUE_RE = re.compile(
    r'\b(?:from|in)\s+([\w.\-]{1,80}\.(?:md|txt|log|json))\b[^.!?\n]{0,25}?'
    r'\b(?:is|was|reads?|shows?|states?)\b\s*(\$?\d(?:[\d,]*\d)?(?:\.\d+)?%?)',
    re.IGNORECASE)
_FACT_READBACK_VALUE_THEN_FILE_RE = re.compile(
    r'(\$?\d(?:[\d,]*\d)?(?:\.\d+)?%?)\s+(?:\w+\s+){0,2}?\b(?:from|in)\s+'
    r'([\w.\-]{1,80}\.(?:md|txt|log|json))\b', re.IGNORECASE)
# Values only, never a bare trailing separator: a value ends on a digit (or
# the '%' sign), so "87," in prose never swallows the comma into the match.
_FACT_VALUE_RE = re.compile(r'\$?\d(?:[\d,]*\d)?(?:\.\d+)?%?')


def _value_shape(value):
    """A coarse (marker, digit-count) bucket for a numeric-looking token —
    '$' / '%' / plain, and how many digits it carries. Two values only
    count as "the same shape" (see `_facts_read_back_claims`) when their
    buckets match, which is what keeps an incidental line number or a date
    fragment in a read-back block from masquerading as a contradicting
    price or score."""
    marker = '$' if value.startswith('$') else ('%' if value.endswith('%') else '')
    digits = re.sub(r'[^\d]', '', value)
    return (marker, len(digits))


def _read_back_blocks(window_text):
    """{basename: block_text} for every file the window shows was read this
    session — the text between a Read/cat/head/tail receipt header and the
    next `[from:`/section boundary. Last-write-wins on a repeated basename
    (the most recent read is the one a later draft would be quoting)."""
    lines = (window_text or "").splitlines()
    blocks = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        base = None
        for rx in _FACT_READBACK_HEADER_RES:
            m = rx.search(line)
            if m:
                base = os.path.basename(m.group(1).strip('\'"()<>'))
                break
        if base:
            body = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if nxt.startswith('[from:') or _WINDOW_SECTION_RE.match(nxt) \
                        or _SECTION_SEPARATOR_RE.match(nxt):
                    break
                body.append(lines[j])
                j += 1
            if base:
                blocks[base] = "\n".join(body)
            i = j
            continue
        i += 1
    return blocks


def _facts_read_back_claims(window_text, draft_text):
    """Family 7 — when the draft quotes a numeric value "from"/"in" a file
    the window shows was read, and that value is absent from the read-back
    block while a different value of the same shape sits in it, name both.
    Silent when the block is absent (nothing to check against) or the
    claimed value is present (nothing to contradict)."""
    blocks = _read_back_blocks(window_text)
    if not blocks:
        return []
    facts, seen = [], set()
    for sentence in _FACT_SENTENCE_SPLIT_RE.split(draft_text or ""):
        pairs = []
        for m in _FACT_READBACK_FILE_THEN_VALUE_RE.finditer(sentence):
            pairs.append((m.group(2), os.path.basename(m.group(1))))
        for m in _FACT_READBACK_VALUE_THEN_FILE_RE.finditer(sentence):
            pairs.append((m.group(1), os.path.basename(m.group(2))))
        for value, base in pairs:
            # A bare 1-digit token ("Line 1:", a list index) is noise, not a
            # quoted value, unless it carries its own $/% marker.
            if len(re.sub(r'[^\d]', '', value)) < 2 and '$' not in value and '%' not in value:
                continue
            block = blocks.get(base)
            if not block:
                continue
            if value in block:
                continue
            block_values = [v for v in _FACT_VALUE_RE.findall(block)
                            if v and (len(re.sub(r'[^\d]', '', v)) >= 2 or '$' in v or '%' in v)]
            shape = _value_shape(value)
            others = [v for v in block_values if v != value and _value_shape(v) == shape]
            if not others:
                continue
            key = (base, value)
            if key in seen:
                continue
            seen.add(key)
            facts.append(
                f"FILE READ-BACK: the draft states {value} from {base}; the "
                f"read-back of {base} in this window does not contain {value} "
                f"and instead shows {others[0]} — CONTRADICTED_BY_FACT.")
    return facts


# Families 8, 9 and 10 (2026-09-18, gate-bench-20260918-fleet round 2). All
# three answer the same measured shape: this fleet's drafts restate a value
# that a tool in the window already stated under a LABEL, and the older
# naive form of the check — "every number in the draft must appear verbatim
# in the window" — was measured at 13 of 20 truths on set 3, i.e. unusable
# (analysis/SET3-AUDIT2.md section 5). What makes these three safe is that
# the value is never compared on bare membership. It is compared only when
# an IDENTITY anchor ties the draft's value to one specific labelled value
# in the window: a shared label word (family 8), the tool's own `conf=`
# receipt list (family 9), or an explicit min/max label for the same
# quantity (family 10). No identity anchor means silence, not a guess.

_FACT_VALUE_TOKEN_RE = re.compile(r'\$?\d(?:[\d,]*\d)?(?:\.\d+)?%?')
_FACT_SCORE_TOKEN_RE = re.compile(r'\b(0\.\d\d?|1\.00)\b')
# Words that may sit BETWEEN a label and its value without breaking the
# pairing ("cut under $3.55", "equity is $10,249"). A linker is never
# itself taken as the label.
_FACT_LINKER_WORDS = frozenset((
    "is", "was", "are", "were", "at", "of", "to", "under", "over", "above",
    "below", "and", "or", "a", "an", "the", "only", "about", "around",
    "approximately", "now", "up", "down", "back", "by", "than", "then", "be",
    "been", "hit", "set", "reads", "read", "shows", "show", "states", "state",
    "sits", "stands", "came",
))
# A value-then-label pairing REQUIRES one of these right after the value
# ("$219 in on fill"). Without a preposition, the next word is just the
# next word ("$4.50, cut under $3.55" must not pair $4.50 with "cut").
_FACT_PREP_WORDS = frozenset(("in", "on", "from", "for", "per", "into",
                              "within", "out"))
# Label words too generic to be an identity anchor. "line"/"value"/"score"
# are in here on purpose: they appear in nearly every tool receipt this
# fleet emits, so pairing on them would be pairing on nothing.
_FACT_LABEL_STOPWORDS = _FACT_LINKER_WORDS | _FACT_PREP_WORDS | frozenset((
    "this", "that", "these", "those", "with", "it", "its", "his", "her",
    "their", "our", "your", "you", "he", "she", "they", "them", "we", "i",
    "my", "me", "not", "no", "but", "all", "any", "has", "have", "had", "do",
    "does", "did", "get", "got", "will", "would", "can", "could", "should",
    "if", "so", "as", "also", "just", "still", "yet", "more", "less", "new",
    "old", "one", "two", "three", "total", "each", "both", "same", "other",
    "there", "here", "when", "what", "which", "who", "how", "why", "after",
    "before", "while", "during", "since", "via", "plus", "minus", "line",
    "value", "number", "score", "time", "date",
))
_FACT_WORD_RE = re.compile(r"[A-Za-z][A-Za-z_\-]{1,}")
_FACT_CLAUSE_BOUNDARY_RE = re.compile(r'[,;:()\[\]/]')
_FACT_COLON_ROW_RE = re.compile(r'^([^:{}\[\]"]{2,70}):\s*(.+)$')
_FACT_COLUMN_SPLIT_RE = re.compile(r'\s{2,}')
_FACT_INNER_PAIR_RE = re.compile(
    r'\b([A-Za-z][A-Za-z_\-]{1,20})\s+(' + _FACT_VALUE_TOKEN_RE.pattern + r')\b')
_FACT_RANGE_RE = re.compile(
    r'([A-Za-z][\w\-]*(?:\s+[A-Za-z][\w\-]*){0,2})\s+(?:from\s+)?('
    + _FACT_VALUE_TOKEN_RE.pattern + r')\s+(?:up\s+)?to\s+('
    + _FACT_VALUE_TOKEN_RE.pattern + r')')


def _fact_stem(word):
    """A crude suffix strip, so a receipt's "filled" and a draft's "fill"
    are the same label. Deliberately not a real stemmer: it must be
    predictable enough to reason about in a review."""
    w = word.lower()
    # No "es" rule: stripping it sent "doses" to "dos" while "dose" stayed
    # "dose", so a plural in the draft stopped matching its own label in the
    # window. A single trailing "s" covers that case and cannot disagree with
    # itself.
    for suf in ("ing", "ed", "s"):
        if len(w) - len(suf) >= 3 and w.endswith(suf):
            return w[: len(w) - len(suf)]
    return w


def _fact_label_keys(text):
    """The stemmed content words of a label — the identity anchors. Words
    under 3 characters and every generic receipt word are dropped."""
    keys = []
    for word in _FACT_WORD_RE.findall(text or ""):
        # NOT split on "-": a hyphenated identifier is one token. Splitting
        # it turned a helper name ending in "-fill" into a "fill" label and
        # shadowed the real "premium collected if filled" row (measured on
        # set 3's l46, 2026-09-18).
        for part in word.lower().replace("_", " ").split():
            if len(part) < 3 or part in _FACT_LABEL_STOPWORDS:
                continue
            keys.append(_fact_stem(part))
    return keys


def _fact_value_marker(value):
    return "$" if value.startswith("$") else ("%" if value.endswith("%") else "")


def _fact_value_number(value):
    try:
        return float(re.sub(r"[^\d.]", "", value) or "x")
    except ValueError:
        return None


def _fact_is_score_value(value):
    """A 0.00-1.00 confidence-shaped token. Kept apart from plain numbers so
    a score is never compared against a count or a line number."""
    return (_fact_value_marker(value) == ""
            and re.fullmatch(r'0\.\d\d?|1\.00?|1', value) is not None)


def _fact_window_label_values(window_text):
    """`label_key -> {value: label_text}` for the labelled numeric rows a
    tool emitted into the window. Two row shapes only, both literal:
    `LABEL: ... VALUE` and a whitespace-column `LABEL  ...  VALUE`. A row
    whose label carries a digit is skipped (that is prose, not a label)."""
    table = {}
    for raw in (window_text or "").splitlines():
        s = raw.strip()
        if not s or _WINDOW_SECTION_RE.match(s) or s.startswith("[from:"):
            continue
        pairs = []
        m = _FACT_COLON_ROW_RE.match(s)
        if m and not re.search(r"\d", m.group(1)):
            label, rest = m.group(1).strip(), m.group(2)
            inner = list(_FACT_INNER_PAIR_RE.finditer(rest))
            if len(inner) >= 2:
                # One row carrying several labelled values
                # ("confidence: min 0.47  median 1.00  max 1.00").
                for im in inner:
                    pairs.append((label + " " + im.group(1), im.group(2)))
            else:
                v = _FACT_VALUE_TOKEN_RE.search(rest)
                if v:
                    pairs.append((label, v.group(0)))
        if not pairs:
            cells = [c.strip() for c in _FACT_COLUMN_SPLIT_RE.split(s) if c.strip()]
            if (len(cells) >= 2 and len(cells[0]) <= 70
                    and not re.search(r"\d", cells[0])):
                for cell in cells[1:]:
                    v = _FACT_VALUE_TOKEN_RE.search(cell)
                    if v:
                        pairs.append((cells[0], v.group(0)))
                        break
        for label, value in pairs:
            for key in set(_fact_label_keys(label)):
                table.setdefault(key, {}).setdefault(value, label)
    return table


def _fact_draft_label_values(draft_text):
    """`(label_key, value)` pairs the draft states by EXPLICIT adjacency.
    Never across a comma, semicolon, colon, slash or paren, and never
    across another value — a pairing that has to jump a clause boundary is
    not a pairing this check is willing to assert."""
    out = []
    for sentence in _FACT_SENTENCE_SPLIT_RE.split(draft_text or ""):
        range_starts = set()
        for m in _FACT_RANGE_RE.finditer(sentence):
            # "interaction from 0.42 up to 0.90" — the labelled row in the
            # window holds the CURRENT value, so the range's endpoint is the
            # one the row can speak to. The start value is left alone: a
            # correct "before" figure that predates the window must not be
            # called a contradiction.
            range_starts.add(m.start(2))
            keys = _fact_label_keys(m.group(1))
            if keys:
                out.append((keys[-1], m.group(3)))
        for m in _FACT_VALUE_TOKEN_RE.finditer(sentence):
            if m.start() in range_starts:
                continue
            value = m.group(0)
            before = sentence[max(0, m.start() - 70):m.start()]
            after = sentence[m.end():m.end() + 70]
            seg = _FACT_CLAUSE_BOUNDARY_RE.split(before)[-1]
            if not _FACT_VALUE_TOKEN_RE.search(seg):
                for word in reversed(_FACT_WORD_RE.findall(seg)[-4:]):
                    lw = word.lower()
                    if lw in _FACT_LINKER_WORDS or lw in _FACT_PREP_WORDS:
                        continue
                    if lw in _FACT_LABEL_STOPWORDS or len(lw) < 3:
                        break
                    out.append((_fact_stem(lw), value))
                    break
            seg2 = _FACT_CLAUSE_BOUNDARY_RE.split(after)[0]
            aw = _FACT_WORD_RE.findall(seg2)
            if (aw and aw[0].lower() in _FACT_PREP_WORDS
                    and not _FACT_VALUE_TOKEN_RE.search(seg2)):
                for word in aw[1:4]:
                    lw = word.lower()
                    if lw in _FACT_PREP_WORDS or lw in _FACT_LINKER_WORDS:
                        continue
                    if lw in _FACT_LABEL_STOPWORDS or len(lw) < 3:
                        break
                    out.append((_fact_stem(lw), value))
                    break
    return out


def _facts_labelled_value_claims(window_text, draft_text):
    """Family 8 — pair LABEL to VALUE. Fires only when the draft states a
    value right next to a label word that the window's own labelled rows
    carry EXACTLY ONE value for, and that value differs in the same shape
    class. The uniqueness requirement is the guard that matters: a label the
    window states two different values for is a label this check knows
    nothing about, so it stays silent rather than pick one."""
    table = _fact_window_label_values(window_text)
    if not table:
        return []
    bad, good, seen = [], [], set()
    for key, value in _fact_draft_label_values(draft_text):
        values = table.get(key)
        if not values or len(values) != 1:
            continue
        wval, label = next(iter(values.items()))
        if _fact_value_marker(wval) != _fact_value_marker(value):
            continue
        if _fact_is_score_value(wval) != _fact_is_score_value(value):
            continue
        dedup = (key, value, wval)
        if dedup in seen:
            continue
        seen.add(dedup)
        shown = label[:60]
        if wval == value:
            good.append(f"LABELLED VALUE: the draft states {value} next to "
                        f"{key!r}; this window's own {shown!r} row also shows "
                        f"{wval} — SUPPORTED.")
        else:
            bad.append(f"LABELLED VALUE: the draft states {value} next to "
                       f"{key!r}; the only {key!r} value in this window is "
                       f"{wval}, on its {shown!r} row — CONTRADICTED_BY_FACT.")
    return bad + good


_FACT_CONF_RECEIPT_RE = re.compile(
    r'\bconf(?:idence)?\s*=\s*(0\.\d\d?|1\.00?|1)\b', re.IGNORECASE)


def _facts_score_list_claims(window_text, draft_text):
    """Family 9 — score-list membership, scoped to ONE tool's own `conf=`
    receipt lines rather than to the whole window. That scope is the whole
    point: SET3-AUDIT2.md section 6 measured the window-wide form of this
    rule at 1 lie and 2 truths on set 3, and it MISSED the case it was
    built for, because the mutated score occurred legitimately elsewhere in
    the window under a different tool. Scoped to the run's own receipt
    lines, that coincidence stops mattering. Requires at least two of the
    draft's scores to be members before it will call a third a stranger, so
    a draft that merely mentions a score in passing never fires."""
    conf_values = {m.group(1) for m in _FACT_CONF_RECEIPT_RE.finditer(window_text or "")}
    if len(conf_values) < 2:
        return []
    quoted = [m.group(1) for m in _FACT_SCORE_TOKEN_RE.finditer(draft_text or "")]
    if len(quoted) < 3:
        return []
    members = {v for v in quoted if v in conf_values}
    strangers = [v for v in dict.fromkeys(quoted) if v not in conf_values]
    if len(members) < 2:
        return []
    shown = ", ".join(sorted(conf_values))
    if not strangers:
        return [f"SCORE LIST: every score the draft quotes ({', '.join(sorted(members))}) "
                f"is one of this run's own conf= values in this window — SUPPORTED."]
    return [f"SCORE LIST: the draft quotes {v} as a score from a run whose own "
            f"conf= values in this window are {shown} — CONTRADICTED_BY_FACT."
            for v in strangers]


_FACT_SUPERLATIVES = {
    "least": "min", "lowest": "min", "smallest": "min", "minimum": "min",
    "min": "min", "worst": "min", "most": "max", "highest": "max",
    "largest": "max", "maximum": "max", "max": "max", "best": "max",
}
_FACT_DRAFT_EXTREMUM_RE = re.compile(
    r'\b(' + "|".join(sorted(_FACT_SUPERLATIVES)) + r')\b'
    r'((?:[\s,]+[A-Za-z][\w\-]*){0,3})[\s,]+('
    + _FACT_VALUE_TOKEN_RE.pattern + r')', re.IGNORECASE)
_FACT_WINDOW_EXTREMUM_RE = re.compile(
    r'([A-Za-z][\w\-]{4,24})\s*:?\s*\b(min|max)\b\s*[:=]?\s*('
    + _FACT_VALUE_TOKEN_RE.pattern + r')', re.IGNORECASE)
_FACT_PREFIX_MATCH_LEN = 6


def _fact_shares_prefix(a, b, n=_FACT_PREFIX_MATCH_LEN):
    """Same quantity, different part of speech — a receipt's "confidence"
    against a draft's "confident". A fixed 6-character prefix, not a synonym
    table: it ties two words that are the same word, and nothing else."""
    a, b = (a or "").lower(), (b or "").lower()
    return len(a) >= n and len(b) >= n and a[:n] == b[:n]


def _facts_claimed_extremum_claims(window_text, draft_text):
    """Family 10 — a claimed extremum against a recorded one. When the draft
    calls a value the least/lowest (or most/highest) of something, and the
    window carries an explicit `min`/`max` row for a quantity of the SAME
    name, a recorded value beyond the claimed bound settles the claim with
    arithmetic. Silent unless both the extremum sense and the quantity name
    match, so "the lowest bid was 3" never meets a max-latency row."""
    recorded = []
    for raw in (window_text or "").splitlines():
        for m in _FACT_WINDOW_EXTREMUM_RE.finditer(raw.strip()):
            recorded.append((m.group(1), m.group(2).lower(), m.group(3)))
    if not recorded:
        return []
    facts, seen = [], set()
    for sentence in _FACT_SENTENCE_SPLIT_RE.split(draft_text or ""):
        for m in _FACT_DRAFT_EXTREMUM_RE.finditer(sentence):
            sense = _FACT_SUPERLATIVES[m.group(1).lower()]
            claimed = m.group(3)
            claimed_n = _fact_value_number(claimed)
            if claimed_n is None:
                continue
            qwords = _FACT_WORD_RE.findall(m.group(1) + " " + m.group(2))
            for qty, wsense, wval in recorded:
                if wsense != sense or wval == claimed:
                    continue
                if not any(_fact_shares_prefix(qty, w) for w in qwords):
                    continue
                if _fact_value_marker(wval) != _fact_value_marker(claimed):
                    continue
                if _fact_is_score_value(wval) != _fact_is_score_value(claimed):
                    continue
                wn = _fact_value_number(wval)
                if wn is None:
                    continue
                if not ((sense == "min" and wn < claimed_n)
                        or (sense == "max" and wn > claimed_n)):
                    continue
                dedup = (qty.lower(), sense, claimed, wval)
                if dedup in seen:
                    continue
                seen.add(dedup)
                facts.append(
                    f"CLAIMED EXTREMUM: the draft states {claimed} as the "
                    f"{sense} {qty}; this window's own {qty} {sense} row "
                    f"shows {wval} — CONTRADICTED_BY_FACT.")
    return facts


def _derived_facts_enabled():
    return os.environ.get(DERIVED_FACTS_ENV, "1") != "0"


def _fact_window_lines(window_text):
    """Every line of the assembled window as (section_label, line), where
    the label is the `[current turn]` / `[session receipts]` /
    `[previous turn -N]` header the line sits under (see
    _WINDOW_SECTION_RE). The label is what a fact cites as its source, so a
    human reading a fact can find the line it came from."""
    label = "the evidence window"
    out = []
    for raw in (window_text or "").splitlines():
        line = raw.rstrip()
        if _WINDOW_SECTION_RE.match(line.strip()):
            label = line.strip()
            continue
        if not line.strip() or _SECTION_SEPARATOR_RE.match(line):
            continue
        out.append((label, line))
    return out


def _fact_ids_in(text):
    """`::`-qualified identifiers in `text`, first-seen order, deduped."""
    seen, out = set(), []
    for m in _FACT_QUAL_ID_RE.finditer(text or ""):
        ident = m.group(1).rstrip('.,;:!?)\'"')
        if ident and ident not in seen:
            seen.add(ident)
            out.append(ident)
    return out


def _fact_mentions_id(text, ident):
    """True when `text` names `ident` — either in full, or by its last
    `::` segment on its own word boundaries (a draft says "fable_awareness"
    where the window says "agent_role::fable_awareness"). Word-bounded so a
    longer name that merely contains this one does not count."""
    if not text:
        return False
    if ident in text:
        return True
    tail = ident.split("::")[-1]
    if len(tail) < 4:
        return False
    return re.search(r'(?<![\w.:\-])' + re.escape(tail) + r'(?![\w.\-])', text) is not None


def _fact_is_listing_line(line, ident):
    """True when `line` reads as a LISTING row for `ident` — the identifier
    followed by at least two numeric columns, which is the shape `card.py`
    and friends print state in. A listing row emitted after a claimed
    removal is a statement that the thing is still there."""
    idx = line.find(ident)
    if idx < 0:
        return False
    if _FACT_REMOVAL_RECEIPT_RE.match(line):
        return False
    rest = line[idx + len(ident):]
    return len(re.findall(r'\b[\d,]+\b', rest)) >= 2


def _fact_strictness(label):
    return _FACT_STRICTNESS.get((label or "").strip().lower())


def _facts_delete_claims(lines, draft_clauses):
    """Family 1 — delete/remove claims. Two halves, both literal:
    every removal RECEIPT in the window is stated as a fact, and any
    identifier the draft claims to have removed that instead appears in a
    later LISTING row is stated as still present. l09's lie is exactly the
    gap between those two halves: the only REMOVED receipt in its window
    names a different card, and the card the draft says it deleted is on a
    post-write listing line one row above."""
    facts = []
    removed = {}
    for label, line in lines:
        m = _FACT_REMOVAL_RECEIPT_RE.match(line)
        if not m:
            continue
        for ident in _fact_ids_in(m.group(2)):
            removed.setdefault(ident, label)
    for ident, label in removed.items():
        facts.append(f"{ident} removed per {label}.")

    claimed = []
    for clause in draft_clauses:
        if not _FACT_DRAFT_REMOVAL_RE.search(clause):
            continue
        for label, line in lines:
            for ident in _fact_ids_in(line):
                if ident in claimed or ident in removed:
                    continue
                if _fact_mentions_id(clause, ident):
                    claimed.append(ident)
    for ident in claimed:
        for label, line in lines:
            if _fact_is_listing_line(line, ident):
                facts.append(f"{ident} still present in {label} after the "
                             "claimed removal.")
                break
    return facts


def _facts_cadence_claims(lines, draft_text):
    """Family 2 — cadence claims. When the draft asserts an injection
    frequency ("rides every turn", "on every prompt", "every N turns"),
    quote the window's OWN cadence line for each card the draft names, and
    do the one integer comparison the judge did not do: `~ every 152t` is
    every 152 turns, which is not every turn (l17)."""
    if not _FACT_DRAFT_CADENCE_RE.search(draft_text or ""):
        return []
    facts, seen = [], set()
    for label, line in lines:
        m = _FACT_CADENCE_RE.search(line)
        if not m:
            continue
        expr = " ".join(m.group(0).split())
        for ident in _fact_ids_in(line):
            if ident in seen or not _fact_mentions_id(draft_text, ident):
                continue
            seen.add(ident)
            n_match = _FACT_EVERY_NT_RE.search(expr)
            tail = "."
            if n_match:
                try:
                    n = int(n_match.group(1).replace(",", ""))
                except ValueError:
                    n = None
                if n is not None and n > 1:
                    tail = (f" — that is every {n} turns, not every turn.")
                elif n == 1:
                    tail = " — that is every turn."
            facts.append(f'{ident} cadence in {label}: "{expr}"{tail}')
    return facts


def _facts_result_tables(lines, draft_text):
    """Family 3 — pass/mismatch tables. When the window holds a results
    table and the draft makes a UNIVERSAL claim ("every", "all", "each")
    about that population, print the counts so the judge is comparing the
    claim against a number instead of eyeballing rows. On t12 this is what
    turns a table that looks like a counterexample list into what it
    actually is: four mismatches, every one of them STRICTER than
    expected, none more permissive — which is why "every destructive
    action refuses" is true there."""
    draft = draft_text or ""
    n_of_m = []
    for label, line in lines:
        for m in _FACT_N_OF_M_RE.finditer(line):
            n_of_m.append((int(m.group(1)), int(m.group(2)),
                           " ".join(m.group(3).split()).lower()))
    rows = []
    for label, line in lines:
        for m in _FACT_TUPLE_ROW_RE.finditer(line):
            rows.append((m.group(1), m.group(2), m.group(3)))
    if not n_of_m and not rows:
        return []

    labels = {lab for _n, _m, lab in n_of_m}
    labels |= {r[1].lower() for r in rows} | {r[2].lower() for r in rows}
    if not _FACT_UNIVERSAL_RE.search(draft):
        return []
    draft_low = draft.lower()
    words = {w for lab in labels for w in re.split(r'[ _-]+', lab) if len(w) >= 4}
    if not any(w in draft_low for w in words):
        return []

    facts, seen = [], set()
    for n, m, lab in n_of_m:
        line = f"table shows {n} of {m} {lab}."
        if line not in seen:
            seen.add(line)
            facts.append(line)
    if rows:
        by_actual = {}
        for _case, _exp, act in rows:
            by_actual[act] = by_actual.get(act, 0) + 1
        for act in sorted(by_actual, key=lambda a: (-by_actual[a], a)):
            facts.append(f"table shows {by_actual[act]} of {len(rows)} mismatch "
                         f"rows with actual {act}.")
        ranked = [(_fact_strictness(e), _fact_strictness(a)) for _c, e, a in rows]
        if all(e is not None and a is not None for e, a in ranked):
            stricter = sum(1 for e, a in ranked if a > e)
            looser = sum(1 for e, a in ranked if a < e)
            facts.append(f"table shows {stricter} of {len(rows)} mismatch rows "
                         f"stricter than expected, {looser} more permissive.")
    return facts


def _facts_merge_claims(lines, draft_text):
    """Family 4 — merge/CI claims. Every merge receipt in the window is
    named with its PR number, and every PR the draft says was merged with
    no such receipt is named as missing one. The absent half is the one a
    judge does not do: t06 claims three merges and the whole window carries
    exactly one merge receipt."""
    found = {}
    for label, line in lines:
        if not _FACT_MERGE_RECEIPT_RE.search(line):
            continue
        for rx in _FACT_PR_NUM_RES:
            m = rx.search(line)
            if m:
                found.setdefault(int(m.group(1)), label)
                break
    facts = [f"merge receipt found for PR #{n} in {found[n]}."
             for n in sorted(found)]
    claimed = set()
    for rx in _FACT_DRAFT_MERGE_RES:
        for m in rx.finditer(draft_text or ""):
            claimed.add(int(m.group(1)))
    for n in sorted(claimed - set(found)):
        facts.append(f"no merge receipt for PR #{n} in window.")
    return facts


def _section_recency_rank(label):
    """A coarse, deterministic ordering of window sections from oldest to
    newest, used only to decide whether a merge receipt found in one
    section postdates a worker report found in another (see
    `_facts_stale_report_claims`). Mirrors the section layering
    `_derive_evidence_text_from_transcript` documents: previous turns
    oldest-to-newest as -K grows smaller in magnitude (turn -1 is newer
    than turn -2), then session receipts (refreshed every Stop event),
    then this turn's relayed reports, then this turn's own tool results —
    the highest-priority, most current material of all. An unrecognised
    or missing label (flat text with no section headers, e.g. a unit
    test's fixture) ranks lowest, so two unlabelled mentions can never
    outrank each other."""
    m = re.match(r'^\[previous turn -(\d+)\]$', label)
    if m:
        return -int(m.group(1))
    return {"[session receipts]": 0, "[current turn reports]": 1,
           "[current turn]": 2}.get(label, -1000)


def _report_not_merged_claims(window_text):
    """[(pr_num, section_label), ...] for every PR a REPORT FROM block
    states is not merged / open / pending. Scans `window_text` directly
    (not the already-filtered fact lines `_fact_window_lines` returns) so
    a report's body is bounded by its own paragraph break or the section
    separators the window assembly itself uses ('---', '===', a new
    section header, or another report's own marker) — reading a claim
    never bleeds past the report it came from into an unrelated
    tool-result line elsewhere in the same section."""
    out = []
    label = "the evidence window"
    in_report = False
    for raw in (window_text or "").splitlines():
        stripped = raw.strip()
        if _WINDOW_SECTION_RE.match(stripped):
            label = stripped
            in_report = False
            continue
        if _REPORT_MARKER_LINE_RE.match(stripped):
            in_report = True
            continue
        if not stripped or _SECTION_SEPARATOR_RE.match(stripped):
            in_report = False
            continue
        if not in_report:
            continue
        for rx in _FACT_REPORT_NOT_MERGED_RES:
            for m in rx.finditer(raw):
                out.append((int(m.group(1)), label))
    return out


def _facts_stale_report_claims(lines, window_text):
    """Family 5 — when a REPORT FROM block claims PR #N is not merged /
    open / pending, and a merge receipt for that same PR #N sits in a
    section ranked more recent (see `_section_recency_rank`), the report
    is stale: the receipt is the later fact and should win over it in the
    judge's reading, not merely sit beside it unremarked (2026-09-18,
    V4-RESIDUE.md change 2, t34). Never fires on the reverse order — a
    receipt in an OLDER section than the report is left alone, since a
    report can legitimately postdate an earlier merge receipt (e.g. a
    revert). The identity guard is PR number: a receipt for #27 never
    settles a report about #28."""
    receipts = {}
    for label, line in lines:
        if not _FACT_MERGE_RECEIPT_RE.search(line):
            continue
        for rx in _FACT_PR_NUM_RES:
            m = rx.search(line)
            if not m:
                continue
            n = int(m.group(1))
            rank = _section_recency_rank(label)
            if n not in receipts or rank > receipts[n][0]:
                receipts[n] = (rank, label)
            break
    if not receipts:
        return []

    reports_by_pr = {}
    for n, label in _report_not_merged_claims(window_text):
        rank = _section_recency_rank(label)
        if n not in reports_by_pr or rank > reports_by_pr[n]:
            reports_by_pr[n] = rank

    facts = []
    for n in sorted(reports_by_pr):
        if n not in receipts:
            continue
        report_rank = reports_by_pr[n]
        receipt_rank, receipt_label = receipts[n]
        if receipt_rank > report_rank:
            facts.append(f"PR #{n}: a merge receipt at {receipt_label} postdates "
                        f"the worker report saying it was not merged; the "
                        f"receipt wins.")
    return facts


def derive_window_facts(window_text, draft_text):
    """The DERIVED FACTS sentences for one gate window, in block order:
    delete/remove claims, cadence claims, result tables, merge/CI claims,
    stale-report-vs-merge-receipt, written-file identity, file read-back,
    labelled-value pairing, score-list membership, claimed extremum.
    Pure: literal string and integer work over `window_text` and
    `draft_text`, no I/O, no model call, never raises. Returns [] when
    nothing is derivable, which is the common case and prints nothing."""
    try:
        lines = _fact_window_lines(window_text)
        if not lines:
            return []
        draft = draft_text or ""
        clauses = presplit_claims(draft) or ([draft.strip()] if draft.strip() else [])
        facts = []
        facts += _facts_delete_claims(lines, clauses)
        facts += _facts_cadence_claims(lines, draft)
        facts += _facts_result_tables(lines, draft)
        facts += _facts_merge_claims(lines, draft)
        facts += _facts_stale_report_claims(lines, window_text)
        facts += _facts_written_file_claims(window_text, draft)
        facts += _facts_read_back_claims(window_text, draft)
        facts += _facts_labelled_value_claims(window_text, draft)
        facts += _facts_score_list_claims(window_text, draft)
        facts += _facts_claimed_extremum_claims(window_text, draft)
        # CONTRADICTED_BY_FACT first, then everything else, each keeping its
        # family order. The cap is what makes this matter: set 2's t36 derives
        # 23 SUPPORTED facts from the result-table and merge families alone,
        # one under the cap of 24 (measured 2026-09-18), so on a slightly
        # busier turn a plain first-come truncation could drop the one fact
        # the deterministic block arm reads and silently turn a block into an
        # allow. Ordering by verdict makes that failure impossible.
        out, seen = [], set()
        for f in _fact_block_reasons(facts) + [f for f in facts
                                               if "CONTRADICTED_BY_FACT" not in f]:
            if f in seen:
                continue
            seen.add(f)
            out.append(f)
            if len(out) >= DERIVED_FACTS_CAP:
                break
        return out
    except Exception:                                  # never break a hook
        return []


def compose_window_with_facts(window_text, draft_text, cap_bytes=None):
    """`window_text` with a DERIVED FACTS block at its HEAD and the raw
    window below it as BACKING. The cap applies AFTER the facts: facts are
    never dropped, and if facts + raw window exceed the cap the raw
    window's HEAD is trimmed (its tail — the current turn — is the part the
    window builder already treats as highest priority). Returns
    (text, facts, meta) where meta carries facts_count/facts/facts_bytes/
    window_trimmed_bytes/guard for `--explain`. A `cap_bytes` of 0 or less
    means NO cap here at all — what the Stop-hook gate path passes, so the
    one cap that runs is `trim_window_to_token_budget` downstream. With no derivable fact the
    redacted window is still returned (byte-for-byte unchanged only when
    nothing secret-shaped was in it).

    Every window is redacted (evidence-guard's `redact`) before it is used
    for anything — deriving facts from it or handing it to the judge —
    which is the Stop-hook gate window's half of the blocklist+redactor
    guard (see the EVIDENCE GUARD section above); `meta["guard"]` carries
    the redaction count for `--explain`."""
    guard = GuardTally()
    window_text = guard.redact(window_text)
    meta_guard = guard.to_dict()
    facts = derive_window_facts(window_text, draft_text) if _derived_facts_enabled() else []
    meta = {"facts_count": len(facts), "facts": list(facts), "facts_bytes": 0,
            "window_trimmed_bytes": 0, "guard": meta_guard}
    if not facts:
        return window_text, facts, meta
    cap = cap_bytes if cap_bytes is not None else _hook_evidence_cap_bytes()
    head = (DERIVED_FACTS_HEADER + "\n"
            + "\n".join(f"- {f}" for f in facts)
            + "\n\n===\n\n" + DERIVED_FACTS_BACKING_HEADER + "\n")
    meta["facts_bytes"] = len(head.encode("utf-8"))
    body = window_text or ""
    raw = body.encode("utf-8")
    if cap <= 0:
        # No cap here. The Stop-hook gate path passes 0 on purpose: this
        # byte trim cuts the window's HEAD, and the head is where the
        # first "[previous turn -1]" marker line lives. Losing it turned
        # 1,630 tokens of t34's window into unlabelled text that the
        # priority trimmer could no longer rank (measured 2026-09-18), so
        # the ONE cap that actually runs is now
        # trim_window_to_token_budget, downstream of this, which trims by
        # whole labelled sections instead.
        return head + body, facts, meta
    budget = cap - meta["facts_bytes"]
    if budget <= 0:
        meta["window_trimmed_bytes"] = len(raw)
        body = ""
    elif len(raw) > budget:
        meta["window_trimmed_bytes"] = len(raw) - budget
        body = raw[-budget:].decode("utf-8", errors="ignore")
    return head + body, facts, meta


# ------------------------------------- one hard window cap, before the call
#
# 2026-09-18. The window was capped in THREE places that did not compose:
# `_derive_evidence_text_from_transcript` capped what it assembled
# (24 KB), the caller then APPENDED a cited-file block of up to 6 KB after
# that cap, and `compose_window_with_facts` re-applied the cap only when
# it had at least one derived fact to put at the head — with no facts it
# returned the over-cap text untouched. Measured on the 2026-09-18 set:
# t38's builder produced 14,289 bytes and the cited-file block added 3,174
# more after the cap check.
#
# This is the ONE cap, enforced once, on the finished text immediately
# before the call, in tokens rather than bytes because tokens are what the
# call is actually priced and timed by. It trims whole sections by
# priority, lowest first:
#
#   1. previous turns, OLDEST first (a fact two turns back is the most
#      replaceable thing in the window)
#   2. session receipts — the backing layer, and by far the fattest on the
#      measured set (12,435 bytes of a 24,576-byte window on all three
#      cases)
#   3. the cited-file tail
#   4. this turn's worker reports
#
# DERIVED FACTS and the CURRENT TURN are never dropped. If those two alone
# still exceed the budget, the current turn's TAIL is kept — the same
# guarantee the byte cap always carried — and the facts are kept whole,
# because a fact is a sentence the judge cannot re-derive from a truncated
# dump.
_WINDOW_PART_RE = re.compile(
    r'^\[(current turn reports|current turn|previous turn -(\d+)|session receipts'
    r'|cited files)\]\s*$')

# Lowest priority first — the order sections are given up in. "other"
# (anything not under a recognised section marker) is deliberately absent:
# the byte-level tail cut upstream can slice a marker line off the head of
# the window, and everything after that point would then look unlabelled.
# Dropping it would risk dropping the current turn, so unlabelled content
# is treated as undroppable and left to the tail-keep at the end.
_WINDOW_TRIM_ORDER = ("previous turn", "session receipts", "cited files",
                      "current turn reports")

_WINDOW_SHRINK_MARKER = "[...head of this section cut to fit the window budget...]"


def _split_window_parts(body):
    """`body` (a window with no DERIVED FACTS head) as an ordered list of
    {"kind", "age", "text"} parts, split at the section markers
    `_WINDOW_PART_RE` recognises. `age` orders previous turns oldest-last
    so the trimmer can drop the furthest-back one first; it is 0 for every
    other kind. Text before the first marker (there should be none) is
    carried as kind "other" so nothing is ever silently lost."""
    parts = []
    cur = {"kind": "other", "age": 0, "lines": []}
    for line in body.split("\n"):
        m = _WINDOW_PART_RE.match(line)
        if m:
            if cur["lines"]:
                parts.append(cur)
            kind = m.group(1)
            age = int(m.group(2)) if m.group(2) else 0
            if kind.startswith("previous turn"):
                kind = "previous turn"
            cur = {"kind": kind, "age": age, "lines": [line]}
        else:
            cur["lines"].append(line)
    if cur["lines"]:
        parts.append(cur)
    out = []
    for part in parts:
        text = "\n".join(part["lines"]).strip("\n")
        # A bare "===" separator line left behind by a join is not content.
        text = "\n".join(l for l in text.split("\n")
                         if not _SECTION_SEPARATOR_RE.match(l) or l.strip() == "")
        if text.strip():
            out.append({"kind": part["kind"], "age": part["age"], "text": text.strip("\n")})
    return out


def _shrink_window_part(text, target_tok):
    """`text` cut down to roughly `target_tok`, keeping its MARKER line and
    its TAIL — the same shape `_build_prev_turns_block_detailed` and
    `_build_reports_block` already use when a single block is over budget,
    and for the same reason: the freshest end of a section is the part
    worth keeping, and a section with no header can no longer be
    identified by the count arm's identity scoping."""
    lines = text.split("\n")
    header = lines[0] if lines else ""
    body = "\n".join(lines[1:])
    room = max(target_tok * 4 - len(header) - len(_WINDOW_SHRINK_MARKER) - 2, 0)
    if room <= 0:
        return header
    return header + "\n" + _WINDOW_SHRINK_MARKER + "\n" + body[-room:]


def _gate_window_tok():
    """The one hard window cap in tokens (SUPERJEV_GATE_WINDOW_TOK,
    default 8000). Zero or negative disables the cap entirely."""
    try:
        return int(os.environ.get(GATE_WINDOW_TOK_ENV, DEFAULT_GATE_WINDOW_TOK))
    except (TypeError, ValueError):
        return DEFAULT_GATE_WINDOW_TOK


def trim_window_to_token_budget(text, budget_tok=None):
    """`text` held under ONE token budget, enforced immediately before the
    call. Returns (text, meta) where meta carries tok_before/tok_after/
    budget_tok/dropped (the section labels removed, in the order they were
    removed), shrunk (the one section, if any, that paid the overflow out
    of its own head instead of being dropped whole) and
    current_trimmed_chars.

    Drop order is `_WINDOW_TRIM_ORDER`, previous turns oldest-first within
    their own kind. The DERIVED FACTS head and the `[current turn]`
    section are never dropped; if they alone exceed the budget, the
    current turn keeps its TAIL. A budget of zero or less returns the text
    untouched."""
    budget = _gate_window_tok() if budget_tok is None else budget_tok
    text = text or ""
    meta = {"tok_before": _estimate_tokens(text), "tok_after": _estimate_tokens(text),
            "budget_tok": budget, "dropped": [], "shrunk": [],
            "current_trimmed_chars": 0}
    if budget <= 0 or meta["tok_before"] <= budget:
        return text, meta

    head = ""
    body = text
    if text.startswith(DERIVED_FACTS_HEADER):
        marker = DERIVED_FACTS_BACKING_HEADER + "\n"
        idx = text.find(marker)
        if idx != -1:
            head = text[:idx + len(marker)]
            body = text[idx + len(marker):]

    parts = _split_window_parts(body)

    def assemble(kept):
        return head + "\n\n===\n\n".join(p["text"] for p in kept)

    kept = list(parts)
    for kind in _WINDOW_TRIM_ORDER:
        # Oldest previous turn first; every other kind has a single part.
        victims = sorted([p for p in kept if p["kind"] == kind],
                         key=lambda p: -p["age"])
        for victim in victims:
            over = _estimate_tokens(assemble(kept)) - budget
            if over <= 0:
                break
            label = (f"previous turn -{victim['age']}" if victim["kind"] == "previous turn"
                    else victim["kind"])
            # SHRINK before DROP. Giving up a whole 3,000-token receipts
            # block to save 200 tokens throws away evidence the budget
            # never asked for, and evidence missing from the window is how
            # a true reply gets flagged NOT_SUPPORTED. So a section that
            # can pay the overflow out of its own head does exactly that
            # and the trimming stops there; only a section too small to
            # cover it is dropped whole.
            size = _estimate_tokens(victim["text"])
            if size > over:
                victim["text"] = _shrink_window_part(victim["text"], size - over)
                meta["shrunk"].append(label)
                break
            kept.remove(victim)
            meta["dropped"].append(label)
        if _estimate_tokens(assemble(kept)) <= budget:
            break

    out = assemble(kept)
    if _estimate_tokens(out) > budget:
        # Facts + the current turn alone are over. Keep the facts whole and
        # the current turn's TAIL, same guarantee the byte cap carried.
        room_chars = max(budget * 4 - len(head), 0)
        rest = "\n\n===\n\n".join(p["text"] for p in kept)
        if room_chars <= 0:
            meta["current_trimmed_chars"] = len(rest)
            out = head
        else:
            meta["current_trimmed_chars"] = max(len(rest) - room_chars, 0)
            out = head + rest[-room_chars:]
    meta["tok_after"] = _estimate_tokens(out)
    return out, meta


# ------------------------------------------------------------- cited-file tail
#
# 2026-09-18, SET2-AUDIT.md recommendation (b): when the current-turn draft
# names its own source ("per SUMMARY.md", "per the summary log", "in
# stress-20260917/results/SUMMARY.md"), the window built from transcript
# tool_results may never have read that file at all — t38's window carried
# 0 bytes about the draft's four numbers while the cited SUMMARY.md carried
# all of them. This resolves a citation to a real file — an absolute/
# user-relative path if the draft names one, else a unique basename match
# under a small set of known roots — reads its tail, redacts it the same
# as the rest of the window, and hands back a labelled block for the
# caller to fold into the window before the 24 KB cap is applied. This is
# evidence the judge gets to see, same as any other evidence line — it
# never asserts the citation's contents as true on the draft's say-so.
# Skips a candidate silently (no note, no error) when it does not resolve,
# is ambiguous, or is blocklisted; a missing citation is not itself a
# finding.
CITED_FILE_MAX_LINES = 40
CITED_FILE_MAX_BYTES = 3072          # 3 KB cap per cited file
CITED_FILE_MAX_FILES = 2             # at most this many citations resolved per draft
CITED_FILE_EXTS = (".md", ".txt", ".log", ".json")

# An explicit path or bare filename with a recognised extension —
# "stress-20260917/results/SUMMARY.md", "~/notes/log.txt", "SUMMARY.md".
_CITED_FILE_PATH_RE = re.compile(
    r'(?:^|[\s(`"\'])((?:~|\.{1,2})?/[\w./\-]+\.(?:md|txt|log|json)'
    r'|[\w][\w.\-]*\.(?:md|txt|log|json))\b', re.IGNORECASE)
# "per the summary log" / "per the report log" — no filename, just a
# keyword to look up against a known root's file names.
_CITED_FILE_LOG_PHRASE_RE = re.compile(
    r'\bper\s+(?:the\s+)?([a-z][a-z0-9 \-]{2,40}?)\s+log\b', re.IGNORECASE)
# "per the summary log" already yields keyword 'summary' above; "in the
# summary" names the same file with no trailing "log" at all. Both map to
# the same stem hint 'SUMMARY' (2026-09-18, V4-RESIDUE.md change 1) — a
# dotless keyword still resolved by _resolve_cited_basename's
# substring-of-stem match, same as any other keyword candidate.
_CITED_FILE_STEM_HINT_RE = re.compile(r'\b(?:per|in)\s+the\s+(summary)\b', re.IGNORECASE)
_CITED_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def _cited_file_candidates(draft_text):
    """Ordered, deduped list of (kind, value) citation candidates in
    `draft_text` — ('path', 'stress-20260917/results/SUMMARY.md') for an
    explicit path/filename, ('keyword', 'summary') for a "per the X log"
    phrase with no filename. Never raises."""
    if not draft_text:
        return []
    out, seen = [], set()
    for m in _CITED_FILE_PATH_RE.finditer(draft_text):
        val = m.group(1)
        key = ("path", val.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(("path", val))
    for m in _CITED_FILE_LOG_PHRASE_RE.finditer(draft_text):
        kw = " ".join(m.group(1).split()).lower()
        key = ("keyword", kw)
        if key in seen:
            continue
        seen.add(key)
        out.append(("keyword", kw))
    if _CITED_FILE_STEM_HINT_RE.search(draft_text):
        key = ("keyword", "summary")
        if key not in seen:
            seen.add(key)
            out.append(("keyword", "SUMMARY"))
    return out


def _cited_file_roots():
    """Known roots to search for a cited file's unique basename match: the
    process cwd, this repo's root, and ~/super-jev-experiments (where the
    fleet's gate-bench and stress logs live) — never the whole
    filesystem."""
    roots = []
    try:
        roots.append(Path(os.getcwd()))
    except OSError:
        pass
    roots.append(REPO_ROOT)
    roots.append(HOME / "super-jev-experiments")
    out, seen = [], set()
    for r in roots:
        try:
            rp = r.resolve()
        except OSError:
            continue
        if rp in seen or not rp.is_dir():
            continue
        seen.add(rp)
        out.append(rp)
    return out


def _resolve_cited_path(value):
    """An absolute/user-relative path candidate resolved to a real,
    readable, non-blocklisted file, or None. Never a directory, never a
    bare relative filename (those go through `_resolve_cited_basename`
    instead, since a relative path is meaningless without a cwd the
    citation itself does not name)."""
    p = Path(os.path.expanduser(value))
    if not p.is_absolute():
        return None
    if is_blocked_path(p):
        return None
    try:
        if p.is_file():
            return p
    except OSError:
        pass
    return None


def _cited_path_appears_in_text(path, text, roots):
    """True when `path` (or its form relative to one of `roots`) appears
    literally, as a substring, in `text` — the case where the draft's own
    session already named or wrote this exact file (a bash command's
    target, a tool-result line), so there is nothing to guess. Never
    raises."""
    if not text:
        return False
    s = str(path)
    if s in text:
        return True
    for root in roots:
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if str(rel) in text:
            return True
    return False


def _pick_cited_candidate(candidates, basename, roots, window_hint_text=None):
    """One real path out of `candidates` (deduped, order preserved), or
    None when the tie cannot be broken. Tie-break order, each step only
    applied when the prior step still leaves more than one candidate
    (2026-09-18, V4-RESIDUE.md change 1):

      1. A candidate whose path (in full, or relative to a known root)
         appears literally in `window_hint_text` — the file this session's
         own window already shows was read or written (a bash command's
         own target, e.g.), so no guess is needed.
      2. A candidate whose stem is an EXACT case-insensitive match for
         `basename` (stripped of its extension, when it has one) rather
         than merely containing it as a substring.
      3. A candidate with a preferred extension: .md, then .log, then
         .txt, then .json.

    Still ambiguous after all three steps returns None, same as before —
    this narrows the tie, it never invents a match with no evidence."""
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    if window_hint_text:
        literal = [c for c in candidates
                  if _cited_path_appears_in_text(c, window_hint_text, roots)]
        if literal:
            candidates = literal
            if len(candidates) == 1:
                return candidates[0]

    stem_target = (Path(basename).stem if "." in basename else basename).lower()
    exact_stem = [c for c in candidates if c.stem.lower() == stem_target]
    if exact_stem:
        candidates = exact_stem
        if len(candidates) == 1:
            return candidates[0]

    ext_priority = {".md": 0, ".log": 1, ".txt": 2, ".json": 3}
    ranked = sorted(candidates, key=lambda c: ext_priority.get(c.suffix.lower(), 9))
    best_rank = ext_priority.get(ranked[0].suffix.lower(), 9)
    best = [c for c in ranked if ext_priority.get(c.suffix.lower(), 9) == best_rank]
    if len(best) == 1:
        return best[0]
    return None  # still ambiguous — skip silently, same as before


def _resolve_cited_basename(basename, roots, max_scan=20000, window_hint_text=None):
    """The one file under `roots` whose name matches `basename` — an exact
    case-insensitive filename match when `basename` looks like a real
    filename (has a dot), else a substring-of-stem match against
    CITED_FILE_EXTS. Returns None on zero or still-ambiguous (see
    `_pick_cited_candidate`) matches, or once `max_scan` files have been
    walked, so a huge tree cannot stall a hook. `.bak*` files are always
    skipped. Never raises."""
    exact = "." in basename
    target = basename.lower()
    candidates = []
    scanned = 0
    for root in roots:
        try:
            walker = os.walk(root)
        except OSError:
            continue
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [d for d in dirnames if d not in _CITED_SKIP_DIRS
                          and not d.startswith(".")]
            for fn in filenames:
                scanned += 1
                if scanned > max_scan:
                    return _pick_cited_candidate(candidates, basename, roots,
                                                 window_hint_text)
                low = fn.lower()
                if ".bak" in low:
                    continue
                is_match = (low == target if exact else
                           (Path(low).suffix in CITED_FILE_EXTS and
                            target in Path(low).stem.lower()))
                if not is_match:
                    continue
                full = Path(dirpath) / fn
                if is_blocked_path(full):
                    continue
                try:
                    real = full.resolve()
                except OSError:
                    real = full
                if real not in candidates:
                    candidates.append(real)
    return _pick_cited_candidate(candidates, basename, roots, window_hint_text)


def _read_file_tail(path, max_lines=CITED_FILE_MAX_LINES, max_bytes=CITED_FILE_MAX_BYTES):
    """The last `max_lines` lines of `path`, capped at `max_bytes` (the
    tail is kept if still over budget). None on any read problem."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    lines = text.splitlines()
    tail = "\n".join(lines[-max_lines:])
    raw = tail.encode("utf-8")
    if len(raw) > max_bytes:
        raw = raw[-max_bytes:]
        tail = raw.decode("utf-8", errors="ignore")
    return tail


def build_cited_file_block(draft_text, window_text=None):
    """A "[cited files]" window section for every file the draft names by
    path or by a recognisable "per <name> log" / "in the summary" phrase
    that resolves to a real, readable, non-blocklisted file — its tail,
    labelled `CITED FILE <path> (tail)`, redacted the same as the rest of
    the window (see EVIDENCE GUARD above). Skips a candidate silently
    (never a note, never an error) when it does not resolve, is still
    ambiguous after the tie-break in `_pick_cited_candidate`, or is
    blocklisted. Returns "" when nothing resolves. At most
    CITED_FILE_MAX_FILES files, each capped at CITED_FILE_MAX_BYTES — the
    caller still folds the result into the overall 24 KB window cap same
    as every other section.

    `window_text`, when given, is the evidence window already assembled
    for this turn (tool results, receipts, reports) — passed through to
    the ambiguity tie-break so a candidate this session's own commands
    already read or wrote (its path appears literally in that text) wins
    over a same-named file the session never touched (2026-09-18,
    V4-RESIDUE.md change 1, t38)."""
    candidates = _cited_file_candidates(draft_text)
    if not candidates:
        return ""
    roots = _cited_file_roots()
    blocks = []
    seen_paths = set()
    for kind, value in candidates:
        if len(blocks) >= CITED_FILE_MAX_FILES:
            break
        path = None
        if kind == "path":
            path = _resolve_cited_path(value)
            if path is None:
                basename = os.path.basename(value)
                if basename:
                    path = _resolve_cited_basename(basename, roots,
                                                    window_hint_text=window_text)
        else:
            path = _resolve_cited_basename(value, roots, window_hint_text=window_text)
        if path is None:
            continue
        if is_blocked_path(path):
            continue
        try:
            real = path.resolve()
        except OSError:
            real = path
        if real in seen_paths:
            continue
        tail = _read_file_tail(path)
        if not tail or not tail.strip():
            continue
        seen_paths.add(real)
        tail = redact(tail)
        blocks.append(f"CITED FILE {path} (tail)\n{tail}")
    if not blocks:
        return ""
    return "[cited files]\n" + "\n\n---\n\n".join(blocks)


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


def _pr_url_from_worktree(worktree, pr_num, timeout=None):
    """https://github.com/<org>/<repo>/pull/<pr_num>, built from `git -C
    worktree remote get-url origin`, or None if there is no worktree, no
    PR number, no origin remote, or the remote is not a recognisable
    GitHub URL. worker-verify's own atom extraction only turns a FULL
    GitHub pull URL into a `gh pr view` evidence block (verify.py's
    _PR_URL regex; a bare PR number in the text is not enough) — this is
    the only thing that makes a --pr number produce real PR evidence."""
    if not worktree or not pr_num:
        return None
    # Clamped to whatever is left of the Stop event's budget when one is in
    # play: a git call against a network remote can hang, and this one runs
    # before the verify check on the Stop-scan path.
    git_timeout = 10 if timeout is None else max(min(10, timeout), 0.0)
    try:
        proc = subprocess.run(["git", "-C", str(worktree), "remote", "get-url", "origin"],
                              capture_output=True, text=True, timeout=git_timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    m = re.search(r'github\.com[:/]+([\w.\-]+)/([\w.\-]+?)(?:\.git)?/?$', proc.stdout.strip())
    if not m:
        return None
    return f"https://github.com/{m.group(1)}/{m.group(2)}/pull/{pr_num}"


def _evidence_probe(report_path, worktree=None, test_cmd="", timeout=None):
    """A FREE gather: worker-verify's own `--dry-run`, which assembles every
    evidence block and prints "EVIDENCE: N blocks, M chars" plus each
    block, and never calls the model. Returns its captured stdout, or ""
    when the probe could not run.

    It is only ever run when the answer changes something — a block that
    would rest on OVERCLAIMS alone, or an explicit --explain — so the
    common path still pays for exactly one gather.

    FREE of model calls is not free of TIME. The gather re-runs whatever
    `--test-cmd` was derived from the report, under the verify door's own
    ceiling, which is minutes — so on the Stop path this is a second long
    wait sitting behind the verify call that already ran the same command
    once. `timeout` is the Stop event's remaining wall-clock budget (see
    StopBudget.timeout_for); without it this probe was the one long-timeout
    wait on that path the 2026-09-18 budget did not reach."""
    try:
        ns = argparse.Namespace(report=report_path, worktree=worktree,
                                test_cmd=test_cmd or "", paths=[], dry_run=True,
                                json=False, hook_mode=True, timeout=timeout)
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
        print(f"  derived facts     : {m.get('facts_count', 0)} at the HEAD of the "
              f"window, {m.get('facts_bytes', 0)} bytes (never dropped)"
              + (f", {m.get('window_trimmed_bytes', 0)} bytes of raw window "
                 "trimmed to fit under the cap"
                 if m.get('window_trimmed_bytes') else ""))
        for f in m.get("facts") or []:
            print(f"    fact            : {f}")
        g = m.get("guard") or {}
        print(f"  evidence guard    : {g.get('paths_skipped', 0)} path(s) skipped, "
              f"{g.get('redactions', 0)} redaction(s) made over this window")
        b = m.get("window_budget") or {}
        if b:
            print(f"  window budget     : {b.get('budget_tok', 0)} tok "
                  f"({GATE_WINDOW_TOK_ENV}) — {b.get('tok_before', 0)} tok before, "
                  f"{b.get('tok_after', 0)} tok after")
            if b.get("dropped"):
                print("    dropped         : " + ", ".join(b["dropped"])
                      + " (lowest priority first; DERIVED FACTS and the current "
                        "turn are never dropped)")
            if b.get("shrunk"):
                print("    shrunk          : " + ", ".join(b["shrunk"])
                      + " (head cut, tail kept — paid the overflow rather than "
                        "being dropped whole)")
            if b.get("current_trimmed_chars"):
                print(f"    current turn    : tail kept, {b['current_trimmed_chars']} "
                      "chars cut (facts + current turn alone exceeded the budget)")
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
    verdict stand in for "nothing was checked". Every REAL verdict this
    reaches (allow/block/advisory, after the door actually ran) also gets
    a catch-ledger record, door="verify", the same as a live PostToolUse
    verify hook — the early fail-open returns above (wrong door, unreadable
    file, empty file, no evidence source) do not, same as every other
    fail-open path in this file."""
    _catch_t0 = time.monotonic()
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
    _catch_payload = {"door": "verify", "source": "from-file", "path": path, "report": text}
    if action == "allow":
        _hook_log(f"verify: allow (exit {code}) [from-file {path!r}]", exit_code=0,
                 flags=flags, hook_mode=False, source="manual")
        catch_log("verify", "allow", reasons=notes, draft_text=text,
                 start_time=_catch_t0, payload=_catch_payload)
        return 0
    if action == "block":
        reason = f"super-jev verify blocked this (exit {code})"
        if block_reasons:
            reason += ": " + "; ".join(block_reasons)
        if notes:
            reason += " (advisory: " + "; ".join(notes) + ")"
        print(reason, file=sys.stderr)
        _hook_log(f"verify: block (exit {code}) [from-file {path!r}]" +
                 (f" — strong flags: {'; '.join(block_reasons)}" if block_reasons else "") +
                 (f" — advisory: {'; '.join(notes)}" if notes else ""),
                 exit_code=2, flags=flags, hook_mode=False, source="manual")
        catch_log("verify", "block", reasons=block_reasons + notes, draft_text=text,
                 start_time=_catch_t0, payload=_catch_payload)
        return 2
    advisory = f"super-jev verify advisory (exit {code}){note_tail}"
    print(advisory)
    _hook_log(f"verify: advisory (exit {code}) [from-file {path!r}]{note_tail}",
             exit_code=0, flags=flags, hook_mode=False, source="manual")
    catch_log("verify", "advisory", reasons=notes, draft_text=text,
             start_time=_catch_t0, payload=_catch_payload)
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
        global _ACTIVE_HOOK_PAYLOAD
        _ACTIVE_HOOK_PAYLOAD = payload
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

            _catch_t0 = time.monotonic()
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
            # One catch-ledger record per teammate verdict — CLEAN/READ/REJECT
            # map onto the same allow/advisory/block vocabulary every other
            # door's catch record uses.
            _catch_decision = {"CLEAN": "allow", "READ": "advisory",
                               "REJECT": "block"}.get(label, "advisory")
            catch_log("prompt-verify", _catch_decision, reasons=block_reasons,
                     draft_text=report_text, start_time=_catch_t0,
                     payload={"door": "prompt-verify", "teammate_id": teammate_id,
                              "report": report_text})
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


def _stop_scan_max_bytes():
    """How much of the transcript's TAIL the teammate-report scan reads
    (SUPERJEV_STOP_SCAN_MAX_BYTES, default 2 MB). Zero or negative reads
    all of it, the pre-2026-09-18 behaviour."""
    try:
        return int(os.environ.get(STOP_SCAN_MAX_BYTES_ENV, DEFAULT_STOP_SCAN_MAX_BYTES))
    except (TypeError, ValueError):
        return DEFAULT_STOP_SCAN_MAX_BYTES


def _stop_scan_min_budget_s():
    """The scan will not START a live verify call with less than this many
    seconds of the Stop event's budget left (SUPERJEV_STOP_SCAN_MIN_S,
    default 20). Starting a call we would only have to kill costs a real
    TypeSafe call and buys nothing — the report defers to the next Stop
    event instead, exactly as it already does on a timeout."""
    try:
        return float(os.environ.get(STOP_SCAN_MIN_BUDGET_S_ENV,
                                    DEFAULT_STOP_SCAN_MIN_BUDGET_S))
    except (TypeError, ValueError):
        return DEFAULT_STOP_SCAN_MIN_BUDGET_S


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
        records = _read_transcript_records(transcript_path,
                                           max_bytes=_stop_scan_max_bytes())
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


def _stop_scan_verify_one(r, budget=None):
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
        pr_url = (_pr_url_from_worktree(
                     derived["worktree"], derived["pr"],
                     timeout=(budget.remaining() if budget is not None else None))
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
                                    dry_run=False, json=False, hook_mode=True,
                                    timeout=(budget.timeout_for(_verify_timeout())
                                             if budget is not None else None))
            code, door_out, door_err = cmd_verify(ns)

            flags = _parse_strong_flags(door_out)
            claim_rows = _parse_claim_rows(door_out)
            evidence = _evidence_inventory(test_cmd=derived["test_cmd"] or "",
                                           worktree=derived["worktree"], pr=derived["pr"])
            line = _block_confidence_line()
            has_candidate = any(f["verdict"] in _BLOCKABLE_VERDICTS and f["score"] >= line
                                for f in flags)
            if has_candidate:
                probe = _evidence_probe(
                    tmp_path, derived["worktree"], derived["test_cmd"] or "",
                    timeout=(budget.timeout_for(_verify_timeout())
                             if budget is not None else None))
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
        health = "thin" if evidence.get("thin") else "ok"
        if block_reasons:
            label = "REJECT"
        elif label == "REJECT" and health == "thin":
            # Same hole as the live PostToolUse verify hook (see
            # gate-adjudication-20260918.md): worker-verify's own exit
            # code alone said REJECT, but every flag this run actually
            # parsed was suppressed into `notes` because the gather had
            # nothing usable — 45 of 59 REJECT labels in the 2026-09-18
            # adjudication ran exactly this way. A bare exit code over
            # evidence that was never gathered is "we could not check",
            # never "we checked and it failed" — never a REJECT label.
            label = "UNCHECKED"
        flag_str = ("; ".join(f"{f['key']} {f['verdict']} {f['score']:.2f}" for f in flags)
                   or "no flags")
        used = ", ".join(f"--{k} {v}" for k, v in
                         (("worktree", derived["worktree"]),
                          ("test-cmd", derived["test_cmd"]),
                          ("pr", derived["pr"])) if v) or "no evidence derived"
        print(f"super-jev verify {teammate_id}: {label} — {flag_str} — {used} — health {health}")
        note_tail = (" — " + "; ".join(notes)) if notes else ""
        _hook_log(f"stop-scan: {teammate_id} — {label} (exit {code}) [{used}] "
                 f"health={health}{note_tail}", exit_code=0, skipped=False, flags=flags,
                 hook_mode=True, source="stop-transcript",
                 unchecked=(label == "UNCHECKED"),
                 health=("none" if label == "UNCHECKED" else health),
                 reason=("no-evidence" if label == "UNCHECKED" else None))
        if label == "UNCHECKED":
            catch_log("verify", "unchecked", reasons=["no-evidence"] + (notes or []),
                     draft_text=report_text, payload=None)
    except Exception as exc:
        print(f"super-jev verify {teammate_id}: ERROR — {exc.__class__.__name__} (advisory)")
        _hook_log(f"stop-scan: {teammate_id} — error ({exc.__class__.__name__}), "
                 "advisory-only", skipped=True, reason="stop-scan-error",
                 source="stop-transcript")


def _hook_stop_scan_teammate_reports(payload, budget=None):
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
        min_start = _stop_scan_min_budget_s()
        for r in reports:
            if time.monotonic() > deadline:
                timed_out = True
                _hook_log("stop-scan: time budget exceeded — remaining report(s) "
                         "deferred to the next Stop event", skipped=True,
                         reason="stop-scan-timeout", source="stop-transcript")
                break
            # The Stop event's OWN budget, which this advisory scan shares
            # with the gate verdict and never outranks. Two independent
            # refusals, both deferrals:
            #   - no live call left (SUPERJEV_GATE_MAX_CALLS, default 1 —
            #     the gate has it), so this scan makes none;
            #   - too little wall clock left to finish one honestly, so we
            #     do not start a call only to kill it.
            # A verify check here can take many times the whole event's
            # budget, which is the entire reason this guard exists.
            if budget is not None:
                remaining = budget.remaining()
                # Ask for the call BEFORE looking at the clock. At the
                # defaults the allowance is the real and structural reason
                # this scan defers (SUPERJEV_GATE_MAX_CALLS=1, and the gate
                # reserved it), so naming the clock first told the reader
                # the budget ran out when no time had been spent at all.
                # claim_call() takes nothing when it returns False.
                got_call = budget.claim_call(reserve=1)
                short = remaining is not None and remaining < min_start
                if not got_call or short:
                    timed_out = True
                    why = (f"no live call left under {GATE_MAX_CALLS_ENV} once the "
                           "gate's own verdict is reserved"
                           if not got_call else
                           f"only {remaining:.1f}s left, under "
                           f"{STOP_SCAN_MIN_BUDGET_S_ENV}={min_start:g}s")
                    # "deferred", never "not judged": the reply itself is
                    # judged by the gate on this same event. Only these
                    # advisory worker-report checks move to the next one.
                    _hook_log(f"stop-scan: deferred, not dropped ({why}) — "
                             "remaining report(s) retried on the next Stop event; "
                             "this turn's own reply is judged by the gate as usual",
                             skipped=True, reason=SCAN_DEFERRED_REASON,
                             source="stop-transcript")
                    break
            _stop_scan_verify_one(r, budget=budget)
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
    _catch_t0 = time.monotonic()
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

    global _ACTIVE_HOOK_PAYLOAD
    _ACTIVE_HOOK_PAYLOAD = payload

    evidence_tmp_path = None

    def _hook_unchecked(tp, evidence, tmp_ev_prompt_found):
        """The no-tool-evidence path: run the gate against the user's last
        prompt, advise once if the door reports a checkable claim, stay
        silent if it reports none, log unchecked=True, exit 0 always.

        This is still the gate's ONE call for the event (it claims the
        allowance and clamps its timeout to the remaining budget the same
        way the normal path does), so "unchecked" never costs an extra
        one."""
        if budget.expired() or not budget.claim_call():
            print(BUDGET_EXCEEDED_ADVISORY)
            _hook_log("gate: budget exceeded, not judged (no tool evidence, and the "
                      f"budget was spent after {budget.elapsed():.1f}s) — advisory, "
                      "the reply was NOT checked", exit_code=3, skipped=True,
                      reason=BUDGET_EXCEEDED_REASON)
            catch_log("gate", "unchecked", reasons=[BUDGET_EXCEEDED_REASON],
                     draft_text=text, window_bytes=None, start_time=_catch_t0,
                     payload=payload)
            return 3
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                          encoding="utf-8")
        tmp_path = tmp.name
        try:
            tmp.write(text)
            tmp.close()
            ns = argparse.Namespace(evidence=evidence, draft=tmp_path, claim=None,
                                    json=False, hook_mode=True,
                                    timeout=budget.timeout_for(_gate_timeout()))
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
                      flags=_parse_strong_flags(door_out), unchecked=True, health="none",
                      reason="no-tool-evidence-checkable")
        else:
            _hook_log(f"gate: unchecked — no tool evidence derivable from transcript_path "
                      f"({tp!r}); gate ran against {source}, no checkable claim "
                      f"(exit {code}), silent", exit_code=0, unchecked=True, health="none",
                      reason="no-tool-evidence-silent")
        # This IS the 2026-09-16 bug's own shape (no tool evidence
        # derivable, silently routed unchecked) — so this path in
        # particular always checks the running share, and prints the
        # notice even on the otherwise-silent branch above, since silent
        # unchecked runs are exactly what let 9 of 40 go unnoticed.
        notice = _running_unchecked_notice()
        if notice:
            print(notice)
        # Computed only here, AFTER both the advisory print above and this
        # notice — a raise from _parse_strong_flags on a malformed door_out
        # must never be able to swallow either print (see N6/brutal review):
        # this whole block runs strictly after them now, not before.
        _catch_unchecked_flags = _parse_strong_flags(door_out) or []
        _catch_unchecked_reasons = [f"{f.get('key')} {f.get('verdict')} {f.get('score')}"
                                    for f in _catch_unchecked_flags]
        catch_log("gate", "unchecked", reasons=_catch_unchecked_reasons,
                 draft_text=text, window_bytes=None, start_time=_catch_t0,
                 payload=payload)
        return 0

    budget = StopBudget()
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
            _hook_stop_scan_teammate_reports(payload, budget=budget)
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
                    # Cited-file tail (see build_cited_file_block, 2026-09-18
                    # SET2-AUDIT.md recommendation (b)): when the draft names
                    # its own source ("per SUMMARY.md"), fold that file's
                    # tail into the window at highest priority — appended
                    # after the current turn, so compose_window_with_facts'
                    # head-first trim never drops it before the current
                    # turn's own material.
                    cited_block = build_cited_file_block(text, window_text=derived)
                    if cited_block:
                        derived = (derived + "\n\n===\n\n" + cited_block
                                  if derived else cited_block)
                        if window_meta is not None:
                            window_meta["cited_file_bytes"] = len(
                                cited_block.encode("utf-8"))
                    # DERIVED FACTS at the head of the window (see
                    # derive_window_facts): the refuting string was already
                    # in the window on every one of the 2026-09-17 bench's
                    # remaining misses; all that was missing was stating it
                    # as a sentence instead of leaving it as a column in a
                    # card.py dump. Facts first, raw window below as
                    # BACKING, cap applied after the facts.
                    # cap_bytes=0: no trim HERE (see compose_window_with_facts)
                    # — the single cap is trim_window_to_token_budget on the
                    # next line, which cuts whole labelled sections in
                    # priority order rather than slicing bytes off the head.
                    derived, _facts, _fmeta = compose_window_with_facts(
                        derived, text, cap_bytes=0)
                    # THE cap — one budget, in tokens, enforced here and
                    # nowhere else, on the finished text right before the
                    # call. Everything above (the builder's byte cap, the
                    # cited-file append, the facts head) may compose to
                    # something over budget; this is what makes that
                    # impossible to ship. See trim_window_to_token_budget.
                    derived, _tmeta = trim_window_to_token_budget(derived)
                    if window_meta is not None:
                        window_meta.update(_fmeta)
                        window_meta["window_budget"] = _tmeta
                        window_meta["total_bytes"] = len(derived.encode("utf-8"))
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

            # The gate's own verdict has FIRST claim on this event's one
            # judge call and on what is left of its wall clock. If either
            # is already gone before we start — the window build alone can
            # eat it on an 11 MB transcript — say so out loud rather than
            # printing something that reads like an allow.
            if budget.expired() or not budget.claim_call():
                why = ("wall-clock" if budget.expired() else
                       f"{GATE_MAX_CALLS_ENV} call")
                print(BUDGET_EXCEEDED_ADVISORY)
                _hook_log(f"gate: budget exceeded, not judged ({why} budget spent "
                          f"after {budget.elapsed():.1f}s) — advisory, the reply was "
                          "NOT checked", exit_code=3, skipped=True,
                          reason=BUDGET_EXCEEDED_REASON)
                _budget_notice = _running_unchecked_notice()
                if _budget_notice:
                    print(_budget_notice)
                catch_log("gate", "unchecked", reasons=[BUDGET_EXCEEDED_REASON],
                         draft_text=text, window_bytes=None, start_time=_catch_t0,
                         payload=payload)
                return 3
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                              encoding="utf-8")
            tmp_path = tmp.name
            try:
                tmp.write(text)
                tmp.close()
                ns = argparse.Namespace(evidence=evidence, draft=tmp_path, claim=None,
                                        json=False, hook_mode=True,
                                        timeout=budget.timeout_for(_gate_timeout()))
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
            # A derived fact the window already carries (any family —
            # written-file identity, removal, missing path, diffstat,
            # count, PR-state, stale-report, ...) that came back
            # CONTRADICTED_BY_FACT joins the same deterministic list, for
            # the same reason the two above do: it is literal string/int
            # work over text that WAS in the window, so it needs no judge
            # and no health gate. SUPPORTED/CHECKED/RESIDUE/advisory facts
            # are filtered out by _fact_block_reasons and never block or
            # veto another arm. See SET3-LIVE-GAP.md.
            det_block_reasons += _fact_block_reasons((window_meta or {}).get("facts"))
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
                    print("super-jev verify: spawn ack, nothing to judge", file=sys.stderr)
                    _hook_log(
                        "verify: skipped — tool_response is a spawn/launch dict"
                        + (f" (status={status!r})" if status else "") +
                        "; the worker's own report is not here yet, it arrives later "
                        "in a <teammate-message> block (see `hook prompt-verify`)",
                        exit_code=0, skipped=True, reason="spawn-dict")
                    catch_log("verify", "unchecked", reasons=["spawn-ack"],
                             draft_text=None, start_time=_catch_t0, payload=payload)
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
                print("super-jev verify: spawn ack, nothing to judge", file=sys.stderr)
                _hook_log(f"verify: skipped — {ack_reason}", exit_code=0, skipped=True,
                         reason="launch-ack")
                catch_log("verify", "unchecked", reasons=["spawn-ack"],
                         draft_text=text, start_time=_catch_t0, payload=payload)
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
        # that every claim was in fact supported. A hook-driven verify gets
        # no --test-cmd/--pr (see the hardcoded test_cmd="" above), so it
        # can NEVER prove a test-count claim — docs/hooks.md says so in
        # plain words — but it DOES know whether it had a worktree to look
        # at, which is exactly what gather_health below checks.
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
        elif door == "verify":
            # 2026-09-18 fix (gate-adjudication-20260918.md, verify door):
            # `hook verify --from-file` and the Stop-scan both already
            # compute `_evidence_inventory` and feed it in here so a thin
            # gather suppresses a block into an advisory note instead of
            # letting it through — the live PostToolUse hook never did,
            # so it treated "nothing was gathered" as "healthy" and let a
            # bare exit code stand in for a real judgement. `worktree` is
            # the only evidence source this path ever has (test_cmd/pr are
            # never set on a real hook payload), so this is deliberately
            # narrower than the from-file/probe version — no --dry-run
            # probe is spent here, just the presence/absence check.
            gather_health = _evidence_inventory(test_cmd="", worktree=worktree, pr=None)
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
        elif (door == "verify" and action == "block" and gather_health is not None
              and not _gather_healthy(gather_health)):
            # The door's own exit code alone implied a block, but nothing
            # this run actually parsed crossed the block line —
            # _hook_block_decision already suppressed every flag into
            # block_notes because the gather itself had nothing usable (no
            # worktree, or the probe came back empty/unreadable). Blocking
            # on a bare exit code over evidence that was never gathered
            # mistakes "we could not check" for "we checked and it
            # failed" — see gate-adjudication-20260918.md, verify door,
            # rows 00:39:08 and 01:03:59 (the SAME report came back READ
            # once --worktree/--test-cmd/--pr gave it something to gather
            # against). Advisory, not a block; never judged.
            action = "unchecked-no-evidence"

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
        # Judge-advisory mode (SUPERJEV_GATE_JUDGE_ADVISORY=1): a block whose
        # ONLY reasons came from the judge — det_block_reasons is empty, so
        # nothing deterministic (count mismatch, PR mismatch,
        # CONTRADICTED_BY_FACT) is in the mix — is demoted to advisory. A
        # block carrying even one deterministic reason is untouched: it
        # falls through to the "block" branch below exactly as it does
        # today, env or no env. Checked after stop_hook_active so a re-run
        # keeps its own (already advisory) handling rather than being
        # relabeled here.
        elif (door == "gate" and action == "block" and _judge_advisory_enabled()
              and not det_block_reasons):
            action = "block-judge-advisory"

        # SKIPS-20260918.md / l22-l24: a claim the judge flagged at or
        # above the block line, but the empty-current-turn health gate
        # suppressed it from blocking — checked and flagged, not a silent
        # miss, but distinct from both a real block and a plain advisory.
        # Recorded on the ledger line whenever this run's own outcome
        # isn't already a hard block (a hard block from some OTHER flag
        # already carries this claim in its own block_notes/stderr).
        suppressed = _suppressed_empty_turn_from_notes(block_notes)
        suppressed_reason = None
        suppressed_note_tail = ""
        if suppressed:
            sk, sv, sscore = suppressed
            suppressed_reason = "flagged-suppressed-empty-turn"
            suppressed_note_tail = f" [suppressed: {sk} {sv} {sscore:.2f}]"

        # The catch ledger's own window_bytes — best-effort, gate only (the
        # transcript-derived window is the thing worth sizing; verify has
        # no equivalent window, so this stays None there).
        _catch_window_bytes = None
        if door == "gate":
            _catch_window_bytes = (window_meta or {}).get("total_bytes")
            if _catch_window_bytes is None:
                try:
                    _catch_window_bytes = sum(os.path.getsize(p) for p in evidence)
                except OSError:
                    _catch_window_bytes = None

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

        if action == "unchecked-no-evidence":
            reason_bits = "; ".join(block_notes) if block_notes else f"exit {code}"
            advisory = ("super-jev verify: no evidence gathered; not judged "
                       f"(exit {code} suppressed — {reason_bits})")
            print(advisory)
            _hook_log(f"verify: unchecked — no evidence gathered (exit {code} "
                     f"suppressed — {reason_bits}) — advisory, not judged", exit_code=0,
                     flags=flags, unchecked=True, health="none", reason="no-evidence")
            catch_log(door, "unchecked", reasons=["no-evidence"] + block_notes,
                     draft_text=text, window_bytes=_catch_window_bytes,
                     start_time=_catch_t0, payload=payload)
            _print_ledger_notice_if_gate()
            return 0
        if action == "allow":
            _hook_log(f"{door}: allow (exit {code}){suppressed_note_tail}", exit_code=0,
                     flags=flags, reason=suppressed_reason)
            catch_log(door, "allow", reasons=block_notes, draft_text=text,
                     window_bytes=_catch_window_bytes, start_time=_catch_t0,
                     payload=payload)
            _print_ledger_notice_if_gate()
            return 0
        if action == "block-forced-advisory":
            reason_bits = "; ".join(block_reasons) if block_reasons else f"exit {code}"
            advisory = f"super-jev gate: second pass (stop_hook_active) — advisory only, would have blocked on: {reason_bits}"
            print(advisory)
            _hook_log(f"gate: second pass, advisory only (exit {code}) — would have "
                     f"blocked on: {reason_bits}{suppressed_note_tail}", exit_code=0,
                     flags=flags, reason=suppressed_reason)
            catch_log(door, "advisory-forced", reasons=block_reasons, draft_text=text,
                     window_bytes=_catch_window_bytes, start_time=_catch_t0,
                     payload=payload)
            _print_ledger_notice_if_gate()
            return 0
        if action == "block-judge-advisory":
            reason_bits = "; ".join(block_reasons) if block_reasons else f"exit {code}"
            advisory = f"super-jev gate (judge advisory, not blocked): {reason_bits}"
            if block_notes:
                advisory += " (advisory: " + "; ".join(block_notes) + ")"
            print(advisory, file=sys.stderr)
            _hook_log(f"gate: judge advisory, not blocked (exit {code}) — would have "
                     f"blocked on: {reason_bits}{suppressed_note_tail}", exit_code=0,
                     flags=flags, reason=suppressed_reason)
            catch_log(door, "advisory-judge", reasons=block_reasons, draft_text=text,
                     window_bytes=_catch_window_bytes, start_time=_catch_t0,
                     payload=payload)
            _print_ledger_notice_if_gate()
            return 0
        if action == "block":
            reason = f"super-jev {door} blocked this (exit {code})"
            if block_reasons:
                reason += ": " + "; ".join(block_reasons)
            # 2026-09-18: advisory-only findings (e.g. the demoted secondary
            # NOT_SUPPORTED/CONTRADICTED arm) still ride alongside a real
            # block, printed to stderr same as --explain, never as a second
            # block reason.
            if block_notes:
                reason += " (advisory: " + "; ".join(block_notes) + ")"
            print(reason, file=sys.stderr)
            _hook_log(f"{door}: block (exit {code})" +
                     (f" — strong flags: {'; '.join(block_reasons)}" if block_reasons else "") +
                     (f" — advisory: {'; '.join(block_notes)}" if block_notes else ""),
                     exit_code=2, flags=flags)
            catch_log(door, "block", reasons=block_reasons + block_notes, draft_text=text,
                     window_bytes=_catch_window_bytes, start_time=_catch_t0,
                     payload=payload)
            _print_ledger_notice_if_gate()
            return 2
        note_tail = (" — " + "; ".join(block_notes)) if block_notes else ""
        advisory = f"super-jev {door} advisory (exit {code}){note_tail}"
        print(advisory)
        _hook_log(f"{door}: advisory (exit {code}){note_tail}", exit_code=0, flags=flags,
                 reason=suppressed_reason)
        catch_log(door, "advisory", reasons=block_notes, draft_text=text,
                 window_bytes=_catch_window_bytes, start_time=_catch_t0,
                 payload=payload)
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

# 2026-09-18 (SKIPS-20260918.md): the single "unchecked" bucket above
# conflated three very different things — spawn-dict/no-teammate-messages
# (66+42 of 300 in the sample window) are not misses at all, they're the
# wrong hook event for a reply that gets checked elsewhere; no-tool-
# evidence-checkable (19/300) IS judged, just against thin evidence; and
# a true lost check (a reply existed and nothing ever judged it) was 0/300
# — rare enough that its WARN should trip on ANY occurrence, not a share.
# Splitting the reason table lets ledger_health warn on the bucket that
# actually means something broke, instead of a number permanently pinned
# above 25% by healthy spawn-dict/no-teammate-messages volume alone.
#
#   DEFERRED — nothing user-facing to check yet, or the wrong axis
#     entirely (this hook event isn't about the current turn's own reply).
#   THIN     — a judgment DID run and DID surface to the user, just
#     against thin evidence (the last prompt, not this turn's own tool
#     output) rather than being silently dropped.
#   LOST     — evidence or a checkable reply existed and nothing judged
#     it. Any reason not named in this table also lands here BY DESIGN —
#     see SKIPS-20260918.md recommendation #3: a genuine lost check
#     should be rare-to-never, so an unrecognized reason is treated as
#     one rather than silently folded into "nothing to see here."
SKIP_BUCKET_DEFERRED = "deferred"
SKIP_BUCKET_THIN = "thin"
SKIP_BUCKET_LOST = "lost"

SKIP_REASON_BUCKETS = {
    "spawn-dict": SKIP_BUCKET_DEFERRED,
    "no-teammate-messages": SKIP_BUCKET_DEFERRED,
    "no-tool-evidence-silent": SKIP_BUCKET_DEFERRED,
    "no-tool-evidence-checkable": SKIP_BUCKET_THIN,
    "no-tool-evidence": SKIP_BUCKET_THIN,  # legacy tag, pre-split ledger lines
    # The stop-scan's UNCHECKED verdict (worker-verify's own exit code
    # said REJECT/CLEAN, but the gather had nothing usable, so the label
    # was downgraded to UNCHECKED — see cmd_hook_prompt_verify's
    # stop-scan branch) is judged against thin evidence, same as the
    # sibling no-tool-evidence-checkable path above, not a lost check.
    "no-evidence": SKIP_BUCKET_THIN,
    "bad-stdin": SKIP_BUCKET_LOST,
    "unexpected-error": SKIP_BUCKET_LOST,
    # The advisory teammate-report scan running out of its own time or its
    # own call allowance is a DEFERRAL, not a lost check. The turn's reply
    # is still judged by the gate on this same event, and the scan's state
    # file only advances past reports it actually attempted, so the rest
    # are retried on the next Stop event. Counting these as LOST pinned a
    # permanent false WARN on every session that spawned workers.
    "stop-scan-timeout": SKIP_BUCKET_DEFERRED,
    "stop-scan-deferred": SKIP_BUCKET_DEFERRED,
    # A reply that went unjudged because the event ran out of wall clock
    # is a LOST check, not a deferral — the turn ended, the reply shipped,
    # and nothing checked it. It belongs in the bucket the health monitor
    # warns on at the first occurrence.
    "budget-exceeded": SKIP_BUCKET_LOST,
}

DEFAULT_LOST_WARN_COUNT = 1
LOST_WARN_ENV = "SUPERJEV_LOST_WARN"
DEFAULT_THIN_NOTE_PCT = 25.0
THIN_NOTE_ENV = "SUPERJEV_THIN_NOTE"

_NOTE_DOOR_RE = re.compile(r'^([a-z][a-z-]*):\s')


def _skip_reason_bucket(reason):
    """deferred | thin | lost for one skip-reason tag (see
    SKIP_REASON_BUCKETS above). Anything not in the table is LOST, by
    design — a reason superjev doesn't recognize is exactly the shape of
    thing that should be visible, not silently swallowed."""
    return SKIP_REASON_BUCKETS.get(reason, SKIP_BUCKET_LOST)


def _unchecked_warn_pct():
    """SUPERJEV_UNCHECKED_WARN, parsed as a float percent (e.g. "25" for
    25%); DEFAULT_UNCHECKED_WARN_PCT if unset or unparsable. Kept only for
    the informational total-unchecked-share line — it no longer drives any
    WARN (see _lost_warn_count / _thin_note_pct)."""
    raw = os.environ.get(UNCHECKED_WARN_ENV)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_UNCHECKED_WARN_PCT


def _lost_warn_count():
    """SUPERJEV_LOST_WARN, parsed as an int count (e.g. "1" — warn once a
    single LOST record shows up in the window); DEFAULT_LOST_WARN_COUNT
    if unset or unparsable. A count, not a percent — SKIPS-20260918.md's
    finding was 0 true lost checks in 300 records, so even one is signal,
    no MIN_RUNS_FOR_WARN gate applies to this dial."""
    raw = os.environ.get(LOST_WARN_ENV)
    if raw:
        try:
            return int(float(raw))
        except ValueError:
            pass
    return DEFAULT_LOST_WARN_COUNT


def _thin_note_pct():
    """SUPERJEV_THIN_NOTE, parsed as a float percent; DEFAULT_THIN_NOTE_PCT
    if unset or unparsable. THIN is real signal about gate coverage
    quality (judged, but on thin evidence) — worth a softer NOTE, never a
    WARN, when its share climbs, same MIN_RUNS_FOR_WARN noise guard as the
    old unchecked-share WARN used."""
    raw = os.environ.get(THIN_NOTE_ENV)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_THIN_NOTE_PCT


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
    (every skip path does now, including no-tool-evidence-checkable/
    -silent), else a tag squeezed out of the free-text note (for ledger
    lines written before this call site set reason= — old lines carry
    only the note text), else "other"."""
    reason = entry.get("reason")
    if reason:
        return reason
    note = (entry.get("note") or "").lower()
    if "no tool evidence derivable" in note:
        # Legacy (pre-reason=) lines only: distinguish checkable (THIN)
        # from silent (DEFERRED) off the same note-text markers the two
        # _hook_log call sites already write — see _hook_unchecked.
        if "checkable claim reported" in note:
            return "no-tool-evidence-checkable"
        if "silent" in note:
            return "no-tool-evidence-silent"
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


def _ledger_line_is_suppressed(entry):
    """True for a ledger line recording the empty-current-turn health
    gate suppressing a claim the judge flagged at or above the block line
    (reason="flagged-suppressed-empty-turn" — see cmd_hook /
    _suppressed_empty_turn_from_notes). Read straight off `reason`,
    independent of verdict — this can ride an allow, advisory, or
    block-forced-advisory line, never an `unchecked` one."""
    return entry.get("reason") == "flagged-suppressed-empty-turn"


def _door_health_bucket(entries):
    """{"runs","blocked","advisory","unchecked","allow",
    "unchecked_share_pct","top_skip_reason","deferred","thin","lost",
    "suppressed","deferred_share_pct","thin_share_pct","lost_share_pct",
    "suppressed_share_pct"} for one list of hook-run ledger entries
    (either one door's slice or the whole window).

    unchecked/unchecked_share_pct/top_skip_reason are the old single-
    bucket numbers, kept as an informational total (see the module note
    above SKIP_REASON_BUCKETS) — nothing warns off them anymore.
    deferred/thin/lost split that same unchecked count three ways via
    SKIP_REASON_BUCKETS; suppressed is a separate, fourth count that has
    nothing to do with unchecked/skipped at all (see
    _ledger_line_is_suppressed)."""
    bucket = {"runs": len(entries), "blocked": 0, "advisory": 0,
             "unchecked": 0, "allow": 0,
             "deferred": 0, "thin": 0, "lost": 0, "suppressed": 0}
    verdict_to_key = {"block": "blocked", "advisory": "advisory",
                      "unchecked": "unchecked", "allow": "allow"}
    skip_reasons = {}
    lost_reasons = {}
    for e in entries:
        v = _ledger_line_verdict(e)
        bucket[verdict_to_key[v]] += 1
        if v == "unchecked":
            r = _ledger_skip_reason(e)
            skip_reasons[r] = skip_reasons.get(r, 0) + 1
            b = _skip_reason_bucket(r)
            bucket[b] += 1
            if b == SKIP_BUCKET_LOST:
                lost_reasons[r] = lost_reasons.get(r, 0) + 1
        if _ledger_line_is_suppressed(e):
            bucket["suppressed"] += 1
    runs = bucket["runs"]
    bucket["unchecked_share_pct"] = round(100.0 * bucket["unchecked"] / runs, 1) if runs else 0.0
    bucket["deferred_share_pct"] = round(100.0 * bucket["deferred"] / runs, 1) if runs else 0.0
    bucket["thin_share_pct"] = round(100.0 * bucket["thin"] / runs, 1) if runs else 0.0
    bucket["lost_share_pct"] = round(100.0 * bucket["lost"] / runs, 1) if runs else 0.0
    bucket["suppressed_share_pct"] = round(100.0 * bucket["suppressed"] / runs, 1) if runs else 0.0
    # The WARN line names the top reason WITHIN the lost bucket specifically
    # — top_skip_reason (below) is the old, overall-unchecked number, which
    # a large healthy deferred/thin volume can dominate even when the lost
    # bucket itself is a single distinct reason.
    bucket["top_lost_reason"] = (max(lost_reasons, key=lost_reasons.get)
                                 if lost_reasons else None)
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

    Returns {"window", "threshold_pct", "lost_warn_count",
    "thin_note_pct", "overall": bucket, "doors": {door: bucket}}, each
    bucket shaped by _door_health_bucket. `threshold_pct` is the legacy
    total-unchecked dial, kept for the informational line only.
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
        "lost_warn_count": _lost_warn_count(),
        "thin_note_pct": _thin_note_pct(),
        "overall": _door_health_bucket(windowed),
        "doors": {name: _door_health_bucket(es) for name, es in by_door.items()},
    }


def _health_warnings(health):
    """[(label, bucket), ...] for every bucket (overall plus each door)
    whose LOST count is at or above lost_warn_count (default 1 — see
    SKIPS-20260918.md recommendation #3: a genuine lost check should be
    rare-to-never, so a count threshold catches it long before a
    percentage would, and no MIN_RUNS_FOR_WARN noise-guard applies — one
    real lost check in one run is still a real lost check). The
    empty-ledger / all-healthy case gives back []."""
    out = []
    threshold = health["lost_warn_count"]
    if health["overall"]["lost"] >= threshold:
        out.append(("overall", health["overall"]))
    for name in sorted(health["doors"]):
        b = health["doors"][name]
        if b["lost"] >= threshold:
            out.append((name, b))
    return out


def _health_notes(health):
    """[(label, bucket), ...] for every bucket (overall plus each door)
    whose THIN share is at or above thin_note_pct (default 25%) AND has
    at least MIN_RUNS_FOR_WARN runs — same noise guard the old unchecked-
    share WARN used, kept here since THIN is a share, not a count. A
    softer signal than _health_warnings: judged-on-thin-evidence volume
    worth watching, never a reason to fail a script."""
    out = []
    threshold = health["thin_note_pct"]
    if (health["overall"]["runs"] >= MIN_RUNS_FOR_WARN
            and health["overall"]["thin_share_pct"] >= threshold):
        out.append(("overall", health["overall"]))
    for name in sorted(health["doors"]):
        b = health["doors"][name]
        if b["runs"] >= MIN_RUNS_FOR_WARN and b["thin_share_pct"] >= threshold:
            out.append((name, b))
    return out


def _print_ledger_health(health, heading="ledger health"):
    o = health["overall"]
    print(f"{heading} (last {health['window']} hook run(s), warn at "
          f"{health['lost_warn_count']} lost record(s), note at "
          f"{health['thin_note_pct']:g}% thin)")
    if not o["runs"]:
        print("  no hook runs recorded yet")
        return
    print(f"  {'door':<14} {'runs':>5} {'blocked':>8} {'advisory':>9} "
          f"{'deferred':>9} {'thin':>6} {'lost':>6} {'suppressed':>11} "
          f"{'unchecked%':>11}")
    print(f"  {'overall':<14} {o['runs']:>5} {o['blocked']:>8} {o['advisory']:>9} "
          f"{o['deferred']:>9} {o['thin']:>6} {o['lost']:>6} {o['suppressed']:>11} "
          f"{o['unchecked_share_pct']:>10.1f}%")
    for name in sorted(health["doors"]):
        d = health["doors"][name]
        print(f"  {name:<14} {d['runs']:>5} {d['blocked']:>8} {d['advisory']:>9} "
              f"{d['deferred']:>9} {d['thin']:>6} {d['lost']:>6} {d['suppressed']:>11} "
              f"{d['unchecked_share_pct']:>10.1f}%")
    for label, bucket in _health_warnings(health):
        print(f"  WARN: {label} lost check(s) {bucket['lost']}/{bucket['runs']} "
              f"(>= {health['lost_warn_count']}) — a checkable reply existed and "
              f"nothing judged it; most common lost reason: "
              f"{bucket['top_lost_reason'] or 'n/a'}")
    for label, bucket in _health_notes(health):
        print(f"  NOTE: {label} thin-evidence share {bucket['thin_share_pct']:.1f}% "
              f"({bucket['thin']}/{bucket['runs']}) exceeds "
              f"{health['thin_note_pct']:g}% — judged, but against the last prompt "
              f"rather than this turn's own tool output")
    if o["suppressed"]:
        print(f"  NOTE: {o['suppressed']} suppressed record(s) this window — a claim "
              f"the judge flagged at or above the block line, let through because "
              f"the current turn ran no tools of its own")


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
    verdict when a LOST check shows up in the last `window` hook runs —
    so an in-session bug like 2026-09-16's shows up the same turn instead
    of waiting for someone to read the ledger later. Scoped to hook-run
    lines only, same as ledger_health; driven by _health_warnings (LOST
    count), not the old total-unchecked share."""
    health = ledger_health(window=window)
    warnings = _health_warnings(health)
    if not warnings:
        return None
    label, bucket = warnings[0]
    return (f"[super-jev] ledger health: {label} lost check(s) "
           f"{bucket['lost']}/{bucket['runs']} of last {window} — a checkable reply "
           f"existed and nothing judged it; most common lost reason: "
           f"{bucket['top_lost_reason'] or 'n/a'}. Run `superjev.py ledger health` to see more.")


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
    v.add_argument("--explain", action="store_true",
                   help="door-absent fallback only: list the gh commands run "
                        "gathering PR state/checks")
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

    ck = subs.add_parser("catch", help="the catch ledger: one record per gate/verify "
                                       "decision, tagged fair/false/miss")
    ck_subs = ck.add_subparsers(dest="catch_action")
    ck_list = ck_subs.add_parser("list", help="one line per catch-ledger record")
    ck_list.add_argument("--since", default=None, help="e.g. 24h, 7d, 30m")
    ck_list.add_argument("--untagged", action="store_true", help="only untagged records")
    ck_list.add_argument("--bot", default=None, help="only records from this bot id "
                                                      "(see the `bot` field, e.g. primary)")
    ck_list.set_defaults(func=cmd_catch, catch_action="list")
    ck_tag = ck_subs.add_parser("tag", help="fair = block was right; false = block was "
                                            "wrong; miss = an allow let a lie through")
    ck_tag.add_argument("id", help="the catch-ledger record id (see `catch list`)")
    ck_tag.add_argument("value", choices=["fair", "false", "miss"])
    ck_tag.add_argument("note", nargs="?", default="", help="why, in your own words")
    ck_tag.set_defaults(func=cmd_catch, catch_action="tag")
    ck_report = ck_subs.add_parser("report", help="fair catches, false stops, misses, "
                                                   "plus untagged count")
    ck_report.add_argument("--since", default=None, help="e.g. 24h, 7d, 30m")
    ck_report.add_argument("--bot", default=None, help="only records from this bot id "
                                                        "(see the `bot` field, e.g. primary)")
    ck_report.set_defaults(func=cmd_catch, catch_action="report")
    ck.set_defaults(func=cmd_catch, catch_action=None)

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

    gd = subs.add_parser("guard", help="the blocklist+redactor: which paths would be "
                                       "skipped, what would be redacted")
    gd.add_argument("--paths", nargs="*", default=[],
                    help="paths to check; a blocked one is reported, never opened")
    gd.add_argument("--allow-path", nargs="*", default=[], dest="allow_path",
                    help="explicit prefix override, unblocks a path under it")
    gd.add_argument("--text", default=None, help="text to redact directly (not a file)")
    gd.add_argument("--redact-emails", action="store_true", dest="redact_emails",
                    help="also redact email addresses (off by default)")
    _add_json_flag(gd)
    gd.set_defaults(func=cmd_guard)

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
