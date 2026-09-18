#!/usr/bin/env node
/**
 * `npm run fetch` — pull, not push (wishlist item 6).
 *
 * Score a whole catalog (a skills index, a tools catalog, a brain INDEX)
 * against one plain request, and print only the top-k ids, instead of
 * injecting the full catalog every turn. Built on `runFetch` in
 * `src/enhance/fetch.ts`, which is itself one relevance question run through
 * the same `planSweep` / `runSweep` engine `npm run sweep` uses.
 */
import { mkdir, open, readFile, stat, writeFile } from 'node:fs/promises';
import { join, resolve, dirname } from 'node:path';
import { Jev } from './jev.ts';
import { StubEvaluator, choiceAnswer } from './enhance/stub.ts';
import {
  DEFAULT_FETCH_MAX_INPUT_TOKENS, DEFAULT_K, DEFAULT_PREFILTER, DEFAULT_CONTEXT_TURNS, DEFAULT_FETCH_FLOOR,
  applyNoneGate, formatFetchPlan, planFetch, runFetch, type FetchCatalogEntry
} from './enhance/fetch.ts';
import { CatalogError, parseCatalogText } from './enhance/catalog.ts';
import type { Answer, Evaluator, Question, Request } from './types.ts';

// Only deliberate, local diagnostics are printed. A raw parser or filesystem
// exception can embed catalog contents or sensitive paths.
class CliError extends Error {}

const usage = `super-jev fetch --catalog CATALOG.json --request "<text>" [options]

Scores the catalog against the request and prints the top-k ids and their
scores. Pull, not push: only the top few records are ever meant to be
loaded by the caller. A cheap local narrowing pass (BM25-lite over text and,
for a v2 catalog, utterances weighted higher, minus negatives, plus recent
--context turns at a lower weight) trims the catalog to the top N first, so
a typical run is ONE provider call. Every record is judged against a "none
of these" option; a record only ranks when it beats it. The none gate then
applies a confidence floor: below it, or when nothing beat none, the result
is a clarifying question instead of a best guess.

  --catalog FILE   JSON: an array of {"id","text",...} records (v1 {id,text}
                   or v2 with optional "utterances"/"negatives"/"tags"), or
                   {"catalog":[...]}. This is the whole index — a skills
                   list, a tools catalog, a brain INDEX.
  --request TEXT   The plain-language request to score the catalog against.
  --context FILE   JSON array of recent user turns, oldest first. The last
                   --context-turns of them are folded into local narrowing
                   at a lower weight, so a referent like "restart it"
                   inherits its subject from the prior turn.
  --context-turns N  How many trailing turns to keep from --context. Default ${DEFAULT_CONTEXT_TURNS}.
  --k       N      How many top ids to return. Default ${DEFAULT_K}.
  --prefilter N    Keep only the top N records by local narrowing before the
                   provider call. Default ${DEFAULT_PREFILTER}; 0 disables.
  --floor   N      Confidence floor for the none gate, in [0,1]. Below it the
                   result asks a clarifying question instead of acting.
                   Default ${DEFAULT_FETCH_FLOOR}.
  --record  ID     After scoring, log {request, context, ranked, chosen: ID,
                   ts} to --ledger, for the feedback loop ("npm run catalog
                   -- learn"). Does not change the printed result.
  --ledger  FILE   Ledger path for --record. Default .superjev/fetch-ledger.jsonl.
  --out     DIR    Output directory. Files are created; a second run to the
                    same directory overwrites rather than failing.
  --budget  N      maxInputTokens per call. Default ${DEFAULT_FETCH_MAX_INPUT_TOKENS}.
  --batch   N      Max records per call.
  --dry-run        Print the plan and cost. Zero network.
  --stub           Run against the offline stub. Synthetic answers, zero
                   network, no API key. Never evidence about ranking quality.
  --json           Print one JSON object to stdout and nothing else.

Live mode is the default and needs TYPESAFE_API_KEY. It sends catalog text
to TypeSafe. Exit codes: 0 ok, 1 usage, 2 transport failure.`;

