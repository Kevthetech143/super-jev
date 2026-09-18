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
| Pick the skill/tool for a request | `fetch` | `python3 $S fetch "<plain request>" --catalog catalog.json --json` | top-k catalog ids with confidence, or `{"noMatch": true, ...}` when nothing clears the floor | a clear top pick above the 0.80 floor is usable as a suggestion. `noMatch: true` means ask a clarifying question rather than guessing | **experimental — do not route silently on this alone.** It beats guessing but has not yet cleared its own admission bar; treat its top pick as a suggestion a human or the routing keyword table can override |
| Build/validate a catalog | `fetch` + hand-editing | build `catalog.json` as `{id, text, utterances?, negatives?, tags?}` per entry, then `python3 $S fetch "<a request you know the right answer to>" --catalog catalog.json --dry-run --json` to see the call plan and cost before spending anything | the dry-run plan: record count, call count, estimated tokens | use this to sanity-check a catalog's size and cost before a live run; there is no separate "validate" door — dry-run is the check | **advisory only** — a plan, not a correctness check |
| Run the benches | `bench` | `python3 $S bench --dry-run --json` (plan and cost, no network) or `--stub` (offline synthetic run) or live (needs `TYPESAFE_API_KEY`) | the run plan (call count, token estimate) or, live, the harness's own accuracy/coverage/cost report | `--dry-run` never touches the network and always exits 5 without a key so nothing downstream reads it as a completed bench. A live bench is the only source of a real number for any door above | **the plan is trusted (pure arithmetic); a live number is only as good as the fixtures it was measured against** |
| Read the ledger | `ledger` | `python3 $S ledger -n 20` | the last N calls (door, argv, exit code, timing) and a per-door count | this is the only record that tells you a door actually ran versus failed open silently — read it before trusting any claim a door made, including this page's own claims | **trusted as a factual log of what ran; it is not a judgment about correctness** |
| Install the hooks | none — this is config, not a door | copy the snippet in `docs/hooks.md` into `~/.claude/settings.json`, pointing `SUPERJEV_GATE_CMD`/`SUPERJEV_VERIFY_CMD` at your own claim-gate/report-verify tools | nothing prints until a hook fires; then `gate`/`verify` output appears inline in the session, and a line lands in the ledger | start with `PostToolUse` only — it is advisory, cannot interrupt a turn, and has the clearest payoff. Add `Stop` once you trust it not to loop (a `stop_hook_active` guard exists but read the loop-guard section of `docs/hooks.md` first) | **advisory-only rollout recommended; not proven safe to block on yet** — see §4 |

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
Command and confirmed real output:
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
 {"id":"pay-coned","text":"pay the Con Edison electric bill"},
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
    "stderr": "Top 3 of 3: amazon-return-authorize=0.67, stock-status-report=0.33, pay-coned=0.33\nGate: below floor 0.80 — Did you mean one of: amazon-return-authorize, stock-status-report, pay-coned?\ncalls: 1\n"
  }
}
```
Note this real run: the top score (0.67) is below the 0.80 none-gate floor,
so the door itself flags this as a `noMatch`-shaped result and offers a
"did you mean" list instead of picking one — exactly the behavior you want
when nothing is confident. Next step on a real `noMatch`: ask the clarifying
question the door hands back, don't guess. On a confident top pick, treat it
as a suggestion, not a routing decision — see §4.

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
- **The ledger is local, plaintext, and gitignored.** It fills up with real
  use and is not meant to be committed; it exists so a person (or the next
  agent) can find out what actually ran, distinct from what a summary claims
  ran.

## Related pages

- [`skills/super-jev/SKILL.md`](../skills/super-jev/SKILL.md) — the full
  reference: every flag, every exit code, the `ask` routing table, the hook
  contract in detail.
- [`docs/hooks.md`](hooks.md) — the three Claude Code hooks, the wiring
  snippet, block-versus-advisory, the loop guard, and the measured failure
  modes behind §4 above.
- [`docs/harnesses.md`](harnesses.md) — where these hooks could attach on
  harnesses other than Claude Code, verified against each harness's own docs
  where possible.
- [`docs/wishlist.md`](wishlist.md) — the seven items this repo is building
  toward, and the honest status of each one today.
- [`docs/lessons.md`](lessons.md) — what live use on real drafts and real hook
  payloads has taught us: feed rules, hook lessons, and the calibration loop.
