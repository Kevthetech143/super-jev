import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

const cli = new URL('../src/navigation-cli.ts', import.meta.url).pathname;

function runCli(input: string, env: Record<string, string | undefined> = {}) {
  const merged: Record<string, string | undefined> = { ...process.env };
  delete merged.TYPESAFE_API_KEY;
  Object.assign(merged, env);
  const result = spawnSync(process.execPath, [cli], { encoding: 'utf8', env: merged, input });
  return { code: result.status, stdout: result.stdout ?? '', stderr: result.stderr ?? '' };
}

const CATALOG = { version: 1, structure: 'flat-files', rootId: 'root', nodes: [{ id: 'root', label: 'root', description: 'root' }] };

// SUPERJEV_NAV_TIMEOUT_MS is read before the transport is built (no network here:
// Jev() throws synchronously without TYPESAFE_API_KEY, so these only exercise the
// env-parsing path added for the concurrency/timeout fix).

test('bad SUPERJEV_NAV_TIMEOUT_MS value does not crash the CLI (falls back to default)', () => {
  const result = runCli(JSON.stringify({ question: 'q', catalog: CATALOG }), { SUPERJEV_NAV_TIMEOUT_MS: 'not-a-number' });
  assert.equal(result.code, 2);
  assert.match(result.stderr, /TYPESAFE_API_KEY/);
});

test('a valid SUPERJEV_NAV_TIMEOUT_MS is accepted and still requires the transport key', () => {
  const result = runCli(JSON.stringify({ question: 'q', catalog: CATALOG }), { SUPERJEV_NAV_TIMEOUT_MS: '20000' });
  assert.equal(result.code, 2);
  assert.match(result.stderr, /TYPESAFE_API_KEY/);
});

test('invalid request shape still rejected regardless of SUPERJEV_NAV_TIMEOUT_MS', () => {
  const result = runCli(JSON.stringify({ question: 'q', catalog: CATALOG, bogus: 1 }), { SUPERJEV_NAV_TIMEOUT_MS: '30000' });
  assert.equal(result.code, 2);
  assert.match(result.stderr, /Navigation input is invalid/);
});
