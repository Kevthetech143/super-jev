// The long-context request header on the Node wire. A judge profile may name a [name, value] header that lifts the judge's input
// limit past its normal one. Jev sends it on a request only when the profile's own window is past that limit AND the request is
// more than small's call budget can carry (state plus the longest question, the measure the Python client refuses on), so a
// request small can carry never asks for it, whatever the preset. The name, the value and the limit all come from the profile.
// Fake fetch throughout: no judge is called. To run this file alone:
//   node --import ./test/clean-env.ts --test test/jev-long-context.test.ts
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import * as jevModule from '../src/jev.ts';
import { getJudge } from '../src/judge.ts';
import { JUDGE_PROFILE, judgeTokens, loadJudgeProfile, type JudgeProfile } from '../src/judge-profile.ts';
import { AuthRejected, JudgeError, Overloaded } from '../src/judge-errors.ts';
import type { Request } from '../src/types.ts';
import { largeTable } from './large-table.ts';

const { Jev } = jevModule;
/** Looked up when called, so a build without the counter fails the tests that read it, not this whole file at import. */
const longCalls = (): number => jevModule.longCalls();

/** The profile at a preset (the loader's fourth parameter). The shipped table offers Small and Medium only (Large waits for its own
 * test), so a request this size is sent through a made-up table: the shipped one plus a `large` row (test/large-table.ts). */
const TABLE = largeTable();
const at = (context: string): JudgeProfile => loadJudgeProfile('typesafe-jev', TABLE, {}, context);
const [NAME, VALUE] = at('large').longContextHeader!;
/** Small's call budget: the most a request can measure and still be carried without the header. */
const BASE = at('large').baseCallTokens;
const URL_ENV = at('large').apiUrlEnv;

const signal = () => new AbortController().signal;
const question = { type: 'noul', instructions: 'Does the note say so?' } as const;
const FILLER = 'The cuff watch note says the wrist model is validated. ';
/** Plain text that counts as exactly `tokens` judge tokens (two bytes each). */
const prose = (tokens: number) => FILLER.repeat(Math.ceil((tokens * 2) / FILLER.length)).slice(0, tokens * 2);
/** A request whose state plus longest question measures exactly `total` judge tokens. */
const sized = (total: number): Request => ({ state: prose(total - judgeTokens(question)), questions: { q1: question } });
const measure = (r: Request) => judgeTokens(r.state) + Math.max(...Object.values(r.questions).map(q => judgeTokens(q)));

type Seen = { url: string; headers: Record<string, string>; raw: string; body: { model: string; state: unknown; questions: Record<string, unknown>; temperature?: number } };
/** A fetch that records each request and answers every question in it; the status is the next of `statuses` (the last repeats), 200 a good reply. */
function fakeFetch(seen: Seen[], statuses: number[] = [200]): typeof fetch {
  return (async (url: unknown, init: RequestInit) => {
    const body = JSON.parse(String(init.body));
    seen.push({ url: String(url), headers: init.headers as Record<string, string>, raw: String(init.body), body });
    const status = statuses[Math.min(seen.length - 1, statuses.length - 1)]!;
    const answers = Object.fromEntries(Object.keys(body.questions).map(id => [id, { type: 'noul', noul: 0.5 }]));
    return status === 200 ? new Response(JSON.stringify({ model: 'm', answers })) : new Response('{}', { status });
  }) as typeof fetch;
}
/** The one request a Jev at `profile` sends for `request`. */
async function send(profile: JudgeProfile, request: Request): Promise<Seen> {
  const seen: Seen[] = [];
  await new Jev({ apiKey: 'k', fetch: fakeFetch(seen), profile }).evaluate(request, signal());
  assert.equal(seen.length, 1);
  return seen[0]!;
}
const carries = (s: Seen) => s.headers[NAME] === VALUE;
/** Run `fn` with the judge pin note (printed on stderr when a pinned try is retried) muted. */
async function quietly(fn: () => Promise<void>) {
  const note = console.error;
  console.error = () => undefined;
  try { await fn(); } finally { console.error = note; }
}

