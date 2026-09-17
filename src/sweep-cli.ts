#!/usr/bin/env node
/**
 * `npm run sweep` — process a pile of records that is bigger than one call, in
 * full, and prove nothing was skipped.
 *
 * The plan is always printed before anything is called, so a caller can read it
 * and stop. `--dry-run` stops there by construction and reaches no network.
 */
import { mkdir, open, readFile, stat } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { Jev } from './jev.ts';
import { StubEvaluator, choiceAnswer } from './enhance/stub.ts';
import { formatSweepPlan, formatSweepReport, planSweep, runSweep, MAX_QUESTIONS_PER_CALL, DEFAULT_SWEEP_GATE, type SweepInputRecord, type SweepQuestion } from './enhance/sweep.ts';
import type { Answer, Evaluator, Question, Request } from './types.ts';

// Only deliberate, local diagnostics are printed. A raw parser or filesystem
// exception can embed record contents or sensitive paths.
class CliError extends Error {}

const usage = `super-jev sweep --records RECORDS.jsonl --questions QUESTIONS.json --out DIR [options]

Asks every question of every record, across as many calls as the budget needs.

  --records   FILE   JSONL, one {"id","text","meta"} object per line. Only id and
                     text are ever sent; meta stays local and is echoed back.
  --questions FILE   JSON: an array of choice questions, or {"questions":[...]}.
                     Each is {"name","instructions","criteria":{option:desc}}.
  --out       DIR    Output directory. Files are created, never overwritten.
  --budget    N      maxInputTokens per call. Default 8000.
  --batch     N      Max records per call. Lowered automatically so that
                     records x questions stays within the ${MAX_QUESTIONS_PER_CALL}-question call cap.
  --gate      N      Accept gate, 0..1. A record is ACCEPTED only when every
                     question about it clears this. Default ${DEFAULT_SWEEP_GATE}.
  --dry-run          Print the plan and write plan.json. Zero network.
  --stub             Run against the offline stub. Synthetic answers, zero
                     network, no API key. Never evidence about anything.

Live mode is the default and needs TYPESAFE_API_KEY. It sends record text to
TypeSafe. Writes results.jsonl, manifest.json, cost.json, report.md, plan.json.
Exit codes: 0 the run completed and the manifest is complete, 1 failure.`;

const MAX_RECORDS_BYTES = 32 * 1024 * 1024;
const MAX_QUESTIONS_BYTES = 256 * 1024;

/** Deterministic 32-bit FNV-1a. Used only to make the stub reproducible. */
function hash(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return h >>> 0;
}

/**
 * The offline stub's answers. Deterministic in the question object, which
 * embeds the record's own reference path and the option set, so the same record
 * gets the same answer at any batch size. These answers are synthetic padding
 * for exercising the plumbing: they are not recorded from any run and they
 * cannot be right or wrong.
 */
function stubEvaluator(): Evaluator {
  return new StubEvaluator({
    script: (request: Request) => {
      const answers: Record<string, Answer> = {};
      for (const [key, question] of Object.entries(request.questions) as [string, Question][]) {
        if (question.type !== 'choice') continue;
        const options = Object.keys(question.criteria);
        const h = hash(JSON.stringify(question));
        // 0.70 to 1.00, so some cells land under the 0.80 gate and the accepted
        // and review groups both get exercised.
        const confidence = Math.round((0.70 + ((h >>> 8) % 31) / 100) * 100) / 100;
        answers[key] = choiceAnswer(options[h % options.length], confidence, options);
      }
      return { model: 'stub-offline', answers };
    }
  });
}

async function readSmallFile(path: string, limit: number, what: string): Promise<string> {
  let info;
  try { info = await stat(path); }
  catch { throw new CliError(`Cannot read the ${what} file; provide a readable regular file`); }
  if (!info.isFile()) throw new CliError(`The ${what} path must be a regular file`);
  if (info.size > limit) throw new CliError(`The ${what} file exceeds ${limit} bytes; split it into smaller runs`);
  try { return await readFile(path, 'utf8'); }
  catch { throw new CliError(`Cannot read the ${what} file`); }
}

