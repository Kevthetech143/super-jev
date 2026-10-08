// Frozen tests: a 503 is a "try again later" status, retried like 429 and 529. Made-up data, fake fetch.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Jev } from '../src/jev.ts';
import { retryOverloaded } from '../src/judge.ts';
import { JUDGE_PROFILE, loadJudgeProfile } from '../src/judge-profile.ts';
import type { Request } from '../src/types.ts';

const request: Request = { state: 'EVIDENCE:\nmade-up file', questions: { c1: { type: 'choice', instructions: 'Is it there?', criteria: { A: 'yes', B: 'no' } } } };
const good = { model: 'm', answers: { c1: { type: 'choice', choice: 'A', confidence: 0.95, probabilities: { A: 0.95, B: 0.05 } } } };
const fast = { ...JUDGE_PROFILE, retryFirstDelayMs: 1 };
const signal = () => new AbortController().signal;
const script = (statuses: number[]) => {
  const state = { calls: 0 };
  const fake = (async () => {
    const status = statuses[Math.min(state.calls++, statuses.length - 1)]!;
    return status === 200 ? new Response(JSON.stringify(good)) : new Response('', { status });
  }) as unknown as typeof fetch;
  return { state, jev: new Jev({ apiKey: 'k', fetch: fake, profile: fast }) };
};

test('503 then 200: the answer comes back after exactly 2 calls', async () => {
  const { state, jev } = script([503, 200]);
  const out = await retryOverloaded(() => jev.evaluate(request, signal()), signal(), fast);
  assert.equal((out.answers.c1 as { choice: string }).choice, 'A');
  assert.equal(state.calls, 2);
});

test('503 four times: gives up after exactly the bounded attempts', async () => {
  const { state, jev } = script([503]);
  await assert.rejects(retryOverloaded(() => jev.evaluate(request, signal()), signal(), fast), /Jev HTTP 503/);
  assert.equal(state.calls, fast.retryAttempts);
  assert.equal(fast.retryAttempts, 4);
});

test('the profiles list 503 with 429 and 529', () => {
  for (const name of ['typesafe-jev', 'laya']) assert.deepEqual(loadJudgeProfile(name).overloadedStatuses, [429, 503, 529], name);
});
