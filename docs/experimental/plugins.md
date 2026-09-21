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

NAME = "stale_branch"          # MUST equal the file stem; also the env key
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


def contribute(window, draft, ctx):        # OPTIONAL — see "Contribute evidence"
    """Extra window lines, in front of the judge. list[str] or []."""
    return []
```

That is the whole contract.

- **`check` returns `None` or one `Verdict`.** `None` means the arm has
  nothing to say, which is the answer it should give most of the time.
- **`reason` names the contradiction, not the check.** It is the line the
  gate prints and the line a human argues with. `explain` is optional
  detail and is never parsed.
- **An arm must not raise.** The registry catches anyway, because an arm
  bug must never be the reason a true reply cannot be sent, but an arm
  that leans on that is broken. When one does, it is one stderr line and
  one entry in the run's `errors` — a raising blocking arm fails open, but
  it is not invisible.
- **`NAME` must be the file stem.** An arm whose `NAME` disagrees with its
  own filename is rejected with one stderr line and skipped. Names and
  stems are one keyspace, so the name in `SUPERJEV_ARM_<NAME>`, the key in
  the config file, the argument to `load_arms(names=...)` and the file on
  disk are always the same word.
- **A file starting with `_` is not an arm**, so private helpers still
  work. So does a module without a callable `check`: it is skipped with
  one stderr line rather than breaking discovery.
- **Keep module-level imports cheap.** Every hook event imports every arm
  in the package, whether it fires or not, so a top-level import of
  something slow is paid on every turn. Import the expensive thing inside
  `check`. For the same reason an arm reaches the skill directory with
  `sys.path.append`, never `insert(0)`: an arm must not reorder the
  importing process's own search path.

`KIND` does one thing today, and it is the gate-level failsafe: a block
every one of whose blocking verdicts came from a `judge`-kind arm can be
demoted (see "Two config surfaces"). Otherwise it is documentation —
`deterministic` means string and integer work with no model call and no
network, `judge` means it needs `ctx["judge"]`. Prefer `deterministic`.
An arm that needs nothing but the window and the draft, like the template
arm does, is the shape to aim for.

### What an arm gets

- **`window`** is a `window_model.Window`: the evidence window as
  labelled pieces, each carrying its section, recency rank and
  provenance. Query it with `lines`, `receipts_for`, `values_labelled`,
  `newest`, `claims`, `receipts`. See `docs/window-model.md`.
- **`draft`** is the text being checked.
- **`ctx`** is the named, growing contract below.

An arm that only reads `window` and `draft` works the same whether the
window came from a live `from_transcript` build or a recorded bench
replay through `from_text`. That is what makes an arm testable offline.

### The `ctx` contract

`ctx` is a **named, growing contract**, not a grab bag: every key here is
documented, and **every key is optional**. An arm reads a key with
`ctx.get(...)` and copes with it being absent, because the caller decides
what it has to give and a bench replay has less than a live gate.

| key | type | who sets it | what it is |
| --- | --- | --- | --- |
| `judge` | `() -> JudgeResult` | the registry, check phase only | the ONE judge call for this run, memoised. Absent during the evidence phase. |
| `judge_window` | `Window` or `JudgeEvidence` | the registry, check phase only | exactly the evidence the judge was handed, window plus any contributed lines. |
| `contributed_lines` | `tuple[str]` | the registry, check phase only | the lines `contribute` put in front of the judge this run. |
| `evidence_text` | `str` | the deterministic gate path | the composed window as flat bytes, for an arm that wants the text rather than the pieces. |
| `caller` | `str` | the deterministic gate path | which path asked — today only `"deterministic"`. |

**Adding a key.** Three rules, and they are the whole process:

1. Add the row to this table, with who sets it, in the same change that
   sets it. A key that is set and not documented is not in the contract.
2. Make it optional in fact, not just on paper — no arm may require it,
   and a caller that has nothing to put there passes nothing.
3. Never overwrite a caller's own key. The registry fills `judge`,
   `judge_window` and `contributed_lines` with `setdefault` semantics and
   copies the dict it was handed, so a caller's `ctx` is never mutated.

## Contribute evidence: an arm that feeds the judge

Some checks cannot rule on a draft from what the window already says.
A fact family may need to put a DERIVED line in front of the judge — one
the composer never wrote — and then let the judge weigh it. That is the
second, optional function:

```python
def contribute(window, draft, ctx):
    """Extra window lines for THIS run. list[str], or [] for nothing."""
    return ["DERIVED: the draft names branch X; the window's receipts are all branch Y"]
