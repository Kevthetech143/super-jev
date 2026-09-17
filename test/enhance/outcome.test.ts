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
  assert.deepEqual(readAnswer(choiceAnswer('invoice', 0.9, ['invoice', 'other'])), { value: 'invoice', confidence: 0.9, malformed: false });
  assert.deepEqual(readAnswer({ type: 'score', score: 2, probabilities: {}, confidence: 0.8 }), { value: '2', confidence: 0.8, malformed: false });
  assert.equal(readAnswer({ type: 'noul', noul: 0.75 }).value, 'true');
  assert.equal(readAnswer({ type: 'noul', noul: 0.25 }).value, 'false');
  assert.equal(readAnswer(undefined).malformed, true);
  assert.equal(readAnswer({ type: 'choice', choice: 'a', probabilities: {}, confidence: Number.NaN } as never).malformed, true);
});
