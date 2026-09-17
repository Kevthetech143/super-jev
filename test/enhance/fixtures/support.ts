import { readFile } from 'node:fs/promises';
import { choiceAnswer } from '../../../src/enhance/stub.ts';
import type { Answer } from '../../../src/types.ts';
import type { EvidenceSpec, SourceDoc } from '../../../src/enhance/evidence.ts';

type Recorded = { choice: string; confidence: number };

const raw = JSON.parse(await readFile(new URL('./recorded-answers.json', import.meta.url), 'utf8')) as {
  note: string;
  classification: Record<string, Record<string, Recorded>>;
  investigation: Record<string, Record<string, Recorded>>;
};

export const recordedNote = raw.note;

/** Turn a recorded answer table into stub-ready choice answers. */
export function answerTable(table: Record<string, Recorded>, options: string[]): Record<string, Answer> {
  return Object.fromEntries(Object.entries(table).map(([id, a]) => [id, choiceAnswer(a.choice, a.confidence, options)]));
}

export const recorded = {
  classification: raw.classification,
  investigation: raw.investigation
};

/**
 * The return-eligibility evidence chain: ticket then order then policy.
 *
 * The identify predicates are deliberately narrow and corpus-specific; they
 * belong to this fixture, not to src/enhance. Roles are tried in order and a
 * document fills at most one, which is why the order doc is matched on the
 * literal "Purchase age:" field rather than on the phrase appearing in prose.
 */
export const returnPolicySpec: EvidenceSpec = {
  name: 'ticket -> order -> policy',
  maxDepth: 3,
  maxDocs: 16,
  roles: [
    { name: 'ticket', identify: (doc, rootId) => doc.id === rootId },
    { name: 'order', identify: doc => /purchase age:\s*\d+/i.test(doc.text) },
    { name: 'policy', identify: doc => /\beligible\b/i.test(doc.text) }
  ]
};

/** Same chain with the narrow prose-reference parser switched on. */
export const returnPolicySpecWithProse: EvidenceSpec = { ...returnPolicySpec, proseReferences: true };

export const eligibilityOptions = {
  eligible: 'Return allowed by the applicable policy',
  ineligible: 'Return disallowed by the applicable policy',
  unknown: 'Insufficient, ambiguous or conflicting evidence'
};

/** Flatten every investigation's documents into one pool. */
export function poolFrom(investigations: { docs: SourceDoc[] }[]): SourceDoc[] {
  return investigations.flatMap(c => c.docs);
}
