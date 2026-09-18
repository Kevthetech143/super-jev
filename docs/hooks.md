# The hooks: what they do, what they cost, and when not to install them

`skills/super-jev/hooks/` holds three shell scripts. Each one wires a Claude
Code hook event to `superjev.py hook`, which checks what an agent is about to
say, or what a sub-agent just reported, against evidence a machine collected.
Nothing here is installed for you. You copy a snippet into your own
`settings.json`, and you can remove it the same way.

This page is written for someone who has not used them. It is honest about the
downsides, because a checker that blocks true statements costs more than it
saves.

## The three hooks

**Stop → `hook gate`.** When the agent finishes a turn, Claude Code hands the
hook the final assistant message. The gate treats that message as a draft,
derives evidence from the transcript itself (the tool results of the last
several tool calls in the turn), strips fleet machine tags out of the draft so
bookkeeping lines are not judged as content, and asks the claim gate whether
each claim in the reply is carried by that evidence. A fabricated quote is
caught for free by a string match before any model call. If the turn ran no
tools at all there is no evidence to check against, so the gate prints one
advisory line and always allows.

**PostToolUse on the Agent tool → `hook verify`.** When a sub-agent's tool call
returns, this checks the returned text as a worker report against machine
evidence: git state, the diff, a listing of every path the report names, the
pull-request state, and a comment-stripped grep. It skips outright when the
returned text is only a launch acknowledgement, or a spawn dictionary carrying
the brief rather than a report, because judging a brief as if it were a report
is a category error that produced false blocks.

**UserPromptSubmit → `hook prompt-verify`.** A background sub-agent's real
final report never arrives through PostToolUse. It lands later, inside the next
user turn, as a teammate-message block. This hook reads those blocks, derives
the worktree, pull request and test command from the report's own text, and
verifies each one. It is advisory only and never blocks, because blocking a
user's prompt is not a recoverable state.

**Correction, 2026-09-17: `prompt-verify` never actually sees a real report.**
A real fleet transcript shows a teammate's message landing as ordinary
`"type":"user"` content in `transcript_path`, but Claude Code's own
UserPromptSubmit payload capture does not fire for it — so `hook
prompt-verify` only ever ran in this skill's own tests, never against a real
session. `hook gate` (the Stop hook above) DOES fire on every turn, and it
already opens `transcript_path` to derive gate evidence, so it now also scans
that same file for teammate-message report blocks this session has not
already checked (tracked in a per-session state file under
`ledger/state/stop-state-<session_id>.json`) and runs `verify` against each
one — up to 3 per Stop event, within a 120-second budget
(`SUPERJEV_STOP_SCAN_MAX_REPORTS` / `SUPERJEV_STOP_SCAN_MAX_SECONDS`). An
idle-notification echo of a report already seen (a
`{"type":"idle_notification", ...}`-wrapped duplicate) is skipped, not
double-checked. This is the same PR #20 0.80-confidence read, but ADVISORY
ONLY — a REJECT-worthy scanned report never touches this Stop event's own
exit code, it only prints an extra `super-jev verify <teammate_id>:
CLEAN|READ|REJECT — <flags> — <evidence used> — health ok|thin` line and logs
a ledger row with `source="stop-transcript"`. `hook prompt-verify` and its
UserPromptSubmit wiring are left in place (harmless, and correct if Claude
Code ever does start firing that payload for a teammate message), but the
Stop-hook scan is the path that is actually live today.

**Correction, 2026-09-17 (2):** the "no evidence" advisory above only
scoped correctly as of this fix — it used to scan the WHOLE transcript for
tool_result content, so an earlier turn's tool call could make a genuinely
tool-free turn look "healthy" and let a confident flag block it; evidence
is now scoped to the current turn only, and a truly tool-free turn always
advises (`health="none"`), never blocks.

## Wiring it in