let savedEnv: NodeJS.ProcessEnv;
beforeEach(() => { savedEnv = { ...process.env }; });
afterEach(() => { process.env = savedEnv; });

test('small sends no header however large the request', async () => {
  for (const total of [BASE - 1, BASE, BASE + 1, BASE * 2]) assert.equal(measure(sized(total)), total);   // sized() puts requests exactly on or beside the line
  const huge = sized(BASE * 8);
  assert.ok(carries(await send(at('large'), huge)), 'control: the same request does carry it at large');
  for (const request of [sized(BASE + 1), sized(BASE * 2), huge]) {
    for (const profile of [at('small'), JUDGE_PROFILE]) {
      const seen = await send(profile, request);
      assert.ok(!(NAME in seen.headers), `${measure(request)} judge tokens at ${profile.context}`);
      // the request is what it was before presets: the same two headers in the same order, and the same body byte for byte
      assert.deepEqual(Object.keys(seen.headers), ['Authorization', 'Content-Type']);
      assert.equal(seen.raw, JSON.stringify({ model: profile.model, ...request }));
    }
  }
});

for (const name of ['medium', 'large']) {
  test(`${name} sends the header on a request over small's ceiling`, async () => {
    for (const total of [BASE + 1, BASE * 2, BASE * 8]) {
      const request = sized(total);
      const seen = await send(at(name), request);
      assert.equal(seen.headers[NAME], VALUE, `${total} judge tokens`);
      assert.equal(seen.headers.Authorization, 'Bearer k');
      assert.equal(seen.headers['Content-Type'], 'application/json');
      assert.deepEqual(seen.body, { model: at(name).model, ...request });      // only a header is added: the body is the request
    }
  });

  test(`${name} sends none on a request under it`, async () => {
    assert.ok(carries(await send(at(name), sized(BASE + 1))), 'control: one token over does carry it');
    for (const request of [sized(judgeTokens(question) + 1), sized(1_000), sized(BASE - 1), sized(BASE), { state: null, questions: { q1: question } }]) {
      assert.ok(!(NAME in (await send(at(name), request)).headers), `${measure(request)} judge tokens at ${name}`);
    }
  });
}

test('the longest question counts, not the sum of them', async () => {
  assert.ok(carries(await send(at('large'), sized(BASE + 1))), 'control: the longest question over the budget does carry it');
  // each question is short and the state leaves room for one of them, but together they are over the budget
  const many: Request = { state: prose(BASE - 2 * judgeTokens(question)), questions: Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`q${i}`, question])) };
  assert.ok(judgeTokens(many.state) + Object.keys(many.questions).length * judgeTokens(question) > BASE);
  assert.ok(measure(many) <= BASE);
  assert.ok(!carries(await send(at('large'), many)));
});

test('a profile without the header setting never sends one', async () => {
  const huge = sized(BASE * 4);
  assert.ok(carries(await send(at('large'), huge)), 'control: the profile as written does carry it');
  const without: JudgeProfile = { ...at('large'), longContextHeader: null, longContextAboveTokens: null };
  assert.ok(!(NAME in (await send(without, huge)).headers));
});

test('the header is whatever the profile names, and applies only when the window is past the limit it states', async () => {
  const request = sized(BASE * 2);
  const seen = await send({ ...at('large'), longContextHeader: ['X-Test-Mode', 'wide'] }, request);
  assert.equal(seen.headers['X-Test-Mode'], 'wide');
  assert.ok(!(NAME in seen.headers));
  const window = at('large').windowTokens;
  assert.ok(carries(await send({ ...at('large'), longContextAboveTokens: window - 1 }, request)), 'a window one past the limit');
  assert.ok(!carries(await send({ ...at('large'), longContextAboveTokens: window }, request)), 'a window at the limit is not past it');
  assert.ok(!carries(await send({ ...at('medium'), longContextAboveTokens: at('medium').windowTokens }, request)));
});

