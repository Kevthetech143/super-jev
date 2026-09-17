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

Every one of these is an argument, not a constant. The two-pass default roughly doubles calls and tokens for a run; that is the price of the disagreement check, and the cost report is there so the price is visible.

## Running it

```
npm run demo:enhance
npm test
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
- **Not wired in.** `src/loop.ts`, `src/cli.ts` and `src/organizer.ts` are untouched. The only change outside `src/enhance/` is one export line in `src/index.ts`. Nothing in the shipped organizer path behaves differently.
- **The disagreement rule is not free and not validated.** It doubles calls, it will send genuinely ambiguous records to review forever, and no one has measured how many correct answers it blocks alongside the wrong ones.
- **The confidence cross-check is calibrated on one answer.** The 0.05 tolerance comes from a single live probe. Nobody has measured how often a real overclaiming answer is also a wrong answer, so the rule's value is assumed, not shown, and the tolerance could be far too loose or far too tight.
- **No estimator accuracy claim.** The plan now matches the request it predicts, which is a statement about two pieces of this harness agreeing with each other. It is not a statement about the provider's tokenizer, and the plan is still not a billing figure.

## Measuring it for real

What a live run would need to establish, none of which this prototype supplies: accepted-error rate and automation coverage reported together, on a held-out set with labels fixed beforehand; the same comparison against the plain organizer, keyword retrieval with abstention, embeddings and a general model; latency, tokens and failure recovery per configuration; acceptable error rates defined per use case before the numbers arrive, with stricter gates for irreversible actions than for a reviewable inbox; and repeated runs under randomized order to see whether the order sensitivity that motivated all of this is actually reduced.
