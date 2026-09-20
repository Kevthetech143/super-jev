# Experimental source retrieval (opt-in)

Status: **experimental**. Nothing here carries an accuracy promise, and none
of it runs unless a caller opts in by calling `retrieveSources`. It changes
no CLI, no fleet default, and no existing `runFetch` behaviour.

## What it is

`src/enhance/retrieval.ts` is an opt-in library that pulls passages out of a
small set of source documents through a bounded async stage chain. It reuses
three existing pieces and adds no new framework or dependency:

- `runFetch` — the one-question relevance sweep, used for the descriptions
  stage and for judging document bundles.
- `prefilterCatalog` — the local BM25-lite pass, used to keep the global
  top-4 chunks (`chunkK`) across a chunk pool without a provider call.
- `applyDirectFitGate` — the experimental direct-fit gate (PR #82 branch):
  a stage's output is only acted on when the rubric called the top pick a
  direct fit (`high`), above the confidence floor and margin.

## Preparation first

Preparation happens BEFORE any passage provider call. Every preparation
record binds caller-REVIEWED passage text and a safe heading to the source
id, the source content SHA, the exact chunk offsets, the expected policy,
and the artifact identity (`doc:Lstart-end`). The judge sees ONLY reviewed
text and safe headings — raw chunk text and raw headings never leave the
module. Any selected chunk without a valid reviewed record (missing, stale,
unreviewed, or under the wrong policy) makes the whole run
`preparation-required`: the passage call never happens, and the pending list
names each passage by id and offsets only, never text.

Descriptions are the one caller text the judge sees, so `descriptionsReviewed`
must be explicitly `true` before any provider call is made.

## Optional exact-duplicate grouping (off by default)

`groupDuplicateSources: true` adds ONE local step before the descriptions
stage: sources that are exact duplicates of each other collapse to a single
canonical source, so the same document is not judged twice under two ids.
Default is `false`, and with the option off the chain is unchanged.

Two sources group only when ALL of this holds:

- their original `text` is byte-identical, AND
- their `description` is byte-identical, AND
- both are FULLY prepared — every chunk of each has a usable reviewed record
  under the run's `expectedPolicy` — AND those reviewed preparations are
  equivalent (same offsets, content SHA, policy, status, reviewed text, safe
  heading).

The canonical is the lexicographically smallest id in the group, so the
choice is deterministic and does not depend on input order. The helper is
exported on its own as `groupExactDuplicateSources(sources, preparation,
expectedPolicy?, targetWords?)`.

What it deliberately will NOT do:

- an alias with missing, stale, unreviewed, wrong-policy, or merely
  different preparation never joins a group, so it can never inherit another
  source's approval — it stays in the chain on its own and still hits the
  preparation gate (and still makes the run `preparation-required`);
- a source whose text or description differs by a single byte is never
  collapsed, so a distinct, better-prepared source is never discarded;
- with no `preparation` at all, nothing groups;
- near-duplicates, normalised text, and partial overlap are out of scope —
  this is exact-match grouping only.

Grouping only decides which sources enter the chain. Original text, content
SHAs, and chunk offsets are untouched, the result's `sources` pointer list
still covers EVERY input source (aliases included), and the alias ids come
back additively in `result.duplicateGroups` (`canonicalId`, `aliasIds`,
`contentSHA`) plus a local `duplicate-grouping` trace entry. The field is
absent entirely when the option is off. Judging thresholds, the gate, the
stage order, and the stage budget are all unchanged.

This is assisted retrieval: the result is prepared evidence for a caller to
act on, never an autonomous answer.

## The chain

1. **chunk all sources once** (local, up front) — every source document is
   cut by `chunkSource` into deterministic heading-aware ~180-word chunks at
   line boundaries, exactly like the experiment's section chunker: a heading
   boundary flushes before the heading, a breadcrumb stack is carried
   (`A > B`, sparse across depths — a skipped level renders empty, e.g.
   `A >  > C` — and heading text is kept untrimmed), the open block flushes
   before adding a line when current-words + next-line-words exceeds the
   target (no blank-line exemption: an oversize line followed by a blank
   line flushes alone), blank lines stay inside the block and count toward
   its offsets, blank-only blocks are dropped, and every chunk records exact
   original line offsets plus the source content SHA.
2. **descriptions** (provider) — `runFetch` over the source descriptions
   with the filter-8 local prefilter; gated by `applyDirectFitGate`. A
   low-confidence/deferred gate does NOT abort the chain: the description
   ranking's top-3 docs (`docK`) continue to passage judging. A
   description-only run can never produce `ready`.
3. **local BM25** (local) — the GLOBAL top-4 chunks (`chunkK`) across ALL
   chunks in the description shortlist, scored in the experiment's exact
   format (`Document description: … / Section: … / Passage: …`), score
   descending with a stable chunk-identity tie-break.
4. **narrow-bundles** (provider) — the top chunks are preparation-checked,
   then judged as DOCUMENT-GROUPED bundles of prepared passages (never
   isolated paragraphs), in the experiment's exact bundle format
   (`Description: … / Automatically retrieved source passages: / <safe
   heading>: <reviewed text>`). Bundles carry the original sourceId as
   their id and follow the BM25 selection order (first-seen document order);
   passages inside a bundle keep BM25 order.
5. **wide-bundles** (provider, **only on narrow refusal**) — the GLOBAL
   top-4 chunks across the WHOLE corpus, so a match outside the description
   shortlist can be recovered; judged as document bundles the same way.
6. **neighbors** (optional, **OFF by default**) — a genuine FOURTH judge
   stage, **only after wide refusal**: previous/next same-document chunks
   are added to the wide selection (deduped, base never dropped),
   preparation-checked, and judged as bundles. Neighbors are never appended
   after an acceptance without judging.

Stages are bounded by `maxStages` (default 3 without neighbors:
descriptions, narrow-bundles, wide-bundles; 4 when `includeNeighbors` is
on). Judge calls are never retried (`maxRetries: 0`); `timeoutMs` is
configurable and passed through.

## Fail-closed

Errors and completeness are checked BEFORE every accept path, not only on
no-match: transport errors, unanswered records, failed validation, and
bookkeeping gaps make the result `refused`. Unknown judge ids throw
`RetrievalError`. A `no-match` is only reported when the judge actually
answered and found no direct fit. Caller input problems (unaffirmed
descriptions, duplicate source ids, invalid chunk offsets) throw
`RetrievalError` before any provider call.

## Ready results

A `ready` result carries ONLY the top-ranked bundle's supporting prepared
passages — never every low-ranked candidate — plus a compact per-stage trace
(what ran, provider calls, kept/dropped ids), and original-source pointers
(`id`, `contentSHA`, `description`) for every source that entered the chain.

## Cache seam

`RetrievalCache` and `cacheKeyFor` define the interface and key format a
future PR can implement against. No cache is implemented here; passing one
changes nothing yet.

## Dependency

This work builds on the PR #82 branch
(`feat/experimental-direct-fit-gate-20260919`): it imports and reuses
`applyDirectFitGate` from `src/enhance/fetch.ts`. The new PR must keep that
branch as its base until PR #82 merges.
