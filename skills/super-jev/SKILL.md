---
name: super-jev
description: "Find skills and reviewed brain/doc evidence through Super Jev source connectors. Check claims, verify agent work, and reuse explicitly approved answers with bounded reviewed-source recovery. Evidence still needs caller review."
---

# Super Jev

One front door: `python3 <this-skill-directory>/dispatch.py <tool> ...`. Use the user's original request and relevant context; keep backend names and setup mechanics out of ordinary replies.

## Commands

| Command | Example | What it does |
|---|---|---|
| `ask` | `python3 ask.py --principal YOUR_AGENT "question in your own words"` | The loop: harness cache first, then every pointer in parallel; open the top file; `--approve` a good hit so the next ask is instant; `--add` a fact with no file (one small pointer per entry, earlier answers untouched); `--miss` when grep found it elsewhere. Errors are printed per pointer and never hidden as no-candidates. |
| `navigate` | `memory --input '{"action":"navigate","pointer":"POINTER_ID","principal":"YOUR_AGENT_NAME","question":"..."}'` | Lower-level call: `ask` wraps this to search one connected pointer's catalog for candidate files. |

**Present setup help plainly:** When a response includes `message` and `setup`/`gettingStarted`, lead with that message and take the authorized next step. Do not tell a first-time user only “preparation required”; that is a machine status, not useful instructions. If they have no records yet, help create a factual collection from their supplied information before connecting it.

**First use:** confirm the selected tool runs and the intended person's/project's records are prepared and connected. If records have only pointers or return `preparation-required`, use the memory `connect` action in [connector setup](references/connectors.md) to prepare authorized local text files. If other setup is missing, follow its hint; never treat a setup failure as “nothing found.” Existing working connections skip this step.

From this skill directory (replace dataset/pointer and principal with your authorized configured values):

```sh
python3 dispatch.py help --topic overview
python3 dispatch.py find --list-datasets
python3 dispatch.py find --dataset DATASET_ID --request "Your original question"
python3 dispatch.py memory --describe
python3 dispatch.py memory --connect my-records --file /absolute/path/to/record.md --principal YOUR_AGENT_NAME
```

The connect command previews files without publishing them. Review their contents and existing provider permission, then run the exact `confirmCommand` it returns. Repeat `--file` for more files. This shortcut creates a single-principal connector; use the JSON guide for shared scopes.

**Checked connect (preferred):** `python3 connect_checked.py CONNECT.json` gates every description against its file with Jev first (SUPPORTED at 0.80 or better), refuses the whole connect on any failure or any file over the 32k-token ceiling, and only then previews and confirms. Add `--check-only` to just check. Verdicts with file hashes land next to CONNECT.json.

**Many files, or a whole agent brain? Bulk prepare:** `python3 prepare_bulk.py --root DIR [--root DIR2 ...] --pointer NAME --principal YOUR_AGENT_NAME [--exclude SUBPATH ...] [--no-recurse] [--limit 50] [--max-files 250] [--refresh]` inventories the union of one or more root folders in the order given (skips hidden dirs, backups, vaults, and any `--exclude`d subpath; `--no-recurse` limits each root to its direct children), holds back files with card/password-like text or over the size ceiling and writes a `prepare-cache/<pointer>-held.txt` with each hold's reason and, for the secret-pattern case, the matching pattern type, line number and a digit-masked line for review, has a cheap writer (`claude -p --model haiku`) draft one description and one sample question per file, gates every description with Jev (one rewrite retry), connects the passing set through the same checked path in parts of at most `--limit` (default 50, the connector's hard cap) named `<pointer>`, `<pointer>-2`, ... when it splits, then checks each file ranks first on its own question. Cache by file hash under `prepare-cache/` so unchanged files are skipped on rerun; `--refresh` also drops any cached file removed from disk and notes it in the report, and warns before a `replace:true` reconnect rotates a pointer's approved answers. Read the HELD and EXCEPTION lines; they are yours to resolve. Limits: `--max-files` (default 250) caps a single run, sample-question findability is a soft check. The writer also drafts four gated labels per file — `kind`, `status`, `as_of`, `subject` — folded into the one claim Jev checks and carried into the connect description; `python3 prepare_bulk.py --list --pointer NAME [--status active] [--kind dashboard] [--within-days 30] [--subject NAME]` then filters the already-gated labels back out of the cache with no writer, gate, or memory call.

For configured memory, `python3 dispatch.py memory --principal YOUR_AGENT_NAME` lists your pointers. Save a search request in your repo's private working area, then run `python3 /path/to/skill/dispatch.py memory --input /path/to/request.json`:

```json
{"action":"search","pointer":"POINTER_ID","principal":"YOUR_AGENT_NAME","question":"Your original question"}
```

