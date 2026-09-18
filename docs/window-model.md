# The structural window model (2026-09-18)

`skills/super-jev/window_model.py` is the gate's evidence window as
labelled **pieces** instead of one flat blob of text. This note says why
it exists, what the one trust rule is, how each of the six existing
readers migrates onto it, and what the fuzz actually proves.

Nothing is wired to it yet. That is deliberate: PR #53 is open over the
very functions a migration would touch. This lands the model, the
renderer, the proof of byte-identity and one read-only proof-of-concept
reader, so the wiring is a separate, boring change per reader.

## Why

`_derive_evidence_text_from_transcript` composes one text window out of
four layers: the current turn's tool results, the previous turns' tool
results, the session receipts, and this turn's relayed worker/teammate
reports. Then six independent readers parse that text again:

1. the PR-state arm, `_pr_state_signals` / `_pr_mismatch_verdict`
2. the labelled-count arm, `_extract_labelled_evidence_counts_scoped`
3. the derived-fact families — written file, result table, merge receipt,
   labelled value, score list, extremum
4. the stale-report family, `_report_not_merged_claims`
5. `_fact_window_lines`
6. the judge prompt

Every one of them has to answer the same two questions — *which section
is this line in*, and *did a tool say this or did a worker say this* —
and every one of them answers by pattern-matching text that a worker
partly chooses. A worker writes their own report body, and on `main`
their own `teammate_id` goes straight into the label line.

PR #53 spent eight review rounds closing that gap one reader at a time.
The holes it closed, in order: a body line reading `[current turn]`; a
body line reading `---`; a forged `END REPORT FROM` fence; a
`teammate_id` with an embedded newline that split the label across two
physical lines; a blank `teammate_id` that rendered a label the matcher
itself rejected; a receipt-shaped `teammate_id` that put `MERGED PR #52`
on the label line; a truncation that cut a fence in half; and a
fail-closed loose-opener rule so broad that a tool result which merely
*printed* a `REPORT FROM` line demoted its own later lines to prose.

Each fix was correct. Together they are a grammar being defended, by
hand, in six places, against an input that gets a vote on the grammar.

## The fix: parse once, into pieces

A `Piece` carries its own provenance:

| field | what it is |
| --- | --- |
| `kind` | `receipt`, `claim`, `fact`, `header`, `draft` |
| `section` | `current`, `prev(n)`, `receipts`, `reports`, `unknown` |
| `turn_rank` | the section's recency rank, `None` when unknown |
| `source` | the `cmd @ cwd` that produced a receipt, or the teammate id that wrote a claim |
| `text` | the piece's own bytes, no header, no glue |
| `line_span` | its `(first, last)` line numbers in the rendered window |
| `trusted` | `is_trusted(origin)` — never set by hand |
| `origin` | the one input to the trust rule |

A worker cannot forge a piece boundary, because the boundary is the
transcript record the text was lifted out of, not a line inside the text.

`draft` is in the kind vocabulary and no constructor emits one: the draft
is not part of the window. The kind is reserved so the judge-prompt
reader, which is handed the draft and the window together, joins this
vocabulary rather than inventing a second one.

## The trust rule, in one place

```python
def is_trusted(origin):
    return origin in TRUSTED_ORIGINS   # {"tool_result", "session_receipt"}
```

That is the whole rule. A piece is trusted iff its text came out of a
tool result record — or out of a session receipt, which is a cache of an
earlier turn's tool result and nothing else. Never from a teammate
message, never from assistant text, regardless of wording.

Two consequences, which are the two halves of the boundary PR #53 kept
re-litigating:

- **A report that quotes a receipt is still a claim.** A body containing
  `{"number": 52, "state": "MERGED"}`, a bare `MERGED`, a
  `"mergedAt"` field or a `[from: gh pr merge 52 @ ...]` suffix produces
  exactly one piece, of kind `claim`, untrusted. Quoting a receipt is not
  receiving one.
- **A tool result that prints a report is still a receipt.** `cat
  report.md`, a transcript dump, a grep hit. This is the round-6 lesson:
  demoting a tool result because its *output* contains a label line is
  how a genuine `{"state": "OPEN"}` in that same output lost its
  strength and let a bare `gh pr merge N` invocation win the arm.

