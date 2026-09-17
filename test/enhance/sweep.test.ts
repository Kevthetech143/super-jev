import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import {
  DEFAULT_SWEEP_GATE, MAX_QUESTIONS_PER_CALL, cellsFor, buildSweepRequest, foldRecordKind,
  formatSweepReport, planSweep, runSweep, validateCells, validateQuestions,
  type SweepInputRecord, type SweepQuestion, type SweepRun
} from '../../src/enhance/sweep.ts';
import { StubEvaluator, choiceAnswer } from '../../src/enhance/stub.ts';
import { toRefs, assignIds } from '../../src/enhance/reference.ts';
import { validateEvaluation } from '../../src/jev.ts';
import type { Answer, Evaluation, Evaluator, Question, Request } from '../../src/types.ts';

const QUESTIONS: SweepQuestion[] = [
  { name: 'kind', instructions: 'Which kind is it?', criteria: { FACT: 'a checkable statement', BEHAVIOR: 'how to act', HISTORY: 'a dated event', OTHER: 'none of these' } },
  { name: 'safe', instructions: 'Is it safe to publish?', criteria: { SAFE: 'no secret of any sort', UNSAFE: 'carries a secret' } },
  { name: 'fresh', instructions: 'Is it current?', criteria: { FRESH: 'still current', POSSIBLY_STALE: 'marked superseded or dated' } }
];

function records(n: number, textLength = 40): SweepInputRecord[] {
  return Array.from({ length: n }, (_, i) => ({ id: `rec-${i}`, text: `record ${i} `.padEnd(textLength, 'x'), meta: { card: 'awareness', index: i } }));
}

/**
 * An evaluator whose answers are decided by a table keyed on the LOGICAL cell
 * key, `q.<name>.<recordId>`. The test therefore never has to know a wire key,
 * and a table lookup that misses is a mapping bug rather than a silent default.
 */
function tableEvaluator(table: Record<string, { choice: string; confidence: number }>, options: { omit?: string[]; extraKeys?: Record<string, Answer>; corrupt?: string[] } = {}) {
  const requests: Request[] = [];
  const omit = new Set(options.omit ?? []);
  const corrupt = new Set(options.corrupt ?? []);
  const evaluator: Evaluator = {
    evaluate: async (request: Request) => {
      requests.push(request);
      const state = (request.state as { records: Record<string, { id: string }> }).records;
      const answers: Record<string, Answer> = {};
      for (const [wireKey, question] of Object.entries(request.questions) as [string, Question][]) {
        // Recover the logical key from what was actually sent: the wire key ends
        // with the record's own state key, and the question name sits between.
        const recordKey = Object.keys(state).find(k => wireKey === `q_${wireKey.slice(2, wireKey.length - k.length - 1)}_${k}`);
        assert.ok(recordKey, `wire key ${wireKey} does not name a record in the state`);
        const name = wireKey.slice(2, wireKey.length - recordKey.length - 1);
        const logical = `q.${name}.${state[recordKey].id}`;
        if (omit.has(logical)) continue;
        if (corrupt.has(logical)) { answers[wireKey] = { type: 'choice', choice: 'NOT_AN_OPTION', confidence: 0.99, probabilities: { NOT_AN_OPTION: 1 } } as Answer; continue; }
        const entry = table[logical];
        assert.ok(entry, `no table entry for ${logical}`);
        if (question.type !== 'choice') throw new Error('expected a choice question');
        answers[wireKey] = choiceAnswer(entry.choice, entry.confidence, Object.keys(question.criteria));
      }
      for (const [k, v] of Object.entries(options.extraKeys ?? {})) answers[k] = v;
      return { model: 'table-offline', answers } satisfies Evaluation;
    }
  };
  return { evaluator, requests };
}

/** Every cell answered at a fixed confidence with the first option. */
function uniformTable(ids: string[], questions: SweepQuestion[], confidence: number) {
  const table: Record<string, { choice: string; confidence: number }> = {};
  for (const id of ids) for (const q of questions) table[`q.${q.name}.${id}`] = { choice: Object.keys(q.criteria)[0], confidence };
  return table;
}

