# Multipass source retrieval experiment

Status: completed diagnostic, not deployed. Main run `multipass-20260919T234238Z`: 78 actual HTTP requests, all complete. Targeted follow-up `focused-20260919T234518Z`: 3 more complete requests. Total 81; provider model `jev-1.13.0`.

## Design

Reused the controlled 20-record catalog and its independently authored 12 questions: nine source-present, three selected-corpus-missing. This is reused development evidence, not a new held-out test. A worker blind to questions/gold built two increasingly detailed, source-grounded evidence packs for every record. The lead reviewed privacy and source hashes before use. Packs contain deidentified navigation excerpts and close paraphrases about source identity, provenance, scope, version and status; no raw clinical findings or personal identifiers. Two short sources were explicitly marked as having no additional navigation evidence.

The original request/context was preserved at every pass. Only selected record IDs carry forward; prior model reasoning, confidence and gold were never sent back as evidence. The experiment measured these branches:

- A: short verified descriptions, local shortlist eight, return three pointers.
- R: rerank A's three pointers using the SAME short descriptions; no-new-evidence control.
- B: rerank A's three pointers with source excerpts (first zoom).
- C: rerank B's shortlist with further source detail (deeper zoom).
- W: expanded evidence for all 20 records. Measured for every case as a control; the adaptive policy uses it only when C refuses.
- V: inspect the final selected winner alone, with the same expanded evidence. Tests re-inspection/confirmation effects; it never upgrades a parent refusal.

The adaptive final decision requires agreement between the accepted parent and V, otherwise ordinary search is required. No further looping after the reviewed evidence is exhausted. This is experimental selection logic, not an installed retrieval service.

## Main results

| Branch | Correct top1 /9 | Correct sources accepted /9 | Wrong source accepted | Missing requests refused /3 |
|---|---:|---:|---:|---:|
| A: one pass | 9 | 7 | 0 | 3 |
| R: rerank without new evidence | 8 | 6 | 0 | 3 |
| B: add source passages | 9 | 8 | 0 | 3 |
| C: add deeper passages | 9 | 8 | 0 | 3 |
| W: all records with expanded passages | 9 | 8 | 0 | 3 |
| V: focused re-inspection | 9 | 8 | 0 | 3 |

The full adaptive combination also accepted eight correct sources, refused all three missing-source requests, and left one correct source for ordinary-search fallback. The initial ranking already had perfect top1 on these nine easy-to-identify positives: the improvement was reduced unnecessary refusal, not an increase from 7/9 to 8/9 ranking accuracy. Extra passes did not monotonically improve results. Merely reranking the same descriptions made one first choice worse, and the third broad evidence level added no aggregate gain.

Single-run branch median request times ranged from 209 to 295.5 ms; these are not whole-pipeline latency or full-agent costs. The main natural cases consumed provider input tokens A=38,239, R=15,585, B=18,408, C=20,594, W=129,884, V=9,562. Controls and widening were intentionally over-measured; no deployment cost or savings claim follows.

## Missing-source recovery test

A separate, labeled fault case repeated one question while withholding its actual source from the initial candidate pool. No-fresh-source reinspection could not recover it. W restored the full corpus, found the correct source, and the final check accepted it. This is one deliberate recovery test, excluded from normal accuracy totals. It demonstrates the need for wider search when the original shortlist is incomplete, not general recovery reliability.

## Targeted final zoom

The unresolved positive consistently ranked its correct source first but did not clear the direct-fit/confidence gate. Inspection of the original showed that the generic evidence pack omitted the specific chronology section requested. The lead added a deidentified, source-grounded navigation paraphrase of that section, preserving the same request and candidate pool. Three identical repeat calls then selected and accepted the correct source (confidence 0.89, 0.85, 0.85).

This is a post-hoc correction, not three new independent cases and not proof of automatic passage retrieval. It supports the hypothesis that missing relevant evidence caused the remaining hesitation. Because the lead manually supplied the passage after inspecting the failure, automatic query-specific passage selection remains unproven. Do not combine this hindsight fix into a claimed held-out 9/9 production score.

## Design consequence

Prefer evidence-driven stages: verified descriptions -> relevant source passages -> targeted additional passages or broader candidate search when needed -> original-source verification/fallback. Repeatedly asking the same thing with the same evidence is not an accuracy strategy. Do not use increased confidence alone as proof of correctness. The next bounded engineering question is how to select source passages reliably and maintain their provenance, rather than adding infrastructure or unlimited reasoning loops.

Private source maps, texts, gold labels and raw logs remain local. No source records, defaults, fleet cards or deployed services were changed. PR #82's opt-in direct-fit gate was used for evaluation; this does not merge or install it.
