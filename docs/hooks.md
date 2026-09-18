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
reading it, so nobody noticed until the next morning. `superjev.py status`
now prints a ledger-health block every time it runs — for the last 50 hook
runs (`--window` to change it), a per-door count of blocked, advisory, and
unchecked/skipped runs, plus the unchecked share, with a `WARN` line and the
most common skip reason once any door's unchecked share crosses 25%
(`SUPERJEV_UNCHECKED_WARN` to change the threshold; a door needs at least
five runs in the window before its share counts, so one unlucky run never
trips a false alarm). The same read is available on its own as `superjev.py
ledger health`, which exits 0 when things look healthy and 4 when they
don't, for wiring into a script or a cron check. And because a bug like this
one is worth catching the same turn it happens rather than the next time
someone runs `status`, the Stop-hook gate itself now checks its own last 20
runs and appends a one-line notice to its own output whenever that running
share is over threshold — so the notice shows up in-session, not just in a
file nobody opened.

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
