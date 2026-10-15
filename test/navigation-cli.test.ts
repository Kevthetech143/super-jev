import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadJudgeProfile } from '../src/judge-profile.ts';
import { TABLE_HOOK, largeTable } from './large-table.ts';

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

// ---- context: which preset (small, medium or large) a run reads at travels in the input as a `context` key; absent means small.
// The preset gives the judge profile (so the long-context header rule), the batcher's call budget and the check's timeout.

// The shipped table offers Small and Medium only (Large waits for its own test), so a run at a size this big reads a made-up table: the
// shipped one plus a `large` row (test/large-table.ts). The CLI is a child process, so TABLE_HOOK, preloaded, hands it the table.
const TABLE = largeTable();
const TABLE_ENV = { NODE_OPTIONS: `--require=${TABLE_HOOK}`, SJ_TABLE: TABLE };
const at = (context: string) => loadJudgeProfile('typesafe-jev', TABLE, {}, context);
const [HEADER_NAME, HEADER_VALUE] = at('large').longContextHeader!;
const FILLER = 'The cuff watch note says the wrist model is validated. ';
/** A flat catalog of `n` passages of `chars` characters each, plain prose. */
function passages(n: number, chars: number) {
  const ids = Array.from({ length: n }, (_, i) => `p${i}`);
  return { version: 1, structure: 'flat-files', rootId: 'root', nodes: [
    { id: 'root', label: 'Sources', description: '', children: ids },
    ...ids.map((id, i) => ({ id, label: `Passage ${i + 1}`, description: `Note ${i}. ${FILLER.repeat(Math.ceil(chars / FILLER.length))}`.slice(0, chars), sourceId: id }))] };
}
const evidence = (catalog: unknown, question = 'fact?') => ({ question, catalog, mode: 'source-evidence' });

/** A --require hook, no network: fetch answers every passage question "states the answer" and, when the process exits, writes to
 * the file SJ_SEEN what it saw: for each request whether it carried the long-context header (SJ_HEADER_NAME, SJ_HEADER_VALUE) and
 * how many questions it had, and the delay of every timer set (the check's timeout is one of them). */
const OFFLINE_HOOK = `
const fs = require('node:fs');
const seen = { requests: [], timers: [] };
const setTimer = global.setTimeout;
global.setTimeout = (fn, ms, ...rest) => { seen.timers.push(ms); return setTimer(fn, ms, ...rest); };
process.on('exit', () => fs.writeFileSync(process.env.SJ_SEEN, JSON.stringify(seen)));
global.fetch = async (_url, opts) => {
  const payload = JSON.parse(opts.body);
  seen.requests.push({ long: opts.headers[process.env.SJ_HEADER_NAME] === process.env.SJ_HEADER_VALUE, questions: Object.keys(payload.questions).length });
  const answers = Object.fromEntries(Object.entries(payload.questions).map(([id, q]) => {
    const keys = Object.keys(q.criteria);
    const choice = keys.find(k => k.endsWith('o_0')) || keys[0];
    return [id, { type: 'choice', choice, confidence: 1, probabilities: Object.fromEntries(keys.map(k => [k, k === choice ? 1 : 0])) }];
  }));
  return new Response(JSON.stringify({ model: 'offline', answers }), { status: 200 });
};`;
type Seen = { requests: { long: boolean; questions: number }[]; timers: number[] };
function runOffline(input: unknown, env: Record<string, string> = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'sj-context-test-'));
  try {
    const hook = join(dir, 'offline.cjs');
    const seenFile = join(dir, 'seen.json');
    writeFileSync(hook, OFFLINE_HOOK);
    const result = runCli(JSON.stringify(input), { TYPESAFE_API_KEY: 'x', NODE_OPTIONS: `--require=${TABLE_HOOK} --require=${hook}`, SJ_TABLE: TABLE, SJ_SEEN: seenFile, SJ_HEADER_NAME: HEADER_NAME, SJ_HEADER_VALUE: HEADER_VALUE, ...env });
    const seen: Seen = existsSync(seenFile) ? JSON.parse(readFileSync(seenFile, 'utf8')) : { requests: [], timers: [] };
    return { ...result, seen };
  } finally { rmSync(dir, { recursive: true, force: true }); }
}