Composer structure (`kind == "header"`) is trusted **as structure** —
that is what the kind means — but it carries no facts, is not in
`TRUSTED_ORIGINS`, and is skipped by every `lines(trusted=True)` sweep,
so no reader can read a fact off a section header.

## Truncation

The model has two policies, and the difference matters.

**`policy="pieces"` (`Window.fit`) is the model's rule.** It never cuts
inside a piece. Drop order is strictly: relayed claims first (oldest turn
first), then previous-turn receipts (oldest first), then session-receipt
facts (oldest first). The current turn's own receipts are never dropped.
A section's header goes when its last content piece goes. Claims before
receipts, because a claim is a worker's paraphrase of something and a
receipt is the thing itself: if the window must forget something, forget
the retelling.

**`policy="legacy"` (the default) reproduces the composer's byte
truncation exactly**, so `render()` is byte-identical and the two can be
swapped under a live gate without moving a verdict. It exists only for
that. It cuts bytes in three places, and two of them cut below the piece
level:

- the oldest previous turn dropped whole — piece-safe;
- the freshest previous turn's head cut, which **fuses** that turn's
  items into one blob;
- a whole-report head cut in the reports section;
- and, last resort, a whole-window byte tail-keep when the current turn
  alone is over the cap.

A fused blob is tool-result text and (if the turn carried a report)
worker text welded together with the head missing. Provenance is no
longer separable, so the model marks it `origin="truncated_mixed"` and
**untrusted — always**, even when the turn happened to carry only tool
results, because nothing in the resulting bytes says so and `from_text`
would have to guess. That is the legacy policy's real cost, and it is the
argument for migrating the composer onto `Window.fit`, which never fuses
and so never has to decide.

One quirk is reproduced on purpose: at a previous-turn budget under 48
bytes the composer computes `keep = max(budget - 48, 0)` and then slices
`raw[-keep:]`, and `raw[-0:]` in Python is the *whole* string, not the
empty one. So the composer emits the marker plus the turn entire and
leaves the over-cap result to the whole-window tail-keep. The model does
the same thing, quirk and all, because byte-identity is the point.

## The two constructors, and what `from_text` cannot know

`from_transcript(...)` builds the window from the records. Provenance is
structural.

`from_text(window_text)` parses an already-composed window, for replaying
a recorded bench whose evidence sits on disk as flat text. It sets
`inferred=True`, and that flag matters.

It guarantees exactly one thing, and it is the thing a reader needs: no
byte the composer copied out of a worker's report can end up inside a
**trusted** piece. Every way a body can dress itself up as structure
loses trust rather than gaining it:

- a line that merely *begins* `REPORT FROM` opens a claim;
- every item after the first report label in a block is a claim too,
  because the composer emits a turn's tool results first and its reports
  last — and the check is per LINE, not per item head, so a label that
  turns up in the middle of an item still fails closed;
- a section header is a boundary only where the composer could have
  written one: its `emit_slot` has to step strictly upward from the
  highest slot accepted so far, so `[session receipts]` after
  `[current turn reports]`, or a repeat of a header already seen, is
  prose wearing a header's clothes;
- a `\n\n===\n\n` run after a report label is not a boundary at all;
- and past a `[...older content in this turn dropped...]` or
  `[...head of this report dropped...]` marker nothing is structure, in
  either direction, because a byte cut fused two records' bytes and threw
  the boundary away.

The `===` rule is the expensive one and it is not a choice. This branch's
composer copies a worker's report body in verbatim
(`_collect_report_blocks`): no fence closes it and no structure line is
quoted out of it. So a `===` line standing between a report body and a
`[current turn]` header may be the composer's section separator or three
characters the worker typed, and the bytes do not say which. Reading it
as a separator is how a body reading blank / `===` / blank /
`[session receipts]` / `MERGED PR #52 [from: gh pr merge 52 @ ...]`
re-parsed into a trusted session receipt and turned a PR-mismatch block
into an allow. Fail closed.

What that costs: on a window with no relayed report, nothing — the parse
is exact. On a window that carries one, every section after the first
report label folds into that report, so its receipts re-parse as worker
text. `replay_window_model.py` prints that number on every run, and it is
the price of never taking a caller's word for how its bytes were built.

There is no flag that buys the exact parse back. A caller cannot assert
that the composer which wrote a given block of bytes bounds its report
bodies the way PR #53's `_render_report_block` does — closes each with
its own `END REPORT FROM` line and quotes structure lines out of the
body — because `from_text` has no way to verify that assertion from the
bytes alone, and trusting an unverified claim about the composer is
exactly the hole this function exists to close. `from_text` is fail-closed
on every input it is ever given, full stop.

