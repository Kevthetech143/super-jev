import test from 'node:test';
import assert from 'node:assert/strict';
import { CostMeter, formatCost } from '../../src/enhance/cost.ts';

test('provider-reported usage is summed and marked as reported', () => {
  const meter = new CostMeter();
  meter.call({ model: 'm', answers: {}, usage: { input_tokens: 100, output_tokens: 20 } }, 12, { input: 1, output: 1 });
  meter.call({ model: 'm', answers: {}, usage: { input_tokens: 50, output_tokens: 10 } }, 8, { input: 1, output: 1 });
  const cost = meter.snapshot();
  assert.equal(cost.calls, 2);
  assert.equal(cost.inputTokens, 150);
  assert.equal(cost.outputTokens, 30);
  assert.equal(cost.estimated, false);
  assert.deepEqual(cost.perCallLatency, [12, 8]);
  assert.match(formatCost(cost), /provider-reported/);
});

test('a call with no usage block falls back to the estimate and taints the total', () => {
  const meter = new CostMeter();
  meter.call({ model: 'm', answers: {}, usage: { input_tokens: 100, output_tokens: 20 } }, 5, { input: 1, output: 1 });
  meter.call({ model: 'm', answers: {} }, 5, { input: 400, output: 60 });
  const cost = meter.snapshot();
  assert.equal(cost.inputTokens, 500);
  assert.equal(cost.outputTokens, 80);
  assert.equal(cost.estimated, true);
  assert.match(formatCost(cost), /ESTIMATED, not a billing figure/);
});

test('a failed call still counts as a call, and retries are counted separately', () => {
  const meter = new CostMeter();
  meter.call(undefined, 3, { input: 10, output: 2 });
  meter.retry();
  meter.call({ model: 'm', answers: {}, usage: { input_tokens: 10, output_tokens: 2 } }, 4, { input: 10, output: 2 });
  const cost = meter.snapshot();
  assert.equal(cost.calls, 2, 'a failed attempt is a real, billable call');
  assert.equal(cost.retries, 1);
  assert.equal(cost.perCallLatency.length, 2);
});

test('wall time is measured and the snapshot does not alias internal state', () => {
  const meter = new CostMeter();
  meter.call({ model: 'm', answers: {}, usage: { input_tokens: 1, output_tokens: 1 } }, 1, { input: 1, output: 1 });
  const first = meter.snapshot();
  first.perCallLatency.push(999);
  meter.call({ model: 'm', answers: {}, usage: { input_tokens: 1, output_tokens: 1 } }, 1, { input: 1, output: 1 });
  const second = meter.snapshot();
  assert.deepEqual(second.perCallLatency, [1, 1]);
  assert.ok(second.wallMs >= 0);
});
