/**
 * Live measurement runner: six conditions, 48 labeled records, one report.
 *
 * What this file is for. The enhancer prototype landed with an explicit caveat
 * that none of it had been measured against live traffic. This runner is the
 * thing that removes the caveat or kills the prototype. It runs the baseline
 * the pilot actually measured and the enhancer path side by side, on the same
 * labels, in the same session, and reports the accepted-error rate for both.
 *
 * What it will not do:
 * - It never reads a `.env` file, a credential file or any secret. The only
 *   credential it touches is `TYPESAFE_API_KEY` from the environment, it is
 *   read by the Jev adapter, and it is never logged, printed or written to a
 *   trace. Request headers are not recorded.
 * - It refuses to start a live run when that variable is unset, rather than
 *   producing a half run or a confusing provider error.
 * - `--dry-run` reaches no network at all and needs no key: it prints the plan,
 *   the call count and the token estimate so the cost is known before a cent
 *   is spent.
 *
 * Usage:
 *   npm run bench:live -- --dry-run
 *   npm run bench:live -- --stub
 *   TYPESAFE_API_KEY=... npm run bench:live
 */
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { Jev } from '../src/jev.ts';
import { organizer } from '../src/organizer.ts';
import { estimateTokens } from '../src/enhance/budget.ts';
import { instructionFor, planEnhancedRun, runEnhancedClassification } from '../src/enhance/classify.ts';
import { buildManifest } from '../src/enhance/coverage.ts';
import { decideOutcome, readAnswer } from '../src/enhance/outcome.ts';
import { arrayPairs, mapAnswers, toRefs } from '../src/enhance/reference.ts';
import type { BatchPlan, CoverageManifest, Evaluation, RecordOutcome, Request } from '../src/enhance/types.ts';
import type { Evaluator } from '../src/types.ts';
import { ABSTAIN_OPTION, CONDITIONS, GATE_MIN_CONFIDENCE, RECORD_SETS, categories, type ConditionSpec, type RecordSet } from './conditions.ts';
import { combine, score, type CallTrace, type LabeledRecord, type Metrics, type RunResult } from './metrics.ts';
import { renderReport } from './report.ts';
import { applyOrder, describeOrder } from './shuffle.ts';

const TIMEOUT_MS = 30_000;
/** Output tokens one answer is assumed to cost. The same figure classify.ts uses. */
const OUTPUT_TOKENS_PER_ANSWER = 30;

/** Read the framing and the records out of a built request, for the trace. */
function describeRequest(request: Request): { framing: 'array' | 'keyed'; recordIds: string[] } {
  const records = (request.state as { records?: unknown })?.records;
  if (Array.isArray(records)) {
    return { framing: 'array', recordIds: records.map((r, i) => (r as { id?: string })?.id ?? `index_${i}`) };
  }
  if (records && typeof records === 'object') {
    return { framing: 'keyed', recordIds: Object.entries(records as Record<string, { id?: string }>).map(([key, r]) => r?.id ?? key) };
  }
  return { framing: 'array', recordIds: [] };
}

type RawPeek = () => { model?: string; usage?: { input_tokens?: number; output_tokens?: number } } | undefined;

/**
 * Wraps any Evaluator and records one trace per call, including calls that
 * threw. A rejected response is the most interesting call in the run, so it is
 * recorded with the same detail as a successful one.
 */
class TracingEvaluator implements Evaluator {
  inner: Evaluator;
  sink: CallTrace[];
  context: { condition: string; set: string };
  /** Last raw provider body, for calls the validator rejected. */
  peekRaw?: RawPeek;
  resetRaw?: () => void;

  constructor(inner: Evaluator, sink: CallTrace[], context: { condition: string; set: string }, peekRaw?: RawPeek, resetRaw?: () => void) {
    this.inner = inner;
    this.sink = sink;
    this.context = context;
    this.peekRaw = peekRaw;
    this.resetRaw = resetRaw;
  }

