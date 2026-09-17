# TypeSafe Jev response contract, as used by this harness

Every claim below is labelled:

- **DOCUMENTED** — stated by TypeSafe's own documentation, with the page it came from.
- **OBSERVED** — measured from recorded live responses, not promised by the provider.
- **INFERRED** — this harness's choice, defensible from the two categories above but not guaranteed.

Sources read on 2026-09-17:

- <https://docs.typesafe.ai/api> (HTTP API reference, response schema)
- <https://docs.typesafe.ai/primitives/score>
- <https://docs.typesafe.ai/primitives/choice>
- <https://docs.typesafe.ai/confidence>
- <https://docs.typesafe.ai/sdk/python/api/types/responses>
- <https://docs.typesafe.ai/llms.txt> (page index)

Evidence for the OBSERVED rows is `results.json` in the `jev-robust` experiment
package: 30 recorded live calls against model `jev-1.13.0`, containing 337
answers that carry a probability distribution.

There is now also **one live call**, made on 2026-09-17 against `jev-1.13.0`,
which returned a `score`, a `choice` and a `noul` answer in one response. It is
checked in verbatim as `test/fixtures/live-score-2026-09-17.json` and is
asserted by the test suite. It confirmed the documented `score` rule and
overturned one INFERRED rule outright; both are marked below. Everything else
in this document is still recorded data or documentation only.

## 1. Response shape

| Claim | Label | Source |
|---|---|---|
| Three question primitives: `choice`, `score`, `noul`. | DOCUMENTED | `/api`, `/primitives` |
| A `choice` answer carries `choice`, `probabilities`, `confidence`. | DOCUMENTED | `/primitives/choice` |
| A `score` answer carries `score`, `probabilities`, `legend`, `confidence`. | DOCUMENTED | `/primitives/score` |
| A `noul` answer carries a single value "on a scale from 0 (no) to 1 (yes)". | DOCUMENTED | `/api` |
| Every question is "evaluated in parallel and in isolation" against shared state. | DOCUMENTED | docs introduction |
| Recorded responses also carry `model` and `usage.input_tokens` / `usage.output_tokens`. | OBSERVED | all 30 calls |
| A `noul` answer carries only that value: no `confidence`, no `probabilities`. | OBSERVED | live call 2026-09-17 |
| A `score` answer carries `legend`, the level labels echoed back. | OBSERVED | live call 2026-09-17 |

This harness ignores `legend`; it derives level keys from the request it sent.
The validator checks a `noul` answer's value and nothing else, which is what
the live response shape requires.

## 2. What `choice` means

> "The highest-probability option." — `/api`

DOCUMENTED: `choice` is the argmax of `probabilities`.

OBSERVED: in 337 of 337 recorded choice answers, `choice` was an argmax key,
and it never carried zero probability mass.

The validator therefore requires `choice` to be a declared option whose
probability is within rounding tolerance of the maximum.

## 3. What `score` means — the important one

> "The probability-weighted answer across the levels; can land between levels." — `/api`
>
> "The `score` in the response is a position on the levels spectrum... it can
> land between two levels." — `/primitives/score`
>
> "It's each level number multiplied by its probability, added up:
> 0 x 0.0 + 1 x 0.70 + 2 x 0.30 = 1.30." — `/primitives/score`
>
> "Expected score, which may fall between the integer rubric levels" — `/sdk/python/api/types/responses`

DOCUMENTED: `score` is the expectation of the level index under
`probabilities`, that is `sum(i * probabilities[i])`. It is **not** the argmax
level, and it is **not** required to be an integer or to sit on a level that
carries probability mass.

This settles the open question in the robustness report. A rule of the form
"score must be a level with non-zero probability" would contradict the
documented semantics and would reject legitimate answers such as score `1.30`
over levels `{0: 0.0, 1: 0.70, 2: 0.30}`, where level 1.30 does not exist. The
harness implements the documented expectation rule instead.

