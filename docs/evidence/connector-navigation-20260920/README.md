# Connector navigation live diagnostic — 2026-09-20

This checks candidate-file navigation, not passage retrieval or answer accuracy.

## Frozen design

Six fictional local Markdown files, four answerable questions and two missing-file questions. Questions and expected source IDs were frozen before provider calls (see `frozen.json`). Each question ran against flat-files and folder-tree twice: 24 total runs, with structure order reversed for the second repeat. No cache or manual recovery was used. Reviewed descriptions and meaningful folder names were deliberately supplied.

## Results

| Layout | Expected file first | Missing questions returned no candidates | Mean CLI wall time |
|---|---:|---:|---:|
| flat-files | 8/8 | 4/4 | 0.848 s |
| folder-tree | 8/8 | 4/4 | 1.419 s |

44 successful Jev evaluations in the frozen run. A separate final smoke through the actual skill dispatcher used three more calls and ranked the rollback file first (`dispatch-smoke.json`), for 47 calls across this validation. Flat runs used one call each; tree runs used three except the two cake-recipe runs, which stopped at the first layer. Calls used one unpinned Jev evaluation, with no automatic retry. `results.json` preserves outputs, traces and per-run wall times; only the local fixture path prefix is replaced with `<fixture>`.

The CLI performed preview, confirmation and registration before navigation. Boundary tests separately exercise authorization, source changes during navigation, catalog/hash mismatches, provider failures and invalid candidate IDs/paths. Review caught and fixed ignoring a winning none choice and ignoring a supplied hash on default flat connections before completion.

## Limits

These are six distinct questions, not 24 independent examples. This is an easy, curated fixture with descriptions that identify each file well. It does not show that folder trees improve accuracy over flat navigation, solve health-brain gates, handle arbitrary data, or safely answer questions unattended. No-candidates is advisory; beam pruning can miss a real source.

## Reproduce

From the repository with Python 3.10+, Node 24+ and `TYPESAFE_API_KEY` configured, run `python3 docs/evidence/connector-navigation-20260920/run.py`. This makes live provider calls (budget 80). Unset temperature/seed overrides and set `SUPERJEV_JUDGE_RUNS=1`. Output and isolated connectors go under `.local/navigation-*`; originals and production connectors are unchanged. Create `.local` first if absent.
