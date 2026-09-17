#!/usr/bin/env node
import { readFile, open, unlink } from 'node:fs/promises';
import { resolve } from 'node:path';
import { run } from './loop.ts';
import { Jev } from './jev.ts';
import { organizer, organizerReport, validateOrganizerInput } from './organizer.ts';
import type { Evaluation, Evaluator } from './types.ts';

// Only deliberate, local diagnostics are safe to print. Parser and filesystem
// exceptions can embed record contents or sensitive paths.
class CliError extends Error {}

const usage = `super-jev organize INPUT.json --live [--out OUTPUT.json]
super-jev organize examples/organizer.json --demo [--out OUTPUT.json]

Node 24+. Live mode requires TYPESAFE_API_KEY and sends records to TypeSafe.
Demo mode accepts only the bundled fixture and uses scripted answers.
JSON output goes to stdout by default. --out creates a NEW file, never overwrites.
Exit codes: 0 complete (may contain review items), 1 failure.
See docs/agents.md and examples/organizer.json for the input contract.`;

let reserved: Awaited<ReturnType<typeof open>> | undefined;
let output: string | undefined;
try {
  const args = process.argv.slice(2);
  if (args.length === 0 || args.includes('--help')) { console.log(usage); process.exit(0); }
  const [command, file, ...flags] = args;
  if (command !== 'organize' || !file || file.startsWith('--')) throw new CliError(usage);
  let mode = '';
  for (let i = 0; i < flags.length; i++) {
    if (flags[i] === '--live' || flags[i] === '--demo') {
      if (mode) throw new CliError('Choose exactly one of --live or --demo');
      mode = flags[i];
    } else if (flags[i] === '--out' && !output && flags[i + 1] && !flags[i + 1].startsWith('--')) output = resolve(flags[++i]);
    else throw new CliError('Unknown or repeated argument');
  }
  if (!mode) throw new CliError('Choose --live or --demo explicitly');
  const raw = await readFile(file, 'utf8');
  if (Buffer.byteLength(raw) > 80_000) throw new CliError('Input exceeds 80 KB; split into smaller batches');
  let input: unknown;
  try { input = JSON.parse(raw); }
  catch { throw new CliError('Input must be valid JSON'); }
  try { validateOrganizerInput(input); }
  catch { throw new CliError('Invalid organizer input; see docs/agents.md for the input contract'); }
  let evaluator: Evaluator;
  if (mode === '--demo') {
    const fixture = JSON.parse(await readFile(new URL('../examples/organizer.json', import.meta.url), 'utf8'));
    if (JSON.stringify(input) !== JSON.stringify(fixture)) throw new CliError('Demo mode only supports the unchanged bundled fixture; use --live for your records');
    evaluator = { evaluate: async () => ({ model: 'scripted-organizer-demo', answers: Object.fromEntries(['invoice', 'receipt', 'support', 'other'].map((choice, i) => [`record_${i}`, { type: 'choice' as const, choice, confidence: 1, probabilities: Object.fromEntries(Object.keys(input.categories).map(k => [k, k === choice ? 1 : 0])) }])) }) };
  } else {
    if (!process.env.TYPESAFE_API_KEY) throw new CliError('Set TYPESAFE_API_KEY to use live Jev');
    evaluator = new Jev();
  }
  // Reserve output before inference to avoid paid calls when output already exists.
  if (output) {
    try { reserved = await open(output, 'wx', 0o600); }
    catch { throw new CliError('Cannot create output; choose a new path in an existing writable directory'); }
  }
  let model = '';
  let usageTokens: Evaluation['usage'];
  const result = await run({ domain: organizer(input), initial: { rows: [], complete: false }, evaluator, maxSteps: 1,
    journal: { append: async e => { if (e.type === 'evaluation') { const data = e.data as Evaluation; model = data.model; usageTokens = data.usage; } } } });
  if (result.status !== 'success') throw new CliError(result.reason);
  const json = JSON.stringify({ version: '0.2.0', mode: mode.slice(2), model, usage: usageTokens, ...organizerReport(input, result.state) }, null, 2) + '\n';
  if (reserved) { await reserved.writeFile(json); await reserved.close(); reserved = undefined; console.error(`Saved ${output}`); }
  else process.stdout.write(json);
} catch (error) {
  if (reserved) {
    await reserved.close().catch(() => {});
    if (output) await unlink(output).catch(() => {
      console.error('Could not remove incomplete output; inspect the requested output path');
    });
  }
  console.error(error instanceof CliError ? error.message : 'Operation failed; check input and output access');
  process.exitCode = 1;
}