OBSERVED, and **CONFIRMED LIVE 2026-09-17**: all 30 recorded calls used
`choice` questions only, so the recorded package contains no `score` answer at
all. The live call of 2026-09-17 supplied one, and it matches the documented
rule exactly: `score` 1.85 over `{0: 0.01, 1: 0.23, 2: 0.66, 3: 0.10, 4: 0.00}`
has expectation `0.23 + 1.32 + 0.30 = 1.85`, equal to the reported value with no
tolerance used. That is one answer from one model version, so it is a
confirmation rather than a guarantee, and the tolerance below stays in place.

### Tolerance on the score check

INFERRED. The docs state no rounding contract (see section 5), and the
`score` examples in the docs are themselves rounded to two decimals (`1.3`,
`1.06`). Comparing a reported `score` to a recomputed expectation therefore
needs slack for two independent roundings: the probabilities that feed the sum,
and the score itself. The harness allows

```
tolerance = 0.01 * (levels - 1) + 0.01
```

one rounding step per unit of level span, plus one for the score's own
rounding. For a two-level rubric that is 0.02; for five levels, 0.05. This is
deliberately loose: it is there to catch answers that are grossly inconsistent
with their own distribution, such as the reported `score: 0` with all mass on
level 1, not to police the provider's arithmetic.

## 4. What `confidence` means

> "How certain the model is, derived from probabilities." — `/api`
>
> "A number from 0 to 1 computed from how `probabilities` is spread." — `/primitives/score`
>
> "A flat shape, with probability spread across several options, means low
> confidence. A single peak on one option means high confidence." — `/primitives/choice`
>
> "you are never locked into our definition. Depending on what you are
> evaluating, a different measure may serve you better, which is exactly why we
> give you the full `probabilities`." — `/confidence`

DOCUMENTED: `confidence` is a number in `[0, 1]` derived from the shape of the
distribution. The documentation gives **no formula**, and in particular does
**not** say that confidence equals the maximum probability.

OBSERVED, across 337 recorded answers:

| Relationship | Count |
|---|---:|
| `confidence` exactly equals the maximum probability | 162 |
| `confidence` is less than the maximum probability | 175 |
| `confidence` exceeds the maximum probability | 0 |

**OVERTURNED LIVE 2026-09-17.** This harness used to reject an answer whose
`confidence` exceeded the maximum probability by more than one rounding step.
That rule was INFERRED, it was flagged here as the one rule a live probe could
overturn, and a live probe overturned it on the first try. The live `score`
answer in `test/fixtures/live-score-2026-09-17.json` returned

| Field | Value |
|---|---|
| `score` | 1.85 |
| `confidence` | 0.71 |
| `probabilities` | `{0: 0.01, 1: 0.23, 2: 0.66, 3: 0.10, 4: 0.00}` |
| Peak probability | 0.66 |

so a real `jev-1.13.0` response carries a confidence 0.05 above its own peak,
on a five-level distribution whose total is exactly 1.00. The rule is removed
from `src/jev.ts` entirely, not narrowed to `choice` answers: the docs give no
formula for confidence at all, so with the score case disproved there is no
basis left for asserting the relationship on any primitive. The live `choice`
answer in the same response happened to be consistent with the old rule
(confidence 1.0, peak 1.0), which proves nothing either way.

What the validator now checks about `confidence` is the whole of what TypeSafe
documents: that it is a finite number in `[0, 1]`. A value outside that range
still fails. Nothing downstream is loosened by this; `confidence` was never a
correctness signal.

Downstream code in this repo keeps using `confidence` for review gating, and
the organizer's gate stays at 0.75. The robustness report's finding stands
unchanged: confidence is not a correctness guarantee.

## 5. Do probabilities sum to 1, and is there a rounding contract?

> "Every option mapped to its probability (floats that sum to 1)." — `/api`
>
> "Each level (string key) mapped to its probability (floats that sum to 1)." — `/api`
>
> "sum of all values is 1" — `/primitives/score`

DOCUMENTED: the distribution sums to 1.

**The documentation states no rounding or decimal-precision contract.** That is
a documented absence, not an oversight on our side: the introduction, both
primitive pages, the confidence page, the HTTP API reference and the Python SDK
response types were all read, and none of them commits to a number of decimals
or to a rounding rule for the returned floats. The only rounding the docs
discuss is rounding you do yourself in your own code, for example rounding a
fractional score to the nearest level to make a decision. We have not invented
a provider-side contract.

