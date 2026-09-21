# Source connectors

A connector is the source-specific way an agent connects data to Super Jev. Use these names when explaining the product. Existing connector workflows share backends; these labels do not create a `connect` command, a new plugin, or automatic synchronization.

| Human-facing connector | Available today | Agent execution/setup |
|---|---|---|
| Skills | Discovery from configured trusted local skill roots | `skills` via sibling `skill-search`; validate the selected roots and each skill's name/description metadata. A scan reads the catalog; no continuous sync service is installed. |
| Brain | Reviewed local records from a specified brain/person/project | `find` for reviewed datasets; configured `memory` for registered pointers and verified reuse. Scope is the listed records, not the whole brain. |
| Documents | Reviewed local text documents | Same reviewed dataset and optional memory workflow as Brain. Extraction of arbitrary PDFs or other formats is not automatic. |
| Repo | A reviewed snapshot of local checked-out repository files | Same local-file preparation workflow. Record the revision and included files. Remote cloning/fetching, merge-triggered refresh and repo watching are not bundled connectors yet. |
| Database | Proposed | No direct database ingestion connector. SQLite answer storage is not a database-source connector. An authorized reviewed export can use Documents, but say it is an export. |
| Website/links | Proposed | No direct URL ingestion/refresh connector. An authorized reviewed local copy can use Documents, but say it is a copy, not a live connection. |

For common setup questions, use `dispatch.py help --question "your question"`; choose `help --topic TOPIC` for a maintained exact topic answer. Help ships with the skill and does not require a connection, key or fresh Jev call. It is not a guarantee of coverage for every how-to question. The guide below supplies the detailed workflow when needed.

## User-defined connectors and names

Skills, Brain, Documents and Repo are convenient starting labels, not a closed list. Users may name a connector for their own source or purpose, such as “Workshop notes” or “Customer handbook.” Use their chosen name in conversation and a valid stable dataset/pointer identifier in tool inputs; a display name is never an executable command.

For a custom connector, keep a short setup note in the user's project: chosen name, purpose, authorized sources/audience, existing backend and dataset/pointer, preparation steps, refresh responsibility, and known limits. Reuse an appropriate configured connection instead of duplicating its data. The note is an agent-readable recipe, not an automatically discovered plugin manifest; the current panel lists registered pointers, not a separate connector registry.

A new label can reuse the reviewed-local-file workflow. A genuinely new source integration needs an implemented and verified adapter; naming it does not create database/URL fetching or syncing. Developers can build their own integration and connect it through the existing reviewed dataset workflow or trusted `retrievalCommand` extension. Preserve its actual interface, source bindings, scope and explicit approval checks; describe an unbuilt custom adapter as needing implementation, not ready.

## Choose existing data or a blank start

If data already exists, preserve the originals and prepare a searchable view of the authorized scope. Do not require a directory rewrite, database migration, or a new table. Messy or unsupported formats may need extraction and agent review before onboarding; a connector label does not make arbitrary data readable.

If the user is starting from scratch, offer this small optional starting layout. It follows the descriptions, stable references and source checks used by the current workflow; it is not a benchmark-proven optimal format or a hard input requirement. Once the user has asked to create the collection, the agent can build it in the user's project using their actual records—do not invent facts to fill it.

```text
INDEX.md           # small catalog of records
records/
  example.md       # one coherent topic or record per file
```

Suggested index columns:

| id | description | path |
|---|---|---|
| project-setup | Where this project's setup steps and prerequisites are recorded | records/project-setup.md |

Keep IDs stable, descriptions factual and specific, and paths resolvable. Use headings inside records; retain sources for factual claims. Add status, event/effective dates and supersedes links when records change over time; distinguish when a fact applies from when its file was edited. Unknown values remain unknown. Skills instead need valid `name` and `description` frontmatter and their normal `SKILL.md` structure—use the available skill-creation workflow, not the record template.

