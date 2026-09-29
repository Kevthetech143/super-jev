// Frozen tests for the one judge doorway (task judge-door). Python twin: skills/super-jev/tests/test_judge_door.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { Jev } from '../src/jev.ts';
import { getJudge, keyEnv, keyPresent, requireKey, retryOverloaded } from '../src/judge.ts';
import { JUDGE_PROFILE, loadJudgeProfile } from '../src/judge-profile.ts';
import { BatchingEvaluator } from '../src/enhance/coalesce.ts';
import { providerFailureReason } from '../src/enhance/navigation.ts';
import { JudgeError, NoKey, AuthRejected, Unreachable, Overloaded, BadReply, TooBig, SecretBlocked } from '../src/judge-errors.ts';
import type { Request } from '../src/types.ts';

const golden = JSON.parse(readFileSync(new URL('./fixtures/judge-request-golden.json', import.meta.url), 'utf8'))[0];
const PROFILES = new URL('../skills/super-jev/judge_profiles.json', import.meta.url);
const request: Request = { state: 'EVIDENCE:\nplain state', questions: {
  ok: { type: 'noul', instructions: 'Is it true?' },
  pick: { type: 'choice', instructions: 'Pick.', criteria: { A: 'a', B: 'b' } } } };
const signal = () => new AbortController().signal;

test('(a) with SUPERJEV_JUDGE unset the request is byte-identical to the one sent before the doorway', async () => {
  assert.equal(process.env.SUPERJEV_JUDGE, undefined);
  let seen: { url: unknown; init: RequestInit | undefined } | undefined;
  const fake: typeof fetch = async (url, init) => {
    seen = { url, init };
    return new Response(JSON.stringify({ model: 'jev-latest', answers: { ok: { type: 'noul', noul: 0.5 }, pick: { type: 'choice', choice: 'A', confidence: 0.9, probabilities: { A: 0.9, B: 0.1 } } } }));
  };
  await getJudge({ apiKey: 'test-not-a-key', fetch: fake }).evaluate(request, signal());
  assert.equal(seen!.url, golden.url);
  assert.equal(seen!.init!.method, golden.method);
  assert.deepEqual(seen!.init!.headers, golden.headers);
  assert.equal(seen!.init!.body, golden.body);
});

test('(b) an unknown judge name fails with one clear line', () => {
  for (const bad of ['nope', 'typesafe-jev ', 'JEV', '../x']) {
    const r = spawnSync(process.execPath, ['-e', "import('./src/judge-profile.ts')"], {
      cwd: new URL('..', import.meta.url), encoding: 'utf8', env: { ...process.env, SUPERJEV_JUDGE: bad } });
    if (bad === 'typesafe-jev ') { assert.equal(r.status, 0); continue; } // whitespace is trimmed, not a new name
    assert.equal(r.status, 1, bad);
    const lines = r.stderr.trim().split('\n');
    assert.equal(lines.length, 1, r.stderr);
    assert.match(lines[0]!, /^super-jev: SUPERJEV_JUDGE: unknown judge .* \(known: fake, typesafe-jev\)$/);
  }
  assert.throws(() => loadJudgeProfile('nope'), /unknown judge "nope"/);
});

