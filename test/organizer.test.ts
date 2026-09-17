import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdtemp, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { organizer, organizerReport, validateOrganizerInput } from '../src/organizer.ts';
import { run } from '../src/loop.ts';
import type { Evaluation } from '../src/types.ts';

const fixture = JSON.parse(await readFile(new URL('../examples/organizer.json', import.meta.url), 'utf8'));

test('organizer sends only id and text and snapshots caller data', async () => {
  const input = structuredClone(fixture);
  input.records[0].privateMetadata = 'SYNTHETIC_METADATA_NOT_FOR_PROVIDER';
  const domain = organizer(input);
  input.records[0].text = 'Changed after construction';
  input.categories.invoice = 'Changed category';
  const state = { rows: [], complete: false };
  const evidence = await domain.observe(state, new AbortController().signal);
  assert.deepEqual(evidence, { records: fixture.records });
  const question = domain.questions(state, evidence).record_0;
  assert.equal(question.type, 'choice');
  if (question.type === 'choice') assert.equal(question.criteria.invoice, fixture.categories.invoice);
});

test('malformed organizer responses cannot produce groups', async () => {
  const valid: Evaluation = {
    model: 'mock', answers: Object.fromEntries(fixture.records.map((_: unknown, i: number) => [
      `record_${i}`, { type: 'choice', choice: 'invoice', confidence: 1,
        probabilities: { invoice: 1, receipt: 0, support: 0, other: 0 } }
    ]))
  };
  for (const invalid of [undefined, { type: 'noul', noul: 1 },
    { type: 'choice', choice: 'unknown', confidence: 1, probabilities: { invoice: 1, receipt: 0, support: 0, other: 0 } },
    { type: 'choice', choice: 'invoice', confidence: 2, probabilities: { invoice: 1, receipt: 0, support: 0, other: 0 } },
    { type: 'choice', choice: 'invoice', confidence: 1, probabilities: { invoice: 0.5, receipt: 0, support: 0, other: 0 } }
  ]) {
    const evaluation = structuredClone(valid);
    evaluation.answers.record_0 = invalid as Evaluation['answers'][string];
    const events: string[] = [];
    const result = await run({ domain: organizer(fixture), initial: { rows: [], complete: false },
      evaluator: { evaluate: async () => evaluation }, journal: { append: async event => { events.push(event.type); } } });
    assert.equal(result.status, 'error');
    assert.deepEqual(result.state.rows, []);
    assert.ok(!events.includes('action_started'));
  }
});

test('CLI live response contract preserves metadata, groups and review with mocked Jev', () => {
  const response = { model: 'mock-jev', usage: { input_tokens: 100, output_tokens: 50 },
    answers: Object.fromEntries(['invoice', 'receipt', 'support', 'other'].map((choice, i) => [
      `record_${i}`, { type: 'choice', choice, confidence: i === 2 ? 0.5 : 0.95,
        probabilities: Object.fromEntries(Object.keys(fixture.categories).map(k => [k, k === choice ? 1 : 0])) }
    ])) };
  const mock = 'data:text/javascript,' + encodeURIComponent(`
    globalThis.fetch = async (url, init) => {
      const request = JSON.parse(init.body);
      if (url !== 'https://api.typesafe.ai/v1/systemone' || request.model !== 'jev-latest' || Object.keys(request.questions).length !== 4) throw new Error('Unexpected request');
      return new Response(JSON.stringify(${JSON.stringify(response)}));
    };
  `);
  const result = spawnSync(process.execPath, ['--import', mock, 'src/cli.ts', 'organize', 'examples/organizer.json', '--live'], {
    encoding: 'utf8', env: { ...process.env, TYPESAFE_API_KEY: 'fixture' }
  });
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.mode, 'live');
  assert.equal(output.model, response.model);
  assert.deepEqual(output.usage, response.usage);
  assert.equal(output.rows.length, 4);
  assert.deepEqual(output.groups, { invoice: ['bill-1'], receipt: ['receipt-1'], support: [], other: [] });
  assert.deepEqual(output.review, ['help-1', 'misc-1']);
});

