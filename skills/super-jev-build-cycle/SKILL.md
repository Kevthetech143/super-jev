---
name: super-jev-build-cycle
description: "Walk a build, fix or new skill through ten steps with Super Jev used inside each one (preflight, brief yourself, name the target, find the cause, brief helpers, check reports, prove, review, check the reply, teach back), leaving a receipt per step. Close refuses until every step has a receipt or a recorded skip. Works for any agent that can run a shell."
---

# Super Jev build cycle

One command, `build_cycle.py`, in this folder. It needs the `super-jev` skill installed next to this one (`../super-jev/`), or `--super-jev DIR`. Every call names a cycle folder and the agent:

```sh
python3 build_cycle.py --dir CYCLE_DIR --principal YOUR_AGENT STEP ...
```

`--principal` (or `SUPERJEV_PRINCIPAL`) is required and never guessed. Pick one `CYCLE_DIR` per build and reuse it for every step.

## Steps

| # | Step | Command | Super Jev use |
|---|---|---|---|
| 0 | preflight | `preflight [--project-dir DIR ...] [--skill "what the new skill does"]` | free: Super Jev reachable, every connection ready, project folders connected; names each fix. `--skill` searches for an existing skill first (reuse beats a duplicate). No receipt until ready. Uses Super Jev's own `ask.py --preflight` when present. |
| 1 | start | `start "the idea in plain words" [--project NAME]` | 4 asks: tried before? design rules? known traps? which files and tests? Open the files it lists. No strong hit = NEW GROUND: research outside first. |
| 2 | target | `target --goal "..." --evidence "lookup id / log line / user report" [--ask]` | optional ask for related past misses |
| 3 | cause | `cause --cause "..." [--file PATH ...] [--ask "question" ...] [--trace last]` | lookups on the cause; `--trace` shows a Super Jev ask's trace when the failure is an ask |
| 4 | brief | `brief` | prints a block to paste into any helper's task, so helpers start briefed |
| 5 | check-report | `check-report --report FILE [--claims FILE] [--worktree DIR] [--evidence FILE ...] [--test-cmd CMD]` | `dispatch.py verify` on the report; each line of the claims file is checked against the worktree's diff and any `--evidence` files (`dispatch.py check`, no connecting needed), or on the connected shelves when neither is given |
| 6 | prove | `prove --cmd "the test command" --output FILE [--output FILE] [--note "old vs new"]` | none; the output file must exist |
| 7 | review | `review --reviewer NAME --verdict "..." --file REVIEW_FILE [--claims FILE] [--worktree DIR] [--evidence FILE ...]` | the reviewer's key claims, checked the same way |
| 8 | reply-check | `reply-check --claims FILE [--worktree DIR] [--evidence FILE ...]` | each key fact of the draft reply (one per line), checked the same way. New code is not connected yet: pass `--worktree` or `--evidence`, or every claim about it comes back NOT FOUND |
| 9 | learn | `learn [--note FILE ...] [--fact "question" "answer" [--source PATH]]` | teach back: notes must sit in a connected folder; facts are saved with `ask.py --add` |

Then:

- `skip STEP --reason "why"`: recorded and printed at close, never silent. `start`, `check-report` and `reply-check` cannot be skipped.
- `status`: steps done / skipped / missing, and each Super Jev use with its mark.
- `mark USE_ID helped|neutral|missed [--note "..."]`: judge each Super Jev use (ids `u1`, `u2`, ... are printed in the receipts and by `status`).
- `close [--log FILE]`: exit 1 naming every missing step and unmarked use. Otherwise writes `summary.md` and appends one tab-separated line per use (time, agent, cycle, id, step, mark, what, note) to `--log` (default `CYCLE_DIR/super-jev-uses.log`).

## Receipts

`CYCLE_DIR/NN-step.md` (for example `01-start.md`): time, agent, the fields you gave, and each Super Jev command with its exit code and full output. A skip is `NN-step.skip.md` with the reason; a later real receipt replaces it. Every Super Jev call is one line in `uses.jsonl`; marks go to `marks.jsonl`.

## Rules

- A listed file is a lead, not an answer: open it before you rely on it. `possible` hits need reading.
- A non-zero exit from `ask.py` (for example a stale pointer) is kept in the receipt, not hidden; act on its printed next step.
- Each ask and each claim is a Super Jev call and may be paid; the start step always makes 4.
- The tool only calls `ask.py` and `dispatch.py`; no daemon, no other network.
