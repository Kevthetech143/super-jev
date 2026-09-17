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
answers that carry a probability distribution. None of it is live-validated by
this pull request; no API call was made while writing it.

## 1. Response shape

| Claim | Label | Source |
|---|---|---|
| Three question primitives: `choice`, `score`, `noul`. | DOCUMENTED | `/api`, `/primitives` |
| A `choice` answer carries `choice`, `probabilities`, `confidence`. | DOCUMENTED | `/primitives/choice` |
| A `score` answer carries `score`, `probabilities`, `legend`, `confidence`. | DOCUMENTED | `/primitives/score` |
| A `noul` answer carries a single value "on a scale from 0 (no) to 1 (yes)". | DOCUMENTED | `/api` |
| Every question is "evaluated in parallel and in isolation" against shared state. | DOCUMENTED | docs introduction |
| Recorded responses also carry `model` and `usage.input_tokens` / `usage.output_tokens`. | OBSERVED | all 30 calls |

This harness ignores `legend`; it derives level keys from the request it sent.

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

OBSERVED: nothing. All 30 recorded calls used `choice` questions only, so there
is no live `score` answer in the package to check this against. The rule is
implemented from the documentation alone.

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

INFERRED: the harness rejects an answer whose `confidence` exceeds the maximum
probability by more than one rounding step (0.01). Every documented reading of
confidence makes a value above the peak internally inconsistent, and the
recorded data never produced one. This is a real rejection risk rather than a
guarantee: if TypeSafe's confidence statistic can legitimately exceed the peak
probability for some distribution shape, this check would reject a valid
answer, and it should be relaxed. It is the one rule here that a live probe
could overturn.

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
   guessed. That covers a total of 0, a total above 1.05, negative values, a
   `NaN`, a non-numeric value, a missing level and an extra level.
4. Normalization is idempotent. The loop validates the same object a second
   time, and the second pass leaves it and `rawProbabilities` alone.
5. A validation failure never triggers a retry of anything, and in particular
   never re-runs a tool effect. The adapter surfaces the failure for an
   explicit new run, which is the pre-existing behaviour in `src/jev.ts`.

Tightening this band is safe; widening it is not, until a live probe shows the
provider emitting three-decimal values or larger shortfalls.

## 6. What is still unverified

- No live call was made for this pull request. Every OBSERVED number above
  comes from previously recorded traces of model `jev-1.13.0`.
- There is no recorded `score` answer anywhere in the package, so the
  expectation rule in section 3 is documentation-only and has never met real
  provider output.
- The 0.01 band rests on 337 answers from one model version. Model aliases
  change, and a future version could emit different precision.
- The confidence rule in section 4 is the most likely of these to be wrong.
- Whether repeating an identical request reliably reproduces the short totals is
  not established; the package has one such occurrence.
