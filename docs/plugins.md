# Plug-in arms and a swappable judge

super-jev's gate is a stack of independent checks over one draft and the
evidence window behind it. Until now those checks were inline calls in
`superjev.py`: adding one meant editing the gate, and swapping one out
meant editing the gate again. This is the plug-in layer that replaces
that — arms are files, modes are env vars, and the judge is behind one
interface.

Two packages:

| package | what it holds |
| --- | --- |
| `skills/super-jev/arms/` | the check arms and their registry |
| `skills/super-jev/judges/` | the judge backend behind one interface |

## Add an arm: one file, one function

Drop a module into `skills/super-jev/arms/`. There is no list to join.
The registry finds arms by listing the package directory, so the file
being there IS the registration.

```python
# skills/super-jev/arms/stale_branch.py
"""One sentence on what this arm catches, and what it deliberately does not."""
from . import Verdict

NAME = "stale_branch"          # registry key, and the env key (upper-cased)
KIND = "deterministic"         # or "judge"
DEFAULT_MODE = "block"         # or "advisory" / "off"


def check(window, draft, ctx):
    """Verdict or None."""
    if not draft:
        return None
    ...
    return Verdict(arm=NAME, decision="block",
                   reason="the one sentence a human reads and the gate blocks on",
                   explain="optional detail for a log")
```

That is the whole contract.

- **`check` returns `None` or one `Verdict`.** `None` means the arm has
  nothing to say, which is the answer it should give most of the time.
- **`reason` names the contradiction, not the check.** It is the line the
  gate prints and the line a human argues with. `explain` is optional
  detail and is never parsed.
- **An arm must not raise.** The registry catches anyway, because an arm
  bug must never be the reason a true reply cannot be sent, but an arm
  that leans on that is broken.
- **A file starting with `_` is not an arm**, so private helpers still
  work. So does a module without a callable `check`: it is skipped with
  one stderr line rather than breaking discovery.

`KIND` is documentation for now: `deterministic` means string and integer
work with no model call and no network, `judge` means it needs the judge.
Prefer `deterministic`. An arm that needs nothing but the window and the
draft, like the template arm does, is the shape to aim for.

### What an arm gets

- **`window`** is a `window_model.Window`: the evidence window as
  labelled pieces, each carrying its section, recency rank and
  provenance. Query it with `lines`, `receipts_for`, `values_labelled`,
  `newest`, `claims`, `receipts`. See `docs/window-model.md`.
- **`draft`** is the text being checked.
- **`ctx`** is a dict of whatever the caller had spare. Treat every key as
  optional. The deterministic gate path passes `evidence_text` (the
  composed window as flat bytes) and `caller`.

An arm that only reads `window` and `draft` works the same whether the
window came from a live `from_transcript` build or a recorded bench
replay through `from_text`. That is what makes an arm testable offline.

## Switch modes

Every arm is in one of three modes:

| mode | effect |
| --- | --- |
| `block` | the verdict blocks |
| `advisory` | the verdict is reported, never blocks |
| `off` | the arm does not run at all |

Three places set it, in this order of precedence:

```sh
# 1. env, per arm — SUPERJEV_ARM_<NAME> with NAME upper-cased
SUPERJEV_ARM_PR_STATE=advisory

# 2. a JSON config file
SUPERJEV_ARMS_CONFIG=~/.config/superjev/arms.json
```

```json
{ "arms": { "pr_state": "advisory", "stale_branch": "off" } }
```

A bare `{"pr_state": "advisory"}` map works too. Failing both, the arm's
own `DEFAULT_MODE` applies.

An unrecognised mode value falls back to the arm's default and prints one
line to stderr saying so — a typo must not silently turn a block into
nothing, and must not spam a hook's stderr either, so it is exactly one
line per bad value per process. A missing or malformed config file is one
warning and no config, never an exception.

### The gate switch

```sh
SUPERJEV_ARMS=1
```

Off by default. With it off, nothing in the `arms` package is consulted
and the gate runs its legacy inline checks exactly as it did before the
package existed. With it on, the gate runs its migrated arms through the
registry and skips the legacy inline call for those arms — it does not
run both, because running both would hide exactly the difference the
switch exists to expose.

Anything in `1/true/yes/on` (case-insensitive) is on.

## The template arm

`arms/pr_state.py` is the one arm migrated end-to-end, and it is the
shape to copy. It checks a draft that says "PR #N merged" against every
PR-state signal the window carries for that PR, and it holds no logic of
its own: it asks `window_model.pr_state_verdict_from_window` and wraps
the answer in a `Verdict`.

The migration is deliberately provable rather than asserted. The same
check now exists twice — `_pr_mismatch_reason` in `superjev.py` (legacy,
scanning raw evidence text) and the arm (asking the window model) — and
`SUPERJEV_ARMS` picks between them. Both offline replays,
`tests/replay_gate_bench.py` and `tests/replay_fact_block_sweep.py`, are
run with the switch off and on and must print the same decisions. That is
why one arm was migrated first instead of all of them: the switch is the
proof harness, and the next arm gets to use it.

When every arm has moved, the legacy call and the switch both go.

## Swap the judge

The deterministic arms are work we own. The judge is not: it is a model
call to TypeSafe Jev, and it is the part of the harness we would most
like to be able to replace — for a test that must not touch the network,
for a second opinion, for a local model later.

```python
from judges import get_judge

judge = get_judge()                       # or get_judge("fake")
result = judge.classify(draft_text, window)
if result.rejected:
    ...
```

`classify` takes the draft and either a `Window` or the window text, and
returns a `JudgeResult`:

| field | meaning |
| --- | --- |
| `code` | the door's own exit code: 0 clean, 2 reject, 3 read |
| `backend` | who answered, so a log can say so |
| `stdout` / `stderr` | the door's output, unchanged |

`code` keeps its existing meaning on purpose. The hook's
block-or-advisory branch is built on it, and this seam is not the place
to redefine it.

Two backends ship:

- **`typesafe`** (default) wraps today's call exactly. It hands the
  window and the draft to `superjev.cmd_gate`, so the ledger row, the
  cap-and-truncate pass, the timeout budget and the exit codes are the
  existing ones. Adding this file changed no behaviour.
- **`fake`** runs `SUPERJEV_GATE_CMD` directly, the way the existing
  self-mocking tests already do, and never falls back to the fleet
  `jev.py`. A test can therefore assert that no live call happened rather
  than hoping. With the var unset it reports itself unavailable through
  the door's own refusal code.

```sh
SUPERJEV_JUDGE=fake
SUPERJEV_GATE_CMD="python3 skills/super-jev/tests/fake_door.py"
```

An unrecognised `SUPERJEV_JUDGE` value falls back to `typesafe` with one
stderr line — a typo must not quietly leave a draft unjudged.

To add a third backend, subclass `judges.Judge`, implement `classify`
(and `available` if it can be missing on a given machine), and add it to
`judges.BACKENDS`. Every backend returns the same `JudgeResult`, so
nothing downstream needs to know which one answered.

## Tests

```sh
python3 -m pytest skills/super-jev/tests/test_arms.py -q
python3 -m pytest skills/super-jev/tests/test_judges.py -q
```

Both are offline. `test_arms.py` proves discovery by writing a throwaway
arm into the package directory and taking it away again, because "the
registry lists the directory" is the claim and a test that imported a
hard-coded name would not test it. `test_judges.py` runs the `fake`
backend through a real subprocess and only ever exercises `typesafe`
with `cmd_gate` monkeypatched, so no test makes a live TypeSafe call.
