/**
 * Offline hit@1 / hit@k bench for the fetch layer (wishlist item 6).
 *
 * This runs entirely against a scripted stub, never against TypeSafe, so it
 * proves the plumbing (planning, batching, ranking, the coverage manifest)
 * and NOTHING about real ranking accuracy. The stub is told the expected
 * answer for each case ahead of time — it scripts the expected catalog id as
 * "high" and everything else "none" — the same "scripted, not evidence"
 * pattern `bench/stub-run.ts` uses for the sweep pilot. A live accuracy
 * number belongs in `bench/live-measure.ts` against real Jev calls, and even
 * then the provider's preview terms keep it out of any committed doc — see
 * `docs/wishlist.md`. Nothing here is written to a file; it only prints.
 *
 *   npm run bench:fetch
 */
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { runFetch, type FetchCatalogEntry } from '../src/enhance/fetch.ts';
import { choiceAnswer } from '../src/enhance/stub.ts';
import type { Answer, Evaluation, Evaluator, Question, Request } from '../src/types.ts';

type Case = { request: string; expectedId: string };
type Fixture = { catalog: FetchCatalogEntry[]; cases: Case[] };

const FIXTURE_PATH = fileURLToPath(new URL('./fetch-cases.json', import.meta.url));
const K = 5;

/**
 * A transport scripted to know the case's own expected id: it answers "high"
 * for that catalog record and "none" for every other, regardless of the
 * request text. This is deliberate — it exercises ranking, k, and the
 * coverage manifest exactly as `runFetch` will use them, without claiming to
 * measure whether a real judge would agree with the label.
 */
function scriptedTransport(expectedId: string): Evaluator {
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

async function main() {
  const fixture = JSON.parse(await readFile(FIXTURE_PATH, 'utf8')) as Fixture;
  if (!fixture.catalog?.length || !fixture.cases?.length) throw new Error('fetch-cases.json is missing a catalog or cases array');

  let hitAt1 = 0;
  let hitAtK = 0;
  const misses: { request: string; expectedId: string; got: string[] }[] = [];

  for (const c of fixture.cases) {
    const run = await runFetch(fixture.catalog, c.request, { transport: scriptedTransport(c.expectedId), k: K });
    const ids = run.ranked.map(r => r.id);
    if (ids[0] === c.expectedId) hitAt1 += 1;
    if (ids.includes(c.expectedId)) hitAtK += 1;
    else misses.push({ request: c.request, expectedId: c.expectedId, got: ids });
  }

  const total = fixture.cases.length;
  console.log(`fetch bench: ${total} case(s) over a ${fixture.catalog.length}-record fake catalog, scripted stub, k=${K}`);
  console.log(`  hit@1: ${hitAt1}/${total}`);
  console.log(`  hit@${K}: ${hitAtK}/${total}`);
  console.log('  NOT evidence of real ranking accuracy: the stub is told the answer ahead of time. See the file header.');
  if (misses.length) {
    console.log('  missed (expected id never in the top-k, which means the stub answered "none" but is not in ranked list — a plumbing bug, not a ranking miss):');
    for (const m of misses) console.log(`    "${m.request}" expected ${m.expectedId}, got [${m.got.join(', ')}]`);
  }
}

main().catch(error => { console.error(error instanceof Error ? error.message : error); process.exitCode = 1; });
