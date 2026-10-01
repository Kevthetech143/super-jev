# Judge support: which judge on which build

A judge (a profile in `judge_profiles.json`, or the test judge `fake`) is "supported" on a build only when a measured run on that same build says so. This page covers the three pieces that exist: the fingerprint that ties a result to a build and a judge, the run that grades the judge against your own bar and writes one record, and `--applies`, which reads a record back and says whether it still applies to a build.

## The fingerprint

```sh
python3 judge_support.py --fingerprint --ask BUILD/ask.py --judge NAME [--env NAME=VALUE ...]
```

Free: no ask, no judge call, no key. It prints one JSON line: the five parts below and `fingerprint`, a SHA-256 over them. Exit 0 prints it; exit 2 is bad input (an unknown judge, a malformed or repeated `--env`, an `--env` for a setting the tool sets itself, a build whose `judge_profiles.json` cannot be read).

| part | what it is |
|---|---|
| `judge` | the profile key after aliases (`typesafe` and `typesafe-jev` give the same part) |
| `implementation` | `profile`, or `fake` for the test judge. `fake` uses the default profile's numbers, so this part keeps a fake result from ever standing in for the default judge |
| `profile_sha256` | that profile's entry in the build's own `judge_profiles.json`, as canonical JSON, without keys that start with `_` (comments and sources) |
| `judge_path_sha256` | the build's code files (`.py`, `.ts`, `.mjs`, `.js`, `.sh`) under `skills/super-jev/` (not its `tests/`), `src/` and `experiments/verified-pointer-memory/`, plus `secret_patterns.json`, which decides what may reach the judge |
| `env_sha256` | the sorted `--env NAME=VALUE` pairs. Values can hold a token, so they are hashed and never printed |

It changes exactly when the judge, its profile, an explicit setting or the engine code that talks to the judge changes. It does not change when you edit another profile, a `_source` or `_comment`, a test, a doc, the changelog or a prepared set (`prepare-cache`), or when the same build is copied to another folder.

The judge path is broad on purpose: the questions put to the judge are worded in `ask.py`, `superjev.py` and `src/`, and thresholds sit outside the profile table. A change to any of them is a new build as far as a result is concerned. A comment-only edit to one of those files counts too; that costs one re-run, never a wrong "supported".

## What the fingerprint cannot see

- A hosted model alias can change behind the same profile (the provider swaps the model under the same name). The fingerprint cannot see that; re-run on request when you learn of it.
- A setting you did not pass with `--env` is not part of it. Settings a run needs must be passed explicitly.
- Prepared sets (`prepare-cache`) are not part of it: they are the data a run reads, not the judge.

## The run

```sh
python3 judge_support.py --ask BUILD/ask.py --judge NAME \
    --cases FILE --cases-sha256 SHA --bar FILE --bar-sha256 SHA \
    --record FILE --max-asks N [--env NAME=VALUE ...] [--time-cap-min M] [--timeout SECS] [--seed N]
```

It asks the judge every case in the file once, on a frozen copy of your state and memory (the paid-replay snapshot: no saved answers, no reconnects, no auto-heal), times each ask, and grades the whole file against the bar. The run is paid: every ask calls the judge many times. `--max-asks` is a hard cap that refuses to start when the file has more cases. Both files are pinned by SHA-256 so a result names exactly what it was graded on.

Exit codes: `0` supported, `1` not supported, `3` incomplete (nothing is claimed either way), `2` bad input (nothing was asked, no record is written).

The child ask runs with your environment minus every `SUPERJEV_*` variable, plus the `--env` pairs, plus `SUPERJEV_JUDGE` set to `--judge`. A stray `SUPERJEV_*` in your shell therefore never reaches the judge, and the fingerprint (which hashes the `--env` pairs) describes what actually ran. A setting the tool owns (`SUPERJEV_JUDGE` and the replay settings) cannot be passed with `--env`, whichever command reads it. The record names the `--env` settings and never holds their values: a value of six or more characters is also scrubbed from any error line it shows up in (a shorter one is left alone, because it would blank ordinary text, so do not pass a secret that short).

### Case file

The paid-replay case format (JSONL, one case per line): `question`, `gold` (absolute paths), `principal`, `split`. A claim case adds `"kind": "claim"` and `"expected": "TRUE"`, `"FALSE"` or `"ABSENT"`. A not-in-files case has `"gold": []` and either `"absent": true` (a question) or `"expected": "ABSENT"` (a claim). The whole file runs, in an order set by `--seed` (default 0; recorded), so the same seed gives the same order. Each principal runs against its own state.

### Bar file

An example only; choose your own numbers:

