# Brain retrieval: richer versus compact index

Status: experimental; neither representation is approved as default brain retrieval. This documents a completed local comparison and its design consequence. It changes no runtime behavior.

## Comparison

The existing Super Jev `runFetch` evaluated two metadata representations of the same 234 record pointers. Both used task-supplied person scope, identical IDs and paired candidate order, original request wording, prefilter 40, top 3, zero retries, and unchanged confidence gates. B used richer descriptions. C removed repeated language and added some verified document-subject distinctions, but also removed useful information. This was not lossless compression or an isolated formatting experiment.

Twelve cases ran in both arms: nine source-present and three missing-source cases. Ten were development/regression cases and two exploratory; **none were held out**. Three other proposed cases were excluded before live execution because labels were unsupported or the expected source was indistinguishable from a non-gold source. These are navigation tests, not tests of clinical answers, multi-document synthesis, or full archive coverage.

| Completed comparison | Rich B | Compact C |
|---|---:|---:|
| Correct top 1, source-present | 6/9 | 6/9 |
| Correct within top 3 | 8/9 | 7/9 |
| Gold retained by local prefilter | 8/9 | 8/9 |
| Abstentions on source-present cases | 7/9 | 5/9 |
| Accepted correct first choices | 2 | 3 |
| Accepted wrong first choices | 0 | 1 |
| Abstentions on missing-source cases | 3/3 | 3/3 |
| Actual HTTP requests | 20 | 12 |
| Provider input tokens | 168,013 | 128,136 |
| Provider output tokens | 20,358 | 20,334 |
| Median query time | 560.5 ms | 286.5 ms |

All 24 jobs completed with 32 actual HTTP requests, within the 48-request cap. No transport or HTTP failures were recorded. Provider-reported model: `jev-1.13.0`. The compact arm used about 24% fewer input tokens and about half the measured median query time, but did not improve first-choice accuracy and introduced one accepted wrong selection. These are single-run observations, not stable latency estimates or end-to-end agent savings.

## Design decision

Do not deploy the compact representation as a default. Preserve distinctions that separate nearby source types, especially an intake log from a curated history. One compact-arm failure occurred before model ranking because the local prefilter dropped the gold source; another confused those two source roles and passed the confidence gate. Lowering the gate would not fix either failure.

The next bounded change should preserve distinguishing purpose language in compact entries and test candidate recall separately from ranking. Any future trial must keep original-source verification and ordinary search fallback for incomplete, unavailable, or uncertain retrieval. Confidence-gate acceptance alone is insufficient evidence of correctness.

## Evidence quality and reproducibility limits

The local runner meters actual HTTP calls, including adapter-internal attempts, persists each result, checks actual coverage manifests and errors, and distinguishes successful completion exactly at the cap from a refused over-budget request. Thirty-seven offline checks passed on the revised preparation, including failure handling and paired identity checks. The live driver exercised `runFetch`; the proposed advisory pointer wrapper was checked offline, not installed or validated as a complete live agent workflow.

Review corrected circular provenance labels: mentioning a portal or clinician document does not make a local summary an original export. Subject mentions also proved insufficient to label a record's modality. Private source inspection corrected the labels, and unsupported cases were excluded before spending calls. Reduced catalog bytes or increased unique-description count alone do not prove preservation of useful distinctions.

The private corpus, source maps, gold/source evidence and raw request logs are intentionally excluded from Git. Local evidence run ID: `lead-live-20260919T222447Z`. This public aggregate report is not a reproducible benchmark dataset; an independent nonsensitive held-out fixture is still needed before broader reliability claims. Earlier toy keyword/read-everything baselines do not establish normal-agent search costs.

Skills discovery remains a separate experimental integration. These brain findings do not validate other document formats, cross-person lookup, prompt-injection resistance, or fleet-wide adoption.
