import test from 'node:test';
import assert from 'node:assert/strict';
import { Jev } from '../src/jev.ts';
import { hasSecret, payloadHasSecret } from '../src/secret-scan.ts';

const request = (text: string) => ({
  state: { question: text },
  questions: { q: { type: 'choice', instructions: 'pick', options: ['a', 'b'] } }
}) as any;

test('Jev refuses to fetch a body with a secret anywhere in it (0 sends)', async () => {
  let sends = 0;
  const jev = new Jev({ apiKey: 'fake', fetch: (async () => { sends++; return new Response('{}', { status: 500 }); }) as any });
  for (const text of ['api_key = sk-live-9fQ2xZ7pL0aBcD3eF4', 'card 4111 1111 1111 1111', 'ghp_' + 'a1'.repeat(18)]) {
    await assert.rejects(jev.evaluate(request(text), new AbortController().signal), /contains a secret; not sent/);
  }
  assert.equal(sends, 0);
  await assert.rejects(jev.evaluate(request('what colour is the box?'), new AbortController().signal), /Jev HTTP 500/);
  assert.ok(sends > 0);
});

test('Node scan matches the Python has_secret cases from the shared pattern file', () => {
  assert.equal(hasSecret('1Password vault'), false);
  assert.equal(hasSecret('dated 2024-01-02 2024-01-03 2024-01-04'), false);
  assert.equal(hasSecret('see https://x.test/?id=1234123412341234'), false);
  assert.equal(hasSecret('token: Zx9Qp2Lm8Rt4Vb6Nc1Hs3Kd7'), true);
  assert.equal(payloadHasSecret({ a: [{ b: 'passwd here' }] }), true);
  assert.equal(payloadHasSecret({ a: ['fine'] }), false);
});
