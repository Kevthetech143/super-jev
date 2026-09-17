import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { run } from '../src/index.ts';
import { validateEvaluation } from '../src/jev.ts';
import type { Evaluation, Question, Request } from '../src/types.ts';

// Regression tests for two validator findings:
//  - score/distribution consistency was unchecked, so score 0 was accepted
//    alongside a distribution putting all mass on level 1;
//  - the 0.001 probability-total tolerance rejected a whole 32-answer batch
//    because two of its distributions totalled 0.99.
// Every rule asserted here is sourced in docs/provider-contract.md.

const scoreRequest = (levels: string[]): Request =>
  ({ state: {}, questions: { q: { type: 'score', instructions: 'Rate', criteria: levels } satisfies Question } });
const choiceRequest = (options: string[]): Request =>
  ({ state: {}, questions: { q: { type: 'choice', instructions: 'Pick', criteria: Object.fromEntries(options.map(o => [o, o])) } satisfies Question } });
const score = (value: number, probabilities: Record<string, number>, confidence = Math.max(...Object.values(probabilities))): Evaluation =>
  ({ model: 'probe', answers: { q: { type: 'score', score: value, confidence, probabilities } } });
const choice = (pick: string, probabilities: Record<string, number>, confidence = Math.max(...Object.values(probabilities))): Evaluation =>
  ({ model: 'probe', answers: { q: { type: 'choice', choice: pick, confidence, probabilities } } });

test('a score inconsistent with its own distribution is rejected', () => {
  // The recorded gap: score 0 with every level-1 unit of mass on level 1.
  assert.throws(() => validateEvaluation(scoreRequest(['low', 'high']), score(0, { 0: 0, 1: 1 }, 1)),
    /Score 0 inconsistent with its distribution/);
  // Argmax dressed up as a score is still wrong: the expectation here is 1.3.
  assert.throws(() => validateEvaluation(scoreRequest(['low', 'mid', 'high']), score(1, { 0: 0, 1: 0.7, 2: 0.3 })),
    /inconsistent with its distribution/);
  assert.throws(() => validateEvaluation(scoreRequest(['low', 'high']), score(1, { 0: 1, 1: 0 })),
    /inconsistent with its distribution/);
});

test('a score that lands between levels is accepted, as the docs require', () => {
  // TypeSafe's own worked example: 0 x 0.0 + 1 x 0.70 + 2 x 0.30 = 1.30.
  // A rule demanding that score sit on a level with mass would reject this.
  validateEvaluation(scoreRequest(['low', 'mid', 'high']), score(1.3, { 0: 0, 1: 0.7, 2: 0.3 }));
  validateEvaluation(scoreRequest(['low', 'mid', 'high']), score(1.06, { 0: 0.24, 1: 0.46, 2: 0.3 }));
  // Rounded to two decimals, within the documented-absence tolerance.
  validateEvaluation(scoreRequest(['a', 'b', 'c', 'd']), score(1.6, { 0: 0.1, 1: 0.4, 2: 0.3, 3: 0.2 }));
  // Within tolerance but not exact, which two-decimal rounding can produce.
  validateEvaluation(scoreRequest(['a', 'b', 'c', 'd']), score(1.58, { 0: 0.1, 1: 0.4, 2: 0.3, 3: 0.2 }));
  // A degenerate distribution still has to match its expectation.
  validateEvaluation(scoreRequest(['low', 'high']), score(1, { 0: 0, 1: 1 }, 1));
});

test('a score outside the level range is still rejected', () => {
  assert.throws(() => validateEvaluation(scoreRequest(['low', 'high']), score(-0.5, { 0: 1, 1: 0 })), /Invalid score/);
  assert.throws(() => validateEvaluation(scoreRequest(['low', 'high']), score(2.5, { 0: 0, 1: 1 }, 1)), /Invalid score/);
  assert.throws(() => validateEvaluation(scoreRequest(['low', 'high']), score(NaN, { 0: 0, 1: 1 }, 1)), /Invalid score/);
});

test('the recorded response that lost a whole batch now validates, with raw values kept', async () => {
  // Replay of results.json call probe/repeat-identical-shuffled, whose
  // record_10 and record_13 distributions total 0.99. The old validator threw
  // "Invalid distribution: record_10" and discarded all 32 answers.
  const fixture = JSON.parse(await readFile(new URL('./fixtures/probe-repeat-identical-shuffled.json', import.meta.url), 'utf8'));
  assert.equal(fixture.validationError, 'Invalid distribution: record_10');
  const request: Request = { state: {}, questions: fixture.questions };
  const response: Evaluation = fixture.response;
  const shortTotals = Object.entries(response.answers)
    .filter(([, a]: [string, any]) => Math.abs(Object.values(a.probabilities as Record<string, number>).reduce((x, y) => x + y, 0) - 1) > 1e-9)
    .map(([id]) => id);
  assert.deepEqual(shortTotals, ['record_10', 'record_13'], 'fixture should carry the two short totals');
  const before = structuredClone(response.answers);

  validateEvaluation(request, response);

  assert.equal(Object.keys(response.answers).length, 32, 'all 32 answers survive');
  for (const id of shortTotals) {
    const answer = response.answers[id] as any;
    assert.ok(answer.rawProbabilities, `${id} must retain the provider's values`);
    assert.deepEqual(answer.rawProbabilities, (before[id] as any).probabilities);
    const total = Object.values(answer.probabilities as Record<string, number>).reduce((x: number, y: number) => x + y, 0);
    assert.ok(Math.abs(total - 1) < 1e-9, `${id} normalized to ${total}`);
    // Normalization must not move the decision or invent an answer.
    assert.equal(answer.choice, (before[id] as any).choice);
    assert.equal(answer.confidence, (before[id] as any).confidence);
  }
  for (const [id, answer] of Object.entries(response.answers) as [string, any][]) {
    if (shortTotals.includes(id)) continue;
    assert.equal(answer.rawProbabilities, undefined, `${id} was already exact and must be untouched`);
    assert.deepEqual(answer.probabilities, (before[id] as any).probabilities);
  }
  // record_10 came back as support 0.55 of 0.99; the choice must stay support.
  assert.equal((response.answers.record_10 as any).choice, 'support');
  assert.equal((response.answers.record_10 as any).rawProbabilities.support, 0.55);
});

