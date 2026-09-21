---
name: super-jev-connect
description: "Onboard and refresh Super Jev connectors: register skills, brain notes, project queues, manual facts, documents/repo/tool docs and fleet datasets as reviewed pointers; draft and gate their kind/status/as_of/subject labels with a confirmed cheap writer model; refresh a pointer when its files change. Use before first use of Super Jev, on a preparation-required or unknown-pointer error, or when connected files changed."
---

# Super Jev Connect

Onboarding and refresh for [Super Jev](../super-jev/SKILL.md). Scripts live in `skills/super-jev/`; run them by relative path from here, e.g. `../super-jev/prepare_bulk.py`.

## 1. What a connector is

A connector is one named set of reviewed files, registered as one pointer, for one principal (agent). "Connected" means registered and prepared for search — never automatically synced to the live source. Update a connector by refreshing it (section 8), not by assuming it tracks its source.

## 2. Connector kinds

| Kind | What it is |
|---|---|
| Skills | Skill roots discovered by name/description; no pointer to register |
| Brain | An agent's own notes |
| Project queue | A pending-work folder |
| Manual entries | One small pointer per approved fact, added with `ask.py --add` |
| Documents / Repo / Tools docs | Reviewed local text: documents, a repo checkout snapshot, or tool documentation |
| Fleet datasets | A shared reviewed set used by more than one agent |
| Database, Website | Proposed only — not built |

## 3. Naming rule

`<principal>-<connector>[-<section>][-<part>]`, e.g. `agent-brain-notes`, `agent-brain-notes-2` (an auto-split part when a folder exceeds the 50-file connect cap). Manual entries are always `<principal>-manual-<hash>`. List your own pointers with:

```sh
python3 dispatch.py memory --principal NAME
```

## 4. Decision rule

- A handful of hand-picked files → `python3 connect_checked.py CONNECT.json`
- A whole folder, or an agent's whole brain across several folders → `python3 prepare_bulk.py --root DIR --pointer NAME --principal NAME`
- A fact with no backing file → `python3 ask.py --principal NAME --add "question" "answer"`

## 5. Cheap writer model — required

Bulk labeling drafts descriptions, sample questions and labels with a cheap writer model, via `--writer-command` (or the `SUPERJEV_WRITER_COMMAND` env var). Before the first bulk run on any project, confirm the writer command and its cost with the operator once, and record the choice. A proven example: `claude -p --model haiku` (Claude Code CLI, Haiku) — this is `prepare_bulk.py`'s own default when neither flag nor env var is set, and it prints a `writer:` banner naming whichever command actually runs. Any command that reads the prompt on stdin and prints a JSON array works. Never run bulk labeling on a premium model.

## 6. Labels

Bulk prepare and manual entries draft four labels — `kind`, `status`, `as_of`, `subject` — gated separately from the description, so a label problem never blocks a file from connecting. `prepare_bulk.py --list [--pointer NAME|--principal NAME] [--status S] [--kind K] [--subject TEXT] [--within-days N]` filters already-gated labels locally, with no judge call. Honesty: a label is only as true as the file it was drafted from; `as_of` shows staleness, not currency — live truth for anything time-sensitive still needs a gated roll-up read fresh, never a cached label.

## 7. Held files

The secret scan holds a file that looks like it carries card/password text, and separately holds any file over the size ceiling; `prepare-cache/<pointer>-held.txt` records each hold's reason (and, for the secret-pattern case, the matching line and a digit-masked excerpt) so a human can review without opening the file. `--allow-held` admits a file the secret scan alone would hold — it stays listed in the held file, noting the override — as an explicit operator decision; it never lifts the size-ceiling hold. Real secret files and vault-style folders stay out of every run regardless.

## 8. Refresh

`--refresh` re-runs a pointer against current disk state and drops any cached file no longer present. A changed file stales its whole pointer until refreshed. Reconnecting an existing pointer sends `replace:true`, which rotates that pointer's approved answers — so keep cached/approved answers on small, stable pointers (manual entries), and refresh larger, more volatile pointers deliberately. A hash-watch job that refreshes automatically on file change is not built yet.

## 9. Safeguards

`preparation-required` and an unknown pointer are setup errors, never "nothing found" — follow the setup step, don't repeat the search or ask another agent to prepare unrelated records. `connect`, `connect_checked.py` and `prepare_bulk.py` always preview before publishing; nothing is registered until the confirm step runs. Originals are never edited — only reviewed copies are prepared and sent to the provider.

Deeper reference: [connector setup](../super-jev/references/connectors.md). Daily use of an already-connected pointer: [`super-jev/SKILL.md`](../super-jev/SKILL.md).
