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
type Passages = Record<string, string>;

/** The state's `passages` when they can be packed with other requests' (an object of strings), else undefined. */
function passagesOf(state: unknown): Passages | undefined {
  if (!state || typeof state !== 'object' || Array.isArray(state)) return undefined;
  const passages = (state as { passages?: unknown }).passages;
  if (!passages || typeof passages !== 'object' || Array.isArray(passages)) return undefined;
  return Object.values(passages).every(text => typeof text === 'string') ? passages as Passages : undefined;
}

/** The state without its packable passages: requests with the same shared state can share a call. */
function sharedState(state: unknown): unknown {
  if (!passagesOf(state)) return state;
  const { passages: _passages, ...rest } = state as { passages: Passages };
  return rest;
}

/**
 * Coalesces evaluate() calls made in the same tick into as few provider calls as
 * fit the budget. Questions are answered independently against the same state,
 * so a batched answer means the same as the unbatched one. Requests with an
 * identical state are merged; so are requests whose states differ only in their
 * `passages` (one content check per file): each request's passage keys get its
 * batch prefix, and its `Classify passages.<key>` references are rewritten to
 * match. Each request keeps its own question keys and answers.
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
      // A request's own passages ride in the merged state, so they count against the budget with its questions.
      const tokens = Object.values(request.questions).reduce((sum, q) => sum + estimateTokens(q) + QUESTION_OVERHEAD, 0)
        + estimateTokens(passagesOf(request.state));
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
      const key = JSON.stringify([!!passagesOf(item.request.state), sharedState(item.request.state)]);
      groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    for (const items of groups.values()) {
      const stateTokens = estimateTokens(sharedState(items[0]!.request.state));
      // First fit: each request joins the first batch it fits in, so one big request does not close a half-full batch.
      const batches: { items: Pending[]; used: number }[] = [];
      for (const item of items) {
        const batch = batches.find(b => b.used + item.tokens <= this.budget);
        if (batch) { batch.items.push(item); batch.used += item.tokens; }
        else batches.push({ items: [item], used: stateTokens + item.tokens });
      }
      for (const batch of batches) void this.send(batch.items);
    }
  }

  private async send(batch: Pending[]): Promise<void> {
    const live = batch.filter(item => !item.done);
    if (!live.length) return;
    const questions: Request['questions'] = {};
    let state = live[0]!.request.state;
    if (live.length > 1 && passagesOf(state)) {
      // Packed passages: prefix each request's passage keys like its question keys, and point its questions at them.
      const passages: Passages = {};
      live.forEach((item, i) => { for (const [key, text] of Object.entries(passagesOf(item.request.state)!)) passages[`b${i}_${key}`] = text; });
      state = { ...(sharedState(state) as object), passages };
      live.forEach((item, i) => {
        const own = Object.keys(passagesOf(item.request.state)!);
        for (const [key, q] of Object.entries(item.request.questions)) {
          questions[`b${i}_${key}`] = { ...q, instructions: q.instructions.replace(/passages\.([A-Za-z0-9_]+)/g, (ref, name: string) => own.includes(name) ? `passages.b${i}_${name}` : ref) };
        }
      });
    } else live.forEach((item, i) => { for (const [key, q] of Object.entries(item.request.questions)) questions[`b${i}_${key}`] = q; });
    const controller = new AbortController();
    // Once every caller has given up (each one's own timeout), nobody can use the
    // answer: stop the call so the process does not sit waiting for it.
    const giveUp = () => { if (live.every(item => item.done)) controller.abort(); };
    for (const item of live) item.onDone = giveUp;
    try {
      this.calls++;
      const evaluation = await this.withRetry({ state, questions }, controller.signal);
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
