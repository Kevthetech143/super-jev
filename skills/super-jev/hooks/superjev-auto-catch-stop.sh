#!/usr/bin/env bash
# Optional Claude Code Stop hook: feed the turn's final assistant text to
# `ask.py --done` so auto-catch can evaluate it against the live receipt.
#
# Advisory only: always exits 0, never blocks the turn. Documented as optional
# in SKILL.md; every model is told to call `--done` by hand after answering,
# this hook just removes the remembering.
#
# Wire it in ~/.claude/settings.json:
#   { "hooks": { "Stop": [ { "hooks": [
#       { "type": "command",
#         "command": "/absolute/path/to/skills/super-jev/hooks/superjev-auto-catch-stop.sh" } ] } ] } }
#
# Requires SUPERJEV_PRINCIPAL in the hook environment. Skips silently when
# autoCatch is off or no live receipt exists for this principal (so turns
# that never asked do not spam the not-caught log).
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRINCIPAL="${SUPERJEV_PRINCIPAL:-}"
[ -n "$PRINCIPAL" ] || exit 0

STATE_ROOT="${SUPERJEV_STATE_DIR:-$HOME/.local/state/super-jev}"
RECEIPT="$STATE_ROOT/$PRINCIPAL/receipts/$PRINCIPAL.json"
[ -f "$RECEIPT" ] || exit 0

PAYLOAD="$(cat)"
TEXT="$(python3 - "$PAYLOAD" <<'PYEOF'
import json, sys
try:
    payload = json.loads(sys.argv[1] or "{}")
except Exception:
    payload = {}
text = payload.get("last_assistant_message") or ""
if not text:
    tp = payload.get("transcript_path")
    if tp:
        try:
            tail = open(tp, encoding="utf-8", errors="replace").read().splitlines()[-400:]
            for line in reversed(tail):
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("type") == "assistant":
                    for b in rec.get("message", {}).get("content", []):
                        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                            text = b["text"]
                            break
                    if text:
                        break
        except Exception:
            pass
sys.stdout.write(text or "")
PYEOF
)"
[ -n "$TEXT" ] || exit 0
exec python3 "$SKILL_DIR/ask.py" --principal "$PRINCIPAL" --done "$TEXT"
