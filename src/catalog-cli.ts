#!/usr/bin/env node
/**
 * `npm run catalog` — the maintenance door for fetch v2 catalogs.
 *
 *   npm run catalog -- validate <catalog.json>
 *   npm run catalog -- learn <ledger.jsonl> <catalog.json> [--out proposals.json]
 *   npm run catalog -- build <skills-dir> <out.json>
 *
 * `validate` reports records with fewer than three utterances, the same
 * phrasing claimed by two records, and a negative that is word-for-word
 * another record's utterance — see `src/enhance/catalog.ts`.
 *
 * `learn` never edits the catalog. It reads a fetch ledger (one JSON object
 * per line, written by `npm run fetch -- --record <id>`) and, for every
 * logged request where the human's chosen id was NOT the run's own top pick,
 * proposes the request text as a new utterance on the record that should
 * have won. The proposals go to a separate file for a human to review and
 * merge by hand.
 *
 * `build` generates a fresh catalog v2 file from a Claude Code skills
 * directory (one subdirectory per skill, each holding a SKILL.md), deriving
 * each record from the skill's own frontmatter description and any
 * Trigger/Use-when lines — see `src/catalog-build-cli.ts` for the generator.
 * `npm run catalog:build` remains as a direct alias to the same generator.
 */
import { readFile, stat, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { formatCatalogValidation, parseCatalogText, validateCatalog, type CatalogRecord } from './enhance/catalog.ts';
import { buildCatalog, usage as buildUsage } from './catalog-build-cli.ts';

class CliError extends Error {}

const usage = `super-jev catalog <validate|learn|build> ...

  validate <catalog.json>
    Reports records with fewer than three utterances, the same phrasing
    claimed by two records, and a negative that collides with another
    record's utterance. Exit 0 when clean, 1 when any finding is reported,
    2 on a bad file.

  learn <ledger.jsonl> <catalog.json> [--out FILE]
    Reads a fetch ledger and proposes new utterances per record from
    requests where the human's chosen id differed from the run's own top
    pick. Writes a proposals file; never edits the catalog. --out defaults
    to proposals.json next to the ledger file.

  build <skills-dir> <out.json>
    Walks a Claude Code skills directory and writes a fresh catalog v2 file,
    one record per skill, from each skill's own SKILL.md. See
    "catalog build --help" for the record shape. Validate the result with
    "catalog validate <out.json>".

  --json   Print one JSON object to stdout and nothing else (validate/learn).`;

const MAX_FILE_BYTES = 32 * 1024 * 1024;

async function readSmallFile(path: string, what: string): Promise<string> {
  let info;
  try { info = await stat(path); }
  catch { throw new CliError(`Cannot read the ${what} file; provide a readable regular file`); }
  if (!info.isFile()) throw new CliError(`The ${what} path must be a regular file`);
  if (info.size > MAX_FILE_BYTES) throw new CliError(`The ${what} file exceeds ${MAX_FILE_BYTES} bytes`);
  try { return await readFile(path, 'utf8'); }
  catch { throw new CliError(`Cannot read the ${what} file`); }
}

type LedgerEntry = {
  request: string;
  context?: string[];
  ranked: { id: string; score: number; confidence: number }[];
  chosen: string;
  ts: string;
};

function parseLedger(text: string): LedgerEntry[] {
  const out: LedgerEntry[] = [];
  const lines = text.split('\n');
  for (const [i, raw] of lines.entries()) {
    const line = raw.trim();
    if (!line) continue;
    let parsed: unknown;
    try { parsed = JSON.parse(line); }
    catch { throw new CliError(`Ledger line ${i + 1} is not valid JSON`); }
    const entry = parsed as Record<string, unknown>;
    if (typeof entry.request !== 'string' || typeof entry.chosen !== 'string' || !Array.isArray(entry.ranked)) {
      throw new CliError(`Ledger line ${i + 1} is missing "request", "chosen" or "ranked"`);
    }
    out.push(entry as unknown as LedgerEntry);
  }
  return out;
}

type Proposal = { id: string; proposedUtterances: string[]; fromRequests: number };

/**
 * Never edits the catalog. For every ledger line where `chosen` was not the
 * run's own top pick (`ranked[0]?.id`), the request text is proposed as a
 * new utterance for `chosen` — deduped, and skipped when it is already one
 * of that record's utterances.
 */
export function proposeUtterances(ledger: LedgerEntry[], catalog: CatalogRecord[]): Proposal[] {
  const existing = new Map<string, Set<string>>();
  for (const r of catalog) existing.set(r.id, new Set((r.utterances ?? []).map(u => u.trim().toLowerCase())));
  const proposedByRecord = new Map<string, Set<string>>();
  const seenIds = new Set(catalog.map(r => r.id));

  for (const entry of ledger) {
    const topPick = entry.ranked[0]?.id;
    if (topPick === entry.chosen) continue; // the run already got it right; nothing to learn
    if (!seenIds.has(entry.chosen)) continue; // chosen id no longer in this catalog; nothing to propose it onto
    const key = entry.request.trim();
    if (!key) continue;
    if (existing.get(entry.chosen)?.has(key.toLowerCase())) continue; // already an utterance
    const bucket = proposedByRecord.get(entry.chosen) ?? new Set<string>();
    bucket.add(key);
    proposedByRecord.set(entry.chosen, bucket);
  }

  const proposals: Proposal[] = [];
  for (const [id, texts] of proposedByRecord) {
    proposals.push({ id, proposedUtterances: [...texts].sort(), fromRequests: texts.size });
  }
  proposals.sort((a, b) => a.id.localeCompare(b.id));
  return proposals;
}

async function runValidate(args: string[]): Promise<number> {
  let json = false;
  const positional: string[] = [];
  for (const a of args) { if (a === '--json') json = true; else if (a.startsWith('--')) throw new CliError(`Unknown argument ${a}`); else positional.push(a); }
  if (positional.length !== 1) throw new CliError(usage);
  const path = resolve(positional[0]);
  const text = await readSmallFile(path, 'catalog');
  let records;
  try { records = parseCatalogText(text); }
  catch (error) { throw new CliError((error as Error).message); }
  const report = validateCatalog(records);
  if (json) process.stdout.write(JSON.stringify(report) + '\n');
  else console.log(formatCatalogValidation(report));
  return report.clean ? 0 : 1;
}

async function runLearn(args: string[]): Promise<number> {
  let json = false, outPath = '';
  const positional: string[] = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--json') json = true;
    else if (a === '--out') { const v = args[++i]; if (!v) throw new CliError('--out needs a value'); outPath = resolve(v); }
    else if (a.startsWith('--')) throw new CliError(`Unknown argument ${a}`);
    else positional.push(a);
  }
  if (positional.length !== 2) throw new CliError(usage);
  const [ledgerPath, catalogPath] = positional.map(p => resolve(p));
  const ledgerText = await readSmallFile(ledgerPath, 'ledger');
  const catalogText = await readSmallFile(catalogPath, 'catalog');
  const ledger = parseLedger(ledgerText);
  let catalog;
  try { catalog = parseCatalogText(catalogText); }
  catch (error) { throw new CliError((error as Error).message); }

  const proposals = proposeUtterances(ledger, catalog);
  const out = outPath || resolve(ledgerPath, '..', 'proposals.json');
  const body = JSON.stringify({
    note: 'Proposed new utterances, mined from ledger entries where the human\'s chosen id differed from the run\'s own top pick. The catalog file was NOT edited; review and merge by hand.',
    ledgerFile: ledgerPath,
    catalogFile: catalogPath,
    generatedAt: new Date().toISOString(),
    proposals
  }, null, 2) + '\n';
  await writeFile(out, body, { mode: 0o600 });

  if (json) { process.stdout.write(JSON.stringify({ proposals, out }) + '\n'); return 0; }
  console.log(`catalog learn: ${ledger.length} ledger line(s), ${proposals.length} record(s) with new proposed utterance(s)`);
  for (const p of proposals) console.log(`  ${p.id}: +${p.proposedUtterances.length}`);
  console.log(`Wrote ${out}`);
  return 0;
}

