# The playbook: which door for which job

super-jev is a collection of callable command-line doors — small, testable
tools an agent or a person can drive from a shell — plus one manual for using
them well. It is not a library you import and not a service you call over the
network. Every door is a subprocess: you run it, it prints one line (or one
JSON object with `--json`) and an exit code, and you act on that.

This page is for someone meeting super-jev cold, human or agent. It answers
one question per section: which door do I run for this job, what comes back,
and what do I do with the answer.

**Where things live.** `skills/super-jev/superjev.py` is the one front door —
every job below goes through it. `skills/super-jev/SKILL.md` is the full
reference (every flag, every exit code, the hook contract). This page is the
short path in: a table, one worked example per door, and what the fleet has
actually learned running these doors for real.

A note on words versus numbers: this page describes accuracy and reliability
in plain language, never as a percentage or a count. The provider behind the
judge restricts publishing performance figures, and the harness's own
`bench` output — not this doc — is where a measured number belongs.

## 1. Job → door → command → verdict → what to do

All commands below assume `S=skills/super-jev/superjev.py` and are run from
a checkout of this repo (so `sweep`, `fetch`, `bench`, `permit`, `chain` find
their npm scripts without any env var).

| job | door | command | what comes back | what each verdict means | trust level today |
|---|---|---|---|---|---|
| Check my own reply before sending | `gate` | `python3 $S gate <evidence files...> --draft reply.md --json` | one verdict per claim, rolled up to CLEAN / READ / REJECT | CLEAN: every claim is carried by the evidence, send it. READ: something is red or under the 0.80 line, a person reads the source before it goes out. REJECT: the evidence disproves a claim, or a quoted line is not actually in the file — do not send | **trusted for loud, confident lies; not trusted alone on a quiet single-claim lie** — see §4 |
| Check a worker's report | `verify` | `python3 $S verify report.md --worktree /path/to/work --test-cmd "npm test" --json` | CLEAN / READ / REJECT plus the evidence table it gathered (git state, path listing, grep, test output) | CLEAN: report matches what the machine can see. READ: a claim is thin or unproven (often a test-count claim with no `--test-cmd` given) — read it yourself. REJECT: a path doesn't exist, a number doesn't match, or a quoted diff isn't real | **trusted when it gathers evidence directly; discount test-count claims when no `--test-cmd` was passed** |
| Is this action safe to run alone | `permit` | `python3 $S permit --snapshot action.json --json` | one of `safe_to_auto` / `needs_approval` / `refuse` | safe_to_auto: proceed without a human. needs_approval: stop, a human signs off first. refuse: do not run this, by policy or by the action's own nature (a hard code rule blocks destructive patterns before any model call runs) | **trusted for the hard-coded refuse class (irreversible/destructive); the model's own safe/needs_approval split is newer and less proven** |
| Is the evidence chain complete before I answer | `chain` | `python3 $S chain --spec spec.json --json` | per-case accepted answer, or a block naming exactly which required role is missing | a case with every required document present gets asked the one real question and answered. A case missing a required role (e.g. no policy doc linked) is blocked in code, before any model call, and the door names the missing role | **trusted** — the completeness check is pure code, not a model judgment, and runs the same in `--dry-run` |
| Pull facts out of a pile bigger than one call | `sweep` | `python3 $S sweep records.jsonl --questions questions.json --out dir --json` | a coverage manifest (every record accounted for), `results.jsonl`, `report.md` | manifest `complete: true` means nothing was silently skipped; each record is `accepted` (every question cleared the gate) or `for review` (something under the gate) | **trusted for coverage** (nothing dropped); **treat individual answers as advisory** near a chunk boundary — the same fact can score differently depending which batch it landed in |
| Pick the skill/tool for a request | `fetch` | `python3 $S fetch "<plain request>" --catalog catalog.json --json` | top-k catalog ids with confidence, or `{"noMatch": true, ...}` when nothing clears the floor and margin | a clear top pick that clears both the 0.60 confidence floor and the 0.10 margin over the runner-up is usable as a suggestion; a confident-looking pick with an almost-as-confident runner-up still gets gated, since a crowded top-1 is a guess too. `noMatch: true` means ask a clarifying question rather than guessing | **experimental — do not route silently on this alone.** It beats guessing but has not yet cleared its own admission bar; treat its top pick as a suggestion a human or the routing keyword table can override |
| Build/validate a catalog | `fetch` + hand-editing | build `catalog.json` as `{id, text, utterances?, negatives?, tags?}` per entry, then `python3 $S fetch "<a request you know the right answer to>" --catalog catalog.json --dry-run --json` to see the call plan and cost before spending anything | the dry-run plan: record count, call count, estimated tokens | use this to sanity-check a catalog's size and cost before a live run; there is no separate "validate" door — dry-run is the check | **advisory only** — a plan, not a correctness check |
| Run the benches | `bench` | `python3 $S bench --dry-run --json` (plan and cost, no network) or `--stub` (offline synthetic run) or live (needs `TYPESAFE_API_KEY`) | the run plan (call count, token estimate) or, live, the harness's own accuracy/coverage/cost report | `--dry-run` never touches the network and always exits 5 without a key so nothing downstream reads it as a completed bench. A live bench is the only source of a real number for any door above | **the plan is trusted (pure arithmetic); a live number is only as good as the fixtures it was measured against** |
| Read the ledger | `ledger` | `python3 $S ledger -n 20` | the last N calls (door, argv, exit code, timing) and a per-door count | this is the only record that tells you a door actually ran versus failed open silently — read it before trusting any claim a door made, including this page's own claims | **trusted as a factual log of what ran; it is not a judgment about correctness** |
| Install the hooks | none — this is config, not a door | copy the snippet in `docs/wire-into-claude-code.md` into `~/.claude/settings.json`, pointing `SUPERJEV_GATE_CMD`/`SUPERJEV_VERIFY_CMD` at your own claim-gate/report-verify tools | nothing prints until a hook fires; then `gate`/`verify` output appears inline in the session, and a line lands in the ledger | start with `PostToolUse` only — it is advisory, cannot interrupt a turn, and has the clearest payoff. Add `Stop` once you trust it not to loop (a `stop_hook_active` guard exists but read the loop-guard section of `docs/wire-into-claude-code.md` first) | **advisory-only rollout recommended; not proven safe to block on yet** — see §4 |