// ---------------------------------------------------------------- expansion

test('the sweep expands one question per record for every question in the file', async () => {
  const input = records(3);
  const ids = input.map(r => r.id!);
  const { evaluator, requests } = tableEvaluator(uniformTable(ids, QUESTIONS, 0.95));
  const run = await runSweep({ records: input, questions: QUESTIONS, budget: { maxRecordsPerCall: 3, maxInputTokens: 40_000 } }, evaluator);

  // One call, and it carried records x questions questions.
  assert.equal(requests.length, 1);
  assert.equal(Object.keys(requests[0].questions).length, 3 * QUESTIONS.length);
  // Every record is in the state exactly once, and only id and text are sent.
  const state = (requests[0].state as { records: Record<string, unknown> }).records;
  assert.equal(Object.keys(state).length, 3);
  for (const entry of Object.values(state)) assert.deepEqual(Object.keys(entry as object).sort(), ['id', 'text']);

  // Every record carries every question back, under the logical q.<name>.<id> key.
  assert.equal(run.results.length, 3);
  for (const result of run.results) {
    assert.equal(result.cells.length, QUESTIONS.length);
    assert.deepEqual(result.cells.map(c => c.questionName), QUESTIONS.map(q => q.name));
    assert.deepEqual(result.cells.map(c => c.logicalKey), QUESTIONS.map(q => `q.${q.name}.${result.id}`));
    for (const cell of result.cells) assert.equal(cell.kind, 'accepted');
  }
  assert.equal(run.plan.totalCells, 9);
});

test('meta is echoed back on every record and never sent to the provider', async () => {
  const input = records(2);
  const { evaluator, requests } = tableEvaluator(uniformTable(input.map(r => r.id!), QUESTIONS, 0.95));
  const run = await runSweep({ records: input, questions: QUESTIONS, budget: { maxInputTokens: 40_000 } }, evaluator);
  assert.deepEqual(run.results.map(r => r.meta), [{ card: 'awareness', index: 0 }, { card: 'awareness', index: 1 }]);
  assert.ok(!JSON.stringify(requests[0]).includes('awareness'), 'meta reached the provider');
});

test('an answer is mapped by the key that was sent, not by position', async () => {
  const input = records(4);
  const ids = input.map(r => r.id!);
  const table = uniformTable(ids, QUESTIONS, 0.95);
  // Give one record a distinguishable answer on one question only.
  table['q.kind.rec-2'] = { choice: 'HISTORY', confidence: 0.91 };
  const { evaluator } = tableEvaluator(table);
  const run = await runSweep({ records: input, questions: QUESTIONS, budget: { maxRecordsPerCall: 4, maxInputTokens: 40_000 } }, evaluator);
  const byId = new Map(run.results.map(r => [r.id, r]));
  assert.equal(byId.get('rec-2')!.cells.find(c => c.questionName === 'kind')!.choice, 'HISTORY');
  for (const id of ['rec-0', 'rec-1', 'rec-3']) {
    assert.equal(byId.get(id)!.cells.find(c => c.questionName === 'kind')!.choice, 'FACT');
  }
});

test('cell keys are safe object keys, unique in a call, and paired to their record', () => {
  const refs = toRefs(assignIds([{ id: 'a/b c', text: 'x' }, { id: '__proto__', text: 'y' }]));
  const cells = cellsFor(refs, QUESTIONS);
  assert.equal(cells.length, 2 * QUESTIONS.length);
  assert.equal(new Set(cells.map(c => c.wireKey)).size, cells.length);
  for (const cell of cells) {
    assert.match(cell.wireKey, /^[A-Za-z][A-Za-z0-9_]{0,63}$/);
    assert.equal(cell.logicalKey, `q.${cell.questionName}.${cell.recordId}`);
  }
  // The request keys are exactly the wire keys, so nothing is matched by order.
  const request = buildSweepRequest(refs, QUESTIONS, cells);
  assert.deepEqual(Object.keys(request.questions).sort(), cells.map(c => c.wireKey).sort());
});

