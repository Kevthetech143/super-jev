# Wiring Super Jev into Claude Code

## 1. The retrieval rule card

1. To find a SKILL, use skill-search — never grep the skills directory.
2. To find a FILE answer, use `ask` — never browse the folders yourself.
3. After EVERY ask: `--approve` a good hit, `--miss` a bad one, `--add` a fact with no file.
4. Read the top evidence file yourself before answering from it.
5. A "preparation required" or "refresh-required" reply means SETUP is pending, not that there is no match — run the connect flow, then ask again.
6. The cache only replays YOUR OWN approved wording — paraphrases go through the search lane.
7. Never state a fact from memory when a door can confirm it; check first.
8. `check` every drafted answer before it leaves: verbatim quotes, not paraphrases.
9. One principal per agent; do not mix principals.
10. When stuck, read `docs/GETTING-STARTED.md` before inventing a workflow.

## 2. Stop-hook claim gate example

Install as a Stop hook; it reads the agent's last reply and blocks when the
reply claims verification without a matching trace line:

```bash
#!/usr/bin/env bash
# Stop hook: claim gate. Paths here are generic — point REPLY_FILE and
# TRACE_LOG at your own files. Exit 1 blocks the reply; exit 0 passes it.
set -eu
REPLY_FILE="${REPLY_FILE:-$HOME/.agent/last-reply.txt}"
TRACE_LOG="${TRACE_LOG:-$HOME/.agent/trace.log}"
if grep -qEi "verified|confirmed|all tests pass|checks out" "$REPLY_FILE"; then
  if ! grep -qF "$(head -c 120 "$REPLY_FILE")" "$TRACE_LOG"; then
    echo "BLOCKED: reply claims verification with no matching trace line" >&2
    exit 1
  fi
fi
exit 0
```

Tested with: `bash -n` syntax check plus one dry run against synthetic
reply/trace files on 2026-09-21 (block case and pass case both behaved).

## 3. Roadmap

The auto-catch hook (approved hits kept without a manual command) arrives in 1.1.
