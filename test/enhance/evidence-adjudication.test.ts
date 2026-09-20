import test from 'node:test';
import assert from 'node:assert/strict';
import { adjudicateEvidence, EvidenceAdjudicationError, type EvidenceAdjudicationInput } from '../../src/enhance/evidence-adjudication.ts';
import type { Answer, Evaluation, Evaluator, Request } from '../../src/types.ts';

function answer(value: 'SUPPORTS' | 'CONFLICTS' | 'INSUFFICIENT'): Answer {
  return { type: 'choice', choice: value, confidence: 0.99, probabilities: { SUPPORTS: value === 'SUPPORTS' ? 0.99 : 0.005, CONFLICTS: value === 'CONFLICTS' ? 0.99 : 0.005, INSUFFICIENT: value === 'INSUFFICIENT' ? 0.99 : 0.005 } };
}

function transport(verdicts: Record<string, 'SUPPORTS' | 'CONFLICTS' | 'INSUFFICIENT'>, overall: 'COMPLETE' | 'PARTIAL' | 'CONFLICTING' | 'INSUFFICIENT' = 'COMPLETE'): { evaluator: Evaluator; requests: Request[] } {
  const requests: Request[] = [];
  return { requests, evaluator: { evaluate: async request => {
    requests.push(request);
    const answers = Object.fromEntries(Object.keys(request.questions).filter(k => verdicts[k]).map(k => [k, answer(verdicts[k])]));
    if (request.questions.overall) answers.overall = { type: 'choice', choice: overall, confidence: 0.99, probabilities: { COMPLETE: overall === 'COMPLETE' ? 0.99 : 0.003, PARTIAL: overall === 'PARTIAL' ? 0.99 : 0.003, CONFLICTING: overall === 'CONFLICTING' ? 0.99 : 0.003, INSUFFICIENT: overall === 'INSUFFICIENT' ? 0.99 : 0.001 } };
    return { model: 'mock-protocol', answers, usage: { input_tokens: 12, output_tokens: 3 } } satisfies Evaluation;
  } } };
}

function fixture(): EvidenceAdjudicationInput {
  return {
    question: 'What action was recommended, and what completed result supports it?',
    requirements: [
      { id: 'recommendation', description: 'the action explicitly recommended' },
      { id: 'completed-result', description: 'the completed result supporting that recommendation' }
    ],
    passages: [
      { sourceId: 'current', text: 'The team recommends replacing the blue widget. The inspection was completed and found a cracked housing.' },
      { sourceId: 'older', text: 'An older note says the blue widget housing was intact.' },
      { sourceId: 'question', text: 'Should the team perhaps replace the blue widget?' }
    ],
    candidates: [
      { id: 'rec', requirementIds: ['recommendation'], quotes: [{ sourceId: 'current', quote: 'The team recommends replacing the blue widget.' }] },
      { id: 'result', requirementIds: ['completed-result'], quotes: [{ sourceId: 'current', quote: 'The inspection was completed and found a cracked housing.' }] },
      { id: 'mere-question', requirementIds: ['recommendation'], quotes: [{ sourceId: 'question', quote: 'Should the team perhaps replace the blue widget?' }] }
    ]
  };
}

test('returns only passing support and cannot call half coverage complete', async () => {
  const input = fixture();
  const mock = transport({ 'candidate:rec': 'SUPPORTS', 'candidate:result': 'INSUFFICIENT', 'candidate:mere-question': 'INSUFFICIENT' });
  const result = await adjudicateEvidence(input, { transport: mock.evaluator });
  assert.equal(result.status, 'partial');
  assert.deepEqual(result.supportingEvidence.map(x => x.id), ['rec']);
  assert.deepEqual(result.uncoveredRequirementIds, ['completed-result']);
  assert.equal(result.requirementsOrigin, 'caller_supplied');
  assert.deepEqual(result.usage, { inputTokens: 24, outputTokens: 6 });
  const state = mock.requests[0].state as { originalQuestion: string; sourcePassages: unknown[] };
  assert.equal(state.originalQuestion, input.question);
  assert.deepEqual(state.sourcePassages, input.passages, 'full surrounding passages reach the judge');
  assert.match(mock.requests[0].questions['candidate:rec'].instructions, /question, possibility, plan, order, recommendation, and completed result/);
  const overallState = mock.requests[1].state as { sourcePassages: { sourceId: string }[] };
  assert.deepEqual(overallState.sourcePassages.map(x => x.sourceId), ['current'], 'overall call gets only full passages cited by kept support');
});