export function parseRecords(text: string): SweepInputRecord[] {
  const records: SweepInputRecord[] = [];
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    let parsed: unknown;
    try { parsed = JSON.parse(line); }
    catch { throw new CliError(`Records line ${i + 1} is not valid JSON`); }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new CliError(`Records line ${i + 1} is not a JSON object`);
    const record = parsed as Record<string, unknown>;
    if (typeof record.text !== 'string' || !record.text.trim()) throw new CliError(`Records line ${i + 1} has no text`);
    if (record.id !== undefined && (typeof record.id !== 'string' || !record.id.trim())) throw new CliError(`Records line ${i + 1} has a non-string id`);
    records.push({ id: record.id as string | undefined, text: record.text, meta: record.meta });
  }
  if (!records.length) throw new CliError('The records file contains no records');
  return records;
}

export function parseQuestions(text: string): SweepQuestion[] {
  let parsed: unknown;
  try { parsed = JSON.parse(text); }
  catch { throw new CliError('The questions file is not valid JSON'); }
  const list = Array.isArray(parsed) ? parsed : (parsed as { questions?: unknown })?.questions;
  if (!Array.isArray(list)) throw new CliError('The questions file must be an array of questions, or an object with a "questions" array');
  return list as SweepQuestion[];
}

/** Create a file that must not already exist, so a rerun never overwrites. */
async function writeNew(dir: string, name: string, body: string): Promise<void> {
  let handle;
  try { handle = await open(join(dir, name), 'wx', 0o600); }
  catch { throw new CliError(`${name} already exists in the output directory; choose a new --out directory`); }
  try { await handle.writeFile(body); } finally { await handle.close(); }
}

function number(raw: string | undefined, flag: string): number {
  const value = Number(raw);
  if (!raw || !Number.isFinite(value)) throw new CliError(`${flag} needs a number`);
  return value;
}

