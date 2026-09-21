# The context enhancer

Prototype. Everything in `src/enhance/` runs offline against a stub provider. None of it has been measured on live traffic or on real data, and nothing here is wired into the organizer or the CLI.

## Why it exists

An offline robustness pilot on this repository found four things worth building against.

**Record association is fragile.** The same 32 labeled records, reordered inside one request, moved from 28 correct to 19 correct. Two of the wrong answers cleared the default 0.75 review gate. Naming each record and asking a question keyed by that name scored better than a positional array, but that variant also changes the prompt wording, so the experiment does not isolate the identifiers on their own.

**Confidence is not correctness.** A Spanish request for a copy of an invoice was classified as a contract at 0.81. An invoice with a partial payment was classified as a receipt at 0.79. Both cleared the gate. A number the model produced about its own answer cannot be the last line of defence.

**More evidence is not enough evidence.** On twelve return-eligibility investigations, an oracle evidence set containing every relevant document still produced a confident wrong answer where the governing policy was simply absent from the corpus. Absence has to be detected in code.

**A batch is a blast radius.** One malformed distribution in a response rejects the whole call under the adapter contract. With 32 records in one call, that is 32 records lost.

The primitives below are the mitigations named in the pilot's own build-gate list. They are prototypes of those mitigations, not evidence that the mitigations work.

## The primitives

### Named record references

`reference.ts`. Every record gets a stable id, caller-supplied or assigned as `records/<n>`. Ids are arbitrary caller strings, so each one is also given a safe question key, and the id-to-key pairing is carried explicitly rather than implied by array position. The request's state is an object keyed by that key, and each question names its own `records.<key>.text` path.

Answers are mapped back one record at a time against the pairing that was actually sent. Every asked key must produce exactly one answer. A key with no answer is reported missing and becomes an `unanswered` outcome. An answer key nobody asked for is rejected and never attached to a record. The positional array framing is kept alongside the keyed one so the two can be compared, and both share the same mapping code so neither can drift into matching by position.

### Bounded, budget-aware batching

`budget.ts` and `batch.ts`. A `ContextBudget` declares `maxInputTokens`, `reservedForQuestions`, `reservedForOutput`, a `tokenEstimator` and a hard `maxRecordsPerCall`. The planner returns the full plan, calls and records per call and estimated tokens per call, before any call happens. A caller can read the plan, decide the run is too big, and stop without spending anything.

Three rules the planner will not bend. A call never exceeds `maxRecordsPerCall` whatever the token arithmetic says. A record whose own cost exceeds the per-call allowance is never packed; it lands in `oversizedRecordIds`, is never sent, and is routed to review. A planned call's estimate never exceeds `maxInputTokens`, so the planner splits or refuses rather than reporting a number the request builder will overshoot. There is no "it will probably fit".

**What the plan guarantees.** The estimate counts every piece of text the request actually carries: each record's text, each record's id, and each record's own question, built from the same question builder the runner uses. The option descriptions and the per-record instruction line repeat once per record on the wire, so they are counted once per record here. On the pilot's 32 records the plan lands within 2% of the request it predicts, and the tests hold it to 5% per call and in total. The classifier and the investigation runner both compute their per-call estimate by calling the same function the plan called, so a reviewed estimate and a charged estimate cannot drift apart.

**What the plan does not guarantee.** It is not a token count and not a billing figure. `tokenEstimator` defaults to characters over four, which is a rule of thumb and not the provider's tokenizer, so the whole plan inherits that error; a different corpus or a different language will shift it. The per-record envelope figure is a measured constant for the shapes in `reference.ts`, not a parse of the serialized request. Output tokens are a reservation, not an estimate: nothing here predicts how long an answer will be. And a plan that fits says nothing about whether the answers will be any good.

The flat `reservedForQuestions` reservation is still available and still the default when a caller uses `planBatches` directly with no question builder to draw on. It is a reservation, not a count: it does not grow with the batch, so on a large call it under-reports badly. The plan says which mode it used, in `questionsCounted` and in the printed budget line, because only a counted plan's number is comparable to a request.

### Coverage manifest