test('context large sets the default timeout to its preset (30000) unless SUPERJEV_NAV_TIMEOUT_MS overrides; medium its own, none small\'s', () => {
  const PRESET_TIMEOUTS = [15_000, 20_000, 30_000, 45_000];
  const timeout = (context: string | undefined, want: number, env: Record<string, string> = {}) => {
    const r = runOffline({ ...evidence(passages(1, 200)), ...(context === undefined ? {} : { context }) }, env);
    assert.equal(r.code, 0, r.stderr);
    assert.ok(r.seen.timers.includes(want), `${context ?? 'no context'} ${JSON.stringify(env)}: wanted a ${want} ms timer, saw ${JSON.stringify(r.seen.timers)}`);
    for (const other of PRESET_TIMEOUTS.filter(t => t !== want)) assert.ok(!r.seen.timers.includes(other), `${context ?? 'no context'}: saw a ${other} ms timer`);
  };
  assert.deepEqual([at('small').navTimeoutMs, at('medium').navTimeoutMs, at('large').navTimeoutMs], [15_000, 20_000, 30_000]);
  timeout(undefined, 15_000);                                              // no context: as before
  timeout('medium', 20_000);
  timeout('large', 30_000);
  timeout('large', 45_000, { SUPERJEV_NAV_TIMEOUT_MS: '45000' });          // the env wins
  timeout('large', 30_000, { SUPERJEV_NAV_TIMEOUT_MS: 'not-a-number' });   // a bad env falls back to the preset's own, not to small's
});

test('an unknown context is a one-line error, before any judge is built', () => {
  const one = { question: 'q', catalog: CATALOG };
  for (const [input, name] of [[{ ...one, context: 'xl' }, 'xl'], [{ batch: [one], context: 'xl' }, 'xl'], [{ batch: [{ ...one, context: 'Large' }] }, 'Large']] as const) {
    const r = runCli(JSON.stringify(input), TABLE_ENV);                    // no key: the context is read first
    assert.equal(r.code, 2);
    assert.equal(r.stdout, '');
    assert.equal(r.stderr, `unknown context ${JSON.stringify(name)} (choose small, medium or large)\n`);
  }
  // a context is a name: anything else is a malformed input
  for (const bad of [5, null, ['large']]) {
    const r = runCli(JSON.stringify({ ...one, context: bad }));
    assert.equal(r.code, 2);
    assert.equal(r.stderr, 'Navigation input is invalid\n');
  }
});

test('one preset per run: named at the top of the input or on its batch items, and all alike', () => {
  const item = evidence(passages(1, 200));
  const timers = (input: unknown) => {
    const r = runOffline(input);
    assert.equal(r.code, 0, r.stderr);
    return r.seen.timers;
  };
  assert.ok(timers({ batch: [{ ...item, context: 'large' }, item] }).includes(30_000), 'named on one item: the whole run reads at it');
  assert.ok(timers({ batch: [{ ...item, context: 'medium' }], context: 'medium' }).includes(20_000), 'named at the top and on the item, alike');
  for (const input of [{ batch: [{ ...item, context: 'large' }, { ...item, context: 'medium' }] }, { batch: [{ ...item, context: 'medium' }], context: 'large' }]) {
    const r = runOffline(input);
    assert.equal(r.code, 2);
    assert.equal(r.stderr, 'Navigation input is invalid\n');
    assert.equal(r.seen.requests.length, 0);
  }
});

test('the batch output reports how many calls carried the long-context header', () => {
  // one note read at length (over small's call budget) and one short one: different states, so two calls
  const batch = [evidence(passages(6, 11_000), 'fact one?'), evidence(passages(1, 200), 'fact two?')];
  const large = runOffline({ batch, context: 'large' });
  assert.equal(large.code, 0, large.stderr);
  const out = JSON.parse(large.stdout);
  assert.deepEqual(out.results.map((r: { status: string }) => r.status), ['candidates', 'candidates']);
  assert.equal(out.calls, 2);
  assert.equal(out.longCalls, 1);
  assert.deepEqual(large.seen.requests.map(r => r.long).sort(), [false, true]);
  // the same two requests at small: it never asks for the header, and says so
  const small = runOffline({ batch });
  assert.equal(small.code, 0, small.stderr);
  const plain = JSON.parse(small.stdout);
  assert.deepEqual([plain.calls, plain.longCalls], [2, 0]);
  assert.ok(small.seen.requests.every(r => !r.long));
  // a single navigation reports it too
  const single = runOffline({ ...batch[0], context: 'large' });
  assert.equal(single.code, 0, single.stderr);
  assert.equal(JSON.parse(single.stdout).longCalls, 1);
});

test('the batcher budget follows the preset: questions small keeps in separate calls share one at large', () => {
  // the same note twice: one state, so their questions share a call while the whole stays under the preset's call budget
  const item = evidence(passages(5, 11_000));
  const small = runOffline({ batch: [item, item] });
  const large = runOffline({ batch: [item, item], context: 'large' });
  for (const r of [small, large]) assert.equal(r.code, 0, r.stderr);
  assert.equal(JSON.parse(small.stdout).calls, 2);
  assert.deepEqual(small.seen.requests.map(r => r.questions), [5, 5]);
  assert.equal(JSON.parse(large.stdout).calls, 1);
  assert.deepEqual(large.seen.requests.map(r => r.questions), [10]);
});