async function runBuild(args: string[]): Promise<number> {
  if (!args.length || args.includes('--help')) { console.log(buildUsage); return 0; }
  const positional = args.filter((a) => !a.startsWith('--'));
  if (positional.length < 2) throw new CliError(buildUsage);

  const skillsDir = resolve(positional[0]);
  const outPath = resolve(positional[1]);

  let dirInfo;
  try { dirInfo = await stat(skillsDir); }
  catch { throw new CliError(`Skills directory does not exist: ${skillsDir}`); }
  if (!dirInfo.isDirectory()) throw new CliError(`Not a directory: ${skillsDir}`);

  const records = await buildCatalog(skillsDir);
  await writeFile(outPath, JSON.stringify(records, null, 2) + '\n', 'utf8');
  console.error(`Wrote ${records.length} catalog record(s) to ${outPath}`);
  return 0;
}

async function main(): Promise<number> {
  const args = process.argv.slice(2);
  if (!args.length || args.includes('--help')) { console.log(usage); return 0; }
  const [sub, ...rest] = args;
  if (sub === 'validate') return runValidate(rest);
  if (sub === 'learn') return runLearn(rest);
  if (sub === 'build') return runBuild(rest);
  throw new CliError(`Unknown subcommand ${sub}\n\n${usage}`);
}

main().then(code => { process.exitCode = code; }).catch(error => {
  console.error(error instanceof CliError ? error.message : 'catalog command failed; check the input files and the output path');
  process.exitCode = error instanceof CliError ? 1 : 2;
});