`coverage.ts`. After a run, every input record appears in exactly one bucket: `accepted`, `review`, `abstain`, `insufficient_evidence`, `failed_validation`, `unanswered` or `unaccounted`. A record the run never reached does not vanish; it becomes `unaccounted` and the manifest reports `complete: false`. A record reported twice is a problem, not last-write-wins. `assertComplete` throws, so a run that cannot account for its own input cannot report success.

### Required-evidence checks

`evidence.ts`. A declarative spec names ordered roles, for example ticket then order then policy, with a predicate per role. The traversal is a breadth-first walk of links that already exist in the data, bounded by `maxDepth` and `maxDocs`, with cycle detection. It follows references. It does not search, rank or retrieve.

Completeness is then checked in code, before the final question is asked. A required role no live document fills blocks. A unique role filled by two or more live documents with no supersession to break the tie blocks. A walk stopped by a bound blocks, because a partial evidence set cannot be called complete. A document whose text marks it superseded is removed from its role and listed separately, so "v2 supersedes v1" resolves to one live policy instead of a conflict. Source ids of every document that filled a role are retained for the audit trail, and only those documents are sent.

A dangling reference is a configured choice, `danglingReferenceIs`, and it defaults to `"incomplete"`. When the dangling reference is the reason a required role is empty, the role check blocks either way and the flag is irrelevant. The flag decides the one case where the role ended up filled another way, for example by the prose parser recovering a policy behind a typo'd link. On `"incomplete"`, the default, the broken reference blocks: the data itself says a document is missing, and whether that document would have changed the answer is exactly what cannot be known without reading it. On `"warning"` the reference is a data-quality note that travels with the result and does not block; that is the right reading for a corpus where broken links are known to be routine noise, and the price is that a genuinely relevant missing document then reaches the model as a note. Either way the reference is recorded in `warnings`, so a reader scanning the data-quality notes sees it without having to know how the flag was set. The recorded pilot ran the `"warning"` reading, so the twelve-case fixture pins it explicitly and stays comparable; the demo runs the new default, which is why case T8 blocks there.

There is an optional narrow prose-reference parser, off by default. It matches only `order reference <ID>` and `policy reference <ID>`. It is not general entity discovery and not relationship extraction. It was written after seeing those two phrasings in the pilot corpus, it will miss any other phrasing, and a different corpus needs its own parser. Turning it on is recorded in the warnings so a reader knows an id came from prose rather than from a structured link.

### Explicit review and abstention

`outcome.ts`. The gate checks in this order, and the order is the policy:

1. evidence incomplete, nothing asked, gives `insufficient_evidence`
2. no answer at all gives `unanswered`
3. a malformed answer gives `failed_validation`
4. passes disagree gives `review`
5. the model chose the abstain option gives `abstain`
6. confidence overclaims the answer's own distribution gives `review`
7. confidence below the gate gives `review`
8. everything above satisfied gives `accepted`

Confidence never reaches step 8 on its own, because a high number cannot skip steps 1 through 7. Disagreement between passes blocks action regardless of confidence, and so does incomplete evidence. Only `accepted` permits an automatic action. `abstain` is kept distinct from `review` so a deliberate declining answer is not counted as a low-confidence accident.

**Step 6, the cross-check.** Confidence is a number the model produces about its own answer. The distribution is a second, separately reported number about the same answer. Reading only the confidence means trusting one of the two and ignoring the other, so the gate compares them: it takes the peak of the answer's own probabilities and routes the record to review when the confidence sits more than `maxConfidenceAbovePeak` above that peak. The peak is normalized first, because the provider sends two-decimal probabilities whose total runs from 0.99 to 1.00 and a rounding-sized shortfall must not move it.

The tolerance is 0.05 and it is INFERRED, a harness choice rather than anything the provider documents. A strict rule, where any excess is a fault, was tried and is wrong: the live probe of 2026-09-17 returned a score answer with confidence 0.71 over a peak probability of 0.66, exactly consistent with its own distribution in every other respect. That single observation is the whole basis for the number, which is why 0.05 sits just above it. It is not a measured threshold, and tightening it needs more live data than one answer.

When an answer carries no usable distribution there is nothing to cross-check. A noul answer is the real case: it returns the single value and nothing else. The gate then falls back to confidence exactly as before and marks the outcome `confidenceOnly: true`, so a reader knows the gate had only the model's self-report to go on. One pass reporting a distribution is enough to clear the marker.

