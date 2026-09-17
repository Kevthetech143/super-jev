import { budget as makeBudget, recordAllowance } from './budget.ts';
import type { BatchPlan, ContextBudget, EnhanceRecord, PlannedCall } from './types.ts';

/**
 * Pack records into calls that fit the budget, and return the plan BEFORE any
 * call happens. The plan is the thing a caller reviews or a test asserts on.
 *
 * Two rules the planner will not bend:
 * 1. A call never exceeds maxRecordsPerCall, whatever the token arithmetic says.
 * 2. A record whose own estimate exceeds the per-call record allowance is never
 *    packed. It lands in oversizedRecordIds. There is no "it will probably fit".
 */
export function planBatches(records: EnhanceRecord[], overrides: Partial<ContextBudget> = {}): BatchPlan {
  const b = makeBudget(overrides);
  const allowance = recordAllowance(b);
  const calls: PlannedCall[] = [];
  const oversizedRecordIds: string[] = [];
  let current: { ids: string[]; tokens: number } | null = null;

  const flush = () => {
    if (!current) return;
    calls.push({ index: calls.length, recordIds: current.ids, estimatedInputTokens: current.tokens + b.reservedForQuestions, overBudget: false });
    current = null;
  };

  for (const record of records) {
    const cost = b.tokenEstimator(record.text) + b.tokenEstimator(record.id);
    if (cost > allowance) { oversizedRecordIds.push(record.id); continue; }
    if (current && (current.ids.length >= b.maxRecordsPerCall || current.tokens + cost > allowance)) flush();
    if (!current) current = { ids: [], tokens: 0 };
    current.ids.push(record.id);
    current.tokens += cost;
  }
  flush();

  return {
    calls,
    oversizedRecordIds,
    budget: b,
    perCallRecordAllowance: allowance,
    totalEstimatedInputTokens: calls.reduce((n, c) => n + c.estimatedInputTokens, 0)
  };
}

/** Human-readable plan, printed before any provider call in the demo. */
export function formatPlan(plan: BatchPlan): string {
  const lines = [
    `plan: ${plan.calls.length} call(s), max ${plan.budget.maxRecordsPerCall} records per call`,
    `budget: maxInputTokens=${plan.budget.maxInputTokens} questions=${plan.budget.reservedForQuestions} output=${plan.budget.reservedForOutput} recordAllowance=${plan.perCallRecordAllowance}`
  ];
  for (const call of plan.calls) lines.push(`  call ${call.index}: ${call.recordIds.length} record(s) [${call.recordIds.join(', ')}] est ${call.estimatedInputTokens} input tokens`);
  if (plan.oversizedRecordIds.length) lines.push(`  excluded as oversized (never sent): ${plan.oversizedRecordIds.join(', ')}`);
  lines.push(`  total estimated input tokens: ${plan.totalEstimatedInputTokens} (estimate, not a billing figure)`);
  return lines.join('\n');
}
