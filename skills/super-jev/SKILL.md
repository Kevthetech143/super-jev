---
name: super-jev
description: "Find skills and reviewed brain/doc evidence through Super Jev source connectors. Check claims, verify agent work, and reuse explicitly approved answers with bounded reviewed-source recovery. Evidence still needs caller review."
---

# Super Jev

Run from this skill directory: `python3 dispatch.py <tool> ...` (or `ask.py` directly, as shown below). Use the user's original request and relevant context; keep backend names and setup mechanics out of ordinary replies.

## Commands

| Command | Example | What it does |
|---|---|---|
| `ask` | `python3 ask.py --principal YOUR_AGENT "question in your own words"` | Cache first, then every connected pointer at once: routing questions for all pointers, and the content checks for all files read, are batched into as few Jev calls as fit under its input ceiling (split automatically; `SUPERJEV_BATCH_JEV=0` goes back to one call per pointer/file); open the top file before answering. `--approve` a good hit so the next ask is instant (add `--rank N` or `--file PATH` to approve a different listed candidate than the top one, possible-tier included, from that candidate's own pointer, quoting only that exact file: its passage that best matches the answer, cited from the file's own reviewed lines when search ranked another file first); `--add` a fact with no file; after using a listed file, leave a trail with `--used last --rank N --answer "..."` (every 5 picks the claim checker saves CLEAN ones as `agent-pick+check`; `--pending-picks` lists unsure ones AND refused ones (REJECT/TIME_SENSITIVE/stale/secret-held), each with its reason, to `--confirm-pick`/`--drop-pick` -- a refused pick no longer vanishes silently, see [verified reuse](references/verified-reuse.md)); `--miss` when found elsewhere. A saved answer whose `--source` file changed is withheld (STALE) and the question falls through to a fresh live search; re-add it with `--add --replace-entry`. Errors print per pointer, never hidden as no-candidates; a stale pointer's line prints the exact refresh command (roots and every principal included) to run as-is. Bench datasets under `.local/retrieval-datasets/` are never listed. Index-type files (INDEX, CATALOG, handoff, templates) rank below real notes; a README ranks as a note once its own content check confirms the answer (a folder dashboard); a possible-tier README with a content score ranks below scored notes but above route-only ones. No candidates means not found, not absent: say it may still exist and offer a by-hand search. A strongly routed file too long to read whole stays possible with the section to start at. A hub yields to a confirmed or higher-scored note in its own folder, a `_staging`/template copy to the real note, a PR-history write-up to a confirmed doc. A question about one person ("my dad", a name, "me") only reads and confirms that person's `global/documents/<name>/` records; group words (parents, kids, family, our) filter nothing. Within a tier, a file read in several passages ranks on its best passage plus half the on-topic share the other passages took; the tier itself still needs one passage >= 0.60 (possible) or >= 0.85 (confirmed). A pointer holding none of a 2+ word question's words or synonyms is not routed, unless it holds the asked-about person's folder. Once every file that reached the possible tier has been content-checked, one extra listwise Jev call asks which single one best answers the question; the winner moves to #1 (never demoting anything else) and is promoted to confirmed only if its own winning probability is itself >= 0.9 (`SUPERJEV_LISTWISE=0` turns this off and keeps today's ranking; a failed/timed-out/no-opinion call also keeps today's ranking); a hub/copy/write-up winner never moves back above a non-hub file already ranked ahead of it, so it can't undo the hub-yields-to-a-source-note rule above. |
| `navigate` | `python3 dispatch.py memory --input NAV.json` (a JSON file path, not inline JSON: `{"action":"navigate","pointer":"POINTER_ID","principal":"YOUR_AGENT_NAME","question":"..."}`) | Lower-level call `ask` wraps: searches one connected pointer's catalog for candidate files. Open the top file; a high score never proves it's in scope. |
| `skills` | `python3 dispatch.py skills --request "..."` (or `--request-file REQUEST.json`) | Skills connector: discover candidate skills from configured trusted local roots; suggestions never execute them. |
| `check` | `python3 dispatch.py check --claim "..." FILE` | Check a claim or draft against evidence. A `--claim` check is decided by its claim rows only; HAS_LEAKS, TIME_SENSITIVE and SELF_CONTRADICTORY print as advisory (OVERCLAIMS still blocks), since they judge an outbound letter, not a note. `ask --answer` still refuses to auto-cache a TIME_SENSITIVE answer, but only when that label's own confidence is >= 0.80 -- a low-confidence TIME_SENSITIVE call is noise, not a finding, and no longer blocks a correct, fully-supported answer. |
| `verify` | `python3 dispatch.py verify REPORT` | Verify an agent's work report (git, tests, PRs, whether cited paths exist). It does not read cited files' contents, so a fact claim about a note comes back unchecked; use `check --claim "..." FILE` for facts. |
| `help` | `python3 dispatch.py help --topic overview` | Quick Start and focused setup answers; no data, key, or Jev call needed. |