```json
{"max_excluded": 2, "rows": [
  {"measure": "top5", "min_rate": 0.9},
  {"measure": "claims_wrong", "max_count": 1},
  {"measure": "secs", "percentile": 50, "max_secs": 45}
]}
```

There are no defaults: a bar without `max_excluded`, with no rows, with an unknown measure or key, with a repeated row or with a row that measures a kind of case the file does not have is refused with exit 2 before any ask.

| measure | what it counts | bar key |
|---|---|---|
| `top5` | answerable questions whose gold file is in the final five | `min_rate` |
| `rank1` | answerable questions whose gold file is ranked first | `min_rate` |
| `absent` | not-in-files questions left unanswered (see below) | `min_rate` |
| `claims_right` | TRUE or FALSE claims whose verdict is the expected one (an `ABSENT` claim is not counted here; `claims_absent_asserted` covers it) | `min_rate` |
| `claims_wrong` | claims asserted TRUE or FALSE against the expected verdict | `max_count` |
| `claims_absent_asserted` | `ABSENT` claims asserted TRUE or FALSE | `max_count` |
| `secs` | the asks finished within `max_secs`; it passes when that share reaches `percentile` (nearest rank) | `percentile`, `max_secs` |

A rate has at most two decimals, and the hits it needs are an integer: `ceil(rate x n)` computed in whole numbers (0.28 of 25 needs 7, never 8 from float rounding). A row that measured nothing never passes.

A not-in-files question passes only when the final ranking is empty, whatever the exit code: exit 1 (searched fully, nothing found) and exit 4 (nothing found, but the search was incomplete) both count. The record keeps each case's exit code, so the difference stays visible. An errored or timed-out claim is held, never asserted: it counts as an error and fails `claims_right`, and it can never make a `claims_wrong` or `claims_absent_asserted` count. An errored question is a miss on every row and over every time line.

### Stopping, and the verdict

- A case whose gold files are all missing at the start, or whose gold or source files changed while its ask ran (drift), is excluded and counted. At most `max_excluded` of them are allowed; one more ends the run `incomplete`.
- The run stops early, `not supported`, as soon as a row can no longer reach its bar even if every remaining case hits (a count row: as soon as it is over its limit). The record says which row and how many cases were not run.
- After an early stop a row reads `PASS` or `FAIL` only when the cases already run settle it: `need` is worked out over every case the row would see (`n`, of which `not_run` have not run), so a rate row is `PASS` once its hits reach that need and `FAIL` once they cannot, and a count row is `FAIL` once it is over its limit. Any other row reads `OPEN` (`"pass": null` in the record) and the verdict does not depend on it.
- `--time-cap-min`, a change to the prepared sets during the run, a pointer that is not ready at the start (nothing is asked) and an interrupt all end it `incomplete`.
- `supported` needs a complete run with every row met. Otherwise a finished run is `not supported`.

### The record

One JSON file, never overwritten (a second run to the same path exits 2). It holds the judge, implementation and fingerprint (with its parts), the `--env` names (never values), both SHA-256 pins, `max_excluded`, the seed, start and end times, the replay state root and snapshot, one entry per bar row (`hits`, `n`, `need`, `pass`: true, false or null for open, the bar, `not_run`, and hits per split), the excluded cases, the error and ask counts, the stop reason, the verdict, and every case with its result, exit code and seconds. Run it on a copy of the build you mean to ship: the fingerprint is the build's.

## Does a record still apply?

```sh
python3 judge_support.py --applies RECORD --ask BUILD/ask.py --judge NAME \
    --cases FILE --cases-sha256 SHA --bar FILE --bar-sha256 SHA [--env NAME=VALUE ...]
```

Free: no ask, no judge call. Exit 0 when the record is finished (the run ended complete, or stopped because the bar was out of reach) and its fingerprint, `cases_sha256` and `bar_sha256` equal today's for that build, judge, cases and bar. It then prints one line: `applies: supported` or `applies: not supported`, worked out again from the record's stored rows (each row's stored bar against its stored hits and n, with the same integer rule). The record's own `verdict` field is never read, so editing it changes nothing. A run that stopped early on an unreachable bar always prints `not supported`.

Exit 1 prints `does not apply: <reason>`: the record is unreadable or damaged, incomplete (the stop reason is named), has no fingerprint, the judge is unknown (the known ones are listed), or the fingerprint, the cases or the bar differ. A fake judge's record never applies to the default judge: the fingerprint carries which implementation ran. Exit 2 is bad input, such as a pin that does not match the file it names (the real SHA is printed) or an `--env` for a setting the tool sets itself.

A record holds for one build, one set and one bar. Changing any of them means a new run and a new record; the old record stays as it was.

A hosted model alias can change behind the same profile (see "What the fingerprint cannot see" above); a record does not see that.
