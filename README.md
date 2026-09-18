# super-jev

A small TypeScript harness that connects **evidence → Jev judgments → permitted actions → verified outcomes**.

Domain-independent core. Pluggable data sources and tools. Local JSONL traces. Zero runtime dependencies. MIT licensed.

**Status: experimental V0.2.0.** Offline demos and mocked API tests pass. The organizer passed a live Jev smoke test with four synthetic records; the two original demos passed historical live smoke tests. This is an independent community project, not affiliated with TypeSafe AI. It contains no model weights.

## Run in a minute

Requires Node.js 24 or later. Node executes the TypeScript source directly; no install or build step is needed. Runtime tests do not perform static TypeScript checking.

```bash
npm run demo
npm run demo -- --documents
npm test
npm run replay -- runs/demo.jsonl
```

The default evaluator is a scripted fixture, clearly labeled in the console. Service recovery is simulated in memory. Document review writes a report into task state. Neither demo affects outside systems.

To use actual Jev decisions with those same local tools, set `TYPESAFE_API_KEY` in your environment, then:

```bash
npm run demo -- --live
npm run demo -- --live --documents
```

Alternatively copy `.env.example` to `.env`, fill it locally, and run `node --env-file=.env examples/demo.ts --live`. Never commit the filled file.

## What the harness does

1. Tracks a goal's domain state for one bounded run.
2. Calls the domain's `observe` hook to fetch fresh evidence and select context.
3. Sends a batch of typed questions to an interchangeable evaluator.
4. Validates answers, lets domain rules select an action, and checks its permissions and arguments.
5. Executes one registered tool, with cancellation signal and an idempotency key.
6. Reduces the outcome into state and checks success using the domain verifier.
7. Records the sequence for inspection without rerunning effects.

Independent questions belong in one request. If a new question requires a tool result, ask it on the next iteration. A domain supplies action candidates in code; Jev does not generate arbitrary commands or tool arguments.

## Add a domain

Implement `Domain<State>` from `src/types.ts`; see `examples/domains.ts` for two complete implementations.

| Hook | Responsibility |
| --- | --- |
| `observe(state, signal)` | Fetch and select relevant evidence; include current position/stage where needed |
| `questions(state, evidence)` | Return a map of Choice, Score, and/or Noul questions |
| `decide(state, evidence, evaluation)` | Combine answers into an action or stop reason |
| `permit(state, action)` | Enforce domain rules independently of model confidence |
| `tools[name].validate(args)` | Reject malformed tool inputs |
| `tools[name].execute(args, context)` | Perform work; honor the signal and deduplicate externally if needed |
| `reduce(state, action, outcome)` | Update task state from the observed result |
| `verify(state, signal)` | Confirm completion with domain-specific evidence |

```typescript
import { run, Jev, JsonlJournal } from './src/index.ts';
import { recovery } from './examples/domains.ts';

const result = await run({
  domain: recovery(),
  initial: { restarted: false, healthy: false },
  evaluator: new Jev(),
  journal: new JsonlJournal('runs/my-run.jsonl'),
  maxSteps: 10,
  timeoutMs: 30_000,
  maxRequestBytes: 100_000,
  maxRepeats: 2,
});
console.log(result.status, result.state);
```

Terminal statuses are `success`, `stopped`, `blocked`, or `error`. Low confidence is handled by your domain's `decide` hook; there is deliberately no universal trading, repair, or review threshold.

## Boundaries that matter

- The byte budget is a local size guard, **not a tokenizer or guarantee of fitting Jev's token budget**. Select smaller evidence sets for large datasets. Request construction errors or provider rejection end the run.
- One run executes serially. `observe` can consume the latest snapshot from any data feed, but this release includes no WebSocket connector, event queue, high-frequency trading engine, or exchange integration.
- No automatic retries of API calls or tools. An external action can succeed even if its response is lost. A trace with `action_started` and no `action_completed` needs reconciliation before retrying.
- Cancellation stops the loop from scheduling further work. It cannot forcibly stop a plugin that ignores its signal or undo an already submitted action. Plugins run in-process and are trusted code, not sandboxed code.
- Replay is **read-only trace inspection**, not crash recovery or exactly-once execution. State is in memory during the run and recorded to the journal after each successful tool result.
- Default journals include context, answers, and outcomes. Keep them private. Use `redact(event)` for domain-specific redaction; redacted traces may lose diagnostic detail. Provider credentials are not part of trace payloads, and raw error text is omitted.
- The document example verifies creation of a report, not correctness of its judgments. Real-world correctness needs labeled examples, tests, or measured outcomes.
- `jev-latest` can change. Log model identifiers and evaluate again after changes. Confidence is not proof of truth or the probability of profitable trading.