Installed `memory.sh` supplies the runtime/config automatically when no explicit repo/config is supplied. Without it, set `SUPERJEV_REPO` to the checkout and pass `memory --config /path/to/config.json ...`; consult `LOCAL-MEMORY.md` if present. `--describe` reports capabilities, not whether records are onboarded. On `ready`, inspect evidence before answering or approving reuse; `verified-cache-hit` returns an already approved scoped answer. Passage-level `no-match` can be a false refusal: open the file `navigate` points at before concluding the fact is absent. Missing preparation requires `connect` onboarding/refresh, not repeated searching. On `preparation-required`, follow the returned setup steps when authorized; do not merely repeat the failed search or ask another agent to prepare unrelated records. After connecting, retry through `memory` with the returned pointer—the old `find` dataset may still be unprepared. The harness chunks and registers files; the agent reviews source scope and permission. Existing authorization may cover this review—do not ask the human to approve each chunk.

**Find the right file first:** For a connected pointer, submit `{"action":"navigate","pointer":"POINTER_ID","principal":"YOUR_AGENT_NAME","question":"Your original question"}` through `memory --input request.json`. Prefer `flat-files` for file finding; folder-tree remains experimental. The connector supplies its reviewed view; Jev guides a bounded search and returns candidate file pointers, not verified answers. Each candidate carries `originalPath`, `score` and `description`; open the top file before answering. Scores are relative to the connected set, so a high score never proves the file is in scope. `no-candidates` or an exhausted budget does not prove absence. At onboarding, choose the structure in [connector setup](references/connectors.md#navigation-structure); existing pointers can use a flat view without re-onboarding.

**Already connected?** Use the known roots, dataset or pointer directly. Do not reload manuals, reopen the panel, enumerate sources or repeat onboarding on every request. Revisit setup for new/changed scope, missing configuration or a reported preparation/freshness problem. Runtime integrity checks stay enabled.

**Maturity:** proven in current use: skill search, file-level `navigate`, checked connect, bulk prepare, `check` as a description gate, dataset and pointer listing, and the not-connected path. Experimental: passage-level `search`, saved-answer reuse (`approve`), and `verify`; treat their results as leads and read the evidence twice. Database and Website/links connectors remain proposed only, not built.

**New agent or need help?** Use `help --topic overview` for Quick Start, `help --question "your how-to question"` for suggested help topics, or `help --topic TOPIC` for an exact topic. Built-in answers need no data, key or Jev call; they cover maintained setup topics, not arbitrary facts. If instructions are already known, skip help. Read longer guides only when needed.

| Tool | Purpose |
|---|---|
| `help` | Quick Start and focused setup answers |
| `skills` | Skills connector: discover candidate skills; suggestions never execute them |
| `find` | Brain/Documents/Repo connectors: retrieve from reviewed local datasets |
| `memory` | Use registered pointers, reviewed recovery and explicitly approved exact-answer reuse |
| `check` | Check a claim or draft against evidence |
| `verify` | Verify an agent report against evidence |

`tools` lists executable tools and separate agent-guided setup workflows. `memory --describe` exposes settings, actions and connector limits without setup. Existing configured pointers use `memory`; `find` alone does not save approved answers. Follow `LOCAL-MEMORY.md` and its `memory.sh` when installed; otherwise configure the documented experiment. The legacy `superjev.py ask` router is not this front door.

Load only the selected workflow's guidance if it is not already known: sibling `skill-search/SKILL.md` for discovery; sibling `fleet-retrieval-experiment/SKILL.md` for reviewed retrieval/preparation; [verified reuse](references/verified-reuse.md) for memory; [checking](references/checking.md) for check/verify. Skill roots follow the caller; check that a suggested skill's tools are available before using it. Existing advanced commands and hooks remain unchanged.

Quick boundaries: `ready` is evidence, not an answer guarantee. Review full support and original provenance before approval; exact verified hits are reusable only within their returned scope. Preserve actual errors, uncertainty and missing preparation. Do not hide ordinary-search, alternate-model or different-dataset recovery; authorized assistance follows the bounded memory guide. No new access, privacy approval, execution permission, automatic syncing or autonomous scheduler is granted here.

For connecting existing data, starting blank or defining a user-named connector, ask help first if needed, then use [connector setup](references/connectors.md) for the procedure. Existing originals can keep their structure; the starter layout is optional. Connector labels describe workflows, not extra executable commands or proof of readiness. Hints are optional advice, never mandatory user prompts. For current-state questions or freshness problems, explain the returned freshness limit briefly; do not repeat it on every ordinary hit. Connected means registered, not automatically synced. Never invent a checked date or treat cache approval time as source freshness.

On misses, incomplete evidence or review failures, preserve the initial result and follow [feedback recording](references/feedback.md). Use the existing non-riding feedback card when available; detailed/private evidence stays local. Report assistance separately from an automatic hit. Normally tell the human the answer or concrete next step, not the internal ceremony.
