#!/usr/bin/env bash
# Example PostToolUse hook wiring for `superjev.py hook verify`.
#
# Claude Code invokes a PostToolUse hook after a tool call completes, with a
# JSON payload on stdin. The real fields it carries (verified against the
# Claude Code hooks docs, not assumed): session_id, transcript_path, cwd,
# hook_event_name, tool_name, tool_input, tool_use_id, and tool_response —
# the tool's own output. There is no "report" field. Point this at the
# Agent/Task tool so a sub-agent's final report gets checked as soon as it
# lands, before the lead agent trusts it:
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
#   - if tool_name is present and is not "Agent", this event is not a
#     sub-agent report — fails open (the matcher above should already
#     prevent this, but the shim checks too rather than trusting it alone),
#   - takes tool_response as the report text (falling back to
#     payload["report"]/["text"]/["message"], then to the last assistant
#     message in transcript_path, for a caller building its own smaller
#     payload instead of forwarding the full one),
#   - takes the worktree from payload["worktree"] if present, else
#     SUPERJEV_HOOK_WORKTREE from the environment, else none — never from
#     payload["cwd"], which is the lead session's directory, not
#     necessarily the worker's tree,
#   - runs it through report-verify, with a timeout (SUPERJEV_VERIFY_TIMEOUT,
#     default 300s) that fails open rather than hanging the session,
#   - exits 0 (silent) on CLEAN, exits 2 with a reason on stderr to block
#     on REJECT (the evidence disproves a claim), and on READ, additionally
#     BLOCKS (exit 2) rather than advises if a claim/draft-level flag in
#     worker-verify's own output crosses the same strong-flag line
#     superjev.py hook gate uses (see stop-gate.sh) — else exits 0 with an
#     advisory line on stdout for READ / NO_CHECKABLE_CLAIMS / BAD_USAGE.
#
# This script is NOT installed into ~/.claude/settings.json by this skill —
# copy the snippet above into your own settings.json to wire it in.
#
# SUPERJEV_VERIFY_CMD must point at a real report-verify tool (see SKILL.md
# Install) or this fails open rather than blocking.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec python3 "$SKILL_DIR/superjev.py" hook verify
