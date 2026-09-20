# Baseline data lifecycle contract

This is a trusted-local, reviewed-dataset baseline. Increasing the number of files does not add authentication, live-source connectors, source editing, or clinical/version reasoning. `cli.py --describe` lists the supported actions; the configured panel reports registered pointers and settings.

## Before onboarding a dataset

The administrator/calling agent must establish:

1. **Scope and authority:** whose data this is, which sources are included/excluded, who may read it, and which source settles conflicts. Use stable source IDs. A dataset called “health” is not proof it includes every patient or every record.
2. **Provenance:** retain the original, a reviewed searchable view, hashes and source locations. Extraction/indexes/cache are derived data; they must be rebuildable. Do not change hashes merely to silence a stale-source result.
3. **Time meaning:** distinguish the event/effective date from the date the whole upstream scope was last checked. Registering a pointer, reading a local file, or approving an answer does not establish upstream freshness.
4. **Refresh procedure:** specify who/what checks the upstream source, how new/deleted/corrected records are enumerated, and how reviewed preparation is rebuilt. Only after that check may a trusted updater record `checkedAt` and re-register the pointer. This release does not supply that updater.
5. **Reuse policy:** snapshot questions concern the saved record. Current questions explicitly request `current` mode and a domain-appropriate maximum source-check age. Put person, date, version and other answer-changing qualifiers in the question; `context` additionally isolates cache keys but is not a retrieval prompt.
6. **Recovery and retention:** retain a recoverable prior source/manifest before replacement. Decide how long local cache data/backups should persist. Expiration blocks reuse; it is not secure erasure or an automatic cleanup service.

These are onboarding requirements, not six new automatically enforced metadata fields.

## Supported lifecycle

| Action/event | Runtime behavior | Operator responsibility |
|---|---|---|
| Register or replace a reviewed pointer | New generation; prior pointer answers and tickets removed | Verify scope, privacy, preparation and intended principal labels |
| Source/manifest changes, disappears or fails validation | Preparation required; stale answer not reused | Diagnose corruption versus intended update, then rebuild/review |
| Current request lacks a recent whole-scope check | Refresh required before retrieval/cache reuse | Fetch/check upstream data; do not renew timestamps blindly |
| Source freshness expires during retrieval/review | Refuse current result/approval | Refresh, then submit a new request |
| New file appears outside registry | Not discovered automatically | Refresh enumeration/coverage; a new file alone does not invalidate the old registered scope |
| Context or freshness requirement changes | Separate cache scope | Include context in the actual question too |
| Remove pointer | Removes that pointer, its answers and tickets | Originals, registry and backups remain; removal is not source deletion |
| Corrupt SQLite | Structured CLI error; no answer | Stop writes, investigate and restore/rebuild; no silent destructive reset |

Invalidation currently applies to the entire pointer generation. That is deliberately conservative. Per-source invalidation is a later optimization, not a requirement for starting safely.

## Evidence that changes meaning with time

| Example | What the caller must establish before approving an answer |
|---|---|
| Old test versus upcoming test | Patient, test identity, date, and completed/scheduled/cancelled status; an appointment is not a test result |
| Updated medication list | Effective date and authoritative reconciliation; a newer note mentioning a drug does not automatically supersede the active list |
| Software fixes | Product, release/version, environment and whether the fix was proposed, merged, released or actually deployed |
| Internet records | Source authority, publication/update date and last upstream check; recently fetched content may still describe an old state |
| Conflicting sources | Identify the conflict and authoritative resolution; do not approve whichever source simply has the newest timestamp |

The currentness gate bounds **time since an upstream check**. It cannot determine these facts or turn an old document into a current answer. When support is incomplete, do not approve/save. An approved historical answer must retain its historical scope.

## Source writes: explicit future interface

Agents can manage pointer registration/removal now. This release does **not** edit/delete original source files through the harness. A future source-write interface should require:

- Explicit write authority, separate from retrieval scope. Hosted use requires real authentication/authorization; local principal strings are labels only.
- Stable target ID plus expected source hash/generation; conflicting concurrent changes fail rather than overwrite.
- A recoverable replacement or tombstone, validated before publication.
- Reviewed extraction/manifest refresh, followed by generation rotation and answer invalidation.
- A receipt identifying what changed, the actor, old/new versions and recovery location. Avoid putting private content into generic audit logs.

No arbitrary file-path mutation endpoint, automatic conflict resolution, distributed database, or background scheduler is needed for this baseline. Enforce the above contract before claiming source CRUD or shared multi-user hosting.

## Assisted recovery and retention

An operator may enable bounded assistance from the same reviewed dataset after a partial/no-match/refused attempt. Source discovery and line references do not authorize unregistered paths. The harness returns prepared evidence IDs for explicit review; absent or conflicting support stays unresolved. Advisory hints recommend preparation/refresh or investigation, never grant permission.

The database retains original attempt request/context/status/trace/reason metadata across pointer replacement/removal. Full raw responses remain caller-owned private artifacts. Cache entries record retrieval versus agent-assisted resolution and originating attempt ID, but invalidation clears those entries: this is not a permanent answer-history archive. Old-generation audit rows remain stored for trusted local inspection; the ordinary attempt API cannot resurrect their visibility by reusing a pointer name. Plan retention for those private audit rows and backups as data grows; no automatic audit-pruning service is included.