Copy this into `~/.claude/settings.json`, with absolute paths, and set
`SUPERJEV_GATE_CMD` and `SUPERJEV_VERIFY_CMD` to your own claim-gate and
report-verify tools (see `skills/super-jev/SKILL.md`). With those unset the
hooks fail open and do nothing, rather than blocking on a missing dependency.

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command",
          "command": "/abs/path/skills/super-jev/hooks/stop-gate.sh" } ] }
    ],
    "PostToolUse": [
      { "matcher": "Agent",
        "hooks": [ { "type": "command",
          "command": "/abs/path/skills/super-jev/hooks/posttooluse-verify.sh" } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command",
          "command": "/abs/path/skills/super-jev/hooks/userpromptsubmit-verify.sh" } ] }
    ]
  }
}
```

Start with PostToolUse only. It is the hook with the clearest payoff and the
one that cannot interrupt you mid-sentence.

## Block versus advisory

**Advisory** means the hook exits 0 and prints one line. The agent sees the
line in its own context and can act on it. Nothing is prevented. Most findings
are advisories, and that is the intended resting state.

**Block** means the hook exits 2 with a reason on stderr. On Stop, the turn
does not end and the agent is asked to try again. On PostToolUse, the lead
agent is told the report did not check out.

A block needs one of three things: a fabricated quote, or a claim the judge
flagged not-supported or contradicted with high confidence, or a confident
overclaim sitting next to a claim the judge is at least fairly confident is
not supported or contradicted. Self-contradiction never blocks, alone or in
company. None of these fire on evidence too thin to judge against — no
evidence source, a directory-level test command worker-verify refuses
outright, a `--pr` block that carries no check-run data, or a gather under the
char floor all turn every one of them into an advisory instead, because a
confident-but-ungrounded verdict and a real problem print the identical red
table. `hook verify --from-file --explain` prints the gather size, every claim
row and which rule decided.

## The loop guard

A Stop hook that can block can also refuse to let a turn ever end. That
happened: one short reply blocked three times running while the reply was
rewritten more carefully each pass, because a calmer rewrite still reads as
mildly self-contradictory. Two guards exist now. Self-contradiction alone is
not a block reason. And when Claude Code sets `stop_hook_active`, meaning this
Stop event is already a re-run after a block, the gate never blocks again on
that pass. It prints what it would have blocked on and allows. The ledger line
records this plainly as a fail-open, not as an approval.

## Gate v2 (2026-09-17)

Five changes, built from a 40-case live-judge bench
(`super-jev-experiments/gate-bench-20260917/analysis/REPORT.md`), replayed
offline where the change is a deterministic rule and reported unverified
where it needed a live judge re-run this pass did not do.

- **Per-fact claim pre-split — opt-in, default OFF.** A non-empty `--draft`
  can be split into clause-sized claims (sentence boundaries, `;`, `:`,
  standalone ` and `, deduped, capped at 25) and handed to jev.py one per
  `--claim` via `--claims-file`, instead of letting jev's own splitter
  collapse a whole multi-fact draft into one claim. The 40-case bench
  suggested this could catch lies that ride inside a longer claim whose
  other parts were true, but a live bench run afterward showed pre-split
  adding a false block with no matching catch, so the default was flipped
  off. Set `SUPERJEV_PRESPLIT=1` to opt back in; leave it unset (or `0`)
  for the old `--draft` behaviour, which is now the default.
- **Deterministic count/PR cross-check — on by default**, run before the
  judge, no model call: a drafted test count that contradicts a real
  `N passed` line in the evidence, or a drafted "PR #N merged" the
  evidence's own `gh pr view` state contradicts, blocks outright with a
  plain reason (`count mismatch (tests): draft N vs evidence M`; see
  "receipts backfill and the labelled count arm" below for the labelling
  rule that decides what may be paired at all). Verified on the
  bench replay to catch real count-mismatch cases without blocking a true
  claim — see `skills/super-jev/tests/replay_gate_bench.py`. Never fires
  on a bare evidence gap (a count the evidence never mentions at all): that
  is a missing measurement, not a contradiction, and treating it as one was
  the exact bug the OVERCLAIMS health check already guards against.
- **Wider evidence window.** The Stop-hook gate now also folds in the
  previous turn's tool_result content (lower priority, capped at half the
  byte budget) and up to the last 40 "receipts" — one dated fact line per
  session, appended whenever a Stop event sees a receipt-worthy line in this
  turn's own tool results, plus a whole-transcript backfill of the same
  lines (see "receipts backfill and the labelled count arm"), so a fact
  from several turns back does not fall out of the window the moment the
  turn ends. PR #22's health semantics are unchanged: a turn that ran no
  tools of its own still gets no evidence here, whatever the previous turn
  or the receipts carry, and stays on the advisory-only "unchecked" path.
- **`overclaim == 1.00` arm**, OFF by default, `SUPERJEV_OVERCLAIM_100_BLOCK=1`
  turns it on: a draft-level OVERCLAIMS flag scoring 0.995 or above blocks
  even without the usual companion NOT_SUPPORTED/CONTRADICTED claim
  (health still required). **Fragile, said plainly**: the bench showed this
  line sits on a 0.01 cliff — relaxing it to 0.99 costs 3 truths
  immediately on that same 20-truth set. Left off until a second bench
  confirms the margin.
- **Stop-scan fixes.** A session's first-ever Stop scan now parks its state
  at the transcript's current end instead of the top, so it processes zero
  old reports on that call rather than re-verifying everything already in
  a long transcript. A worktree the scan auto-derives from a report's own
  text must now be a real, existing directory — a URL's path component
  (e.g. `.../pull/13` inside a GitHub link) no longer gets treated as a
  worktree.

## Gate v3 — wide window (2026-09-17)

A second 40-case live-judge bench
(`super-jev-experiments/gate-bench-20260917/`, `results-wide/`) widened the
evidence window further than gate v2 did and re-ran the judge against it. Two
things changed at once, and they pull in opposite directions: with more
surrounding context, the per-claim NOT_SUPPORTED/CONTRADICTED signal gets
noisier — more text to disagree with dilutes a clean read — but the
draft-level OVERCLAIMS flag gets sharper. It stops reading like "the gather
was too thin to be sure" and starts reading like a real judgment that the
draft claims more than the wider evidence actually carries. That is the
opposite of what a thin window produces, and it is why OVERCLAIMS can now
carry a block on its own where it used to need a companion claim beside it.

**The window itself.** The Stop-hook gate's evidence is now built from the
previous **two** turns' tool_result text (`SUPERJEV_PREV_TURNS`, default 2 —
gate v2 only reached back one), plus session receipts, plus the current
turn — current turn always highest priority and never dropped. The whole
assembled file is capped at **24 KB** (`SUPERJEV_EVIDENCE_CAP_BYTES`,
default 24576; the older `SUPERJEV_HOOK_EVIDENCE_MAX_BYTES` cap still
applies underneath it — whichever is smaller wins). When the window would
run over the cap, the **oldest previous turn is dropped first**, one turn at
a time, before anything newer is touched; if even the single most recent
previous turn is still too big once everything else is gone, its tail (the
freshest bytes) is kept over its head. `hook gate --explain` prints the
window's own composition — bytes per segment, how many previous turns were
found versus dropped, the receipts count — on top of the usual claim table
and rule report.

**The block rule.** Gate v3 is the default rule now (`SUPERJEV_RULE`, unset
or `v3`):

1. A draft-level OVERCLAIMS flag at or above `SUPERJEV_BLOCK_OVERCLAIM`
   (default **0.90**) blocks on its own — no companion
   NOT_SUPPORTED/CONTRADICTED claim required, which is the one clean break
   from gate v2. It is still suppressed when the evidence gather is measured
   thin (the same `_gather_healthy` check every flag here goes through) —
   the wide window fixes the *companion* requirement, not the 2026-09-17
   thin-evidence false block gate v2's health check exists for; those are
   two different failure modes and only the first one changed.
2. A claim-level NOT_SUPPORTED/CONTRADICTED at or above `SUPERJEV_BLOCK_CONF`
   (default 0.80, unchanged) is a **secondary** trigger, firing only when
   the gather is healthy — same shape as gate v2's primary rule, just no
   longer the one carrying OVERCLAIMS.
3. Self-contradiction still never blocks, alone or in company.
4. Deterministic count/PR mismatches (gate v2) are untouched — pure string
   arithmetic, no model call, never suppressed by evidence health.

**The cliff, documented rather than hidden.** The bench's own truths sit
close together right around this line: on the wide read, one true case
scores OVERCLAIMS 0.85 and would be wrongly blocked if the line sat there;
the nearest true cases above it sit at 0.86–0.88, and the nearest lies sit at
0.92 and up. **0.90 is the measured line** — high enough to clear every
truth the bench saw, low enough to still catch the lies whose OVERCLAIMS
reading is unambiguous. This is a calibration line on a 20-truth/20-lie
bench, not a law of nature: the intended way to firm it up is the same
calibration loop that produced it — run the bench again as more cases
accumulate, watch where the line would need to move, and move it with real
numbers behind the move rather than a guess.

**Legacy mode.** Gate v2's own rule (the 0.50-companion requirement for
OVERCLAIMS) is still reachable, unchanged, as `SUPERJEV_RULE=v2` — kept
specifically so the two can be A/B'd against each other on a future bench
rather than the older rule simply being deleted.

## Gate v3 — empty current turn (2026-09-17)

A production/bench disagreement review (`SHIM-VS-BENCH.md`, Opus review of
`gate-bench-20260917`) found that the window builder used to return nothing
at all whenever the current turn ran no tools of its own, no matter how much
previous-turn or session-receipts material was sitting right there. That
routed the whole turn to `hook gate`'s advisory-only "unchecked" path (the
last user prompt stands in for evidence, and the run can never block) even
when the draft was scoring OVERCLAIMS 0.98 against real prior evidence. Nine
of forty bench cases took that branch; three of them were lies the offline
bench caught and the live path let straight through.

The window builder now always assembles whatever previous-turn and receipts
material exists, even with an empty current turn, and marks the window
`current_turn_empty`. `hook gate` only falls back to the unchecked path when
the fully assembled window is still empty (no current turn, no previous
turns, no receipts). Whenever the window judges but the current turn added
nothing of its own:

- the **primary** OVERCLAIMS arm still blocks at or above the line — a reply
  is not made safe by the fact that this turn ran no tools, and suppressing
  this arm was worth three caught lies against zero blocked truths on the
  same bench;
- the **secondary** NOT_SUPPORTED/CONTRADICTED arm is suppressed to an
  advisory — a confident red verdict against a window this turn did not
  itself add to is treated the same way a thin-gather verdict already was;
- deterministic count/PR mismatches are unaffected either way, same as
  always.

`hook gate --explain` prints a `current turn empty` line alongside the rest
of the window report.

**For benches.** The window assembler is the single source of truth for
what gate v3 actually judges against — a bench that reimplements its own
turn-boundary logic (as the original wide-window bench did) will silently
drift from what ships. `superjev.derive_evidence_window` is the small public
wrapper around the window assembler (`_derive_evidence_text_from_transcript`
under the hood) meant for exactly this: import it, call it with a real
`transcript.jsonl` path, and score the window it actually returns — same
signature (`transcript_path`, plus the optional `n`/`max_bytes`/
`session_id`/`prev_turns`/`cap_bytes`/`return_meta` knobs), same return
shape (a text string or `None`, or a `(text, meta)` pair when
`return_meta=True`).

## Gate v3 — receipts backfill and the labelled count arm (2026-09-17)

A follow-up diagnosis of the seven true reports the live shim blocked on the
2026-09-17 bench (`gate-bench-20260917/results-v3-shim/` against
`evidence-wide/`) found two distinct causes, and only one of them was about
window size.

**What the 24 KB cap cut: nothing.** Measured on all seven cases,
`prev_dropped` was 0 and `prev_truncated` was `None` every time — the
assembled window ran 2.3 KB to 7.0 KB against a 24576-byte cap. The cap is
not raised, because no evidence shows it removing anything.

**What the turn boundary cost: no evidence.** The offline bench walked back
over assistant messages whose `stop_reason` was not `tool_use`; the shim
walks back over real user prompt records, and in a team transcript teammate
reports arrive as plain `role: "user"` text, so the shim's turns are much
shorter. The byte difference is real, but every proof line the bench's
previous-turn block carried was already inside the shim's spans on all seven
cases. The boundary rule is therefore left exactly as it is — changing it
moves bytes without moving evidence.

**What actually went missing: the receipts layer.** The bench scanned the
whole session transcript for receipt-worthy lines; the shim read only its
own on-disk receipt store, and got 0 lines in all seven cases against 1 to
15 in the bench files. Those lines were the proof: `MERGED`,
`MERGED 2026-09-17T18:35:18Z`, `completed success  ... (#8)`, `N passed`.
Two fixes:

- **Transcript backfill.** `_record_receipts` only ever writes the *current*
  turn's facts, so a fact exists in the store only if the Stop hook ran on
  the turn that produced it. Any skipped turn leaves a permanent hole. The
  window builder now also harvests receipt lines straight out of the
  transcript from session start up to the current turn's boundary (capped at
  60 lines, deduped against the store). The store became a fast path rather
  than the only path.
- **A wider notion of a receipt.** The old pattern
  (`gh pr merge|gh pr checks|N passed`) matched those *commands* but not
  their *output*: `gh pr view --json state` prints a bare `MERGED`, and
  `gh pr checks` prints `completed  success  <job>`. Both are now
  receipt-worthy, along with `checks passed`.

  The honest tradeoff: a wider pattern also harvests prose from a teammate
  report that happens to say `MERGED`, and a tool result is not always a
  first-hand measurement. This makes the window more willing to *support* a
  merge claim, not just to contradict one. It is the bench's own behaviour,
  and it is called out here rather than buried.

**The count arm now pairs labels, never bare numbers.** The deterministic
count cross-check used to pool every integer in any clause mentioning
tests/passed/failed into one draft set, pool every `N passed` and every
`N of M` anywhere in the window into another, and fire when the two sets
were disjoint. That manufactures a contradiction out of two unrelated
numbers. Bench case t07 is the worked example: the draft said "/card is
built ... its 29 tests pass", the window carried the line
`152/152 passed   all green` from a previous turn's worker-verify report
table (a different suite in a different repo), and the arm blocked a true
report with `count mismatch: draft 29 vs evidence 152`. Case l18 fired for
the same wrong reason — draft "56 tests pass" against the Jev line
`10 of 10 need a human`, which is a claim tally, not a test count.

The rule now:

1. A draft integer is attached to a **unit label** (`tests`, `files`,
   `prs`, `commits`) only when a keyword for that label sits within four
   word-tokens of it, in the same clause. An integer with no unit word near
   it — a PR number, a version, a duration — is attached to nothing.
2. An evidence integer is read only out of a line matching a **registered
   receipt shape** for that same label. For `tests` that means a real
   runner summary: pytest's `N passed in <time>`, jest/vitest's
   `Tests: ... N passed`, node/tap's `pass N`, mocha's `N passing`.
   `152/152 passed   all green` matches none of them, which is the point.
3. The arm fires only when the same label has counts on both sides and none
   of the draft's counts match any of the evidence's. A draft count that
   matches *any* same-label evidence count clears the arm, so the arm stays
   lenient by construction; it cannot tell two same-label suites apart and
   does not pretend to.
