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
| learn | `learn [--note FILE ...] [--fact "question" "answer" [--source PATH]]` | Forgetting. Notes must sit in a connected folder; facts are saved with `ask.py --add`. |

`start`, `review` and `reply-check` cannot be skipped. Optional notes, never required: `target --goal ... --evidence ...`, `cause --cause ... [--ask ...] [--trace last]`, `brief` (a block to paste into a helper's task), `prove --cmd ... --output FILE`.

Then: `mark USE_ID helped|neutral|missed [--note ...]` for each Super Jev use, `status`, and `close [--log FILE]` (exit 1 naming missing steps, unmarked uses or a FIX review; else writes `summary.md` and one log line per use).

## Checking claims: shelves or direct

New code, a worker's report or a one-off file is not on Super Jev's shelves. Claims about it looked up on the shelves come back NOT FOUND. Pass `--worktree DIR` (its diff against origin/main, committed and uncommitted) or `--evidence FILE` and Super Jev judges those files directly (`dispatch.py check`, no connecting; a diff is judged as code). With neither, claims are looked up on the connected shelves.

## Rules

- A listed file is a lead, not an answer: open it before you rely on it.
- A non-zero exit from Super Jev is kept in the receipt, not hidden; act on its printed next step.
- Each ask and each claim check is a Super Jev call and may be paid.
- The tool only calls `ask.py`, `dispatch.py` and `git`; no daemon, no other network.
