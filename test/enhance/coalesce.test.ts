import test from 'node:test';
import assert from 'node:assert/strict';
import { BatchingEvaluator, estimateTokens } from '../../src/enhance/coalesce.ts';
import { navigate } from '../../src/enhance/navigation.ts';
import type { Evaluator, Request } from '../../src/types.ts';
import { Overloaded, TooBig } from '../../src/judge-errors.ts';

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
      if (Object.keys(request.questions).length > fail400Over) throw new TooBig('Jev HTTP 400');
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
  const flaky = { async evaluate(r: Request, s: AbortSignal) { if (failures-- > 0) throw new Overloaded('Jev HTTP 529'); return inner.evaluate(r, s); } };
  const out = await navigate(flat(['alpha', 'beta']), 'alpha', { transport: new BatchingEvaluator(flaky), timeoutMs: 10_000 });
  assert.equal(out.candidates[0]?.sourceId, 'alpha');
});

test('a shared call every caller has given up on is stopped, not waited on', async () => {
  // Each navigate times out on its own; the provider call they shared must then be
  // aborted, or navigation-cli sits waiting for an answer nobody will read.
  let innerSignal: AbortSignal | undefined;
  const hanging = { evaluate(_r: Request, s: AbortSignal) { innerSignal = s; return new Promise<never>((_, reject) => s.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })), { once: true })); } };
  const batched = new BatchingEvaluator(hanging);
  const out = await Promise.allSettled([flat(['alpha', 'beta']), flat(['alpha', 'gamma'])].map(c => navigate(c, 'alpha', { transport: batched, timeoutMs: 30 })));
  assert.equal(batched.calls, 1);
  assert.ok(out.every(r => r.status === 'rejected' && /timed out/.test(String(r.reason?.message))));
  assert.equal(innerSignal?.aborted, true);
});

test('a shared call is not stopped while one caller still waits for it', async () => {
  let innerSignal: AbortSignal | undefined;
  let answer: (() => void) | undefined;
  const inner = fake();
  const slow = { evaluate(r: Request, s: AbortSignal) { innerSignal = s; return new Promise(resolve => { answer = () => resolve(inner.evaluate(r, s)); }); } } as Evaluator;
  const batched = new BatchingEvaluator(slow);
  const quick = navigate(flat(['alpha', 'beta']), 'alpha', { transport: batched, timeoutMs: 20 });
  const patient = navigate(flat(['alpha', 'gamma']), 'alpha', { transport: batched, timeoutMs: 10_000 });
  await assert.rejects(quick, /timed out/);
  assert.equal(batched.calls, 1);
  assert.equal(innerSignal?.aborted, false);
  answer!();
  assert.equal((await patient).candidates[0]?.sourceId, 'alpha');
});

test('a 429/529 backoff ends when every caller has given up, with no further call', async () => {
  let sent = 0;
  const busy = { async evaluate() { sent++; throw new Overloaded('Jev HTTP 529'); } };
  const batched = new BatchingEvaluator(busy);
  const out = await Promise.allSettled([flat(['alpha', 'beta']), flat(['alpha', 'gamma'])].map(c => navigate(c, 'alpha', { transport: batched, timeoutMs: 50 })));
  assert.ok(out.every(r => r.status === 'rejected'));
  // Past the first 1s backoff: a retry that ignored the abort would have been sent by now.
  await new Promise(resolve => setTimeout(resolve, 1_300));
  assert.equal(sent, 1);
});

/** Answers a content check from the passage each question names: o_0 when its text holds the question word. */
function reader(): Evaluator & { sent: Request[] } {
  const sent: Request[] = [];
  return {
    sent,
    async evaluate(request: Request) {
      sent.push(request);
      const state = request.state as { question: string; passages: Record<string, string> };
      const answers = Object.fromEntries(Object.entries(request.questions).map(([key, q]) => {
        const ref = /Classify passages\.([A-Za-z0-9_]+)\./.exec(q.instructions)![1]!;
        const pick = state.passages[ref]!.includes(state.question) ? 'o_0' : 'o_none';
        return [key, { type: 'choice', choice: pick, confidence: 0.9, probabilities: { o_0: pick === 'o_0' ? 0.9 : 0.1, o_none: pick === 'o_0' ? 0.1 : 0.9 } }];
      }));
      return { model: 'fake', answers } as never;
    }
  };
}

function file(name: string, texts: string[]) {
  return {
    version: 1 as const, structure: 'flat-files' as const, rootId: 'root' as const,
    nodes: [{ id: 'root', label: name, description: name, children: texts.map((_, i) => `${name}_c${i}`) },
      ...texts.map((text, i) => ({ id: `${name}_c${i}`, label: `${name} part ${i + 1}`, description: text, sourceId: `${name}:${i}` }))]
  };
}

