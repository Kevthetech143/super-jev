# Lead's personal health-brain retrieval check

Status: completed curated experiment, not deployed or representative held-out validation. Run `personal-20260919T230654Z`: 40 jobs, 40 actual HTTP calls, zero failures; provider model `jev-1.13.0`.

## What was personally checked

The lead read the actual health-brain INDEX and verified the purpose, status, provenance limits and relevant sections of 20 sources: shared workflow and follow-up records plus one person's history, research, preparation and program files. None were the 20 records selected for the earlier controlled experiment. Source hashes were frozen and rechecked unchanged after the run. Every returned pointer resolved to a reviewed source.

The lead authored 20 requests and the expected sources, then faithful navigation descriptions, before live evaluation. This is deliberately a personal test, not a blinded comparison: the same lead wrote descriptions and questions. Cases include stale preparation versus actual outcomes, correction history, missing artifacts and two requests requiring two sources. All requests were preserved in the score, including abstentions; no post-result description changes or additional tuning calls.

## Results

| Metric | All 20 candidates | Diagnostic shortlist 8 |
|---|---:|---:|
| Correct first source, single-source questions | 15/15 | 15/15 |
| Both required sources within top 3, two-source questions | 2/2 | 2/2 |
| Full requested source coverage within top 3 | 17/17 | 17/17 |
| Positive requests accepted by direct-fit gate | 16/17 | 16/17 |
| Positive requests deferred despite correct first source | 1/17 | 1/17 |
| Missing-source requests deferred | 3/3 | 3/3 |
| Accepted wrong source | 0 | 0 |
| Actual HTTP calls | 20 | 20 |
| Provider input tokens | 167,680 | 70,199 |
| Provider output tokens | 19,260 | 7,740 |
| Median query time | 268.5 ms | 273.5 ms |

The legacy and opt-in direct-fit gates agreed on this run. The latter was applied to the same saved live outputs; no extra requests were used for gate scoring. The one positive fallback was a recovered specialist-workup source that ranked first but did not clear the confidence checks. Missing-source outcomes refer only to the selected corpus, not the full archive or absence of care.

Filtering lost no required source on this set and reduced provider input tokens by about 58%. It did not reduce median latency in this run. Production prefilter 40 does nothing on 20 records; shortlist 8 is an explicit diagnostic. No source-order repeats or latency-distribution claims. Ordinary search was not re-benchmarked here; the earlier independently blinded baseline remains a separate experiment.

## What the review exposed

An index entry summarized a research note as having found no time-of-day evidence, while the current note contains a later, qualified correction. The lead described the current source and its limits rather than copying the stale index. Another document has a summary-like title but an explicit historical-preparation banner; it must not be represented as a completed encounter outcome.

The evidence supports faithful, current descriptions as a useful retrieval representation for this bounded navigation task. It does not prove that descriptions can be generated and maintained reliably at full-brain scale or that total agent tokens are lower: source verification/index construction and ordinary-agent model tokens remain outside the provider-only measurement.

The next design issue is source-backed index maintenance: keep IDs and source pointers stable, retain purpose/status/provenance distinctions, and invalidate descriptions when their source changes. These results do not identify database capacity, caching, CDN or load balancing as the limiting factor. They do not validate medical synthesis, arbitrary files, adversarial records or fleet-wide deployment.

Private corpus, source maps, questions and raw logs remain outside Git. Local evidence: `private-lead-health-test/runs/personal-20260919T230654Z/`. Runtime defaults and original records were not modified.
