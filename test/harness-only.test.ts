// Frozen test: SUPERJEV_HARNESS_ONLY=1 (set by ask.py from a principal's settings.json) means no judge call is sent. Fake fetch.
import test from 'node:test';
import assert from 'node:assert/strict';
import { Jev } from '../src/jev.ts';
import type { Request } from '../src/types.ts';

const request: Request = { state: 'EVIDENCE:\nmade-up file', questions: { c1: { type: 'choice', instructions: 'Is it there?', criteria: { A: 'yes', B: 'no' } } } };

test('harness-only: the judge is Unreachable and fetch is never called; off: it is called', async () => {
  let calls = 0;
  const fake = (async () => { calls++; return new Response('{}', { status: 500 }); }) as unknown as typeof fetch;
  const jev = new Jev({ apiKey: 'k', fetch: fake });
  const ac = () => new AbortController().signal;
  process.env.SUPERJEV_HARNESS_ONLY = '1';
  try {
    await assert.rejects(jev.evaluate(request, ac()), /harness-only/);
    assert.equal(calls, 0);
  } finally { delete process.env.SUPERJEV_HARNESS_ONLY; }
  await assert.rejects(jev.evaluate(request, ac()));
  assert.ok(calls >= 1);
});