OBSERVED, across the same 337 answers:

| Measurement | Value |
|---|---|
| Distribution totals, minimum | 0.99 |
| Distribution totals, maximum | 1.00 |
| Largest deviation from 1 | 0.01 |
| Answers with a total below 0.995 | 2 of 337 |
| Answers with a total above 1.005 | 0 of 337 |
| Decimal places in any probability value | at most 2 |

The two short totals are both in call `probe/repeat-identical-shuffled`, which
is a verbatim repeat of an earlier request. `record_10` returned
`{invoice: 0.1, contract: 0.09, support: 0.55, receipt: 0.02, other: 0.23}`,
totalling 0.99; `record_13` returned
`{contract: 0.14, invoice: 0.17, support: 0.11, receipt: 0.02, other: 0.55}`,
also 0.99. Every value in both is a two-decimal number, which is exactly what a
distribution rounded to two decimals does when the rounding happens to go down.
Three values elsewhere in the data print with 16 or 17 decimals (`0.81`, `0.46`,
`0.94`); those are IEEE-754 artifacts of two-decimal values, not extra provider
precision.

That one response made the old validator reject all 32 answers in the batch,
because its tolerance was 0.001.

### The normalization policy

INFERRED, and narrow on purpose:

1. A distribution is accepted only if its total is within **0.01** of 1. That
   band is the worst case for the two-decimal output we observe: five levels
   each rounded down by up to 0.005 cannot move the total by a full cent per
   level, and the observed worst case is exactly 0.01.
2. Inside the band, if the total is not already 1, the values are divided by
   the total so downstream arithmetic sees a true distribution. The provider's
   untouched values are kept on the answer as `rawProbabilities`, so an audit
   can always see what was actually returned.
3. Outside the band, the answer is rejected. Nothing is substituted, retried or
   guessed. The band is `|total - 1| <= 0.01`, so the rejection threshold is a
   total below 0.99 or above 1.01. That covers a total of 0, a total above 1.01,
   negative values, a `NaN`, a non-numeric value, a missing level and an extra
   level.
4. Validation does not write to the response it was given. `validateEvaluation`
   returns the evaluation to use downstream, and the provider's parsed reply is
   left exactly as it arrived, so a frozen response validates and a caller
   holding the original can still see what was actually returned. The loop
   journals and decides on the returned object.
5. Validation is a fixed point: validating an already-validated evaluation
   returns an equal object, including `rawProbabilities`.
6. `rawProbabilities` is this harness's own audit field and never a provider
   field. A value arriving under that name is never trusted. On the
   renormalizing path it is overwritten with the probabilities the provider
   actually sent. On the pass-through path it is kept only if it is a
   distribution over the same levels that renormalizes to those probabilities,
   which is what revalidating our own output looks like; anything else is
   rejected with `Untrusted rawProbabilities`, because trusting it would let a
   response forge its own audit trail.
7. A validation failure never triggers a retry of anything, and in particular
   never re-runs a tool effect. The adapter surfaces the failure for an
   explicit new run, which is the pre-existing behaviour in `src/jev.ts`.

Tightening this band is safe; widening it is not, until a live probe shows the
provider emitting three-decimal values or larger shortfalls.

## 6. What is still unverified

- One live call has been made, on 2026-09-17, and it is checked in as a
  fixture. Every other OBSERVED number above comes from 30 previously recorded
  calls to `jev-1.13.0`.
- The score expectation rule has now met real provider output exactly once. One
  answer, one model version.
- The 0.01 band rests on 337 recorded answers plus that one live response, all
  from one model version. Model aliases change, and a future version could emit
  different precision.
- `confidence` has no documented formula, and the one relationship this harness
  asserted about it was wrong. Nothing beyond "a number in `[0, 1]`" should be
  assumed about it.
- Whether repeating an identical request reliably reproduces the short totals is
  not established; the package has one such occurrence.
- The live call used one state and three questions. Other shapes, longer
  rubrics, and `noul` answers carrying extra fields are untested against the
  live provider.
