import type { Answer, OutcomeKind, RecordOutcome } from './types.ts';

export type GateConfig = {
  /** Minimum confidence for an accept. Necessary, never sufficient. */
  minConfidence: number;
  /**
   * The option that means "the model declined to pick". Answering with it is a
   * deliberate abstention, not a low-confidence accident.
   */
  abstainValue?: string;
  /**
   * When more than one pass ran, disagreement blocks action. Set false only to
   * demonstrate what the rule prevents; production callers leave it true.
   */
  requireAgreement: boolean;
};

export const DEFAULT_GATE: GateConfig = { minConfidence: 0.75, requireAgreement: true };

export type PassResult = { pass: number; value?: string; confidence?: number; malformed?: boolean; answered: boolean };

/** Read a choice/score answer into a comparable value plus its confidence. */
export function readAnswer(answer: Answer | undefined): { value?: string; confidence?: number; malformed: boolean } {
  if (!answer || typeof answer !== 'object') return { malformed: true };
  if (answer.type === 'choice') {
    if (typeof answer.choice !== 'string' || !Number.isFinite(answer.confidence)) return { malformed: true };
    return { value: answer.choice, confidence: answer.confidence, malformed: false };
  }
  if (answer.type === 'score') {
    if (!Number.isFinite(answer.score) || !Number.isFinite(answer.confidence)) return { malformed: true };
    return { value: String(answer.score), confidence: answer.confidence, malformed: false };
  }
  if (answer.type === 'noul') {
    if (!Number.isFinite(answer.noul)) return { malformed: true };
    return { value: answer.noul >= 0.5 ? 'true' : 'false', confidence: Math.abs(answer.noul - 0.5) * 2, malformed: false };
  }
  return { malformed: true };
}

/**
 * The gate. Order of checks is the policy:
 *   0. evidence incomplete, nothing asked -> insufficient_evidence
 *   1. no answer at all              -> unanswered
 *   2. a malformed answer            -> failed_validation
 *   3. evidence incomplete in code   -> insufficient_evidence  (caller-supplied)
 *   4. passes disagree               -> review                 (regardless of confidence)
 *   5. the model abstained           -> abstain
 *   6. confidence below the gate     -> review
 *   7. everything above satisfied    -> accepted
 *
 * Confidence never reaches step 7 on its own: a high number cannot skip steps
 * 1 to 5. That is the whole point of the ordering.
 */
export function decideOutcome(id: string, passes: PassResult[], config: Partial<GateConfig> = {}, evidenceProblem?: string): RecordOutcome {
  const gate = { ...DEFAULT_GATE, ...config };
  const trail = passes.map(p => ({ pass: p.pass, value: p.value, confidence: p.confidence }));
  const make = (kind: OutcomeKind, reason: string, value?: string, confidence?: number): RecordOutcome => ({ id, kind, value, confidence, reason, passes: trail });

  // Nothing was asked because the evidence check already failed. Reporting
  // this as "unanswered" would hide the reason, so it short-circuits.
  if (evidenceProblem && !passes.length) return make('insufficient_evidence', evidenceProblem);
  if (!passes.length || passes.every(p => !p.answered)) return make('unanswered', 'no pass returned an answer for this record');
  const unanswered = passes.filter(p => !p.answered);
  if (unanswered.length) return make('review', `pass ${unanswered.map(p => p.pass).join(', ')} returned no answer for this record`);
  const malformed = passes.filter(p => p.malformed);
  if (malformed.length) return make('failed_validation', `pass ${malformed.map(p => p.pass).join(', ')} returned a malformed answer`);

  const values = passes.map(p => p.value!);
  const confidences = passes.map(p => p.confidence!);
  const lowest = Math.min(...confidences);
  const value = values[0];

  if (evidenceProblem) return make('insufficient_evidence', evidenceProblem, value, lowest);
  if (gate.requireAgreement && passes.length > 1 && new Set(values).size > 1) {
    return make('review', `passes disagreed (${passes.map(p => `p${p.pass}=${p.value}@${p.confidence}`).join(' vs ')}); disagreement blocks action whatever the confidence`, undefined, lowest);
  }
  if (gate.abstainValue !== undefined && values.every(v => v === gate.abstainValue)) {
    return make('abstain', `the model chose the abstain option "${gate.abstainValue}"`, value, lowest);
  }
  if (lowest < gate.minConfidence) return make('review', `lowest confidence ${lowest} is below the ${gate.minConfidence} gate`, value, lowest);
  return make('accepted', `all ${passes.length} pass(es) agreed on "${value}" at confidence >= ${gate.minConfidence}`, value, lowest);
}

/** Outcomes that permit an automatic action. Nothing else does. */
export function actionAllowed(outcome: RecordOutcome): boolean {
  return outcome.kind === 'accepted';
}
