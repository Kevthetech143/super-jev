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

test('source-evidence mode reaches the evaluator in single and batched CLI requests', async () => {
  const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const dir = mkdtempSync(join(tmpdir(), 'sj-mode-test-'));
  const hook = join(dir, 'offline.cjs');
  // A local fetch stub: this test cannot reach the network, even on failure.
  writeFileSync(hook, `global.fetch = async (_url, opts) => {
    const payload = JSON.parse(opts.body);
    const answers = Object.fromEntries(Object.entries(payload.questions).map(([id, q]) => {
      const keys = Object.keys(q.criteria);
      const evidence = q.instructions.includes('exact answer') || q.instructions.includes('ANY requested fact');
      const choice = evidence ? keys.find(k => k.endsWith('o_none')) : keys.find(k => k.endsWith('o_0'));
      if (!choice) throw new Error('unexpected request');
      return [id, {type:'choice', choice, confidence:1,
        probabilities:Object.fromEntries(keys.map(k => [k, k === choice ? 1 : 0]))}];
    }));
    return new Response(JSON.stringify({model:'offline', answers}), {status:200});
  };`);
  const catalog = { version: 1, structure: 'flat-files', rootId: 'root', nodes: [
    { id: 'root', label: 'Sources', description: '', children: ['p'] },
    { id: 'p', label: 'Passage', description: 'Some context.', sourceId: 'p' }
  ] };
  const env = { TYPESAFE_API_KEY: 'x', NODE_OPTIONS: `--require=${hook}` };
  try {
    const single = runCli(JSON.stringify({ question: 'fact?', catalog, mode: 'source-evidence' }), env);
    assert.equal(single.code, 0, single.stderr);
    assert.ok(JSON.parse(single.stdout).candidates[0].score < 1e-9);
    const batch = runCli(JSON.stringify({ batch: [
      { question: 'fact?', catalog, mode: 'source-evidence' },
      { question: 'fact?', catalog },
      { question: 'fact?', catalog, mode: 'source-discovery' }
    ] }), env);
    assert.equal(batch.code, 0, batch.stderr);
    const rows = JSON.parse(batch.stdout).results;
    assert.ok(rows[0].candidates[0].score < 1e-9);
    assert.equal(rows[1].candidates[0].score, 1);
    assert.equal(rows[2].status, "no-candidates");
    const bad = runCli(JSON.stringify({ question: 'fact?', catalog, mode: 'arbitrary prompt' }), env);
    assert.equal(bad.code, 2);
    assert.match(bad.stderr, /mode is invalid/);
  } finally { rmSync(dir, {recursive:true, force:true}); }
});
