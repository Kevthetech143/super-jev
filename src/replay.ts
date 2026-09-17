import { readJournal } from './journal.ts';
const path = process.argv[2];
if (!path) { console.error('Usage: npm run replay -- runs/demo.jsonl'); process.exit(1); }
for (const event of await readJournal(path)) {
  console.log(`${event.runId.slice(0, 8)} step=${event.step} ${event.type} ${JSON.stringify(event.data)}`);
}
