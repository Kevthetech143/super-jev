# Pointer-memory experiment results — 2026-09-20

## Conclusion

A persistent pointer plus question can hide cache/freshness/retrieval mechanics from
an agent, while explicit evidence review controls answer reuse. This supports a
public local experiment. It does not establish production fleet authentication,
autonomous learning, arbitrary-data coverage, or unattended worker reliability.

## Real Jev runs

Questions and expected sources were recorded before each run. They were development
questions derived from known reviewed records, not independently authored held-out
questions. Three questions had been used in the earlier cache pilot. No ordinary
search rescue or model fallback was counted as success.

| Run | Distinct questions | Observed result |
|---|---:|---|
| Initial pointer prototype through installed retrieval | 8 answerable + 2 absent, repeated 3 times | 24/24 answerable runs contained the expected supporting quote; 6/6 absent runs returned no-match |
| Public bundled engine bridge through `memory` CLI after structural fixes | Same 8 + 2, once | 8/8 contained the expected quote; 2/2 absent returned no-match |
| Lead-reviewed exact reuse | 8 approved answers | 160 concurrent service lookups, no additional Jev calls |
| Public CLI exact reuse | Same 8 approved answers, 3 repeats each | 24 cache hits with provider credential removed; median 0.229 s including dispatcher/Python startup |

Fresh public CLI requests had median 1.777 s (23 Jev calls across ten requests).
The earlier 30-run batch made 70 Jev calls. Manual review and initial preparation
are excluded from these latency comparisons. Approval was performed by the lead
LLM; a cheap autonomous reviewing model was not evaluated. Exact quotes were
checked against reviewed manifest passages, and original bindings stayed unchanged.
The early 160-hit measurement was before final storage race fixes; the final CLI
and regression runs validate the revised path. These are different measurements,
not independent corroborating datasets.

## Mechanical and public-interface validation

- 31 synthetic checks passed, including 1,000 cache hits with 16 concurrent callers
  and one initial injected retrieval, not 1,000 live Jev questions.
- 16 unit/regression tests passed: public JSON interface, malformed provider output,
  TTLs, stale/unreviewed data, revocation race, manifest replacement race, concurrent
  schema initialization, non-mutating unsupported-schema refusal, and cleanup.
- 10 dispatcher tests passed; skill frontmatter validation and whitespace checks passed.
- A separate agent followed the public synthetic quickstart without a provider key:
  options, registration and panel worked; search reported the missing key clearly;
  removal then returned unknown-pointer. No private fleet tooling was needed.

The independent review found two races in the initial prototype. Cache reads now
recheck pointer generation/allowed principal in the same database transaction.
Approval hashes and parses identical manifest bytes, then revalidates bindings
before committing. Targeted regression tests reproduce the earlier failure
conditions. This does not provide a transactional multi-file filesystem snapshot.

## Storage-size probe

Synthetic 4 KiB source files, ten unchanged hits at each size, current full-snapshot
validation included, no live provider calls after initial injected retrieval:

| Files | Median hit | Maximum hit |
|---:|---:|---:|
| 1 | 2.07 ms | 2.95 ms |
| 100 | 21.97 ms | 25.47 ms |
| 1,000 | 298.38 ms | 454.28 ms |

Changing the last source blocked reuse at every size. Full-dataset hashing is safe
but has visible size-dependent overhead and broad invalidation. This test does
not establish large-dataset latency guarantees. Keep this simple implementation
until measured use justifies an incremental freshness mechanism.

## Discovery miss retained

The first indirect memory-capability skill query returned `clarify` with the wrong
shortlist; Super Jev was absent. Its initial broad description did not explicitly
name saved answers or dataset pointers. After adding that accurate metadata, the
unchanged query ranked Super Jev first, but the gate still returned `clarify`.
Both results were Jev-backed (two calls, zero retries each). This is a repaired
first-place ranking on one development query, not a clean unambiguous routing win.

## Boundaries

- Only ten distinct real questions on one already-reviewed corpus; no health-brain
  onboarding, new arbitrary datasets, hours-long endurance or semantic reuse test.
- The pointer remembers approved scope; it cannot repair missing preparation by itself.
- Local scope names are not authentication. Do not expose this to untrusted users.
- A reviewer can still approve a wrong interpretation. Citation containment is not
  a correctness oracle; no automatic approval on `ready` is implemented.
- No scheduler, general file extractor, global cold-request deduplication, secure
  erasure or automatic expired-record collector is claimed.
- No deployment change or measured end-to-end token-saving claim.

Private live request/response traces and approvals remain local. Only this redacted
summary and reproducible synthetic tooling are committed.
