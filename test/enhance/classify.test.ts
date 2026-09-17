import test from 'node:test';
import assert from 'node:assert/strict';
import { planEnhancedRun, runEnhancedClassification } from '../../src/enhance/classify.ts';
import { StubEvaluator, choiceAnswer, scriptFromTable } from '../../src/enhance/stub.ts';
import { assertComplete } from '../../src/enhance/coverage.ts';
import { categories, documents } from './fixtures/cases.ts';
import { answerTable, recorded } from './fixtures/support.ts';
import type { Request, StubScript } from '../../src/enhance/types.ts';

// `categories` is the criteria object sent to the provider; `optionIds` is just its keys.
const options = categories;
const optionIds = Object.keys(categories);
const records = documents.map(d => ({ id: d.id, text: d.text }));
const named = answerTable(recorded.classification.namedShuffled, optionIds);
const array = answerTable(recorded.classification.arrayShuffled, optionIds);

/** Deterministic shuffle, same permutation the pilot used. */
const shuffled = [...records].sort((a, b) => ((Number(a.id.slice(1)) * 13) % 37) - ((Number(b.id.slice(1)) * 13) % 37));

/**
 * The stub answers from whichever recorded table matches the framing of the
 * request it is handed. Both tables are live-recorded, so a two-pass run
 * reproduces a real disagreement between the two framings.
 */
const byFraming: StubScript = (request, index) => {
  const isKeyed = !Array.isArray((request.state as { records: unknown }).records);
  return scriptFromTable(isKeyed ? named : array, { usage: { input_tokens: 900, output_tokens: 120 } })(request, index);
};

/**
 * Two framings, because that is the disagreement the recorded pilot data can
 * reproduce offline. The production default is two named-reference passes in
 * different record orders; only one keyed recording exists, so the stub cannot
 * reproduce that one.
 */
const config = {
  records, options, abstainOption: 'other',
  passes: [
    { name: 'array-forward', framing: 'array' as const, order: 'input' as const },
    { name: 'keyed-forward', framing: 'keyed' as const, order: 'input' as const }
  ]
};

test('the plan is available before any call and covers every record on every pass', () => {
  const { plans, records: assigned } = planEnhancedRun({ records, options, abstainOption: 'other' });
  assert.equal(plans.length, 2);
  assert.equal(assigned.length, 32);
  for (const { plan } of plans) {
    assert.equal(plan.calls.length, 8, '32 records at four per call');
    assert.deepEqual(plan.calls.map(c => c.recordIds.length), [4, 4, 4, 4, 4, 4, 4, 4]);
    assert.deepEqual([...plan.calls.flatMap(c => c.recordIds)].sort(), records.map(r => r.id).sort());
    assert.deepEqual(plan.oversizedRecordIds, []);
  }
  assert.deepEqual(plans[1].plan.calls[0].recordIds, ['D32', 'D31', 'D30', 'D29'], 'the second pass reverses the order');
});

test('answers map back to the right record under shuffled input order', async () => {
  const stub = new StubEvaluator({ script: scriptFromTable(named), validate: true });
  const run = await runEnhancedClassification({ records: shuffled, options, abstainOption: 'other', passes: [{ name: 'keyed', framing: 'keyed', order: 'input' }] }, stub);
  assert.equal(stub.requests.length, 8);
  assertComplete(run.manifest);
  for (const outcome of run.manifest.outcomes) {
    const expected = recorded.classification.namedShuffled[outcome.id];
    assert.equal(outcome.passes[0].value, expected.choice, `${outcome.id} kept its own answer`);
    assert.equal(outcome.passes[0].confidence, expected.confidence);
  }
});

test('the same records in input order produce the same per-record mapping', async () => {
  const forward = await runEnhancedClassification({ records, options, abstainOption: 'other', passes: [{ name: 'keyed', framing: 'keyed', order: 'input' }] }, new StubEvaluator({ script: scriptFromTable(named) }));
  const reverse = await runEnhancedClassification({ records: shuffled, options, abstainOption: 'other', passes: [{ name: 'keyed', framing: 'keyed', order: 'reverse' }] }, new StubEvaluator({ script: scriptFromTable(named) }));
  const pick = (run: typeof forward) => Object.fromEntries(run.manifest.outcomes.map(o => [o.id, o.passes[0].value]));
  assert.deepEqual(pick(forward), pick(reverse), 'named references make the mapping independent of order and batching');
});

