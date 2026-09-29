---
name: super-jev-build-cycle
description: "Walk a build, fix or new skill through six steps with Super Jev inside each one: preflight, start (what we already know), check helpers' reports, an independent review by a fresh agent, check your reply, teach back. One receipt per step; close refuses until the required steps are done and the latest review says SHIP. Works for any agent that can run a shell."
---

# Super Jev build cycle (v1, stable)

One command, `build_cycle.py`, in this folder. It needs the `super-jev` skill installed next to this one (`../super-jev/`), or `--super-jev DIR`. Every call names a cycle folder and the agent:

```sh
python3 build_cycle.py --dir CYCLE_DIR --principal YOUR_AGENT STEP ...
```

`--principal` (or `SUPERJEV_PRINCIPAL`) is required and never guessed. Use one `CYCLE_DIR` per build.

## The six steps

| Step | Command | What it catches |
|---|---|---|
| preflight | `preflight [--project-dir DIR ...] [--skill "what the new skill does"]` | Free. Super Jev reachable, connections ready, the project folder connected; `--skill` finds an existing skill first. No receipt until ready. |
| start | `start "the idea in plain words" [--topic "the subject"] [--project NAME] [--ask "a sharper question" ...]` | One ask, "what have we already tried, decided or learned about <topic>", plus your own. The topic is the subject in at most 12 words, as you'd name it when searching your notes (e.g. "secret scan holding files over numbers read as card numbers"); a longer question finds fewer notes. An idea of 12 words or fewer is its own topic; a longer one needs `--topic`, or start stops before asking. Open what it lists and mark each use. No strong hit = NEW GROUND: research outside first. |
| check-report | `check-report --report FILE [--claims FILE] [--worktree DIR] [--evidence FILE ...] [--test-cmd CMD]` | A helper's wrong claims. `dispatch.py verify` on the report; each claim line is checked against the worktree's diff and `--evidence` files directly. Skip with a reason when no helper worked. |
| review | `review-brief --worktree DIR [--test-cmd CMD]`, then `review --reviewer NAME --verdict "SHIP ..."/"FIX ..." --file ANSWER [--claims FILE] [--worktree DIR]` | What the builder can't see. `review-brief` writes a self-contained brief (goal, diff, tests, what to try to break). Give it to a NEW agent with no history of this work; record its answer. The reviewer must not be the builder. Close refuses while the latest review says FIX. |
| reply-check | `reply-check --claims FILE [--worktree DIR] [--evidence FILE ...]` | A wrong fact reaching your user. One key fact of your draft reply per line. |
| learn | `learn [--note FILE ...] [--fact "question" "answer" [--source PATH]]` | Forgetting. Notes must sit in a connected folder; facts are saved with `ask.py --add` (same secret scan and claim check as every save path; kept until the source file changes or `--miss` marks it wrong). |

