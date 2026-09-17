/**
 * Scoring for the live measurement runner.
 *
 * Every number here is computed from two things only: the labels fixed before
 * the pilot ran, and the outcome the harness's own gate produced. Nothing is
 * rescored by hand and no answer is reinterpreted after the fact.
 *
 * The number that matters is the ACCEPTED-ERROR RATE: records that were wrong
 * AND accepted for automatic action. A high raw accuracy with a high accepted-
 * error rate is a worse system than a lower accuracy that routes its mistakes
 * to a human, so accuracy is never reported on its own.
 */
import type { CoverageManifest, RecordOutcome } from '../src/enhance/types.ts';

export type LabeledRecord = { id: string; text: string; expected: string; kind?: string };

/** One provider call, as recorded by the tracing evaluator. */
export type CallTrace = {
  condition: string;
  set: string;
  pass: string;
  callIndex: number;
  recordIds: string[];
  framing: 'array' | 'keyed';
  model?: string;
  inputTokens?: number;
  outputTokens?: number;
  latencyMs: number;
  /** Present when the response was rejected or the call failed. */
  error?: string;
  request: unknown;
  response: unknown;
};

export type RunResult = {
  condition: string;
  conditionLabel: string;
  set: string;
  order: string;
  manifest: CoverageManifest;
  traces: CallTrace[];
  wallMs: number;
};

export type AcceptedWrong = { id: string; chose: string; expected: string; confidence: number | null; kind?: string };

export type Metrics = {
  condition: string;
  conditionLabel: string;
  set: string;
  order: string;
  total: number;
  /** Final harness value equals the label. A blocked disagreement has no value and counts wrong. */
  correct: number;
  accuracy: number;
  /** First pass answer equals the label, so single-pass and two-pass runs stay comparable. */
  pass1Correct: number;
  pass1Accuracy: number;
  accepted: number;
  /** Share of records the harness would act on with no human. */
  automationCoverage: number;
  acceptedWrong: number;
  acceptedErrorRate: number;
  review: number;
  reviewRate: number;
  abstain: number;
  abstainRate: number;
  /** insufficient_evidence, failed_validation, unanswered, unaccounted. */
  otherKinds: Record<string, number>;
  manifestComplete: boolean;
  manifestProblems: string[];
  calls: number;
  inputTokens: number;
  outputTokens: number;
  /** True when at least one call returned no usage block, so totals are incomplete. */
  tokensIncomplete: boolean;
  medianLatencyMs: number;
  p95LatencyMs: number;
  /** Per-call latencies, kept so the combined figure pools observations. */
  latencies: number[];
  wallMs: number;
  /** Calls whose whole response was rejected or failed. Every record in them is lost. */
  validationRejections: number;
  rejectionMessages: string[];
  models: string[];
  acceptedWrongList: AcceptedWrong[];
};

export function median(values: readonly number[]): number {
  if (!values.length) return 0;
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : Math.round((s[mid - 1] + s[mid]) / 2);
}

/** Nearest-rank p95: the smallest observation at or above 95% of the sample. */
export function percentile(values: readonly number[], p: number): number {
  if (!values.length) return 0;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.max(0, Math.ceil(p * s.length) - 1))];
}

const OTHER_KINDS = ['insufficient_evidence', 'failed_validation', 'unanswered', 'unaccounted'] as const;

function firstPassValue(outcome: RecordOutcome): string | undefined {
  return outcome.passes.find(p => p.pass === 1)?.value;
}

