# Experimental source retrieval (opt-in)

Status: **experimental**. The lead is still measuring whether the neighbor
stage helps. Nothing here carries an accuracy promise, and none of it runs
unless a caller opts in. It changes no CLI, no fleet default, and no
existing `runFetch` behaviour.

## What it is

`src/enhance/retrieval.ts` is an opt-in library that pulls passages out of a
small set of source documents through a bounded async stage chain. It reuses
three existing pieces and adds no new framework or dependency:

- `runFetch` — the one-question relevance sweep, used for the descriptions
  stage and for judging chunk bundles.
- `prefilterCatalog` — the local BM25-lite pass, used to keep the top-4
  chunks per gated source without a provider call.
- `applyDirectFitGate` — the experimental direct-fit gate (PR #82 branch):
  a stage's output is only acted on when the rubric called the top pick a
  direct fit (`high`), above the confidence floor and margin.

## The chain

1. **descriptions** (provider) — `runFetch` over the source descriptions
   with the filter-8 local prefilter; gated by `applyDirectFitGate`.
   Keeps the ranked top-3 docs by default (`docK`).
2. **local BM25** (local) — each surviving source is cut by `chunkSource`
   into deterministic heading-aware ~180-word chunks at line boundaries;
   `prefilterCatalog` keeps the local top-4 chunks per source (`chunkK`).
3. **narrow-bundles** (provider) — one bundle per chunk, judged and gated.
4. **wide-bundles** (provider, **only on refusal**) — when the narrow
   stage's gate refuses, each source's kept chunks are re-judged as one
   wide bundle per source.
5. **neighbors** (optional, **OFF by default**) — adds the immediately
   preceding/following chunk in the same source, deduped, never dropping
   the base selection.
6. **prepare** (local) — binds every selected passage to its source's
   content SHA and a preparation policy.

Stages are bounded by `maxStages` (default 3: descriptions, narrow-bundles,
wide-bundles). The bound counts provider stages — a stage's provider calls
may still batch; it is not an HTTP-call cap. Judge calls are never retried
(`maxRetries: 0`); `timeoutMs` is configurable and passed through.

## Preparation, not redaction

A passage is usable only when an explicit preparation callback or
contentSHA-keyed map binds it to the exact source bytes (`contentSHA`) and a
`reviewed` preparation policy. Unknown, stale (bytes changed since review),
or unreviewed selections return `preparation_required` — never the raw text
as a fallback, and never an automatic redaction or approval. A
`preparation_required` result withholds the whole selection and names each
pending passage by source, chunk, and offsets only.

## Fail-closed

Incomplete judge coverage, transport errors, and unknown judge ids never
produce passages: the result is `refused`. A `no-match` is only reported
when the judge actually answered and found no direct fit. Caller input
problems (duplicate source ids, foreign ids, invalid chunk offsets) throw
`RetrievalError`.

## Ready results

A `ready` result carries the prepared passages grouped per source in
source-rank order (each with its description), a compact per-stage trace
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