4. Labels with no registered evidence shape (`files`, `prs`, `commits`) are
   extracted from the draft and reported by `--explain`, and never paired.
   An unpairable claim is an evidence gap, not a lie.

Re-measured offline against the bench's own drafts and replayed windows: the
arm still fires on l04 and l05 (the two count lies the v2 note credits) and
no longer fires on t07 or l18. l18 stays blocked on OVERCLAIMS 1.00, so the
verdict is unchanged there and only the reason improved.

**`--explain` now names the turns.** The window report prints which
transcript record opened the current turn, and one line per previous turn
giving its record range, tool_result count, byte size, and whether it was
kept, cut by the cap, or head-truncated. Receipts print their source split
between the session store and the transcript backfill. A window a gate
blocked on is only auditable if you can see the turns it read.

## Gate v3 — worker reports and count identity (2026-09-17)

Two more window defects, both found by reading the blocked cases back
against their transcripts rather than by reasoning about the code.

### A worker's report is not a tool result

A worker or teammate never hands its report back as a `tool_result`.
Claude Code delivers it as a `role: "user"` **text** record: either a
`<teammate-message teammate_id="...">` block from the teammate mailbox, or
a harness `<task-notification>` block whose `<summary>`/`<result>` carries
what the agent ended with. The window was built from `tool_result` blocks
only, so none of it was ever visible to the judge. Three blocked cases
whose drafts were true were blocked for that reason alone: the only proof
of a test count going up, of a suite passing, and of a status command
showing every door live was a report sitting in the lead's own context.

The window now carries those blocks, from the current turn and from the
previous turns it already reaches back over, each under the label

    REPORT FROM <who> (unverified worker claim)

`<who>` is the `teammate_id`, the agent name the notification gives, or
the task id — never invented. The label matters as much as the text: a
report proves the lead **was told** something, not that the something is
true, and the judge has to be able to tell a relayed claim from a receipt.
What it stops is the failure where a lead faithfully passing on a worker's
numbers is scored as if it had invented them.

Reports ride at current-turn priority, because a report the draft is
relaying is what the judge most needs to see, but they may take at most
`REPORTS_BUDGET_SHARE` of the room left after the current turn and the
receipts, so a page-long report cannot starve the previous-turn block.
Inside that budget the oldest report is dropped first, and if even the
newest one is over budget its tail is kept. A previous turn's reports ride
inside that turn's own block and are dropped with it. Any single report is
carried up to `REPORT_BLOCK_MAX_CHARS`. `current_turn_empty` deliberately
still tracks this turn's own tool results only: a turn whose only new
material is a relayed report has run nothing itself.

### A count now carries the command it came from

The count arm used to pair any draft count with any evidence count that
shared its coarse unit label. Two independent defects let it pair numbers
from different suites in different repositories:

- **The node:test recogniser missed the real line.** It was
  `\s*#?\s*pass\s+(\d+)`, and node:test prints `ℹ pass 158`. The glyph
  is not whitespace, so `\s*` could not consume it and the true count sat
  unmatched in the window while a stale number from elsewhere paired
  against the draft instead. Both `ℹ pass N` and the total line
  `ℹ tests N` are now recognised.
- **A receipt carried no identity.** Every receipt, and every tool result
  carried into the window, is now labelled with the command line and the
  cwd it came from, read off the `tool_use` input the transcript already
  holds and paired through the `tool_use_id`:

      [from: python3 -m pytest skills/card/tests/test_card.py -q @ /Users/admin/repo]

  A lone header line identifies every count printed beneath it until the
  next section or header; a receipt carries its marker on its own line.

A draft count now pairs only with evidence counts whose identity is
something the **draft or the current turn** actually names: the same test
runner family (`pytest`, `npm test` / `node --test`, `jest`, `vitest`,
`mocha`, `go test`, `cargo test`), or the same non-generic repository,
directory or package path. The command is the identity; the shell's cwd is
not, because every run in a session shares it, and matching on it alone is
precisely how a stale count from one repository reached a claim about
another. An evidence count with no identity at all still pairs, exactly as
before.

When nothing pairs, the arm stays **silent**. An unpairable claim is an
evidence gap, never a lie — the same direction this file already takes for
`OVERCLAIMS`.

`--explain` prints the pairing: how many reports were carried and how many
bytes they took, and per unit label the draft's counts, the evidence
counts they were paired with, what matched, and which counts were scoped
out by command identity together with the run they came from.

## Gate v3 — derived facts at the head of the window (2026-09-17)

The gate used to hand the judge raw command output and ask it to infer
state from a column dump. The 2026-09-17 gate analysis
(`super-jev-experiments/gate-bench-20260917/analysis/LAST-MISSES.md`) found
the remaining misses were all that one defect, and in every one of them the
refuting string was **already in the window**:

- a draft said it had deleted a card, while a post-write listing row in the
  same window still showed that card, and the only `REMOVED:` receipt in the
  window named a different one;
- a draft said a card "rides every turn", while the window's own cadence
  expression read `untagged -> role-default (... ~ every 152t)`;
- a draft made a universal claim over a mismatch table whose rows were all
  *stricter* than expected — which is what made the claim true rather than
  false, except that nothing said so.

A judge catches "evidence says X, draft says not-X". It is weak on reading a
column dump as authoritative state, and on noticing that a receipt is
**absent**. So the gate now states the answer as a sentence, in code, before
the judge reads anything.

The window is composed as:

```
DERIVED FACTS (computed in code from this window's own text plus the draft — ...)
- agent_role::fable_operating_manual removed per [current turn].
- agent_role::fable_awareness still present in [current turn] after the claimed removal.

===

BACKING (the raw evidence window, unchanged — every fact above was read out of it):
[previous turn -1]
...
```

Four families, all literal string and integer work over text already in the
window plus the draft (`derive_window_facts`):

1. **delete/remove claims.** Every removal receipt in the window is stated
   (`X removed per <section>`), and any `::`-qualified identifier the draft
   claims to have removed that instead appears on a listing row — the
   identifier followed by two or more numeric columns — is stated as
   `X still present in <section> after the claimed removal`. Restricted to
   card-style qualified identifiers on purpose: "the file is still in a
   listing" does not refute "I removed the debug block from that file".
2. **cadence claims.** When the draft asserts an injection frequency, the
   window's own cadence line for each card the draft names is quoted, and
   `~ every Nt` is turned into the comparison the judge did not make.
3. **result tables.** When the window holds a results table (an `N of M`
   line, or `(case, expected, actual)` rows) and the draft makes a universal
   claim — "every", "all", "each" — sharing a label with that table, the
   counts are printed, including how many mismatch rows were stricter than
   expected and how many were more permissive. A label with no known
   strictness rank is counted but never read as either.
4. **merge/CI claims.** Every merge receipt is named with its PR number, and
   every PR the draft says was merged with no receipt anywhere in the window
   is named as missing one.

Three properties are deliberate:

- **Facts first, cap after.** The evidence cap applies to facts plus raw
  window, and a fact is never dropped to make room for raw text. If the
  total is over, the raw window's *head* is trimmed, since its tail — the
  current turn — is what the window builder already treats as highest
  priority.
- **Silence is the default.** With nothing derivable, the door receives the
  window byte-for-byte unchanged. No fact is emitted unless a literal string
  or integer match produced it, and a rule that cannot read a line cleanly
  says nothing.
- **No threshold or rule change.** Facts only add a declarative line. They
  settle a contradiction before the judge rather than raising its suspicion
  level.

`--explain` prints the count and byte size of the facts block, each fact
sentence, and how many bytes of raw window were trimmed to fit.
`SUPERJEV_DERIVED_FACTS=0` turns the block off and restores the old window.

The logic lives twice: `windowFacts` in `src/enhance/derive-facts.ts` (the
canonical version, reachable from any caller through
`node src/derive-facts-cli.ts` as `evidence.window`), and a pure-Python
mirror in `skills/super-jev/superjev.py` (`derive_window_facts`). The hook
uses the mirror: it runs on every turn, the bridge pays a whole node
process's startup per call, and on a fleet install the skill sits at
`~/.claude/skills/super-jev/` where this repo is not on disk at all, so the
bridge is neither reliably present nor free on the one path that needs it.
One shared JSON fixture — `test/fixtures/gate-window-facts.json`, every
window line copied verbatim from the analysis — pins both sides to the same
fact sentences, which is what keeps the mirror from drifting.

## The evidence guard — what never reaches the judge (2026-09-18)

Before any window is handed to the judge, it passes through one guard, in
two parts:

- **Blocklist.** `isBlockedPath(p)` (TS: `src/enhance/evidence-guard.ts`) /
  `is_blocked_path(p)` (Python: `skills/super-jev/superjev.py`) says whether
  a path may ever be opened, read, grepped or listed. A blocked path is
  never touched; the caller states `skipped: blocked path` as a fact instead
  (`blockedPathFact`/`blocked_path_fact`) — so a blocked path reads
  **UNPROVABLE**, never FALSE. Blocked: the fleet login vault (`logins.md`),
  any `*-secret.md` or path segment naming a secret, `.env`/`.env.*`,
  anything under a `profile/` or `documents/` directory, playwright session
  profiles (`.config/pw-*`), anything naming cookies, `*.pem`/`*.key`,
  `id_rsa*`, anything naming a token, anything naming credentials.