test('a question set is validated before anything is planned', () => {
  assert.throws(() => validateQuestions([]), /at least one question/);
  assert.throws(() => validateQuestions([{ name: 'a b', instructions: 'x', criteria: { A: 'a', B: 'b' } }]), /Question name must match/);
  assert.throws(() => validateQuestions([{ name: 'kind', instructions: 'x', criteria: { A: 'a' } }]), /at least two options/);
  assert.throws(() => validateQuestions([QUESTIONS[0], QUESTIONS[0]]), /Duplicate question name/);
  assert.throws(() => validateQuestions([{ name: 'kind', instructions: '  ', criteria: { A: 'a', B: 'b' } }]), /no instructions/);
});

// ---------------------------------------------------------------- the cap

test('the question cap lowers records per call, and the plan says so', () => {
  // 3 questions per record against a cap of 10 allows 3 records per call.
  const plan = planSweep({ records: records(12), questions: QUESTIONS, budget: { maxRecordsPerCall: 8, maxInputTokens: 200_000 }, maxQuestionsPerCall: 10 });
  assert.equal(plan.effectiveRecordsPerCall, 3);
  assert.match(plan.recordsPerCallReason, /10-question cap allows 3 record\(s\)/);
  for (const call of plan.plan.calls) {
    assert.ok(call.recordIds.length <= 3, `call ${call.index} carried ${call.recordIds.length} records`);
    assert.ok(call.recordIds.length * QUESTIONS.length <= 10);
  }
  assert.equal(plan.plan.calls.length, 4);
});

test('no planned call ever exceeds the 255-question cap at the default', () => {
  // A huge record allowance and a huge batch request: only the cap can bind.
  const plan = planSweep({ records: records(400, 10), questions: QUESTIONS, budget: { maxRecordsPerCall: 1000, maxInputTokens: 5_000_000, reservedForOutput: 0 } });
  assert.equal(MAX_QUESTIONS_PER_CALL, 255);
  assert.equal(plan.effectiveRecordsPerCall, Math.floor(255 / QUESTIONS.length));
  assert.equal(plan.effectiveRecordsPerCall, 85);
  for (const call of plan.plan.calls) assert.ok(call.recordIds.length * QUESTIONS.length <= 255);
  assert.equal(plan.plan.calls.length, Math.ceil(400 / 85));
});

test('the tighter of the two caps wins', () => {
  // --batch 2 is tighter than the cap's 85, so it decides.
  const plan = planSweep({ records: records(5), questions: QUESTIONS, budget: { maxRecordsPerCall: 2, maxInputTokens: 200_000 } });
  assert.equal(plan.effectiveRecordsPerCall, 2);
  assert.match(plan.recordsPerCallReason, /requested 2 record\(s\) per call fits/);
  assert.deepEqual(plan.plan.calls.map(c => c.recordIds.length), [2, 2, 1]);
});

test('more questions than the cap is refused, not truncated', () => {
  const many = Array.from({ length: 12 }, (_, i) => ({ name: `q${i}`, instructions: 'x', criteria: { A: 'a', B: 'b' } }));
  assert.throws(() => planSweep({ records: records(2), questions: many, maxQuestionsPerCall: 10 }), /exceeds the 10-question cap/);
});

test('the token budget can bind before the cap does', () => {
  // A small window with long records: the budget splits calls well under 85.
  const plan = planSweep({ records: records(10, 2000), questions: QUESTIONS, budget: { maxInputTokens: 8000, maxRecordsPerCall: 1000 } });
  assert.ok(plan.plan.calls.length > 1, 'expected the budget to split the run');
  for (const call of plan.plan.calls) assert.ok(call.estimatedInputTokens <= 8000);
  assert.ok(plan.plan.questionsCounted, 'question text must be counted, not reserved flat');
});

