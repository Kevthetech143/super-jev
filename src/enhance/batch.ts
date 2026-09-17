import { budget as makeBudget, recordAllowance } from './budget.ts';
import type { BatchPlan, ContextBudget, EnhanceRecord, PlannedCall, Question } from './types.ts';

/**
 * Estimated tokens for one record's own question, built from the very question
 * object the request builder will send. Serializing the question is how the
 * repeated option descriptions get counted once per record, which is what they
 * actually cost on the wire.
 */
export function questionTokens(question: Question, b: ContextBudget): number {
  return b.tokenEstimator(JSON.stringify(question));
}

/**
 * A caller-supplied cost for the question that will accompany a record. The
 * index is the record's position inside its call, because the positional
 * framing names `records[i]` in the question text.
 */
export type QuestionCost = (record: EnhanceRecord, indexInCall: number) => number;

/** What one record costs on the wire: its text and its id, both of which are sent. */
export function recordTokens(record: EnhanceRecord, b: ContextBudget): number {
  return b.tokenEstimator(record.text) + b.tokenEstimator(record.id);
}

/**
 * Per-record JSON envelope: the braces, the two field names, the quotes and the
 * commas that wrap every record and every question in the request object.
 * Measured from the shapes reference.ts builds, not guessed. INFERRED, and
 * counted because leaving it out is how an estimator quietly under-reports.
 */
export const PER_RECORD_ENVELOPE_TOKENS = 12;

/**
 * Estimated input tokens for the call that carries exactly these records.
 *
 * This is the one arithmetic the planner and the runner share, so a plan cannot
 * promise one number while the request builder produces another.
 *
 * With a `questionCost` the estimate is honest per record: every record pays
 * for its own question text, so repeated option descriptions are counted as
 * many times as they are sent. Without one, the estimate falls back to the flat
 * `reservedForQuestions` reservation, which is a reservation and not a count.
 */
export function callInputTokens(records: EnhanceRecord[], b: ContextBudget, questionCost?: QuestionCost): number {
  return packedTokens(records, b, questionCost) + (questionCost ? 0 : b.reservedForQuestions);
}

/**
 * The part of a call the allowance is measured against: record payload, plus
 * question text and envelope when questions are counted. The flat
 * `reservedForQuestions` is deliberately NOT included, because in the uncounted
 * mode it is already subtracted out of the allowance; counting it twice would
 * shrink the window for no reason.
 */
export function packedTokens(records: EnhanceRecord[], b: ContextBudget, questionCost?: QuestionCost): number {
  return records.reduce((n, r, i) => n + recordTokens(r, b) + (questionCost ? questionCost(r, i) + PER_RECORD_ENVELOPE_TOKENS : 0), 0);
}

/**
 * Tokens one call may spend on records and their questions.
 *
 * When questions are counted for real there is nothing left to reserve for
 * them, so the whole input window minus the output reservation is available and
 * `reservedForQuestions` is not subtracted a second time. That is the only
 * difference, and it is why an honest estimate does not shrink the usable
 * window.
 */
export function callAllowance(b: ContextBudget, counted: boolean): number {
  return counted ? b.maxInputTokens - b.reservedForOutput : recordAllowance(b);
}

/**
 * Pack records into calls that fit the budget, and return the plan BEFORE any
 * call happens. The plan is the thing a caller reviews or a test asserts on.
 *
 * Three rules the planner will not bend:
 * 1. A call never exceeds maxRecordsPerCall, whatever the token arithmetic says.
 * 2. A record whose own cost exceeds the per-call allowance is never packed. It
 *    lands in oversizedRecordIds. There is no "it will probably fit".
 * 3. A planned call's estimate never exceeds maxInputTokens. When questions are
 *    counted, that bound covers the question text too, so the plan splits or
 *    refuses instead of reporting a number the request builder will overshoot.
 */
export function planBatches(records: EnhanceRecord[], overrides: Partial<ContextBudget> = {}, questionCost?: QuestionCost): BatchPlan {
  const b = makeBudget(overrides);
  const counted = questionCost !== undefined;
  const allowance = callAllowance(b, counted);
  const calls: PlannedCall[] = [];
  const oversizedRecordIds: string[] = [];
  let current: EnhanceRecord[] = [];

  const flush = () => {
    if (!current.length) return;
    calls.push({ index: calls.length, recordIds: current.map(r => r.id), estimatedInputTokens: callInputTokens(current, b, questionCost), overBudget: false });
    current = [];
  };
  // Cost of adding this record to the call it would land in. With questions
  // counted, the cost depends on the position it would take, so it is measured
  // there rather than assumed.
  const costIn = (record: EnhanceRecord, position: number) =>
    recordTokens(record, b) + (questionCost ? questionCost(record, position) + PER_RECORD_ENVELOPE_TOKENS : 0);

  for (const record of records) {
    if (costIn(record, 0) > allowance) { oversizedRecordIds.push(record.id); continue; }
    if (current.length && (current.length >= b.maxRecordsPerCall || packedTokens([...current, record], b, questionCost) > allowance)) flush();
    // After a flush the record lands at position 0 of a fresh call, which is
    // exactly the case checked above, so it always fits.
    current.push(record);
  }
  flush();

  return {
    calls,
    oversizedRecordIds,
    budget: b,
    perCallRecordAllowance: allowance,
    questionsCounted: counted,
    totalEstimatedInputTokens: calls.reduce((n, c) => n + c.estimatedInputTokens, 0)
  };
}

/** Human-readable plan, printed before any provider call in the demo. */
export function formatPlan(plan: BatchPlan): string {
  const lines = [
    `plan: ${plan.calls.length} call(s), max ${plan.budget.maxRecordsPerCall} records per call`,
    plan.questionsCounted
      ? `budget: maxInputTokens=${plan.budget.maxInputTokens} output=${plan.budget.reservedForOutput} perCallAllowance=${plan.perCallRecordAllowance} (question text counted per record, not reserved flat)`
      : `budget: maxInputTokens=${plan.budget.maxInputTokens} questions=${plan.budget.reservedForQuestions} output=${plan.budget.reservedForOutput} recordAllowance=${plan.perCallRecordAllowance} (questions reserved flat, NOT counted)`
  ];
  for (const call of plan.calls) lines.push(`  call ${call.index}: ${call.recordIds.length} record(s) [${call.recordIds.join(', ')}] est ${call.estimatedInputTokens} input tokens`);
  if (plan.oversizedRecordIds.length) lines.push(`  excluded as oversized (never sent): ${plan.oversizedRecordIds.join(', ')}`);
  lines.push(`  total estimated input tokens: ${plan.totalEstimatedInputTokens} (estimate, not a billing figure)`);
  return lines.join('\n');
}