test('the unpinned retry carries the same header decision', async () => {
  process.env.SUPERJEV_JUDGE_TEMPERATURE = '0.3';
  await quietly(async () => {
    for (const [request, long] of [[sized(BASE * 2), true], [sized(BASE), false]] as const) {
      const seen: Seen[] = [];
      await new Jev({ apiKey: 'k', fetch: fakeFetch(seen, [400, 200]), profile: at('large') }).evaluate(request, signal());
      assert.equal(seen.length, 2, 'the pinned try, then the same request unpinned');
      assert.equal(seen[0]!.body.temperature, 0.3);
      assert.ok(!('temperature' in seen[1]!.body));
      assert.deepEqual(seen.map(carries), [long, long], `${measure(request)} judge tokens`);
    }
  });
});

test('long calls counts only requests that carried the header', async () => {
  const before = longCalls();
  await send(at('small'), sized(BASE * 2));                                    // small never asks for it
  await send(at('large'), sized(BASE));                                        // under small's ceiling
  await send(at('large'), { state: null, questions: { q1: question } });
  assert.equal(longCalls() - before, 0);
  await send(at('large'), sized(BASE + 1));
  await send(at('medium'), sized(BASE * 2));
  assert.equal(longCalls() - before, 2);
  // a request that was sent counts whatever came back, and a request sent again counts again
  const busy = new Jev({ apiKey: 'k', fetch: fakeFetch([], [429, 200]), profile: at('large') });
  await assert.rejects(busy.evaluate(sized(BASE * 2), signal()), Overloaded);
  await busy.evaluate(sized(BASE * 2), signal());
  assert.equal(longCalls() - before, 4);
  // a pinned try and its unpinned retry are two requests
  process.env.SUPERJEV_JUDGE_TEMPERATURE = '0.3';
  await quietly(async () => { await new Jev({ apiKey: 'k', fetch: fakeFetch([], [400, 200]), profile: at('large') }).evaluate(sized(BASE * 2), signal()); });
  assert.equal(longCalls() - before, 6);
});

test('the header goes where the key may go: the judge\'s own host, or this machine for a metering proxy', async () => {
  const request = sized(BASE * 2);
  process.env[URL_ENV] = 'http://127.0.0.1:18765/v1/systemone';
  const proxied = await send(at('large'), request);
  assert.equal(proxied.url, 'http://127.0.0.1:18765/v1/systemone');
  assert.ok(carries(proxied), 'a counting proxy on this machine gets it, or a metered run would measure small');
  // another host gets neither the key nor the header: nothing is sent and nothing is counted
  process.env[URL_ENV] = 'https://other.example.test/v1/systemone';
  const before = longCalls();
  const seen: Seen[] = [];
  await assert.rejects(new Jev({ apiKey: 'k', fetch: fakeFetch(seen), profile: at('large') }).evaluate(request, signal()), AuthRejected);
  assert.equal(seen.length, 0);
  assert.equal(longCalls() - before, 0);
  // a judge that takes no key is sent the request wherever the URL points, but the header still stays home
  const keyless: Seen[] = [];
  await new Jev({ fetch: fakeFetch(keyless), profile: { ...at('large'), keyRequired: false } }).evaluate(request, signal());
  assert.equal(keyless.length, 1);
  assert.equal(keyless[0]!.url, 'https://other.example.test/v1/systemone');
  assert.ok(!(NAME in keyless[0]!.headers) && !('Authorization' in keyless[0]!.headers));
  assert.equal(longCalls() - before, 0);
});

test('getJudge hands its profile to the judge, and the implementation is the profile\'s kind', async () => {
  const request = sized(BASE * 2);
  const seen: Seen[] = [];
  await getJudge({ apiKey: 'k', fetch: fakeFetch(seen), profile: at('large') }).evaluate(request, signal());
  await getJudge({ apiKey: 'k', fetch: fakeFetch(seen) }).evaluate(request, signal());      // no profile: the one SUPERJEV_JUDGE picks, at small
  assert.deepEqual(seen.map(carries), [true, false]);
  assert.throws(() => getJudge({ apiKey: 'k', profile: { ...at('large'), kind: 'nope' } }),
    (e: unknown) => e instanceof JudgeError && e.message === 'no implementation for judge kind "nope"');
});