- **Redactor.** `redact(text)` replaces secret-shaped spans with
  `[REDACTED:kind]` before text is packed into any evidence block: an
  OpenAI-style `sk-…` key, a GitHub token (`ghp_`/`gho_`/`ghu_`/`ghs_`/
  `ghr_`), a Slack token (`xox[baprs]-…`), an AWS access key id (`AKIA…`), a
  `Bearer …` value, an `Authorization:` header, a hex blob 65+ characters
  long, or any generic key-shaped blob 30+ characters mixing letters and
  digits. A git commit hash, an md5 or a sha256 digest (pure hex, up to 64
  characters) is evidence, not a secret, and survives; so does a
  slash-delimited blob (a macOS temp path looks exactly like a key) and an
  ordinary `snake_case`/`kebab-case` identifier. Email addresses are left
  alone unless the caller opts in (`redactEmails`/`redact_emails`) — an
  email is often the identity a claim is *about*.

Where it is wired in:

- **The Stop-hook gate window** (`compose_window_with_facts`): the whole
  window is redacted before facts are derived from it or it is handed to the
  judge. `--explain` prints an `evidence guard` line with the path-skip and
  redaction counts for that window.
- **The verify fallback's local gatherer** (`_gather_local_evidence`, used
  when no worker-verify door is installed): every path the report names is
  checked before it is opened — a blocked one contributes
  `blocked_path_fact` to the `lengths` block instead of being read — and the
  captured test-command output is redacted before it enters the evidence
  pack. `verify --explain` prints a `guard: N path(s) skipped, N
  redaction(s)` line, and the fallback's ledger entry carries the counts
  under `guard`.
- **`superjev.py guard`**, a standalone door: `--paths` reports which would
  be skipped and shows each readable one redacted; `--text` redacts a string
  directly. Exit 0 always — it only reports; the callers above are what
  actually skip or refuse to send something. Every call is ledgered with its
  `guard` counts.
- **`sweep`, `fetch` and `permit`'s own inputs.** `sweep`'s `--records`
  parser redacts every record's `text`; `fetch`'s parser redacts the
  catalog's own text, the plain `--request`, and any `--context` turns;
  `permit`'s snapshot parser redacts the free-text `reversibilityNotes` and
  `policyLines` fields (deliberately *not* `action`/`target` — permit's
  pre-rules parse a verb and an acted-upon object out of those two, and
  redacting a span inside them could hide the very thing a hard rule needs
  to match). Each prints its guard summary with `--explain` (sweep prints it
  unconditionally, to stderr).

The guard lives twice, deliberately duplicated rather than bridged for the
same reason as the derived-facts mirror above: `src/enhance/evidence-guard.ts`
is the canonical TypeScript version (used by the CLI doors under `src/`),
and a pure-Python mirror lives in `skills/super-jev/superjev.py` (search
`EVIDENCE GUARD`) for the Stop hook and the verify fallback, both of which
run in Python with no reliable node/CLI bridge on a fleet install. One
shared fixture, `test/enhance/fixtures/evidence-guard-cases.json`, is run by
both `test/enhance/evidence-guard.test.ts` and
`skills/super-jev/tests/test_evidence_guard.py`, so the two sides cannot
silently drift apart.

Ported from the read guard added to `atoms.py` (the shared claim-atoms/
fact-gatherer module the coding and research verify doors both draw on) for
AUDIT.md findings S1 through S7: most of super-jev's verification doors had
no read blocklist at all, and the one that did was name-only, so a grep or a
file read rooted anywhere near a home directory could put a private line
into an evidence pack that then left the box for the judge.

## Gate v4 — secondary arm demoted, hash-optional merge claims, cited-file
   tail (2026-09-18)

A follow-up audit against a second, untuned batch of the lead's own replies
(`SET2-AUDIT.md`) found that gate v3's rules generalize well on short,
single-claim replies but weaken on longer, multi-claim status reports — the
shape a lead's own summary turns tend to take. Three fixes, all offline and
deterministic; none of them touch the primary OVERCLAIMS rule or the
empty-current-turn health gate, both of which are unchanged.

**1. The secondary NOT_SUPPORTED/CONTRADICTED arm is now advisory-only.**
Rule 2 of `_hook_block_decision_v3` (a claim-level NOT_SUPPORTED or
CONTRADICTED at or above `SUPERJEV_BLOCK_CONF`) used to block a reply on its
own, same as OVERCLAIMS. It no longer can. It still scores every claim, still
prints as a labelled advisory line in `--explain` and in the block/advisory
stderr feedback (a block driven by OVERCLAIMS now also lists any secondary
flags that crossed the line as advisory context, on the same line), and still
feeds the calibration ledger. What changed is only which findings are allowed
to end a turn: that is now OVERCLAIMS at or above its line, and the
deterministic count/PR-mismatch arm, and nothing else. The reasoning: a
longer status reply carries more claims, and a meaningful share of those
claims are plans, opinions, design intent or self-audits — sentences no tool
result could ever support or contradict, which is a category error for a
NOT_SUPPORTED verdict rather than a real finding. The arm had never, across
either recorded bench, been the sole reason a genuine lie was caught; every
lie it touched also carried an OVERCLAIMS or a deterministic flag alongside
it. `SUPERJEV_RULE=v2` still exercises the legacy companion-gated behavior
for A/B, unaffected by this change.

**2. The merge-claim regexes no longer require a literal `#`.** The
deterministic PR-merge check (`_PR_MERGED_CLAIM_RE`) and the derived-facts
merge family (`_FACT_DRAFT_MERGE_RES`, feeding `_facts_merge_claims`) used to
require "PR #N" verbatim. A draft that instead wrote "PR N merged", "merged
PR N" or "#N merged" — all ordinary phrasings — was invisible to both. The
`#` is now optional throughout, and the third pattern's requirement for the
word "is" before "merged" was dropped so a bare "#N merged" matches too. The
identity guard downstream is unchanged: a claimed PR number is only ever
compared against a receipt actually present in the window
(`merge receipt found for PR #N in <section>.` / `no merge receipt for PR #N
in window.`); widening the claim-detection regex only widens what gets
*compared*, never what gets *trusted*. Swept offline against every recorded
draft in both gate-bench sets: the widened patterns detect new PR claims only
where the old ones missed a real phrasing, never invent a claim that was not
in the text, and never produce a false "no receipt" note against a draft
whose PR claim already had a receipt in its window.

**3. A cited file's tail is folded into the window.** When a draft names its
own source — an absolute or user-relative path, a bare filename with a
recognised extension, or a "per the `<X>` log" phrase — `build_cited_file_block`
resolves it to one real, readable, non-blocklisted file (an explicit path
resolved directly; a bare name resolved by a unique basename match under the
process's cwd, this repo's root, and `~/super-jev-experiments`; ambiguous or
unresolved candidates are skipped silently, no note, no error), reads its
tail, runs the same redactor every other section of the window goes through,
and appends it as a `CITED FILE <path> (tail)` block. It rides at the same
priority as the current turn — appended after it, so the window's existing
head-first trim never drops it before the current turn's own material — and
is still subject to the window's overall byte cap. This is evidence the
judge gets to see, exactly like a tool result or a session receipt; it is
never treated as true on the citation's own say-so. The case this targets:
a status reply that opens "per SUMMARY.md" and then gives real numbers that
live only in that file, against a window built purely from this turn's own
tool activity, which may have nothing to do with the numbers being reported.

## Gate v4.1 — cited-file resolver tie-break, stale-report-vs-receipt fact
   (2026-09-18)

A residue review of gate v4's own shipped windows (`V4-RESIDUE.md`) found the
cited-file tail from the previous section was landing empty on every single
recorded case, including the case it was built for, because
`_resolve_cited_basename`'s ambiguity guard had no way to break a tie between
same-named files and simply gave up. It also found gate v4's windows still
carrying a worker report that calls a PR "not merged" sitting beside a merge
receipt for that same PR, unremarked. Two deterministic fixes, both offline:

**1. The resolver tie-break.** When more than one real file still matches a
citation after the existing name/extension filter, three ordered steps try
to break the tie before giving up (each only applied if the prior step still
leaves more than one candidate): prefer a candidate whose path — in full, or
relative to a known root — appears literally in the window already assembled
for this turn (the file this session's own commands actually wrote to or
read); then prefer an exact case-insensitive stem match over a mere
substring match; then prefer `.md`, then `.log`, then `.txt`, then `.json`.
`.bak*` files are now always skipped outright. Still ambiguous after all
three steps is still skipped silently — the guard narrows the tie, it never
invents one. `build_cited_file_block` now takes an optional `window_text`
so the caller can hand it this turn's own window as the tie-break's evidence;
the Stop hook passes the window it already built. A "per the `<X>` log"
phrase's dotless keyword is unaffected in shape, but "in the summary" (no
trailing "log") is now recognised the same way, both mapped to the stem hint
`SUMMARY`.