## 2. One worked example per door

Each example below was actually run against this checkout, offline
(`--dry-run` or `--stub`, no live TypeSafe call), to confirm the command and
the shape of what comes back.

### gate

Evidence file `evidence.md`:
```
The order O100 was placed on 2026-08-15 and shipped on 2026-08-20, five
days later than the promised 2-day handling window.
```
Draft `draft.md`:
```
Order O100 shipped five days late, on 2026-08-20 against a 2-day promise.
```
Command:
```bash
python3 $S gate evidence.md --draft draft.md --json
```
Live output shape (confirmed against `SKILL.md`'s own documented example —
this needs a real `SUPERJEV_GATE_CMD` and network, so it was not re-run live
for this page):
```json
{
  "door": "gate",
  "verdict": "CLEAN",
  "exit_code": 0,
  "summary": "CLEAN — every claim is carried by the evidence at or above 0.80. Send it.",
  "details": {"stdout": "...", "stderr": ""},
  "would_run": ["python3", "/path/to/jev.py", "evidence.md", "--kit", "reply", "--draft", "draft.md"]
}
```
Next step: exit 0 / CLEAN, send the draft. Any other verdict, read `details`
for the per-claim table before sending anything.

### verify

Report file `report.md`:
```
COMPLETE: fixed the off-by-one in sweep-cli.ts, added a test, npm test
passes (312 tests).
```
Command:
```bash
python3 $S verify report.md --worktree /path/to/checkout --dry-run --json
```
Confirmed real output (run against this checkout, `--dry-run` so no model
call was made, evidence gathering only):
```json
{
  "door": "verify",
  "verdict": "CLEAN",
  "exit_code": 0,
  "summary": "CLEAN — every claim in the report is carried by the evidence.",
  "details": {"stdout": "... git status, git log, ls, wc -l, grep, and \"NO TEST WAS RUN\" because --test-cmd was not given ..."}
}
```
Note what actually happened here: the door found no such file as
`sweep-cli.ts` at the path it inferred (the real file lives at
`src/sweep-cli.ts`) and printed `MISSING`, and because no `--test-cmd` was
passed it printed `NO TEST WAS RUN. Every claim about a test count ... is
UNPROVABLE.` `--dry-run` collects evidence but does not judge, so this
particular run never reached CLEAN/READ/REJECT — pass `--test-cmd` and drop
`--dry-run` for the door to actually judge. Next step: read the path listing
and grep results in `details.stdout` before trusting a claim about a file
that "MISSING" flags.

#### Frame evidence as derived facts first