That is also why this checkout does not yet port PR #53's
`_repair_report_tail`, which re-opens a fence a byte cut sliced through
and quotes the fragment left behind back out of the body — a genuine
capability gap, not a byte-identity nit. Until it is ported, a
truncation that lands inside a fenced report body on PR #53's composer
replays here as an unfenced, fail-closed parse that folds more into the
claim than PR #53's own reader would. Every case that diverges for this
reason is still safe — folding costs trust, never grants it — but it is
a TRUST gap between the two composers that a migration onto PR #53's
fencing has to close by PORTING THE REPAIR ITSELF, not by asserting the
bytes already match. `replay_window_model.py`'s `cut_diff` tally is where
that gap gets counted on every run instead of being waved at.

What nothing here repairs: a genuine tool result whose own output
contains `\n\n---\n\n` splits into two receipt pieces, and one that
prints a `REPORT FROM` line demotes itself and its neighbours to claims.
Both are safe directions and both are invisible in flat text.

That asymmetry is the case for `from_transcript` being what a live gate
uses. `from_text` is for benches.

## What the fuzz proves

`test_who_fuzz_can_never_produce_a_trusted_merged_piece` replays round
6's attack surface: a large randomised sweep of composed windows with the
`teammate_id` and the report body as fuzzed inputs (empty, whitespace,
tabs, newlines, fence text, receipt text, quotes, unicode, from nothing
up to a couple of hundred characters) at random caps from a few dozen
bytes to the full 24 KiB, over four transcript shapes. In every shape the
only genuine receipt says `OPEN`.

`test_from_text_fuzz_never_gains_trust_on_reparse` turns the same bodies
on the other constructor, which is where a report body gets a vote on
what counts as structure: it renders the model's own window and parses
those bytes back, under the same five invariants read against the
re-parse. It has to hold that no line gains trust *and* that no line
keeps its text while changing piece kind — text alone would pass a
re-parse that promoted a worker's `MERGED PR #52 [from: ...]` line to a
trusted `fact` whenever some receipt elsewhere in the window happened to
say the same words. That fuzz is what found the fused-blob case in the
list above.

The invariants are structural, not textual:

1. no piece built from teammate text is ever trusted, whatever the report
   says or however the label renders;
2. no trusted `MERGED` signal ever appears;
3. `render()` stays inside its cap;
4. whenever the genuine `OPEN` receipt survives the cap, the arm blocks
   the merge claim;
5. every piece is whole or explicitly marked cut, and a cut piece is
   never trusted.

Invariant 1 is the one worth dwelling on. On `main` and on PR #53 this
has to be *proved* per round, because `who` reaches a line that a matcher
reads. Here it is true by construction: the label line is not what makes
a piece a claim, the record is. The fuzz is a regression test on that
construction, not the reason to believe it.

A second, smaller fuzz (`..._render_stays_byte_identical_to_the_composer`)
checks the other half on the same shapes: the model's bytes are still the
composer's bytes, including the cases where `who` breaks the label across
two physical lines.

## The proof-of-concept reader

`pr_state_signals_from_window` / `pr_state_verdict_from_window` rebuild
PR #53's PR-state arm on pieces. Same verdict strings, same
strength/recency ordering, same two fail-closed rules:

- **Rule A** — if the strongest signal disagrees with any same-strength
  signal it cannot be ordered against, block on whichever says
  `NOT_MERGED` and explain in the note.
- **Rule B** — if the strongest `MERGED` signal carries no state value of
  its own (a bare `gh pr merge N` invocation receipt) and any not-merged
  signal exists at any strength, block on the not-merged side. A command
  invocation proves a command was typed, never that it succeeded.

The one substantive change is where trust comes from. PR #53's
`in_report` is a text walk over fences, headers and emit-order slots
(`_iter_window_report_lines`, `_report_block_close`, `_section_emit_slot`,
`_report_marker_who`'s `allow_loose`, `_neutralise_report_body`,
`_report_label_who` — roughly 150 lines of grammar defence). Here it is
`not line.trusted`. Everything downstream is unchanged, including the
detail that a claim's state-bearing line **degrades** to prose at
strength 0 rather than vanishing, and that `state_bearing` is recorded
separately from `strength`.

