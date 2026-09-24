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

## 3. Worker Leash — PreToolUse hook (fourth hook)

`skills/super-jev/leash.py` (wired by `skills/super-jev/hooks/pretooluse-leash.sh`)
is a fourth hook, on the `PreToolUse` event, matching `Write|Edit|NotebookEdit|Bash`.
It is **not installed by default** — copy the snippet below into your own
`settings.json` to turn it on, and remove it the same way.

**What it does.** It only acts on sub-agent tool calls. A live capture
(`hook_input_log.jsonl` from the original prototype run) showed every real
lead-session `PreToolUse` payload has no `agent_id` (and no `agent_type`);
every sub-agent payload has one. A lead call is always allowed, untouched. For
a sub-agent call on `Write`/`Edit`/`NotebookEdit`, the target path is checked
against an always-deny list first, then an allow-list keyed by the worker's
`agent_id` (falling back to `agent_type`, then `"default"`). `Bash` is
best-effort: a handful of regexes try to spot a write target in the command
string (`>`, `>>`, `tee`, `cp`, `mv`, `rm`) and check it the same way.

**Config.** `leash.json` next to `leash.py` (see `leash.example.json`), or
`LEASH_CONFIG` to point elsewhere. Keys are `agent_id` or `agent_type`
values, `"default"` when neither matches; each value is a list of
directories that worker may write inside. `LEASH_ALLOW` (colon-separated
paths) overrides the whole allow-list from the environment instead.
`LEASH_DENY` (colon-separated) overrides the built-in always-deny defaults
(`.env`, `*secret*`, and one example fleet path); an `always_deny` list in
`leash.json` does the same. Add your own reply-outbox path (or any path a
worker must never write to directly) to your `always_deny` list — the
built-in example (`/tmp/ai-wrapper`) is this fleet's own, not a universal
default. `LEASH_LOG` (optional) appends a jsonl decision log.

**Path safety.** Every path (allow-list entry, always-deny pattern, and the
call's own target) is resolved the same way before comparison: `~`
expanded, made absolute relative to cwd (`..` normalized), then
`os.path.realpath` to follow symlinks, then casefolded — macOS's default
filesystem (HFS+/APFS) is case-insensitive, so a case-sensitive compare
would let a worker dodge the leash by changing case. A denied real
directory reached through an allowed symlink is still denied.

**Fail-open, on purpose.** Any internal error (bad JSON, a crashing regex,
anything unexpected) allows the call and logs the error, rather than
blocking. A crashing `PreToolUse` hook blocks every tool call in the
session, for every agent — lead included — which is worse than the leash
occasionally missing a write it should have caught.

**Install snippet:**

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|NotebookEdit|Bash",
        "hooks": [
          { "type": "command", "command": "/absolute/path/to/skills/super-jev/hooks/pretooluse-leash.sh" }
        ]
      }
    ]
  }
}
```

**Gaps — read before relying on this.**

- Bash detection is regex, not a real shell parser. A write hidden behind a
  variable, a subshell, quoting tricks, command substitution, or a command
  the pattern list doesn't know about (`sed -i`, `dd`, `sqlite3`, `python3 -c
  "open(...).write(...)"`, etc.) is invisible to this hook. Treat the Bash
  side as a deterrent, not a guarantee.
- Sub-agent detection relies on `agent_id`/`agent_type` being present on the
  payload, which is how this fleet's harness calls sub-agents today; a
  different harness or a future Claude Code payload shape could carry a
  worker call with neither field, in which case it is treated as a lead
  call and allowed.
- The always-deny defaults are examples, not universal truths. Every
  install should add its own reply-outbox path (wherever your harness
  writes the agent's final answer) and any other path a worker must never
  write to directly.
- Tested with `python3 -m pytest skills/super-jev/tests/test_leash.py`
  (stdlib `unittest`/`pytest`, no live Claude Code session) plus a manual
  dry run piping a synthetic `PreToolUse` payload on stdin. Not yet proven
  against a live multi-agent session under load.

## 4. Roadmap

The auto-catch hook (approved hits kept without a manual command) arrives in 1.1.
