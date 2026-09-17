import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';

// Regression test for "named-pipe input hangs before file-type rejection": the
// organizer CLI blocked in open() waiting for a writer, so it never reached its
// regular-file check. Every child here carries its own kill timer, so this file
// cannot hang the test runner even if the bug comes back.

const cli = new URL('../src/cli.ts', import.meta.url).pathname;
const posix = process.platform !== 'win32';

async function runCli(args: string[], timeoutMs = 2_000) {
  const child = spawn(process.execPath, [cli, ...args], { stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '';
  let stderr = '';
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  child.stdout.on('data', chunk => { stdout += chunk; });
  child.stderr.on('data', chunk => { stderr += chunk; });
  const started = process.hrtime.bigint();
  let killed = false;
  const timer = setTimeout(() => { killed = true; child.kill('SIGKILL'); }, timeoutMs);
  const code = await new Promise<number | null>(resolve => child.on('close', resolve));
  clearTimeout(timer);
  return { code, stdout, stderr, killed, elapsedMs: Number(process.hrtime.bigint() - started) / 1e6, timeoutMs };
}

test('a named pipe is rejected as a non-regular file instead of blocking', { skip: !posix }, async () => {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-fifo-'));
  try {
    const fifo = join(dir, 'records.json');
    await promisify(execFile)('mkfifo', [fifo]);
    // No writer will ever open this pipe. A blocking open waits forever.
    const result = await runCli(['organize', fifo, '--demo']);
    assert.equal(result.killed, false, `CLI had to be killed after ${result.timeoutMs} ms; it blocked on the pipe`);
    assert.ok(result.elapsedMs < result.timeoutMs, `took ${result.elapsedMs.toFixed(0)} ms`);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /Input must be a regular JSON file/);
    assert.equal(result.stdout, '', 'a rejected input must produce no report');
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('a directory is rejected as a non-regular file', { skip: !posix }, async () => {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-dir-'));
  try {
    const result = await runCli(['organize', dir, '--demo']);
    assert.equal(result.killed, false);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /Input must be a regular JSON file/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('a character device is rejected as a non-regular file', { skip: !posix }, async () => {
  const result = await runCli(['organize', '/dev/zero', '--demo']);
  assert.equal(result.killed, false, 'reading /dev/zero must not be attempted');
  assert.equal(result.code, 1);
  assert.match(result.stderr, /Input must be a regular JSON file/);
});

test('a missing path is refused without naming the path back to the caller', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-missing-'));
  try {
    const secret = join(dir, 'private-client-records.json');
    const result = await runCli(['organize', secret, '--demo']);
    assert.equal(result.killed, false);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /Cannot read input/);
    assert.ok(!result.stderr.includes('private-client-records'), 'the requested path must not be echoed');
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('a regular file is still read and the byte limit still applies', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-regular-'));
  try {
    const path = join(dir, 'records.json');
    await writeFile(path, await readFile(new URL('../examples/organizer.json', import.meta.url), 'utf8'));
    const accepted = await runCli(['organize', path, '--demo'], 10_000);
    assert.equal(accepted.code, 0, accepted.stderr);
    assert.match(accepted.stdout, /"groups"/);

    const oversized = join(dir, 'big.json');
    await writeFile(oversized, JSON.stringify({ padding: 'x'.repeat(90_000) }));
    const rejected = await runCli(['organize', oversized, '--demo'], 10_000);
    assert.equal(rejected.code, 1);
    assert.match(rejected.stderr, /exceeds 80 KB/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});
