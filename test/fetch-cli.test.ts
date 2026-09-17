import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const cli = new URL('../src/fetch-cli.ts', import.meta.url).pathname;

function runCli(args: string[], env: Record<string, string | undefined> = {}) {
  const merged: Record<string, string | undefined> = { ...process.env };
  delete merged.TYPESAFE_API_KEY;
  Object.assign(merged, env);
  const result = spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8', env: merged });
  return { code: result.status, stdout: result.stdout ?? '', stderr: result.stderr ?? '' };
}

const CATALOG = JSON.stringify([
  { id: 'gate', text: 'Check a draft reply against its evidence before it is sent.' },
  { id: 'pay-coned', text: 'Pay a Con Edison electric bill via the guest checkout flow.' },
  { id: 'tweet', text: 'Post to X/Twitter on the fleet account.' }
]);

async function withTmp(fn: (dir: string) => Promise<void>) {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-fetch-cli-'));
  try { await fn(dir); } finally { await rm(dir, { recursive: true, force: true }); }
}

// ---------------------------------------------------------------- dry-run

test('--dry-run prints the plan and reaches zero network with no API key set', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--dry-run', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.mode, 'dry-run');
    assert.equal(parsed.catalogSize, 3);
    assert.equal(parsed.calls, 1);
  });
});

test('--json prints exactly one parseable JSON object and nothing else on stdout', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--json']);
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.stdout.trim().split('\n').length, 1);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.mode, 'stub');
    assert.ok(Array.isArray(parsed.ranked));
    assert.equal(parsed.ranked.length, 3);
    for (const entry of parsed.ranked) {
      assert.equal(typeof entry.id, 'string');
      assert.equal(typeof entry.score, 'number');
      assert.equal(typeof entry.confidence, 'number');
    }
  });
});

// ---------------------------------------------------------------- rerun to same --out

test('a second run to the same --out overwrites instead of throwing', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const outDir = join(dir, 'out');
    const first = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--out', outDir, '--json']);
    assert.equal(first.code, 0, first.stderr);
    const second = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--out', outDir, '--json']);
    assert.equal(second.code, 0, second.stderr);
    assert.equal(first.stdout, second.stdout);
    const ranked = JSON.parse(await readFile(join(outDir, 'ranked.json'), 'utf8'));
    assert.ok(Array.isArray(ranked.ranked));
  });
});

// ---------------------------------------------------------------- k cap

test('--k caps the number of ids returned', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--k', '1', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.ranked.length, 1);
  });
});

// ---------------------------------------------------------------- exit codes

test('missing --catalog is a usage error, exit 1', () => {
  const result = runCli(['--request', 'anything', '--dry-run']);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /usage|--catalog/i);
});

test('missing --request is a usage error, exit 1', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--dry-run']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /--request/);
  });
});

test('a malformed catalog file is a usage error, exit 1', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, 'not json', 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'anything', '--dry-run']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /not valid JSON/);
  });
});

test('live mode without TYPESAFE_API_KEY refuses instead of calling the network', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'anything']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /TYPESAFE_API_KEY/);
  });
});

test('--help prints usage and exits 0', () => {
  const result = runCli(['--help']);
  assert.equal(result.code, 0);
  assert.match(result.stdout, /--catalog/);
  assert.match(result.stdout, /--request/);
});