test('a record too big for one call is never sent and lands in review', async () => {
  const input = [...records(2, 40), { id: 'huge', text: 'y'.repeat(200_000) }];
  const { evaluator } = tableEvaluator(uniformTable(['rec-0', 'rec-1'], QUESTIONS, 0.95));
  const run = await runSweep({ records: input, questions: QUESTIONS, budget: { maxInputTokens: 8000 } }, evaluator);
  assert.deepEqual(run.plan.plan.oversizedRecordIds, ['huge']);
  const huge = run.results.find(r => r.id === 'huge')!;
  assert.equal(huge.kind, 'review');
  assert.match(huge.cells[0].reason, /never sent/);
  assert.equal(run.manifest.complete, true, 'an oversized record must still be accounted for');
  assert.ok(run.manifest.byKind.review.includes('huge'));
});

// ---------------------------------------------------------- manifest completeness

test('the manifest accounts for every input record exactly once', async () => {
  const input = records(30);
  const { evaluator } = tableEvaluator(uniformTable(input.map(r => r.id!), QUESTIONS, 0.95));
  const run = await runSweep({ records: input, questions: QUESTIONS, budget: { maxRecordsPerCall: 4, maxInputTokens: 40_000 } }, evaluator);
  assert.equal(run.manifest.totalRecords, 30);
  assert.equal(run.manifest.complete, true);
  assert.deepEqual(run.manifest.problems, []);
  const all = Object.values(run.manifest.byKind).flat();
  assert.equal(all.length, 30, 'every record appears in exactly one bucket');
  assert.deepEqual([...all].sort(), input.map(r => r.id!).sort());
  assert.equal(run.manifest.byKind.unaccounted.length, 0);
  assert.equal(run.results.length, 30);
});

test('a record whose answers never arrive is unanswered, not missing', async () => {
  const input = records(3);
  const omit = QUESTIONS.map(q => `q.${q.name}.rec-1`);
  const { evaluator } = tableEvaluator(uniformTable(input.map(r => r.id!), QUESTIONS, 0.95), { omit });
  const run = await runSweep({ records: input, questions: QUESTIONS, budget: { maxInputTokens: 40_000 }, maxRetries: 0 }, evaluator);
  assert.equal(run.manifest.complete, true);
  assert.deepEqual(run.manifest.byKind.unanswered, ['rec-1']);
  assert.deepEqual(run.manifest.byKind.accepted.sort(), ['rec-0', 'rec-2']);
});

test('one missing cell costs its record only, and the rest of the call still lands', async () => {
  const input = records(4);
  const { evaluator } = tableEvaluator(uniformTable(input.map(r => r.id!), QUESTIONS, 0.95), { omit: ['q.safe.rec-2'] });
  const run = await runSweep({ records: input, questions: QUESTIONS, budget: { maxRecordsPerCall: 4, maxInputTokens: 40_000 }, maxRetries: 0 }, evaluator);
  const byId = new Map(run.results.map(r => [r.id, r]));
  assert.equal(byId.get('rec-2')!.kind, 'unanswered');
  assert.equal(byId.get('rec-2')!.cells.find(c => c.questionName === 'kind')!.kind, 'accepted');
  for (const id of ['rec-0', 'rec-1', 'rec-3']) assert.equal(byId.get(id)!.kind, 'accepted');
  assert.equal(run.manifest.complete, true);
});

test('one malformed answer costs its own cell, not the whole call', async () => {
  const input = records(4);
  const { evaluator } = tableEvaluator(uniformTable(input.map(r => r.id!), QUESTIONS, 0.95), { corrupt: ['q.kind.rec-1'] });
  const run = await runSweep({ records: input, questions: QUESTIONS, budget: { maxRecordsPerCall: 4, maxInputTokens: 40_000 }, maxRetries: 0 }, evaluator);
  const byId = new Map(run.results.map(r => [r.id, r]));
  assert.equal(byId.get('rec-1')!.kind, 'unanswered');
  for (const id of ['rec-0', 'rec-2', 'rec-3']) assert.equal(byId.get(id)!.kind, 'accepted', `${id} was lost to another record's bad answer`);
  assert.equal(run.manifest.complete, true);
  assert.ok(run.errors.some(e => /Invalid choice|Invalid distribution/.test(e)), `expected a validation error, got ${JSON.stringify(run.errors)}`);
});