  async evaluate(request: Request, signal: AbortSignal): Promise<Evaluation> {
    this.resetRaw?.();
    const shape = describeRequest(request);
    const started = performance.now();
    try {
      const evaluation = await this.inner.evaluate(request, signal);
      this.sink.push({
        ...this.context, pass: 'pending', callIndex: this.sink.length, ...shape,
        model: evaluation.model,
        inputTokens: evaluation.usage?.input_tokens,
        outputTokens: evaluation.usage?.output_tokens,
        latencyMs: Math.round(performance.now() - started),
        request, response: evaluation
      });
      return evaluation;
    } catch (error) {
      const raw = this.peekRaw?.();
      this.sink.push({
        ...this.context, pass: 'pending', callIndex: this.sink.length, ...shape,
        model: raw?.model,
        inputTokens: raw?.usage?.input_tokens,
        outputTokens: raw?.usage?.output_tokens,
        latencyMs: Math.round(performance.now() - started),
        error: (error as Error).message,
        request, response: raw ?? null
      });
      throw error;
    }
  }
}

/** A live Jev adapter plus a peek at the untouched provider body. */
export function liveEvaluator(): { evaluator: Evaluator; peekRaw: () => any; resetRaw: () => void } {
  let raw: any;
  const jev = new Jev({
    fetch: async (url, init) => {
      const response = await fetch(url as any, init);
      raw = await response.clone().json().catch(() => null);
      return response;
    }
  });
  return { evaluator: jev, peekRaw: () => raw, resetRaw: () => { raw = undefined; } };
}

export type EvaluatorFactory = (context: { condition: string; set: string }) => {
  evaluator: Evaluator; peekRaw?: () => any; resetRaw?: () => void;
};

/**
 * Condition A/B: the baseline the pilot measured. The request is built by the
 * organizer itself, so the prompt is the shipped organizer prompt and not a
 * copy of it that can drift.
 */
async function runBaseline(records: LabeledRecord[], evaluator: Evaluator): Promise<CoverageManifest> {
  const domain = organizer({ records: records.map(({ id, text }) => ({ id, text })), categories });
  const state = { rows: [], complete: false };
  const controller = new AbortController();
  const evidence = await domain.observe(state, controller.signal);
  const request: Request = { state: evidence, questions: domain.questions(state, evidence) };
  const pairs = arrayPairs(toRefs(records.map(({ id, text }) => ({ id, text }))));
  const gate = { minConfidence: GATE_MIN_CONFIDENCE, abstainValue: ABSTAIN_OPTION, requireAgreement: true };
  const ids = records.map(r => r.id);

  let evaluation: Evaluation | undefined;
  let failure: string | undefined;
  try {
    evaluation = await evaluator.evaluate(request, AbortSignal.timeout(TIMEOUT_MS));
  } catch (error) {
    failure = (error as Error).message;
  }

  if (!evaluation) {
    // One call carried every record, so a rejected response loses all of them.
    // That is the cost of the single-batch baseline and it is reported as such.
    const outcomes: RecordOutcome[] = ids.map(id => ({
      id, kind: 'failed_validation' as const,
      reason: `the single baseline call for this record set was rejected or failed: ${failure}`,
      passes: []
    }));
    return buildManifest(ids, outcomes);
  }

  const mapping = mapAnswers(pairs, evaluation);
  const byId = new Map(mapping.answered.map(entry => [entry.id, entry.answer]));
  const outcomes = ids.map(id => {
    const answer = byId.get(id);
    if (!answer) return decideOutcome(id, [{ pass: 1, answered: false }], gate);
    const read = readAnswer(answer);
    const known = read.value !== undefined && Object.hasOwn(categories, read.value);
    return decideOutcome(id, [{
      pass: 1, answered: true, malformed: read.malformed || !known,
      value: read.value, confidence: read.confidence, peak: read.peak
    }], gate);
  });
  return buildManifest(ids, outcomes);
}

/** Every planned call of an enhancer run, flattened in execution order. */
function expectedCalls(plans: { pass: string; plan: BatchPlan }[]): { pass: string; ids: Set<string> }[] {
  return plans.flatMap(p => p.plan.calls.map(call => ({ pass: p.pass, ids: new Set(call.recordIds) })));
}