The reason `verify` trusts "the report matches the machine" is that it never
hands a judge raw command output and asks it to read. **Every claim is
answered by exactly one kind of evidence, and the answer is computed in code
before any judge sees it:**

| the claim is about | what answers it |
|---|---|
| a test count | the test run's own summary lines, not a commit subject or a changelog line elsewhere in the evidence |
| a path's existence or length | a directory listing and a line count on that exact path |
| being tracked / checked in / committed | the git index (`git ls-files`), never just "the file is on disk" |
| a commit | whether that hash is an object in the repository |
| a branch | whether a branch of that name exists, local or remote-tracking |
| a push | the real ahead/behind count against the upstream, not the word "pushed" |
| a diff size | `git diff --shortstat` against the stated base |
| a pull request's state or checks | `gh pr view` / `gh pr checks`, which are two different questions |
| a live external validation | nothing in this pattern can ever answer yes — every source here is a local read-only command |

Once every atom above is turned into one plain sentence — a **derived
fact** — a claim that a fact flatly contradicts is settled right there, at
confidence 1.00, and never sent anywhere. Only what is left over needs a
judge at all. `verify`'s own worker-verify door does this internally (see
`~/.claude/skills/worker-verify/SKILL.md`, "HOW EVIDENCE IS FRAMED"); this
repository carries the same pair of pure functions, `deriveFacts` and
`preRules`, in `src/experimental/derive-facts.ts`, so any harness — not just
worker-verify — can read evidence the same way. `verify`'s door-absent
fallback (no `worker-verify` installed and `SUPERJEV_VERIFY_CMD` unset) uses
exactly this: it gathers a small git/test evidence set itself and runs it
through `src/experimental/derive-facts-cli.ts` before it will settle anything, printing
the DERIVED FACTS block first, then the pre-rule verdicts. It never calls a
judge, so it can say `CONTRADICTED_BY_FACT`, but it can never say CLEAN — an
unsettled claim there stays READ, unverified, not vouched for.

### permit

Snapshot `action.json`:
```json
{"action": "delete file", "target": "/tmp/foo", "reversible": false}
```
Command:
```bash
python3 $S permit --snapshot action.json --dry-run --json
```
Confirmed real output — `--dry-run` prints the question it would ask, no
network:
```json
{
  "door": "permit",
  "verdict": "RAN",
  "exit_code": 0,
  "details": {
    "stdout": "Would ask one permit question for \"delete file\" on \"/tmp/foo\". { \"questions\": { \"permit\": { \"criteria\": { \"safe_to_auto\": \"...\", \"needs_approval\": \"...\", \"refuse\": \"...\" } } } }",
    "stderr": "Dry run: nothing was sent, no provider was called."
  }
}
```
Drop `--dry-run` (with `TYPESAFE_API_KEY` set) for a real answer. Next step:
`refuse` — stop. `needs_approval` — hold for a human. `safe_to_auto` — proceed,
but see §4 on how far to trust this split today.