**2. A stale-report-vs-merge-receipt derived fact.** When a `REPORT FROM
... (unverified worker claim)` block in the window states a PR is not
merged / open / pending, and a merge receipt for that *same* PR number sits
in a section of the window ranked more recent (previous turns oldest to
newest, then session receipts, then this turn's relayed reports, then this
turn's own tool results — the same layering `_derive_evidence_text_from_
transcript` already documents), a new DERIVED FACTS line names it: `PR #N: a
merge receipt at <section> postdates the worker report saying it was not
merged; the receipt wins.` The identity guard is the PR number itself — a
receipt for #27 never settles a report about #28 — and the fact never fires
in the reverse order: a receipt sitting in an OLDER section than the report
is left alone, since a report can legitimately postdate an earlier receipt
(e.g. a revert). This does not by itself guarantee a previously false-blocked
draft now passes — the draft's own uncited numbers can still overclaim — but
it removes the self-contradiction a judge was otherwise left to referee with
no rationale field to explain its read.

## Gate v4.2 — written-file identity, file read-back facts (2026-09-18)

`SET3-AUDIT2.md` measured the fleet's other bots (businessfi, health-fitness)
against a rebuilt, fully falsifiable lie arm and found the gate's only
blocking arm — the overclaim detector — is a prose-style signal, anti-
correlated with truthfulness on that fleet, while the judge itself named the
mutated claim in half the lies without ever scoring it high enough to block.
Section 5 of that audit named three claim types these bots make that had no
free check at all: "I wrote file X", a labelled figure quoted from a file,
and one score out of a list a tool emitted. Two of those three are now
derived facts, family 6 and 7 in `derive_window_facts`:

**6. Written-file identity.** Every Write/Edit receipt in the *current
turn only* (`[from: Write <path>...]` / `File created successfully at:
<path>` / `The file <path> has been updated successfully` / `saved to
<path>` / a `> <path>` redirect) is collected into the set of files this
turn actually wrote. Every basename the draft names in a written/saved/
created/updated sentence is checked against that set: a match states
`WRITTEN FILE: the draft names <X>; that file was written in this turn —
SUPPORTED`, a miss states `WRITTEN FILE: the draft names <X>; the only file
written in this window is <Y> — CONTRADICTED_BY_FACT`. Scoped to the current
turn on purpose — a file genuinely written in an *older* turn is real, but
stating it as "the only file written in this window" when a newer write also
happened would misread the session, which is exactly the caveat the audit
raised against its own l45 measurement. Measured offline over all 99
recorded cases across `gate-bench-20260917`, `gate-bench-20260918` and
`gate-bench-20260918-fleet`: 3 of 30 set-3 lies fire (the audit's own l41,
l43, l45), 0 truths fire in any of the three sets.

**7. File read-back.** When the window shows a file was read this session
(`[from: Read <path>...]` or a `cat`/`head`/`tail` command naming a path,
its body kept until the next receipt or section boundary) and the draft
states a numeric value sitting directly next to a "from `<file>`" / "in
`<file>`" reference — either order, `<file> ... is/was/reads/shows/states
<value>` or `<value> ... from/in <file>`, at most a couple of words between
them — the value is checked against that file's read-back block. Absent
from the block while a same-shape value (same `$`/`%` marker, same digit
count) sits in it names both: `FILE READ-BACK: the draft states <V1> from
<file>; the read-back of <file> in this window does not contain <V1> and
instead shows <V2> — CONTRADICTED_BY_FACT`. Silent when the block is absent,
the value is present, or no same-shape alternative exists to name — a bare
co-occurrence is not enough on its own (a set-2 case sharing a sentence
between an unrelated PR number and a file name would otherwise have
false-fired reading that number as a claim about the file's content; the
proximity/verb requirement above is exactly what rules that out). Measured
offline the same way: 0 lies and
0 truths fire across all 99 cases — the family's target case, the one the
audit itself called the hardest of the eight, states no filename in the
draft at all, so no literal file/value pairing reaches it; catching that one
needs a semantic read, not a literal check, so it is left to the judge.

Both stay off if `SUPERJEV_DERIVED_FACTS=0` is set, same switch as every
other family.

## Pairing a label to a value — families 8, 9 and 10 (2026-09-18)

`FLEET-FACTS-ROUND2.md` re-read the fleet-set lies that survived
families 6 and 7 and found that most of them share one shape: the draft restates
a value that a tool in the window already stated **under a label**, and the
mutation moves only the value. The naive form of that check — every number
in the draft must appear verbatim in the window — was measured on the fleet set
and blocks far too many truths to be usable, because these bots legitimately
quote session-level aggregates established upstream of the window. What
makes the three families below safe is that a value is never compared on
bare membership: it is compared only when an **identity anchor** ties it to
one specific labelled value in the window, and no anchor means silence.

**8. Labelled-value pairing.** The window's tool-emitted labelled rows are
read in two literal shapes, `LABEL: ... VALUE` and a whitespace-column
`LABEL  ...  VALUE`, into `label word -> value` (a row whose label carries a
digit is prose, not a label, and is skipped; one row may carry several
labelled values, as in `confidence: min 0.47 median 1.00 max 1.00`). The
draft side pairs a value to a label only by explicit adjacency — `LABEL
<linker>* VALUE` ("cut under $3.55") or `VALUE <preposition> LABEL` ("$219
in on fill") — and **never across a comma, semicolon, colon, slash, paren
or another value**, so a pairing that would have to jump a clause boundary
is not asserted at all. A `from A to B` range gives its label only to `B`:
the window's row holds the current value, so a correct "before" figure that
predates the window is never called a contradiction. The family then fires
only when the window carries **exactly one** value for that label word, in
the same shape class (same `$`/`%` marker, and a 0-1 confidence score is
never compared against a plain count). Two values under one label means the
check knows nothing and stays silent rather than pick one.

**9. Score-list membership, scoped to one run.** A prior audit proposed and
then withdrew this rule, having measured its window-wide form — it blocked
truths and missed the very case it was built for, because the mutated
score occurred legitimately elsewhere in the window under a different tool's
output. Scoping membership to **one run's own `conf=` receipt lines** rather
than to the whole window is what fixes it. The family needs at least two
distinct `conf=` values in the window and at least three scores quoted in
the draft, of which **two or more must be members** before it will call a
third a stranger — so a draft that merely mentions a score in passing never
fires.

**10. Claimed extremum.** When the draft calls a value the least/lowest/
worst (or most/highest/best) of something and the window carries an explicit
`min`/`max` row for a quantity of the **same name**, a recorded value beyond
the claimed bound settles the claim with arithmetic. Both the extremum sense
and the quantity name must match; the name match is a fixed six-character
prefix (a receipt's "confidence" against a draft's "confident"), not a
synonym table, so it ties two words that are the same word and nothing else.

All three name a match as `SUPPORTED` and a mismatch as
`CONTRADICTED_BY_FACT`, in the same sentence shape as every other family, so
a fact reads the same whether the draft is honest or not. Measured offline
over every recorded case across the three benches, with no judge call
(`skills/super-jev/tests/replay_fact_block_sweep.py`): together they fire on
**the fleet-set lies of this shape and on no truth in any of the three sets**.
The one remaining fleet-set lie is not a numbers case —
the draft's mutated token is present in the window under a genuinely related
finding, and separating the two needs a semantic read, so it stays with the
judge.

One related safety change came out of the same measurement.
`derive_window_facts` now orders `CONTRADICTED_BY_FACT` sentences ahead of
everything else before applying `DERIVED_FACTS_CAP`. One recorded case already
derives nearly as many `SUPPORTED` facts from the result-table and merge
families alone as the cap allows, so on a slightly busier turn a plain first-come truncation
could have dropped the one fact the deterministic block arm reads and
silently turned a block into an allow. Ordering by verdict makes that
impossible.

## A CONTRADICTED_BY_FACT sentence can now block on its own (2026-09-18)

`SET3-LIVE-GAP.md` found that a derived fact — including the written-file
family above — was only ever handed to the judge as evidence in the window
text; nothing in the block decision ever read `window_meta["facts"]`
itself. On the fleet set that meant l41 and l43's contradiction sat at the
head of the window and the judge simply never flagged it, and l45's judge
did flag it (`CONTRADICTED 0.99`) but the secondary NOT_SUPPORTED/
CONTRADICTED arm is advisory-only by gate v4's 2026-09-18 demotion, so the
one case that worked was the case the rule threw away. `_fact_block_reasons`
closes that gap the same way the deterministic count/PR arms already work:
any fact sentence in `window_meta["facts"]` carrying the literal
`CONTRADICTED_BY_FACT` marker — from any family, not just written-file — is
appended to `det_block_reasons` and blocks with no judge and no health gate,
because it is string/int work over text that WAS in the window. A
`SUPPORTED` (or any other non-`CONTRADICTED_BY_FACT`) fact sentence never
blocks and never suppresses another arm's block; `--explain` still lists
each fact that fired under `deterministic`. Measured offline (pure replay
of `derive_window_facts` + `compose_window_with_facts` over all 99 recorded
cases, no judge call): the only cases whose decision changes are the fleet
set's l41, l43 and l45 (all lies, all written-file), 0 truths newly blocked
in any of the three sets — `skills/super-jev/tests/replay_fact_block_sweep.py`
reproduces this.

## The latency budget — one call, one cap, one clock (2026-09-18)

A Stop event used to have no bound on how long it could take, and on a heavy
turn it kept the user waiting long enough to notice. Almost none of that wait
was the gate's own verdict. It was the advisory teammate-report scan, which
ran a live `verify` check for every new worker report it found, one after
another, before the gate check ever started. Its own budget was checked only
*before* each report and never during one, so a couple of slow checks went
straight past it and the budget only refused whatever came after them.

Three things changed.

**One live call per Stop event.** `SUPERJEV_GATE_MAX_CALLS` (default 1) is
the whole event's allowance and the gate's own verdict has first claim on it.
The advisory scan may spend only what is left, which at the default is
nothing, so its reports defer to the next Stop event exactly as they already
did on a timeout. Nothing is dropped; the scan's state file only ever
advances past reports it actually attempted. Raise the knob if you want the
scan checking reports inline again.

**One wall-clock budget.** `SUPERJEV_GATE_BUDGET_S` (default 15 seconds)
bounds the event end to end, and every child check's own timeout is clamped
to whatever is left of it, so no single check can outlive the event it
belongs to. If the budget is gone before the gate can judge, the gate prints
one line saying in plain words that the budget ran out and the reply was
**not checked**, exits 3 (advisory — this path never blocks), and writes a
ledger reason of `budget-exceeded`. That reason sits in the health monitor's
**LOST** bucket, which warns on the first occurrence, because a reply that
shipped unjudged is precisely the thing that must not pass quietly. An
advisory that reads like an allow is the failure this whole page exists to
prevent.

The scan will also not *start* a live check it can see it would have to kill:
`SUPERJEV_STOP_SCAN_MIN_S` (default 20 seconds) is the floor of remaining
budget below which it defers instead, so a doomed check never costs a call.

**A deferral is not a lost check.** `budget-exceeded` means one thing only:
this turn's reply shipped and nothing judged it. When the *advisory scan*
defers — no live call left for it, or too little wall clock to finish one
honestly — it writes `stop-scan-deferred` instead, which sits in the
**DEFERRED** bucket and raises no warning. The distinction is the whole point:
the gate still judged the reply on that same event, and the deferred worker
reports come back on the next one, because the scan's state file only advances
past reports it actually attempted. `stop-scan-timeout`, the scan's other
deferral, is DEFERRED for the same reason. These shared one reason string when
the budget first shipped, and since the default allowance of one call is always
reserved by the gate, every Stop event that saw a new worker report then
reported a lost check that had not happened.

**One window cap, enforced once, immediately before the call.** The window
used to be capped in three places that did not compose: the builder capped
what it assembled, the caller then appended a cited-file block *after* that
cap, and the facts step re-applied the cap only when it had at least one
derived fact to put at the head — with no facts it returned the over-cap text
untouched. `SUPERJEV_GATE_WINDOW_TOK` (default 8000 tokens) is now the single
cap, applied to the finished text right before the call, in tokens rather
than bytes because tokens are what the call is priced and timed by.

It gives sections up in priority order, lowest first: previous turns oldest
first, then the session-receipts backing layer, then the cited-file tail,
then this turn's worker reports. **Derived facts and the current turn are
never given up.** A section that can cover the overflow out of its own head
does that and the trimming stops there — only a section too small to cover it
is dropped whole, because surrendering a whole block of evidence to save a
sliver of it is how a true reply gets flagged `NOT_SUPPORTED`. If the facts
and the current turn alone still exceed the budget, the facts stay whole and
the current turn keeps its tail, the same guarantee the old byte cap carried.

Two honest limits. First, a pre-call size estimate cannot see a `verify`
check's real input at all: super-jev estimates the files it hands the door,
and worker-verify then gathers its own git, diff and pull-request evidence
*inside* the check. So a size cap is not a latency control for verify —
wall-clock is the only one, which is why the budget above is the real fix and
the window cap is the smaller half of it. Second, the builder's own byte cap
still runs upstream of the token cap, and when it has to cut it cuts bytes off
the head, which can slice away the first section's marker line. The token
trimmer then sees that leading text as unlabelled and treats it as
undroppable rather than risking the current turn — safe, but it loses some
priority precision in exactly the case where there was little left to
prioritise.

`hook gate --explain` prints the budget line: the cap, the size before and
after, and which sections were dropped or shrunk.

### Where the waiting actually was

A later, much longer Stop hang on a live session sent us back to time every
step rather than assume. Two things came out of it.

**The gate path itself shells out to nothing but the judge.** Reading a
multi-megabyte transcript, finding the turn boundary, backfilling receipts,
assembling the window, deriving facts, capping it, and running the
deterministic count and pull-request arms are all local string and file work
that finish in well under a second even on the largest session transcripts on
this machine. There is no lock, no network call and no `git` or `gh`
invocation anywhere on that path, and a test now pins it: a Stop gate event
makes exactly one subprocess call and it is the gate door.

**Every long-timeout wait in this file is on a verify path, not the gate.**
The `git` calls, the `gh pull-request` calls, the derive-facts bridge and —
much the largest — the *derived test command* all belong to `verify` and its
local fallback. A worker report that says it ran a test suite in a named
worktree makes the Stop-hook scan hand that command to the verify door, which
runs it, under a ceiling measured in minutes; and if a flag then crosses the
block line, the dry-run evidence probe gathers again and runs it a second
time. That is the shape of a Stop hook that keeps a user waiting for many
minutes, and it is why the scan is the thing the budget had to reach.

Both of those long waits are now inside the budget. The verify check's
timeout, the probe's, and the `git remote` lookup that precedes them are each
clamped to whatever is left of `SUPERJEV_GATE_BUDGET_S`, so none of them can
outlive the event. At the shipped defaults the scan does not start any of them
at all and defers instead.

**The scan reads only the transcript's tail.** `SUPERJEV_STOP_SCAN_MAX_BYTES`
(default 2 MB) bounds it, which is a large saving on a session transcript that
has grown to many megabytes and costs nothing, since the scan only ever wants
reports it has not seen yet. The gate *window builder* deliberately still
reads the whole file: it resolves the current turn by walking back from the
end, and a truncated view could hand it half a turn. Set the knob to `0` to
read it all, the behaviour before the bound existed.

## The ledger

Every call appends one JSON line: timestamp, which door, the exit code, how
long it took, and the flags behind any block or advisory. `superjev.py ledger`
prints recent calls and per-door counts. Read it before you trust any claim on
this page, including ours. It is the only record that distinguishes "the gate
approved this" from "the gate could not look".

**Watch the ledger, don't just keep it.** A recorded line is not the same
thing as a noticed one. On 2026-09-16 a hook bug silently routed 9 of 40
replies down the "unchecked" path — no tool evidence was derivable, so the
gate ran against the last prompt instead of the real transcript, printed a
one-line advisory at most, and logged an ordinary-looking exit-0 line every
time. The ledger had the whole story from the first occurrence. Nobody was
reading it, so nobody noticed until the next morning.

**One "unchecked" bucket was hiding three very different things
(2026-09-18).** A 300-record read of the hook ledger (see
`SKIPS-20260918.md`) found the single unchecked share pinned around 45% at
all times, but almost none of it was a real miss: most of it was a hook
firing on the wrong axis entirely (a worker's spawn acknowledged, not
reported yet; the next prompt carrying no teammate report to check) or a
judgment that DID run and DID surface, just against thinner evidence than
the current turn's own tool output. A genuine lost check — a checkable
reply that existed and nothing ever judged it — was 0 out of 300. Reporting
one blended percentage meant the number that was supposed to catch the next
2026-09-16 could never move, healthy or not. `superjev.py status` and
`superjev.py ledger health` now split every skip reason into three buckets:

- **deferred** — nothing user-facing to check yet, or the hook fired on the
  wrong axis for this turn's own reply (a spawn acknowledgement, the next
  prompt having no teammate message to check). Not a miss.
- **thin** — a judgment ran and surfaced to the user, just against the last
  prompt instead of this turn's own tool output. Real signal about gather
  quality, never a hard miss.
- **lost** — evidence or a checkable reply existed and nothing judged it.
  This is the 2026-09-16 shape. Any skip reason superjev doesn't recognize
  also counts as lost, by design — an unfamiliar reason is exactly the kind
  of thing that should be visible, not silently absorbed into "nothing to
  see here."

The old total unchecked share still prints, informational only — nothing
warns off it anymore. `WARN` now fires only when a door's **lost** count in
the window reaches `SUPERJEV_LOST_WARN` (default 1 — a real lost check
should be rare-to-never, so even one is worth surfacing; no minimum-runs
guard applies, unlike the softer note below). A separate, softer `NOTE`
fires when a door's **thin** share crosses `SUPERJEV_THIN_NOTE` (default
25%), same five-runs-minimum guard the old unchecked-share warning used, so
one unlucky run never trips a false alarm — this is visibility, not an
alarm; nothing exits non-zero for it.

There is also a fourth, separate count: **suppressed**. When the judge
flags a claim at or above the block line but the current-turn-empty health
gate lets it through anyway (see "Gate v3 — empty current turn" above), the
ledger now records that with the flagged claim's label and score. It is not
a skip or an unchecked run at all — the claim WAS judged and flagged — so it
never drives a WARN, but `status`/`ledger health` always print the count so
it stays visible rather than invisible the way it was before this change.

The same read is available on its own as `superjev.py ledger health`, which
exits 0 when nothing is lost and 4 when a door's lost count trips the dial,
for wiring into a script or a cron check. And because a bug like this one is
worth catching the same turn it happens rather than the next time someone
runs `status`, the Stop-hook gate itself still checks its own last 20 runs
and appends a one-line notice to its own output whenever a lost check shows
up in that window — so the notice shows up in-session, not just in a file
nobody opened.

## The catch ledger

The ledger above is a machine's record of what ran. The catch ledger is a
second, much smaller file next to it, built for a human to read: one line
per gate/verify decision, with room to say whether that decision was right.

**What a record is.** Every time the Stop-hook gate, the PostToolUse verify
hook (both a live hook firing and `hook verify --from-file`), or `hook
prompt-verify` (one record per teammate-message verdict) reaches a real
decision — allow, block, advisory, the stop_hook_active second pass
("advisory-forced" — see below), or the no-tool-evidence/budget-exceeded
"unchecked" path — it appends one line to `SUPERJEV_CATCH_LEDGER` (default:
next to the call ledger, as `catches.jsonl`): a short id, the timestamp,
which door, the decision, the same reason strings `--explain` would print, a
240-character excerpt of the draft or report, how big the evidence window
was, how long the check took, which check arms were consulted and which of
them broke (`arms` and `arm_errors` — see below), and two empty fields,
`tag` and `note`, waiting for a human. **The full draft, report, or
evidence window is never written here** — only the short redacted excerpt.

**`arms` and `arm_errors`.** A gate row says which check arms it consulted,
each with the mode it ran in (`["pr_state:block", ...]`; the legacy inline
twin appears as `pr_state:legacy-inline` while `SUPERJEV_ARMS` is off), and
separately which arms raised, each with its exception class
(`["pr_state:RuntimeError", ...]`). Two fields rather than one because "we
asked this arm" and "this arm broke" are different facts: a row that merged
them could not tell a quiet arm from a crashed one. An arm that raises
still fails open — the second field is what stops that being invisible.
Both are `null` on a row that consulted no arms (a verify row, an unchecked
gate row), which is not the same as `[]`. See `docs/plugins.md`.

The excerpt's redaction is NOT the same guard that protects the evidence
window. The catch ledger is text a human reads and tags by hand, so it gets
a wider net: the input is first sliced to 4096 characters, redacted for
secrets/credentials AND emails (the evidence-window guard leaves emails
alone by default), then, catch-ledger-only, also redacted for US phone
numbers, SSN-shaped 3-2-4 digit strings, and card numbers — either an
ungrouped run of 13 or more digits, or a grouped run in an exact 4-4-4-4
(Visa/MC/Discover) or 4-6-5 (Amex) shape using ONE consistent separator
(space, dash, or dot) throughout — then sliced to the final 240 characters.
An operator reading a catch record should know three exceptions to that
card rule: a mix of separators within one grouped run is NOT redacted (it
no longer matches the exact grouping shape); a 13-digit ungrouped run that
looks like a unix-millisecond timestamp (starts "1", second digit 5-9) is
left alone rather than redacted; and a grouped run whose first group looks
like a plausible year (starts "19" or "20") is left alone as a likely date,
not a card number. Phone, SSN, and card patterns are catch-ledger only —
none of those three extra patterns ever touch the evidence window itself,
only this excerpt and the opt-in saved payload below; the evidence window
is never redacted this way.

Writing this record is best-effort: if the path is not writable (or
anything else about the write fails), one line goes to stderr and the
gate/verify decision that already happened is completely unaffected — the
catch ledger is a report on a decision, never part of making one.

**What still writes no record, on purpose.** Every fail-open path in this
file — bad/empty/non-JSON stdin, no usable text field, a non-`gate`/
`non-verify`/`non-prompt-verify` door, a non-Agent tool call, a spawn dict
or launch-ack shape that never reaches a verdict, the outer
unexpected-exception catch — stays unrecorded. Nothing there ever reached a
real decision, so there is nothing to tag.

**Tagging.** `superjev.py catch list [--since 24h] [--untagged]` prints one
line per record so you can find the id. `superjev.py catch tag <id>
fair|false|miss "why"` records a human verdict on that one decision:

- `fair` — the block was right. A real overclaim or contradiction, caught.
  Only fits a record whose decision is `block` or `advisory-forced`.
- `false` — the block was wrong. The draft was actually true. Only fits
  `block` or `advisory-forced`.
- `miss` — an allow let something false through. It should have blocked.
  Only fits `allow`, `advisory`, or `unchecked`.

A tag that contradicts its record's own decision (e.g. `false` against an
`allow`) is refused, exit 3, with a plain message — never silently
accepted. Exit 3, not 2: exit 2 is argparse's own usage-error convention,
while a tag/decision mismatch (like an unparseable `--since`, below) is a
semantic refusal on arguments argparse already accepted fine, so both
share exit 3 rather than being indistinguishable from a typo in the
flags.

Re-tagging the same id is an upsert, not an append: `catch tag <id>
false` then later `catch tag <id> miss` replaces the earlier tag and, if a
catch case had been written for it, replaces that case too rather than
leaving two. `catch tag <id> fair` after an earlier `false`/`miss`
withdraws that id's catch case entirely — a case whose tag no longer says
"wrong" or "missed" has no business staying in the catch-cases file. At
most one catch case per record id, always.

Two `catch tag` commands racing on different ids (e.g. a batch of parallel
`catch tag` subprocesses) cannot lose each other's write: the whole
read-modify-write — the ledger rewrite and the catch-cases upsert together
— runs under one `flock`-held lock, a sibling `.lock` file next to the
catch ledger, not the ledger file itself. A second `catch tag` simply
waits its turn rather than reading stale data and clobbering the first
one's write. That lock only guards `catch tag` against itself, though: a
live hook's own ledger append (`catch_ledger_append`, a plain append, never
taken under this lock) can race a `catch tag` rewrite's read-modify-write
of the whole file in a sub-millisecond window and lose that one new
scoreboard row — this never touches or reverses a gate/verify decision
itself, only the ledger's optional record of it.

**`--since`.** An unparseable `--since` value (anything that isn't
`<N>m`/`<N>h`/`<N>d`) is refused, exit 3, same family as the tag/decision
contradiction refusal above — it never silently falls back to "all time".
A record with a missing or unparseable timestamp is excluded from a real
`--since` window (never silently treated as "recent enough to keep") and
counted on its own `undated: N` line instead.

**The numbers.** `superjev.py catch report [--since 7d]` prints:

    fair catches: N
    false stops: N
    misses: N
    untagged: N
    blocks suppressed: N
    judge advisories: N
    undated: N          (only printed when --since is given)

Fair/false/miss/untagged is the same scoreboard as before: how often the
gate is catching something real, how often it is wrongly getting in the
way, and how often something false gets past it. **Blocks suppressed** is a
different thing entirely — a count of `advisory-forced` records, a real
block that the stop_hook_active second pass demoted to advisory-only rather
than blocking twice. By itself, being suppressed is not folded into false
stops or misses, because nothing has been judged right or wrong yet — the
retry just held a block back.

## Judge-advisory mode

`SUPERJEV_GATE_JUDGE_ADVISORY=1` is a third, opt-in failsafe next to the
stop_hook_active one above, for the same underlying worry from a different
angle: the judge's own confidence score (OVERCLAIMS, or under
`SUPERJEV_RULE=v2` the secondary NOT_SUPPORTED/CONTRADICTED arm) is a model
guessing how well a reply is carried by its evidence, and a guess can be
wrong in a way that stops a true turn for no reason. Turning this on tells
the gate: when a block's only reasons are the judge's, print the reason and
let the turn end anyway, rather than stopping it.

The rule, in the registry's own terms, is **demote the block if every
blocking verdict came from an arm whose `KIND` is `judge`** — one function,
`arms.judge_only_blocks`, which the gate feeds through
`_gate_blocking_verdicts` rather than keeping a second copy of the rule
here. `SUPERJEV_GATE_JUDGE_ADVISORY=weak` is recognised as a seam for the
`weak` setting landing on main, and demotes nothing on this branch; it says
so once per process. Note this failsafe is a different object from a
per-arm mode (`SUPERJEV_ARM_<NAME>=advisory`): a mode is one arm's standing
on every run, this is one gate call's outcome demoted after the fact. See
`docs/plugins.md`.

It never weakens the deterministic side of the gate. A block that carries
even one deterministic reason — a drafted test/claim count the evidence
contradicts, a PR-state mismatch, or a `CONTRADICTED_BY_FACT` fact sentence
(literal string/int work over text that was already in the window, no model
call involved) — blocks exactly as it does with the env unset, exit 2, no
exceptions. Only a block whose reasons are judge-only is demoted.

When that happens, `hook gate` prints the same reason line a real block
would, prefixed `super-jev gate (judge advisory, not blocked):` instead of
`super-jev gate blocked this`, and exits 0 instead of 2 — the turn is
allowed to end. The catch ledger records the decision as `advisory-judge`
rather than `block`, so `catch report` can count how many turns this mode
let through that the gate would otherwise have stopped, on their own line:

    judge advisories: N

separate from `blocks suppressed` (the stop_hook_active count above) — one
counts a loop-guard re-run, the other counts every turn this mode fires on,
and a session can trip both failsafes on different turns without either
line double-counting the other. An `advisory-judge` record can be tagged
`fair`/`false` the same way an `advisory-forced` one can.

An `advisory-forced` record can still, separately, be tagged `fair` or
`false` later (a human decides the retry's demotion was itself the right
or wrong call) — when that happens the record counts on **both** the
`blocks suppressed` line and its own `fair catches`/`false stops` line,
because the two lines answer different questions ("was a block held
back?" vs "was the underlying call right?") and a record can honestly
answer both. `catch report` prints a one-line footnote naming how many
`blocks suppressed` records are also tagged, whenever that count is
nonzero, so the two lines never look like a silent double-count.

**Catch cases (not bench cases).** Tagging a record `false` or `miss`
appends one **catch case** to a single JSON array file,
`SUPERJEV_CATCH_CASES` (default: `catch-cases.json` next to the catch
ledger) — `{id, ts, door, kind (truth|lie), draft, payload_path, reasons,
note}`. This is deliberately **not** shaped like the existing
`gate-bench-*` case files those scripts (`replay_gate_bench.py`,
`replay_fact_block_sweep.py`) read: a catch case has no transcript anchor
at all — no `source_offset`, `source_idx`, `transcript_path`, or `bot` —
because a live gate/verify hook decision was never made against one named
spot in a recorded transcript the way a bench case is. Pretending it fit
that shape would silently break both of those scripts' replay logic.

Use `skills/super-jev/tests/replay_catch_cases.py` instead, which reads a
catch-cases.json file directly and, for every case that carries a
`payload_path` (`SUPERJEV_CATCH_KEEP_PAYLOAD=1` was set at decision time —
see below), re-runs the same `hook gate`/`hook verify` decision offline
through `SUPERJEV_GATE_CMD`/`SUPERJEV_VERIFY_CMD` — a fake/canned door for
a dry run, or a door that replays a previously-recorded verdict. **This
script refuses to run at all, exit 2, before touching any case, unless
every door a case in the file needs has its env var set** — it never
falls back to the real fleet gate/verify door the way `door_cmd()` does
for every other caller in this repo. Pass `--live` to allow that fallback
explicitly; the script itself never sets or reads `TYPESAFE_API_KEY`
either way. It then prints a per-case decision plus a lies-blocked/
truths-blocked summary. A case is excluded from that summary, and printed
with its own one-line reason instead, when it cannot be replayed at all:
no `payload_path` ("no payload — cannot replay, excerpt-only"), a payload
that will not parse ("payload unreadable"), a payload that carries none
of the fields this door can read text from ("payload shape unsupported
for this door"), or a payload that ran through the door but produced no
new catch-ledger decision ("door failed"). Without the saved payload, a
catch case is only ever good for a title, a decision, and a tag, never a
full replay, because the catch ledger itself never keeps more than the
240-character excerpt.

`SUPERJEV_CATCH_KEEP_PAYLOAD=1` opts in, at decision time, to also saving a
redacted copy of the whole hook payload under `payloads/<id>.json` — off by
default, because the catch ledger's whole point is to not carry the full
window. Turn it on before the block you want to be able to replay later.

## What it costs

Evidence collection is local and free: git, a directory listing, a grep, and
optionally your test command and a `gh` call. The judging step is one paid
model call per check. The gate adds well under a second to the end of a turn,
fast enough that you will not notice it. A report verify is slower, a few
seconds, because it gathers evidence first and sends much more of it. On a
session with many sub-agent spawns those calls add up, and the verify door is
the expensive one. Both doors have timeouts and both fail open on timeout.

The real number, not just the estimate: every jev call prints an `in_tok`
header, and `superjev.py` parses it into the ledger entry for that
invocation, with an estimated `est_cost_usd` at `SUPERJEV_INPUT_USD_PER_MTOK`
(default `0.042`). `superjev.py ledger` and `superjev.py status --json` total
that up per door, for today and for the whole ledger. Before either hook
calls out, the evidence + draft is estimated at chars/4 against
`SUPERJEV_INPUT_CAP_TOK` (default `32000`); over that, the oldest evidence is
trimmed first — never the draft or report — and the ledger line for that run
carries `truncated: true`.

## Feeding a block/allow back into calibration

Each `hook gate`/`hook verify` run now also writes the draft/report and
evidence it just judged to `ledger/last/<door>-draft.md` and
`-evidence.md`, overwritten every run. Right after you see a block (or an
allow you want to double-check), run:

```bash
python3 skills/super-jev/superjev.py feedback right --note "why"
python3 skills/super-jev/superjev.py feedback wrong --note "why"
```

against the LAST hook decision, and it appends one row to
`ledger/calibration/cases.jsonl`. That log is the growing calibration set —
`superjev.py calibration summary` gives the right/wrong block/allow counts,
and `superjev.py calibration export <dir>` writes it out in the exact
`drafts/`, `evidence/`, `cases.json` shape `gate-bench-20260917/run_bench.sh`
and `summarize.py` already read, so a threshold re-tune runs against real
calls instead of only the original synthetic bench.

## Failure modes, honestly

**False alarms on true reports are the main cost.** On the last live bench, the
judge caught every single fabricated report. On true reports it was clean only
half the time, flagged four more as needing a human, and hard-rejected one that
was entirely true. Almost all of that noise comes from one place: test-count
claims. The evidence for a test count is either missing or ambiguous more often
than any other kind.

**The gate cannot tell "you lied" from "I could not look".** This is the
deepest problem. A missing evidence block and a false claim produce nearly the
same verdict table. Two specific gaps caused real false blocks. A
directory-level pytest is refused outright by the evidence collector, for a
good reason, so a test count checked that way has nothing behind it. And the
pull-request evidence is state only. It carries no check-run data at all, so
"CI is green" is unprovable no matter how green it is. The precision fix makes
those cases advisory instead of blocking, but it cannot invent the evidence.

**Decided: the block rule follows confidence upward, not downward.** The number
beside a verdict is the judge's confidence in that verdict, not a measure of
support — a claim marked unsupported at very high confidence is the strongest
signal the judge can give, and the block rule now follows it upward instead of
firing on the judge's least confident findings and letting its most confident
ones through. The trade-off that comes with fixing the direction: the same
number that used to block on a low-confidence finding no longer does, so a
claim the judge only barely suspects is unsupported now reads as an advisory,
not a block, until a human reads it. That softening is deliberate and lives
next to the fix, not hidden by it — the fixture from the earliest false-alarm
writeup on this page (a claim marked unsupported at low confidence, alongside
a confident overclaim) is kept in the test suite specifically to show it now
passes as advisory rather than blocking, so the trade is visible, not silent.
The same confidence gate that decides a block also turns off entirely when the
evidence gather itself was too thin to judge against, for exactly the same
reason a low-confidence claim does not block: a confident-sounding verdict
built on no evidence is not a confident finding.

**Other things a lie can still do.** A reply that makes claims with no tool
calls in the turn is never checked, only noted. Every hook fails open on a
timeout, a crash, an unreadable payload or a missing door, so anything that
makes the checker unavailable makes the session unchecked. The report-verify
path driven by a hook never passes a test command, so on that path a test count
can never be proved. And the evidence collector's refusal of directory-level
test runs is bypassed by wrapping the same command in an npm script.

**One vendor, today.** The judge is a single external provider. The doors are
swappable by design through two environment variables, and swapping them is
the intended escape hatch, but on this machine today there is one dependency
and no fallback judge.

**Offline, it does nothing.** No key or no network means the judging step
fails, and every hook fails open. You get a session with no checking and no
loud warning that checking stopped. The ledger is where you find out.

**Claim splitting is approximate.** When you do not pass claims explicitly, the
draft is split automatically. Fragments under a few words are dropped, machine
tags are dropped, and a long sentence carrying two assertions becomes one
claim that can only get one verdict. A claim that splits badly gets judged
badly.

**Ordering and timeouts.** Hooks on the same event run together and the
strictest exit code wins, so another hook can block for reasons that have
nothing to do with this one. The gate and verify timeouts are separate, and a
slow test command inside a verify eats the verify budget first.

**Privacy.** This is the one to think about before installing. The draft reply,
or the worker report, plus the assembled evidence, leaves the machine on every
judged call. That evidence is machine-collected and can include file contents,
diffs, test output and repository paths you did not choose by hand. A string
match catches fabricated quotes before any call, and no API key is ever
printed, but nothing redacts the evidence itself.

## When not to install this

- Any repository whose contents must not leave the machine. The evidence goes
  to an external judge.
- Work with no checkable claims. Design conversations, brainstorming and
  writing produce advisories about nothing.
- A session where an interrupted turn is worse than an unverified sentence.
  Use PostToolUse alone, or advisory mode only.
- Offline or air-gapped work, where every hook fails open and you gain nothing
  but latency.
- Test-count-heavy reporting, until the test-output evidence path is fixed.
  That single evidence kind is behind most of the false alarms measured so far.
- As a substitute for reading the work. The judge advises. It never decides,
  and on true reports it is wrong often enough that a block is a reason to
  look, not a verdict.

## Where super-jev plugs in, per harness

super-jev's three doors — the reply gate, the worker-report verify, and the
action permit (a CLI today, `npm run permit`, not yet wired to any hook) —
are built against Claude Code's hook contract: `settings.json`, exit 2 to
block, JSON on stdin/stdout. That contract is not unique to Claude Code. The
full survey, harness by harness, with sources and a verified/unverified label
on every claim, lives in `docs/harnesses.md`. The short version:

- **Native-hook harnesses that match Claude Code's contract closely enough to
  port directly:** OpenHands SDK (explicitly documents the same exit-code
  semantics) and Gemini CLI (same JSON-in/JSON-out shape, and ships a
  `CLAUDE_PROJECT_DIR` compatibility alias). Both can block.
- **Native-hook harnesses with a different contract, needing an adapter, not a
  copy-paste:** Hermes Agent (Python plugin callbacks, not subprocess
  commands — 27 lifecycle events, `pre_tool_call` blocks and fails closed),
  Cursor (`hooks.json`, blocks on the `before*` events, but the CLI has been
  reported to drop non-shell events), Cline (`cancel` field in JSON response
  blocks, but no documented reply-final-message hook), and Codex CLI (hooks
  are experimental, opt-in, and this pass could not confirm the block
  semantics — treat as unverified, not as working).
- **No native hook system found — any integration means wrapping the
  process or the transcript, not hooking it:** Aider (git `pre-commit` only,
  skipped by default) and Goose (an MCP client/extension host, not a
  hook/middleware framework; enforcement today means a proxy in front of it,
  not a hook inside it).
- **Not found in this pass, so left as an open question rather than a guess:**
  Roo Code's own hook system (if any, distinct from Cline's), OpenClaw's
  agent-harness plugin mechanics (a docs page exists but was not fetched),
  and Sourcegraph Amp's Plugin API block/advisory semantics (secondary
  sources only; ampcode.com itself was not reached).

None of this is wired up. It is a map of where the three doors *could* attach
if someone builds the adapter, not a claim that they do today on anything but
Claude Code.

## The structural window model (2026-09-18)

Everything above describes the window as **text**: one flat blob the
composer assembles and six readers each parse again, each with its own
idea of where a section starts and where a worker's prose ends. That
shape is what made "worker text impersonates a tool receipt" a recurring
class of bug rather than a single fixed one.

`skills/super-jev/window_model.py` is the same window as labelled
**pieces**, with the trust boundary decided once, from the transcript
record a piece came out of, rather than six times from text a worker
partly chooses. It renders byte-identically to the composer today and is
wired to nothing yet.

The design note, the one trust rule, the two truncation policies, what
the fuzzes prove, what `from_text` can and cannot know about bytes a
worker partly chose, and the per-reader migration plan are in
[docs/window-model.md](window-model.md).