No user-managed database is needed for this baseline. The index is an authoring aid, not an automatically parsed import contract. The memory `connect` action prepares explicit text-file paths and registers a pointer after agent review; it maintains the manifest and passages internally. Follow the same sample-query and freshness checks below. Do not claim a collection is connected merely because files or an index were created.

Friendly setup hint: “Starting fresh? I can create a small, clearly described collection that works with Super Jev's existing preparation workflow. If you already have data, we can keep its structure and prepare a searchable view instead.” Give this hint when the source is absent or the user asks how to start, not on every successful lookup.

## Set up a new connection or repair an existing one

This is onboarding/repair guidance, not a per-query checklist. Once connected, use the known tool and dataset/pointer directly. Repeat discovery, setup checks or sample tests only when the connection or scope changes or the runtime reports a problem.

1. Identify the connector, exact source scope and authorized audience. Reuse known context; ask only for missing information needed to choose the right data/person. A connector does not grant new access.
2. Inspect existing setup. For Skills, read sibling `skill-search/SKILL.md` and its roots configuration. For Brain/Documents/Repo, list reviewed datasets through `find --list-datasets`; if local memory is configured, inspect its panel and registered sources. Follow installation-specific `LOCAL-MEMORY.md` and `memory.sh` when present.
3. Reuse an appropriate ready dataset/pointer. For missing passages, use the `connect` action below with the authorized original text-file paths; the harness creates descriptions, chunks, source bindings and registration. A pointer-only index is not the source content. For a custom reviewed/redacted projection, use the existing sibling `fleet-retrieval-experiment/SKILL.md` preparation workflow instead. Do not treat repo membership as privacy approval.
4. Run a relevant sample request. Verify returned supporting passages and any approved exact-repeat answer. Report the observed outcome; one successful sample does not prove complete coverage.
5. Explain coverage and freshness. Reviewed file-based connectors currently require preparation refresh and re-registration after source changes. Existing guards block stale reuse; they do not automatically fetch new material. Missing preparation or uncertainty stays visible. Enabling the assisted loop does not enable automatic answer approval or a scheduler.

## Explain the connection to the human

Keep it short: name the connector, say whether it is ready/needs setup/needs refresh/unavailable, state what is included, and identify any required next step. These are plain-language summaries; preserve actual machine `status` and `nextAction` in the execution record.

Examples (use actual scope and observed results):

- “Your Skills connector searches the configured local skills. New skills need valid name and description metadata to be discoverable.”
- “Your Brain connector is ready for the reviewed records we selected. Other records still need onboarding; updates require a refresh.”
- “Your Repo connector uses the reviewed local snapshot at this revision. It does not follow new commits automatically yet.”
- “A direct database connector is not available yet. We can plan a reviewed export if that suits your needs.”

If data changed, say that the connection needs refreshing rather than that no answer exists. If retrieval needs agent assistance, report that separately from an automatic hit. Never say “your whole brain is connected,” “live synced,” or “always up to date” without evidence of that exact capability and coverage.

## Freshness and manual updates

A source is the underlying file, record, repo checkout, database or webpage. A registered connector may hold only a reviewed snapshot of it. Connected does not mean automatically synchronized.

- Registered local files changed: update/review the preparation and re-register. Known changed bindings block reuse.
- Export or local copy: obtain the newer export/copy first, then review, rebuild preparation and re-register. Unchanged copies cannot reveal changes upstream. Record who owns this manual refresh.
- No supporting source yet: the verified-answer workflow needs reviewed material. With authorization, record the user's supplied facts in their project, retain their provenance, then prepare/register them. Do not invent a source or approve unsupported answers.

For a current-state question, briefly say: “This uses the registered snapshot; newer information may exist.” Offer the concrete refresh step when needed, without a repeated onboarding ceremony. Snapshot mode supplies no checked date. Current mode requires a trusted whole-scope `checkedAt` within the requested age; this records the upstream check, not a guarantee that every fact is true or still current. File modification dates, cache approval times and event dates are not substitutes. Warnings never override a stale/refresh refusal. Automated fetching and syncing remain unimplemented.

