#!/usr/bin/env bash
# Example PostToolUse hook wiring for `superjev.py hook verify`.
#
# Claude Code invokes a PostToolUse hook after a tool call completes, with a
# JSON payload on stdin (fields include at least session_id,
# transcript_path, hook_event_name, tool_name, tool_input, tool_response).
# Point this at the Agent/Task tool so a sub-agent's final report gets
# checked as soon as it lands, before the lead agent trusts it:
#
#   {
#     "hooks": {
#       "PostToolUse": [
#         {
#           "matcher": "Agent",
#           "hooks": [
#             { "type": "command", "command": "/absolute/path/to/skills/super-jev/hooks/posttooluse-verify.sh" }
#           ]
#         }
#       ]
#     }
#   }
#
# `superjev.py hook verify` reads the payload and:
#   - uses payload["report"] / ["text"] / ["message"] as the report text if
#     present (a wrapper hook script can build this smaller shape itself
#     instead of forwarding the full Claude Code payload — see SKILL.md),
#   - otherwise falls back to the last assistant message in
#     payload["transcript_path"],
#   - runs it through report-verify,
#   - exits 0 (silent) on CLEAN, exits 0 with an advisory line on stdout for
#     READ / NO_CHECKABLE_CLAIMS / BAD_USAGE, exits 2 with a reason on
#     stderr to block on REJECT (the evidence disproves a claim).
#
# This script is NOT installed into ~/.claude/settings.json by this skill —
# copy the snippet above into your own settings.json to wire it in.
#
# SUPERJEV_VERIFY_CMD must point at a real report-verify tool (see SKILL.md
# Install) or this fails open rather than blocking.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec python3 "$SKILL_DIR/superjev.py" hook verify
