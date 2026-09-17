import test from 'node:test';
import assert from 'node:assert/strict';
import { actionAllowed, decideOutcome, readAnswer, DEFAULT_GATE } from '../../src/enhance/outcome.ts';
import { choiceAnswer } from '../../src/enhance/stub.ts';

const pass = (n: number, value: string, confidence: number) => ({ pass: n, answered: true, value, confidence });

test('agreement plus confidence accepts', () => {
  const outcome = decideOutcome('r', [pass(1, 'invoice', 0.99), pass(2, 'invoice', 0.92)]);
  assert.equal(outcome.kind, 'accepted');
  assert.equal(outcome.value, 'invoice');
  assert.equal(outcome.confidence, 0.92, 'the lowest confidence across passes is reported');
  assert.ok(actionAllowed(outcome));
});

test('disagreement blocks action no matter how confident either pass is', () => {
  const outcome = decideOutcome('r', [pass(1, 'contract', 1), pass(2, 'support', 1)]);
  assert.equal(outcome.kind, 'review');
  assert.equal(outcome.value, undefined, 'a blocked record carries no accepted value');
  assert.match(outcome.reason, /disagreement blocks action whatever the confidence/);
  assert.equal(actionAllowed(outcome), false);
});

test('confidence alone never accepts: incomplete evidence outranks a perfect score', () => {
  const outcome = decideOutcome('r', [pass(1, 'eligible', 1), pass(2, 'eligible', 1)], {}, 'policy document is missing');
  assert.equal(outcome.kind, 'insufficient_evidence');
  assert.equal(outcome.reason, 'policy document is missing');
  assert.equal(actionAllowed(outcome), false);
});

test('an evidence failure before any call reports insufficient evidence, not unanswered', () => {
  const outcome = decideOutcome('r', [], {}, 'two live policies with no precedence');
  assert.equal(outcome.kind, 'insufficient_evidence');
  assert.deepEqual(outcome.passes, []);
});

test('low confidence goes to review and keeps the value for the human', () => {
  const outcome = decideOutcome('r', [pass(1, 'invoice', 0.4)]);
  assert.equal(outcome.kind, 'review');
  assert.equal(outcome.value, 'invoice');
  assert.match(outcome.reason, /below the 0.75 gate/);
});

test('choosing the abstain option is an abstention, not a review', () => {
  const outcome = decideOutcome('r', [pass(1, 'other', 0.98), pass(2, 'other', 0.97)], { abstainValue: 'other' });
  assert.equal(outcome.kind, 'abstain');
  assert.equal(actionAllowed(outcome), false);
});

test('a low-confidence abstention still abstains rather than accepting', () => {
  assert.equal(decideOutcome('r', [pass(1, 'other', 0.2)], { abstainValue: 'other' }).kind, 'abstain');
});

test('no answer at all is unanswered; a partial answer across passes is review', () => {
  assert.equal(decideOutcome('r', [{ pass: 1, answered: false }]).kind, 'unanswered');
  assert.equal(decideOutcome('r', []).kind, 'unanswered');
  const partial = decideOutcome('r', [pass(1, 'invoice', 1), { pass: 2, answered: false }]);
  assert.equal(partial.kind, 'review');
  assert.match(partial.reason, /pass 2 returned no answer/);
});

test('a malformed answer is failed_validation', () => {
  const outcome = decideOutcome('r', [pass(1, 'invoice', 1), { pass: 2, answered: true, malformed: true }]);
  assert.equal(outcome.kind, 'failed_validation');
});

test('every outcome keeps the per-pass trail for inspection', () => {
  const outcome = decideOutcome('r', [pass(1, 'contract', 0.81), pass(2, 'support', 1)]);
  assert.deepEqual(outcome.passes, [{ pass: 1, value: 'contract', confidence: 0.81 }, { pass: 2, value: 'support', confidence: 1 }]);
});

test('turning agreement off is possible and demonstrates what the rule prevents', () => {
  const outcome = decideOutcome('r', [pass(1, 'contract', 0.81), pass(2, 'support', 1)], { requireAgreement: false });
  assert.equal(outcome.kind, 'accepted', 'without the rule the first pass wins on confidence alone');
  assert.equal(DEFAULT_GATE.requireAgreement, true, 'the default keeps the rule on');
});

test('readAnswer handles every answer shape and rejects junk', () => {
  assert.deepEqual(readAnswer(choiceAnswer('invoice', 0.9, ['invoice', 'other'])), { value: 'invoice', confidence: 0.9, peak: 0.9, malformed: false });
  assert.deepEqual(readAnswer({ type: 'score', score: 2, probabilities: {}, confidence: 0.8 }), { value: '2', confidence: 0.8, peak: undefined, malformed: false }, 'an empty distribution yields no peak, so there is nothing to cross-check');
  assert.equal(readAnswer({ type: 'noul', noul: 0.75 }).value, 'true');
  assert.equal(readAnswer({ type: 'noul', noul: 0.25 }).value, 'false');
  assert.equal(readAnswer(undefined).malformed, true);
  assert.equal(readAnswer({ type: 'choice', choice: 'a', probabilities: {}, confidence: Number.NaN } as never).malformed, true);
});

// --- the confidence-versus-peak cross-check ---------------------------------
//
// Confidence is the model's own number about its answer. The distribution is a
// second number about the same answer. These tests pin what happens when the
// two disagree, and, just as importantly, what happens when they disagree by
// only the amount a real provider answer was observed to disagree by.

