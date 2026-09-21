# ci — judge-run comparison tooling

This directory holds small, dependency-free Python tools for comparing judge
runs of a bench. Runs live under `<bench>/runs/<tag>/` as `decisions.tsv`
(`case_id<TAB>verdict_text<TAB>decision`).

**Judge drift baseline.** When the same code is run twice, any case whose
decision flips between the two runs is judge drift (non-determinism), not a
real regression. `ci/drift_baseline.py <bench> <tagA> <tagB> [--out <json>]`
computes the per-bench drift set — the case ids that flipped — and writes it
as JSON (`bench`, `tags`, `drift_ids`, plus per-run counts
`truths_blocked: [a, b]` and `lies_allowed: [a, b]`: how many drifted cases
each run decided false / true, plus `only_in_a` / `only_in_b`: case ids
present in only one of the two runs). Then `ci/compare_runs.py <bench> <tagA>
<tagB> [--diff ID] [--baseline <json>]` labels each flipped case `DRIFT`
(verdict text same, decision flipped) or `WINDOW CHANGE` (verdict text
changed); with `--baseline`, a flip is reported as `KNOWN DRIFT` and excluded
from the counts only when its id is in the known-drift set AND it is a DRIFT
flip (verdict text unchanged) — a WINDOW CHANGE flip on a drift-listed case
stays counted and is labelled `WINDOW CHANGE (case is also in the drift
set)`, so a genuine window change is never hidden from the merge decision.
Decisions are normalized before comparison (case-insensitive; synonyms like
`yes`/`true` fold together), so spelling differences are not counted as
flips. Cases present in only one run are reported (count + ids), never
silently ignored. Both tools print a one-line summary and exit 0 on success,
1 on bad input (missing/malformed decisions.tsv or baseline file, reported as
a single line naming the path/row, no traceback). Tests live in `ci/tests/`
and use synthetic `decisions.tsv` fixtures in tmp dirs — no recorded fleet
payload text.

**Gated PR merge.** `ci/merge_gated.sh <pr-number> [--repo-dir <path>]` waits
for a PR's CI checks to go green (`gh pr checks`, polled, with a positive
pass gate — pending checks never merge), scrubs the PR title+body for
performance numbers with `ci/scrub_numbers.py` and aborts before merging on
a hit, then squash-merges. It never passes `gh`'s own `--delete-branch` flag
(that makes `gh` check out the base branch locally, which fails whenever
that branch is already checked out in another git worktree, even though the
merge on GitHub succeeded); instead it deletes the head branch on the
remote directly and ignores a failure there, since that's cleanup, not part
of the merge gate. It confirms the merge by re-reading `gh pr view --json
state` rather than trusting the merge command's exit code, and only
`git pull --ff-only`s `--repo-dir` when that checkout is currently on the
PR's base branch — `--repo-dir` may safely be any worktree of the repo
(where `.git` is a file, not a directory) sitting on any branch. Prints
exactly one final line, `PASS <sha>` or `FAIL: <step>`. Tests in
`ci/tests/test_merge_gated.sh` drive it against a fake `gh` for the CI/merge
scenarios, plus a real temporary git repo and `git worktree add` for the
`--repo-dir` cases, with a `git` shim that only intercepts `push`/`pull` so
no test ever touches a real remote.
