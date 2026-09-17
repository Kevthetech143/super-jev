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
  const received: Evaluation = fixture.response;
  const shortTotals = Object.entries(received.answers)
    .filter(([, a]: [string, any]) => Math.abs(Object.values(a.probabilities as Record<string, number>).reduce((x, y) => x + y, 0) - 1) > 1e-9)
    .map(([id]) => id);
  assert.deepEqual(shortTotals, ['record_10', 'record_13'], 'fixture should carry the two short totals');
  const before = structuredClone(received);

  const response = validateEvaluation(request, received);

  assert.deepEqual(received, before, "the provider's own response must come back untouched");
  assert.equal(Object.keys(response.answers).length, 32, 'all 32 answers survive');
  for (const id of shortTotals) {
    const answer = response.answers[id] as any;
    assert.ok(answer.rawProbabilities, `${id} must retain the provider's values`);
    assert.deepEqual(answer.rawProbabilities, (before.answers[id] as any).probabilities);
    const total = Object.values(answer.probabilities as Record<string, number>).reduce((x: number, y: number) => x + y, 0);
    assert.ok(Math.abs(total - 1) < 1e-9, `${id} normalized to ${total}`);
    // Normalization must not move the decision or invent an answer.
    assert.equal(answer.choice, (before.answers[id] as any).choice);
    assert.equal(answer.confidence, (before.answers[id] as any).confidence);
  }
  for (const [id, answer] of Object.entries(response.answers) as [string, any][]) {
    if (shortTotals.includes(id)) continue;
    assert.equal(answer.rawProbabilities, undefined, `${id} was already exact and must be untouched`);
    assert.deepEqual(answer.probabilities, (before.answers[id] as any).probabilities);
  }
  // record_10 came back as support 0.55 of 0.99; the choice must stay support.
  assert.equal((response.answers.record_10 as any).choice, 'support');
  assert.equal((response.answers.record_10 as any).rawProbabilities.support, 0.55);
});

test('validation is a fixed point, so a validated evaluation revalidates unchanged', () => {
  const request = choiceRequest(['a', 'b']);
  const once = validateEvaluation(request, choice('a', { a: 0.55, b: 0.44 }, 0.55));
  const twice = validateEvaluation(request, structuredClone(once));
  assert.deepEqual(twice, once, 'a second pass must change nothing');
  assert.deepEqual((once.answers.q as any).rawProbabilities, { a: 0.55, b: 0.44 });
  assert.deepEqual((twice.answers.q as any).rawProbabilities, { a: 0.55, b: 0.44 });
});

test('validation never writes to the caller, so a deeply frozen response passes', () => {
  const request = choiceRequest(['a', 'b']);
  // The renormalizing path is the only one that ever wanted to write.
  const received = choice('a', { a: 0.55, b: 0.44 }, 0.55);
  Object.freeze(received);
  Object.freeze(received.answers);
  Object.freeze(received.answers.q);
  Object.freeze((received.answers.q as any).probabilities);

  const response = validateEvaluation(request, received);

  assert.deepEqual((received.answers.q as any).probabilities, { a: 0.55, b: 0.44 }, 'input untouched');
  assert.equal((received.answers.q as any).rawProbabilities, undefined, 'no field added to the input');
  assert.notEqual(response.answers.q, received.answers.q, 'the validated answer is a new object');
  const total = Object.values((response.answers.q as any).probabilities as Record<string, number>).reduce((x, y) => x + y, 0);
  assert.ok(Math.abs(total - 1) < 1e-9, `renormalized to ${total}`);
});

