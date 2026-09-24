#!/usr/bin/env bash
# Example PreToolUse hook wiring for `leash.py` — the worker leash.
#
# Claude Code invokes a PreToolUse hook before a tool call runs, with a
# JSON payload on stdin. This hook only acts on SUB-AGENT calls: a real
# lead-session PreToolUse payload never carries an "agent_id" (verified
# against a live capture — see docs/hooks.md), so a lead call is always
# allowed with no further work. For a sub-agent call on Write, Edit,
# NotebookEdit, or Bash, the target path is checked against an always-deny
# list, then an allow-list keyed by agent_id (leash.json, or LEASH_ALLOW /
# LEASH_DENY env vars). Bash target detection is best-effort regex, not a
# real shell parser — see docs/hooks.md "Gaps".
#
#   {
#     "hooks": {
#       "PreToolUse": [
#         {
#           "matcher": "Write|Edit|NotebookEdit|Bash",
#           "hooks": [
#             { "type": "command", "command": "/absolute/path/to/skills/super-jev/hooks/pretooluse-leash.sh" }
#           ]
#         }
#       ]
#     }
#   }
#
# Config: LEASH_CONFIG (default: leash.json next to leash.py), LEASH_ALLOW
# (colon-separated paths, overrides leash.json entirely), LEASH_DENY
# (colon-separated always-deny patterns, overrides the built-in defaults),
# LEASH_LOG (optional jsonl decision log path).
#
# Exit 0 (silent) = allow. Exit 2 + one line on stderr = block.
# On any internal error this fails OPEN (allow + log) — a crashing
# PreToolUse hook blocks every tool call for every agent, lead included,
# which is worse than the leash missing one write. See docs/hooks.md.
#
# This script is NOT installed into ~/.claude/settings.json by this skill —
# copy the snippet above into your own settings.json to wire it in.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec python3 "$SKILL_DIR/leash.py"
