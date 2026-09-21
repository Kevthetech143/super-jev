#!/usr/bin/env bash
# Example UserPromptSubmit hook wiring for `superjev.py hook prompt-verify`.
#
# Claude Code invokes a UserPromptSubmit hook right before it hands the
# user's next prompt to the model, with a JSON payload on stdin carrying
# (among other fields) session_id, transcript_path, cwd, and "prompt" — the
# full text of the user's next turn.
#
# Why this hook exists at all: a background Agent/Task spawn's PostToolUse
# event never carries the worker's real, final report. Its tool_response is
# either a launch acknowledgement or, for a real background spawn, a DICT
# like {"status": "teammate_spawned", "prompt": "<the whole worker brief>"}
# — see posttooluse-verify.sh and SKILL.md's "spawn-dict" section for the
# 2026-09-17 finding this fixes. The worker's actual report arrives LATER,
# inside the lead's next USER turn, rendered by Claude Code's own teammate
# mailbox as one or more:
#
#   <teammate-message teammate_id="X" summary="...">
#   ...the worker's full final report...
#   </teammate-message>
#
# blocks. PostToolUse cannot see that turn at all — only UserPromptSubmit
# can. `hook prompt-verify` reads every teammate-message block out of
# payload["prompt"], and for each one that looks like a real report (a
# COMPLETE/INCOMPLETE word, a PR mention, or a test count) runs `verify`
# against it, with evidence auto-derived from the report's own text (an
# absolute path -> --worktree, a "PR #N" -> --pr, an npm test/pytest phrase
# -> --test-cmd). See SKILL.md's "hook prompt-verify" section for the full
# derivation rules.
#
#   {
#     "hooks": {
#       "UserPromptSubmit": [
#         {
#           "hooks": [
#             { "type": "command", "command": "/absolute/path/to/skills/super-jev/hooks/userpromptsubmit-verify.sh" }
#           ]
#         }
#       ]
#     }
#   }
#
# This shim ALWAYS exits 0 — UserPromptSubmit blocking would eat the operator's
# own message along with the check result, not just a verdict, so
# `prompt-verify` is advisory-only by construction: it prints one line per
# report it checked ("super-jev verify <teammate_id>: CLEAN|READ|REJECT —
# ..."), to stdout, and never blocks the turn.
#
# This script is NOT installed into ~/.claude/settings.json by this skill —
# copy the snippet above into your own settings.json to wire it in.
#
# SUPERJEV_VERIFY_CMD must point at a real report-verify tool (see SKILL.md
# Install) or every report this hook finds fails open rather than checking
# anything.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec python3 "$SKILL_DIR/superjev.py" hook prompt-verify