```

`run_arms` runs in two phases, in this order:

1. **The evidence phase.** Every arm's `contribute` is called. The lines
   are collected in arm-name order and appended to the evidence as one
   `[contributed by check arms]` section. `ctx` carries **no `judge` key**
   in this phase, on purpose: an arm cannot both feed the judge and read
   its answer.
2. **The check phase.** Every arm's `check` is called, and `ctx["judge"]`
   is now live — so the judge, if it is asked at all, is asked over the
   window *including* the contributed lines.

The underlying `Window` is never mutated. A contribution is extra
evidence for this run, wrapped in a `JudgeEvidence` that renders the
window and then the block; the window's own identity (and the round-trip
property `from_text(render(w)) == w`) is untouched.

An arm that only contributes still needs a `check` — return `None` from
it. An arm that only checks needs no `contribute` at all.

### A contributed line is never evidence

**A contributed line carries no trust and no receipt strength, ever.** It
is a check arm's own assertion about this run, not something received from
anywhere, so nothing it says can be read as a tool result.

That is enforced structurally rather than by anyone remembering to. The
block is a first-class window section, `window_model.SECTION_CONTRIBUTED`:

- **one header, one definition.** `window_model.HEADER_CONTRIBUTED` is
  the string, and `arms.CONTRIBUTED_HEADER` is an alias of it. The
  renderer that writes the line and the reader that recognises it cannot
  disagree about its bytes.
- **its own emit slot, the highest of any section.** `emit_slot` ranks it
  above every other section's slot, which means it renders LAST — after
  `[current turn]`, not before it — matching where `JudgeEvidence.render`
  actually writes it (appended once the rest of the window is already
  rendered). The `===` before it is a boundary `from_text` accepts.
- **its own piece origin, `check_arm`.** Not in
  `window_model.TRUSTED_ORIGINS`, so the single trust rule answers no for
  the ordinary reason rather than by a special case. `pr_state_signals_
  from_window` reads `ln.trusted`, so a contributed PR-state line degrades
  to prose at strength 0 with no extra plumbing.
- **prose to every deterministic reader.** The count reader, the
  labelled-value table, the receipt half of the merge/CI and stale-report
  families, and the not-merged claim scan all skip the block. An arm's own
  `**61 passed**` never enters the count table.
- **neutralised line by line.** `window_model.neutralise_contributed_line`
  strips any `[from: cmd @ cwd]` identity tail and prefixes the line with
  `> `, so no single line passes for a receipt row on its own shape
  either.
- **no ordering.** `recency_rank` returns `None` for the section. A
  contributed block is not a turn, so it has no recency to weigh against a
  real receipt.

Why all of that for one block: before it was a section, the header was one
the reader did not recognise, so the `===` before it was not a boundary
and `_section_chunks` folded the whole block into the chunk above it. When
that chunk was `[session receipts]`, every contributed line — and any
`[from: ...]` tail forged onto one — re-parsed as a **trusted session
receipt**. Arm text laundered into a receipt by nothing but its position.

The invariant is a test: after `render()` and `from_text`, no contributed
line has trust. That is the renderer and the reader, and between the two
of them the pair is airtight — but a window this big does not go to the
judge as rendered; `superjev.trim_window_to_token_budget` cuts it to the
gate's token budget first, and a trimmer that does not know the
contributed header is a third way to lose the guarantee, not covered by
either half above. `_WINDOW_PART_RE`/`_WINDOW_TRIM_ORDER` register the
header as its own section, evicted FIRST and always WHOLE — header
included, never partially shrunk — specifically because a partial cut
that kept the block's tail but sliced its header off would hand
`from_text` unlabelled bytes, which a downstream reader can fold into
whatever trusted section sits above them. And in the one case the
section boundary cannot be proved regardless — a window carrying an
unbounded report body makes every later `===` un-provable — the block is
absorbed into that report's *claim*. Untrusted either way. There is no
arrangement of these bytes that gains trust.

## One judge call per run

`ctx["judge"]()` returns a `judges.JudgeResult`. The **first** call
performs the single TypeSafe classify over the draft and the (possibly
contributed-to) evidence; every later call in the same run returns that
same object.

```python
NAME = "overclaims"
KIND = "judge"

