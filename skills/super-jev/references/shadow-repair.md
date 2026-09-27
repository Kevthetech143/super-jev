# Shadow repair comparisons

`shadow_repair.py` proposes and evaluates an isolated repair. It never merges, releases, refreshes a connector or edits a source note. Build code is trusted local Python, not sandboxed input. Keep private artifacts outside the repository and outside connected source folders.

## Capture once, before tuning

Provide a target JSON object with `question`, `gold` (absolute paths) and `principal`, and explicit JSONL controls. The target becomes tuned; controls retain their scorecard splits. Supply independently reserved held-out cases using the scorecard reservation file and its previously pinned checksum. No reserved set means no held-out evidence, never a pass.

```sh
python3 shadow_repair.py --case target.json --controls controls.jsonl --baseline old/ask.py --candidate old/ask.py --cache /path/to/prepare-cache --held-out reserved.json --held-out-sha256 PIN --out /private/run/baseline
```

Omit the two held-out flags when none exists; the report must then reject acceptance. Capture writes a new sibling `baseline-snapshot` directory. It freezes requests, principal-scoped pointer metadata, reviewed file entries, permitted source bytes, connector names/admission decisions, and baseline Python dependencies. It records missing/excluded sources instead of reading vault or credential paths. The bundled secret-scan patterns are code configuration, not credentials. Bounds: 32 cases, 2,000 reviewed source paths, 64 MiB of source bytes, and the existing per-file size ceiling. At least one control is required. No paid call is made.

## Propose and compare

Prepare the smallest candidate code patch in a separate worktree. For a missing/stale note, write a proposal for the owner rather than changing the source. The runner classifies only what it can measure: a missing/unavailable reviewed source, mismatched reviewed/current bytes, or a free read-slot miss. If word search succeeds, it reports routing/content as unresolved; it does not invent a routing diagnosis without paid-stage evidence.

Replay with the original snapshot and its printed checksum:

```sh
python3 shadow_repair.py --snapshot /private/run/baseline-snapshot --snapshot-sha256 PIN --candidate candidate/ask.py --out /private/run/candidate
```

Each result directory contains `requests.jsonl`, `scorecard.json`, `candidate.patch`, `proposal.md`, `report.json` and a copied candidate build. Existing result directories are refused. The snapshot and its source/code hashes are verified before replay. Source reads use frozen bytes at the original logical paths; current notes are neither edited nor used as changing comparison inputs. The report records the runner and scorecard code hashes as well as build hashes. Retain that tool version and the snapshot to reproduce results.

## Fixed decision rules

The target must move from outside the actual word-search read slots to inside them. A numeric rank movement alone is insufficient. Controls and retrospective cases that were readable must stay readable; every reserved held-out case must preserve that property. A changed read-slot budget is explicit in the report and requires paid-stage cost/quality measurement before promotion. Nested scorecard counts use the baseline read budget; `target_readable` and `read_slots` separately show the actual per-build read limits, so wider reading is not reported as better ranking. Scorecard split validation still applies; gains do not cancel losses.

An ACCEPT is only a shadow recommendation, never deployment authority. Independent held-out evidence and the separately reviewed paid-stage check are required. Until that check is integrated and its evidence bound to this snapshot/build pair, the offline runner returns REJECT with the explicit missing-evidence reason, even when word-search improves. The runner does not treat an arbitrary external “passed” flag as paid evidence. Routing, content truth and model uncertainty remain outside an offline word-search success.

Exit 0 means shadow acceptance, 1 means a completed rejection report, and 2 means invalid input or capture/replay failure. A rejected repair is a valid result; it does not establish that the source note is wrong or that the search system cannot improve.