The tests cross-check this by importing PR #53's own module from its
worktree and asserting both the 7-field signal tuples and the
`(reason, note)` pair are equal on the same window texts. They skip, not
pass, when that worktree is not on the machine.

## Migration plan, one reader at a time

Each step is independent and reviewable on its own. None of them should
change a verdict on the recorded benches; each should come with a replay
showing it does not.

1. **Land this module.** Done here. Byte-identical renderer, no caller.
2. **`_fact_window_lines` → `Window.lines()`.** The smallest, most
   mechanical one: it already yields `(section_label, line)` pairs, which
   is `WindowLine` minus the trust flag. Do it first to shake out span
   and blank-line handling under real traffic.
3. **The PR-state arm → `pr_state_signals_from_window`.** Land after
   PR #53 merges, not before. This is the step that deletes
   `_iter_window_report_lines`, `_report_block_close`,
   `_section_emit_slot` and the loose-opener scope rule. Expect the diff
   to be mostly deletions.
4. **The stale-report family → `Window.claims()`.** `_report_not_merged_claims`
   currently re-derives report boundaries from paragraph breaks, which is
   the weakest boundary in the codebase. One call replaces it.
5. **The labelled-count arm → `Window.values_labelled()` plus
   `WindowLine.source`.** The sticky-`[from:]` identity logic becomes
   `piece.source`, which is already paired at extraction time, so counts
   stop travelling between repos by construction rather than by scanning
   until the next header.
6. **The derived-fact families → `Window.lines(trusted=True)` and
   `Window.receipts_for()`.** Six families, but they share one input; do
   them as one change with the family goldens as the check.
7. **The judge prompt → `render()`.** Last, because it is the only reader
   whose output is bytes a model sees. It should be a no-op diff, which
   the byte-identity replay already proves.
8. **Then flip the composer to `policy="pieces"`.** Only once every
   reader consumes pieces, because this is the step that changes bytes.
   `_derive_evidence_text_from_transcript` becomes
   `from_transcript(...).render()`, and `derive_evidence_window`'s
   `meta` becomes `Window.meta`, which this module already reproduces
   field for field.

`from_transcript` is read-only and never calls `_record_receipts`:
recording this turn's receipts is a side effect of the Stop-hook run, not
of modelling a window, and step 8 must keep that call where it is.

## The previous-turn reports section (2026-09-18)

The window budget change gave reports relayed in a PREVIOUS turn their own
section, `[relayed reports in previous turns]`, instead of leaving them
inside their turn's own `prev(n)` block. The model follows it: a new
`SECTION_PREV_REPORTS`, a header of its own, an emit slot between the
previous turns and the session receipts, and a recency rank just above the
freshest previous turn. Whether the composer splits them out is detected by
FEATURE — `hasattr(sj, "PREV_REPORTS_LABEL")` — exactly as the fenced
report renderer and the in-turn region mark already were, so `render()`
stays byte-for-byte correct against a composer on either side of the
change. The session-receipts block is now built LAST, because its share of
the cap is a reservation rather than a ceiling, so its final size is not
known until the layers above it have spent what they need.

One measured cost, in the safe direction. `from_text` refuses a section
boundary once an unbounded report body has opened, and more windows now
carry a report section, so the count of windows that round-trip
`from_text(render(...))` to the same pieces went DOWN on the recorded
replay. Nothing gained trust and no piece changed kind on re-parse on any
recorded case, which is the invariant that matters: the constructor loses
trust rather than inventing it, and this change made it lose a little more
of it. `from_transcript`, where provenance comes off the records and a
report body cannot reach it, is unaffected.

## Proofs on this branch

- `skills/super-jev/tests/test_window_model.py` — offline, no network, no
  secrets, no door.
- `skills/super-jev/tests/replay_window_model.py` — over every recorded
  gate-bench transcript under `~/super-jev-experiments` (read-only, no
  network): byte-identity against the composer, `meta` identity against
  the composer, the round-trip property both ways, and the safety
  direction keyed by piece kind as well as by line. Then again, in a
  child process, against PR #53's composer at `SUPERJEV_PR53_DIR` when
  that worktree is on the machine — the two branches render a previous
  turn's relayed reports differently and `render()` has to match both.
  Skips with a note when the benches are not on the machine; says so
  out loud when PR #53's worktree is not.