test('a two-pass run produces a complete coverage manifest for all 32 records', async () => {
  const run = await runEnhancedClassification(config, new StubEvaluator({ script: byFraming, validate: true }));
  assertComplete(run.manifest);
  assert.equal(run.manifest.totalRecords, 32);
  const counted = Object.values(run.manifest.byKind).reduce((n, ids) => n + ids.length, 0);
  assert.equal(counted, 32, 'every record lands in exactly one bucket');
  assert.deepEqual(run.manifest.byKind.unaccounted, []);
  assert.deepEqual(run.manifest.byKind.unanswered, []);
  assert.deepEqual(run.manifest.byKind.failed_validation, []);
});

test('the two live-recorded accepted errors are blocked by cross-pass disagreement', async () => {
  const run = await runEnhancedClassification(config, new StubEvaluator({ script: byFraming, validate: true }));
  const byId = new Map(run.manifest.outcomes.map(o => [o.id, o]));

  // D09: a Spanish request for an invoice copy. The array framing called it a
  // contract at 0.81, clearing the 0.75 gate. The named framing called it
  // support at 1.0. The label is support.
  const d09 = byId.get('D09')!;
  assert.equal(d09.kind, 'review');
  assert.equal(d09.value, undefined);
  assert.deepEqual(d09.passes.map(p => p.value), ['contract', 'support']);
  assert.equal(d09.passes[0].confidence, 0.81);
  assert.match(d09.reason, /disagreement blocks action whatever the confidence/);

  // D22: an invoice with a partial payment. The array framing called it a
  // receipt at 0.79, also above the gate. The label is invoice.
  const d22 = byId.get('D22')!;
  assert.equal(d22.kind, 'review');
  assert.deepEqual(d22.passes.map(p => p.value), ['receipt', 'invoice']);
  assert.equal(d22.passes[0].confidence, 0.79);

  for (const id of ['D09', 'D22']) assert.equal(byId.get(id)!.kind === 'accepted', false, `${id} must never be accepted`);
});

test('a single pass would have accepted those two errors, which is why two passes exist', async () => {
  const run = await runEnhancedClassification(
    { records, options, abstainOption: 'other', passes: [{ name: 'array-only', framing: 'array', order: 'input' }] },
    new StubEvaluator({ script: byFraming })
  );
  const byId = new Map(run.manifest.outcomes.map(o => [o.id, o]));
  assert.equal(byId.get('D09')!.kind, 'accepted');
  assert.equal(byId.get('D22')!.kind, 'accepted');
  assert.notEqual(byId.get('D09')!.value, documents.find(d => d.id === 'D09')!.expected, 'and both accepted values are wrong');
  assert.notEqual(byId.get('D22')!.value, documents.find(d => d.id === 'D22')!.expected);
});

test('no record is accepted on confidence alone when the passes disagree', async () => {
  const run = await runEnhancedClassification(config, new StubEvaluator({ script: byFraming }));
  for (const outcome of run.manifest.outcomes) {
    if (outcome.kind !== 'accepted') continue;
    const values = new Set(outcome.passes.map(p => p.value));
    assert.equal(values.size, 1, `${outcome.id} was accepted, so its passes must agree`);
    assert.ok(outcome.confidence! >= 0.75, `${outcome.id} was accepted, so its lowest confidence must clear the gate`);
    assert.notEqual(outcome.value, 'other', 'the abstain option is never an accept');
  }
});