test('(c) a second profile with a different window, line, endpoint and key changes behaviour only through the profile', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'judge-door-'));
  const table = JSON.parse(readFileSync(PROFILES, 'utf8'));
  table.profiles.second = { ...table.profiles['typesafe-jev'], aliases: [], api_url: 'https://judge.example/v1/ask', model: 'second-1',
    key_env: 'SECOND_JUDGE_KEY', window_tokens: 50000, confidence_line: 0.9, retry_attempts: 2, overloaded_statuses: [503] };
  const file = join(dir, 'profiles.json');
  writeFileSync(file, JSON.stringify(table));
  const second = loadJudgeProfile('second', file);
  assert.equal(second.windowTokens, 50000);
  assert.equal(second.confidenceLine, 0.9);
  assert.notEqual(second.callTokens, JUDGE_PROFILE.callTokens);
  assert.equal(keyEnv(second), 'SECOND_JUDGE_KEY');
  assert.equal(keyPresent({ SECOND_JUDGE_KEY: 'k' }, second), true);
  assert.equal(keyPresent({ TYPESAFE_API_KEY: 'k' }, second), false);
  let url: unknown; let body: any;
  const fake: typeof fetch = async (u, init) => { url = u; body = JSON.parse(String(init?.body)); return new Response('{}', { status: 503 }); };
  await assert.rejects(new Jev({ apiKey: 'k', fetch: fake, profile: second }).evaluate(request, signal()), Overloaded);
  assert.equal(url, 'https://judge.example/v1/ask');
  assert.equal(body.model, 'second-1');
  // the same 503 is a plain bad reply for the default profile: only the profile decides
  await assert.rejects(new Jev({ apiKey: 'k', fetch: fake }).evaluate(request, signal()), BadReply);
  // and the retry rule follows the profile's attempt count
  let calls = 0;
  await assert.rejects(retryOverloaded(async () => { calls++; throw new Overloaded('x'); }, signal(), { ...second, retryFirstDelayMs: 1 }), Overloaded);
  assert.equal(calls, 2);
});

const KINDS: [string, () => JudgeError][] = [
  ['no-key', () => new NoKey('Set K to go')], ['auth-rejected', () => new AuthRejected('Jev HTTP 401')],
  ['unreachable', () => new Unreachable('down')], ['overloaded', () => new Overloaded('Jev HTTP 529')],
  ['bad-reply', () => new BadReply('junk')], ['too-big', () => new TooBig('Jev HTTP 400')],
  ['secret-blocked', () => new SecretBlocked('has a secret')]
];

test('(d) every error type means no verdict, never a pass', async () => {
  for (const [kind, make] of KINDS) {
    const err = make();
    assert.equal(err.kind, kind);
    assert.ok(err instanceof JudgeError && err instanceof Error);
    // through the batching door: the caller gets the same typed rejection, never an evaluation
    // (overloaded is left out here only because the door would wait out its retries; see the retry test)
    if (kind !== 'overloaded') {
      const batch = new BatchingEvaluator({ async evaluate(): Promise<never> { throw make(); } });
      await assert.rejects(batch.evaluate({ state: 's', questions: { q: { type: 'noul', instructions: 'i' } } }, signal()),
        (e: unknown) => e instanceof JudgeError && e.kind === kind);
    }
    // through navigation: an error becomes a refusal to answer, with a printable reason
    assert.match(providerFailureReason(err), /\S/);
  }
  assert.equal(providerFailureReason(new AuthRejected('Jev HTTP 401', 'Jev HTTP 401 (TypeSafe rejected the API key)')), 'Jev HTTP 401 (TypeSafe rejected the API key)');
  assert.equal(providerFailureReason(new Error('whatever')), 'unknown cause');
});

test('(d) one retry rule: only Overloaded is retried', async () => {
  const fast = { ...JUDGE_PROFILE, retryFirstDelayMs: 1 };
  for (const [kind, make] of KINDS) {
    let calls = 0;
    await assert.rejects(retryOverloaded(async () => { calls++; throw make(); }, signal(), fast));
    assert.equal(calls, kind === 'overloaded' ? JUDGE_PROFILE.retryAttempts : 1, kind);
  }
  let n = 0;
  const ok = await retryOverloaded(async () => { if (n++ < 2) throw new Overloaded('x'); return { model: 'm', answers: {} }; }, signal(), fast);
  assert.equal(ok.model, 'm');
});

test('(d) the key check comes from the judge, with a NoKey when none is set', () => {
  assert.throws(() => requireKey('go', undefined, {}), NoKey);
  assert.throws(() => requireKey('go', m => new RangeError(m), {}), /^RangeError: Set TYPESAFE_API_KEY to go$/);
  assert.doesNotThrow(() => requireKey('go', undefined, { TYPESAFE_API_KEY: 'k' }));
  assert.throws(() => new Jev({ fetch: async () => new Response('') , apiKey: '' }), NoKey);
});
