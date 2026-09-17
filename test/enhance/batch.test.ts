import test from 'node:test';
import assert from 'node:assert/strict';
import { formatPlan, planBatches } from '../../src/enhance/batch.ts';
import { budget, estimateTokens, recordAllowance, DEFAULT_BUDGET } from '../../src/enhance/budget.ts';

const records = (n: number, chars = 40) => Array.from({ length: n }, (_, i) => ({ id: `r${i}`, text: 'x'.repeat(chars) }));

test('the default budget caps a call at four records', () => {
  assert.equal(DEFAULT_BUDGET.maxRecordsPerCall, 4);
  const plan = planBatches(records(10));
  assert.deepEqual(plan.calls.map(c => c.recordIds.length), [4, 4, 2]);
  assert.equal(plan.calls[0].index, 0);
});

test('the plan is produced without any provider and reports tokens per call', () => {
  const plan = planBatches(records(5, 4000), { maxRecordsPerCall: 8 });
  for (const call of plan.calls) {
    assert.ok(call.estimatedInputTokens > 0);
    assert.ok(call.estimatedInputTokens <= plan.budget.maxInputTokens, 'a planned call must fit maxInputTokens');
  }
  assert.equal(plan.totalEstimatedInputTokens, plan.calls.reduce((n, c) => n + c.estimatedInputTokens, 0));
});

test('token pressure splits calls below the record cap', () => {
  const allowance = recordAllowance(budget());
  const big = 'y'.repeat(allowance * 4 * 0.6);
  const plan = planBatches([{ id: 'a', text: big }, { id: 'b', text: big }, { id: 'c', text: big }]);
  assert.deepEqual(plan.calls.map(c => c.recordIds), [['a'], ['b'], ['c']]);
});

test('a record too big for the budget is excluded, never optimistically packed', () => {
  const allowance = recordAllowance(budget());
  const plan = planBatches([{ id: 'ok', text: 'short' }, { id: 'huge', text: 'z'.repeat(allowance * 4 + 400) }]);
  assert.deepEqual(plan.oversizedRecordIds, ['huge']);
  assert.deepEqual(plan.calls.flatMap(c => c.recordIds), ['ok']);
  assert.match(formatPlan(plan), /excluded as oversized \(never sent\): huge/);
});

test('an empty input plans zero calls', () => {
  const plan = planBatches([]);
  assert.deepEqual(plan.calls, []);
  assert.equal(plan.totalEstimatedInputTokens, 0);
});

test('the budget refuses configurations that leave no room', () => {
  assert.throws(() => budget({ maxInputTokens: 100, reservedForQuestions: 60, reservedForOutput: 60 }), /leave no room/);
  assert.throws(() => budget({ maxRecordsPerCall: 0 }), /positive integer/);
  assert.throws(() => budget({ maxInputTokens: -1 }), /positive number/);
});

test('the estimator is deterministic and grows with length', () => {
  assert.equal(estimateTokens(''), 0);
  assert.equal(estimateTokens('abcd'), 1);
  assert.ok(estimateTokens('a'.repeat(400)) > estimateTokens('a'.repeat(40)));
});

test('a custom estimator drives the packing', () => {
  // The planner charges for the id as well as the text, because both are sent.
  const tokenEstimator = (text: string) => (text.length > 10 ? 1000 : 0);
  const plan = planBatches(records(4), { tokenEstimator, maxInputTokens: 4000, reservedForQuestions: 500, reservedForOutput: 500 });
  assert.equal(plan.perCallRecordAllowance, 3000);
  assert.deepEqual(plan.calls.map(c => c.recordIds.length), [3, 1]);
});
