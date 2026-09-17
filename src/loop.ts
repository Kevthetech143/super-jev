import { randomUUID } from 'node:crypto';
import { validateEvaluation } from './jev.ts';
import type { Domain, Evaluator, Journal, Event } from './types.ts';

export async function run<S>(options: {
  domain: Domain<S>; initial: S; evaluator: Evaluator; journal: Journal;
  maxSteps?: number; timeoutMs?: number; maxRequestBytes?: number;
  maxRepeats?: number; signal?: AbortSignal;
  redact?: (event: Event) => Event;
}) {
  const { domain, evaluator, journal } = options;
  const maxSteps = options.maxSteps ?? 10;
  const timeoutMs = options.timeoutMs ?? 30_000;
  const maxBytes = options.maxRequestBytes ?? 100_000;
  const maxRepeats = options.maxRepeats ?? 2;
  for (const n of [maxSteps, timeoutMs, maxBytes, maxRepeats]) {
    if (!Number.isSafeInteger(n) || n < 1) throw new Error('Limits must be positive integers');
  }
  const runId = randomUUID();
  const signal = AbortSignal.any([AbortSignal.timeout(timeoutMs), ...(options.signal ? [options.signal] : [])]);
  let state = structuredClone(options.initial);
  let step = 0;
  const repeats = new Map<string, number>();
  async function log(type: string, data: unknown) {
    const event = { runId, timestamp: new Date().toISOString(), type, step, data: structuredClone(data) };
    await journal.append(options.redact ? options.redact(event) : event);
  }
  async function bounded<T>(fn: () => Promise<T>): Promise<T> {
    signal.throwIfAborted();
    return new Promise<T>((resolve, reject) => {
      const abort = () => reject(new Error('Run cancelled or timed out'));
      signal.addEventListener('abort', abort, { once: true });
      Promise.resolve().then(fn).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
    });
  }
  async function finish(status: string, reason: string) {
    await log('finished', { status, reason, state });
    return { runId, status, reason, state, steps: step };
  }
  await log('started', { domain: domain.name, state, limits: { maxSteps, timeoutMs, maxBytes, maxRepeats } });
  try {
    if (await bounded(() => domain.verify(state, signal))) return await finish('success', 'Goal already verified');
    for (step = 1; step <= maxSteps; step++) {
      const evidence = await bounded(() => domain.observe(state, signal));
      const request = { state: evidence, questions: domain.questions(state, evidence) };
      if (!Object.keys(request.questions).length) throw new Error('Domain returned no questions');
      if (Buffer.byteLength(JSON.stringify(request), 'utf8') > maxBytes) throw new Error('Context exceeds byte budget; reduce evidence');
      await log('request', request);
      const evaluation = await bounded(() => evaluator.evaluate(request, signal));
      validateEvaluation(request, evaluation);
      await log('evaluation', evaluation);
      const decision = domain.decide(state, evidence, evaluation);
      await log('decision', decision);
      if (decision.kind === 'stop') return await finish('stopped', decision.reason);
      const action = decision.action;
      if (!Object.hasOwn(domain.tools, action.tool)) return await finish('blocked', 'Unknown tool');
      const tool = domain.tools[action.tool];
      if (!tool.validate(action.args) || !domain.permit(state, action)) return await finish('blocked', 'Arguments or policy rejected action');
      const fingerprint = JSON.stringify(action);
      const count = (repeats.get(fingerprint) ?? 0) + 1;
      repeats.set(fingerprint, count);
      if (count > maxRepeats) return await finish('stopped', 'Repeated action limit');
      signal.throwIfAborted();
      const idempotencyKey = `${runId}:${step}`;
      // Persist intent before effects. A timeout may leave an uncertain external outcome.
      await log('action_started', { action, idempotencyKey });
      const outcome = await bounded(() => tool.execute(action.args, { signal, idempotencyKey }));
      await log('action_completed', { action, outcome });
      state = domain.reduce(state, action, outcome);
      await log('state', state);
      if (await bounded(() => domain.verify(state, signal))) return await finish('success', 'Goal verified');
    }
    step = maxSteps;
    return await finish('stopped', 'Step limit');
  } catch {
    // Provider/tool error text can contain credentials or private data; omit it from logs.
    return await finish('error', signal.aborted ? 'Cancelled or timed out; reconcile any started action' : 'Stage failed; inspect the last recorded event');
  }
}
