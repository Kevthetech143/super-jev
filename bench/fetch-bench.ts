/**
 * Admission bench for the fetch layer (wishlist item 6, fetch v2).
 *
 * Still entirely offline, against a scripted stub — never TypeSafe — so this
 * proves the PLUMBING (local narrowing, the prefilter cut, batching, ranking,
 * the none gate, the coverage manifest) end to end, and nothing about a real
 * judge's accuracy. The judge stage itself is scripted to know the answer
 * ("high" for the expected id, "none" for the rest, "none" for everything on
 * a no-match case), the same "scripted, not evidence" pattern the v1 bench
 * used. What v2 adds real signal about is everything BEFORE the judge: the
 * local-narrowing prefilter (BM25-lite over text + utterances, minus
 * negatives, plus context) has to actually keep the true record in its top N
 * for the scripted judge to ever see it, and the none gate has to actually
 * fire on a no-match case. A live accuracy number belongs in
 * `bench/live-measure.ts`; see `docs/wishlist.md` for why no number from
 * that path is ever committed.
 *
 * The held-out split matters here specifically because of the feedback loop
 * (`npm run catalog -- learn`): this bench simulates one round of it by
 * adding each TRAIN-split case's own request text as a new utterance on its
 * expected record, then narrows and judges only the HOLDOUT split against
 * that augmented catalog. A holdout request's own exact text is never added
 * as an utterance for any record, so a passing hit@1 on holdout is evidence
 * the narrowing pass generalizes past the literal phrasings it was fed, not
 * evidence it just memorized the test.
 *
 *   npm run bench:fetch
 *   npm run bench:fetch -- bench/fetch-cases.json --holdout 0.3 --seed 42 --min-hit1 0.8
 */
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { applyNoneGate, DEFAULT_FETCH_FLOOR, DEFAULT_FETCH_MARGIN, DEFAULT_PREFILTER, runFetch, type FetchCatalogEntry } from '../src/enhance/fetch.ts';
import { choiceAnswer } from '../src/enhance/stub.ts';
import type { Answer, Evaluation, Evaluator, Question, Request } from '../src/types.ts';

export type Case = { request: string; context?: string[]; expected_id: string | null };
export type Fixture = { catalog: FetchCatalogEntry[]; cases: Case[] };

const DEFAULT_FIXTURE_PATH = fileURLToPath(new URL('./fetch-cases.json', import.meta.url));
const K = 5;