### Cost accounting

`cost.ts`. Every run returns `calls`, `inputTokens`, `outputTokens`, `retries`, `wallMs` and `perCallLatency`, so retries and extra passes are visible rather than hidden inside a wall-clock number. A failed attempt counts as a call, because it was billed. Provider-reported usage is used when present; otherwise the estimator fills in and the whole account is flagged `estimated`, because a mixed total is not a billing statement.

## The sweep

`sweep.ts` and `src/sweep-cli.ts`. The first thing in `src/enhance/` that is a whole job rather than a primitive: a pile of records bigger than one call, processed in full, with proof that nothing was skipped. Run it with `npm run sweep`.

```
npm run sweep -- --records records.jsonl --questions questions.json --out out/ --dry-run
npm run sweep -- --records records.jsonl --questions questions.json --out out/
```

### The unit of work is a cell, not a record

The classifier asks one question per record. A sweep asks several named questions of every record, so the unit is a cell: one record crossed with one question. A call carries `records x questions` questions. That is the only structural difference, and it is the reason the provider's per-call question cap binds here and never binds the classifier.

Records arrive as JSONL, one `{"id", "text", "meta"}` object per line. Only `id` and `text` are ever sent. `meta` is caller data, it stays local, and it is echoed back on the matching output row, which is what lets a caller carry a record's provenance through the run without handing it to a provider.

Questions arrive as JSON, an array of choice questions each with a `name`, `instructions` and `criteria`. The CLI expands them: every question in the file is asked of every record in the file.

### Keys: the logical one and the one on the wire

Every cell has two identifiers. The logical key is `q.<name>.<recordId>` and it is what every output file reports. The wire key is a safe object key derived from the question name and the record's own reference key.

They are separate for the reason `reference.ts` already separates a record id from its question key: a record id and a question name are arbitrary caller strings, and a request key has to be a safe object key, so a dotted logical key cannot also be the key on the wire. The pairing is carried explicitly for every cell in every call, so an answer is never matched to a cell by position. A record id that sanitizes into a collision falls back to a positional wire key, which is safe because uniqueness only has to hold inside one request.

### Two caps, and the tighter one wins

A call is bounded by `maxRecordsPerCall`, which `--batch` sets, and independently by the question cap, which allows `floor(cap / questions per record)` records per call. The plan applies both and reports which one decided, in `recordsPerCallReason` and in the printed plan. A question set larger than the cap cannot carry even one record, and that is refused outright rather than silently truncated.

The token budget is the third bound and it is enforced by `batch.ts` exactly as before. Question text is counted per record rather than reserved flat, because every question is sent once per record: a record carrying three questions pays for three question bodies and three per-record envelopes, and the estimate says so.

`MAX_QUESTIONS_PER_CALL` is 255. That figure is DOCUMENTED by the brief that commissioned this feature and is not measured by this repository; `docs/provider-contract.md` records no question-count limit at all. It is a named constant and a `maxQuestionsPerCall` knob rather than a buried literal, so a corrected figure is a one-line change. Planning under the real cap wastes a little of the window; planning over it loses a whole call, so the bound is treated as hard.

### The gate is per cell, the grouping is per record

Each cell goes through `outcome.ts` unchanged, so a sweep inherits the whole gate policy including the confidence-versus-distribution cross-check. The record-level disposition is then the worst of its cells: `failed_validation` beats `unanswered` beats `review` beats `accepted`. A record is ACCEPTED only when every question about it cleared the gate. The sweep gate defaults to 0.80, stricter than the 0.75 organizer gate, and `--gate` moves it.

A sweep is a single pass, so there is no cross-pass disagreement check here. That is a real reduction in strictness against the classifier's two-pass default, taken because a sweep's job is volume and a second pass doubles a large run's cost. The disagreement detector is still the stronger rule and a caller who needs it should use the classifier.

### One bad answer costs one cell

`validateEvaluation` is the adapter contract and it is all-or-nothing: one malformed distribution rejects the whole response. On a call carrying 255 cells that is 255 records' worth of work lost to one bad answer, which is the "a batch is a blast radius" failure named above at its worst.

