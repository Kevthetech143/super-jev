// Judge pin tests: body construction and the pin-retry path (v4: the pin is
// OPT-IN, and any non-2xx on a pinned call retries once without the pin).
// Fake transport throughout - no live TypeSafe call is ever made.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { Jev, judgePinFields, judgeRuns, judgeDecisionSummary, JUDGE_TEMPERATURE_ENV, JUDGE_SEED_ENV, JUDGE_RUNS_ENV } from '../src/jev.ts';
import type { Request, Evaluation } from '../src/types.ts';

const NODE_KEY = 'TYPESAFE_API_KEY';
let savedEnv: NodeJS.ProcessEnv;

beforeEach(() => {
  savedEnv = { ...process.env };
  process.env[NODE_KEY] = 'test-key';
  delete process.env[JUDGE_TEMPERATURE_ENV];
  delete process.env[JUDGE_SEED_ENV];
  delete process.env[JUDGE_RUNS_ENV];
});

afterEach(() => {
  process.env = savedEnv;
});

const REQ: Request = {
  state: null,
  questions: { q1: { type: 'noul', instructions: 'is it true?' } }
};

const OK_BODY: Evaluation = {
  model: 'jev-test',
  answers: { q1: { type: 'noul', noul: 0.7 } }
};

type Call = { url: string; init: any };

function fakeFetch(script: Array<{ status: number; body: unknown }>, calls: Call[]) {
  return (async (url: any, init: any) => {
    calls.push({ url: String(url), init });
    const step = script[Math.min(calls.length - 1, script.length - 1)];
    return new Response(JSON.stringify(step.body), {
      status: step.status,
      headers: { 'Content-Type': 'application/json' }
    });
  }) as typeof fetch;
}

function captureStderr() {
  const lines: string[] = [];
  const orig = console.error;
  console.error = (...args: unknown[]) => { lines.push(args.map(String).join(' ')); };
  return { lines, restore: () => { console.error = orig; } };
}

const bodies = (calls: Call[]) => calls.map(c => JSON.parse(c.init.body));

test('defaults: no pin is sent when SUPERJEV_JUDGE_TEMPERATURE is unset', async () => {
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([{ status: 200, body: OK_BODY }], calls) });
  const ev = await jev.evaluate(REQ, new AbortController().signal);
  assert.equal(calls.length, 1);
  const b = bodies(calls)[0];
  assert.ok(!('temperature' in b), 'temperature must not be sent when SUPERJEV_JUDGE_TEMPERATURE is unset');
  assert.ok(!('seed' in b), 'seed must not be sent when SUPERJEV_JUDGE_SEED is unset');
  assert.equal(b.model, 'jev-latest');
  assert.deepEqual(ev.answers.q1, { type: 'noul', noul: 0.7 });
});

test('SUPERJEV_JUDGE_TEMPERATURE and SUPERJEV_JUDGE_SEED are sent when set', async () => {
  process.env[JUDGE_TEMPERATURE_ENV] = '0.3';
  process.env[JUDGE_SEED_ENV] = '42';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([{ status: 200, body: OK_BODY }], calls) });
  await jev.evaluate(REQ, new AbortController().signal);
  const b = bodies(calls)[0];
  assert.equal(b.temperature, 0.3);
  assert.equal(b.seed, 42);
});

test('malformed env values degrade to omitting the field, never to a hard fail', async () => {
  process.env[JUDGE_TEMPERATURE_ENV] = 'abc';
  process.env[JUDGE_SEED_ENV] = '3.7';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([{ status: 200, body: OK_BODY }], calls) });
  await jev.evaluate(REQ, new AbortController().signal);
  const b = bodies(calls)[0];
  assert.ok(!('temperature' in b), 'bad temperature must be omitted');
  assert.ok(!('seed' in b), 'non-integer seed must be omitted');
});

