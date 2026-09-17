import { run, Jev, JsonlJournal } from '../src/index.ts';
import { recovery, review, DemoEvaluator } from './domains.ts';
const live = process.argv.includes('--live');
const document = process.argv.includes('--documents');
const evaluator = live ? new Jev() : new DemoEvaluator();
const journal = new JsonlJournal(`runs/${document ? 'documents' : 'demo'}.jsonl`);
console.log(live ? 'LIVE Jev API; tools remain local demo tools.' : 'OFFLINE scripted demo; no Jev API calls.');
const result = document
  ? await run({ domain: review(), initial: { document: 'Owner: Alex. Prepare the onboarding guide. Deadline not specified.', reviewed: false }, evaluator, journal })
  : await run({ domain: recovery(), initial: { restarted: false, healthy: false }, evaluator, journal });
console.log(JSON.stringify(result, null, 2));
if (result.status !== 'success') process.exitCode = 1;