test('a provider-supplied rawProbabilities is never trusted', () => {
  const request = choiceRequest(['a', 'b']);
  // rawProbabilities is this harness's audit field. A value arriving under
  // that name alongside an already-exact total cannot have come from
  // renormalizing these probabilities, so the answer is rejected.
  const forged = choice('a', { a: 0.6, b: 0.4 }, 0.6);
  (forged.answers.q as any).rawProbabilities = { a: 0.99, b: 0.01 };
  assert.throws(() => validateEvaluation(request, forged), /Untrusted rawProbabilities: q/);
  // A shape that is not even a distribution over these levels is rejected too.
  const bogus = choice('a', { a: 0.6, b: 0.4 }, 0.6);
  (bogus.answers.q as any).rawProbabilities = { a: 'lots' };
  assert.throws(() => validateEvaluation(request, bogus), /Untrusted rawProbabilities: q/);
  // On the renormalizing path it is overwritten with what the provider sent,
  // never carried through.
  const shifted = choice('a', { a: 0.55, b: 0.44 }, 0.55);
  (shifted.answers.q as any).rawProbabilities = { a: 0.01, b: 0.99 };
  const response = validateEvaluation(request, shifted);
  assert.deepEqual((response.answers.q as any).rawProbabilities, { a: 0.55, b: 0.44 });
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

test('a confidence above its distribution peak is accepted, as live output requires', () => {
  // The former INFERRED rule rejected this. It was overturned on 2026-09-17 by
  // a real score answer carrying confidence 0.71 over a peak of 0.66; see
  // docs/provider-contract.md section 4 and the live fixture below.
  validateEvaluation(choiceRequest(['a', 'b']), choice('a', { a: 0.55, b: 0.45 }, 1));
  validateEvaluation(choiceRequest(['a', 'b']), choice('a', { a: 0.55, b: 0.45 }, 0.55));
  validateEvaluation(choiceRequest(['a', 'b']), choice('a', { a: 0.55, b: 0.45 }, 0.11));
  // Confidence is still required to be a finite number in [0, 1], which is all
  // the provider documents.
  assert.throws(() => validateEvaluation(choiceRequest(['a', 'b']), choice('a', { a: 0.55, b: 0.45 }, 1.5)), /Invalid distribution/);
  assert.throws(() => validateEvaluation(choiceRequest(['a', 'b']), choice('a', { a: 0.55, b: 0.45 }, -0.1)), /Invalid distribution/);
  assert.throws(() => validateEvaluation(choiceRequest(['a', 'b']), choice('a', { a: 0.55, b: 0.45 }, NaN)), /Invalid distribution/);
});

test('the live response of 2026-09-17 validates exactly as recorded', async () => {
  // The repository's only live evidence: one call to jev-1.13.0 by the lead,
  // stored verbatim. It carries all three primitives at once.
  const fixture = JSON.parse(await readFile(new URL('./fixtures/live-score-2026-09-17.json', import.meta.url), 'utf8'));
  assert.equal(fixture.response.model, 'jev-1.13.0');
  const request: Request = { state: fixture.state, questions: fixture.questions };
  const received: Evaluation = fixture.response;
  const before = structuredClone(received);

  const response = validateEvaluation(request, received);

  assert.deepEqual(received, before, 'the recorded response must come back untouched');
  assert.deepEqual(Object.keys(response.answers), ['urgency', 'kind', 'eligible']);
  // Nothing was renormalized: the live totals are already 1.
  for (const id of ['urgency', 'kind']) {
    const answer = response.answers[id] as any;
    assert.equal(answer.rawProbabilities, undefined, `${id} needed no renormalization`);
    assert.deepEqual(answer.probabilities, (before.answers[id] as any).probabilities);
  }
  // The score answer is the counterexample: confidence 0.71 over a peak 0.66.
  const urgency = response.answers.urgency as any;
  assert.equal(urgency.score, 1.85);
  assert.equal(urgency.confidence, 0.71);
  assert.equal(Math.max(...Object.values(urgency.probabilities as Record<string, number>)), 0.66);
  assert.ok(urgency.confidence > 0.66, 'the recorded confidence really is above the peak');
  // And it confirms the DOCUMENTED score rule against live output: the
  // expectation of the level index is 1.85 exactly.
  const expected = Object.entries(urgency.probabilities as Record<string, number>)
    .reduce((sum, [k, v]) => sum + Number(k) * v, 0);
  assert.ok(Math.abs(expected - 1.85) < 1e-9, `live score expectation was ${expected}`);
  // The choice answer, and the noul answer that carries only its value.
  assert.equal((response.answers.kind as any).choice, 'return');
  assert.deepEqual(response.answers.eligible, { type: 'noul', noul: 0.95 });
  // A score that does not match the live distribution is still rejected.
  const broken = structuredClone(received);
  (broken.answers.urgency as any).score = 3.4;
  assert.throws(() => validateEvaluation(request, broken), /inconsistent with its distribution/);
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
