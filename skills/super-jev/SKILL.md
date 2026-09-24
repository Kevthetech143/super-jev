---
name: super-jev
description: "Find skills and reviewed brain/doc evidence through Super Jev source connectors. Check claims, verify agent work, and reuse explicitly approved answers with bounded reviewed-source recovery. Evidence still needs caller review."
---

# Super Jev

Run from this skill directory: `python3 dispatch.py <tool> ...` (or `ask.py` directly, as shown below). Use the user's original request and relevant context; keep backend names and setup mechanics out of ordinary replies.

## Commands

| Command | Example | What it does |
|---|---|---|
| `ask` | `python3 ask.py --principal YOUR_AGENT "question in your own words"` | Cache first, then every connected pointer in parallel; open the top file before answering. `--approve` a good hit so the next ask is instant; `--add` a fact with no file; `--miss` when found elsewhere. Errors print per pointer, never hidden as no-candidates. |
| `navigate` | `memory --input '{"action":"navigate","pointer":"POINTER_ID","principal":"YOUR_AGENT_NAME","question":"..."}'` | Lower-level call `ask` wraps: searches one connected pointer's catalog for candidate files. Open the top file; a high score never proves it's in scope. |
| `skills` | `python3 dispatch.py skills --request "..."` | Skills connector: discover candidate skills from configured trusted local roots; suggestions never execute them. |
| `check` | `python3 dispatch.py check --claim "..." FILE` | Check a claim or draft against evidence. |
| `verify` | `python3 dispatch.py verify REPORT` | Verify an agent's report against evidence. |
| `help` | `python3 dispatch.py help --topic overview` | Quick Start and focused setup answers; no data, key, or Jev call needed. |

## Not connected yet?

A `preparation-required` result, an unknown pointer, or onboarding a new person/project is setup work, not "nothing found": see [`super-jev-connect/SKILL.md`](../super-jev-connect/SKILL.md). Check what you already have first:

```sh
python3 dispatch.py memory --principal YOUR_AGENT_NAME
```

## Live decision traces and Jev's voice

Every lookup (cache hits too, tier `cache`/`stale`) appends one redacted JSON line to `$STATE/traces.jsonl`: id, question, routing, content-check scores, final ranking, tier, timings, errors — never file contents or secrets. Rotates at ~20MB (one `.1` kept). `--approve` marks the last lookup for that question "right", `--miss` "wrong", `--add` after a miss "wrong, added". `ask.py --principal YOUR_AGENT --trace-report [--days 7]` reports right/wrong/unlabeled and top wrong questions.

When a lookup returns no usable answer, the last line `ask.py` prints is exactly:

```
Jev: I didn't have this. Want me to find it by hand and save it for next time?
```

**A harness relaying `ask.py` output to a human must pass that line on verbatim.**

## Maturity

Proven in current use: skill search, file-level `navigate`, checked connect, bulk prepare, `check` as a description gate, dataset and pointer listing, and the not-connected path. Experimental: passage-level `search`, saved-answer reuse (`approve`), and `verify` — treat their results as leads and read the evidence twice.

## Safeguards

`ready` is evidence, not an answer guarantee; review full support and original provenance before approval, and reuse an exact verified hit only within its returned scope. Preserve actual errors, uncertainty and missing preparation — a pointer error must never be hidden inside a "no candidates" reply. Connected means registered, not automatically synced; never invent a checked date or treat cache approval time as source freshness. No new access, privacy approval, execution permission, automatic syncing, or autonomous scheduler is granted here.

## Banned patterns

Do not: treat "preparation required" as an answer; repeat a failed search instead of connecting or refreshing; say "your whole brain is connected" or "always up to date" without evidence of that exact capability; bury a pointer error under a "no results" summary; approve an answer without reading its evidence.

## Deeper guides

[Connector setup, onboarding and refresh](../super-jev-connect/SKILL.md) (bulk prepare, labels, held files, refresh) · [connector reference](references/connectors.md) · [verified reuse](references/verified-reuse.md) · [checking](references/checking.md) · [feedback recording](references/feedback.md)

On misses, incomplete evidence, or review failures, follow [feedback recording](references/feedback.md); report assistance separately from an automatic hit.