/** A choice answer built by hand, so confidence and the distribution can be set independently. */
const overclaiming = (choice: string, confidence: number, probabilities: Record<string, number>) =>
  ({ type: 'choice' as const, choice, confidence, probabilities });

test('a confidence far above the distribution peak goes to review, never accepted', () => {
  const answer = overclaiming('invoice', 0.99, { invoice: 0.34, receipt: 0.33, other: 0.33 });
  const read = readAnswer(answer);
  assert.equal(read.confidence, 0.99);
  assert.equal(read.peak, 0.34);
  const outcome = decideOutcome('r', [{ pass: 1, answered: true, value: read.value, confidence: read.confidence, peak: read.peak }]);
  assert.equal(outcome.kind, 'review');
  assert.match(outcome.reason, /confidence overclaims the answer's own distribution/);
  assert.match(outcome.reason, /confidence 0.99 vs peak probability 0.34/);
  assert.equal(outcome.value, 'invoice', 'the value is kept for the human who reviews it');
  assert.equal(actionAllowed(outcome), false);
});

test('the live-observed 0.71-over-0.66 gap passes the cross-check instead of being called a fault', () => {
  // Recorded live on 2026-09-17, model jev-1.13.0: a score answer with
  // confidence 0.71 over a peak probability of 0.66, otherwise exactly
  // consistent with its own distribution. A strict rule would reject it.
  const answer = { type: 'score' as const, score: 1.85, confidence: 0.71, probabilities: { 0: 0.01, 1: 0.23, 2: 0.66, 3: 0.1, 4: 0 } };
  const read = readAnswer(answer);
  assert.equal(read.peak, 0.66);
  assert.ok(read.confidence! - read.peak! > DEFAULT_GATE.maxConfidenceAbovePeak - 1e-9, 'this really is a gap of one whole tolerance, not a comfortable margin');
  const outcome = decideOutcome('r', [{ pass: 1, answered: true, value: read.value, confidence: read.confidence, peak: read.peak }], { minConfidence: 0.7 });
  assert.equal(outcome.kind, 'accepted');
  assert.doesNotMatch(outcome.reason, /overclaims/);
  assert.match(outcome.reason, /consistent with the reported distribution/);
});

test('the tolerance is 0.05 and is documented as a harness choice, not a provider rule', () => {
  assert.equal(DEFAULT_GATE.maxConfidenceAbovePeak, 0.05);
  const justOver = readAnswer(overclaiming('a', 0.72, { a: 0.66, b: 0.34 }));
  assert.equal(decideOutcome('r', [{ pass: 1, answered: true, value: justOver.value, confidence: justOver.confidence, peak: justOver.peak }], { minConfidence: 0.7 }).kind, 'review');
});

test('a rounding-sized total does not move the peak, so a 0.99 distribution is judged the same as a 1.00 one', () => {
  // The provider sends two-decimal probabilities that total 0.99 or 1.00. The
  // peak is normalized, so the cross-check does not fire on rounding alone.
  assert.equal(readAnswer(overclaiming('a', 0.5, { a: 0.5, b: 0.49 }))!.peak, 0.5 / 0.99);
});

test('a confidence below the peak is never a fault: under-claiming is fine', () => {
  const read = readAnswer(overclaiming('a', 0.8, { a: 0.99, b: 0.01 }));
  assert.equal(decideOutcome('r', [{ pass: 1, answered: true, value: read.value, confidence: read.confidence, peak: read.peak }]).kind, 'accepted');
});

test('with no distribution the gate falls back to confidence and says so', () => {
  const noul = readAnswer({ type: 'noul', noul: 0.95 });
  assert.equal(noul.peak, undefined, 'a noul answer carries no distribution');
  const outcome = decideOutcome('r', [{ pass: 1, answered: true, value: noul.value, confidence: noul.confidence, peak: noul.peak }]);
  assert.equal(outcome.kind, 'accepted');
  assert.equal(outcome.confidenceOnly, true);
  assert.match(outcome.reason, /on confidence alone because no pass reported a distribution/);
});

test('one pass with a distribution is enough to stop the confidenceOnly marker', () => {
  const withPeak = { pass: 1, answered: true, value: 'a', confidence: 0.9, peak: 0.9 };
  const without = { pass: 2, answered: true, value: 'a', confidence: 0.9 };
  assert.equal(decideOutcome('r', [withPeak, without]).confidenceOnly, undefined);
  assert.equal(decideOutcome('r', [without]).confidenceOnly, true);
});

test('the overclaim check cannot be reached past disagreement or a malformed answer', () => {
  const bad = { pass: 1, answered: true, value: 'a', confidence: 1, peak: 0.2 };
  assert.match(decideOutcome('r', [bad, { pass: 2, answered: true, value: 'b', confidence: 1, peak: 0.9 }]).reason, /disagreement blocks action/);
  assert.equal(decideOutcome('r', [bad, { pass: 2, answered: true, malformed: true }]).kind, 'failed_validation');
  assert.equal(decideOutcome('r', [bad], { abstainValue: 'a' }).kind, 'abstain');
});

test('an unanswered record is not marked confidenceOnly, because nothing was judged', () => {
  assert.equal(decideOutcome('r', [{ pass: 1, answered: false }]).confidenceOnly, undefined);
});
