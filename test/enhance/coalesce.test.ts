import test from 'node:test';
import assert from 'node:assert/strict';
import { BatchingEvaluator, estimateTokens } from '../../src/enhance/coalesce.ts';
import { navigate } from '../../src/enhance/navigation.ts';
import type { Evaluator, Request } from '../../src/types.ts';

function flat(words: string[]) {
  return {
    version: 1 as const, structure: 'flat-files' as const, rootId: 'root' as const,
    nodes: [{ id: 'root', label: 'root', description: 'root', children: words },
      ...words.map(w => ({ id: w, label: w, description: `${w} notes`, sourceId: w }))]
  };
}

/** Picks the option whose label matches the question, every time, and records each call. */
function fake(fail400Over = Infinity): Evaluator & { sent: Request[] } {
  const sent: Request[] = [];
  return {
    sent,
    async evaluate(request: Request) {
      sent.push(request);
      if (Object.keys(request.questions).length > fail400Over) throw new Error('Jev HTTP 400');
      const word = (request.state as { question: string }).question;
      const answers = Object.fromEntries(Object.entries(request.questions).map(([key, q]) => {
        const criteria = (q as { criteria: Record<string, string> }).criteria;
        const keys = Object.keys(criteria);
        const pick = keys.find(k => criteria[k]!.startsWith(`${word}:`)) ?? 'o_none';
        return [key, { type: 'choice', choice: pick, confidence: 0.9, probabilities: Object.fromEntries(keys.map(k => [k, k === pick ? 0.9 : 0.1 / (keys.length - 1)])) }];
      }));
      return { model: 'fake', answers } as never;
    }
  };
}

test('navigations started together share one call and get the same answers as alone', async () => {
  const catalogs = [flat(['alpha', 'beta']), flat(['gamma', 'alpha']), flat(['delta'])];
  const alone = await Promise.all(catalogs.map(c => navigate(c, 'alpha', { transport: fake() })));
  const inner = fake();
  const batched = new BatchingEvaluator(inner);
  const together = await Promise.all(catalogs.map(c => navigate(c, 'alpha', { transport: batched })));
  assert.equal(inner.sent.length, 1);
  assert.equal(Object.keys(inner.sent[0]!.questions).length, 3);
  assert.deepEqual(together.map(r => r.candidates), alone.map(r => r.candidates));
});

test('a batch over the token budget is split, and each part still answers', async () => {
  const catalogs = Array.from({ length: 6 }, (_, i) => flat([`alpha`, `f${i}`]));
  const perQuestion = estimateTokens({ criteria: flat(['alpha', 'f0']).nodes }) ;
  const inner = fake();
  const batched = new BatchingEvaluator(inner, perQuestion * 2 + 40);
  const out = await Promise.all(catalogs.map(c => navigate(c, 'alpha', { transport: batched })));
  assert.ok(inner.sent.length >= 3, `sent ${inner.sent.length} calls`);
  assert.ok(out.every(r => r.candidates[0]?.sourceId === 'alpha'));
});

test('an HTTP 400 on a multi-question batch splits it and retries the halves', async () => {
  const inner = fake(2);
  const batched = new BatchingEvaluator(inner);
  const out = await Promise.all([0, 1, 2, 3].map(i => navigate(flat(['alpha', `f${i}`]), 'alpha', { transport: batched })));
  assert.ok(out.every(r => r.status === 'candidates'));
  assert.equal(inner.sent.length, 3); // 4 refused, then 2 + 2
});

test('a 529 or 429 is retried with backoff, then answers', async () => {
  const inner = fake();
  let failures = 1;
  const flaky = { async evaluate(r: Request, s: AbortSignal) { if (failures-- > 0) throw new Error('Jev HTTP 529'); return inner.evaluate(r, s); } };
  const out = await navigate(flat(['alpha', 'beta']), 'alpha', { transport: new BatchingEvaluator(flaky), timeoutMs: 10_000 });
  assert.equal(out.candidates[0]?.sourceId, 'alpha');
});