test('content checks of different files share one call, and each passage answers back to its own file', async () => {
  const files = [file('one', ['nothing here', 'the gate code is alpha']), file('two', ['alpha is the gate code']), file('three', ['beta only', 'gamma only'])];
  const alone = await Promise.all(files.map(f => navigate(f, 'alpha', { transport: reader(), mode: 'source-evidence' })));
  const inner = reader();
  const batched = new BatchingEvaluator(inner);
  const together = await Promise.all(files.map(f => navigate(f, 'alpha', { transport: batched, mode: 'source-evidence' })));
  const held = (r: { candidates: { sourceId: string; score: number }[] }) => r.candidates.filter(c => c.score > 0.5).map(c => c.sourceId);
  assert.deepEqual(together.map(held), [['one:1'], ['two:0'], []]);
  assert.deepEqual(together.map(r => r.candidates), alone.map(r => r.candidates));
  assert.equal(inner.sent.length, 1);
  const sent = inner.sent[0]!;
  const state = sent.state as { question: string; passages: Record<string, string> };
  assert.deepEqual(Object.keys(state.passages).sort(), ['b0_branch_0', 'b0_branch_1', 'b1_branch_0', 'b2_branch_0', 'b2_branch_1']);
  assert.equal(state.question, 'alpha');
  for (const [key, q] of Object.entries(sent.questions)) assert.ok(q.instructions.endsWith(`Classify passages.${key}.`), key);
});

test('a lone content check is sent unchanged', async () => {
  const inner = reader();
  await navigate(file('one', ['alpha']), 'alpha', { transport: new BatchingEvaluator(inner), mode: 'source-evidence' });
  assert.deepEqual(Object.keys((inner.sent[0]!.state as { passages: object }).passages), ['branch_0']);
  assert.match(inner.sent[0]!.questions.b0_branch_0!.instructions, /Classify passages\.branch_0\.$/);
});

test('packed passages are split under the token budget, and every file still answers', async () => {
  const text = (w: string) => `${w} ${'filler words '.repeat(40)}`;
  const files = Array.from({ length: 5 }, (_, i) => file(`f${i}`, [text(i % 2 ? 'alpha' : 'beta')]));
  const solo = reader();
  await navigate(files[0]!, 'alpha', { transport: solo, mode: 'source-evidence' });
  const one = solo.sent[0]!;
  // Room for two files' questions and passages in one call, not three.
  const budget = estimateTokens(one.state) + 2 * (estimateTokens(one.questions.branch_0) + 20) + estimateTokens(one.state) + 10;
  const inner = reader();
  const batched = new BatchingEvaluator(inner, budget);
  const out = await Promise.all(files.map(f => navigate(f, 'alpha', { transport: batched, mode: 'source-evidence' })));
  assert.deepEqual(out.map(r => r.candidates.filter(c => c.score > 0.5).length), [0, 1, 0, 1, 0]);
  assert.ok(inner.sent.length >= 2 && inner.sent.length < files.length, `sent ${inner.sent.length} calls`);
  for (const r of inner.sent) {
    const used = estimateTokens((({ passages: _p, ...rest }) => rest)(r.state as { passages: object })) + estimateTokens((r.state as { passages: object }).passages)
      + Object.values(r.questions).reduce((sum, q) => sum + estimateTokens(q) + 20, 0);
    assert.ok(Object.keys(r.questions).length === 1 || used <= budget + 10, `call of ${used} tokens over ${budget}`);
  }
});

test('content checks for different questions are not packed together', async () => {
  const inner = reader();
  const batched = new BatchingEvaluator(inner);
  await Promise.all([navigate(file('one', ['alpha']), 'alpha', { transport: batched, mode: 'source-evidence' }),
    navigate(file('two', ['beta']), 'beta', { transport: batched, mode: 'source-evidence' })]);
  assert.equal(inner.sent.length, 2);
});

test('a big request does not close a half-full batch: a later small one still joins it', async () => {
  const words = (n: number) => `alpha ${'filler '.repeat(n)}`;
  const files = [file('mid', [words(300)]), file('big', [words(600)]), file('small', [words(10)])];
  const solo = reader();
  await navigate(files[0]!, 'alpha', { transport: solo, mode: 'source-evidence' });
  const q = estimateTokens(solo.sent[0]!.questions.branch_0) + 20;
  const shared = estimateTokens({ question: 'alpha', purpose: (solo.sent[0]!.state as { purpose: string }).purpose });
  const size = (f: ReturnType<typeof file>) => q + estimateTokens({ branch_0: f.nodes[1]!.description });
  // mid + small fit; mid + big and big + small do not.
  const budget = shared + size(files[0]!) + size(files[2]!) + 5;
  assert.ok(shared + size(files[1]!) + size(files[2]!) > budget);
  const inner = reader();
  const batched = new BatchingEvaluator(inner, budget);
  await Promise.all(files.map(f => navigate(f, 'alpha', { transport: batched, mode: 'source-evidence' })));
  assert.equal(inner.sent.length, 2);
});