def check(window, draft, ctx):
    result = ctx["judge"]()          # one call, however many arms ask
    if result.clean:
        return None
    return Verdict(arm=NAME, decision="block", reason="...")
```

- **Zero arms with `KIND = "judge"` -> zero judge calls.** Nothing asks,
  so nothing is classified, and the `judges` package is not even
  imported.
- **N arms with `KIND = "judge"` -> exactly one judge call.** All N
  interpret the same `JudgeResult` in their own `check`, each in its own
  way, and the model is paid for once.

An arm that needs nothing from the judge must not touch `ctx["judge"]`.

## Two config surfaces, and they are different objects

There are exactly two places behaviour is configured, and confusing them
is the mistake this section exists to prevent.

| surface | scope | what it is |
| --- | --- | --- |
| **per-arm mode** — `SUPERJEV_ARM_<NAME>`, config file, `DEFAULT_MODE` | one arm, every run | that arm's standing. An `advisory` arm never blocks in the first place; an `off` arm does not run. |
| **the gate failsafe** — `SUPERJEV_GATE_JUDGE_ADVISORY` | one gate call, after the fact | that call's OUTCOME being demoted, over whatever mix of arms happened to fire. |

A mode is read on every run and belongs to the arm. The failsafe is read
once per gate call and belongs to the gate. Setting one never changes the
other.

### Per-arm mode

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

### The gate failsafe

One rule, stated in registry terms:

> **Demote the block if every blocking verdict came from an arm whose
> `KIND` is `judge`.**

That rule lives in `arms.judge_only_blocks(verdicts, kinds, weak_verdicts=None)`
and nowhere else. The gate's own reasons have not all moved to the registry yet, so
`superjev._gate_blocking_verdicts` adapts this call's reason lists into
verdicts and kinds and hands them to that one function — the gate never
names which judge arm fired, which is why the rule covers the v2 and v3
judge arms alike. A block carrying even one deterministic verdict (a
count mismatch, a PR-state mismatch, a `CONTRADICTED_BY_FACT` fact
sentence) is untouched and still blocks with today's exit code.

Two edges, both deliberate:

- a block with **no reason line at all** came from the judge's own exit
  code, so it counts as a judge verdict — otherwise it would look like it
  came from nobody;
- an arm **missing from the kinds map** is treated as deterministic, so a
  forgotten arm fails CLOSED and its block is never demoted.

```sh
SUPERJEV_GATE_JUDGE_ADVISORY=1        # the whole failsafe: demote it
SUPERJEV_GATE_JUDGE_ADVISORY=weak     # only the weak judge verdicts
```

`weak` is the same rule with a **filter**, not a second rule. The gate
passes `weak_verdicts` — the verdicts a weak demotion covers
(NOT_SUPPORTED, CONTRADICTED, SELF_CONTRADICTORY, never OVERCLAIMS) — and
`judge_only_blocks` then demotes only when every blocking verdict is both
judge-kind *and* named in that set. A blocking judge verdict outside it
keeps its block, so an OVERCLAIMS finding still stops the turn while the
per-claim arm no longer does.

The filter reads each verdict's own `verdict` attribute, which
`_gate_blocking_verdicts` fills in from the reason line's
`key VERDICT score` shape. A verdict that has none — a reason line in a
shape the gate cannot parse, or the exit-code block above — is **not**
demotable under a filter. Fail closed: a finding that cannot say which
verdict it is must not be demoted by a rule that selects on verdicts.

See `docs/wire-into-claude-code.md`, "Judge-advisory mode", for the operator's view of
both levels.

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

The same check now exists twice — `_pr_mismatch_reason` in `superjev.py`
(legacy, scanning raw evidence text) and the arm (asking the window
model) — and `SUPERJEV_ARMS` picks between them. Both offline replays,
`tests/replay_gate_bench.py` and `tests/replay_fact_block_sweep.py`, are
run with the switch off and on and must print the same decisions.

**What that proves, and what it does not.** Be clear about the limit: the
recorded replay cases never fire the PR-state arm. `replay_gate_bench.py`
consults it on every case and it returns nothing on all of them, and
`replay_fact_block_sweep.py` does not reach the PR arm at all — it only
asks whether a window carries a `CONTRADICTED_BY_FACT` sentence. So the
replays prove that turning the switch on changes nothing else in the
gate, which is worth proving and is all they prove. That the migrated arm
decides the same way as its legacy twin rests on the unit tests in
`tests/test_arms.py`, which put a contradicting window in front of both
and compare the reason strings.

That is why one arm was migrated first instead of all of them: the switch
is the proof harness, and the next arm gets to use it.

When every arm has moved, the legacy call and the switch both go.

## Where arms are found

`arm_names()` lists a search path, and the file being there IS the
registration:

1. `skills/super-jev/arms/` — the shipped package;
2. every directory named by `SUPERJEV_ARMS_EXTRA_DIR` (os.pathsep
   separated), or passed as `extra=` to `arm_names` / `load_arm` /
   `load_arms` / `run_arms`.

The package directory wins a stem collision, so a shipped arm cannot be
shadowed by a loose file. An arm in an extra directory is still imported
as `arms.<stem>`, so `from . import Verdict` works in it exactly as in a
shipped arm.

The extra directory exists so a **test double is never written into the
shipped package**. `test_arms.py` writes its throwaway arms into a
`tmp_path` and points the registry at it: a test run cannot leave an arm
behind in a live checkout. A private local arm can use the same door.

## What the gate records

`run_arms` returns `(verdicts, run)`, where `run` is an `ArmRun`. The gate
used to throw that away. Two of its fields now reach the catch ledger, as
two separate fields on purpose:

| ledger field | from | shape |
| --- | --- | --- |
| `arms` | `run.consulted` | `["pr_state:block", ...]` — every arm consulted, with the mode it ran in |
| `arm_errors` | `run.errors` | `["pr_state:RuntimeError", ...]` — every arm that raised, with the exception class |

"We asked this arm" and "this arm broke" are different facts, and a row
that merged them could not tell a quiet arm from a crashed one. A raising
blocking arm still fails open — the point of the second field is that it
stops being invisible when it does.

With the switch off, the legacy inline twin is recorded as
`pr_state:legacy-inline`, so the row says what was consulted either way
rather than reading as "no arms ran". Both fields are `null` on a row
that consulted nothing (the verify door, an unchecked gate row), which is
not the same as `[]`.

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

Both are offline. `test_arms.py` proves discovery by writing throwaway
arms into a temp directory and pointing `SUPERJEV_ARMS_EXTRA_DIR` at it,
because "the registry lists its search path" is the claim and a test that
imported a hard-coded name would not test it. Nothing is written into
`arms/`. The one-judge-call claim is proved by counting: a subclass of the
shipped `fake` backend counts its own `classify` calls, and two judge arms
in one run produce exactly one call and the same `JudgeResult` object.
`test_judges.py` runs the `fake` backend through a real subprocess and
only ever exercises `typesafe` with `cmd_gate` monkeypatched, so no test
makes a live TypeSafe call.