test('attributes a conflict and does not settle it as support', async () => {
  const input = fixture();
  input.candidates.push({ id: 'history-conflict', requirementIds: ['completed-result'], quotes: [
    { sourceId: 'current', quote: 'found a cracked housing' },
    { sourceId: 'older', quote: 'housing was intact' }
  ] });
  const mock = transport({ 'candidate:rec': 'SUPPORTS', 'candidate:result': 'SUPPORTS', 'candidate:mere-question': 'INSUFFICIENT', 'candidate:history-conflict': 'CONFLICTS' });
  const result = await adjudicateEvidence(input, { transport: mock.evaluator });
  assert.equal(result.status, 'conflicting');
  assert.deepEqual(result.conflicts.map(x => x.id), ['history-conflict']);
  assert.ok(!result.supportingEvidence.some(x => x.id === 'history-conflict'));
  assert.deepEqual(result.conflicts[0].quotes.map(x => x.sourceId), ['current', 'older']);
});

test('rejects stale quotes and unknown sources before any provider call', async () => {
  for (const mutate of [
    (x: EvidenceAdjudicationInput) => { x.candidates[0].quotes[0].quote = 'a stale paraphrase'; },
    (x: EvidenceAdjudicationInput) => { x.candidates[0].quotes[0].sourceId = 'missing'; }
  ]) {
    const input = fixture(); mutate(input);
    let calls = 0;
    const evaluator: Evaluator = { evaluate: async () => { calls++; throw new Error('must not run'); } };
    await assert.rejects(() => adjudicateEvidence(input, { transport: evaluator }), EvidenceAdjudicationError);
    assert.equal(calls, 0);
  }
});

test('missing judge answers and transport errors are distinct from semantic insufficiency', async () => {
  const input = fixture();
  const missing = transport({ 'candidate:rec': 'SUPPORTS' });
  const incomplete = await adjudicateEvidence(input, { transport: missing.evaluator });
  assert.equal(incomplete.status, 'error');
  assert.deepEqual(incomplete.rejected.map(x => x.id), ['result', 'mere-question']);
  const failed = await adjudicateEvidence(input, { transport: { evaluate: async () => { throw new Error('offline'); } } });
  assert.equal(failed.status, 'error');
  assert.equal(failed.error, 'offline');
  assert.equal(failed.calls, 1);
});

test('low-confidence support is rejected without poisoning valid support', async () => {
  const input = fixture();
  const evaluator: Evaluator = { evaluate: async request => {
    if (request.questions.overall) return { model: 'mock', answers: { overall: { type: 'choice', choice: 'PARTIAL', confidence: 0.9, probabilities: { COMPLETE: 0.03, PARTIAL: 0.9, CONFLICTING: 0.03, INSUFFICIENT: 0.04 } } } };
    return { model: 'mock', answers: {
      'candidate:rec': { ...answer('SUPPORTS'), confidence: 0.95 },
      'candidate:result': { ...answer('SUPPORTS'), confidence: 0.60 },
      'candidate:mere-question': answer('INSUFFICIENT')
    } };
  } };
  const result = await adjudicateEvidence(input, { transport: evaluator });
  assert.equal(result.status, 'partial');
  assert.deepEqual(result.supportingEvidence.map(x => x.id), ['rec']);
  assert.match(result.rejected.find(x => x.id === 'result')!.reason, /below threshold/);
  assert.equal(result.error, undefined);
});

test('non-finite candidate confidence cannot enter supporting evidence', async () => {
  const input = fixture();
  const evaluator: Evaluator = { evaluate: async () => ({ model: 'mock', answers: {
    'candidate:rec': { ...answer('SUPPORTS'), confidence: Number.NaN },
    'candidate:result': answer('INSUFFICIENT'), 'candidate:mere-question': answer('INSUFFICIENT')
  } }) };
  const result = await adjudicateEvidence(input, { transport: evaluator });
  assert.equal(result.status, 'error');
  assert.deepEqual(result.supportingEvidence, []);
});

test('a failed second call records both attempted calls', async () => {
  const input = fixture();
  let calls = 0;
  const evaluator: Evaluator = { evaluate: async request => {
    calls++;
    if (calls === 2) throw new Error('overall unavailable');
    return { model: 'mock', answers: Object.fromEntries(Object.keys(request.questions).map(k => [k, answer(k === 'candidate:mere-question' ? 'INSUFFICIENT' : 'SUPPORTS')])) };
  } };
  const result = await adjudicateEvidence(input, { transport: evaluator });
  assert.equal(result.status, 'error');
  assert.equal(result.calls, 2);
});

test('whole-question conflict carries source attribution', async () => {
  const input = fixture();
  const mock = transport({ 'candidate:rec': 'SUPPORTS', 'candidate:result': 'SUPPORTS', 'candidate:mere-question': 'INSUFFICIENT' }, 'CONFLICTING');
  const result = await adjudicateEvidence(input, { transport: mock.evaluator });
  assert.equal(result.status, 'conflicting');
  assert.deepEqual(result.overallConflict?.sourceIds, ['current']);
});
