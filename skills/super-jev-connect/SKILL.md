---
name: super-jev-connect
description: "Onboard and refresh Super Jev connectors: register skills, brain notes, project queues, manual facts, documents/repo/tool docs and fleet datasets as reviewed pointers; draft and gate their kind/status/as_of/subject labels with a confirmed cheap writer model; refresh a pointer when its files change. Use before first use of Super Jev, on a preparation-required or unknown-pointer error, or when connected files changed."
---

# Super Jev Connect

Onboarding and refresh for [Super Jev](../super-jev/SKILL.md). Scripts live in `skills/super-jev/`; run them by relative path from here, e.g. `../super-jev/prepare_bulk.py`.


## What to connect

Connect what agents will search again: notes, lessons, decisions, runbooks, a code library they keep asking about. Don't connect one-off material (a single report, a draft, today's diff, a pasted page); check it directly with `dispatch.py check FILE --claim "..."` from the super-jev skill instead. Connecting costs writer and Jev calls up front and keeps the files in every lookup; a direct check costs one call and keeps nothing.
## 1. What a connector is

A connector is one named set of reviewed files, registered as one pointer, for one or more principals (agents). "Connected" means registered and prepared for search — never automatically synced to the live source. Update a connector by refreshing it (section 8), not by assuming it tracks its source.

## 2. Connector kinds

| Kind | What it is |
|---|---|
| Skills | Skill roots discovered by name/description; no pointer to register |
| Brain | An agent's own notes |
| Project queue | A pending-work folder |
| Manual entries | One small pointer per approved fact, added with `ask.py --add` (no expiry; stale only if its `--source` file changes) |
| Documents / Repo / Tools docs | Reviewed local text: documents, a repo checkout snapshot, or tool documentation |
| Fleet datasets | A shared reviewed set used by more than one agent (shared knowledge folder, skills catalog); see section 10 |
| Database, Website | Proposed only — not built |

## 3. Naming rule

`<principal>-<connector>[-<section>][-<part>]`, e.g. `agent-brain-notes`, `agent-brain-notes-2` (an auto-split part when a folder exceeds the 50-file connect cap). Manual entries are always `<principal>-manual-<hash>`. List your own pointers with:

```sh
python3 dispatch.py memory --principal NAME
```

## 4. Decision rule

- Original files that must stay redacted → attach a reviewed declarative viewTransform policy per source; follow [reviewed-view recipes](../super-jev/references/reviewed-views.md). Never reconnect a redacted legacy dataset as raw originals.
- A handful of hand-picked files → `python3 connect_checked.py CONNECT.json`
- A whole folder, or an agent's whole brain across several folders → `python3 prepare_bulk.py --root DIR --pointer NAME --principal NAME`
- Markdown only: code and other text files are not supported. A note over 250,000 bytes is held "too big, split it"; split it into smaller .md files and reconnect.
- A GitHub repo's history (PRs with reviews, issues, commit messages, release notes) → `python3 connect_github.py OWNER/REPO --pointer NAME --principal NAME` (or `superjev connect-github ...`); add `--refresh` after each merge to fetch only what changed. Uses your signed-in `gh`; secret-looking lines are dropped. See [connectors.md](../super-jev/references/connectors.md#github-repo-history).
- A fact with no backing file → `python3 ask.py --principal NAME --add "question" "answer"`
- A human at the `superjev` terminal chat → drag the file or folder into the window: it shows what will connect and asks first (a folder defaults to No), then runs `prepare_bulk.py` for them.