## Connect local files: paths to searchable passages

Use this when records are missing or a pointer-only dataset returns `preparation-required`. A configured memory runtime is required (`memory --describe` describes it; `memory --principal AGENT` shows that agent's pointers). For a new single-agent connector, use the shortcut below; JSON remains available for shared or custom scopes. There is no positional `memory connect` subcommand.

From the installed skill directory:

```sh
python3 dispatch.py memory --connect my-records --file /absolute/path/to/record.md --principal YOUR_AGENT_NAME
```

Repeat `--file` for additional files. This is a local preview, not approval. Review the files and permission, then run the returned **`confirmCommand`** exactly as printed; it binds the reviewed file hashes and uses your configured runtime. No JSON editing is needed. Changed bytes return a new review preview instead of accepting old approval. Use `--replace` only for the same existing single-principal connector; shared connectors or a dataset name different from its pointer use the JSON workflow below.

Alternatively, save a request in your repo's private working area:

```json
{
  "action": "connect",
  "pointer": "example-reviewed",
  "principals": ["YOUR_AGENT_NAME"],
  "sources": [{"path": "/absolute/path/to/authorized-record.md"}]
}
```

Run `python3 /path/to/super-jev/dispatch.py memory --input /path/to/connect.json`.

The first call returns the preparation/review requirements without publishing a connector. Review the actual source files and permission to send their **entire text** to the configured Jev provider. Use existing authorization where it clearly applies; ask only for genuinely missing permission or scope. Copy the returned source hashes into the request, add `reviewed: true`, and submit it again. Optional factual `description` values help discovery; the harness supplies basic descriptions if omitted. No user chunk-by-chunk ceremony is needed. This confirms source preparation, not the correctness of future answers.

A successful `registered` response means preparation and registration finished. Search using the returned pointer and your principal, check returned evidence, and approve answers only through the existing review flow. Test an actual question before describing the connector as working end to end. Do not call the first review response “connected” or “no matching records.”

Refresh uses the same pointer, dataset and exact principal scope with `replace: true` and newly reviewed source hashes. It invalidates that pointer's prior answers/tickets. Do not use replacement to widen a single-user connector to another person or to add audience members silently. If the old connector is shared or its scope is uncertain, keep it intact and create a separately named connector for the authorized scope.

Initial support is explicit local nonempty UTF-8 text files on macOS/Linux, up to 50 files and 5 MiB per request. Folders, binary/PDF documents and remote URLs need prior authorized extraction or explicit file selection; there is no automatic crawl or live synchronization. The harness preserves originals. Connecting raw private material does not automatically sanitize it: if full text is not authorized for the provider, prepare a reviewed/redacted projection using the existing workflow instead.

### Checked connect

`python3 dispatch.py`'s connect flow trusts the description you write for
each source; `connect_checked.py` checks that trust first. Given the same
CONNECT.json, it runs the claim gate on every source's description against
its own file, and only calls through to connect if every source comes back
SUPPORTED at or above the confidence line (0.80 by default, `--line` to
move it). A NOT_SUPPORTED or under-the-line description, or a file over the
gate's 32k-token ceiling, refuses the whole connect; nothing is registered
until every source passes. `--check-only` runs just the gate. It writes
`<name>.verdicts.json` next to the request with each source's verdict and
sha256. It does not watch for file changes after a connect and does not
chunk or truncate oversized files — split or drop them and rerun.

### Bulk prepare

`python3 prepare_bulk.py --root DIR [--root DIR2 ...] --pointer NAME
--principal YOUR_AGENT_NAME [--exclude SUBPATH ...] [--no-recurse] [--limit
50] [--max-files 250] [--refresh]` is the checked-connect workflow run over a
whole folder, or a whole agent brain spanning several folders, instead of a
hand-picked file list. Repeat `--root` to inventory the union of multiple
roots, in the order given; `--exclude` (repeatable) skips any file whose path
relative to its root starts with that subpath, and `--no-recurse` limits each
root to its direct children. It skips hidden dirs, backups and vault-style
subdirectories, and holds back any file that looks like it carries
card/password text or sits over the gate's size ceiling, writing a
`prepare-cache/<pointer>-held.txt` with each hold's reason and, for the
secret-pattern case, the pattern type, line number and a digit-masked line so
a human can review without opening the file. A cheap writer model drafts one
description and one sample question per remaining file; the same claim gate
used by `connect_checked.py` checks each description against its own file,
with one rewrite retry on a failure. Only the passing set is connected,
through the normal preview-then-confirm path; a set larger than `--limit`
(default 50, the connector's hard per-request cap) is split into parts named
`<pointer>`, `<pointer>-2`, `<pointer>-3`, ... in stable sorted-path order,
each connected separately, with the cache and report staying keyed by the
base pointer. After connecting, it re-runs each connected file's own sample
question through `navigate` and reports whether the file ranks first, as a
soft findability check, not a pass/fail gate. Results and a cache keyed by
file hash land under `prepare-cache/` next to the script, so an unchanged
file is skipped on the next run; `--refresh` additionally drops any cached
file no longer present on disk from the cache and the connect set, noting it
in the report, and a reconnect that hits an existing pointer with
`replace:true` prints a one-line warning that it rotates that pointer's
approved answers. `--max-files` (default 250) refuses an oversized run before
any drafting starts. `--no-connect` stops after drafting and gating, for a
dry run.

By default the writer is `claude -p --model haiku` (change the model with
`--writer-model`). Systems without Claude Code can supply another local writer:

```sh
python3 prepare_bulk.py --root DIR --pointer NAME --principal YOUR_AGENT_NAME \
  --writer-command 'my-description-writer --model small'
```

`--writer-command` is parsed into an argument list and run without a shell.
The program receives the existing UTF-8 writer prompt on standard input: its
final `FILES:` section is a JSON array of the file records. It must write only
a JSON array to standard output. Each array member must be an object with
`path`, `description`, and `question`; `path` must match an input file path.
The writer should send diagnostics only to standard error. A start failure,
nonzero exit, or invalid JSON stops the bulk run with an error; writer output
and diagnostics are never echoed, so command output containing sensitive text
is not logged by this tool.

For example, a small adapter can read the prompt, parse the text after its
`FILES:` marker, and emit its result array. It does not need Claude Code or a
particular model SDK:

```sh
my-description-writer --model small < prompt.txt > result.json
```

The writer also drafts four labels per file alongside the description: `kind`
(dashboard, playbook, ledger, record, index, pointer, research, note),
`status` (active, closed, paper, done, unknown — using only what the file
itself states; "NOT FILED"/pending/open counts as active), `as_of` (the date
the file claims for that status, or unknown), and `subject` (1-4 words). A
label outside its enum is coerced to `unknown` locally before anything is
gated, so a bad writer response never crashes the run. Labels are gated in a
second pass, separate from the description: the description alone must pass
first (that decides whether the file connects at all, with the one rewrite
retry described above); only then is the label sentence ("This file is a
`<kind>` about `<subject>`. Its status is `<status>`[ as of `<as_of>`].")
gated on its own against the same file. A label problem — under the
confidence line, `NOT_SUPPORTED`, `CONTRADICTED`, or `ERROR` — never drops
the file; it resets all four labels to `unknown`, records `labels_verdict`
and `labels_confidence` in the cache entry, and the file still connects on
its plain description. Only a file whose labels also gated clean carries them
in brackets in the connect description, so ranking sees them; this costs two
judge calls per file whose description passes (one when it doesn't).
`python3 prepare_bulk.py --list --pointer NAME [--status active]
[--kind dashboard] [--within-days 30] [--subject NAME]` reads the labels back
out of `prepare-cache/<pointer>.json` (and any `<pointer>-N.json` part
caches), filters and sorts by `as_of` descending, and never calls the writer,
the gate, or memory; `--within-days` excludes files whose `as_of` is unknown
and reports how many were excluded. A label is only as true as the file it
was drafted and gated from; `as_of` shows staleness, not currency — live
truth for anything time-sensitive still needs a gated roll-up read fresh, not
a cached label.

### Ask loop

`python3 ask.py --principal YOUR_AGENT "question"` is the front door over
everything above: harness `cached` action first (no local map, zero provider
calls), then every connected pointer in parallel. State (`lookups.jsonl`,
`manual/`) lives under `$SUPERJEV_STATE_DIR` or
`~/.local/state/super-jev/<principal>/`, never inside a repo checkout.
`--add "question" "answer" [--source /path]` records a fact with no file as
its own one-file `<principal>-manual-<hash>` pointer, so it never replaces or
invalidates any other pointer's approved answers; a later cache hit on that
pointer re-hashes `--source` and warns if the original file changed.

## Navigation structure

Record how the connected view is organized at onboarding. Supported structures are `flat-files` (default) and `folder-tree`. These describe only the explicit reviewed source set, not the entire disk. The connector exposes stable node IDs, a root, and available navigation actions. A database or arbitrary graph is not silently treated as a tree; direct database ingestion and automatic index-link parsing remain unsupported.

For an existing folder layout, preview the same explicit files with:

```sh
python3 dispatch.py memory --connect project-docs --structure folder-tree --file /project/docs/setup.md --file /project/docs/operations/release.md --principal YOUR_AGENT_NAME
```

Review the proposed navigation metadata as well as source scope and permission, then run the returned `confirmCommand`. It preserves the reviewed structure and metadata hash. No directory crawling occurs; only supplied files enter the view. A folder-tree derives groups from their parent directories. Source descriptions should explain what each file actually contains; structure alone does not establish relevance.

For agent-authored groupings such as an index of brain records, the JSON `connect` workflow can supply each source's `navigationPath`, an array of group labels, with `structure:"folder-tree"`. For example a source may have `"navigationPath":["Projects","Super Jev"]`. This is an explicit reviewed projection, not permission to follow links or read additional files. Use the preview's navigation metadata hash when confirming. Originals stay unchanged. Refresh an existing structure with the normal reviewed `replace:true` flow.

To locate candidate files, save this request in the caller repo's private working area and run `memory --input`:

```json
{"action":"navigate","pointer":"project-docs","principal":"YOUR_AGENT_NAME","question":"Where are deployment rollback instructions?","limits":{"beamWidth":3,"maxRounds":6,"maxResults":3}}
```

The harness presents the root's options to Jev, retains several promising routes, opens their registered children, and repeats within the limits. It tracks visited branches and limits exploration. Returned source locations remain bound to the registered reviewed snapshot and authorized pointer; stale sources must be refreshed. Existing pre-navigation pointers use a flat view of their already registered descriptions.

`candidates` means inspect these files. `no-candidates` means no file was selected in this bounded run. `budget-exhausted` means the exploration limit was reached. None of these statuses approves an answer, proves that the answer is absent, or writes the answer cache. Keep `search` for passage retrieval and the existing explicit approval flow for verified reuse. Navigation is advisory even if a ranking score is high.

Design reference: [TypeSafe hierarchical classification](https://docs.typesafe.ai/cookbooks/hierarchical_classification) keeps multiple paths instead of making a single irreversible branch choice. This implementation bounds traversal over registered local sources; it is not a general crawler or a claim of universal file-finding accuracy.

For everyday file finding, start with `flat-files`. Folder-tree is experimental: pruning a folder can hide relevant files, especially when a question needs files in separate folders. After navigation, read candidate files before answering; suggestions do not establish support or absence. For an existing connector whose source bytes changed, refresh with the same pointer and scope plus `replace:true` (CLI `--replace`), review current hashes, and confirm before retrying.