## Source map

```text
src/types.ts       Domain, tool, evaluator and journal contracts
src/loop.ts        Bounded execution loop
src/jev.ts         Jev HTTP adapter and answer validation
src/journal.ts     JSONL persistence
src/replay.ts      Read-only trace inspection CLI
src/organizer.ts   Configurable record classification and review routing
src/cli.ts         JSON organizer CLI with explicit demo/live modes
bench/            Live measurement runner, fixtures and report generator
examples/         Two original domain demos and organizer input fixture
test/             Core behavior and mocked API tests
docs/             Architecture and launch draft
```

## Validation

The suite has 50 tests covering the core loop, response validation, mocked API adapter and CLI response contract, organizer grouping and review policy, bounded input reads, record-field minimization, CLI privacy, output protection, and failed-output cleanup. One live response from `jev-1.13.0`, recorded on 2026-09-17, is checked in as a fixture and asserted by the suite; it confirmed the documented `score` rule and disproved one rule this harness had inferred. Every rule the validator enforces is sourced and labelled in [the provider contract](docs/provider-contract.md). Both original demos and the organizer demo pass offline. Historical v0.1 live smoke tests used three API evaluations and local demo tools; see docs/live-validation.json. One v0.2 live organizer evaluation on `jev-1.13.0` classified four synthetic records as expected and verified grouping and the `other` review queue. See [organizer validation](docs/organizer-validation.md) for results and limits. This is control-flow validation and a small smoke test, not a broad Jev capability benchmark. Native TypeScript execution does not provide static type checking.

The enhancer path has not been measured against live traffic yet. `npm run bench:live -- --dry-run` prints the plan for the measurement that would settle it: six conditions over the 48 labeled synthetic records, 76 calls, with the accepted-error rate as the headline number. See [the measurement runner](docs/bench-live-measure.md) for how to run it and what it cannot show.

## API references

