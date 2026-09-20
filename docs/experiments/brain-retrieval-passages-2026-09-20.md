# Automatic passage selection and source evidence bundles

Status: completed development experiment, not deployed. This tests whether the harness can retrieve the relevant source passages that the prior experiment's lead selected manually. It does not establish a general breakthrough, independent held-out accuracy, or fleet-wide savings.

## Method

Reused the 20 verified real health-record sources and 12 independently authored but previously exercised questions: nine source-present and three missing from the selected corpus. All source hashes still match the earlier experiment. Markdown headings and approximately 180-word line boundaries produced 431 chunks with exact original file/line pointers. Every chunk was checked against its source bytes.

The existing harness BM25 search selected four chunks inside the prior description-stage shortlist, and separately four across the full 20-source corpus. Selection used original local source text, heading ancestry and the existing verified document description, without query-specific tuning or consulting gold labels. A privacy worker blind to questions and gold rendered the 61 distinct selected chunks into deidentified navigation paraphrases. The lead reviewed all outputs and requested privacy corrections. Four Spanish passages were translated/paraphrased in English. This manual privacy processing can change discriminability and is not an automated production component.

A separate reviewer, blind to Jev outputs and privacy paraphrases, labeled all 73 distinct question/chunk pairs against original source text: 29 direct, six partial, 38 none. The lead audited key cases against originals. These are source-finding questions: direct support means the passage identifies the requested source or relationship, not that it contains an entire clinical answer. A passage merely being in the gold document is insufficient. Source gold and passage support are measured separately; some index/log passages directly identify a source without themselves being that source.

No clinical diagnosis or treatment was assessed. Raw source text, identities, source paths, gold and labels were not sent to Jev. Provider inputs were reviewed deidentified passages and previously verified descriptions.

## Search baseline

| Ordinary keyword search | Direct passage first /9 | Direct passage in four /9 |
|---|---:|---:|
| Original text, inside shortlisted documents | 7 | 9 |
| Original text, full corpus | 8 | 9 |
| Same deidentified text subsequently given to Jev, narrow pool | 9 | 9 |
| Same deidentified text subsequently given to Jev, wide pool | 8 | 9 |

The same-evidence baseline was computed separately without model outputs or tuning; top-four coverage is inherited from the fixed pool. The narrow original-text 7/9 versus Jev 9/9 is **not** evidence of a Jev-only improvement: privacy paraphrasing/translation also changed the representation. On identical narrow evidence, ordinary keyword ranking matched Jev.

Automatic search retrieved the previously missed chronology section, plus a second supporting open-items passage in wider search. Neither was manually inserted into the candidate list.

## Isolated passage judging

Run `passage-auto-20260920a`: 96 complete actual HTTP calls. Two repeats per question, narrow/wide pools, and two rubrics: installed relevance versus an appended instruction requiring support in the passage itself. The latter is a local evaluator adapter, not an installed change. Measured both the existing gate and an experimental variant that groups same-document candidates before comparing confidence margins. Neither uses independent support labels.

| Judge / pool | Direct passage first /18 positive runs | Accepted direct passages /18 | Accepted insufficient passages |
|---|---:|---:|---:|
| Standard / narrow | 18 | 7 | 0 |
| Standard / wide | 15 | 6 | 2 |
| Passage-support instruction / narrow | 16 | 2 | 0 |
| Passage-support instruction / wide | 16 | 0 | 0 |

Acceptance in this table uses the source-grouped gate; the unchanged passage gate was also logged. All missing-source requests were refused (six repeated runs per arm). The standard wide arm shows why ranking a correct source is not enough: two accepted paragraphs did not independently establish the required source identity. The stricter instruction mostly increased refusal. Treating paragraphs as separate candidate documents did not give a useful automatic acceptance rate.

## Bundle automatically retrieved passages by source

Development follow-up `passage-bundle-20260920a`: 48 complete actual HTTP calls. The same selected passages and frozen privacy renderings were mechanically grouped by document and appended to its verified description. No additional evidence, gold, previous model scores or handpicked passage was introduced. The installed relevance rubric and opt-in PR #82 direct-fit gate were used unchanged. Bundling and document-oriented presentation are a combined intervention, not an isolated causal test of grouping alone.

| Evidence bundle pool | Correct source with supporting evidence first /18 | Accepted correct supported result /18 | Wrong or unsupported accepted | Missing refused /6 |
|---|---:|---:|---:|---:|
| Narrow | 18 | 14 | 0 | 6 |
| Wide | 18 | 16 | 0 | 6 |
| Narrow, widen only on refusal | 18 | 16 | 0 | 6 |

The adaptive result corresponds to eight of nine positive questions accepted in each repeat. The chronology question now succeeds when widening supplies two supporting passages. A different full-report question remains below the confidence floor in both repeats; no threshold was lowered to make it pass. Aggregate acceptance is still eight of nine, so this is an automatic-selection advance, not a new overall accuracy breakthrough. Ordinary same-evidence narrow ranking already matched the correct-first performance; Jev's acceptance/refusal behavior is a separate capability to evaluate.

## Fresh live chain check

Run `passage-smoke-20260920a`: three actual calls on the chronology question, without replaying prior model selections:

1. Fresh description search shortlisted the correct source but did not clear the gate.
2. Harness searched its real local sections, grouped selected reviewed passages by source, and Jev still deferred.
3. Harness widened the search across the corpus and supplied the resulting evidence bundles. Jev accepted the correct source with two supporting original-source pointers.

The lead verified both returned spans against source hashes and independent passage labels. The chain stored the unchanged question, checked documents/passages, stage decisions, and final source offsets. It has a finite three-stage limit and returns fallback on unresolved evidence. Any newly selected passage lacking a reviewed privacy rendering stops for privacy review rather than being sent raw. It returns an evidence candidate for lead verification, not an automatically certified answer.

## Verification and limits

All 147 measured HTTP requests completed without errors using `jev-1.13.0`: 96 isolated-passage comparisons, 48 bundle comparisons, three live chain calls. Main comparison provider tokens: isolated passages 218,928 input /22,192 output; bundles 56,666 input /4,632 output. These totals measure different experimental arms, not comparable full-agent cost or production savings.

The strongest supported pattern here is: descriptions -> local passage search -> source evidence bundles -> widen on refusal -> verify original source pointers. The automatic source selection and widening worked on the previously manual chronology case. Remaining limits include reused development questions, only 20 records, manual privacy paraphrasing/translation, a still-refused positive, and unmeasured total orchestration cost. The main comparisons reuse the old first-stage shortlist; only the one smoke case reruns the entire chain. No runtime, fleet defaults, source records, or confidence thresholds were changed, merged, or deployed. Private scripts, raw data, labels and run logs remain local.
