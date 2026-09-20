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

## Set up an existing source workflow

1. Identify the connector, exact source scope and authorized audience. Reuse known context; ask only for missing information needed to choose the right data/person. A connector does not grant new access.
2. Inspect existing setup. For Skills, read sibling `skill-search/SKILL.md` and its roots configuration. For Brain/Documents/Repo, list reviewed datasets through `find --list-datasets`; if local memory is configured, inspect its panel and registered sources. Follow installation-specific `LOCAL-MEMORY.md` and `memory.sh` when present.
3. Reuse an appropriate existing dataset/pointer. Otherwise follow sibling `fleet-retrieval-experiment/SKILL.md` to create reviewed descriptions, prepared passages and source/hash bindings; for memory, follow [verified reuse](verified-reuse.md) and register that reviewed dataset with authorized principal labels. A path or URL alone is not onboarding. Do not treat repo membership as privacy approval.
4. Run a relevant sample request. Verify returned supporting passages and any approved exact-repeat answer. Report the observed outcome; one successful sample does not prove complete coverage.
5. Explain coverage and freshness. Reviewed file-based connectors currently require preparation refresh and re-registration after source changes. Existing guards block stale reuse; they do not automatically fetch new material. Missing preparation or uncertainty stays visible. Enabling the assisted loop does not enable automatic answer approval or a scheduler.

## Explain the connection to the human

Keep it short: name the connector, say whether it is ready/needs setup/needs refresh/unavailable, state what is included, and identify any required next step. These are plain-language summaries; preserve actual machine `status` and `nextAction` in the execution record.

Examples (use actual scope/results, not these invented counts):

- “Your Skills connector searches the configured local skills. New skills need valid name and description metadata to be discoverable.”
- “Your Brain connector is ready for the reviewed records we selected. Other records still need onboarding; updates require a refresh.”
- “Your Repo connector uses the reviewed local snapshot at this revision. It does not follow new commits automatically yet.”
- “A direct database connector is not available yet. We can plan a reviewed export if that suits your needs.”

If data changed, say that the connection needs refreshing rather than that no answer exists. If retrieval needs agent assistance, report that separately from an automatic hit. Never say “your whole brain is connected,” “live synced,” or “always up to date” without evidence of that exact capability and coverage.
