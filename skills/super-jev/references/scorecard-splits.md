# Scorecard splits and frozen reservations

The scorecard grades free word search only. It reports `tuned`, `held-out` and `retrospective` separately in text and JSON (`splits`). Untagged and legacy `harvested` cases are retrospective. Unrecognized old labels also count as retrospective, with a text notice and `original_split` in JSON; they never establish held-out evidence. Any baseline-readable case lost by any compared build exits 1; a tuned gain cannot cancel a held-out loss. Explicit cases with empty gold lists cannot be graded by word search: they are excluded and counted separately (`excluded_no_gold`), never counted as passes. They cannot enter a frozen reservation. Invalid inputs exit 2. The existing aggregate gained/lost line remains available.

## Reserve before tuning

Have the non-builder select new cases before inspecting or tuning against their results. Already-used cases stay retrospective or tuned. Put all known paraphrases under the same `group`, and related source files under the same `source_family`; assign each complete family to one split. Exact normalized questions and resolved gold paths are also checked for cross-split overlap, including across the selected principals. The tool cannot discover semantic paraphrases or prove that a person has never seen a case.

A JSONL case (paths are illustrative):

```json
{"question":"Which plan was chosen?","gold":["/notes/decision.md"],"principal":"reader","split":"held-out","group":"plan-choice","source_family":"project-decisions"}
```

Reserve from the complete proposed case pool, including tuned/retrospective rows so known overlaps can be rejected:

```sh
python3 scorecard.py --principal reader --cases proposed.jsonl --freeze-held-out reserved.json
```

This writes only the held-out cases to a new manifest; it refuses to overwrite a file and makes no ask/provider call. Both group fields are required for reserved cases. Record its printed SHA-256 in the review plan or another independently retained record BEFORE tuning. Do not recompute the expected checksum after modifying the file.

Compare builds with that pinned value:

```sh
python3 scorecard.py --principal reader --no-harvest --cases tuned-and-past.jsonl --held-out reserved.json --held-out-sha256 PINNED_SHA256 --ask old/ask.py --ask new/ask.py --cache /path/to/prepare-cache
```

Include every reserved principal with repeated `--principal`. The checksum is checked before loading builds. Additional held-out inputs must match the reservation, and all selected inputs are checked for cross-split overlap. `--no-harvest` makes the pool explicit; otherwise saved feedback is included and overlapping retrospective feedback can correctly invalidate the holdout. A tagged held-out set without a frozen manifest is still reported and its losses still fail, but output says its checksum was not supplied (`held_out_verified: false`).

A checksum detects changed case bytes against the supplied pin; it is not access control, a trusted timestamp, or proof of unseen data. It does not freeze source files, cache contents or build code: preserve those separately for reproducible comparisons. Once a held-out result guides a repair, retire that reservation, move the exposed family to tuned, and independently reserve a new family before the next round. Keep the old manifest and pin as history; do not rewrite them. An empty held-out split provides no held-out evidence.
