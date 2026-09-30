import type { Evaluation, Evaluator, Request } from '../types.ts';
import { JUDGE_PROFILE, judgeTokens } from '../judge-profile.ts';
import { retryOverloaded } from '../judge.ts';
import { TooBig } from '../judge-errors.ts';

/**
 * Estimated input budget for one batched judge call: the judge profile's
 * callTokens (for the state plus the longest question, under its ceiling).
 * Tokens are counted by judgeTokens, on the
 * high side so number-dense text is never under-counted, and a batch is kept under
 * this many estimated tokens.
 */
export const BATCH_TOKEN_BUDGET = JUDGE_PROFILE.callTokens;
const QUESTION_OVERHEAD = 20;

export const estimateTokens = judgeTokens;

type Pending = { request: Request; resolve: (e: Evaluation) => void; reject: (e: unknown) => void; tokens: number; done: boolean; onDone?: () => void };

/**
 * Coalesces evaluate() calls made in the same tick into as few provider calls as
 * fit the budget. Questions are answered independently against the same state,
 * so a batched answer means the same as the unbatched one. Only requests with an
 * identical state are merged; each keeps its own question keys and answers.
 */
export class BatchingEvaluator implements Evaluator {
  calls = 0;
  private pending: Pending[] = [];
  private scheduled = false;
  private inner: Evaluator;
  private budget: number;
  constructor(inner: Evaluator, budget = BATCH_TOKEN_BUDGET) { this.inner = inner; this.budget = budget; }

  evaluate(request: Request, signal: AbortSignal): Promise<Evaluation> {
    return new Promise((resolve, reject) => {
      const tokens = Object.values(request.questions).reduce((sum, q) => sum + estimateTokens(q) + QUESTION_OVERHEAD, 0);
      const item: Pending = { request, resolve, reject, tokens, done: false };
      signal.addEventListener('abort', () => settle(item, () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))), { once: true });
      this.pending.push(item);
      if (!this.scheduled) { this.scheduled = true; setImmediate(() => this.flush()); }
    });
  }

  private flush(): void {
    this.scheduled = false;
    const groups = new Map<string, Pending[]>();
    for (const item of this.pending.splice(0)) {
      if (item.done) continue;
      const key = JSON.stringify(item.request.state);
      groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    for (const items of groups.values()) {
      const stateTokens = estimateTokens(items[0]!.request.state);
      let batch: Pending[] = [];
      let used = stateTokens;
      for (const item of items) {
        if (batch.length && used + item.tokens > this.budget) { void this.send(batch); batch = []; used = stateTokens; }
        batch.push(item);
        used += item.tokens;
      }
      if (batch.length) void this.send(batch);
    }
  }

  private async send(batch: Pending[]): Promise<void> {
    const live = batch.filter(item => !item.done);
    if (!live.length) return;
    const questions: Request['questions'] = {};
    live.forEach((item, i) => { for (const [key, q] of Object.entries(item.request.questions)) questions[`b${i}_${key}`] = q; });
    const controller = new AbortController();
    // Once every caller has given up (each one's own timeout), nobody can use the
    // answer: stop the call so the process does not sit waiting for it.
    const giveUp = () => { if (live.every(item => item.done)) controller.abort(); };
    for (const item of live) item.onDone = giveUp;
    try {
      this.calls++;
      const evaluation = await this.withRetry({ state: live[0]!.request.state, questions }, controller.signal);
      live.forEach((item, i) => {
        const answers = Object.fromEntries(Object.keys(item.request.questions).map(key => [key, evaluation.answers[`b${i}_${key}`]!]));
        settle(item, () => item.resolve({ ...evaluation, answers }));
      });
    } catch (error) {
      // An estimate that still ran over the ceiling (HTTP 400): split and retry each half.
      if (live.length > 1 && error instanceof TooBig) {
        const half = Math.ceil(live.length / 2);
        await Promise.all([this.send(live.slice(0, half)), this.send(live.slice(half))]);
        return;
      }
      for (const item of live) settle(item, () => item.reject(error));
    }
  }

  private withRetry(request: Request, signal: AbortSignal): Promise<Evaluation> {
    return retryOverloaded(() => this.inner.evaluate(request, signal), signal);
  }
}

function settle(item: Pending, fn: () => void): void {
  if (item.done) return;
  item.done = true;
  fn();
  item.onDone?.();
}
