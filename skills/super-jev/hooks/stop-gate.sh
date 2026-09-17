#!/usr/bin/env bash
# Example Stop hook wiring for `superjev.py hook gate`.
#
# Claude Code invokes a Stop hook with a JSON payload on stdin (fields
# include at least session_id, transcript_path, hook_event_name,
# stop_hook_active) and reads this script's stdout/stderr/exit code to
# decide whether to allow the turn to end. This script passes that payload
# straight through to `superjev.py hook gate`, which:
#   - reads the last assistant message out of transcript_path as the draft
#     (since a Stop payload carries no "draft" field directly),
#   - runs it through the claim gate,
#   - exits 0 (silent) on CLEAN, exits 0 with an advisory line on stdout for
#     READ, exits 2 with a reason on stderr to block on REJECT.
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