export function score(run: RunResult, records: readonly LabeledRecord[]): Metrics {
  const expectedById = new Map(records.map(r => [r.id, r]));
  const outcomes = run.manifest.outcomes;
  const total = outcomes.length;
  const acceptedWrongList: AcceptedWrong[] = [];
  let correct = 0, pass1Correct = 0, accepted = 0, review = 0, abstain = 0;
  const otherKinds: Record<string, number> = Object.fromEntries(OTHER_KINDS.map(k => [k, 0]));

  for (const outcome of outcomes) {
    const label = expectedById.get(outcome.id)?.expected;
    if (outcome.value !== undefined && outcome.value === label) correct += 1;
    if (firstPassValue(outcome) !== undefined && firstPassValue(outcome) === label) pass1Correct += 1;
    if (outcome.kind === 'accepted') {
      accepted += 1;
      if (outcome.value !== label) {
        acceptedWrongList.push({
          id: outcome.id,
          chose: outcome.value ?? 'NONE',
          expected: label ?? 'UNKNOWN',
          confidence: outcome.confidence ?? null,
          kind: expectedById.get(outcome.id)?.kind
        });
      }
    } else if (outcome.kind === 'review') review += 1;
    else if (outcome.kind === 'abstain') abstain += 1;
    else otherKinds[outcome.kind] = (otherKinds[outcome.kind] ?? 0) + 1;
  }

  const latencies = run.traces.map(t => t.latencyMs);
  const rejections = run.traces.filter(t => t.error);
  const usable = run.traces.filter(t => t.inputTokens !== undefined);
  const share = (n: number) => (total ? n / total : 0);

  return {
    condition: run.condition,
    conditionLabel: run.conditionLabel,
    set: run.set,
    order: run.order,
    total,
    correct,
    accuracy: share(correct),
    pass1Correct,
    pass1Accuracy: share(pass1Correct),
    accepted,
    automationCoverage: share(accepted),
    acceptedWrong: acceptedWrongList.length,
    acceptedErrorRate: share(acceptedWrongList.length),
    review,
    reviewRate: share(review),
    abstain,
    abstainRate: share(abstain),
    otherKinds,
    manifestComplete: run.manifest.complete,
    manifestProblems: run.manifest.problems,
    calls: run.traces.length,
    inputTokens: usable.reduce((n, t) => n + (t.inputTokens ?? 0), 0),
    outputTokens: usable.reduce((n, t) => n + (t.outputTokens ?? 0), 0),
    tokensIncomplete: usable.length !== run.traces.length,
    medianLatencyMs: median(latencies),
    p95LatencyMs: percentile(latencies, 0.95),
    latencies,
    wallMs: run.wallMs,
    validationRejections: rejections.length,
    rejectionMessages: [...new Set(rejections.map(t => t.error!))],
    models: [...new Set(run.traces.map(t => t.model).filter((m): m is string => typeof m === 'string'))],
    acceptedWrongList
  };
}

/**
 * Combine the per-set metrics for one condition into the 48-record figure.
 * Counts add; rates are recomputed from the combined counts, never averaged.
 */
export function combine(condition: string, conditionLabel: string, parts: readonly Metrics[]): Metrics {
  const sum = (pick: (m: Metrics) => number) => parts.reduce((n, m) => n + pick(m), 0);
  const total = sum(m => m.total);
  const share = (n: number) => (total ? n / total : 0);
  const otherKinds: Record<string, number> = {};
  for (const part of parts) for (const [kind, n] of Object.entries(part.otherKinds)) otherKinds[kind] = (otherKinds[kind] ?? 0) + n;
  const correct = sum(m => m.correct);
  const pass1Correct = sum(m => m.pass1Correct);
  const accepted = sum(m => m.accepted);
  const acceptedWrong = sum(m => m.acceptedWrong);

  return {
    condition,
    conditionLabel,
    set: 'combined-48',
    order: parts.map(m => `${m.set}: ${m.order}`).join('; '),
    total,
    correct,
    accuracy: share(correct),
    pass1Correct,
    pass1Accuracy: share(pass1Correct),
    accepted,
    automationCoverage: share(accepted),
    acceptedWrong,
    acceptedErrorRate: share(acceptedWrong),
    review: sum(m => m.review),
    reviewRate: share(sum(m => m.review)),
    abstain: sum(m => m.abstain),
    abstainRate: share(sum(m => m.abstain)),
    otherKinds,
    manifestComplete: parts.every(m => m.manifestComplete),
    manifestProblems: parts.flatMap(m => m.manifestProblems),
    calls: sum(m => m.calls),
    inputTokens: sum(m => m.inputTokens),
    outputTokens: sum(m => m.outputTokens),
    tokensIncomplete: parts.some(m => m.tokensIncomplete),
    // Latency is a per-call property, so the combined figure is taken over the
    // pooled per-call observations rather than over the two summaries.
    medianLatencyMs: median(parts.flatMap(m => m.latencies)),
    p95LatencyMs: percentile(parts.flatMap(m => m.latencies), 0.95),
    latencies: parts.flatMap(m => m.latencies),
    wallMs: sum(m => m.wallMs),
    validationRejections: sum(m => m.validationRejections),
    rejectionMessages: [...new Set(parts.flatMap(m => m.rejectionMessages))],
    models: [...new Set(parts.flatMap(m => m.models))],
    acceptedWrongList: parts.flatMap(m => m.acceptedWrongList)
  };
}
