#!/usr/bin/env bash
# Example Stop hook wiring for `superjev.py hook gate`.
#
# Claude Code invokes a Stop hook with a JSON payload on stdin. The real
# fields it carries (verified against the Claude Code hooks docs, not
# assumed): session_id, transcript_path, cwd, hook_event_name,
# stop_hook_active, and last_assistant_message — the current turn's final
# assistant text, handed directly so a hook does not have to re-parse a
# possibly-stale transcript. There is no "draft" and no "evidence" field.
# Claude Code reads this script's stdout/stderr/exit code to decide whether
# to allow the turn to end. This script passes the payload straight through
# to `superjev.py hook gate`, which:
#   - takes last_assistant_message as the draft (falling back to the last
#     assistant message in transcript_path if that field is ever absent),
#   - derives evidence from the transcript itself when the payload names
#     none — the tool_result content of the last N tool calls found in it
#     (N = SUPERJEV_HOOK_EVIDENCE_N, default 8; size-capped by
#     SUPERJEV_HOOK_EVIDENCE_MAX_BYTES). If NO tool evidence is derivable
#     (the turn ran no tools), it still runs the gate with the user's last
#     prompt as the only evidence and, only if the gate reports a checkable
#     claim, prints ONE advisory line ("reply makes claims with no tool
#     evidence this turn — mark them unverified or gather evidence"); no
#     checkable claim means silence. That path never blocks (exit 0) and
#     the ledger line carries unchecked=true,
#   - before checking, strips fleet machine tags out of the draft (a
#     trailing [BOARD: ...] line, Rung:/Rung line: lines, [Add] [Skip]
#     buttons, and raw <...> system tags) — these are bookkeeping, not the
#     reply's own content, and the gate's leaked_internal/
#     self_contradictory scoring has misread them as internal leakage or a
#     contradiction. Pattern list is SUPERJEV_STRIP_PATTERNS-overridable
#     (JSON list of regex strings),
#   - runs it through the claim gate, with a timeout (SUPERJEV_GATE_TIMEOUT,
#     default 90s) that also fails open rather than hanging the session,
#   - exits 0 (silent) on CLEAN, exits 2 with a reason on stderr to block
#     on REJECT (fabricated quote), and on READ, additionally BLOCKS (exit
#     2) rather than advises if a claim or draft-level flag in the gate's
#     own output crosses its own line — a claim-level NOT_SUPPORTED or
#     CONTRADICTED at or above SUPERJEV_BLOCK_CONF (default 0.80) blocks,
#     and so does OVERCLAIMS at or above the same line when a claim in the
#     same run is also NOT_SUPPORTED/CONTRADICTED at 0.50 or higher — but
#     only when the evidence gather itself was healthy enough to trust a
#     confident verdict; a gather too thin to judge against (no evidence
#     source, a refused directory-level test command, a probe under the
#     char floor) suppresses every one of these into an advisory instead,
#     because a confident-but-ungrounded verdict and a real problem print
#     the same red table. SELF_CONTRADICTORY never blocks, alone or in
#     company — else exits 0 with an advisory line on stdout,
#   - if the payload carries stop_hook_active=true (Claude Code's own
#     signal that this Stop event is a re-run because a prior Stop hook
#     already blocked this turn), never blocks on this pass regardless of
#     flags — prints a one-line advisory naming what it would have blocked
#     on and exits 0, so a gate that keeps disagreeing with a rewritten
#     reply cannot loop the session forever.
#   - as of 2026-09-17, this same run ALSO scans transcript_path for
#     <teammate-message> report blocks this session has not already
#     checked (Claude Code's UserPromptSubmit payload never actually
#     fires for those, so `hook prompt-verify` alone never sees a real
#     one) and runs `verify` against each — advisory only, never touching
#     this script's own exit code.
#     See SKILL.md's "Correction, 2026-09-17" paragraph and docs/wire-into-claude-code.md.
#   - as of 2026-09-18, the whole Stop event is bounded, because that scan
#     is what used to keep the user waiting. SUPERJEV_GATE_MAX_CALLS
#     (default 1) is the event's live-call allowance and the gate's own
#     verdict claims it first, so at the default the scan makes no live
#     call and defers its reports to the next Stop event. SUPERJEV_GATE_
#     BUDGET_S (default 15s) bounds the event end to end and clamps every
#     child check's timeout to what is left of it; when it runs out before
#     the gate can judge, this script prints "budget exceeded, not judged"
#     and exits 3 — advisory, never a block — and the ledger records
#     reason "budget-exceeded" in the health monitor's LOST bucket.
#     That reason is for an UNJUDGED REPLY only. The advisory scan
#     deferring its worker-report checks logs "stop-scan-deferred"
#     instead, which is a DEFERRED record: the reply was still judged
#     by the gate, and the deferred reports are retried next Stop.
#     SUPERJEV_GATE_WINDOW_TOK (default 8000) is the one hard cap on the
#     evidence window, applied once right before the call, giving up
#     previous turns, then receipts, then the cited-file tail, then worker
#     reports — never the derived facts or the current turn.
#     SUPERJEV_STOP_SCAN_MAX_BYTES (default 2MB) bounds the scan's own read
#     to the transcript's tail. Timed, not assumed: the gate path shells
#     out to nothing but the judge — every long-timeout wait in the skill
#     (git, gh, the derive-facts bridge, and above all a test command
#     derived from a worker's report and then run twice) belongs to the
#     verify path, and all of them are now clamped to the event budget.
#     See docs/wire-into-claude-code.md, "The latency budget".
#
# This script is NOT installed into ~/.claude/settings.json by this skill —
# copy the snippet below into your own settings.json to wire it in.
#
#   {
#     "hooks": {
#       "Stop": [
#         {
#           "hooks": [
#             { "type": "command", "command": "/absolute/path/to/skills/super-jev/hooks/stop-gate.sh" }
#           ]
#         }
#       ]
#     }
#   }
#
# SUPERJEV_GATE_CMD must point at a real claim-gate tool (see SKILL.md
# Install) or this fails open (advisory-free no-op) rather than blocking —
# a hook must never wedge a session over a missing dependency.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec python3 "$SKILL_DIR/superjev.py" hook gate