So the sweep validates one cell at a time, with the same validator, against a request containing only that cell's own question. A malformed answer then costs its own cell and nothing else, and the validated copy the contract hands back is what reaches the gate. A call is only retried when the whole response is unusable, because re-asking 255 cells to chase a handful costs more than it saves. Rejected unasked keys, missing cells and per-cell validation failures are all recorded and reported.

### Batch size and the re-ask band

The sweep's own default for `maxRecordsPerCall` is 45, distinct from the shared enhancer default of 4 that the classifier and the investigator still use. It comes from a hand ground-truth review that compared a small batch size against a much larger one on the same real pile: the larger batch was safe, in that it showed no drift toward accepting more records and no falloff in quality for records answered later in a call, and it was substantially cheaper in wall time for the same token spend. But the larger batch also came out a little less sharp specifically on the classification question, the one that decides whether a record is worth keeping at all. 45 sits in the reviewer's recommended middle range: most of the latency win, without paying the larger batch's full cost in classification sharpness. `--batch`, the `SWEEP_BATCH` environment variable, and `budget.maxRecordsPerCall` on a library call all override it in either direction, and a batch larger than 45, up to the 255-question cap, is explicitly allowed and was found safe by that review, not merely tolerated.

The same review found that batch size was never really the lever worth pulling: almost every disagreement between the two batch sizes it compared was a record whose deciding confidence, the lowest confidence among its cells, sat close to the 0.80 accept gate. Both batch sizes agreed on the record's actual label in nearly every one of those cases; they only differed in how sure the model sounded, and that is boundary noise rather than a real difference of judgement.

The re-ask band targets that noise directly, instead of trying to fix it with batch size. After pass 1, any record whose deciding confidence lands in a configurable half-open band, `[0.70, 0.90)` by default, is asked a second time, in its own second call. A second call never carries more than 20 records regardless of the primary batch size, so a re-ask stays cheap even when the primary batch is large. The final label folds the two passes: a record is ACCEPTED only when *both* passes independently accept it; a record pass 1 already sent to review is never pulled back to accepted by a second pass, since it already failed the gate once. A record outside the band, whether clearly accepted or clearly not, is left alone: it is never sent a second time and never costs a second call. `--reask-band LO,HI` moves the band; `--no-reask` turns the whole mechanism off and returns the sweep to a single pass, exactly as before this feature existed.

The band check, like everything else the sweep does, uses the named-reference framing and reads pass-1 band membership off the results in the original record order, so which records get re-asked, and how the re-ask calls are batched, never depends on how pass 1 happened to split records across its own calls. A caller of `runSweep`/`planSweep` as a library (the `fetch` door, for instance) never gets a re-ask unless it sets `reaskBand` itself; the CLI is the only place the default band is switched on automatically.

Every re-asked record's pass-1 and pass-2 outcome, and the confidence each pass decided on, are carried in `results.jsonl` under a `reask` block, and the calls a re-ask spent are counted in the same `cost.json` totals as every other call the run made — there is no separate, hidden ledger for the second pass.

### Outputs

`--dry-run` prints the plan, writes `plan.json`, and reaches no network by construction. Live and `--stub` runs write five files, each created and never overwritten:

| file | what it holds |
|---|---|
| `plan.json` | calls, records per call, which cap decided, estimated tokens, oversized records |
| `results.jsonl` | one row per record: the record id, the echoed `meta`, and per question the choice, the confidence, the probability distribution and the cell's own outcome |
| `manifest.json` | the coverage manifest: every input record in exactly one bucket, with `complete` and any problems |
| `cost.json` | calls, tokens, retries, wall time, per-call latency, and whether the figures are estimates |
| `report.md` | ACCEPTED and REVIEW tables with counts, the manifest buckets, the errors, and what the report is not |

A live run needs `TYPESAFE_API_KEY` and the CLI refuses to start without it, before it reads any input file, unless `--dry-run` or `--stub` was asked for. An incomplete manifest exits non-zero, so a run that cannot account for its own input cannot report success.

`--stub` runs the whole path against `StubEvaluator` with deterministic synthetic answers, derived from a hash of the question object so the same record gets the same answer at any batch size. It exercises the plumbing with no key and no network. It is not evidence about anything.

