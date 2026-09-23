# bench:live, the measurement runner

The enhancer prototype shipped with an explicit caveat: none of it had been
measured against live traffic. This runner is what removes that caveat or kills
the prototype. It runs the baseline the pilot actually measured and the enhancer
path side by side, under the same labels, in the same session, and reports the
accepted-error rate for both.

## Running it

```
npm run bench:live -- --dry-run   # the plan, the call count, the token estimate. No network, no key.
npm run bench:live -- --stub      # the whole bench against the offline stub. No network, no key.
TYPESAFE_API_KEY=... npm run bench:live
```

A live run refuses to start without `TYPESAFE_API_KEY` in the environment: one
line on stderr, exit code 2. The runner reads no `.env` file and no credential
file, and it records requests and responses only, never headers, so no key
reaches an artifact. On a Mac where Node cannot find a certificate bundle,
prefix the live command with `SSL_CERT_FILE=$(python3 -m certifi)`.

## What it measures

48 records: the 32 labeled pilot records and the 16 holdout records, copied
verbatim into `bench/fixtures/` with their labels untouched. Six conditions, run
on each set and reported per set and combined:

| Condition | Path | References | Batch | Passes | Order |
| --- | --- | --- | --- | --- | --- |
| A | baseline organizer | positional `records[i]` | all records, one call | one | forward |
| B | baseline organizer | positional `records[i]` | all records, one call | one | shuffle seed 1 on the 32, reverse on the 16 |
| C | enhancer | named `records.<id>.text` | four per call | one | forward |
| D | enhancer | named `records.<id>.text` | four per call | one | shuffle seed 1 on the 32, reverse on the 16 |
| E | enhancer, full default | named `records.<id>.text` | four per call | two, opposite orders, disagreement blocks | shuffle seed 1 on the 32, reverse on the 16 |
| F | as E | as E | as E | as E | shuffle seed 2 on both sets |

The gate is the organizer's own default and is not a new variable: accept only
at confidence 0.75 or above, require agreement across passes, and treat `other`
as a deliberate abstention rather than a low score.

Every order is produced by a seeded shuffle, so a condition can be rerun and
land on the same order. The plan is 76 calls with no retries.

## What comes out

Each run writes a timestamped directory under `bench/out/`, which is not
committed:

- `raw.jsonl`, one line per provider call with the request, the response, the
  returned model field, the usage block and the latency.
- `report.md`, one table per record set plus the combined 48, the accepted-wrong
  records with their confidences, the prompts actually sent, and a paragraph on
  what the run does and does not show.
- `results.json`, the same numbers machine-readable, plus every coverage
  manifest.

The column that matters is the accepted-error rate: records that were wrong and
were accepted for automatic action. Accuracy on its own is not the deliverable,
because a system that routes its mistakes to a human is better than one with a
slightly higher accuracy that acts on them.

## Limits

48 synthetic adversarial records, one order seed per condition, no repetition at
a fixed order, no confidence interval and no significance claim. A gap of one or
two records between conditions is inside the noise this design cannot resolve.
The labels were fixed before any call in the original pilot, so the scoring is
not tuned to the answers, but these are still our own records and not a sample
of real traffic. Results are internal only under the provider terms of service:
not a public benchmark, not a comparative claim.
