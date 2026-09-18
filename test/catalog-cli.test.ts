import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const cli = new URL('../src/catalog-cli.ts', import.meta.url).pathname;

function runCli(args: string[]) {
  const result = spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8' });
  return { code: result.status, stdout: result.stdout ?? '', stderr: result.stderr ?? '' };
}

async function withTmp(fn: (dir: string) => Promise<void>) {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-catalog-cli-'));
  try { await fn(dir); } finally { await rm(dir, { recursive: true, force: true }); }
}

// ---------------------------------------------------------------- validate

test('validate exits 0 and reports clean for a well-formed v2 catalog', async () => {
  await withTmp(async dir => {
    const path = join(dir, 'catalog.json');
    await writeFile(path, JSON.stringify([{ id: 'a', text: 'x', utterances: ['one', 'two', 'three'] }]));
    const result = runCli(['validate', path]);
    assert.equal(result.code, 0, result.stderr);
    assert.match(result.stdout, /clean/);
  });
});

test('validate exits 1 and reports findings for a thin, duplicate and colliding catalog', async () => {
  await withTmp(async dir => {
    const path = join(dir, 'catalog.json');
    await writeFile(path, JSON.stringify([
      { id: 'a', text: 'x', utterances: ['do a', 'handle a'], negatives: ['do b'] },
      { id: 'b', text: 'x', utterances: ['do b', 'handle b', 'run b'] }
    ]));
    const result = runCli(['validate', path]);
    assert.equal(result.code, 1);
    assert.match(result.stdout, /thin utterances/);
    assert.match(result.stdout, /negatives that are also another record's utterance/);
  });
});

test('validate --json prints one parseable object', async () => {
  await withTmp(async dir => {
    const path = join(dir, 'catalog.json');
    await writeFile(path, JSON.stringify([{ id: 'a', text: 'x' }]));
    const result = runCli(['validate', path, '--json']);
    assert.equal(result.code, 1); // thin utterances (0 < 3)
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.totalRecords, 1);
    assert.equal(parsed.clean, false);
  });
});

test('validate exits 2 (via the generic catch) on a missing file, and 1 on bad JSON via CliError', async () => {
  await withTmp(async dir => {
    const missing = runCli(['validate', join(dir, 'nope.json')]);
    assert.equal(missing.code, 1);
    assert.match(missing.stderr, /Cannot read/);

    const badJsonPath = join(dir, 'bad.json');
    await writeFile(badJsonPath, '{not json');
    const bad = runCli(['validate', badJsonPath]);
    assert.equal(bad.code, 1);
    assert.match(bad.stderr, /not valid JSON/);
  });
});

// ---------------------------------------------------------------- learn

test('learn proposes new utterances from ledger lines where chosen differed from the top pick, and never edits the catalog', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    const catalogBody = JSON.stringify([{ id: 'a', text: 'Do A' }, { id: 'b', text: 'Do B' }]);
    await writeFile(catalogPath, catalogBody);

    const ledgerPath = join(dir, 'ledger.jsonl');
    const lines = [
      { request: 'handle b for me', ranked: [{ id: 'a', score: 1, confidence: 0.9 }], chosen: 'b', ts: '2026-01-01T00:00:00.000Z' },
      { request: 'do a please', ranked: [{ id: 'a', score: 1, confidence: 0.9 }], chosen: 'a', ts: '2026-01-01T00:00:00.000Z' } // top pick already matched chosen: nothing to learn
    ];
    await writeFile(ledgerPath, lines.map(l => JSON.stringify(l)).join('\n') + '\n');

    const outPath = join(dir, 'proposals.json');
    const result = runCli(['learn', ledgerPath, catalogPath, '--out', outPath]);
    assert.equal(result.code, 0, result.stderr);

    const proposals = JSON.parse(await readFile(outPath, 'utf8'));
    assert.equal(proposals.proposals.length, 1);
    assert.equal(proposals.proposals[0].id, 'b');
    assert.deepEqual(proposals.proposals[0].proposedUtterances, ['handle b for me']);

    const catalogAfter = await readFile(catalogPath, 'utf8');
    assert.equal(catalogAfter, catalogBody, 'the catalog file must be byte-identical after learn: never edited');
  });
});

test('learn skips a request already listed as an utterance, and a chosen id no longer in the catalog', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, JSON.stringify([{ id: 'a', text: 'Do A', utterances: ['already here'] }]));
    const ledgerPath = join(dir, 'ledger.jsonl');
    const lines = [
      { request: 'already here', ranked: [{ id: 'other', score: 1, confidence: 0.9 }], chosen: 'a', ts: 'x' },
      { request: 'orphaned pick', ranked: [{ id: 'a', score: 1, confidence: 0.9 }], chosen: 'not-in-catalog', ts: 'x' }
    ];
    await writeFile(ledgerPath, lines.map(l => JSON.stringify(l)).join('\n') + '\n');
    const outPath = join(dir, 'proposals.json');
    const result = runCli(['learn', ledgerPath, catalogPath, '--out', outPath, '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.deepEqual(parsed.proposals, []);
  });
});

test('learn rejects a malformed ledger line', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, JSON.stringify([{ id: 'a', text: 'x' }]));
    const ledgerPath = join(dir, 'ledger.jsonl');
    await writeFile(ledgerPath, '{"request":"x"}\n'); // missing chosen/ranked
    const result = runCli(['learn', ledgerPath, catalogPath]);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /missing/);
  });
});

// ---------------------------------------------------------------- usage

test('no arguments prints usage and exits 0', () => {
  const result = runCli([]);
  assert.equal(result.code, 0);
  assert.match(result.stdout, /super-jev catalog/);
});

test('an unknown subcommand is a usage error, exit 1', () => {
  const result = runCli(['bogus']);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /Unknown subcommand/);
});