test('an answer key nobody asked for is rejected and never attached to a record', async () => {
  const input = records(2);
  const extra = { q_kind_not_a_record: choiceAnswer('FACT', 0.99, Object.keys(QUESTIONS[0].criteria)) };
  const { evaluator } = tableEvaluator(uniformTable(input.map(r => r.id!), QUESTIONS, 0.95), { extraKeys: extra });
  const run = await runSweep({ records: input, questions: QUESTIONS, budget: { maxInputTokens: 40_000 } }, evaluator);
  assert.equal(run.results.length, 2);
  assert.ok(run.errors.some(e => e.includes('rejected unasked answer key q_kind_not_a_record')));
  assert.equal(run.manifest.complete, true);
});

test('validateCells salvages the good answers from a response with one bad one', () => {
  const refs = toRefs(assignIds([{ id: 'a', text: 'x' }, { id: 'b', text: 'y' }]));
  const cells = cellsFor(refs, [QUESTIONS[1]]);
  const request = buildSweepRequest(refs, [QUESTIONS[1]], cells);
  const good = choiceAnswer('SAFE', 0.9, ['SAFE', 'UNSAFE']);
  const evaluation: Evaluation = { model: 'm', answers: { [cells[0].wireKey]: good, [cells[1].wireKey]: { type: 'choice', choice: 'SAFE', confidence: 5, probabilities: { SAFE: 1, UNSAFE: 0 } } as Answer } };
  const result = validateCells(request, evaluation);
  assert.equal(result.answers.size, 1);
  assert.ok(result.answers.has(cells[0].wireKey));
  assert.equal(result.errors.length, 1);
  // The contract itself would have rejected both answers at once, which is the
  // blast radius this per-cell validation exists to shrink.
  assert.throws(() => validateEvaluation(request, evaluation), /Invalid distribution/);
  // And it accepts the salvaged one on its own.
  const single: Request = { state: request.state, questions: { [cells[0].wireKey]: request.questions[cells[0].wireKey] } };
  assert.doesNotThrow(() => validateEvaluation(single, { model: 'm', answers: { [cells[0].wireKey]: good } }));
});

// ------------------------------------------------------------- gate grouping

test('a record is accepted only when every question clears the gate', async () => {
  const input = records(4);
  const ids = input.map(r => r.id!);
  const table = uniformTable(ids, QUESTIONS, 0.95);
  table['q.fresh.rec-1'] = { choice: 'FRESH', confidence: 0.79 };   // just under 0.80
  table['q.kind.rec-3'] = { choice: 'FACT', confidence: 0.80 };     // exactly at the gate
  const { evaluator } = tableEvaluator(table);
  const run = await runSweep({ records: input, questions: QUESTIONS, budget: { maxRecordsPerCall: 4, maxInputTokens: 40_000 } }, evaluator);
  const byId = new Map(run.results.map(r => [r.id, r]));
  assert.equal(byId.get('rec-0')!.kind, 'accepted');
  assert.equal(byId.get('rec-1')!.kind, 'review', 'one cell at 0.79 must block the whole record');
  assert.match(byId.get('rec-1')!.cells.find(c => c.questionName === 'fresh')!.reason, /below the 0.8 gate/);
  assert.equal(byId.get('rec-2')!.kind, 'accepted');
  assert.equal(byId.get('rec-3')!.kind, 'accepted', 'a cell exactly at the gate clears it');
  assert.deepEqual(run.manifest.byKind.review, ['rec-1']);
  assert.equal(run.plan.gate, DEFAULT_SWEEP_GATE);
});