## Not connected yet?

A `preparation-required` result, an unknown pointer, or onboarding a new person/project is setup work, not "nothing found": see [`super-jev-connect/SKILL.md`](../super-jev-connect/SKILL.md). Check what you already have first:

```sh
python3 dispatch.py memory --principal YOUR_AGENT_NAME
```

## Live decision traces and Jev's voice

Every lookup (cache hits too, tier `cache`/`stale`) appends one redacted JSON line to `$STATE/traces.jsonl`: id, question, routing, content-check scores, final ranking, tier, timings, errors — never file contents or secrets. Rotates at ~20MB (one `.1` kept). `--approve` marks the last lookup for that question "right", `--miss` "wrong", `--add` after a miss "wrong, added". `ask.py --principal YOUR_AGENT --trace-report [--days 7]` reports right/wrong/unlabeled and top wrong questions. Each line also carries per-stage detail (cache, routing with Jev's none-probability, word-search top 10 (BM25 per 3500-character passage, a file scores its best passage; a written phone number counts as "phone number"; a file under 0.55x the best not-yet-routed word score takes no read slot) and each file's fate, chunks/wording per content check, tie-break, final rule); `ask.py --principal YOUR_AGENT --trace-show <lookup_id|last>` prints it as plain lines to see where a file dropped out.

When a lookup returns no usable answer, the last line `ask.py` prints is exactly:

```
Super Jev: I didn't have this. Want me to find it by hand and save it for next time?
```

**A harness relaying `ask.py` output to a human must pass that line on verbatim.**

## Maturity

Proven in current use: skill search, file-level `navigate`, checked connect, bulk prepare, `check` as a description gate, dataset and pointer listing, and the not-connected path. Experimental: passage-level `search`, saved-answer reuse (`approve`), and `verify` — treat their results as leads and read the evidence twice.

## Safeguards

`ready` is evidence, not an answer guarantee; review full support and original provenance before approval, and reuse an exact verified hit only within its returned scope. Preserve actual errors, uncertainty and missing preparation — a pointer error must never be hidden inside a "no candidates" reply. Connected means registered, not automatically synced; never invent a checked date or treat cache approval time as source freshness. No new access, privacy approval, execution permission, automatic syncing, or autonomous scheduler is granted here. Test/scratch output is never connected or word-searched when named `ops/sj*/...` (not `ops/sj-manual/`), `*superjev-test*` or `*-hand-test-*`; save test reports under those names or outside connected folders so they can't come back as answers.

A pointer stuck `preparation-required`/`refresh-required` is just waiting on its own refresh, not a live failure — it's marked `[STALE]` in the error line, never counts toward the sick-pointer circuit breaker, and never gets benched. If every one of its files is already reviewed at its current bytes (nothing to redraft), the ask reconnects it before reading (no writer or Jev calls, one 45 s budget for all reconnects in the lookup; a failed or timed-out reconnect sets no cooldown) and asks it again in the same lookup; a split part (`<pointer>-2`) heals through its parent's recipe. A file that really changed still gets the bounded background refresh. A principal's own `<principal>-brain-root` pointer (its own brain) is never benched either, even after real repeated failures — it still shows its true status every call rather than a generic "benched" message.

`ask.py --principal YOUR_AGENT --followup` re-tries pending misses and proposes a fresh top file for one-step `--approve` only when it's confirmed, isn't the exact file already named wrong, and actually shares subject terms with the question — a same-topic neighbor file (e.g. a different date's report in the same folder) can out-score everything on routing alone without being about what was asked, so it's never proposed.

## Banned patterns

Do not: treat "preparation required" as an answer; repeat a failed search instead of connecting or refreshing; say "your whole brain is connected" or "always up to date" without evidence of that exact capability; bury a pointer error under a "no results" summary; approve an answer without reading its evidence.

## Deeper guides

[Connector setup, onboarding and refresh](../super-jev-connect/SKILL.md) (bulk prepare, labels, held files, refresh, GitHub repo history via `connect_github.py`) · [connector reference](references/connectors.md) · [verified reuse](references/verified-reuse.md) · [checking](references/checking.md) · [feedback recording](references/feedback.md). On misses, incomplete evidence, or review failures, follow [feedback recording](references/feedback.md); report assistance separately from an automatic hit.