test('normalization is idempotent, so the loop can validate the same object twice', () => {
  const response = choice('a', { a: 0.55, b: 0.44 }, 0.55);
  const request = choiceRequest(['a', 'b']);
  validateEvaluation(request, response);
  const once = structuredClone(response);
  validateEvaluation(request, response);
  assert.deepEqual(response, once, 'a second pass must change nothing');
});

test('totals outside one rounding step are rejected, never normalized', () => {
  const request = choiceRequest(['a', 'b']);
  for (const [label, probabilities] of [
    ['total 0', { a: 0, b: 0 }],
    ['total 0.5', { a: 0.5, b: 0 }],
    ['total 0.97', { a: 0.5, b: 0.47 }],
    ['total 1.06', { a: 0.56, b: 0.5 }],
    ['total 2', { a: 1, b: 1 }]
  ] as [string, Record<string, number>][]) {
    assert.throws(() => validateEvaluation(request, choice('a', probabilities, 0.5)),
      /Invalid distribution total/, `${label} must be rejected`);
  }
});

test('grossly malformed distributions are rejected on shape', () => {
  const request = choiceRequest(['a', 'b']);
  assert.throws(() => validateEvaluation(request, choice('a', { a: 1.5, b: -0.5 }, 1)), /Invalid distribution/);
  assert.throws(() => validateEvaluation(request, choice('a', { a: NaN, b: 1 }, 1)), /Invalid distribution/);
  assert.throws(() => validateEvaluation(request, choice('a', { a: 1 }, 1)), /Invalid distribution/, 'missing level');
  assert.throws(() => validateEvaluation(request, choice('a', { a: 0.5, b: 0.5, c: 0 }, 0.5)), /Invalid distribution/, 'extra level');
  assert.throws(() => validateEvaluation(request, choice('a', { a: '1' as any, b: 0 }, 1)), /Invalid distribution/, 'string value');
  assert.throws(() => validateEvaluation(request, choice('a', { a: Infinity, b: 0 }, 1)), /Invalid distribution/);
  assert.throws(() => validateEvaluation(request, { model: 'probe', answers: { q: { type: 'choice', choice: 'a', confidence: 1 } as any } }), /Invalid distribution/);
});

test('a choice that is not the documented argmax is rejected', () => {
  assert.throws(() => validateEvaluation(choiceRequest(['a', 'b']), choice('b', { a: 0.9, b: 0.1 }, 0.9)), /Invalid choice/);
  assert.throws(() => validateEvaluation(choiceRequest(['a', 'b']), choice('c', { a: 0.9, b: 0.1 }, 0.9)), /Invalid choice/);
});

test('a confidence above its distribution peak is rejected', () => {
  // INFERRED rule; see docs/provider-contract.md section 4.
  assert.throws(() => validateEvaluation(choiceRequest(['a', 'b']), choice('a', { a: 0.55, b: 0.45 }, 1)),
    /Confidence exceeds its distribution peak/);
  // Equal to the peak, and below it, are both normal in the recorded data.
  validateEvaluation(choiceRequest(['a', 'b']), choice('a', { a: 0.55, b: 0.45 }, 0.55));
  validateEvaluation(choiceRequest(['a', 'b']), choice('a', { a: 0.55, b: 0.45 }, 0.11));
  assert.throws(() => validateEvaluation(choiceRequest(['a', 'b']), choice('a', { a: 0.55, b: 0.45 }, 1.5)), /Invalid distribution/);
});

test('noul answers are unaffected', () => {
  const request: Request = { state: {}, questions: { q: { type: 'noul', instructions: 'True?' } } };
  validateEvaluation(request, { model: 'probe', answers: { q: { type: 'noul', noul: 0 } } });
  validateEvaluation(request, { model: 'probe', answers: { q: { type: 'noul', noul: 1 } } });
  assert.throws(() => validateEvaluation(request, { model: 'probe', answers: { q: { type: 'noul', noul: 1.2 } } }), /Invalid probability/);
});

test('a validation failure ends the run without running or re-running a tool', async () => {
  // Inference retries stay separate from tool execution: there is no retry
  // path, and a rejected response cannot reach a tool at all.
  let executions = 0;
  let evaluations = 0;
  const result = await run({
    domain: {
      name: 'probe',
      observe: async () => ({}),
      questions: () => ({ q: { type: 'score', instructions: 'Rate', criteria: ['low', 'high'] } }),
      decide: () => ({ kind: 'act', action: { tool: 't', args: {} } }),
      permit: () => true,
      tools: { t: { validate: () => true, execute: async () => { executions++; return {}; } } },
      reduce: s => s,
      verify: async () => false
    },
    initial: {},
    evaluator: { evaluate: async () => { evaluations++; return score(0, { 0: 0, 1: 1 }, 1); } },
    journal: { append: async () => {} }
  });
  assert.equal(result.status, 'error');
  assert.equal(executions, 0, 'a rejected response must never reach a tool');
  assert.equal(evaluations, 1, 'and must not be retried');
});