- [TypeSafe API reference](https://docs.typesafe.ai/api)
- [Question primitives and batching](https://docs.typesafe.ai/primitives)
- [Architecture patterns](https://docs.typesafe.ai/patterns)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Useful first contributions: additional domain examples, live API contract checks, static typechecking setup, redaction helpers, feed adapters, and a persistent checkpoint/reconciliation design.

## License

[MIT](LICENSE). The software license does not grant access to Jev; use of the hosted model is subject to TypeSafe's own terms.


## New in v0.2: organize records

```bash
npm run organize -- examples/organizer.json --demo
npm run organize -- your-records.json --live --out organized.json
```

Live mode requires `TYPESAFE_API_KEY`; it transmits your selected records to TypeSafe. The free scripted demo only accepts the bundled fixture. Output contains categories, confidence values, grouped IDs, and a review queue. Low-confidence and `other` items are excluded from automatic groups. Original files are never changed. Existing output files are never overwritten.

For machine-readable stdout, invoke `node src/cli.ts organize examples/organizer.json --demo` directly (npm prints script banners). Output files are created with owner-only permissions. The CLI does not save run logs; keep your own input and output files outside the checkout to avoid committing private records.

Define your own categories and records using [the sample input](examples/organizer.json). Agents with terminal access can follow [the agent usage contract](docs/agents.md). The organizer uses the existing harness loop, including model-response validation, permission checks, and completion verification. It is not an MCP server or an automatically installed skill.

The original live smoke tests cover service recovery and document review. Organizer tests include controlled offline fixtures and one live synthetic-record smoke test; see the release notes for validation scope.

## Permit: safe to run automatically?

`src/permit-cli.ts` (`npm run permit`) answers one question before an agent
clicks, pays, sends or deletes: is this one action safe to run without a
human? It asks a single choice question (`safe_to_auto` / `needs_approval` /
`refuse`) through the transport and decides through the same gate as the rest
of the harness (`decideOutcome` in `src/enhance/outcome.ts`), at a 0.80
confidence threshold: anything below it never runs automatically.

A hard rule, enforced in code rather than left to the prompt, means an action
never gets to `safe_to_auto` on the model's say-so alone whatever it answered
or how confident it was. It runs *before* the model is ever called, so an
obviously irreversible action never spends a call finding out whether the
model would have agreed. The rule scans the action, target, reversibility
notes and policy lines together, on text that is normalized first (NFKC
fold, zero-width and bidi characters stripped, Cyrillic/Greek homoglyphs
mapped to their Latin lookalike, lowercased) so `Ｄelete`, `dеlete`
(Cyrillic е) and `de​lete` (zero-width space) all match the same rule as
`delete`. Matching is on stems and regexes, not whole phrases, so `deletes`,
`deleting`, `rm -f`, `drop table`, `git push origin main -f` and `send an
email` all match, not just their base form.

The hard rule is not one class. Each matched label folds into one of two, and
the result carries which one fired as `class`:

- **`destructive`** — no practical undo, and no human-approval step makes it
  retroactively fine to have already run, so the verdict is `refuse` (exit
  `3`), no matter what the model would have said. Families: `rm -rf`,
  wipe/purge, `drop table|database|column`, truncate, force-push (`push -f`,
  `git push ... -f`, or the same thing phrased as "push over the remote
  history"), `reset --hard`, `checkout --`, `branch -D`, format of a storage
  target only (disk, drive, volume, partition, SD card, USB, `mkfs`,
  `diskutil erase` — "format the code" is not a hard rule), overwrite,
  `curl | sh`, a wire transfer, and crypto.
- **`irreversible_routine`** — cannot be taken back once it happens, but it
  is an ordinary, named action a human can look at and approve, so the
  verdict is `needs_approval` (exit `2`), not `refuse`. Families:
  delete/remove/rm (bare, no `-rf`), `merge to/into main`,
  pay/payment/invoice/transfer/settle/dollar-amounts/usd, sending an
  email/message/text/sms, replying to a customer/client, post/publish/tweet/
  release/deploy, restarting or stopping an app/bot/server/service/launchd,
  shutdown, and `chmod`/`chown -R`.

Either way the model is never called: the class only changes which side of
"may a human still say yes" the hard rule lands on, not whether it skips the
call.

A second, softer list of cues — "clean up", "tidy", "old backups", "stale",
"away", "over the remote", "history", "production", "prod", "live" — is not
irreversible on its own, so it does not skip the model call, but it still
downgrades an otherwise `safe_to_auto` answer to `needs_approval`: language a
pilot run showed correlates with wanting a human look even when the model was
confident.

Confidence is cross-checked against the answer's own probability
distribution (see below); when the provider returns no distribution at all,
there is nothing to cross-check, and permit now treats that as
`needs_approval` rather than accepting the self-report alone — the output
names the gap (`noDistributionApplied`) instead of silently skipping the
check.

```bash
# snapshot.json: {"action": "...", "target": "...", "reversible": true,
#                 "reversibilityNotes": "...", "policyLines": ["..."]}
npm run permit -- --snapshot snapshot.json --dry-run --json   # plan only, zero calls
npm run permit -- --snapshot snapshot.json --stub --json      # offline plumbing check
npm run permit -- --snapshot snapshot.json --action "delete the record" --json  # live, needs TYPESAFE_API_KEY
```

Exit codes: `0` safe_to_auto, `2` needs_approval, `3` refuse, `1` usage or
failure. `src/enhance/permit.ts` is the domain-independent library; the CLI
is argument parsing and printing around it.

### Pre-rules: settling the free checks before the judge is even asked

Before any of the above runs, `src/enhance/permit-prerules.ts` (default ON;
`--no-prerules` turns it off) applies a second, code-only layer in front of
`decidePermit`'s own hard rule. It was ported from an offline experiment that
replayed the same rules against 30 saved live permit cases plus 20
hand-labeled real commands with zero dangerous outcomes before this port:
never once did a rule let an action expected to refuse or need approval
through as automatically safe.

It does two things the hard rule's flat keyword match cannot do on its own,
because both need facts about the concrete thing being acted on, not just
words in the sentence:

- **Parses the verb and its object.** `rm -rf PATH`, `git push --force
  BRANCH`, `drop table NAME`, a disk/volume/partition target for
  `format`/`mkfs`/`diskutil erase`, a dollar amount, and a payee are pulled
  out with narrow, specific patterns, not free text. This is also where the
  formatter false-refuse is fixed for the pre-rule layer the same way it was
  already fixed in the hard rule: `format the code` or `run the formatter`
  never counts as the disk-wipe sense of "format," only an actual storage
  target (or `mkfs`/`diskutil erase`) does.
- **Reads live reversibility facts when `--cwd` is given**: does the target
  path exist, is it tracked and committed in git, is there a backup sibling
  file, is a named branch `main`/`master` or otherwise protected, and (with
  `--worktree`) is the resolved path inside the task's own worktree. Without
  `--cwd` these stay unknown, never a guessed `false`.

On top of those facts, three rules fire, each settling the question with
**no model call at all**, and the JSON output's `calls` field reads `0` when
one of them does:

1. **Money above a configurable threshold, or a new payee, always needs
   approval.** `--money-threshold` sets the dollar line (default `200`, the
   same number as this fleet's own risk-scaled payments rule); `--new-payee`
   marks the payee as not already on file. Either one settles the verdict to
   `needs_approval` regardless of what the judge would have said, and
   regardless of the amount if a new payee is involved.
2. **A destructive verb refuses outright, no judge call** — same class,
   same verdict `decidePermit`'s own hard rule already reaches, just settled
   one step earlier so the manifest shows `0` calls for this path too.
3. **The one carve-out**: a destructive verb whose target is tracked and
   committed in git *and* inside the task's own `--worktree` is not refused
   sight unseen. It is recoverable from git's own index, so instead it is
   passed through to the judge with that fact attached (`"recoverable from
   the index"`), never auto-allowed on the strength of the fact alone.

Nothing here can ever settle a verdict to `safe_to_auto`. The only three
verdicts a pre-rule can return are `refuse`, `needs_approval`, or
`defer_to_judge` — a pre-rule can only make the outcome more cautious or
leave it for the judge, never less cautious. When nothing settles it, the
derived facts still print first, then whatever the judge (or the CLI's own
existing hard rule) decides for what is left. `--explain` names which rule
fired, or that none did.

```bash
npm run permit -- --snapshot snapshot.json --stub --json \
  --cwd /path/to/repo --worktree /path/to/repo/worktree \
  --money-threshold 200 --explain
npm run permit -- --snapshot snapshot.json --stub --json --no-prerules   # old behavior, pre-rules off
```

## Chain: evidence completeness before the final question

`src/chain-cli.ts` (`npm run chain`) wraps the evidence-chain library that
already existed — `gatherEvidence`/`traverse` (`src/enhance/evidence.ts`) and
`runInvestigation` (`src/enhance/investigate.ts`) — behind a JSON role spec,
so a case like "ticket, then order, then policy" can be checked from the
command line instead of from a test file.

Completeness is checked in code before any question is asked: a case missing
a required role, or with a role two documents fill and neither supersedes the
other, is blocked and never sent. `--dry-run` runs only that in-code check
and reaches no network, because whether a case is blocked never depends on a
model call.

```bash
# spec.json: {"question": "...", "options": {...}, "rootId": "T1",
#             "roles": [{"name": "ticket", "match": {"type": "self"}},
#                        {"name": "order",  "match": {"type": "idPrefix", "prefix": "O"}},
#                        {"name": "policy", "match": {"type": "idPattern", "pattern": "^P"}}],
#             "docs": [{"id": "T1", "text": "...", "links": ["O100", "P1"]}, ...]}
npm run chain -- --spec spec.json --dry-run --json   # in-code completeness check only
npm run chain -- --spec spec.json --stub --json      # offline plumbing check
npm run chain -- --spec spec.json --json             # live, needs TYPESAFE_API_KEY
```

A spec can also carry a `"cases"` array to check several cases against the
same document pool and roles in one call. See `npm run chain -- --help` for
the full role-match vocabulary (`self`, `idPrefix`, `idPattern`,
`textPattern`) and every spec field.

Exit codes: `0` every case resolved, `2` at least one case is blocked on
insufficient evidence (the missing roles are named in the output), `1` usage
or failure.

## Fetch: pull, not push (wishlist item 6)

Score a whole catalog — a skills index, a tools catalog, a brain INDEX — against one plain request, and load only the top few entries instead of injecting the full catalog every turn.

```bash
npm run fetch -- --catalog examples/organizer.json --request "check my draft against the evidence" --dry-run
npm run fetch -- --catalog my-catalog.json --request "pay the electric bill" --k 5 --out out --json
```

`--catalog` is a JSON array of records (or `{"catalog":[...]}`). A v1 record is just `{"id","text"}`. A **v2** record (`src/enhance/catalog.ts`) adds three optional fields, drawn from the tool/skill-retrieval literature (ToolRet, semantic-router's `Route` objects — see `docs/wishlist.md` for the citations): `utterances` (realistic user phrasings that should route here, e.g. "restart it," not "invoke the process supervisor restart skill"), `negatives` (phrasings that sound close but should NOT route here) and `tags` (freeform, never scored). A v1-only catalog behaves exactly as before; the two schemas can even mix in one array.

Under the hood, `runFetch` (`src/enhance/fetch.ts`) is one relevance question — the request folded into its instructions, never interpolated raw — run through the same `planSweep` / `runSweep` engine `npm run sweep` uses, so the per-call question cap and the named `records.<key>.text` references that keep the sweep path order-insensitive apply here unchanged. Before that one call, a local, zero-cost **narrowing pass** (BM25-lite) keeps only the top N records (`--prefilter N`, default 8, `0` disables): `text` and `utterances` are scored (utterances weighted higher — a real request reads like an utterance, not a catalog description), `negatives` are subtracted, and `--context FILE` (a JSON array of recent user turns, oldest first; `--context-turns` keeps the trailing N, default 3) is folded into the query at a lower weight, so a referent like "restart it" inherits its subject from the prior turn. `calls` reports how many provider calls the run actually made — 1 when the narrowed set fits one batch.

Every kept record gets a `high`/`medium`/`low`/`none` relevance level and a confidence, where `none` means "none of these": a record ranks only if it beats `none`. On top of that sits the **none gate** (`applyNoneGate`, `--floor N`, default 0.60, and `--margin N`, default 0.10): when nothing beat `none`, or the top pick's confidence is below the floor, or the gap between the top pick and the runner-up is below the margin, the result is `{noMatch: true, candidates: [...], ask: "..."}` — the closest 3 candidates and one clarifying question built from their ids — instead of a best guess. Otherwise it's `{noMatch: false, ranked: [...]}`, the top `k` `{id, score, confidence}` entries (ties broken by confidence, then by id).

Why a margin on top of a floor: a confident-looking top-1 sitting in a crowded field — a runner-up almost as confident — is still a guess, just one that reads confident. The floor alone can't tell "clearly the best answer" apart from "the least-bad of two nearly-tied answers"; the margin is the gap between top-1 and top-2, and it's what catches the second case. An offline replay of saved live fetch rankings found that pairing a lower floor (0.60) with a margin requirement (0.10) served more correct top picks with fewer wrong serves than the old floor-only rule at 0.80 — described here in words, not as benchmarked numbers; see the fetch-floor experiment report for the sweep. The old floor-only behaviour is still reachable with `--floor 0.80 --margin 0`. Both are also overridable via the `SUPERJEV_FETCH_FLOOR` and `SUPERJEV_FETCH_MARGIN` env vars; a CLI flag always wins over its env var. `--dry-run` prints the plan and reaches no network. `--stub` runs the offline stub. Live mode needs `TYPESAFE_API_KEY`.

**Feedback loop.** `npm run fetch -- ... --record <chosen-id> [--ledger FILE]` (default `.superjev/fetch-ledger.jsonl`) appends `{request, context, ranked, chosen, ts}` after scoring, without changing the printed result. `npm run catalog -- learn <ledger.jsonl> <catalog.json> [--out proposals.json]` mines that ledger: for every line where the human's `chosen` id differed from the run's own top pick, it proposes the request text as a new utterance on the record that should have won, and writes a **separate proposals file** — it never edits the catalog. `npm run catalog -- validate <catalog.json>` reports records with fewer than three utterances, the same phrasing claimed by two records, and a negative that is word-for-word another record's utterance. `npm run catalog -- build <skills-dir> <out.json>` (alias: `npm run catalog:build`) generates a fresh catalog v2 file from a Claude Code skills directory — one record per skill, derived from each skill's own SKILL.md frontmatter `description` and any `Trigger`/`Use when` lines, no hand tuning. Validate what it writes with `catalog -- validate`. `validate`, `learn` and `build` are one door: `npm run catalog -- <subcommand>`.

`npm run bench:fetch` runs the offline **admission bench** (`bench/fetch-bench.ts`) against `bench/fetch-cases.json` — a scripted stub, still plumbing only, never a judge-accuracy claim (see the file header). What v2 adds real signal about: it splits the cases seeded (`--holdout 0.3` default, `--seed 42`), simulates one round of `catalog -- learn` by adding each TRAIN-split case's own request text as an utterance, and narrows + judges only the HOLDOUT split — a holdout request's own text is never one of the added utterances, so a passing hit@1 is evidence the narrowing pass generalizes, not that it memorized the fixture. It reports hit@1, hit@k, no-match precision/recall and calls per request on the holdout split, and exits non-zero when hit@1 falls below `--min-hit1` (default 0.8).

## Agent front door (Claude Code skill)

`skills/super-jev/` is a small [Claude Code](https://docs.claude.com/en/docs/claude-code) skill: a single Python file, `superjev.py`, that gives an agent one command for every check in this repo instead of four things to remember. It is a thin wrapper — every judgement still belongs to the tool it wraps. New here? **[`docs/playbook.md`](docs/playbook.md)** is the fast path in: a job-to-door table, one worked example per door, and what real dogfooding says to trust today. **[`docs/lessons.md`](docs/lessons.md)** is what live use has taught us since: feed rules, hook lessons, and why a couple of promising-sounding changes did not pan out.

Install it by symlink or copy:

```bash
ln -s "$(pwd)/skills/super-jev" ~/.claude/skills/super-jev
```

| subcommand | what it does |
| --- | --- |
| `gate <evidence...> --draft <file>` / `--claim "..."` | checks a draft or claim against the evidence files behind it, via `SUPERJEV_GATE_CMD` |
| `verify <report> [--worktree P] [--test-cmd C] [--paths ...]` | checks a "done" report against machine-collected evidence, via `SUPERJEV_VERIFY_CMD` |
| `sweep <records.jsonl> --questions <q.json> --out <dir>` | runs `npm run sweep` in this repo, with proof nothing was skipped |
| `fetch <request> --catalog <catalog.json>` | runs `npm run fetch` in this repo, scoring the catalog and returning the top ids |
| `bench [--dry-run] [--stub]` | runs `npm run bench:live` in this repo, or prints the dry-run plan with no `TYPESAFE_API_KEY` |
| `permit` / `chain` | not built yet; each names its own item in [`docs/wishlist.md`](docs/wishlist.md) and exits 6 instead of failing silently |
| `ask "<one plain sentence>"` | routes a plain request to the right subcommand above with a keyword table, no model call |
| `status` | reports which subcommands are live in this checkout right now |

`gate` and `verify` wrap a claim-gate tool and a report-verify tool that are not part of this repo — point `SUPERJEV_GATE_CMD` and `SUPERJEV_VERIFY_CMD` at your own. `sweep` and `bench` need no env var at all: `SUPERJEV_REPO` defaults to this checkout's own root. Full docs, the exit-code table, and the routing keywords are in [`skills/super-jev/SKILL.md`](skills/super-jev/SKILL.md).

**Frame evidence as derived facts first.** When no report-verify tool is installed (`SUPERJEV_VERIFY_CMD` unset, nothing at the fleet-local fallback path), `verify` does not just refuse — it gathers a small git/test evidence set itself and reads it in code before anyone reads it by eye: a test count is answered by the run's own summary lines and nothing else in the evidence; a path is answered by whether it is actually in the git index, not just on disk; a commit or branch is answered by whether it exists at all; a push is answered by the real ahead/behind count. Each answer becomes one plain-English **derived fact**, and any claim a fact flatly contradicts is settled right there — `CONTRADICTED_BY_FACT` at confidence 1.00 — before anything would have gone to a judge. The pure functions behind this, `deriveFacts` and `preRules`, live in [`src/enhance/derive-facts.ts`](src/enhance/derive-facts.ts) and are reachable from any language through [`src/derive-facts-cli.ts`](src/derive-facts-cli.ts). This fallback never calls a judge, so it can say a claim is contradicted, but it can never say CLEAN — an unsettled claim stays READ, unverified, not vouched for. See [`docs/playbook.md`](docs/playbook.md#frame-evidence-as-derived-facts-first) for the full claim-type-to-evidence-type table.

Tests: `python3 -m pytest skills/super-jev/tests -q`, or `npm run test:skill`. Fully offline — every wrapped door is a fake in the test, so no test reaches TypeSafe, npm or git.

### Hooks

`skills/super-jev/hooks/` wires three Claude Code hook events to `superjev.py
hook`, so a draft reply or a sub-agent's report is checked against machine
evidence before anyone acts on it. **[`docs/hooks.md`](docs/hooks.md)** is the
page to read first: what each hook does, the `settings.json` snippet, the loop
guard, what "block" and "advisory" mean, the call ledger, what it costs, the
measured failure modes, and when not to install it. Nothing is installed for
you, and with `SUPERJEV_GATE_CMD`/`SUPERJEV_VERIFY_CMD` unset every hook fails
open.
