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
  /**
   * How far a reported confidence may sit above the peak of the answer's own
   * probability distribution before the answer is treated as overclaimed and
   * routed to review.
   *
   * INFERRED, not documented by the provider. A strict rule (any excess is a
   * fault) is wrong: the live probe of 2026-09-17 returned a score answer with
   * confidence 0.71 over a peak probability of 0.66, and that answer was
   * otherwise exactly consistent with its own distribution. So a rounding-and-
   * calibration-sized gap is tolerated and only a gross gap blocks. 0.05 is
   * chosen to sit just above the one real observation; it is a harness choice,
   * not a measured threshold, and tightening it needs more live data.
   */
  maxConfidenceAbovePeak: number;
};

export const DEFAULT_GATE: GateConfig = { minConfidence: 0.75, requireAgreement: true, maxConfidenceAbovePeak: 0.05 };

/**
 * Float slack for the peak comparison. The observed worst case, 0.71 - 0.66,
 * evaluates to 0.05000000000000004 in IEEE-754, so a bare `> 0.05` comparison
 * would reject the very answer the tolerance exists to admit.
 */
const FLOAT_SLACK = 1e-9;

export type PassResult = {
  pass: number;
  value?: string;
  confidence?: number;
  /**
   * Peak of the answer's probability distribution, when the answer carried one.
   * Absent for a noul answer and for any answer with no usable distribution.
   */
  peak?: number;
  malformed?: boolean;
  answered: boolean;
};

/**
 * Peak of an answer's own distribution, normalized so a rounding-sized total
 * (the provider sends two-decimal values summing to 0.99 or 1.00) does not
 * shift the peak. Returns undefined when there is no usable distribution, which
 * is the signal that the gate has only confidence to work with.
 */
export function peakProbability(answer: Answer | undefined): number | undefined {
  const p = (answer as { probabilities?: Record<string, number> } | undefined)?.probabilities;
  if (!p || typeof p !== 'object') return undefined;
  const values = Object.values(p).filter(v => typeof v === 'number' && Number.isFinite(v) && v >= 0);
  if (values.length !== Object.keys(p).length || !values.length) return undefined;
  const total = values.reduce((x, y) => x + y, 0);
  if (!(total > 0)) return undefined;
  return Math.max(...values) / total;
}

/** Read a choice/score answer into a comparable value plus its confidence. */
export function readAnswer(answer: Answer | undefined): { value?: string; confidence?: number; peak?: number; malformed: boolean } {
  if (!answer || typeof answer !== 'object') return { malformed: true };
  const peak = peakProbability(answer);
  if (answer.type === 'choice') {
    if (typeof answer.choice !== 'string' || !Number.isFinite(answer.confidence)) return { malformed: true };
    return { value: answer.choice, confidence: answer.confidence, peak, malformed: false };
  }
  if (answer.type === 'score') {
    if (!Number.isFinite(answer.score) || !Number.isFinite(answer.confidence)) return { malformed: true };
    return { value: String(answer.score), confidence: answer.confidence, peak, malformed: false };
  }
  if (answer.type === 'noul') {
    // A noul answer carries the single value and nothing else, so there is no
    // distribution to cross-check against.
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
 *   6. confidence overclaims its own distribution peak -> review
 *   7. confidence below the gate     -> review
 *   8. everything above satisfied    -> accepted
 *
 * Confidence never reaches step 8 on its own: a high number cannot skip steps
 * 1 to 6. That is the whole point of the ordering.
 *
 * Step 6 is the cross-check. Confidence is a number the model produces about
 * its own answer; the distribution is a second, separately reported number
 * about the same answer. When the two disagree grossly the answer is not
 * trusted, whatever the confidence says. When the answer carried no usable
 * distribution there is nothing to cross-check, and the outcome is marked
 * `confidenceOnly` so a reader knows the gate had only the model's own
 * self-report to go on.
 */
export function decideOutcome(id: string, passes: PassResult[], config: Partial<GateConfig> = {}, evidenceProblem?: string): RecordOutcome {
  const gate = { ...DEFAULT_GATE, ...config };
  const trail = passes.map(p => p.peak === undefined ? { pass: p.pass, value: p.value, confidence: p.confidence } : { pass: p.pass, value: p.value, confidence: p.confidence, peak: p.peak });
  // Only the passes that actually produced a usable answer decide whether the
  // gate had a distribution to work with.
  const answering = passes.filter(p => p.answered && !p.malformed);
  const confidenceOnly = answering.length > 0 && answering.every(p => p.peak === undefined);
  const make = (kind: OutcomeKind, reason: string, value?: string, confidence?: number): RecordOutcome =>
    confidenceOnly ? { id, kind, value, confidence, reason, passes: trail, confidenceOnly: true } : { id, kind, value, confidence, reason, passes: trail };

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
  const overclaimed = passes.filter(p => p.peak !== undefined && p.confidence! - p.peak > gate.maxConfidenceAbovePeak + FLOAT_SLACK);
  if (overclaimed.length) {
    return make('review', `confidence overclaims the answer's own distribution (${overclaimed.map(p => `p${p.pass}: confidence ${p.confidence} vs peak probability ${Number(p.peak!.toFixed(4))}`).join('; ')}); the tolerated gap is ${gate.maxConfidenceAbovePeak}`, value, lowest);
  }
  if (lowest < gate.minConfidence) return make('review', `lowest confidence ${lowest} is below the ${gate.minConfidence} gate`, value, lowest);
  return make('accepted', `all ${passes.length} pass(es) agreed on "${value}" at confidence >= ${gate.minConfidence}${confidenceOnly ? ', on confidence alone because no pass reported a distribution' : ' and consistent with the reported distribution'}`, value, lowest);
}

/** Outcomes that permit an automatic action. Nothing else does. */
export function actionAllowed(outcome: RecordOutcome): boolean {
  return outcome.kind === 'accepted';
}
