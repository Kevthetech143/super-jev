/**
 * The six conditions the live measurement runs, and the two record sets it
 * runs them on.
 *
 * Conditions A and B are the baseline the pilot actually measured: the
 * organizer path, positional `records[i]` references, every record in one
 * call. Conditions C to F are the context-enhancer path: named
 * `records.<id>.text` references, batches of four, one or two passes, with the
 * gate deciding what may be acted on. The only reason to run all six is that a
 * claim about the enhancer is worthless without the baseline it is measured
 * against, under the same labels, in the same session, on the same day.
 */
import { categories, documents } from './fixtures/cases.ts';
import { holdout } from './fixtures/holdout.ts';
import type { PassSpec } from '../src/enhance/classify.ts';
import type { OrderSpec } from './shuffle.ts';
import type { LabeledRecord } from './metrics.ts';

export { categories };

export const PILOT_32 = 'pilot-32';
export const HOLDOUT_16 = 'holdout-16';

export type RecordSet = { id: string; records: LabeledRecord[]; note: string };

export const RECORD_SETS: RecordSet[] = [
  {
    id: PILOT_32,
    records: documents as LabeledRecord[],
    note: '32 adversarial synthetic records, labels fixed before the pilot ran'
  },
  {
    id: HOLDOUT_16,
    records: holdout as LabeledRecord[],
    note: '16 holdout records written after the pilot hypothesis, labels fixed before any holdout call'
  }
];

/** The abstain option. Choosing it is a deliberate decline, not a low score. */
export const ABSTAIN_OPTION = 'other';

/** The organizer's own default, reused unchanged so the gate is not a new variable. */
export const GATE_MIN_CONFIDENCE = 0.75;

const FORWARD: Record<string, OrderSpec> = {
  [PILOT_32]: { kind: 'forward' },
  [HOLDOUT_16]: { kind: 'forward' }
};

/**
 * The perturbed order, chosen to match what the pilot actually measured on each
 * set: shuffle seed 1 on the 32, reverse on the 16.
 */
const PERTURBED: Record<string, OrderSpec> = {
  [PILOT_32]: { kind: 'shuffle', seed: 1 },
  [HOLDOUT_16]: { kind: 'reverse' }
};

/** A second, independent perturbation, so F asks whether E's result repeats. */
const SEED_2: Record<string, OrderSpec> = {
  [PILOT_32]: { kind: 'shuffle', seed: 2 },
  [HOLDOUT_16]: { kind: 'shuffle', seed: 2 }
};

const KEYED_FORWARD: PassSpec = { name: 'keyed-forward', framing: 'keyed', order: 'input' };
const KEYED_REVERSE: PassSpec = { name: 'keyed-reverse', framing: 'keyed', order: 'reverse' };

export type ConditionSpec = {
  id: string;
  label: string;
  engine: 'baseline' | 'enhancer';
  /** Order the records arrive in, per record set. */
  order: Record<string, OrderSpec>;
  /** Enhancer only. Two passes means disagreement can block a record. */
  passes?: PassSpec[];
  /** Enhancer only. The pilot's better-scoring batch size. */
  maxRecordsPerCall?: number;
};

export const CONDITIONS: ConditionSpec[] = [
  {
    id: 'A',
    label: 'baseline organizer, array references, one batch, forward order',
    engine: 'baseline',
    order: FORWARD
  },
  {
    id: 'B',
    label: 'baseline organizer, array references, one batch, perturbed order',
    engine: 'baseline',
    order: PERTURBED
  },
  {
    id: 'C',
    label: 'enhancer, named references, batches of 4, one pass, forward order',
    engine: 'enhancer',
    order: FORWARD,
    passes: [KEYED_FORWARD],
    maxRecordsPerCall: 4
  },
  {
    id: 'D',
    label: 'enhancer, named references, batches of 4, one pass, perturbed order',
    engine: 'enhancer',
    order: PERTURBED,
    passes: [KEYED_FORWARD],
    maxRecordsPerCall: 4
  },
  {
    id: 'E',
    label: 'enhancer full default, named references, batches of 4, two passes in opposite orders, disagreement blocks action',
    engine: 'enhancer',
    order: PERTURBED,
    passes: [KEYED_FORWARD, KEYED_REVERSE],
    maxRecordsPerCall: 4
  },
  {
    id: 'F',
    label: 'condition E repeated from a second independent shuffle (order-repeat check)',
    engine: 'enhancer',
    order: SEED_2,
    passes: [KEYED_FORWARD, KEYED_REVERSE],
    maxRecordsPerCall: 4
  }
];

/** Pilot figures, carried only as context for the report. Never used in scoring. */
export const PILOT_REFERENCE: Record<string, Record<string, string>> = {
  A: { [PILOT_32]: '28/32 correct', [HOLDOUT_16]: '16/16 correct' },
  B: { [PILOT_32]: '19/32 correct (shuffled)', [HOLDOUT_16]: '10/16 correct (reverse)' }
};
