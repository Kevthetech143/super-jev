import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const cli = new URL('../src/experimental/chain-cli.ts', import.meta.url).pathname;

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

async function withSpec(body: unknown, fn: (dir: string, path: string) => Promise<void>) {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-chain-'));
  try {
    const path = join(dir, 'spec.json');
    await writeFile(path, JSON.stringify(body));
    await fn(dir, path);
  } finally { await rm(dir, { recursive: true, force: true }); }
}

const options = { eligible: 'Return allowed', ineligible: 'Return disallowed', unknown: 'Insufficient evidence' };

const roles = [
  { name: 'ticket', match: { type: 'self' } },
  { name: 'order', match: { type: 'idPrefix', prefix: 'O' } },
  { name: 'policy', match: { type: 'idPattern', pattern: '^P' } }
];

const completeDocs = [
  { id: 'T1', text: 'Customer ticket T1', links: ['O100', 'P1'] },
  { id: 'O100', text: 'Purchase age: 10 days' },
  { id: 'P1', text: 'Returns within 30 days are eligible' }
];

const incompleteDocs = [
  { id: 'T1', text: 'Customer ticket T1', links: ['O100'] },
  { id: 'O100', text: 'Purchase age: 10 days' }
];

test('no arguments prints usage and exits 0, same convention as sweep', async () => {
  const result = await runCli([]);
  assert.equal(result.code, 0);
  assert.match(result.stdout, /super-jev chain/);
});

test('--spec with no value is a usage error, exit 1', async () => {
  const result = await runCli(['--spec']);
  assert.equal(result.code, 1);
});

test('--help exits 0', async () => {
  const result = await runCli(['--help']);
  assert.equal(result.code, 0);
  assert.match(result.stdout, /super-jev chain/);
});

test('a missing spec file is a usage error, exit 1', async () => {
  const result = await runCli(['--spec', '/no/such/spec.json', '--dry-run']);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /Cannot read the spec file/);
});

test('an unknown role match type is a usage error, exit 1', async () => {
  await withSpec({ question: 'is it eligible?', rootId: 'T1', options, roles: [{ name: 'ticket', match: { type: 'nonsense' } }], docs: completeDocs }, async (_dir, path) => {
    const result = await runCli(['--spec', path, '--dry-run']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /unknown match type/);
  });
});

test('--dry-run blocks a case missing a required role, exit 2, zero network, and names the missing role', async () => {
  await withSpec({ question: 'is it eligible?', rootId: 'T1', options, roles, docs: incompleteDocs }, async (_dir, path) => {
    const result = await runCli(['--spec', path, '--dry-run', '--json']);
    assert.equal(result.code, 2);
    assert.equal(result.killed, false);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.mode, 'dry-run');
    assert.equal(parsed.blocked.length, 1);
    assert.equal(parsed.blocked[0].id, 'case');
    assert.match(parsed.blocked[0].problems.join(' '), /required role "policy" is not filled/);
    assert.deepEqual(parsed.askable, []);
    assert.match(result.stderr, /nothing was sent, no provider was called/);
  });
});

test('--dry-run passes a complete case through as askable, exit 0, zero network', async () => {
  await withSpec({ question: 'is it eligible?', rootId: 'T1', options, roles, docs: completeDocs }, async (_dir, path) => {
    const result = await runCli(['--spec', path, '--dry-run', '--json']);
    assert.equal(result.code, 0);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.blocked.length, 0);
    assert.deepEqual(parsed.askable, ['case']);
    assert.equal(parsed.calls, 1);
  });
});

test('--stub on a complete case resolves to accepted with per-role refs, exit 0', async () => {
  await withSpec({ question: 'is it eligible?', rootId: 'T1', options, roles, docs: completeDocs }, async (_dir, path) => {
    const result = await runCli(['--spec', path, '--stub', '--json']);
    assert.equal(result.code, 0);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.blocked, 0);
    assert.equal(parsed.results.length, 1);
    const r = parsed.results[0];
    assert.equal(r.blocked, false);
    assert.equal(r.outcomeKind, 'accepted');
    assert.equal(r.value, 'eligible', 'the fixed stub always answers the first listed option');
    const roleNames = r.roles.map((x: { role: string }) => x.role);
    assert.deepEqual(roleNames, ['ticket', 'order', 'policy']);
    assert.deepEqual(r.roles.find((x: { role: string }) => x.role === 'ticket').docIds, ['T1']);
    assert.deepEqual(r.roles.find((x: { role: string }) => x.role === 'order').docIds, ['O100']);
    assert.deepEqual(r.roles.find((x: { role: string }) => x.role === 'policy').docIds, ['P1']);
  });
});

test('--stub on an incomplete case still blocks, and never sends a question for it, exit 2', async () => {
  await withSpec({ question: 'is it eligible?', rootId: 'T1', options, roles, docs: incompleteDocs }, async (_dir, path) => {
    const result = await runCli(['--spec', path, '--stub', '--json']);
    assert.equal(result.code, 2);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.blocked, 1);
    const r = parsed.results[0];
    assert.equal(r.blocked, true);
    assert.equal(r.outcomeKind, 'insufficient_evidence');
    assert.match(r.reason, /required evidence incomplete/);
  });
});

test('multiple cases over the same pool: one blocked, one complete, mixed exit 2', async () => {
  const docs = [
    ...completeDocs,
    { id: 'T2', text: 'Customer ticket T2', links: ['O200'] },
    { id: 'O200', text: 'Purchase age: 5 days' }
  ];
  await withSpec({ options, roles, docs, cases: [{ id: 'T1', question: 'is it eligible?', rootId: 'T1' }, { id: 'T2', question: 'is it eligible?', rootId: 'T2' }] }, async (_dir, path) => {
    const result = await runCli(['--spec', path, '--stub', '--json']);
    assert.equal(result.code, 2);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.blocked, 1);
    const byId = Object.fromEntries(parsed.results.map((r: { id: string }) => [r.id, r]));
    assert.equal(byId.T1.blocked, false);
    assert.equal(byId.T1.outcomeKind, 'accepted');
    assert.equal(byId.T2.blocked, true);
    assert.match(byId.T2.reason, /required evidence incomplete/);
  });
});

test('live mode without TYPESAFE_API_KEY, --dry-run or --stub is a usage error, exit 1', async () => {
  await withSpec({ question: 'is it eligible?', rootId: 'T1', options, roles, docs: completeDocs }, async (_dir, path) => {
    const child = spawn(process.execPath, [cli, '--spec', path], {
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