test('one missing answer rejects its whole call, and small batches bound the damage', async () => {
  // The adapter contract rejects a response that omits an asked question, so a
  // single missing answer costs the whole call. With four records per call the
  // blast radius is four records rather than all 32. That is the clearest
  // argument for the small default batch.
  const stub = new StubEvaluator({ script: scriptFromTable(named, { omit: ['D07'] }) });
  const run = await runEnhancedClassification({ records, options, abstainOption: 'other', passes: [{ name: 'keyed', framing: 'keyed', order: 'input' }], maxRetries: 2 }, stub);
  assert.match(run.passes[0].errors.join(' '), /Missing or mismatched answer: D07/);
  assert.equal(run.cost.retries, 2, 'the call carrying D07 was retried before giving up');
  assert.deepEqual(run.manifest.byKind.unanswered, ['D05', 'D06', 'D07', 'D08'], 'exactly the one call is lost');
  assert.equal(run.manifest.byKind.accepted.length + run.manifest.byKind.review.length + run.manifest.byKind.abstain.length, 28);
  assertComplete(run.manifest);
});

test('with response validation off, only the skipped record is unanswered', async () => {
  const stub = new StubEvaluator({ script: scriptFromTable(named, { omit: ['D07'] }) });
  const run = await runEnhancedClassification({ records: records.slice(4, 8), options, abstainOption: 'other', passes: [{ name: 'keyed', framing: 'keyed', order: 'input' }], maxRetries: 1, validateResponses: false }, stub);
  assert.deepEqual(run.manifest.byKind.unanswered, ['D07']);
  assert.match(run.passes[0].errors.join(' '), /1 asked record\(s\) unanswered/);
  assert.equal(run.manifest.outcomes.filter(o => o.kind !== 'unanswered').length, 3, 'the other three records in the call keep their answers');
  assertComplete(run.manifest);
});

test('an answer nobody asked for is rejected and recorded, never attached to a record', async () => {
  const stub = new StubEvaluator({ script: scriptFromTable(named, { extraKeys: { D99: choiceAnswer('invoice', 1, optionIds) } }) });
  const run = await runEnhancedClassification({ records: records.slice(0, 4), options, abstainOption: 'other', passes: [{ name: 'keyed', framing: 'keyed', order: 'input' }], maxRetries: 0 }, stub);
  assert.match(run.passes[0].errors.join(' '), /rejected unasked answer keys D99/);
  assert.deepEqual(run.passes[0].mappings[0].rejected, ['D99']);
  assert.equal(run.manifest.outcomes.some(o => o.id === 'D99'), false);
  assertComplete(run.manifest);
});

/**
 * Responses the validator must reject however the total is read.
 *
 * The earlier fixture here used a distribution totalling 0.99, which the
 * validator once rejected on a 0.001 tolerance. It no longer does, and it
 * should not: a two-decimal total of 0.99 is what the provider actually sends,
 * so a rounding-sized shortfall is renormalized rather than refused. These
 * fixtures are malformed in ways no rounding rule can excuse. One omits a level
 * that was asked about. The other's mass is a tenth short, ten rounding steps
 * outside the band.
 */
const malformedDistributions = {
  'a level that was asked about is missing entirely': { type: 'choice' as const, choice: 'invoice', confidence: 1, probabilities: { invoice: 0.5, receipt: 0.5, support: 0, contract: 0 } },
  'the total is 0.9, far outside any rounding step': { type: 'choice' as const, choice: 'invoice', confidence: 1, probabilities: { invoice: 0.5, receipt: 0.4, support: 0, contract: 0, other: 0 } }
};

for (const [why, answer] of Object.entries(malformedDistributions)) {
  test(`a malformed distribution fails validation and is reported, not accepted: ${why}`, async () => {
    const stub = new StubEvaluator({ script: scriptFromTable({ ...named, D01: answer }) });
    const run = await runEnhancedClassification({ records: records.slice(0, 4), options, abstainOption: 'other', passes: [{ name: 'keyed', framing: 'keyed', order: 'input' }], maxRetries: 1 }, stub);
    assert.match(run.passes[0].errors.join(' '), /Invalid distribution/);
    assert.equal(run.manifest.byKind.unanswered.length, 4, 'the whole call was rejected, so no record in it got an answer');
    assertComplete(run.manifest);
    assert.equal(run.cost.calls, 2, 'the rejected call and its retry both cost money');
    assert.equal(run.cost.retries, 1);
  });
}

