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
examples/         Two original domain demos and organizer input fixture
test/             Core behavior and mocked API tests
docs/             Architecture and launch draft
```

## Validation

The suite has 26 tests covering the core loop, mocked API adapter and CLI response contract, organizer grouping and review policy, bounded input reads, record-field minimization, CLI privacy, output protection, and failed-output cleanup. Both original demos and the organizer demo pass offline. Historical v0.1 live smoke tests used three API evaluations and local demo tools; see docs/live-validation.json. One v0.2 live organizer evaluation on `jev-1.13.0` classified four synthetic records as expected and verified grouping and the `other` review queue. See [organizer validation](docs/organizer-validation.md) for results and limits. This is control-flow validation and a small smoke test, not a broad Jev capability benchmark. Native TypeScript execution does not provide static type checking.

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
