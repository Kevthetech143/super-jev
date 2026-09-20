# Fresh health-brain questions with frozen passage-bundle retrieval

Status: completed validation on new questions over the existing 20-source collection; not deployed. The same source snapshot, descriptions, chunking, retrieval rules, relevance rubric and confidence thresholds were retained from the preceding passage experiment. This is not validation over the entire health brain or a new corpus.

## Design and independence

An independent author inspected the original records and wrote 20 new source-finding questions: 16 source-present and four absent from the selected collection. All 11 source documents not targeted by the earlier positive questions are represented. Repeated-source questions target different provenance or status relationships. The author could inspect old questions to exclude duplicates, but did not see the catalog descriptions, privacy renderings, model outputs or rankings. Questions and execution code were hashed before the first provider call.

Each question ran through a fresh description stage, then the conditional passage workflow:

1. BM25 filter eight verified descriptions; Super Jev ranks candidates; retain up to three source IDs.
2. Search the original local chunks inside those sources, retrieve four, group their reviewed passages by document, and judge the resulting evidence bundles.
3. Only on refusal, search all 431 chunks, retrieve four, regroup and judge. Stop with source pointers or ordinary-search fallback.

The first-stage states were persisted during a necessary manual privacy-review pause. The continuation used actual conditional decisions, rather than selecting a favorable result from precomputed alternative runs. A maximum of three live judge calls was allowed per case, with no provider retries, threshold changes, query expansion or question-specific retrieval tuning.

The union of narrow/wide candidates contained 94 source chunks. Fifteen retained identical previously reviewed privacy renderings; a question-blind worker rendered 79 new ones. The lead read all new renderings and checked IDs, privacy patterns and source offsets. Before passage judging, the lead restored an omitted source detail, corrected a chronology paraphrase, and removed clinical substance from three renderings. This human privacy work is still a manual component and can affect retrieval discriminability.

A separate reviewer labeled all 119 question/chunk pairs before passage judging. The lead conservatively changed one direct label to partial after checking the original: the passage requested a later portal review but did not document its completion. Final labels: 34 direct, 25 partial, 60 none. These are source-finding/evidence-pointer judgments, not a test of complete clinical answers or medical accuracy. Two questions have identifying details distributed across different source passages; the retrieved evidence supports the distinctive source relationship without reproducing every qualifier. Those limits were recorded before model results.

## Results

| Outcome | Count |
|---|---:|
| Source-present requests | 16 |
| Correct source accepted with supporting original passage | 15/16 (93.75%) |
| Wrong source accepted | 0 |
| Accepted source without direct supporting passage | 0 |
| Source-present request left for fallback | 1 |
| Missing-source requests refused | 4/4 |
| Correct among accepted results | 15/15 |

Fourteen cases stopped after two judge calls. Six used three calls: one additional successful source retrieval, the unresolved positive, and all four missing-source questions. All 20 cases are included; no failures or inconvenient questions were discarded.

The initial description pass ranked the correct source first for 13/16 positives, retained a gold source in the filtered eight and final shortlist for all 16, and accepted seven correct sources with no wrong accepts. Adding retrieved evidence and conditional widening raised accepted supported source retrieval to 15/16 on these same fresh questions. This is a within-test improvement in useful completion, not a claim that the earlier 8/9 and this 15/16 form a statistically comparable accuracy trend.

## Ordinary-search comparison

Original-text keyword search over the whole collection placed the gold source first on 15/16 positives, and a directly supporting passage first on 14/16. Its top four retained a gold source on all 16; direct passage coverage was 15/16 under the conservative labels. Inside the initial shortlist, keyword search placed a gold source first on 14/16 and direct evidence first on 12/16, with direct evidence somewhere in the top four for all 16.

Keyword ranking on the exact same deidentified document bundles given to Jev placed the gold source first on 16/16 positives in both candidate pools. This control is conditioned on the already selected pools and verified descriptions; it is not a complete no-Jev replacement evaluation. It shows no demonstrated Jev ranking advantage on those prepared bundles. Plain keyword ranking has no tested abstention gate here, so its missing-source precision cannot be equated with Jev's measured four refusals. Total lead/worker context, privacy-preparation cost and end-to-end token savings were not measured.

## Remaining case

The unresolved question asked for a log showing a transition from a family report to a later completed portal review. Jev ranked the correct log first at both evidence stages but deferred. The selected log fragment ended while the review was still pending; the immediately following source section recorded completion but was not retrieved. A timeline candidate did contain the transition, but the frozen gold targeted the log. This source ambiguity and split-evidence limitation were documented before judging; gold was not rewritten afterward.

This suggests testing neighboring-section retrieval when a requested sequence is split across adjacent sections. That behavior was not added or tested in this frozen run, and the case remains a fallback rather than a claimed success.

## Verification

46 actual HTTP requests: 20 description calls and 26 conditional evidence calls. All completed successfully with `jev-1.13.0`; no incomplete rows, transport failures, or budget refusals. Provider usage was 96,249 input and 10,746 output tokens. All original-source hashes remained unchanged, returned offsets resolved to their original spans, and phase-frozen code, questions, evidence and labels matched their recorded hashes at completion.

These are one execution per fresh question, a small same-corpus sample, and manually deidentified passages. Zero observed wrong accepts is not a guarantee of 100% reliability. No runtime defaults, thresholds, fleet cards, original records, merges or deployments changed. Private questions, source data, scripts, labels and raw logs remain in the ignored repo-owned experiment directory.
