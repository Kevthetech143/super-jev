import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const cli = new URL('../src/permit-cli.ts', import.meta.url).pathname;

async function runCli(args: string[], timeoutMs = 5_000) {
  const child = spawn(process.execPath, [cli, ...args], { stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '', stderr = '';
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  child.stdout.on('data', c => { stdout += c; });
  child.stderr.on('data', c => { stderr += c; });
  let killed = false;
  const timer = setTimeout(() => { killed = true; child.kill('SIGKILL'); }, timeoutMs);
  const code = await new Promise<number | null>(resolve => child.on('close', resolve));
  clearTimeout(timer);
  return { code, stdout, stderr, killed };
}

async function withSnapshot(body: unknown, fn: (dir: string, path: string) => Promise<void>) {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-permit-'));
  try {
    const path = join(dir, 'snapshot.json');
    await writeFile(path, JSON.stringify(body));
    await fn(dir, path);
  } finally { await rm(dir, { recursive: true, force: true }); }
}

test('no arguments prints usage and exits 0, same convention as sweep', async () => {
  const result = await runCli([]);
  assert.equal(result.code, 0);
  assert.match(result.stdout, /super-jev permit/);
});

test('--snapshot with no value is a usage error, exit 1', async () => {
  const result = await runCli(['--snapshot']);
  assert.equal(result.code, 1);
});

test('--help exits 0', async () => {
  const result = await runCli(['--help']);
  assert.equal(result.code, 0);
  assert.match(result.stdout, /super-jev permit/);
});

test('a missing snapshot file is a usage error, exit 1', async () => {
  const result = await runCli(['--snapshot', '/no/such/file.json', '--dry-run']);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /Cannot read the snapshot file/);
});

test('a snapshot with no action and no --action is a usage error, exit 1', async () => {
  await withSnapshot({ target: 'x' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--dry-run']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /No action/);
  });
});

test('--dry-run reaches no network and exits 0, printing the request it would send', async () => {
  await withSnapshot({ action: 'refresh the cache', target: 'cache/main' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--dry-run', '--json']);
    assert.equal(result.code, 0);
    assert.equal(result.killed, false);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.mode, 'dry-run');
    assert.equal(parsed.request.state.action, 'refresh the cache');
    assert.match(result.stderr, /nothing was sent, no provider was called/);
  });
});

test('--action overrides a snapshot action', async () => {
  await withSnapshot({ action: 'snapshot action' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--action', 'overridden action', '--dry-run', '--json']);
    assert.equal(result.code, 0);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.request.state.action, 'overridden action');
  });
});

test('--stub with a non-irreversible action returns safe_to_auto, exit 0, valid json shape', async () => {
  await withSnapshot({ action: 'refresh the local cache' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json']);
    assert.equal(result.code, 0);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'safe_to_auto');
    assert.equal(parsed.hardRuleApplied, false);
    assert.ok(typeof parsed.confidence === 'number');
    assert.ok(typeof parsed.reason === 'string');
    assert.equal(parsed.id, 'action');
  });
});

test('--stub with an irreversible action (hard rule) is downgraded to needs_approval, exit 2', async () => {
  await withSnapshot({ action: 'delete the production record' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json']);
    assert.equal(result.code, 2);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'needs_approval');
    assert.equal(parsed.hardRuleApplied, true);
    assert.deepEqual(parsed.matchedKeywords, ['delete']);
  });
});

test('--stub with an irreversible action in the target field is also downgraded', async () => {
  await withSnapshot({ action: 'run cleanup', target: 'wire the funds' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json']);
    assert.equal(result.code, 2);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'needs_approval');
    assert.deepEqual(parsed.matchedKeywords, ['wire']);
  });
});

test('--min-confidence set above the fixed stub confidence forces needs_approval', async () => {
  await withSnapshot({ action: 'refresh the local cache' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json', '--min-confidence', '0.99']);
    assert.equal(result.code, 2);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'needs_approval');
  });
});

test('live mode without TYPESAFE_API_KEY, --dry-run or --stub is a usage error, exit 1', async () => {
  await withSnapshot({ action: 'refresh the local cache' }, async (_dir, path) => {
    const child = spawn(process.execPath, [cli, '--snapshot', path], {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, TYPESAFE_API_KEY: '' }
    });
    let stderr = '';
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', c => { stderr += c; });
    const code = await new Promise<number | null>(resolve => child.on('close', resolve));
    assert.equal(code, 1);
    assert.match(stderr, /Set TYPESAFE_API_KEY/);
  });
});