test('CLI enforces the byte limit before parsing oversized multibyte input', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-'));
  const file = join(dir, 'large.json');
  try {
    for (const size of [80_000, 80_001]) {
      const raw = '"' + 'é'.repeat(39_999) + '"' + (size === 80_001 ? ' ' : '');
      assert.equal(Buffer.byteLength(raw), size);
      await writeFile(file, raw);
      const result = spawnSync(process.execPath, ['src/cli.ts', 'organize', file, '--demo'], { encoding: 'utf8' });
      assert.equal(result.status, 1);
      assert.match(result.stderr, size === 80_000 ? /Invalid organizer input/ : /Input exceeds 80 KB/);
    }
  } finally { await rm(dir, { recursive: true, force: true }); }
});
test('organizer validates unique IDs, fallback and confidence', () => {
  assert.throws(() => validateOrganizerInput({ ...fixture, records: [fixture.records[0], fixture.records[0]] }));
  assert.throws(() => validateOrganizerInput({ ...fixture, categories: { invoice: 'bill', receipt: 'paid' } }));
  assert.throws(() => validateOrganizerInput({ ...fixture, minConfidence: 2 }));
});
test('organizer groups confident records and preserves low-confidence/other items for review', async () => {
  const answers = Object.fromEntries(['invoice','receipt','support','other'].map((choice, i) => [`record_${i}`, {type:'choice' as const,choice,confidence:i===2?0.4:1,probabilities:Object.fromEntries(Object.keys(fixture.categories).map(k=>[k,k===choice?1:0]))}]));
  const result = await run({ domain:organizer(fixture), initial:{rows:[],complete:false}, evaluator:{evaluate:async()=>({model:'test',answers})}, journal:{append:async()=>{}} });
  assert.equal(result.status,'success');
  const report = organizerReport(fixture,result.state);
  assert.deepEqual(report.groups.invoice,['bill-1']);
  assert.deepEqual(report.groups.support,[]);
  assert.deepEqual(report.review,['help-1','misc-1']);
  assert.equal(report.rows.length,4);
});
test('CLI emits machine-readable offline result', () => {
  const r=spawnSync(process.execPath,['src/cli.ts','organize','examples/organizer.json','--demo'],{encoding:'utf8'});
  assert.equal(r.status,0,r.stderr);
  const output=JSON.parse(r.stdout);
  assert.equal(output.mode,'demo');
  assert.deepEqual(output.groups.support,['help-1']);
});
test('CLI creates output and refuses overwrites', async () => {
  const dir=await mkdtemp(join(tmpdir(),'super-jev-'));
  try {
    const args=['src/cli.ts','organize','examples/organizer.json','--demo','--out',join(dir,'output.json')];
    assert.equal(spawnSync(process.execPath,args).status,0);
    const before=await readFile(join(dir,'output.json'),'utf8');
    assert.equal(spawnSync(process.execPath,args).status,1);
    assert.equal(await readFile(join(dir,'output.json'),'utf8'),before);
  } finally {await rm(dir,{recursive:true,force:true});}
});
test('CLI rejects conflicting modes and unknown flags', () => {
  for(const flags of [['--live','--demo'],['--demo','--force']]) {
    assert.equal(spawnSync(process.execPath,['src/cli.ts','organize','examples/organizer.json',...flags]).status,1);
  }
});

test('organizer rejects forged review flags and incomplete results', async () => {
  const domain = organizer(fixture);
  const rows = fixture.records.map((r: { id: string }, i: number) => ({
    id: r.id, category: i === 3 ? 'other' : 'invoice', confidence: 1, needsReview: i === 3
  }));
  const verify = (value: typeof rows) => domain.verify({ complete: true, rows: value }, new AbortController().signal);
  assert.equal(await verify(rows), true);
  assert.equal(await verify(rows.slice(1)), false);
  assert.equal(await verify(new Array(rows.length)), false);
  assert.equal(await verify(rows.map(r => ({ ...r, needsReview: false }))), false);
  assert.equal(await verify(rows.map(r => ({ ...r, confidence: 0.2 }))), false);
  assert.equal(await verify(rows.map(r => ({ ...r, confidence: 0.75 }))), true);
  assert.equal(domain.tools.group_records.validate(rows.map(r => ({ ...r, needsReview: false }))), false);
});

test('CLI rejects private malformed, modified, oversized and invalid input without echoing it', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-'));
  const file = join(dir, 'input.json');
  try {
    for (const raw of [
      'PRIVATE_RECORD_DO_NOT_PRINT',
      JSON.stringify({ ...fixture, records: [{ id: 'private', text: 'PRIVATE_RECORD_DO_NOT_PRINT' }] }),
      JSON.stringify({ ...fixture, records: [] }),
      JSON.stringify({ ...fixture, minConfidence: '0.75' }),
      JSON.stringify({ ...fixture, records: [{ id: 'large', text: 'x'.repeat(80_000) }] })
    ]) {
      await writeFile(file, raw);
      const result = spawnSync(process.execPath, ['src/cli.ts', 'organize', file, '--demo'], { encoding: 'utf8' });
      assert.equal(result.status, 1);
      assert.equal(result.stdout, '');
      assert.ok(!result.stderr.includes('PRIVATE_RECORD_DO_NOT_PRINT'));
    }
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('CLI validates arguments and missing credentials before any live request', () => {
  for (const flags of [[], ['--demo', '--demo'], ['--demo', '--out'], ['--live']]) {
    const result = spawnSync(process.execPath, ['src/cli.ts', 'organize', 'examples/organizer.json', ...flags], {
      encoding: 'utf8', env: { ...process.env, TYPESAFE_API_KEY: '' }
    });
    assert.equal(result.status, 1);
    assert.equal(result.stdout, '');
  }
});

test('CLI uses private output permissions and removes failed output with a mocked transport', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-'));
  const output = join(dir, 'output.json');
  // Replace fetch before importing the CLI: no network request can occur.
  const mock = 'data:text/javascript,' + encodeURIComponent(`
    globalThis.fetch = async () => {
      console.error('MOCK_FETCH_CALLED');
      throw new Error('PRIVATE_PROVIDER_ERROR');
    };
  `);
  const invoke = () => spawnSync(process.execPath, ['--import', mock, 'src/cli.ts', 'organize', 'examples/organizer.json', '--live', '--out', output], {
    encoding: 'utf8', env: { ...process.env, TYPESAFE_API_KEY: 'fixture' }
  });
  try {
    const failed = invoke();
    assert.equal(failed.status, 1);
    assert.ok(failed.stderr.includes('MOCK_FETCH_CALLED'));
    assert.ok(!failed.stderr.includes('PRIVATE_PROVIDER_ERROR'));
    await assert.rejects(stat(output), { code: 'ENOENT' });
    const demo = spawnSync(process.execPath, ['src/cli.ts', 'organize', 'examples/organizer.json', '--demo', '--out', output]);
    assert.equal(demo.status, 0);
    assert.equal((await stat(output)).mode & 0o777, 0o600);
    const before = await readFile(output, 'utf8');
    const existing = invoke();
    assert.equal(existing.status, 1);
    assert.ok(!existing.stderr.includes('MOCK_FETCH_CALLED'));
    assert.equal(await readFile(output, 'utf8'), before);
  } finally { await rm(dir, { recursive: true, force: true }); }
});
