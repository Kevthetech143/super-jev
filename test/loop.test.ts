import test from 'node:test';
import assert from 'node:assert/strict';
import { run, Jev } from '../src/index.ts';
import { recovery, review, DemoEvaluator } from '../examples/domains.ts';
import type { Event } from '../src/types.ts';
function setup() {
  const events: Event[] = [];
  return { domain: recovery(), initial: { restarted: false, healthy: false }, evaluator: new DemoEvaluator(), journal: { append: async (e: Event) => { events.push(e); } }, events };
}
test('feedback advances state and verifies goal after two distinct actions', async () => {
  const s = setup(); const r = await run(s);
  assert.equal(r.status, 'success'); assert.equal(r.steps, 2);
  assert.equal(s.events.filter(e => e.type === 'action_completed').length, 2);
});
test('second domain uses the same core and batches questions', async () => {
  const s = setup(); const r = await run({ ...s, domain: review(), initial: { document: 'Owner: Alex', reviewed: false } });
  assert.equal(r.status, 'success');
  assert.equal(Object.keys((s.events.find(e => e.type === 'request')!.data as any).questions).length, 2);
});
test('policy denial prevents tool execution', async () => {
  const s = setup(); s.domain.permit = () => false;
  assert.equal((await run(s)).status, 'blocked');
  assert.ok(!s.events.some(e => e.type === 'action_started'));
});
test('malformed model output never reaches a tool', async () => {
  const s = setup(); s.evaluator.evaluate = async () => ({ model: 'broken', answers: {} });
  assert.equal((await run(s)).status, 'error');
  assert.ok(!s.events.some(e => e.type === 'action_started'));
});
test('unknown tools are blocked even if domain proposes them', async () => {
  const s = setup(); s.domain.decide = () => ({ kind: 'act', action: { tool: 'shell', args: {} } });
  assert.equal((await run(s)).status, 'blocked');
});
test('invalid arguments prevent execution', async () => {
  const s = setup(); s.domain.decide = () => ({ kind: 'act', action: { tool: 'restart', args: { unexpected: true } } });
  assert.equal((await run(s)).status, 'blocked');
});
test('context and step budgets are enforced', async () => {
  assert.equal((await run({ ...setup(), maxRequestBytes: 1 })).status, 'error');
  const r = await run({ ...setup(), maxSteps: 1 });
  assert.equal(r.reason, 'Step limit'); assert.equal(r.steps, 1);
});
test('repeated actions are stopped', async () => {
  const s = setup(); s.domain.verify = async () => false;
  s.domain.decide = () => ({ kind: 'act', action: { tool: 'check', args: {} } });
  assert.equal((await run({ ...s, maxRepeats: 1 })).reason, 'Repeated action limit');
});
test('cancelled runs perform no actions', async () => {
  const s = setup(); const controller = new AbortController(); controller.abort();
  assert.equal((await run({ ...s, signal: controller.signal })).status, 'error');
  assert.ok(!s.events.some(e => e.type === 'action_started'));
});
test('hanging observation is bounded', async () => {
  const s = setup(); s.domain.observe = async () => new Promise(() => {});
  const keepAlive = setInterval(() => {}, 100);
  try { assert.equal((await run({ ...s, timeoutMs: 20 })).status, 'error'); }
  finally { clearInterval(keepAlive); }
});
test('tool failures are not retried and secret errors are not logged', async () => {
  const s = setup(); let calls = 0;
  s.domain.tools.restart.execute = async () => { calls++; throw new Error('SECRET'); };
  assert.equal((await run(s)).status, 'error'); assert.equal(calls, 1);
  assert.ok(!JSON.stringify(s.events).includes('SECRET'));
});
test('Jev adapter sends documented payload and validates response', async () => {
  const fake: typeof fetch = async (url, init) => {
    assert.equal(url, 'https://api.typesafe.ai/v1/systemone');
    assert.equal(JSON.parse(String(init?.body)).model, 'jev-latest');
    assert.equal((init?.headers as any).Authorization, 'Bearer fixture');
    return new Response(JSON.stringify({ model: 'jev-latest', answers: { ok: { type: 'noul', noul: 0.8 } } }));
  };
  const result = await new Jev({ apiKey: 'fixture', fetch: fake }).evaluate({ state: 'hello', questions: { ok: { type: 'noul', instructions: 'Is this a greeting?' } } }, new AbortController().signal);
  assert.equal(result.answers.ok.type, 'noul');
});
test('HTTP failure contains no provider body or credentials', async () => {
  const client = new Jev({ apiKey: 'fixture', fetch: async () => new Response('SECRET', { status: 429 }) });
  await assert.rejects(client.evaluate({ state: '', questions: {} }, new AbortController().signal), /^Error: Jev HTTP 429$/);
});
