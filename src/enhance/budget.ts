import type { ContextBudget } from './types.ts';

/**
 * Deterministic character-based token estimate. Four characters per token is a
 * rough rule of thumb, not a tokenizer. It exists so the batcher can refuse to
 * overfill a call; it is never reported as a billed token count.
 */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  return Math.ceil(text.length / 4);
}

/**
 * Defaults chosen from the pilot, not from theory:
 * - maxRecordsPerCall 4, because batches of four scored better than one large
 *   batch in the recorded pilot and keep a single bad call cheap to retry.
 * - the reservations leave room for per-record questions and for the answer
 *   block, which grows with the number of records in the call.
 */
export const DEFAULT_BUDGET: ContextBudget = {
  maxInputTokens: 8000,
  reservedForQuestions: 1500,
  reservedForOutput: 2000,
  tokenEstimator: estimateTokens,
  maxRecordsPerCall: 4
};

export function budget(overrides: Partial<ContextBudget> = {}): ContextBudget {
  const merged = { ...DEFAULT_BUDGET, ...overrides };
  if (!Number.isFinite(merged.maxInputTokens) || merged.maxInputTokens <= 0) throw new Error('maxInputTokens must be a positive number');
  for (const field of ['reservedForQuestions', 'reservedForOutput'] as const) {
    if (!Number.isFinite(merged[field]) || merged[field] < 0) throw new Error(`${field} must be zero or more`);
  }
  if (!Number.isInteger(merged.maxRecordsPerCall) || merged.maxRecordsPerCall < 1) throw new Error('maxRecordsPerCall must be a positive integer');
  if (typeof merged.tokenEstimator !== 'function') throw new Error('tokenEstimator must be a function');
  if (recordAllowance(merged) <= 0) throw new Error('Reservations leave no room for records; raise maxInputTokens or lower the reservations');
  return merged;
}

/** Tokens available for record payload in one call, after reservations. */
export function recordAllowance(b: ContextBudget): number {
  return b.maxInputTokens - b.reservedForQuestions - b.reservedForOutput;
}
