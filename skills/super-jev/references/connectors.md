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

No database is needed for this baseline. The index is an authoring aid, not an automatically parsed import contract: the agent still builds and reviews the existing manifest, descriptions and passage preparations, then registers the dataset/pointer. Follow the same sample-query and freshness checks below. Do not claim a collection is connected merely because files or an index were created.

Friendly setup hint: “Starting fresh? I can create a small, clearly described collection that works with Super Jev's existing preparation workflow. If you already have data, we can keep its structure and prepare a searchable view instead.” Give this hint when the source is absent or the user asks how to start, not on every successful lookup.

## Set up an existing source workflow

1. Identify the connector, exact source scope and authorized audience. Reuse known context; ask only for missing information needed to choose the right data/person. A connector does not grant new access.
2. Inspect existing setup. For Skills, read sibling `skill-search/SKILL.md` and its roots configuration. For Brain/Documents/Repo, list reviewed datasets through `find --list-datasets`; if local memory is configured, inspect its panel and registered sources. Follow installation-specific `LOCAL-MEMORY.md` and `memory.sh` when present.
3. Reuse an appropriate existing dataset/pointer. Otherwise follow sibling `fleet-retrieval-experiment/SKILL.md` to create reviewed descriptions, prepared passages and source/hash bindings; for memory, follow [verified reuse](verified-reuse.md) and register that reviewed dataset with authorized principal labels. A path or URL alone is not onboarding. Do not treat repo membership as privacy approval.
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
