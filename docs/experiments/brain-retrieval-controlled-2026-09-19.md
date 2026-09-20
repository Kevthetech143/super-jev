# Controlled real-record investigation

Run lead-20260919T225755Z;24 jobs and 24 actual HTTP requests; no failures. Model jev-1.13.0. No source edits or runtime deployment.

Fixed 20 real records from one person. Source-index worker verified originals and froze20 unique faithful descriptions. Separate question writer read original selected sources without the catalog or prior results, produced 9 source-present and 3 selected-corpus-missing requests. Separate ordinary-search worker saw only requests/ID-path list and searched/read allowed originals blind to gold/catalog/Jev. This is curated exploratory navigation, not representative heldout or clinical/synthesis validation. Generic ambiguous requests were excluded by author and documented before live.

| Metric | Jev all20 | Jev shortlist8 | Ordinary source search |
|---|---:|---:|---:|
| Correct top1 |9/9|9/9|9/9|
| Correct within top3 |9/9|9/9|9/9|
| Gold retained |9/9|9/9|not applicable|
| Gate abstained on positive |1/9|2/9|0/9|
| Gate abstained on missing |3/3|2/3|3/3|
| Accepted wrong missing source |0|1|0|
| HTTP calls |12|12|0 Jev|
| Provider input tokens |90884|38239|UNKNOWN model tokens|
| Provider output tokens |12276|4932|UNKNOWN|
| Median query ms |274|242.5|not comparable|

Default prefilter 40 does nothing on 20 records. Shortlist 8 is an explicitly more aggressive diagnostic, not evidence for loss at production 40. No gold lost here, but candidate/ranking/confidence changed. One missing MRI query selected a preparation sheet scored LOW (1/3) with confidence 0.61, runner-up 0.44. Legacy applyNoneGate checks confidence and confidence margin, not relevance level, so it accepted a result the judge itself said would not satisfy the request. This is a concrete gate limitation, not proof of prompt misunderstanding. Candidate context effects and model variation not separated by this single run.

Ordinary search used six shell search/read calls, shared across 12 questions; searched 326,200 bytes of originals, did not necessarily ingest all bytes into model. Approximately 4 minutes worker-reported whole-task time vs Jev per-query transport/runtime is NOT fair latency comparison. Model tokens uninstrumented, so no overall savings verdict. Index construction/verification costs excluded from provider measurements.

Outcome: faithful source distinctions support accurate ranking on this bounded corpus; no blanket brain readiness. Source transcriptions plus analyst commentary must not be labeled pure originals. Keep explicit purpose/version/provenance limits. Fix opt-in direct-fit gate and retain original-source reads and ordinary search fallback. Do not expand to full brains/docs until independent broader workload demonstrates value. No additional live calls to tune this batch.

## Opt-in gate verification

An experimental `applyDirectFitGate` now requires the rubric's high/direct-fit score as well as all existing confidence checks. Offline replay of these saved live outputs rejected the observed false match without losing any previously accepted correct result: 8/9 accepted correct without filtering and 7/9 with shortlist 8; the remaining positive cases defer to fallback. All three missing-source cases abstained in both arms. This is a replay of the development evidence, not independent validation of the new gate. Existing defaults remain unchanged. Focused tests: 53 passed independently; worker-reported full suite: 582 passed.
