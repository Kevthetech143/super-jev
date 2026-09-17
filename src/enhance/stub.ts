import { validateEvaluation } from '../jev.ts';
import type { Answer, Evaluation, Request, StubScript } from './types.ts';

/**
 * Offline stand-in for the Jev adapter. It implements the same Evaluator
 * interface and reaches no network, so every test and the demo run with no API
 * key. A stub can reproduce recorded answers; it cannot produce evidence about
 * accuracy, latency or cost. Only a live run can do that.
 */
export class StubEvaluator {
  readonly requests: Request[] = [];
  private script: StubScript;
  private validate: boolean;
  private latencyMs: number;

  constructor(options: { script: StubScript; validate?: boolean; latencyMs?: number }) {
    if (typeof options?.script !== 'function') throw new Error('StubEvaluator needs a script function');
    this.script = options.script;
    this.validate = options.validate ?? false;
    this.latencyMs = options.latencyMs ?? 0;
  }

  async evaluate(request: Request, signal: AbortSignal): Promise<Evaluation> {
    signal?.throwIfAborted?.();
    const index = this.requests.length;
    this.requests.push(request);
    if (this.latencyMs) await new Promise(resolve => setTimeout(resolve, this.latencyMs));
    const evaluation = this.script(request, index);
    if (this.validate) validateEvaluation(request, evaluation);
    return evaluation;
  }
}

/**
 * Build a script from a table of recorded answers keyed by record id. The
 * request's own question keys decide what is returned, which is what lets a
 * test shuffle the input and still check the mapping.
 */
export function scriptFromTable(
  table: Record<string, Answer>,
  options: { keyToId?: (key: string, request: Request) => string; usage?: { input_tokens: number; output_tokens: number }; model?: string; omit?: string[]; extraKeys?: Record<string, Answer> } = {}
): StubScript {
  const keyToId = options.keyToId ?? ((key, request) => {
    const records = (request.state as { records?: unknown })?.records;
    if (records && !Array.isArray(records) && typeof records === 'object') {
      const entry = (records as Record<string, { id?: string }>)[key];
      if (entry?.id) return entry.id;
    }
    if (Array.isArray(records)) {
      const match = /^record_(\d+)$/.exec(key);
      if (match) return (records[Number(match[1])] as { id?: string })?.id ?? key;
    }
    return key;
  });
  const omit = new Set(options.omit ?? []);
  return (request) => {
    const answers: Record<string, Answer> = {};
    for (const key of Object.keys(request.questions)) {
      const id = keyToId(key, request);
      if (omit.has(id) || omit.has(key)) continue;
      const answer = table[id];
      if (answer) answers[key] = structuredClone(answer);
    }
    for (const [key, answer] of Object.entries(options.extraKeys ?? {})) answers[key] = structuredClone(answer);
    return { model: options.model ?? 'stub-offline', answers, usage: options.usage };
  };
}

/**
 * Build a choice answer that satisfies the adapter contract, for fixtures.
 *
 * `confidence` and the probability distribution are separate fields, and a
 * recorded confidence is not always a usable probability share: the contract
 * requires the chosen option to be the distribution's argmax, which is
 * impossible when the confidence is below 1/options. So the chosen option's
 * mass is floored at 1/options while the recorded confidence is carried
 * through untouched. The distribution here is synthetic padding; only the
 * choice and the confidence come from the recorded run.
 */
export function choiceAnswer(choice: string, confidence: number, options: string[]): Answer {
  if (!options.includes(choice)) throw new Error(`choiceAnswer: ${choice} is not one of the options`);
  const rest = options.filter(o => o !== choice);
  const lead = Math.max(confidence, 1 / options.length);
  const share = rest.length ? (1 - lead) / rest.length : 0;
  const probabilities: Record<string, number> = { [choice]: lead };
  for (const option of rest) probabilities[option] = share;
  return { type: 'choice', choice, probabilities, confidence };
}
