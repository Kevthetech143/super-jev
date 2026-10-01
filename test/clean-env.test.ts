// The suite gives the same result whatever the caller's shell exports.
// test/clean-env.ts is preloaded by `npm test` (node --import ./test/clean-env.ts --test ...). It clears,
// before any test loads, the product's own settings: every SUPERJEV_* except SUPERJEV_TEST_*, every judge
// profile's key and url variable (read from judge_profiles.json), and SWEEP_BATCH.
// Python twin: skills/super-jev/tests/test_clean_env.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const PROFILES = new URL('../skills/super-jev/judge_profiles.json', import.meta.url);
const PRELOAD = fileURLToPath(new URL('./clean-env.ts', import.meta.url));

const judgeVariables = (): string[] => {
  const names = new Set<string>();
  for (const profile of Object.values(JSON.parse(readFileSync(PROFILES, 'utf8')).profiles) as Array<Record<string, string>>) {
    for (const name of [profile.key_env, profile.api_url_env]) if (name) names.add(name);
  }
  return [...names];
};

test('a test run starts from a clean environment', () => {
  const stray = Object.keys(process.env).filter((k) => k.startsWith('SUPERJEV_') && !k.startsWith('SUPERJEV_TEST_'));
  assert.deepEqual(stray, [], `the caller's settings reached the tests: ${stray}`);
  const leaked = judgeVariables().filter((k) => k in process.env);
  assert.deepEqual(leaked, [], `a judge key or url reached the tests: ${leaked}`);
  assert.equal(process.env.SWEEP_BATCH, undefined);
});

test('a polluted shell is scrubbed by the preload, and test knobs are left alone', () => {
  const polluted: NodeJS.ProcessEnv = { ...process.env };
  for (const name of judgeVariables()) polluted[name] = 'not-a-real-value';
  Object.assign(polluted, {
    SUPERJEV_JUDGE: 'typesafe',
    SUPERJEV_STATE_DIR: '/nonexistent/not-a-real-state-dir',
    SUPERJEV_WRITER_COMMAND: 'false',
    SUPERJEV_AUTO_CACHE: '0',
    SWEEP_BATCH: '7',
    SUPERJEV_TEST_KEEP: 'kept',
  });
  const r = spawnSync(process.execPath, ['--import', PRELOAD, '-e', 'console.log(JSON.stringify(process.env))'],
    { encoding: 'utf8', env: polluted });
  assert.equal(r.status, 0, r.stderr);
  const seen = JSON.parse(r.stdout) as Record<string, string>;
  const left = Object.keys(seen).filter((k) => k.startsWith('SUPERJEV_') && !k.startsWith('SUPERJEV_TEST_'));
  assert.deepEqual(left, []);
  assert.deepEqual(judgeVariables().filter((k) => k in seen), []);
  assert.equal(seen.SWEEP_BATCH, undefined);
  assert.equal(seen.SUPERJEV_TEST_KEEP, 'kept');
  assert.equal(seen.PATH, process.env.PATH);
});