const sameSet = (a: Set<string>, b: readonly string[]) => a.size === b.length && b.every(id => a.has(id));

/**
 * Attribute each trace to the pass that produced it. The enhancer runs its
 * passes in order and its calls in plan order, so walking the planned sequence
 * and allowing a repeat of the current call as a retry recovers the mapping
 * without the runner having to reach inside the enhancer.
 */
function attributePasses(traces: CallTrace[], plans: { pass: string; plan: BatchPlan }[]): void {
  const expected = expectedCalls(plans);
  let cursor = 0;
  for (const trace of traces) {
    if (cursor < expected.length && sameSet(expected[cursor].ids, trace.recordIds)) {
      trace.pass = expected[cursor].pass;
      cursor += 1;
    } else if (cursor > 0 && sameSet(expected[cursor - 1].ids, trace.recordIds)) {
      trace.pass = `${expected[cursor - 1].pass} (retry)`;
    } else {
      trace.pass = 'unattributed';
    }
  }
}

export function enhancerConfig(condition: ConditionSpec, ordered: LabeledRecord[]) {
  return {
    records: ordered.map(({ id, text }) => ({ id, text })),
    options: categories,
    abstainOption: ABSTAIN_OPTION,
    gate: { minConfidence: GATE_MIN_CONFIDENCE },
    budget: { maxRecordsPerCall: condition.maxRecordsPerCall ?? 4 },
    passes: condition.passes,
    timeoutMs: TIMEOUT_MS
  };
}

/** Run one condition against one record set. */
export async function runCondition(
  set: RecordSet, condition: ConditionSpec, makeEvaluator: EvaluatorFactory
): Promise<RunResult> {
  const order = condition.order[set.id];
  const ordered = applyOrder(set.records, order);
  const traces: CallTrace[] = [];
  const context = { condition: condition.id, set: set.id };
  const made = makeEvaluator(context);
  const tracing = new TracingEvaluator(made.evaluator, traces, context, made.peekRaw, made.resetRaw);
  const started = performance.now();

  let manifest: CoverageManifest;
  if (condition.engine === 'baseline') {
    manifest = await runBaseline(ordered, tracing);
    for (const trace of traces) trace.pass = 'single';
  } else {
    const config = enhancerConfig(condition, ordered);
    const run = await runEnhancedClassification(config, tracing);
    attributePasses(traces, run.plans);
    manifest = run.manifest;
  }

  return {
    condition: condition.id,
    conditionLabel: condition.label,
    set: set.id,
    order: describeOrder(order),
    manifest,
    traces,
    wallMs: Math.round(performance.now() - started)
  };
}

export type PlanLine = {
  condition: string;
  conditionLabel: string;
  set: string;
  order: string;
  calls: number;
  estimatedInputTokens: number;
  estimatedOutputTokens: number;
  detail: string[];
};

/**
 * The whole run, planned, with nothing called. Token figures are the harness's
 * own deterministic character estimate and are never a billing figure.
 */
