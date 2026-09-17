import test from 'node:test';
import assert from 'node:assert/strict';
import { run } from '../src/index.ts';
import { recovery, DemoEvaluator } from '../examples/domains.ts';
import type { Event } from '../src/types.ts';

// Regression tests for "run deadline does not cover journaling": a journal
// append that never resolved outlived a 20 ms deadline by more than a second,
// and a failed intent write could still be followed by a tool effect.

function setup(append: (event: Event) => Promise<void>) {
  const acted: string[] = [];
  const domain = recovery();
  const tools = Object.fromEntries(Object.entries(domain.tools).map(([name, tool]) => [name, {
    validate: tool.validate.bind(tool),
    execute: async (args: unknown, context: { signal: AbortSignal; idempotencyKey: string }) => {
      acted.push(name);
      return tool.execute(args, context);
    }
  }]));
  return {
    domain: { ...domain, tools }, initial: { restarted: false, healthy: false },
    evaluator: new DemoEvaluator(), journal: { append }, acted
  };
}

test('a journal append that never resolves cannot outlive the run deadline', async () => {
  const s = setup(() => new Promise<void>(() => {}));
  // The pending append holds no handle, so keep the loop alive deliberately.
  const keepAlive = setInterval(() => {}, 100);
  const started = process.hrtime.bigint();
  let settled: Awaited<ReturnType<typeof run>> | undefined;
  let rejected = false;
  try { settled = await run({ ...s, timeoutMs: 20 }); }
  catch { rejected = true; }
  finally { clearInterval(keepAlive); }
  const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;
  assert.ok(elapsedMs < 500, `run took ${elapsedMs.toFixed(0)} ms, expected well under 1 s`);
  assert.ok(rejected || settled!.status === 'error', 'a dead journal must end the run, not hang it');
  if (settled) assert.equal(settled.journaled, false, 'the closing record could not be written');
  assert.deepEqual(s.acted, [], 'no tool may run when the journal is dead');
});

test('a journal append that hangs only on the intent record leaves the action not attempted', async () => {
  const s = setup(event => event.type === 'action_started' ? new Promise<void>(() => {}) : Promise.resolve());
  const keepAlive = setInterval(() => {}, 100);
  let result: Awaited<ReturnType<typeof run>>;
  try { result = await run({ ...s, timeoutMs: 50 }); }
  finally { clearInterval(keepAlive); }
  assert.equal(result.status, 'error');
  assert.match(result.reason, /not attempted/);
  assert.deepEqual(s.acted, [], 'the effect must not follow a failed intent write');
});

test('a rejected intent record stops the action and reports it as not attempted', async () => {
  const s = setup(event => event.type === 'action_started'
    ? Promise.reject(new Error('disk full')) : Promise.resolve());
  const result = await run(s);
  assert.equal(result.status, 'error');
  assert.equal(result.reason, 'Intent log failed or timed out; action not attempted');
  assert.deepEqual(s.acted, [], 'the effect must not follow a failed intent write');
  assert.equal(result.journaled, true, 'the closing record still succeeded here');
});

test('an action whose completion cannot be logged is reported as outcome unknown', async () => {
  const s = setup(event => event.type === 'action_completed'
    ? Promise.reject(new Error('disk full')) : Promise.resolve());
  const result = await run(s);
  assert.equal(result.status, 'error');
  assert.match(result.reason, /outcome unknown/);
  // This is the honest reading: the effect did happen, only its record failed.
  assert.deepEqual(s.acted, ['restart']);
});

test('a healthy journal still records every stage and reports journaled', async () => {
  const events: Event[] = [];
  const s = setup(async event => { events.push(event); });
  const result = await run(s);
  assert.equal(result.status, 'success');
  assert.equal(result.journaled, true);
  for (const type of ['started', 'request', 'evaluation', 'decision', 'action_started', 'action_completed', 'state', 'finished']) {
    assert.ok(events.some(e => e.type === type), `missing ${type} record`);
  }
});