const MAX_CATALOG_BYTES = 32 * 1024 * 1024;
const MAX_CONTEXT_BYTES = 1 * 1024 * 1024;

/** Deterministic 32-bit FNV-1a. Used only to make the stub reproducible. */
function hash(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return h >>> 0;
}

/**
 * The offline stub's answers. Deterministic in the question object, which
 * embeds the record's own reference path and the request text, so the same
 * record and request pair gets the same answer at any batch size. Synthetic
 * padding for exercising the plumbing: not evidence about ranking quality.
 */
function stubEvaluator(): Evaluator {
  return new StubEvaluator({
    script: (request: Request) => {
      const answers: Record<string, Answer> = {};
      for (const [key, question] of Object.entries(request.questions) as [string, Question][]) {
        if (question.type !== 'choice') continue;
        const options = Object.keys(question.criteria);
        const h = hash(JSON.stringify(question));
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

/** Parses v1 ({id,text}) and v2 (+utterances/negatives/tags) catalogs alike. See `src/enhance/catalog.ts`. */
export function parseCatalog(text: string): FetchCatalogEntry[] {
  try { return parseCatalogText(text); }
  catch (error) { throw new CliError(error instanceof CatalogError ? error.message : 'The catalog file is not valid'); }
}

/** JSON array of recent user turns, oldest first. Every entry must be a string. */
function parseContext(text: string): string[] {
  let parsed: unknown;
  try { parsed = JSON.parse(text); }
  catch { throw new CliError('The context file is not valid JSON'); }
  if (!Array.isArray(parsed) || !parsed.every(v => typeof v === 'string')) throw new CliError('The context file must be a JSON array of strings (recent user turns, oldest first)');
  return parsed as string[];
}

/** Overwrite on rerun, on purpose: a fetch is meant to be re-run every turn. */
async function writeOut(dir: string, name: string, body: string): Promise<void> {
  const handle = await open(join(dir, name), 'w', 0o600);
  try { await handle.writeFile(body); } finally { await handle.close(); }
}

function number(raw: string | undefined, flag: string): number {
  const value = Number(raw);
  if (!raw || !Number.isFinite(value)) throw new CliError(`${flag} needs a number`);
  return value;
}

async function main(): Promise<number> {
  const args = process.argv.slice(2);
  if (!args.length || args.includes('--help')) { console.log(usage); return 0; }

  let catalogPath = '', request = '', outDir = '', contextPath = '', recordId = '', ledgerPath = '';
  let maxInputTokens: number | undefined, batch: number | undefined, k: number | undefined, prefilter: number | undefined;
  let contextTurns: number | undefined, floor: number | undefined;
  let dryRun = false, stub = false, json = false;
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    const next = () => { const v = args[++i]; if (!v || v.startsWith('--')) throw new CliError(`${flag} needs a value`); return v; };
    if (flag === '--catalog') { if (catalogPath) throw new CliError('Repeated --catalog'); catalogPath = resolve(next()); }
    else if (flag === '--request') { if (request) throw new CliError('Repeated --request'); request = next(); }
    else if (flag === '--context') { if (contextPath) throw new CliError('Repeated --context'); contextPath = resolve(next()); }
    else if (flag === '--context-turns') { if (contextTurns !== undefined) throw new CliError('Repeated --context-turns'); contextTurns = number(next(), '--context-turns'); }
    else if (flag === '--k') { if (k !== undefined) throw new CliError('Repeated --k'); k = number(next(), '--k'); }
    else if (flag === '--prefilter') { if (prefilter !== undefined) throw new CliError('Repeated --prefilter'); prefilter = number(next(), '--prefilter'); }
    else if (flag === '--floor') { if (floor !== undefined) throw new CliError('Repeated --floor'); floor = number(next(), '--floor'); }
    else if (flag === '--record') { if (recordId) throw new CliError('Repeated --record'); recordId = next(); }
    else if (flag === '--ledger') { if (ledgerPath) throw new CliError('Repeated --ledger'); ledgerPath = resolve(next()); }
    else if (flag === '--out') { if (outDir) throw new CliError('Repeated --out'); outDir = resolve(next()); }
    else if (flag === '--budget') { if (maxInputTokens !== undefined) throw new CliError('Repeated --budget'); maxInputTokens = number(next(), '--budget'); }
    else if (flag === '--batch') { if (batch !== undefined) throw new CliError('Repeated --batch'); batch = number(next(), '--batch'); }
    else if (flag === '--dry-run') dryRun = true;
    else if (flag === '--stub') stub = true;
    else if (flag === '--json') json = true;
    else throw new CliError(`Unknown argument ${flag}\n\n${usage}`);
  }
  if (!catalogPath) throw new CliError(usage);
  if (!request || !request.trim()) throw new CliError('--request needs a non-empty value');
  if (k !== undefined && (!Number.isInteger(k) || k < 1)) throw new CliError('--k must be a positive integer');
  if (batch !== undefined && (!Number.isInteger(batch) || batch < 1)) throw new CliError('--batch must be a positive integer');
  if (prefilter !== undefined && (!Number.isInteger(prefilter) || prefilter < 0)) throw new CliError('--prefilter must be a non-negative integer (0 disables)');
  if (contextTurns !== undefined && (!Number.isInteger(contextTurns) || contextTurns < 0)) throw new CliError('--context-turns must be a non-negative integer');
  if (floor !== undefined && (!Number.isFinite(floor) || floor < 0 || floor > 1)) throw new CliError('--floor must be a number in [0,1]');
  if (ledgerPath && !recordId) throw new CliError('--ledger only applies with --record');
  // The key check happens before any file is read, so a run that cannot
  // possibly reach the provider fails immediately and cheaply.
  if (!dryRun && !stub && !process.env.TYPESAFE_API_KEY) throw new CliError('Set TYPESAFE_API_KEY to run a live fetch, or use --dry-run or --stub');

  const catalog = parseCatalog(await readSmallFile(catalogPath, MAX_CATALOG_BYTES, 'catalog'));
  const contextTurnsN = contextTurns ?? DEFAULT_CONTEXT_TURNS;
  const context = contextPath ? parseContext(await readSmallFile(contextPath, MAX_CONTEXT_BYTES, 'context')).slice(-contextTurnsN) : undefined;
  const floorN = floor ?? DEFAULT_FETCH_FLOOR;
  const options = {
    k, prefilter, context,
    ...(maxInputTokens !== undefined || batch !== undefined ? { budget: { ...(maxInputTokens !== undefined ? { maxInputTokens } : {}), ...(batch !== undefined ? { maxRecordsPerCall: batch } : {}) } } : {})
  };

  let plan;
  try { plan = planFetch(catalog, request, options); }
  catch (error) { throw new CliError((error as Error).message); }

  if (outDir) {
    try { await mkdir(outDir, { recursive: true, mode: 0o700 }); }
    catch { throw new CliError('Cannot create the output directory'); }
  }

  const planJson = {
    mode: dryRun ? 'dry-run' : stub ? 'stub' : 'live',
    catalogSize: plan.catalogSize,
    request,
    k: plan.k,
    prefilter: plan.prefilter,
    calls: plan.plan.plan.calls.length,
    recordsPerCall: plan.plan.effectiveRecordsPerCall,
    recordsPerCallReason: plan.plan.recordsPerCallReason,
    estimatedInputTokens: plan.plan.plan.totalEstimatedInputTokens,
    estimatedOutputTokens: plan.plan.totalCells * 30,
    estimateBasis: 'characters over four, the planner estimator; not the provider tokenizer and not a billing figure',
    oversizedRecordIds: plan.plan.plan.oversizedRecordIds
  };

  if (dryRun) {
    if (json) { process.stdout.write(JSON.stringify(planJson) + '\n'); return 0; }
    console.log(formatFetchPlan(plan));
    if (outDir) { await writeOut(outDir, 'plan.json', JSON.stringify(planJson, null, 2) + '\n'); console.error(`Saved ${join(outDir, 'plan.json')}`); }
    console.error('Dry run: nothing was sent, no provider was called.');
    return 0;
  }

  if (!json) console.log(formatFetchPlan(plan));

  const transport: Evaluator = stub ? stubEvaluator() : new Jev();
  let run;
  try { run = await runFetch(catalog, request, { ...options, transport }); }
  catch (error) {
    console.error(`Fetch transport failed: ${(error as Error).message}`);
    return 2;
  }

  const gate = applyNoneGate(run, floorN);

  const resultJson = {
    mode: stub ? 'stub' : 'live',
    request,
    context: context ?? [],
    k: run.plan.k,
    catalogSize: run.plan.catalogSize,
    prefilter: run.plan.prefilter,
    floor: floorN,
    ranked: run.ranked,
    noMatch: run.noMatch,
    noMatchConfidence: run.noMatchConfidence,
    // The none gate: below the confidence floor, or when nothing beat "none
    // of these" at all, `gated` is true and `candidates`/`ask` are the
    // closest things to offer a human instead of a guess. See --json in the
    // usage text — this is the shape a caller should branch on.
    gated: gate.noMatch,
    candidates: gate.noMatch ? gate.candidates : [],
    ask: gate.noMatch ? gate.ask : null,
    calls: run.calls,
    model: run.model,
    manifestComplete: run.manifest.complete,
    errors: run.errors
  };

  if (recordId) {
    const path = ledgerPath || resolve('.superjev', 'fetch-ledger.jsonl');
    try {
      await mkdir(dirname(path), { recursive: true, mode: 0o700 });
      const line = JSON.stringify({ request, context: context ?? [], ranked: run.ranked, chosen: recordId, ts: new Date().toISOString() }) + '\n';
      const handle = await open(path, 'a', 0o600);
      try { await handle.writeFile(line); } finally { await handle.close(); }
    } catch { throw new CliError('Cannot write the ledger file'); }
    if (!json) console.error(`Recorded chosen=${recordId} to ${path}`);
  }

  if (outDir) {
    await writeOut(outDir, 'ranked.json', JSON.stringify(resultJson, null, 2) + '\n');
    await writeOut(outDir, 'manifest.json', JSON.stringify({
      totalRecords: run.manifest.totalRecords,
      complete: run.manifest.complete,
      problems: run.manifest.problems,
      counts: Object.fromEntries(Object.entries(run.manifest.byKind).map(([kind, ids]) => [kind, ids.length]))
    }, null, 2) + '\n');
    await writeOut(outDir, 'cost.json', JSON.stringify({
      ...run.cost, model: run.model,
      note: run.cost.estimated ? 'token figures came from the planner estimator, not the provider usage block; not a billing figure' : 'token figures came from the provider usage block'
    }, null, 2) + '\n');
  }

  if (json) { process.stdout.write(JSON.stringify(resultJson) + '\n'); return run.manifest.complete ? 0 : 1; }

  if (run.noMatch) console.error(`No match: "none of these" won for every record judged (confidence ${run.noMatchConfidence.toFixed(2)}); nothing in the catalog serves this request.`);
  else console.error(`Top ${run.ranked.length} of ${run.plan.catalogSize}: ${run.ranked.map(r => `${r.id}=${r.score.toFixed(2)}`).join(', ') || '(none)'}`);
  if (gate.noMatch) console.error(`Gate: below floor ${floorN.toFixed(2)} — ${gate.ask}`);
  console.error(`calls: ${run.calls}${run.plan.prefilter.dropped ? ` (prefilter dropped ${run.plan.prefilter.dropped} of ${run.plan.catalogSize} locally)` : ''}`);
  if (outDir) console.error(`Wrote ranked.json, manifest.json and cost.json to ${outDir}`);
  if (run.errors.length) console.error(`${run.errors.length} validation or mapping problem(s) recorded`);
  if (!run.manifest.complete) { console.error('The coverage manifest is INCOMPLETE; do not treat this run as finished.'); return 1; }
  return 0;
}

main().then(code => { process.exitCode = code; }).catch(error => {
  console.error(error instanceof CliError ? error.message : 'Fetch failed; check the input files and the output directory');
  process.exitCode = error instanceof CliError ? 1 : 2;
});
