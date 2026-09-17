// Domain-independent context-enhancer primitives.
// Every type here is about bookkeeping around a provider call, never about a
// specific business domain. Domain knowledge belongs in the caller's spec.
import type { Answer, Evaluation, Question, Request } from '../types.ts';

/** One input record. `id` is caller-supplied or assigned as `records/<n>`. */
export type EnhanceRecord = { id: string; text: string };

/**
 * A record plus the question key actually sent to the provider. Record ids are
 * arbitrary caller strings; question keys must be safe object keys, so the two
 * are kept separate and the pairing is carried explicitly rather than implied
 * by array position.
 */
export type RecordRef = { id: string; key: string; text: string };

/** Everything the batcher is allowed to spend. All values are token counts. */
export type ContextBudget = {
  maxInputTokens: number;
  reservedForQuestions: number;
  reservedForOutput: number;
  /** Deterministic, provider-independent estimate. Never a billing figure. */
  tokenEstimator: (text: string) => number;
  /** Hard cap on records per call, independent of token arithmetic. */
  maxRecordsPerCall: number;
};

export type PlannedCall = {
  index: number;
  /** Record ids in this call, in the order they will be sent. */
  recordIds: string[];
  estimatedInputTokens: number;
  /**
   * True when a single record does not fit the per-call record allowance. Such
   * a record is never silently packed; it is reported here and excluded.
   */
  overBudget: boolean;
};

export type BatchPlan = {
  calls: PlannedCall[];
  /** Record ids the budget cannot carry at all. Excluded from every call. */
  oversizedRecordIds: string[];
  budget: ContextBudget;
  /** Tokens one call may spend on records and, when counted, their questions. */
  perCallRecordAllowance: number;
  /**
   * True when the plan counted each record's actual question text instead of
   * reserving a flat `reservedForQuestions`. Only a counted plan's estimate is
   * comparable to what the request builder will actually send.
   */
  questionsCounted: boolean;
  totalEstimatedInputTokens: number;
};

/**
 * Terminal disposition of one record.
 * - accepted: every pass agreed and the gate passed. Action is allowed.
 * - review: a human must look. Includes cross-pass disagreement.
 * - abstain: the model deliberately chose the configured abstain option.
 * - insufficient_evidence: required evidence was incomplete in code, before
 *   or regardless of any model answer. No action.
 * - failed_validation: a malformed or rejected answer.
 * - unanswered: the record was asked about and no answer came back.
 * - unaccounted: bookkeeping bug. The manifest must never ship one of these.
 */
export type OutcomeKind =
  | 'accepted' | 'review' | 'abstain' | 'insufficient_evidence'
  | 'failed_validation' | 'unanswered' | 'unaccounted';

export type RecordOutcome = {
  id: string;
  kind: OutcomeKind;
  /** Chosen label when there is one. Absent for unanswered records. */
  value?: string;
  /** Lowest confidence across passes. Confidence alone never accepts. */
  confidence?: number;
  /** Plain-words explanation of why this kind was chosen. */
  reason: string;
  /** One entry per pass, so disagreement is inspectable after the fact. */
  passes: { pass: number; value?: string; confidence?: number; peak?: number }[];
  /**
   * True when no pass reported a usable probability distribution, so the gate
   * had only the model's own confidence number to work with and could not
   * cross-check it. Absent when at least one distribution was available.
   */
  confidenceOnly?: true;
};

export type CoverageManifest = {
  totalRecords: number;
  byKind: Record<OutcomeKind, string[]>;
  outcomes: RecordOutcome[];
  /** True only when every input record appears exactly once and none is unaccounted. */
  complete: boolean;
  problems: string[];
};

export type CostAccount = {
  calls: number;
  inputTokens: number;
  outputTokens: number;
  retries: number;
  wallMs: number;
  perCallLatency: number[];
  /**
   * True when any call's token figures came from the estimator instead of the
   * provider's usage block. Estimated totals are not a billing statement.
   */
  estimated: boolean;
};

/** Mapping report for a single call: which asked key produced which answer. */
export type MappingReport = {
  /** key -> record id, for the keys actually asked in this call. */
  asked: { key: string; id: string }[];
  answered: { key: string; id: string; answer: Answer }[];
  /** Asked keys with no answer. */
  missing: { key: string; id: string }[];
  /** Answer keys nobody asked for. Rejected, never mapped to a record. */
  rejected: string[];
};

export type StubScript = (request: Request, callIndex: number) => Evaluation;

export type { Answer, Evaluation, Question, Request };
