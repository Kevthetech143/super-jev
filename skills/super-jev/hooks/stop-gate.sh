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
#     own output crosses its own line — NOT_SUPPORTED at or below
#     SUPERJEV_BLOCK_NOT_SUPPORTED (default 0.20) or OVERCLAIMS at or above
#     SUPERJEV_BLOCK_OVERCLAIM (default 0.80) always block on their own;
#     SELF_CONTRADICTORY at or below 0.30 only counts as a block reason
#     when the SAME run also carries a blocking NOT_SUPPORTED or OVERCLAIMS
#     flag — self-contradiction alone never blocks — else exits 0 with an
#     advisory line on stdout,
#   - if the payload carries stop_hook_active=true (Claude Code's own
#     signal that this Stop event is a re-run because a prior Stop hook
#     already blocked this turn), never blocks on this pass regardless of
#     flags — prints a one-line advisory naming what it would have blocked
#     on and exits 0, so a gate that keeps disagreeing with a rewritten
#     reply cannot loop the session forever.
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