export function planRun(sets: RecordSet[] = RECORD_SETS, conditions: ConditionSpec[] = CONDITIONS): PlanLine[] {
  const lines: PlanLine[] = [];
  for (const set of sets) {
    for (const condition of conditions) {
      const order = condition.order[set.id];
      const ordered = applyOrder(set.records, order);
      if (condition.engine === 'baseline') {
        const domain = organizer({ records: ordered.map(({ id, text }) => ({ id, text })), categories });
        const request = { state: { records: ordered.map(({ id, text }) => ({ id, text })) }, questions: domain.questions({ rows: [], complete: false }, null) };
        lines.push({
          condition: condition.id, conditionLabel: condition.label, set: set.id, order: describeOrder(order),
          calls: 1,
          estimatedInputTokens: estimateTokens(JSON.stringify(request)),
          estimatedOutputTokens: ordered.length * OUTPUT_TOKENS_PER_ANSWER,
          detail: [`1 call carrying all ${ordered.length} records, positional records[i] references`]
        });
        continue;
      }
      const { plans } = planEnhancedRun(enhancerConfig(condition, ordered));
      lines.push({
        condition: condition.id, conditionLabel: condition.label, set: set.id, order: describeOrder(order),
        calls: plans.reduce((n, p) => n + p.plan.calls.length, 0),
        estimatedInputTokens: plans.reduce((n, p) => n + p.plan.totalEstimatedInputTokens, 0),
        estimatedOutputTokens: plans.reduce((n, p) => n + p.plan.calls.reduce((m, c) => m + c.recordIds.length * OUTPUT_TOKENS_PER_ANSWER, 0), 0),
        detail: plans.map(p => `pass ${p.pass}: ${p.plan.calls.length} call(s), max ${p.plan.budget.maxRecordsPerCall} records each${p.plan.oversizedRecordIds.length ? `, EXCLUDED as oversized: ${p.plan.oversizedRecordIds.join(', ')}` : ''}`)
      });
    }
  }
  return lines;
}

export function formatPlanLines(lines: PlanLine[]): string {
  const out: string[] = ['Planned run (no network reached, no key required):', ''];
  for (const line of lines) {
    out.push(`  ${line.condition} / ${line.set} / order ${line.order}: ${line.calls} call(s), est ${line.estimatedInputTokens} input + ${line.estimatedOutputTokens} output tokens`);
    for (const detail of line.detail) out.push(`      ${detail}`);
  }
  const calls = lines.reduce((n, l) => n + l.calls, 0);
  out.push('');
  out.push(`  TOTAL: ${calls} calls, est ${lines.reduce((n, l) => n + l.estimatedInputTokens, 0)} input + ${lines.reduce((n, l) => n + l.estimatedOutputTokens, 0)} output tokens`);
  out.push('  Token figures are this harness\'s deterministic character estimate, not a billing figure.');
  out.push('  The call count assumes no retries; a rejected response costs one extra call for that batch.');
  return out.join('\n');
}

/**
 * The two instruction strings this run sends, read out of the shipped builders
 * rather than retyped, so the report always shows what actually went on the
 * wire.
 */
export function promptsInUse(): { baseline: string; enhancer: string } {
  const domain = organizer({ records: [{ id: 'EXAMPLE', text: 'example' }], categories });
  const baseline = domain.questions({ rows: [], complete: false }, null).record_0.instructions;
  const enhancer = instructionFor(categories, ABSTAIN_OPTION).keyed({ id: 'EXAMPLE', key: 'EXAMPLE', text: 'example' }).instructions;
  return { baseline, enhancer };
}

export type BenchOutput = {
  mode: 'live' | 'stub';
  startedAt: string;
  finishedAt: string;
  outDir: string;
  plan: PlanLine[];
  runs: RunResult[];
  bySet: Record<string, Metrics[]>;
  combined: Metrics[];
  report: string;
};

/**
 * Run every condition on every set, score it, and write the artifacts.
 * The evaluator is injected, which is what lets the offline test drive this
 * exact code path against the stub with no network and no key.
 */