test('the fixtures above really are malformed against the options actually asked', () => {
  // Pins the premise, so a change to the option list cannot quietly turn
  // "missing a level" into "carries every level".
  assert.equal(optionIds.length, 5);
  assert.equal(Object.keys(malformedDistributions['a level that was asked about is missing entirely'].probabilities).length, 4);
  assert.equal(Object.values(malformedDistributions['the total is 0.9, far outside any rounding step'].probabilities).reduce((x, y) => x + y, 0), 0.9);
});

test('cost accounting reports calls, tokens, retries and per-call latency', async () => {
  const run = await runEnhancedClassification(config, new StubEvaluator({ script: byFraming }));
  assert.equal(run.cost.calls, 16, 'two passes of eight calls');
  assert.equal(run.cost.perCallLatency.length, 16);
  assert.equal(run.cost.retries, 0);
  assert.equal(run.cost.inputTokens, 16 * 900);
  assert.equal(run.cost.outputTokens, 16 * 120);
  assert.equal(run.cost.estimated, false, 'the stub reports usage, so nothing was estimated');
  assert.ok(run.cost.wallMs >= 0);
});

test('with no usage from the provider the totals are marked estimated', async () => {
  const run = await runEnhancedClassification({ records: records.slice(0, 4), options, abstainOption: 'other', passes: [{ name: 'keyed', framing: 'keyed', order: 'input' }] }, new StubEvaluator({ script: scriptFromTable(named) }));
  assert.equal(run.cost.estimated, true);
  assert.ok(run.cost.inputTokens > 0);
});

test('an oversized record is never sent and is routed to review, not dropped', async () => {
  const oversized = [...records.slice(0, 3), { id: 'BIG', text: 'z'.repeat(200000) }];
  const run = await runEnhancedClassification({ records: oversized, options, abstainOption: 'other', passes: [{ name: 'keyed', framing: 'keyed', order: 'input' }] }, new StubEvaluator({ script: scriptFromTable(named) }));
  assert.deepEqual(run.plans[0].plan.oversizedRecordIds, ['BIG']);
  const big = run.manifest.outcomes.find(o => o.id === 'BIG')!;
  assert.equal(big.kind, 'review');
  assert.match(big.reason, /record and its question exceed the per-call allowance/);
  assertComplete(run.manifest);
  for (const request of run.passes[0].plan.calls) assert.equal(request.recordIds.includes('BIG'), false);
});

test('records with no caller id still get stable references and full coverage', async () => {
  const anonymous = documents.slice(0, 6).map(d => ({ text: d.text }));
  const table = Object.fromEntries(anonymous.map((_, i) => [`records/${i}`, choiceAnswer('invoice', 0.9, optionIds)]));
  const run = await runEnhancedClassification({ records: anonymous, options, abstainOption: 'other', passes: [{ name: 'keyed', framing: 'keyed', order: 'input' }] }, new StubEvaluator({ script: scriptFromTable(table) }));
  assertComplete(run.manifest);
  assert.deepEqual(run.manifest.byKind.accepted, ['records/0', 'records/1', 'records/2', 'records/3', 'records/4', 'records/5']);
});

test('the stub never reaches the network and needs no key', async () => {
  const stub = new StubEvaluator({ script: byFraming });
  await runEnhancedClassification({ records: records.slice(0, 4), options, abstainOption: 'other' }, stub);
  assert.equal(process.env.TYPESAFE_API_KEY, undefined, 'these tests run with no API key set');
  for (const request of stub.requests) assert.ok(request.questions && request.state);
});

test('a batch size of one is legal and changes only the plan, not the mapping', async () => {
  const run = await runEnhancedClassification({ records, options, abstainOption: 'other', budget: { maxRecordsPerCall: 1 }, passes: [{ name: 'keyed', framing: 'keyed', order: 'input' }] }, new StubEvaluator({ script: scriptFromTable(named) }));
  assert.equal(run.cost.calls, 32);
  assertComplete(run.manifest);
  for (const outcome of run.manifest.outcomes) assert.equal(outcome.passes[0].value, recorded.classification.namedShuffled[outcome.id].choice);
});
