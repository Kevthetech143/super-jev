import test from 'node:test';
import assert from 'node:assert/strict';
import { formatPlan, planBatches, PER_RECORD_ENVELOPE_TOKENS } from '../../src/enhance/batch.ts';
import { budget, estimateTokens, recordAllowance, DEFAULT_BUDGET } from '../../src/enhance/budget.ts';
import { instructionFor, planEnhancedRun } from '../../src/enhance/classify.ts';
import { assignIds, buildKeyedRequest, toRefs } from '../../src/enhance/reference.ts';
import { categories, documents } from './fixtures/cases.ts';

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

// --- an honest estimate: what the plan promises is what the request costs ----
//
// The flat `reservedForQuestions` reservation does not grow with the batch, but
// the question block does: every record carries its own instruction line and
// its own full copy of the option descriptions. A plan that reserved a flat
// number under-counted a 32-record call by roughly a factor of four, which is
// how a "this fits in 8000 tokens" plan turned into a request four times that
// size. These tests hold the estimate to the real request.

const pilotRecords = documents.map(d => ({ id: d.id, text: d.text }));
const keyedPass = [{ name: 'keyed', framing: 'keyed' as const, order: 'input' as const }];

/** Build the request the runner would send for one planned call, using the runner's own question builder. */
function requestFor(recordIds: string[], options: Record<string, string>, abstainOption?: string) {
  const refs = toRefs(assignIds(pilotRecords));
  const byId = new Map(refs.map(r => [r.id, r]));
  return buildKeyedRequest(recordIds.map(id => byId.get(id)!), instructionFor(options, abstainOption).keyed);
}

test('the plan estimate matches the request it predicts, within 5%', () => {
  const { plans } = planEnhancedRun({ records: pilotRecords, options: categories, abstainOption: 'other', passes: keyedPass });
  const plan = plans[0].plan;
  assert.equal(plan.questionsCounted, true, 'the classifier asks the planner to count real question text');
  assert.ok(plan.calls.length > 1);
  for (const call of plan.calls) {
    const real = estimateTokens(JSON.stringify(requestFor(call.recordIds, categories, 'other')));
    const drift = Math.abs(call.estimatedInputTokens - real) / real;
    assert.ok(drift <= 0.05, `call ${call.index}: planned ${call.estimatedInputTokens} vs request ${real} is ${(drift * 100).toFixed(1)}% off`);
  }
  const realTotal = plan.calls.reduce((n, c) => n + estimateTokens(JSON.stringify(requestFor(c.recordIds, categories, 'other'))), 0);
  assert.ok(Math.abs(plan.totalEstimatedInputTokens - realTotal) / realTotal <= 0.05);
});

test('at 40 records per call in an 8000-token window the plan splits instead of promising one call it cannot send', () => {
  const budgetOverride = { maxRecordsPerCall: 40, maxInputTokens: 8000 };
  const { plans } = planEnhancedRun({ records: pilotRecords, options: categories, abstainOption: 'other', budget: budgetOverride, passes: keyedPass });
  const plan = plans[0].plan;

  // The record cap would allow all 32 in one call. Token arithmetic must not.
  assert.ok(plan.calls.length > 1, 'the 32 records do not fit one 8000-token call once questions are counted');
  assert.deepEqual(plan.calls.flatMap(c => c.recordIds).length, pilotRecords.length, 'no record is dropped by the split');
  for (const call of plan.calls) {
    assert.ok(call.estimatedInputTokens <= plan.budget.maxInputTokens, `call ${call.index} estimate ${call.estimatedInputTokens} must fit maxInputTokens`);
    const real = estimateTokens(JSON.stringify(requestFor(call.recordIds, categories, 'other')));
    assert.ok(real <= plan.budget.maxInputTokens, `the request actually built for call ${call.index} is ${real} tokens and must fit too`);
  }

  // What the old flat reservation would have claimed for the same 32 records in
  // one call, and what that call really costs. The gap is the bug.
  const flatClaim = pilotRecords.reduce((n, r) => n + estimateTokens(r.text) + estimateTokens(r.id), 0) + DEFAULT_BUDGET.reservedForQuestions;
  const truth = estimateTokens(JSON.stringify(requestFor(pilotRecords.map(r => r.id), categories, 'other')));
  assert.ok(flatClaim < 4000 && truth > 8000, `the flat reservation claimed ${flatClaim} tokens for a request that costs ${truth}`);
  assert.ok(truth / flatClaim > 3, 'the flat reservation under-counted by more than a factor of three');
});

test('a record whose own question will not fit is refused, not optimistically packed', () => {
  // A single option description big enough that one record plus its question
  // exceeds the window. The record itself is tiny; only the question is not.
  const fat = { keep: 'k'.repeat(40000), drop: 'd' };
  const { plans } = planEnhancedRun({ records: pilotRecords.slice(0, 3), options: fat, passes: keyedPass });
  assert.deepEqual(plans[0].plan.oversizedRecordIds, pilotRecords.slice(0, 3).map(r => r.id));
  assert.deepEqual(plans[0].plan.calls, []);
});

test('without a question cost the planner keeps the old flat reservation and says so in the plan', () => {
  const plan = planBatches(records(4));
  assert.equal(plan.questionsCounted, false);
  assert.equal(plan.perCallRecordAllowance, recordAllowance(budget()), 'the flat reservation is subtracted from the allowance, exactly as before');
  assert.match(formatPlan(plan), /questions reserved flat, NOT counted/);
});

test('a counted plan spends the whole input window, not the window minus a reservation it is also counting', () => {
  const plan = planBatches(records(4), {}, () => 10);
  assert.equal(plan.questionsCounted, true);
  assert.equal(plan.perCallRecordAllowance, DEFAULT_BUDGET.maxInputTokens - DEFAULT_BUDGET.reservedForOutput);
  assert.match(formatPlan(plan), /question text counted per record/);
});

test('the question cost is charged once per record, because that is how often it is sent', () => {
  const one = planBatches(records(1), { maxRecordsPerCall: 8 }, () => 100);
  const four = planBatches(records(4), { maxRecordsPerCall: 8 }, () => 100);
  assert.equal(four.calls[0].estimatedInputTokens - one.calls[0].estimatedInputTokens, 3 * (100 + PER_RECORD_ENVELOPE_TOKENS + estimateTokens('x'.repeat(40)) + estimateTokens('r0')));
});