/** Deterministic 32-bit PRNG (mulberry32), so a fixed --seed always produces the same split. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Fisher-Yates with a seeded PRNG. Does not mutate the input. */
export function seededShuffle<T>(items: T[], seed: number): T[] {
  const rng = mulberry32(seed);
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/**
 * Split into train/holdout by a fixed fraction, seeded so the split is
 * reproducible. `fraction` is the holdout share, e.g. 0.3 keeps 30% out.
 */
export function splitHoldout<T>(items: T[], fraction: number, seed: number): { train: T[]; holdout: T[] } {
  if (!Number.isFinite(fraction) || fraction < 0 || fraction > 1) throw new Error('holdout fraction must be in [0,1]');
  const shuffled = seededShuffle(items, seed);
  const holdoutCount = Math.round(shuffled.length * fraction);
  return { train: shuffled.slice(holdoutCount), holdout: shuffled.slice(0, holdoutCount) };
}

/**
 * Simulate one round of `npm run catalog -- learn`: every train-split case
 * with a real expected id has its own request text added as a new utterance
 * on that record (deduped, case/space-normalized), and nothing else changes.
 * Returns a new catalog array; the input is never mutated.
 */
export function augmentCatalogWithTrainUtterances(catalog: FetchCatalogEntry[], trainMatchCases: Case[]): FetchCatalogEntry[] {
  const byId = new Map<string, Set<string>>();
  for (const c of trainMatchCases) {
    if (!c.expected_id) continue;
    const bucket = byId.get(c.expected_id) ?? new Set<string>();
    bucket.add(c.request.trim());
    byId.set(c.expected_id, bucket);
  }
  return catalog.map(r => {
    const additions = byId.get(r.id);
    if (!additions?.size) return r;
    const existing = new Set((r.utterances ?? []).map(u => u.trim().toLowerCase()));
    const merged = [...(r.utterances ?? [])];
    for (const a of additions) if (!existing.has(a.toLowerCase())) merged.push(a);
    return { ...r, utterances: merged };
  });
}

/**
 * A transport scripted to know the case's own expected id: it answers "high"
 * for that catalog record and "none" for every other record it is actually
 * asked about, regardless of the request text. A no-match case (expectedId
 * null) answers "none" for every record. Exercises ranking, the none gate,
 * and the coverage manifest exactly as `runFetch` will use them, without
 * claiming to measure whether a real judge would agree with the label. A
 * record the local narrowing pass drops before the judge is never asked
 * about at all — that is the plumbing this bench is actually checking.
 */
function scriptedTransport(expectedId: string | null): Evaluator {
  return {
    evaluate: async (request: Request): Promise<Evaluation> => {
      const state = (request.state as { records: Record<string, { id: string }> }).records;
      const answers: Record<string, Answer> = {};
      for (const [wireKey, question] of Object.entries(request.questions) as [string, Question][]) {
        if (question.type !== 'choice') continue;
        const recordKey = Object.keys(state).find(k => wireKey.endsWith(`_${k}`));
        const id = recordKey ? state[recordKey].id : undefined;
        const options = Object.keys(question.criteria);
        answers[wireKey] = choiceAnswer(id === expectedId ? 'high' : 'none', 0.9, options);
      }
      return { model: 'scripted-offline', answers };
    }
  };
}

function number(raw: string | undefined, flag: string): number {
  const value = Number(raw);
  if (!raw || !Number.isFinite(value)) throw new Error(`${flag} needs a number`);
  return value;
}

async function main() {
  const args = process.argv.slice(2);
  let fixturePath = DEFAULT_FIXTURE_PATH;
  let holdout = 0.3, seed = 42, minHit1 = 0.8, floor = DEFAULT_FETCH_FLOOR, margin = DEFAULT_FETCH_MARGIN, prefilter = DEFAULT_PREFILTER;
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    const next = () => { const v = args[++i]; if (v === undefined) throw new Error(`${flag} needs a value`); return v; };
    if (flag === '--holdout') holdout = number(next(), '--holdout');
    else if (flag === '--seed') seed = number(next(), '--seed');
    else if (flag === '--min-hit1') minHit1 = number(next(), '--min-hit1');
    else if (flag === '--floor') floor = number(next(), '--floor');
    else if (flag === '--margin') margin = number(next(), '--margin');
    else if (flag === '--prefilter') prefilter = number(next(), '--prefilter');
    else if (!flag.startsWith('--')) fixturePath = resolve(flag);
    else throw new Error(`Unknown argument ${flag}`);
  }

  const fixture = JSON.parse(await readFile(fixturePath, 'utf8')) as Fixture;
  if (!fixture.catalog?.length || !fixture.cases?.length) throw new Error('The cases file is missing a catalog or cases array');

  const { train, holdout: holdoutCases } = splitHoldout(fixture.cases, holdout, seed);
  const trainMatchCases = train.filter(c => c.expected_id !== null);
  const catalog = augmentCatalogWithTrainUtterances(fixture.catalog, trainMatchCases);

  let hitAt1 = 0, hitAtK = 0, calls = 0;
  let tpNoMatch = 0, fpNoMatch = 0, fnNoMatch = 0;
  const holdoutMatch = holdoutCases.filter(c => c.expected_id !== null);
  const holdoutNoMatch = holdoutCases.filter(c => c.expected_id === null);
  const misses: { request: string; expected: string | null; got: string[]; gated: boolean }[] = [];

  for (const c of holdoutCases) {
    const run = await runFetch(catalog, c.request, {
      transport: scriptedTransport(c.expected_id), k: K, prefilter, context: c.context
    });
    calls += run.calls;
    const gate = applyNoneGate(run, floor, margin);
    const ids = run.ranked.map(r => r.id);
    const actualNoMatch = c.expected_id === null;
    const predictedNoMatch = gate.noMatch;

    if (predictedNoMatch && actualNoMatch) tpNoMatch += 1;
    else if (predictedNoMatch && !actualNoMatch) fpNoMatch += 1;
    else if (!predictedNoMatch && actualNoMatch) fnNoMatch += 1;

    if (actualNoMatch) {
      if (!(predictedNoMatch && ids.length === 0)) misses.push({ request: c.request, expected: null, got: ids, gated: predictedNoMatch });
      continue;
    }
    if (ids[0] === c.expected_id) hitAt1 += 1;
    if (ids.includes(c.expected_id)) hitAtK += 1;
    else misses.push({ request: c.request, expected: c.expected_id, got: ids, gated: predictedNoMatch });
  }

  const matchTotal = holdoutMatch.length || 1;
  const hit1 = hitAt1 / matchTotal;
  const hitK = hitAtK / matchTotal;
  const precisionDenom = tpNoMatch + fpNoMatch;
  const recallDenom = tpNoMatch + fnNoMatch;
  const noMatchPrecision = precisionDenom ? tpNoMatch / precisionDenom : 1;
  const noMatchRecall = recallDenom ? tpNoMatch / recallDenom : 1;
  const callsPerRequest = holdoutCases.length ? calls / holdoutCases.length : 0;

  console.log(`fetch admission bench: ${fixture.cases.length} case(s), ${fixture.catalog.length}-record catalog, seed=${seed}, holdout=${holdout} (${holdoutCases.length} held out, ${train.length} train), prefilter=${prefilter}, floor=${floor}, margin=${margin}, k=${K}`);
  console.log(`  train-split utterances added: ${catalog.reduce((s, r, i) => s + ((r.utterances?.length ?? 0) - (fixture.catalog[i]?.utterances?.length ?? 0)), 0)} (holdout requests' own text is never one of them)`);
  console.log(`  hit@1 (holdout): ${hitAt1}/${holdoutMatch.length} = ${hit1.toFixed(2)}`);
  console.log(`  hit@${K} (holdout): ${hitAtK}/${holdoutMatch.length} = ${hitK.toFixed(2)}`);
  console.log(`  noMatch precision (holdout): ${tpNoMatch}/${precisionDenom || 0} = ${noMatchPrecision.toFixed(2)}`);
  console.log(`  noMatch recall (holdout): ${tpNoMatch}/${recallDenom || 0} = ${noMatchRecall.toFixed(2)} (${holdoutNoMatch.length} actual no-match case(s) in holdout)`);
  console.log(`  calls per request (holdout): ${callsPerRequest.toFixed(2)}`);
  console.log('  NOT evidence of real judge accuracy: the judge stage is scripted to know the answer. It IS evidence the local-narrowing/none-gate plumbing carries the true record through to the judge, and back out, correctly. See the file header.');
  if (misses.length) {
    console.log('  missed on holdout (expected id never in the top-k after narrowing, or a no-match case that still produced a pick):');
    for (const m of misses) console.log(`    "${m.request}" expected ${m.expected ?? 'noMatch'}, got [${m.got.join(', ')}]${m.gated ? ' (gated: asked instead)' : ''}`);
  }

  if (hit1 < minHit1) {
    console.log(`  FAIL: hit@1 on holdout (${hit1.toFixed(2)}) is below --min-hit1 (${minHit1})`);
    process.exitCode = 1;
  }
}

if (import.meta.filename === process.argv[1]) {
  main().catch(error => { console.error(error instanceof Error ? error.message : error); process.exitCode = 1; });
}