Cost: each connected file gets its description and labels checked by Jev; small files are checked several to one call (`SUPERJEV_BATCH_JEV=0` checks one file per call). The findability report (each file's own sample question searched after connecting) is off by default; add `--findability` for it, one paid search per file.

Before connecting a folder, `python3 ../super-jev/ask.py --principal NAME --preflight --project-dir DIR` shows how many of its files are already connected (free), so you do not connect it twice.

## 5. Cheap writer model — required

Bulk labeling drafts descriptions, sample questions and labels with a cheap writer model, via `--writer-command` (or the `SUPERJEV_WRITER_COMMAND` env var). Before the first bulk run on any project, confirm the writer command and its cost with the operator once, and record the choice. A proven example: `claude -p --model haiku` (Claude Code CLI, Haiku) — this is `prepare_bulk.py`'s own default when neither flag nor env var is set and the `claude` CLI is installed, and it prints a `writer:` banner naming whichever command actually runs. With no `claude` CLI, or with `--writer builtin`, a no-model writer quotes each file's headings instead (labels stay unknown). The writer reads an excerpt, not the whole file: the headings (evenly sampled past 15) and the first 1,200 characters, plus, for a file over 12,000 characters, 8 short passages spread across it; the gate still judges the whole file. Any command that reads the prompt on stdin and prints a JSON array works. Never run bulk labeling on a premium model.

## 6. Labels

Bulk prepare and manual entries draft four labels — `kind`, `status`, `as_of`, `subject` — gated separately from the description, so a label problem never blocks a file from connecting. `prepare_bulk.py --list [--pointer NAME|--principal NAME] [--status S] [--kind K] [--subject TEXT] [--within-days N]` filters already-gated labels locally, with no judge call. Honesty: a label is only as true as the file it was drafted from; `as_of` shows staleness, not currency — live truth for anything time-sensitive still needs a gated roll-up read fresh, never a cached label.

## 7. Held files

The secret scan holds a file that looks like it carries card/password text, and separately holds any file over the 250,000-byte size ceiling ("too big, split it": no sections, no size approval); `prepare-cache/<pointer>-held.txt` records each hold's reason (and, for the secret-pattern case, the matching line and a digit-masked excerpt) so a human can review without opening the file. A held file has no override: for secret-like text or name, or a credential/key suffix, Super Jev never sends that text, so remove or move the value; for a size hold, edit or split the note; then reconnect. Real secret files and vault-style folders stay out of every run regardless. Every connect ends with `CONNECTED n, HELD m, FAILED k` and exits 0 only when m and k are 0 (1 if anything failed, 3 if anything was held): read that line, never report "connected" while HELD or FAILED is above 0.

## 8. Refresh

`--refresh` replays the pointer's recorded recipe (roots, principals, excludes, --no-recurse, --name, --limit from `prepare-cache/<pointer>-report.json`) for anything not given on the command line (a new `--root` does not inherit the old `--no-recurse`), so `prepare_bulk.py --refresh --pointer NAME --principal NAME` is enough and never widens the pointer. `--limit` is the per-part size (at most 50 files per connect request), not a cap on total files. A report written before recipes were recorded is pinned to its recorded file list (kept as `scopeFiles`), so a grown root is not re-inventoried; its folders take in new notes through a one-time growth snapshot: files already there but not in the list are listed as WAITING (the report never recorded its `--exclude`, so they may have been left out on purpose) and join only with `--refresh --admit PATH` after a person checks each; a file created after the snapshot joins on its own when its folder had no waiting file (or all were admitted), no other pointer's files share that folder, it is not links.md/INDEX.md, the folder is not inside profile/, documents/ or another vault folder, and there are at most `--max-files` such files (same holds, review and gate); files in other folders stay out; pass a new `--exclude`, `--no-recurse` or a different `--root` to rescope it, or re-run without `--refresh` (full `--root` inventory) to take in the whole folder. A held file (too big, or secret-like) has no override: edit or split it, then re-run. It re-runs the pointer against current disk state and drops any cached file no longer present. A changed file stales its whole pointer until refreshed. Reconnecting an existing pointer sends `replace:true`, which rotates that pointer's approved answers — so keep cached/approved answers on small, stable pointers (manual entries), and refresh larger, more volatile pointers deliberately. `python3 ../super-jev/refresh_changed.py [--dry-run] [--skip NAME]` re-prepares only the pointers whose connected files changed or whose folders hold a file no pointer has seen, with each pointer's recorded roots, principal, excludes and --no-recurse (its first line is the outcome: `fresh`, `refreshed`, `stale` (--dry-run), `needs-setup` or `error`, and the exit code matches: 0, 0, 3, 2, 1; a report that records no principal is `needs-setup` with the one command to run, after which it heals itself); schedule it yourself if you want it periodic. An ask does the new-file part on its own: at most once per 10 minutes per agent it scans that agent's pointers in the background and auto-heals (same lock, queue, cooldown and hourly cap) each one with a new file, a report that records no principal is healed as the asking agent, which the refresh records (the pointer keeps its registered scope); a later ask finds the file. A pointer whose root sits inside profile/, documents/ or another vault folder is never scanned for new files. `SUPERJEV_NEW_FILE_SCAN=0` turns the scan off. Keep auto-rebuilt files (links.md, INDEX.md) out of a pointer with --exclude so they do not stale it daily.

## 9. Safeguards

`preparation-required` and an unknown pointer are setup errors, never "nothing found" — follow the setup step, don't repeat the search or ask another agent to prepare unrelated records. An ask reports them as `OUTCOME: needs-setup` (exit 4, with the one command to run) when nothing was found, or as `partial: N sets not searched` on a `found`; act on the `next:` command, then ask again. (The kinds are `found`, `not-found`, `not-supported`, `needs-setup`, `error`; details in the super-jev skill.) `connect`, `connect_checked.py` and `prepare_bulk.py` always preview before publishing; nothing is registered until the confirm step runs. Originals are never edited — only reviewed copies are prepared and sent to the provider.

## 10. Shared sets and onboarding defaults

Every agent should see the fleet's shared sets (shared knowledge folder, skills catalog), not only its own brain. Connect a shared set once, then share it: `python3 ../super-jev/share_pointers.py --principal NAME --pointer POINTER` (exact names, `--dry-run` to preview). Every connection is private until a person marks it shareable: `prepare_bulk.py --shareable` at connect, or `share_pointers.py --principal NAME --mark POINTER` later (`--unmark` undoes it); an unmarked pointer is refused, and one with any source in an agent's brain, `documents/` or `profile/` cannot be marked. It uses the memory `register` action on the already-connected pointer: no reconnect, no writer, no Jev call, zero cost. It drops that pointer's cached answers (new generation), and a stale pointer must be refreshed first.

List the shared sets once in `~/.local/state/super-jev/shared-pointers.json` (`{"pointers": ["fleet-knowledge", "main-skills-catalog"]}`, exact names). Every fully connected `prepare_bulk.py` run then shares them with its `--principal`s (`--no-shared` to skip); for an agent already connected run `share_pointers.py --principal NAME --shared`. Never connect a per-agent copy of a shared folder; share the one pointer.

Git worktree copies (`.claude/worktrees/`, or any checkout whose `.git` file points into `.git/worktrees/`) are never inventoried, even as a `--root`: they are stale copies of a brain or repo.

Deeper reference: [connector setup](../super-jev/references/connectors.md). Daily use of an already-connected pointer: [`super-jev/SKILL.md`](../super-jev/SKILL.md).