`start`, `review` and `reply-check` cannot be skipped. Optional notes, never required: `target --goal ... --evidence ...`, `cause --cause ... [--ask ...] [--trace last]`, `brief` (a block to paste into a helper's task), `prove --cmd ... --output FILE`.

Then: `mark USE_ID helped|neutral|missed [--note ...]` for each Super Jev use, `status`, and `close [--log FILE]` (exit 1 naming missing steps, unmarked uses or a FIX review; else writes `summary.md` and one log line per use).

## Earn the Button (any new feature or new file type)

Nothing new gets a button until it earns one. Seven steps, in order, each leaves a file in the cycle dir (`onboard-N-name.md`). A step cannot run before the one above it, and an earlier step cannot be redone once a later one exists (start a new cycle dir). `onboard check` re-checks all seven against the real files, and `close` refuses while an onboarding is started but not earned.

| # | Command | Must show |
|---|---|---|
| 1 | `onboard need --who --how-often --fails` | Real use, at least 3 real words each (no "-", "x", "n/a"). No evidence = stop. |
| 2 | `onboard simplest --answer "..."` | Could an existing rule be widened instead of a new path? Written answer, 5+ words. |
| 3 | `onboard promise --line "accepts ...; returns ...; failure is reported as ..." --approved 'Kelvin approved: 2026-09-29'` | `--approved` must say "Kelvin approved" plus a date (YYYY-MM-DD, not in the future) or a quoted phrase. The line goes into `/Users/admin/agents/primary-brain/superjev-source-contract.md` and must stay there as a whole line. |
| 4 | `onboard frozen --dev FILE --heldout FILE` | Separate JSONL files, each with `supported`, `absent` (not found) and `invalid` (failure) cases. sha256 recorded before build; a later edit fails `check`. |
| 5 | `onboard build --worktree DIR --branch NAME --deletes "what it removes, or none: why"` | Own worktree on a feature branch (not main). The reviewer's own answer file starts with SHIP (the typed `--verdict` is not used), is newer than the frozen step, and names the branch or worktree. |
| 6 | `onboard prove --cases10-file F --eval60-before-file F --eval60-file F --test-cmd CMD [--cases10-ids F]` | 10 of 10 frozen questions pass, eval60 not lower (before and after are different files; after is newer than the build step), the feature's own frozen test passes. |
| 7 | `onboard record --timeline-row T --timeline-file F --card-now L --card-file F` | The timeline row and card NOW line are present in those files, and `check` looks again each time. |

What is checked and what is self-reported:
- Checked by the tool: step order, file contents and sha256 (frozen cases, result files, contract line), the branch is not main, the review receipt, the pass counts (it counts lines in the result files: a line passes when the field right after `qNN` (and up to two label columns) is `rank 1` to `rank 5`, "not-found OK" or "absent-OK"; text after "|" never counts; ids must be distinct; cases10 must be exactly the ids in `superjev-tests/try248/cases10.jsonl`; eval60 has 60 lines), the test command runs and exits 0, and it must use the frozen dev or held-out file path.
- Self-reported: who asked and how often, the simplest-rule answer, what the build deletes, and the "Kelvin approved" wording (the tool checks its shape, not that Kelvin said it). The result files themselves are only as honest as whoever produced them: run the tests and score them yourself, do not type numbers into a file.

## Checking claims: shelves or direct

New code, a worker's report or a one-off file is not on Super Jev's shelves. Claims about it looked up on the shelves come back NOT FOUND. Pass `--worktree DIR` (its diff against origin/main, committed and uncommitted) or `--evidence FILE` and Super Jev judges those files directly (`dispatch.py check`, no connecting; a diff is judged as code). With neither, claims are looked up on the connected shelves.

Secret-shaped text never blocks the check: a file of the diff that holds any (a test's fake card number or sample key, or a real one) is withheld whole and named, and the rest is judged; a plain `--evidence` file that holds any is dropped. A claim about a withheld file cannot be checked, and a claim that itself holds a secret is refused.

## Rules

- A listed file is a lead, not an answer: open it before you rely on it.
- A non-zero exit from Super Jev is kept in the receipt, not hidden; act on its printed next step.
- Each ask and each claim check is a Super Jev call and may be paid.
- The tool only calls `ask.py`, `dispatch.py` and `git`; no daemon, no other network.
- Step back after two FIX reviews on the same change. Before the next round, write in the cycle dir why each fix failed, and ask whether the design itself is wrong. Prefer a simpler rule that closes the whole class of failure (fail safe, no keyword lists) over another patch.
- Marks are honest grades of Super Jev's help: `helped` only when what it returned or checked changed your work; a fact saved in `learn` is `neutral`; a tool crash (missing key, timeout) is `neutral` with a note naming the fault, not `missed`. `missed` means the answer existed and Super Jev did not find it.
- Before a PR merges, update its description to the final state: review rounds, what changed after the PR opened, and any known gap.