test('a non-2xx on a pinned call retries ONCE without the pin and logs the pin note', async () => {
  process.env[JUDGE_TEMPERATURE_ENV] = '0';
  process.env[JUDGE_SEED_ENV] = '7';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 400, body: { error: 'unknown field: temperature' } },
    { status: 200, body: OK_BODY }
  ], calls) });
  const cap = captureStderr();
  try {
    const ev = await jev.evaluate(REQ, new AbortController().signal);
    assert.deepEqual(ev.answers.q1, { type: 'noul', noul: 0.7 });
  } finally {
    cap.restore();
  }
  assert.equal(calls.length, 2, 'exactly one retry');
  const [first, second] = bodies(calls);
  assert.equal(first.temperature, 0);
  assert.equal(first.seed, 7);
  assert.ok(!('temperature' in second) && !('seed' in second), 'retry must drop the pin fields');
  assert.ok(cap.lines.some(l => l.includes('judge pin:')), 'must log the pin note');
});

test('no retry when no pin was sent: the default request is exactly today\'s call', async () => {
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 400, body: { error: 'rate limit exceeded' } }
  ], calls) });
  await assert.rejects(jev.evaluate(REQ, new AbortController().signal), /Jev HTTP 400/);
  assert.equal(calls.length, 1, 'no retry on an unpinned 4xx');
});

test('a 5xx on a pinned call retries once unpinned', async () => {
  process.env[JUDGE_TEMPERATURE_ENV] = '0';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 500, body: { error: 'boom' } },
    { status: 200, body: OK_BODY }
  ], calls) });
  const cap = captureStderr();
  try {
    await jev.evaluate(REQ, new AbortController().signal);
  } finally {
    cap.restore();
  }
  assert.equal(calls.length, 2, 'exactly one retry');
  const [first, second] = bodies(calls);
  assert.equal(first.temperature, 0);
  assert.ok(!('temperature' in second), 'retry must drop the pin fields');
  assert.ok(cap.lines.some(l => l.includes('judge pin:')), 'must log the pin note');
});

test('a 5xx with no pin sent never retries', async () => {
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 500, body: { error: 'boom' } }
  ], calls) });
  await assert.rejects(jev.evaluate(REQ, new AbortController().signal), /Jev HTTP 500/);
  assert.equal(calls.length, 1);
});

test('a failed retry throws with BOTH texts: pin rejected: <original>; unpinned retry: <second>', async () => {
  process.env[JUDGE_TEMPERATURE_ENV] = '0';
  process.env[JUDGE_SEED_ENV] = '7';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 422, body: { error: 'unknown field: seed' } },
    { status: 400, body: { error: 'still bad' } }
  ], calls) });
  const cap = captureStderr();
  try {
    // The original failure must not be masked by the retry's status.
    await assert.rejects(
      jev.evaluate(REQ, new AbortController().signal),
      /pin rejected: Jev HTTP 422.*unknown field: seed.*unpinned retry: Jev HTTP 400/
    );
  } finally {
    cap.restore();
  }
  assert.equal(calls.length, 2, 'must not retry more than once');
  assert.ok(!cap.lines.some(l => l.includes('judge pin:')),
    'the pin note must NOT be logged when the retry fails');
});

test('retry fires even when the 4xx body names no pin field (any non-2xx on a pinned call)', async () => {
  process.env[JUDGE_TEMPERATURE_ENV] = '0';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 429, body: { error: 'rate limit exceeded' } },
    { status: 200, body: OK_BODY }
  ], calls) });
  const cap = captureStderr();
  try {
    await jev.evaluate(REQ, new AbortController().signal);
  } finally {
    cap.restore();
  }
  assert.equal(calls.length, 2, 'exactly one retry');
  assert.ok(!('temperature' in bodies(calls)[1]), 'retry must drop the pin fields');
  assert.ok(cap.lines.some(l => l.includes('judge pin:')), 'must log the pin note');
});

test('a pinned call whose 4xx names the pin field also retries (wording-independent)', async () => {
  process.env[JUDGE_SEED_ENV] = '7';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 422, body: { error: 'bad request', details: ['seed is not a supported field'] } },
    { status: 200, body: OK_BODY }
  ], calls) });
  const cap = captureStderr();
  try {
    await jev.evaluate(REQ, new AbortController().signal);
  } finally {
    cap.restore();
  }
  assert.equal(calls.length, 2, 'exactly one retry');
  const second = bodies(calls)[1];
  assert.ok(!('temperature' in second) && !('seed' in second), 'retry must drop the pin fields');
  assert.ok(cap.lines.some(l => l.includes('judge pin:')), 'must log the pin note');
});