## Budget knobs and the defaults

| Knob | Default | Why |
|---|---|---|
| `maxInputTokens` | 8000 | A deliberately small working window, so the batcher has to do real arithmetic rather than fitting everything by accident. |
| `reservedForQuestions` | 1500 | A fallback reservation, used only when the planner has no question builder to count from. Option descriptions repeat in every record's question, so the real question block grows with the batch and a flat reservation under-reports it; the classifier and the investigator therefore count the question text instead. |
| `reservedForOutput` | 2000 | The answer block carries a distribution per record and grows with the batch. |
| `maxRecordsPerCall` | 4 | Batches of four beat one large batch in the pilot, and one rejected response then costs four records instead of thirty-two. |
| `tokenEstimator` | `chars / 4`, rounded up | Deterministic and provider-independent. A rule of thumb, not a tokenizer, and never reported as a billed count. |
| `minConfidence` | 0.75 | Matches the organizer's existing gate. Necessary, never sufficient. |
| `maxConfidenceAbovePeak` | 0.05 | How far a confidence may sit above the peak of its own distribution before the answer is treated as overclaimed. INFERRED from one live answer, at confidence 0.71 over peak 0.66. Not a measured threshold. |
| `danglingReferenceIs` | `"incomplete"` | A reference the data makes to a document nobody holds means the evidence set is missing something on the data's own account. The safer reading blocks; `"warning"` keeps the old note-and-continue behaviour. |
| passes | two, named references, opposite record orders | Answers moved with record order in the pilot, so a second pass in a different order is the cheapest disagreement detector available. |
| `maxRetries` | 1 | One retry per call on a rejected response. Retries are counted and reported. |
| sweep `gate` | 0.80 | A record is ACCEPTED only when every question about it clears this. Stricter than the organizer's 0.75 because a sweep has no second pass to catch disagreement. |
| `maxQuestionsPerCall` | 255 | The provider's per-call question cap, which decides how many records a call can carry once questions are multiplied out. DOCUMENTED by the commissioning brief, not measured here. |
| sweep `maxRecordsPerCall` (`--batch` / `SWEEP_BATCH`) | 45 | The sweep's own default, distinct from the shared enhancer default above it. From a ground-truth review of batch sizes; see "Batch size and the re-ask band". Larger batches, up to the 255-question cap, are allowed. |
| sweep `reaskBand` (`--reask-band`) | `[0.70, 0.90)` on by default in the CLI, opt-in as a library call | Any record whose pass-1 deciding confidence lands in this band is asked a second time; both passes must accept for a final accept. `--no-reask` disables it. |
| sweep re-ask batch cap | 20 records per call | A second-pass call never carries more than this many records, independent of the primary `maxRecordsPerCall`. |

Every one of these is an argument, not a constant. The two-pass default roughly doubles calls and tokens for a run; that is the price of the disagreement check, and the cost report is there so the price is visible.

## Running it

```
npm run demo:enhance
npm test
npm run sweep -- --help
```

The demo runs both pipelines against the stub, prints the plan before any call, then the coverage manifest, the cost summary, and the per-case evidence decisions. No API key, no network, no charges.

**The stub's probability floor.** The adapter contract requires a choice answer's chosen option to be the peak of its distribution, which is impossible when the recorded confidence is below one over the number of options. So `stub.ts` floors the chosen option's probability mass at that value when it builds a fixture. The recorded confidence itself is carried through untouched and is the only field any decision reads; the distribution around it is synthetic padding. One of the 164 recorded answers is affected: D18 in the `arrayShuffled` table, recorded at confidence 0.16 against five options, is padded to 0.20 of the mass while its confidence stays 0.16.

The offline fixtures port the pilot's 32 labeled classification records and 12 linked-evidence cases, and script the stub with answers recorded from the live pilot calls. With those answers the tests assert that records map back correctly under shuffled input order and any batch size, that the manifest accounts for every record exactly once, that the missing-policy and conflicting-policy cases are blocked in code before any question is asked, and that the two records the pilot accepted wrongly are both routed to review by cross-pass disagreement rather than accepted on confidence.

