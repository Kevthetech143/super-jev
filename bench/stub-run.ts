/**
 * The offline stub provider used by `--stub` and by the offline test.
 *
 * A stub proves the plumbing and nothing else. It can show that every record is
 * accounted for, that the mapping survives a shuffle, that disagreement blocks
 * a record and that the accepted-error arithmetic is right. It cannot say
 * anything about accuracy, latency or cost, because the answers are ours.
 */
import { StubEvaluator, choiceAnswer } from '../src/enhance/stub.ts';
import type { Answer, Evaluation, Request } from '../src/enhance/types.ts';
import { RECORD_SETS, categories } from './conditions.ts';
import type { EvaluatorFactory } from './live-measure.ts';
import type { LabeledRecord } from './metrics.ts';

const OPTIONS = Object.keys(categories);

/** Deliberate faults, so an offline run exercises every outcome kind. */
export const SCRIPTED = {
  /** Answered wrongly at high confidence: these become accepted errors. */
  wrongAndConfident: { D16: 'receipt', H14: 'receipt' } as Record<string, string>,
  /** Answered correctly but under the gate: these become review. */
  lowConfidence: ['D22', 'H10'],
  /** Flips on the second ask, so a two-pass condition sees disagreement. */
  flipsOnSecondAsk: { D01: 'support' } as Record<string, string>
};

const HIGH = 0.92;
const LOW = 0.4;

export function labelTable(records: readonly LabeledRecord[]): Record<string, { choice: string; confidence: number }> {
  return Object.fromEntries(records.map(r => {
    const choice = SCRIPTED.wrongAndConfident[r.id] ?? r.expected;
    const confidence = SCRIPTED.lowConfidence.includes(r.id) ? LOW : HIGH;
    return [r.id, { choice, confidence }];
  }));
}

/** Map an asked question key back to a record id, for either framing. */
export function idForKey(key: string, request: Request): string {
  const records = (request.state as { records?: unknown })?.records;
  if (Array.isArray(records)) {
    const match = /^record_(\d+)$/.exec(key);
    if (match) return (records[Number(match[1])] as { id?: string })?.id ?? key;
    return key;
  }
  const entry = (records as Record<string, { id?: string }> | undefined)?.[key];
  return entry?.id ?? key;
}

/**
 * One scripted provider per condition run. The ask counter is per instance, so
 * a flipping record disagrees across passes inside one condition and does not
 * leak into the next condition.
 */
export function scriptedEvaluator(records: readonly LabeledRecord[]): StubEvaluator {
  const table = labelTable(records);
  const asks = new Map<string, number>();
  return new StubEvaluator({
    validate: true,
    script: (request: Request): Evaluation => {
      const answers: Record<string, Answer> = {};
      let inputTokens = 0;
      for (const key of Object.keys(request.questions)) {
        const id = idForKey(key, request);
        const entry = table[id];
        if (!entry) continue;
        const seen = (asks.get(id) ?? 0) + 1;
        asks.set(id, seen);
        const flip = SCRIPTED.flipsOnSecondAsk[id];
        const choice = flip && seen > 1 ? flip : entry.choice;
        answers[key] = choiceAnswer(choice, entry.confidence, OPTIONS);
        inputTokens += 40;
      }
      return {
        model: 'stub-offline',
        answers,
        usage: { input_tokens: inputTokens, output_tokens: Object.keys(answers).length * 12 }
      };
    }
  });
}

/** Factory for runBench: a fresh scripted provider per condition and set. */
export function stubFactory(sets = RECORD_SETS): EvaluatorFactory {
  return ({ set }) => {
    const records = sets.find(s => s.id === set)?.records ?? [];
    return { evaluator: scriptedEvaluator(records) };
  };
}