try {
  const args = process.argv.slice(2);
  if (!args.length || args.includes('--help')) { console.log(usage); process.exit(0); }

  let recordsPath = '', questionsPath = '', outDir = '';
  let maxInputTokens: number | undefined, batch: number | undefined, gate: number | undefined;
  let dryRun = false, stub = false;
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    const next = () => { const v = args[++i]; if (!v || v.startsWith('--')) throw new CliError(`${flag} needs a value`); return v; };
    if (flag === '--records') { if (recordsPath) throw new CliError('Repeated --records'); recordsPath = resolve(next()); }
    else if (flag === '--questions') { if (questionsPath) throw new CliError('Repeated --questions'); questionsPath = resolve(next()); }
    else if (flag === '--out') { if (outDir) throw new CliError('Repeated --out'); outDir = resolve(next()); }
    else if (flag === '--budget') { if (maxInputTokens !== undefined) throw new CliError('Repeated --budget'); maxInputTokens = number(next(), '--budget'); }
    else if (flag === '--batch') { if (batch !== undefined) throw new CliError('Repeated --batch'); batch = number(next(), '--batch'); }
    else if (flag === '--gate') { if (gate !== undefined) throw new CliError('Repeated --gate'); gate = number(next(), '--gate'); }
    else if (flag === '--dry-run') dryRun = true;
    else if (flag === '--stub') stub = true;
    else throw new CliError(`Unknown argument ${flag}\n\n${usage}`);
  }
  if (!recordsPath || !questionsPath) throw new CliError(usage);
  if (!outDir && !dryRun) throw new CliError('--out is required unless --dry-run');
  if (gate !== undefined && (gate <= 0 || gate > 1)) throw new CliError('--gate must be greater than 0 and at most 1');
  if (batch !== undefined && (!Number.isInteger(batch) || batch < 1)) throw new CliError('--batch must be a positive integer');
  // The key check happens before any file is read, so a run that cannot
  // possibly reach the provider fails immediately and cheaply.
  if (!dryRun && !stub && !process.env.TYPESAFE_API_KEY) throw new CliError('Set TYPESAFE_API_KEY to run a live sweep, or use --dry-run or --stub');

  const records = parseRecords(await readSmallFile(recordsPath, MAX_RECORDS_BYTES, 'records'));
  const questions = parseQuestions(await readSmallFile(questionsPath, MAX_QUESTIONS_BYTES, 'questions'));
  const config = {
    records, questions, gate,
    budget: { ...(maxInputTokens !== undefined ? { maxInputTokens } : {}), ...(batch !== undefined ? { maxRecordsPerCall: batch } : {}) }
  };

  let plan;
  try { plan = planSweep(config); }
  catch (error) { throw new CliError((error as Error).message); }
  const planText = formatSweepPlan(plan);
  console.log(planText);

  if (outDir) {
    try { await mkdir(outDir, { recursive: true, mode: 0o700 }); }
    catch { throw new CliError('Cannot create the output directory'); }
  }
  const planJson = {
    mode: dryRun ? 'dry-run' : stub ? 'stub' : 'live',
    records: plan.records.length,
    questions: plan.questionNames,
    cells: plan.records.length * plan.questionNames.length,
    calls: plan.plan.calls.length,
    recordsPerCall: plan.effectiveRecordsPerCall,
    recordsPerCallReason: plan.recordsPerCallReason,
    maxQuestionsPerCall: plan.maxQuestionsPerCall,
    gate: plan.gate,
    estimatedInputTokens: plan.plan.totalEstimatedInputTokens,
    estimatedOutputTokens: plan.totalCells * 30,
    estimateBasis: 'characters over four, the planner estimator; not the provider tokenizer and not a billing figure',
    perCall: plan.plan.calls.map(c => ({ index: c.index, records: c.recordIds.length, questions: c.recordIds.length * plan.questionNames.length, estimatedInputTokens: c.estimatedInputTokens })),
    oversizedRecordIds: plan.plan.oversizedRecordIds
  };

  if (dryRun) {
    if (outDir) { await writeNew(outDir, 'plan.json', JSON.stringify(planJson, null, 2) + '\n'); console.error(`Saved ${join(outDir, 'plan.json')}`); }
    console.error('Dry run: nothing was sent, no provider was called.');
    process.exit(0);
  }

  const evaluator: Evaluator = stub ? stubEvaluator() : new Jev();
  const run = await runSweep(config, evaluator);

  await writeNew(outDir, 'plan.json', JSON.stringify(planJson, null, 2) + '\n');
  await writeNew(outDir, 'results.jsonl', run.results.map(r => JSON.stringify({
    id: r.id, outcome: r.kind, meta: r.meta,
    answers: Object.fromEntries(r.cells.map(c => [c.questionName, {
      key: c.logicalKey, choice: c.choice, confidence: c.confidence, probabilities: c.probabilities, outcome: c.kind, reason: c.reason
    }]))
  })).join('\n') + '\n');
  await writeNew(outDir, 'manifest.json', JSON.stringify({
    totalRecords: run.manifest.totalRecords,
    complete: run.manifest.complete,
    problems: run.manifest.problems,
    counts: Object.fromEntries(Object.entries(run.manifest.byKind).map(([kind, ids]) => [kind, ids.length])),
    byKind: run.manifest.byKind
  }, null, 2) + '\n');
  await writeNew(outDir, 'cost.json', JSON.stringify({
    ...run.cost, model: run.model,
    note: run.cost.estimated ? 'token figures came from the planner estimator, not the provider usage block; not a billing figure' : 'token figures came from the provider usage block'
  }, null, 2) + '\n');
  await writeNew(outDir, 'report.md', formatSweepReport(run, { source: recordsPath }));

  const accepted = run.results.filter(r => r.kind === 'accepted').length;
  console.error(`Wrote results.jsonl, manifest.json, cost.json, report.md and plan.json to ${outDir}`);
  console.error(`${accepted} accepted, ${run.results.length - accepted} for review, manifest complete=${run.manifest.complete}`);
  if (run.errors.length) console.error(`${run.errors.length} validation or mapping problem(s) recorded in report.md`);
  if (!run.manifest.complete) { console.error('The coverage manifest is INCOMPLETE; do not treat this run as finished.'); process.exitCode = 1; }
} catch (error) {
  console.error(error instanceof CliError ? error.message : 'Sweep failed; check the input files and the output directory');
  process.exitCode = 1;
}
