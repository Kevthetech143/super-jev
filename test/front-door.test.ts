// connect + ask wrapper (additions only): real engine, temp state folder, no network.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CLI = join(ROOT, 'src', 'jev-chat-cli.ts');
const tmp = mkdtempSync(join(tmpdir(), 'sj-door-'));
const folder = join(tmp, 'Photos');
mkdirSync(folder);
writeFileSync(join(folder, 'refunds.md'), '# Refunds\nThe refund policy allows returns within 30 days.\n');
writeFileSync(join(folder, 'canary.md'), '# Canary\nThe canary holds at 5 percent of traffic for 40 minutes.\n');
writeFileSync(join(folder, 'lunch.md'), '# Lunch\nThe team lunch is on Fridays.\n');
const env = { ...process.env, SUPERJEV_STATE_DIR: join(tmp, 'state'), USER: 'sam' };
const sj = (...a: string[]) => spawnSync(process.execPath, [CLI, ...a], { encoding: 'utf8', env });

test('FD1 connect registers a pointer for a folder of 3 .md files and prints the success line', () => {
  const r = sj('connect', folder);
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.equal(r.stdout.trim(), 'Connected 3 files from Photos. Ask me anything.');
});

test('FD2 ask returns a ranked result against that pointer', () => {
  const r = sj('ask', 'what is the refund policy');
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /^Top match: .*\.md/);
});

test('FD3 connect on a missing folder fails plainly', () => {
  const r = sj('connect', join(tmp, 'nope'));
  assert.equal(r.status, 2);
  assert.match(r.stderr, /Usage: superjev connect/);
});