That last result deserves care. It holds because the two passes draw on two different recorded framings that genuinely disagreed in the live pilot. It shows the rule firing on real recorded disagreement. It does not show that the rule catches accepted errors in general.

## What this does not do

- **No accuracy claim.** The stub replays scripted answers. It cannot be right or wrong, so no test here measures accuracy, and none of this is evidence that the enhanced path classifies better than the plain organizer. Only a live run can measure that.
- **No latency or cost claim.** The stub does not call a provider. The token figures in the demo come from the stub's own scripted usage block and from the estimator. They are not measurements.
- **No proof of correct global reasoning across chunks.** Bounded batching lets every record pass through the model across several calls. It does not show that a conclusion requiring facts from two different calls is reached correctly, and it does not show that an oversized document survives chunking with its facts intact. Cross-chunk contradictions, duplicate handling, summary loss and long-range reasoning all still need their own benchmarks.
- **No retrieval.** The traversal follows links that already exist in the data. There is no search, no ranking, no embeddings, and no discovery of a relationship that was not already written down. The prose parser matches two hardcoded phrases and is not a substitute.
- **No real-data validation.** Every fixture is synthetic, written as an adversarial example. There is no consented, de-identified real-use corpus behind any of it, and no independently reviewed labels.
- **No coverage claim.** A high correctness count is not an automation rate. In the demo's deliberately strict two-framing configuration most records land in review, and the ratio of accepted to review on real work is unknown.
- **No security audit.** The injection fixtures measure classification confusion only. Untrusted text is carried as evidence and never executed, but that is a design property here, not a tested security boundary.
- **Not wired in.** `src/loop.ts`, `src/cli.ts` and `src/organizer.ts` are untouched. Outside `src/enhance/` the additions are one export line in `src/index.ts`, the sweep's own CLI in `src/sweep-cli.ts` and its `npm run sweep` script. Nothing in the shipped organizer path behaves differently.
- **The sweep has no live numbers.** Every sweep test runs against a table-driven offline evaluator or the stub. They check the expansion, the two caps, the manifest, the per-cell validation salvage and the gate grouping. None of them measures accuracy, latency, cost or the accepted-error rate, and the wishlist's two sweep benchmarks, facts split across a chunk boundary and duplicated or superseded records, are not built here.
- **The 255-question cap is unverified by this repository.** It comes from the brief, not from a provider document and not from a measurement. If the real cap is lower, a planned call will be rejected whole.
- **A sweep is one pass, plus an optional targeted second pass.** It drops the classifier's default cross-pass disagreement check across the whole run, so a sweep's ACCEPTED bar is still genuinely lower than the classifier's. The re-ask band only re-asks the records whose deciding confidence sits near the gate; it is not a second full pass over the pile and is not a substitute for the classifier's disagreement check.
- **The re-ask band is untested against a live provider.** It is unit-tested with an offline table-driven evaluator only, covering agreement, disagreement, out-of-band records being left alone, order-safety, and the 20-record re-ask cap. Its batch-size rationale comes from a hand ground-truth review of one pair of real runs; the review's own author called the direction well-supported and the effect size unverified.
- **The disagreement rule is not free and not validated.** It doubles calls, it will send genuinely ambiguous records to review forever, and no one has measured how many correct answers it blocks alongside the wrong ones.
- **The confidence cross-check is calibrated on one answer.** The 0.05 tolerance comes from a single live probe. Nobody has measured how often a real overclaiming answer is also a wrong answer, so the rule's value is assumed, not shown, and the tolerance could be far too loose or far too tight.
- **No estimator accuracy claim.** The plan now matches the request it predicts, which is a statement about two pieces of this harness agreeing with each other. It is not a statement about the provider's tokenizer, and the plan is still not a billing figure.

## Measuring it for real

What a live run would need to establish, none of which this prototype supplies: accepted-error rate and automation coverage reported together, on a held-out set with labels fixed beforehand; the same comparison against the plain organizer, keyword retrieval with abstention, embeddings and a general model; latency, tokens and failure recovery per configuration; acceptable error rates defined per use case before the numbers arrive, with stricter gates for irreversible actions than for a reviewable inbox; and repeated runs under randomized order to see whether the order sensitivity that motivated all of this is actually reduced.
