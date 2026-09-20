#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { Jev } from './jev.ts';
import { adjudicateEvidence, type EvidenceAdjudicationInput } from './enhance/evidence-adjudication.ts';

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (args.length !== 2 || args[0] !== '--input') throw new Error('Usage: npm run evidence:adjudicate -- --input POOL.json');
  if (!process.env.TYPESAFE_API_KEY) throw new Error('Set TYPESAFE_API_KEY to run evidence adjudication');
  if ((process.env.SUPERJEV_JUDGE_RUNS ?? '1') !== '1') throw new Error('Evidence adjudication requires SUPERJEV_JUDGE_RUNS=1');
  if (process.env.SUPERJEV_JUDGE_TEMPERATURE !== undefined || process.env.SUPERJEV_JUDGE_SEED !== undefined) throw new Error('Evidence adjudication requires judge temperature and seed overrides to be unset');
  let raw: string;
  try { raw = args[1] === '-' ? await new Promise<string>((resolve, reject) => {
    let s = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', x => { s += x; if (s.length > 200_000) reject(new Error('stdin exceeds 200000 characters')); }); process.stdin.on('end', () => resolve(s)); process.stdin.on('error', reject);
  }) : await readFile(args[1], { encoding: 'utf8', flag: 'r' }); }
  catch { throw new Error('could not read input'); }
  let input: EvidenceAdjudicationInput;
  try { input = JSON.parse(raw) as EvidenceAdjudicationInput; }
  catch { throw new Error('input is not valid JSON'); }
  const result = await adjudicateEvidence(input, { transport: new Jev({ apiKey: process.env.TYPESAFE_API_KEY }), timeoutMs: 30_000 });
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
  if (result.status === 'error') process.exitCode = 2;
}

main().catch(err => { process.stderr.write(`evidence-adjudication: ${err instanceof Error ? err.message : String(err)}\n`); process.exitCode = 1; });