test('pin rejection is detected ONCE per evaluate: RUNS=2 retries only once total', async () => {
  process.env[JUDGE_TEMPERATURE_ENV] = '0';
  process.env[JUDGE_RUNS_ENV] = '2';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 400, body: { error: 'temperature not allowed' } },
    { status: 200, body: OK_BODY },
    { status: 200, body: OK_BODY }
  ], calls) });
  const cap = captureStderr();
  let ev;
  try {
    ev = await jev.evaluate(REQ, new AbortController().signal);
  } finally {
    cap.restore();
  }
  assert.equal(calls.length, 3, 'one retry for the whole evaluate, not one per run');
  const bs = bodies(calls);
  assert.equal(bs[0].temperature, 0, 'run 1 first tries the pin');
  assert.ok(!('temperature' in bs[1]), 'the retry drops the pin');
  assert.ok(!('temperature' in bs[2]), 'run 2 reuses the outcome: no pin, no retry');
  assert.equal(cap.lines.filter(l => l.includes('judge pin:')).length, 1,
    'the pin note must be logged exactly once per evaluate');
  assert.deepEqual(ev.judgeRuns.runErrors, [null, null]);
});

test('judgePinFields: opt-in — no env means no pin fields', () => {
  assert.deepEqual(judgePinFields({}), {});
  assert.deepEqual(judgePinFields({ [JUDGE_SEED_ENV]: '11' }), { seed: 11 });
  assert.deepEqual(judgePinFields({ [JUDGE_TEMPERATURE_ENV]: '1' }), { temperature: 1 });
});

const ALT_BODY: Evaluation = {
  model: 'jev-test',
  answers: { q1: { type: 'noul', noul: 0.2 } }
};

test('SUPERJEV_JUDGE_RUNS=2 stable: two judge calls, canonical result, unstable=false', async () => {
  process.env[JUDGE_RUNS_ENV] = '2';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 200, body: OK_BODY },
    { status: 200, body: OK_BODY }
  ], calls) });
  const cap = captureStderr();
  let ev;
  try {
    ev = await jev.evaluate(REQ, new AbortController().signal);
  } finally {
    cap.restore();
  }
  assert.equal(calls.length, 2, 'the judge must be called twice');
  assert.deepEqual(ev.answers.q1, { type: 'noul', noul: 0.7 }, 'run 1 is canonical');
  assert.equal(ev.judgeRuns.runs, 2);
  assert.equal(ev.judgeRuns.decisions.length, 2);
  assert.equal(ev.judgeRuns.decisions[0], ev.judgeRuns.decisions[1]);
  assert.equal(ev.judgeRuns.unstable, false);
  assert.ok(cap.lines.some(l => l.includes('judge runs:') && l.includes('unstable=false')));
});

test('SUPERJEV_JUDGE_RUNS=2 unstable: disagreeing runs are recorded and marked unstable', async () => {
  process.env[JUDGE_RUNS_ENV] = '2';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 200, body: OK_BODY },
    { status: 200, body: ALT_BODY }
  ], calls) });
  const ev = await jev.evaluate(REQ, new AbortController().signal);
  assert.equal(calls.length, 2);
  assert.deepEqual(ev.answers.q1, { type: 'noul', noul: 0.7 }, 'run 1 stays canonical');
  assert.equal(ev.judgeRuns.runs, 2);
  assert.equal(ev.judgeRuns.decisions.length, 2);
  assert.notEqual(ev.judgeRuns.decisions[0], ev.judgeRuns.decisions[1]);
  assert.equal(ev.judgeRuns.unstable, true);
});