Underneath this, `npm run permit` (the CLI this skill subcommand wraps) now
runs a second, code-only pre-rule layer before the judge is ever asked —
default on, `--no-prerules` turns it off. It parses the verb and the
concrete thing being acted on (`rm -rf PATH`, a force-push branch, `drop
table`, a disk/volume target for `format`, a dollar amount + payee), and
with `--cwd`/`--worktree` reads live facts about it (does it exist, is it
tracked and committed in git, is it inside the task's own worktree). Two
things settle with zero model calls: money over `--money-threshold` (default
$200) or a new payee (`--new-payee`) always needs approval, and a
destructive verb refuses outright unless the target is a tracked,
committed file inside the given worktree — recoverable from git's own
index, so that one case is passed to the judge with that fact attached
instead of refused sight unseen. It can never settle a verdict to
`safe_to_auto`, only make it more cautious or leave it for the judge.
`--explain` names which rule fired, if one did. See the "Pre-rules" section
of the main README for the full rule list.

### chain

Spec `spec.json` (a ticket linking to an order linking to a policy):
```json
{
  "question": "is this return eligible?",
  "options": {"eligible": "meets policy", "ineligible": "fails policy"},
  "roles": [
    {"name": "ticket", "match": {"type": "self"}},
    {"name": "order", "match": {"type": "idPrefix", "prefix": "O"}},
    {"name": "policy", "match": {"type": "idPrefix", "prefix": "P"}}
  ],
  "docs": [
    {"id": "T1", "text": "customer wants to return item, order O100", "links": ["O100"]},
    {"id": "O100", "text": "order placed 30 days ago", "links": ["P1"]},
    {"id": "P1", "text": "returns eligible within 30 days"}
  ],
  "cases": [{"id": "T1", "rootId": "T1", "question": "is this return eligible?"}]
}
```
Command and sanitized illustrative output:
```bash
python3 $S chain --spec spec.json --stub --json
```
```json
{
  "door": "chain",
  "verdict": "RAN",
  "exit_code": 0,
  "details": {
    "stdout": "T1: accepted = eligible (all 1 pass(es) agreed on \"eligible\" at confidence >= 0.75 ...)\n  ticket: T1\n  order: O100\n  policy: P1\n"
  }
}
```
Next step: a case with every role present and an accepted answer is done.
Delete one required doc (e.g. drop `P1` or its link) and rerun — the door
blocks that case in code and names the missing role, before any model call.

### sweep

Records `records.jsonl`:
```
{"id":"r1","text":"order O100 shipped late"}
{"id":"r2","text":"order O101 arrived on time"}
```
Questions `questions.json` (note the field is `name`, not `id`):
```json
{"questions":[{"name":"late","instructions":"was the order late?","criteria":{"yes":"the order arrived late","no":"the order arrived on time"}}]}
```
Command and confirmed real output:
```bash
python3 $S sweep records.jsonl --questions questions.json --out out/ --stub --json
```
```json
{
  "door": "sweep",
  "verdict": "RAN",
  "exit_code": 0,
  "details": {
    "stdout": "sweep plan: 2 record(s) x 1 question(s) [late] = 2 cell(s) ... calls: 1 ...",
    "stderr": "Wrote results.jsonl, manifest.json, cost.json, report.md and plan.json to out/\n1 accepted, 1 for review, manifest complete=true\n"
  }
}
```
Next step: check `manifest.json`'s `complete: true` first — that is the proof
nothing was skipped. Then read `results.jsonl`: an `accepted` record cleared
the gate on every question; a `for review` record did not, a person reads it.

### fetch

Catalog `catalog.json`:
```json
[{"id":"amazon-return-authorize","text":"authorize an Amazon return"},
 {"id":"pay-utility","text":"pay the example utility electric bill"},
 {"id":"stock-status-report","text":"per-ticker wheel campaign scoreboard"}]
```
Command and confirmed real output:
```bash
python3 $S fetch "pay the electric bill" --catalog catalog.json --stub --json
```
```json
{
  "door": "fetch",
  "verdict": "RAN",
  "exit_code": 0,
  "details": {
    "stdout": "fetch plan: 3 catalog record(s) ... prefilter: top 8 by local token overlap ...",
    "stderr": "Top 3 of 3: amazon-return-authorize=0.67, stock-status-report=0.33, pay-utility=0.33\nGate: below floor 0.80 — Did you mean one of: amazon-return-authorize, stock-status-report, pay-utility?\ncalls: 1\n"
  }
}
```
This sanitized run illustrates the old floor-only gate (`--floor 0.80`,
before this doc's margin rule existed): the top score (0.67) was below the
0.80 none-gate floor, so the door flagged this as a `noMatch`-shaped result
and offered a "did you mean" list instead of picking one — exactly the
behavior you want when nothing is confident. Under today's default
(`--floor 0.60`, `--margin 0.10`), the same three scores (0.67, 0.33, 0.33)
would clear both: 0.67 is above the 0.60 floor, and the gap to the runner-up
(0.34) is above the 0.10 margin, so this exact run would now be served
instead of gated — that's the lower floor doing its job on a case that
wasn't actually ambiguous. Next step on a real `noMatch`: ask the clarifying
question the door hands back, don't guess. On a confident top pick, treat it
as a suggestion, not a routing decision — see §4.

Free checks first, judge for the rest: before any of that, the door runs
three offline pre-rules over the catalog's own `utterances`/`negatives` —
plain string matching, no call. A request that contains exactly one record's
own unique multi-word trigger phrase, and none of its negatives, is served
straight from the catalog with zero judge calls (`source: "trigger"` in the
JSON, and the manifest/cost both show `calls: 0`). A judge top-1 that hits
one of its own negative phrases gets demoted to review instead of served,
with the reason printed. A request that names no record's trigger at all,
and whose judge top-1 comes back under the floor, is `noMatch`. These are on
by default; `--no-prerules` skips them, and `--explain` prints the facts
they were decided from.

### bench

Command and confirmed real output:
```bash
python3 $S bench --dry-run --json
```
```json
{
  "door": "bench",
  "verdict": "RAN",
  "exit_code": 0,
  "details": {
    "stdout": "Planned run (no network reached, no key required): A / pilot-32 / order forward: 1 call(s), est 8334 input + 960 output tokens ... TOTAL: 76 calls, est 100052 input + 11520 output tokens ..."
  }
}
```
Next step: read the call count and token estimate, decide whether a live run
(needs `TYPESAFE_API_KEY`) is worth the cost before running it for real.

## 3. What a good input looks like, per door

- **`gate` evidence** — actual tool output the agent read this turn: file
  contents, command output, a fetched page. Not a summary of what the agent
  believes the evidence says. If the evidence file is a paraphrase, the gate
  is checking the paraphrase against itself.
- **`verify` reports** — claims with numbers and paths that a machine can
  check: a file path that exists, a test count from a command you're willing
  to pass with `--test-cmd`, a PR number the door can turn into a real
  `gh pr view` check. A report that says "it works" with nothing checkable in
  it gives `verify` nothing to grade.
- **`permit` snapshots** — the actual action, its target, and whether it's
  reversible: `{"action": "...", "target": "...", "reversible": true|false}`.
  Vague actions ("clean things up") and missing targets are exactly the
  inputs the hard-coded refuse rule and the model both have the least to work
  with.
- **`chain` specs** — every document the case actually touches, with real
  `links` between them, and roles defined narrowly enough that a missing
  document is actually missing, not just named differently (`idPrefix`
  matching is literal — `"O"` will not match an id that starts `"ORD-"`).
- **`sweep` records** — one `{"id","text"}` per record, `text` being the
  actual content to judge, not a pointer to it. `meta` is echoed back but
  never sent to the judge — put anything sensitive there, not in `text`.
- **`fetch` catalogs** — real, distinct descriptions per entry. The v2 schema
  (`utterances`, `negatives`, `tags`) scores better than a bare `{id,text}`
  pair; `negatives` (near-miss phrasings that should *not* route here) are
  as valuable as the positive `utterances`.

## 4. What not to trust yet

This section is drawn from real dogfooding on this repo — live runs, not
fixtures — described in words, with no figures repeated here (see the live
bench output for numbers).

- **The gate reliably catches a loud, confident lie** — a fabricated quote,
  an invented number sitting next to nothing that supports it, a claim the
  judge scores as clearly contradicted. **It is much weaker on a quiet,
  single-claim lie**, especially when the true fact and the false one are
  merged into one sentence the claim-splitter treats as one claim, or when
  the disproving fact sits buried deep in a large evidence window. In a real
  calibration run against a deliberately planted set of quiet single-claim
  lies, a sizable share slipped through, and true replies still paid a
  small tax of their own. Read the per-claim table yourself on anything
  that matters; do not treat a CLEAN verdict on a single dense claim as
  proof.
- **A missing-evidence run and a real problem can produce the same red
  table.** The gate and verify cannot always tell "you lied" from "I could
  not look." A directory-level test command that the evidence collector
  refuses outright, or a PR check with no CI state attached, reads the same
  as an unsupported claim unless you recognize the gap yourself. This is why
  the hook shim treats a thin evidence gather as advisory, never a block —
  do the same when reading a door's output by hand: check what evidence was
  actually gathered before trusting a REJECT.
- **`fetch` is explicitly experimental for routing.** It has run real
  holdout benches (never-seen requests) and improved across iterations, but
  it has not yet cleared its own admission bar of beating a hand-written
  routing map on both accuracy and cost. Use its top pick as one input to a
  decision, not the decision.
- **`permit`'s safe/needs_approval split is newer than its refuse rule.**
  The hard-coded refuse class (irreversible, destructive actions) has been
  live-tested and holds. The model's finer split between `safe_to_auto` and
  `needs_approval` is less proven and has shown at least one case of
  over-triggering (a keyword match too eager to call something destructive)
  in real testing — read `docs/wishlist.md` item 5 before leaning on it for
  anything irreversible.
- **The hook shim had real false blocks before recent fixes**, all now
  patched: judging a background agent's launch acknowledgement or its spawn
  brief as if it were a finished report, and a loop where a Stop hook kept
  re-blocking a reply that was being rewritten more carefully each pass.
  Both are fixed in this checkout, but they are evidence that a hook that
  can block deserves a cautious rollout — see the recommendation in the
  table above to start with `PostToolUse` (advisory-shaped by construction)
  before adding `Stop` (which can block).
- **A reply with no tool calls in the turn is not checked, only noted.**
  The gate needs evidence to check against; a turn with none gets a silent
  advisory pass, which looks identical in the ledger to "checked and clean"
  unless you read the ledger's own notes.
- **A declined check must still speak.** A pre-rule can do its job — check a
  claim, find the fact and the claim actually agree once a tolerance or a
  policy is applied — and still hurt you, if the only trace of that work is
  silence. What this looks like: a truth-side claim gets flagged as if it
  were contradicted, evidence for it is technically "present" in the pack
  but never paired with the thing it was meant to settle, or a pre-rule's
  own tolerance (a clock-drift window, a numeric-match check, a labelling
  check) is never surfaced in the prose the judge actually reads. The judge
  only sees the rendered sentences, not the code path that decided not to
  fire; a bare number or a flat unqualified statement sitting next to a
  claim reads as an open contradiction even when the check that produced it
  already cleared the claim. The rule going forward: every free check a
  door runs writes exactly one sentence either way — fired (a stated
  conflict), declined (a stated reason it is *not* a conflict, naming the
  tolerance or policy that applied), or not applicable (nothing was
  checkable, so nothing is said). A door that runs a check and then says
  nothing about the result it got has a hole here, whichever of the three
  outcomes actually happened. See `docs/coverage.md` for where this has been
  wired in and where it has not.
- **Rule on the residue, do not label it unprovable.** This is the sibling
  defect to "a declined check must still speak," and it shows up on claims
  the report actually gets right. The shape: one sentence pairs a core the
  evidence can prove with a trailing clause no local tier can observe — that
  a click *happened*, that repos were *read* rather than merely present on
  disk, that code still *validates* against something, that a message went
  out *via the helper* it named. The door then does one of two things, and
  both cost you: it says nothing about that clause, or it labels it
  UNPROVABLE. Either way the judge has no such label to give back — it only
  has SUPPORTED, NOT_SUPPORTED and CONTRADICTED — so an honest "cannot tell"
  reads as "not proved," and a true report gets flagged. How to spot it: an
  advisory (a READ line, not a hard block) on a report you have separately
  confirmed is true, where the evidence for the flagged claim is present but
  never actually paired with the clause the judge is unsure about. The rule:
  once the core of a claim is backed by a check-shaped fact, write one more
  sentence that rules on the residue instead of naming it — say plainly what
  is observable, name what stands in for the part that is not (a matching
  record, a consistent timestamp, a file present where the claim says it
  would be), and say in so many words that the unobservable clause must not
  count against the claim. Never do this when the core itself is
  contradicted or has no matching fact at all — a planted lie has no backed
  core for this rule to reach, so it keeps its wording and stays caught. A
  companion rule rides with it: never print a contradiction-shaped sentence
  the door itself goes on to retract. Pair a claimed value only with the
  object the sentence actually names it against, not the first candidate in
  the claim, and once a tolerance has already forgiven a gap, do not also
  print the raw comparison line that reads as a flat conflict on its own —
  say only the ruling. See `docs/coverage.md` for where the shared ruling
  helper lives.

## 5. The loop: how you calibrate this yourself

super-jev is not a fixed oracle — it is measured against its own bench, and
the measurement is how you find out whether it's good enough for your use.

1. **Run for real.** Use `gate`/`verify`/`permit`/`chain` on actual work, or
   install the hooks in advisory mode (`PostToolUse` first).
2. **Every call is logged.** `ledger/calls.jsonl` under the skill directory
   records every door invocation — timestamp, argv, exit code, timing, and
   (for hook calls) whether it fired or failed open. Nothing here is
   inferred after the fact; it's what actually ran. Run `python3 $S ledger`
   to read it.
3. **Build a labeled set from real cases.** When you find a case where a
   door's verdict was wrong — a lie it missed, a truth it blocked, a route
   it picked wrong — save the input and the expected answer. This is how the
   fleet's own calibration sets (referenced in `docs/wishlist.md` and the
   bench fixtures) were built: from real misses, not synthetic guesses.
4. **Run the bench against that set.** `python3 $S bench` (live, needs
   `TYPESAFE_API_KEY`) or the fetch/sweep equivalents report accuracy,
   review rate, coverage and cost together, over your own fixtures. This is
   the only place a real number should live — not this page, not a chat
   reply.
5. **Feed misses back into the tool that owns them.** A missed lie is a
   claim-gate or claim-splitter problem, not a super-jev-wrapper problem —
   fix it in the tool behind `SUPERJEV_GATE_CMD`/`SUPERJEV_VERIFY_CMD`. A
   missed route is a catalog problem — add the missing `utterances` or
   `negatives` and rerun `fetch`. Never patch a threshold in the wrapper
   itself; every judgment belongs to the door behind it.
6. **Re-run the bench, confirm the fix moved the number, repeat.** This is
   the whole loop, and it is designed to run indefinitely as real use turns
   up new cases — there is no final, fixed accuracy number for any door
   here.

## 6. Fail-open rules and cost, in words

- **Every hook fails open, always, and logs why.** Empty input, no evidence,
  a subprocess timeout, an unreachable judge, a bad invocation — every one
  of these lets the turn proceed rather than blocking it, and every one
  writes a ledger line saying so. A checker that can silently disappear
  without you finding out is worse than no checker; the ledger is where you
  find out.
- **A door that isn't built exits 6 and names its own wishlist item**,
  rather than failing inside a subprocess with a stack trace. Read the exit
  code, not the prose, when scripting against this.
- **Offline doors cost nothing.** `--dry-run` and `--stub` on every door that
  supports them touch no network and need no API key — use them to check a
  command's shape or a catalog's size before spending anything live.
- **A live call costs one paid model call per judged unit** — one gate check,
  one verify, one sweep cell (batched where the budget allows), one fetch
  request. `bench --dry-run` prints the estimated call count and token
  figure for a planned run before you commit to it.
- **Nothing here reads a secret file.** No login file, no credential file —
  the API key is passed through the environment to the child process only,
  never printed, never logged. `status` reports whether a key is present as
  `yes`/`no` and nothing more.
- **What never reaches the judge.** Before any evidence — a gate window, a
  verify fallback's gathered files and test output, or a raw request/record
  handed to `superjev.py guard` directly — is packed for the judge, it goes
  through one guard, in plain words: a path is never opened at all if it
  names the fleet login vault, a `*-secret.md` file, an `.env` file, anything
  under a `profile/` or `documents/` folder, a browser or playwright session
  file, a private key or `id_rsa`, or anything naming a token or credential —
  it is reported as skipped instead, and that silence is treated as
  "unprovable", never read as "the claim about it must be false". Separately,
  whatever text *does* get packed has anything that looks like a live secret
  masked out first — an API key, a GitHub or Slack token, an AWS key id, a
  bearer token or an `Authorization:` header — while a git commit hash or a
  file checksum is left alone, because that is evidence, not a secret. `guard`
  and `verify --explain` report how many paths were skipped and how many
  redactions were made on any given call, so this is checkable, not just
  claimed.
- **The ledger is local, plaintext, and gitignored.** It fills up with real
  use and is not meant to be committed; it exists so a person (or the next
  agent) can find out what actually ran, distinct from what a summary claims
  ran.

## 7. Onboarding a new area

`verify` today only knows how to check a coding report: a commit, a pushed
branch, a tracked file, a test count, a PR's own checks. Bringing the same
kind of checking to a different area — research, a browser task, a config
change, a sent message, a payment — is not a new judge and not a new
harness. It is the same recipe, run again, learned from doing it once for
real tonight.

1. **List the claim types an agent actually makes in that area.** Not every
   sentence it could write — the specific, checkable things it tends to
   claim. For a coding report that's a test count, a commit pushed, a file
   tracked in git, a PR's checks. For research it's a source existing and a
   quote matching what the source actually says. For a browser task it's
   the state of a page after an action. For a config change it's a diff
   existing plus a backup of what it replaced. For a sent message it's a
   delivery receipt.
2. **For each claim type, name the free check** — one command or one file
   read, no model call — **and the one-sentence fact it yields.** "The
   report says 312 tests passed; `npm test` printed 312 passed" is a free
   check. "The report says the PR is up; `gh pr view` printed its real
   state" is a free check. If there is no free check for a claim type yet,
   that is the actual gap to close, not a reason to skip straight to asking
   the judge.
3. **Put the derived facts first in the evidence, and label the raw command
   output as backing.** A judge (or a person) reading "git log shows commit
   a1b2c3d on this branch, pushed to origin" decides faster and more
   consistently than one reading a raw `git log`/`git push` transcript and
   having to work out what it proves. The raw output stays in the evidence
   too — as the receipt behind the fact, not the thing being handed over
   first.
4. **Settle by fact what a fact flatly contradicts.** If the free check
   already proves a claim false — the file the report names does not exist,
   the test count in the report does not match what the test command
   printed — that claim is decided right there, for free, before any model
   call. Only the claims a free check cannot settle go to the judge. This is
   the same split `verify` already runs for coding reports: a `MISSING` path
   or a mismatched test count is settled by fact; everything else goes to
   the claim gate.
5. **Plant lies and truths pulled from real transcripts, and run the bench
   through the real hook path — never only offline.** A bench that only
   calls the checking code directly can pass while the actual hook that
   fires in a live session disagrees, because the hook has its own evidence
   collection, its own scaffolding-stripping, its own fail-open rules in
   front of the same judge. That gap was not theoretical: on 2026-09-16, a
   hook bug silently routed several replies down the "unchecked" path — no
   evidence was derivable, so the gate fell back to checking the last prompt
   instead of the real transcript — and every one of those calls still
   logged an ordinary-looking exit-0 line. The offline bench never saw that
   bug because it never went through the hook. Test the thing that actually
   runs in production, not a shortcut that skips the part that broke.
6. **Ship only what beats the previous live run.** A new claim type, a new
   free check, a reworded question to the judge — none of it goes live on
   the strength of sounding right. Run the bench (real hooks, real
   transcripts) before the change and after, and ship only if the after run
   is actually better, not merely different.
7. **Watch ledger health after a day.** A new area adds new ways for
   evidence collection to come up empty — a source that can't be fetched, a
   page that changed before the check ran, a payment API with no read-only
   endpoint. Read `superjev.py ledger health` (or the ledger-health block
   `status` prints) after the new area has been live about a day, and treat
   a rising unchecked share the same as a wrong verdict: a signal that this
   area's evidence collection needs work, not proof the area is fine because
   nothing got blocked.

### Coverage today

What claim types the `verify` door actually checks today, and what still
needs a free check written before it can join it.

| area | claim types | covered by `verify` today | intended free check |
|---|---|---|---|
| coding | test count, commit pushed, file tracked, PR checks | yes | `git log`/`git status`, a path listing, the test command's own output, `gh pr view` |
| research | source exists, quote matches the source | no | fetch the cited source and grep for the quoted text |
| browser | page state after an action | no | a fresh read of the page (DOM text or a screenshot diff) after the claimed action |
| config | diff exists, a backup of the prior value exists | no | `diff` against the backup file, and a file-exists check on the backup itself |
| messages | delivery receipt | no | the sending API's own delivery/read-receipt or message-id lookup |
| payments | amount, payee, and confirmation id match a real transaction | no | the payment provider's own transaction-lookup call, read-only |

### What the judge is and is not

The judge behind every door here is a fast, consistent scorer: hand it a
claim and the evidence for it, and it tells you whether the evidence carries
the claim. It is not a reader that goes hunting through a pile of context to
find the one line that matters. The more you hand it beyond what actually
bears on the claim, the softer and less reliable its answer gets — a big,
loosely relevant evidence window does not make it smarter, it makes the real
fact harder to find inside the noise. This is exactly why the free checks and
the derived facts above come first: they do the finding, so the judge only
ever has to do the judging.

## Related pages

- [`skills/super-jev/SKILL.md`](../skills/super-jev/SKILL.md) — the full
  reference: every flag, every exit code, the `ask` routing table, the hook
  contract in detail.
- [`docs/wire-into-claude-code.md`](wire-into-claude-code.md) — the three Claude Code hooks, the wiring
  snippet, block-versus-advisory, the loop guard, and the measured failure
  modes behind §4 above.
- [`docs/harnesses.md`](harnesses.md) — where these hooks could attach on
  harnesses other than Claude Code, verified against each harness's own docs
  where possible.
- [`docs/wishlist.md`](wishlist.md) — the seven items this repo is building
  toward, and the honest status of each one today.
- [`docs/lessons.md`](lessons.md) — what live use on real drafts and real hook
  payloads has taught us: feed rules, hook lessons, and the calibration loop.
- [`docs/coverage.md`](coverage.md) — the claim-type → evidence-type table
  for every door (gate, verify, research, config+messages, browser,
  skillpick, permits, the shared atoms library): what free check answers
  each claim type, the derived-fact sentence shape, and an explicit
  NOT COVERED YET list per door.