test('the gate is configurable and moves the grouping', async () => {
  const input = records(2);
  const table = uniformTable(input.map(r => r.id!), QUESTIONS, 0.85);
  const { evaluator } = tableEvaluator(table);
  const loose = await runSweep({ records: input, questions: QUESTIONS, gate: 0.80, budget: { maxInputTokens: 40_000 } }, evaluator);
  assert.deepEqual(loose.results.map(r => r.kind), ['accepted', 'accepted']);
  const { evaluator: second } = tableEvaluator(table);
  const strict = await runSweep({ records: input, questions: QUESTIONS, gate: 0.90, budget: { maxInputTokens: 40_000 } }, second);
  assert.deepEqual(strict.results.map(r => r.kind), ['review', 'review']);
  assert.equal(strict.plan.gate, 0.90);
});

test('the worst cell decides the record', () => {
  assert.equal(foldRecordKind(['accepted', 'accepted']), 'accepted');
  assert.equal(foldRecordKind(['accepted', 'review']), 'review');
  assert.equal(foldRecordKind(['review', 'unanswered']), 'unanswered');
  assert.equal(foldRecordKind(['unanswered', 'failed_validation']), 'failed_validation');
  assert.equal(foldRecordKind([]), 'unanswered');
});

test('the report groups accepted and review with counts that add up', async () => {
  const input = records(5);
  const table = uniformTable(input.map(r => r.id!), QUESTIONS, 0.95);
  table['q.safe.rec-4'] = { choice: 'UNSAFE', confidence: 0.55 };
  const { evaluator } = tableEvaluator(table);
  const run = await runSweep({ records: input, questions: QUESTIONS, budget: { maxRecordsPerCall: 2, maxInputTokens: 40_000 } }, evaluator);
  const report = formatSweepReport(run, { source: '/tmp/records.jsonl' });
  assert.match(report, /\| ACCEPTED \| 4 \|/);
  assert.match(report, /\| REVIEW \| 1 \|/);
  assert.match(report, /## ACCEPTED/);
  assert.match(report, /## REVIEW/);
  // Every record id appears in the report exactly once.
  for (const id of input.map(r => r.id!)) {
    assert.equal(report.split(`| ${id} |`).length - 1, 1, `${id} is not listed exactly once`);
  }
  assert.match(report, /Not an accuracy claim/);
});

// ------------------------------------------------------------------- the CLI

const cliPath = new URL('../../src/sweep-cli.ts', import.meta.url).pathname;

async function fixtureDir(n = 6) {
  const dir = await mkdtemp(join(tmpdir(), 'sweep-cli-'));
  await writeFile(join(dir, 'records.jsonl'), records(n).map(r => JSON.stringify(r)).join('\n') + '\n');
  await writeFile(join(dir, 'questions.json'), JSON.stringify({ questions: QUESTIONS }, null, 2));
  return dir;
}

test('the CLI dry run prints a plan, writes only plan.json, and needs no key', async () => {
  const dir = await fixtureDir(10);
  try {
    const result = spawnSync(process.execPath, [cliPath, '--records', join(dir, 'records.jsonl'), '--questions', join(dir, 'questions.json'), '--out', join(dir, 'out'), '--dry-run', '--batch', '4'],
      { encoding: 'utf8', env: { ...process.env, TYPESAFE_API_KEY: '' } });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /sweep plan: 10 record\(s\) x 3 question\(s\)/);
    assert.match(result.stdout, /calls: 3/);
    assert.match(result.stdout, /total estimated input tokens: \d+/);
    assert.match(result.stderr, /Dry run: nothing was sent/);
    const plan = JSON.parse(await readFile(join(dir, 'out', 'plan.json'), 'utf8'));
    assert.equal(plan.mode, 'dry-run');
    assert.equal(plan.cells, 30);
    assert.equal(plan.calls, 3);
    assert.deepEqual(plan.perCall.map((c: { records: number }) => c.records), [4, 4, 2]);
    // A dry run writes nothing else.
    await assert.rejects(readFile(join(dir, 'out', 'results.jsonl'), 'utf8'));
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('the CLI refuses a live run with no API key, before reading any file', async () => {
  const dir = await fixtureDir(2);
  try {
    const result = spawnSync(process.execPath, [cliPath, '--records', join(dir, 'missing.jsonl'), '--questions', join(dir, 'questions.json'), '--out', join(dir, 'out')],
      { encoding: 'utf8', env: { ...process.env, TYPESAFE_API_KEY: '' } });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Set TYPESAFE_API_KEY/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('the CLI stub run writes every output file and a complete manifest', async () => {
  const dir = await fixtureDir(9);
  try {
    const result = spawnSync(process.execPath, [cliPath, '--records', join(dir, 'records.jsonl'), '--questions', join(dir, 'questions.json'), '--out', join(dir, 'out'), '--stub', '--batch', '4'],
      { encoding: 'utf8', env: { ...process.env, TYPESAFE_API_KEY: '' } });
    assert.equal(result.status, 0, result.stderr);
    const manifest = JSON.parse(await readFile(join(dir, 'out', 'manifest.json'), 'utf8'));
    assert.equal(manifest.totalRecords, 9);
    assert.equal(manifest.complete, true);
    assert.equal(Object.values(manifest.byKind).flat().length, 9);
    const lines = (await readFile(join(dir, 'out', 'results.jsonl'), 'utf8')).trim().split('\n');
    assert.equal(lines.length, 9);
    for (const line of lines) {
      const row = JSON.parse(line);
      assert.deepEqual(Object.keys(row.answers).sort(), ['fresh', 'kind', 'safe']);
      assert.equal(row.meta.card, 'awareness');
      for (const [name, answer] of Object.entries(row.answers) as [string, { key: string }][]) {
        assert.equal(answer.key, `q.${name}.${row.id}`);
      }
    }
    const cost = JSON.parse(await readFile(join(dir, 'out', 'cost.json'), 'utf8'));
    assert.equal(cost.calls, 3);
    assert.equal(cost.estimated, true);
    assert.match(cost.note, /not a billing figure/);
    const report = await readFile(join(dir, 'out', 'report.md'), 'utf8');
    assert.match(report, /# Sweep report/);
    assert.match(report, /stub-offline/);
    assert.match(result.stderr, /manifest complete=true/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('the CLI never overwrites an existing output file', async () => {
  const dir = await fixtureDir(2);
  try {
    const args = [cliPath, '--records', join(dir, 'records.jsonl'), '--questions', join(dir, 'questions.json'), '--out', join(dir, 'out'), '--stub'];
    const first = spawnSync(process.execPath, args, { encoding: 'utf8', env: { ...process.env, TYPESAFE_API_KEY: '' } });
    assert.equal(first.status, 0, first.stderr);
    const second = spawnSync(process.execPath, args, { encoding: 'utf8', env: { ...process.env, TYPESAFE_API_KEY: '' } });
    assert.equal(second.status, 1);
    assert.match(second.stderr, /already exists/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('the CLI rejects malformed records and unknown flags', async () => {
  const dir = await fixtureDir(2);
  try {
    await writeFile(join(dir, 'bad.jsonl'), '{"id":"a","text":"ok"}\nnot json\n');
    const bad = spawnSync(process.execPath, [cliPath, '--records', join(dir, 'bad.jsonl'), '--questions', join(dir, 'questions.json'), '--dry-run'], { encoding: 'utf8' });
    assert.equal(bad.status, 1);
    assert.match(bad.stderr, /line 2 is not valid JSON/);
    const unknown = spawnSync(process.execPath, [cliPath, '--records', join(dir, 'records.jsonl'), '--questions', join(dir, 'questions.json'), '--frobnicate'], { encoding: 'utf8' });
    assert.equal(unknown.status, 1);
    assert.match(unknown.stderr, /Unknown argument --frobnicate/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});