export async function runBench(options: {
  mode: 'live' | 'stub';
  makeEvaluator: EvaluatorFactory;
  outDir: string;
  sets?: RecordSet[];
  conditions?: ConditionSpec[];
  onProgress?: (message: string) => void;
}): Promise<BenchOutput> {
  const sets = options.sets ?? RECORD_SETS;
  const conditions = options.conditions ?? CONDITIONS;
  const plan = planRun(sets, conditions);
  const startedAt = new Date().toISOString();
  const runs: RunResult[] = [];

  for (const set of sets) {
    for (const condition of conditions) {
      options.onProgress?.(`running condition ${condition.id} on ${set.id} (${describeOrder(condition.order[set.id])})`);
      runs.push(await runCondition(set, condition, options.makeEvaluator));
    }
  }
  const finishedAt = new Date().toISOString();

  const bySet: Record<string, Metrics[]> = {};
  for (const set of sets) {
    bySet[set.id] = conditions.map(condition => {
      const run = runs.find(r => r.condition === condition.id && r.set === set.id)!;
      return score(run, set.records);
    });
  }
  const combined = conditions.map(condition =>
    combine(condition.id, condition.label, sets.map(set => bySet[set.id][conditions.indexOf(condition)]))
  );

  const report = renderReport({
    mode: options.mode, startedAt, finishedAt, outDir: options.outDir,
    sets: sets.map(s => ({ id: s.id, note: s.note })),
    bySet, combined,
    plannedCalls: plan.reduce((n, l) => n + l.calls, 0),
    plannedInputTokens: plan.reduce((n, l) => n + l.estimatedInputTokens, 0),
    prompts: promptsInUse()
  });

  await mkdir(options.outDir, { recursive: true });
  // Raw traces carry the request and the response only. No headers, so no key.
  const raw = runs.flatMap(run => run.traces.map(trace => JSON.stringify(trace))).join('\n');
  await writeFile(join(options.outDir, 'raw.jsonl'), raw ? `${raw}\n` : '');
  await writeFile(join(options.outDir, 'report.md'), report);
  await writeFile(join(options.outDir, 'results.json'), `${JSON.stringify({
    mode: options.mode, startedAt, finishedAt,
    design: 'Six conditions over 48 labeled synthetic records, one order seed each. Labels fixed before testing. No significance claim. Internal only.',
    gate: { minConfidence: GATE_MIN_CONFIDENCE, abstainOption: ABSTAIN_OPTION, requireAgreement: true },
    plan,
    metrics: { bySet, combined },
    manifests: runs.map(run => ({ condition: run.condition, set: run.set, order: run.order, manifest: run.manifest }))
  }, null, 2)}\n`);

  return { mode: options.mode, startedAt, finishedAt, outDir: options.outDir, plan, runs, bySet, combined, report };
}

function timestampDir(): string {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  return join(new URL('.', import.meta.url).pathname, 'out', stamp);
}

export async function main(argv: string[]): Promise<number> {
  const dryRun = argv.includes('--dry-run');
  const stub = argv.includes('--stub');

  if (argv.includes('--help')) {
    console.log([
      'bench:live - measure the baseline organizer against the context enhancer.',
      '',
      '  --dry-run   print the plan, the call count and the token estimate. No network, no key.',
      '  --stub      run end to end against the offline stub provider. No network, no key.',
      '  (no flag)   live run. Requires TYPESAFE_API_KEY in the environment.'
    ].join('\n'));
    return 0;
  }

  if (dryRun) {
    console.log(formatPlanLines(planRun()));
    return 0;
  }

  if (!stub && !process.env.TYPESAFE_API_KEY) {
    console.error('Refusing to start: set TYPESAFE_API_KEY in the environment for a live run, or pass --dry-run or --stub.');
    return 2;
  }

  const outDir = timestampDir();
  if (stub) {
    const { stubFactory } = await import('./stub-run.ts');
    const output = await runBench({ mode: 'stub', makeEvaluator: stubFactory(), outDir, onProgress: m => console.log(m) });
    console.log(`\nstub run written to ${output.outDir}`);
    return 0;
  }

  console.log(formatPlanLines(planRun()));
  console.log('');
  const output = await runBench({
    mode: 'live',
    makeEvaluator: () => liveEvaluator(),
    outDir,
    onProgress: message => console.log(message)
  });
  console.log('');
  for (const row of output.combined) {
    console.log(`${row.condition}: accuracy ${row.correct}/${row.total}, coverage ${row.accepted}/${row.total}, accepted errors ${row.acceptedWrong}/${row.total}, calls ${row.calls}`);
  }
  console.log(`\nwritten to ${output.outDir} (report.md, results.json, raw.jsonl)`);
  return 0;
}

if (import.meta.filename === process.argv[1]) {
  process.exitCode = await main(process.argv.slice(2));
}