test('a failure on run 2 keeps run 1 canonical, records the error, and does not throw', async () => {
  process.env[JUDGE_RUNS_ENV] = '2';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 200, body: OK_BODY },
    { status: 500, body: { error: 'boom' } }
  ], calls) });
  const cap = captureStderr();
  let ev;
  try {
    ev = await jev.evaluate(REQ, new AbortController().signal);
  } finally {
    cap.restore();
  }
  assert.equal(calls.length, 2);
  assert.deepEqual(ev.answers.q1, { type: 'noul', noul: 0.7 }, 'run 1 stays canonical');
  assert.equal(ev.judgeRuns.runs, 2);
  assert.equal(ev.judgeRuns.decisions.length, 2);
  assert.equal(ev.judgeRuns.decisions[0], 'q1=noul:0.7');
  assert.equal(ev.judgeRuns.decisions[1], '', 'a failed run has an empty digest');
  assert.equal(ev.judgeRuns.runErrors[0], null);
  assert.match(ev.judgeRuns.runErrors[1]!, /Jev HTTP 500/);
  assert.equal(ev.judgeRuns.unstable, true, 'a failed run is fail-closed unstable');
  assert.ok(cap.lines.some(l => l.includes('judge runs:') && l.includes('unstable=true')));
});

test('a failure on run 1 still throws: there is no canonical evaluation to keep', async () => {
  process.env[JUDGE_RUNS_ENV] = '2';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 500, body: { error: 'boom' } }
  ], calls) });
  await assert.rejects(jev.evaluate(REQ, new AbortController().signal), /Jev HTTP 500/);
  assert.equal(calls.length, 1, 'a run 1 failure aborts the remaining runs');
});

test('a pin-rejection retry failure on run 2 is recorded, not thrown', async () => {
  process.env[JUDGE_TEMPERATURE_ENV] = '0';
  process.env[JUDGE_RUNS_ENV] = '2';
  const calls: Call[] = [];
  const jev = new Jev({ fetch: fakeFetch([
    { status: 200, body: OK_BODY },
    { status: 400, body: { error: 'unknown field: temperature' } },
    { status: 400, body: { error: 'still bad' } }
  ], calls) });
  const cap = captureStderr();
  let ev;
  try {
    ev = await jev.evaluate(REQ, new AbortController().signal);
  } finally {
    cap.restore();
  }
  assert.equal(calls.length, 3, 'run 2 gets the one shared retry');
  assert.deepEqual(ev.answers.q1, { type: 'noul', noul: 0.7 }, 'run 1 stays canonical');
  assert.match(ev.judgeRuns.runErrors[1]!, /pin rejected:.*unpinned retry:/);
  assert.equal(ev.judgeRuns.unstable, true);
  assert.ok(!cap.lines.some(l => l.includes('judge pin:')),
    'the pin note must NOT be logged when the retry fails');
});

test('SUPERJEV_JUDGE_RUNS malformed or < 1 degrades to a single run', async () => {
  for (const bad of ['abc', '0', '-2', '2.5']) {
    process.env[JUDGE_RUNS_ENV] = bad;
    const calls: Call[] = [];
    const jev = new Jev({ fetch: fakeFetch([{ status: 200, body: OK_BODY }], calls) });
    const cap = captureStderr();
    try {
      const ev = await jev.evaluate(REQ, new AbortController().signal);
      assert.equal(ev.judgeRuns.runs, 1);
      assert.equal(ev.judgeRuns.unstable, false);
    } finally {
      cap.restore();
    }
    assert.equal(calls.length, 1, `RUNS=${bad} must make exactly one judge call`);
    assert.ok(cap.lines.some(l => l.includes('judge runs:') && l.includes('not a positive integer')));
  }
});

test('judgeRuns: default 1, parses positive integers from env', () => {
  assert.equal(judgeRuns({}), 1);
  assert.equal(judgeRuns({ [JUDGE_RUNS_ENV]: '3' }), 3);
  assert.equal(judgeRuns({ [JUDGE_RUNS_ENV]: '' }), 1);
});

test('judgeDecisionSummary: canonical, sorted, tab/newline-free', () => {
  const s = judgeDecisionSummary(OK_BODY);
  assert.equal(s, 'q1=noul:0.7');
  assert.ok(!/[\t\n\r]/.test(s));
});
