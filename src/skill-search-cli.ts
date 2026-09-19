#!/usr/bin/env node
/**
 * `node src/skill-search-cli.ts --roots-file ROOTS.json --request-file REQUEST.json [--local-only]`
 *
 * Default skill discovery entry: suggest which installed skills serve one
 * plain request, without executing anything. Advisory only — the caller loads
 * the chosen SKILL.md itself and decides what to run. Skill files are read
 * locally only to extract frontmatter metadata; bodies are never sent to the
 * judge and never printed. Retrieval returns suggestions, never permission to
 * execute: a same-technique/different-purpose match (rent vs a generic
 * browser skill) stays advisory, never auto.
 *
 * ROOTS.json is a JSON array of trusted, absolute skill-directory paths
 * (caller-owned). REQUEST.json is {"request": "<original user wording>",
 * "context": ["<recent turn>", ...]} with the necessary recent turns.
 *
 * One compact JSON object goes to stdout and nothing else:
 *   {status, candidates, source, error?, model?, usage?}
 * where status is one of:
 *   exact       — a /slash-name or unambiguous exact skill name resolved
 *                 locally; no judge call was made.
 *   suggestions — advisory top-3 (judge-ranked, or locally ranked).
 *   no_match    — nothing under the roots serves this request.
 *   fallback    — the judge path failed (no key, timeout, transport error,
 *                 unusable or incomplete answers); candidates are local-only
 *                 and clearly marked as such. A service failure is NEVER
 *                 reported as no_match.
 *   clarify     — the request is too thin to search on, or the judge's top
 *                 pick was too close to call; candidates are the closest
 *                 things found, if any.
 * source is "local" when no judge call was made and "jev" when one was.
 *
 * Scanning: SKILL.md / skill.md in each root itself and in first-level
 * subdirectories only — no arbitrary filesystem recursion. Duplicate skill
 * names resolve by deterministic root precedence (first root wins); the
 * winner's path is retained as provenance. Skills with empty or malformed
 * descriptions are excluded and counted, never sent anywhere.
 *
 * Exact-name resolution (exact normalized name only -- never a fuzzy
 * near-miss) and the narrow underspecified-request check are local and
 * deterministic. Anything else goes through the bounded judge path over the
 * top 40 local matches: one attempt per batch (maxRetries 0) under a fixed
 * timeout, with all dependent call costs collected into `usage`. Batch
 * packing may split the work into more than one provider call; the bound is
 * one attempt per batch, not a call count.
 */
import { readFile, readdir, stat } from 'node:fs/promises';
import { basename, isAbsolute, join, resolve } from 'node:path';
import { Jev } from './jev.ts';
import { extractDescription } from './catalog-build-cli.ts';
import {
  DEFAULT_CONTEXT_TURNS, DEFAULT_FETCH_FLOOR, DEFAULT_FETCH_MARGIN,
  applyNoneGate, prefilterCatalog, runFetch, tokenize,
  type FetchCatalogEntry
} from './enhance/fetch.ts';
import type { Evaluator } from './types.ts';

class CliError extends Error {}

/** Short descriptions are capped, mirroring catalog-build-cli's TEXT_CAP. */
export const DESCRIPTION_CAP = 300;
/** Local narrowing keeps the top N records before the judge path. */
export const LOCAL_NARROW_N = 40;
/** At most three candidates are ever returned. */
export const JUDGE_TOP_K = 3;
/** Fixed, bounded judge timeout for the CLI (one attempt per batch). */
export const SKILL_SEARCH_TIMEOUT_MS = 30_000;
/** Longer requests never reach the judge; the caller shortens them first. */
export const MAX_REQUEST_CHARS = 4000;
/** Request plus recent context, combined, must fit in this before the judge. */
export const MAX_INPUT_CHARS = 8000;
/** Input file size guard. */
export const MAX_FILE_BYTES = 1 * 1024 * 1024;

export type SkillSearchStatus = 'exact' | 'suggestions' | 'no_match' | 'fallback' | 'clarify';
export type SkillCandidate = { id: string; name: string; path: string; description: string };
export type SkillSearchUsage = {
  calls: number;
  inputTokens: number;
  outputTokens: number;
  retries: number;
  estimated: boolean;
};
export type SkillSearchResult = {
  status: SkillSearchStatus;
  candidates: SkillCandidate[];
  source: 'local' | 'jev';
  /** Skills excluded for an empty/malformed description or an unreadable skill file. */
  metadataSkipped: number;
  /** Same skill name under a later root; the first root's copy won. */
  duplicateCount: number;
  error?: string;
  model?: string;
  usage?: SkillSearchUsage;
};
export type SkillEntry = { id: string; name: string; path: string; description: string };
export type SkillSearchDiagnostics = {
  rootsScanned: number;
  skillsFound: number;
  /** Skills excluded for an empty/malformed description or an unreadable skill file. */
  skipped: number;
  /** Same skill name under a later root; the first root's copy won. */
  duplicates: number;
};
export type SkillSearchOptions = {
  roots: string[];
  request: string;
  context?: string[];
  localOnly?: boolean;
  /** Injected fake transport for unit tests; the lead runs live. */
  transport?: Evaluator;
  /** Defaults to TYPESAFE_API_KEY. Tests pass a dummy value with a fake transport. */
  apiKey?: string;
  /** Test-only override of the fixed CLI timeout. The CLI never sets this. */
  timeoutMs?: number;
};

const SKILL_FILENAMES = ['SKILL.md', 'skill.md'];

/**
 * Minimal metadata validity on top of the shared parser. The shared
 * `extractDescription` returns the raw value for YAML-ism leaks like
 * `description: null` (as the string "null") and passes unmatched quotes
 * through verbatim, so treat those as malformed here: the description must
 * be a non-empty string with matched quotes. No new dependency -- the
 * existing parser does the extraction work.
 */
export function descriptionOrNull(source: string): string | null {
  const d = extractDescription(source).trim();
  if (!d) return null;
  if (d === 'null' || d === '~') return null;
  const first = d[0];
  if ((first === '"' || first === "'") && (d.length < 2 || d[d.length - 1] !== first)) return null;
  return d;
}

async function findSkillFile(dir: string): Promise<string | null> {
  for (const name of SKILL_FILENAMES) {
    try {
      const p = join(dir, name);
      if ((await stat(p)).isFile()) return p;
    } catch { /* try the next casing */ }
  }
  return null;
}

/**
 * Scan the trusted roots for skills. Every root is validated BEFORE any
 * scanning starts: an unreadable root is a hard error, never a partial
 * search presented as complete. Each root contributes the root itself (when
 * it is a skill) plus its first-level subdirectories — no deeper recursion.
 */
export async function scanSkillRoots(roots: string[]): Promise<{ skills: SkillEntry[]; skipped: number; duplicates: number }> {
  for (const root of roots) {
    let info;
    try { info = await stat(root); }
    catch { throw new CliError(`Cannot use skill root (unreadable): ${root}`); }
    if (!info.isDirectory()) throw new CliError(`Not a skill directory: ${root}`);
  }
  const skills: SkillEntry[] = [];
  const seen = new Set<string>();
  let skipped = 0;
  let duplicates = 0;
  for (const root of roots) {
    // The root itself may be a skill; then its first-level subdirectories.
    const candidates: Array<{ name: string; dir: string }> = [{ name: basename(root), dir: root }];
    let entries;
    try { entries = await readdir(root, { withFileTypes: true }); }
    catch { throw new CliError(`Cannot list skill root: ${root}`); }
    for (const entry of entries) {
      if (entry.isDirectory()) candidates.push({ name: entry.name, dir: join(root, entry.name) });
    }
    for (const { name, dir } of candidates) {
      const skillFile = await findSkillFile(dir);
      if (!skillFile) continue;
      // Deterministic root precedence: the first root naming a skill wins;
      // the winner's path is kept as provenance.
      if (seen.has(name)) { duplicates++; continue; }
      let source: string;
      try { source = await readFile(skillFile, 'utf8'); }
      catch { skipped++; continue; }
      const description = descriptionOrNull(source);
      if (!description) { skipped++; continue; }
      seen.add(name);
      skills.push({
        id: name,
        name,
        path: skillFile,
        description: description.length > DESCRIPTION_CAP ? description.slice(0, DESCRIPTION_CAP) : description
      });
    }
  }
  return { skills, skipped, duplicates };
}

/** Normalize a request for name matching: one leading slash stripped, case folded, surrounding quotes and trailing punctuation dropped. */
export function normalizeSkillName(raw: string): string {
  let s = raw.trim().toLowerCase();
  if (s.startsWith('/')) s = s.slice(1);
  return s.replace(/^["'`]+|["'`,.,;:!?]+$/g, '').trim();
}

/**
 * Resolve an exact /slash-name or exact skill name locally. Exact means an
 * actual exact normalized name match — never a fuzzy near-miss. A near-miss
 * (e.g. "care" for a "card" skill) resolves to null and flows through the
 * ordinary local/judge path as suggestions, never as status "exact". Typos
 * stay in the suggestions path; the user's wording is never rewritten, and
 * only the winning candidate's own name is ever returned.
 */
export function resolveExactSkill(skills: SkillEntry[], request: string): SkillEntry | null {
  const want = normalizeSkillName(request);
  if (!want) return null;
  const exact = skills.filter(s => s.name.toLowerCase() === want);
  return exact.length === 1 ? exact[0] : null;
}

// Words that carry no searchable content, in tokenize()'s folded form.
// Deliberately narrow: only pronouns, determiners, auxiliaries, be/have/do
// forms and vague nouns. This is an underspecified check, not a universal
// ambiguity detector — anything with a real content word goes to the judge.
const FILLER = new Set([
  'it', 'its', 'this', 'thi', 'that', 'these', 'those', 'them', 'they', 'their',
  'he', 'him', 'his', 'she', 'her', 'we', 'us', 'our', 'you', 'your',
  'me', 'my', 'thing', 'stuff', 'something', 'anything', 'everything',
  'nothing', 'whatever', 'what', 'which', 'who', 'whom', 'whose',
  'how', 'why', 'when', 'where',
  'do', 'did', 'done', 'doing', 'can', 'could', 'would', 'should',
  'will', 'shall', 'may', 'might', 'must',
  'the', 'an', 'and', 'or', 'but', 'of', 'to', 'in', 'on', 'for',
  'with', 'at', 'by', 'from', 'as',
  'is', 'are', 'was', 'were', 'be', 'been', 'being',
  'have', 'has', 'had', 'having',
  'please', 'no', 'not', 'yes', 'if', 'then', 'than',
  'there', 'here', 'so', 'just', 'very', 'too', 'such'
]);

export function contentTokens(text: string): string[] {
  return tokenize(text).filter(t => !FILLER.has(t));
}

// Action verbs that carry no searchable content on their own. A request that
// is only one of these plus a bare pronoun ("update it", "check this",
// "fix that") with no usable context cannot be searched — it clarifies.
const VAGUE_VERBS = new Set([
  'update', 'check', 'fix', 'change', 'modify', 'set', 'add', 'remove',
  'restart', 'run', 'start', 'stop'
]);
// Bare pronouns in tokenize()'s folded form ('this' folds to 'thi' via the
// shared trailing-s stemmer; 'its' folds to 'it').
const BARE_PRONOUNS = new Set(['it', 'thi', 'this', 'that', 'these', 'those', 'them']);

/**
 * Narrow, deterministic underspecified check. True when the request has no
 * content tokens and the context turns add none either, OR when the request
 * is just a vague action verb plus a bare pronoun ("update it", "check
 * this", "fix that") with no usable context. A pronoun-only request with a
 * usable referent in context ("update it" after "the pdf-editor config") is
 * NOT underspecified — it goes to the judge. Substantive requests
 * ("update repository configuration") always proceed. Never invents a task
 — it clarifies.
 */
export function isUnderspecified(request: string, context: string[] = []): boolean {
  if (contentTokens(context.join(' ')).length > 0) return false;
  const content = contentTokens(request);
  if (content.length === 0) return true;
  if (content.length === 1 && VAGUE_VERBS.has(content[0])) {
    return tokenize(request).some(t => BARE_PRONOUNS.has(t));
  }
  return false;
}

function toFetchEntries(skills: SkillEntry[]): FetchCatalogEntry[] {
  // Names are indexed as utterances (weighted higher than the description
  // text by the shared local scorer): a real request reads like a name.
  return skills.map(s => ({
    id: s.id,
    text: s.description,
    utterances: [s.name, `/${s.name}`],
    negatives: [] as string[],
    tags: [s.path]
  }));
}

/** Local top-N by the shared BM25-lite scorer, names indexed. Deterministic. */
export function scoreLocal(skills: SkillEntry[], request: string, context: string[], topN: number): SkillEntry[] {
  const entries = toFetchEntries(skills);
  const { kept, scores } = prefilterCatalog(entries, request, topN, { context });
  const order = new Map(kept.map((e, i) => [e.id, i]));
  const byId = new Map(skills.map(s => [s.id, s]));
  return kept
    .map(e => ({ skill: byId.get(e.id)!, score: scores[e.id] ?? 0, order: order.get(e.id)! }))
    .sort((a, b) => b.score - a.score || a.order - b.order)
    .map(r => r.skill);
}

function zeroUsage(): SkillSearchUsage {
  return { calls: 0, inputTokens: 0, outputTokens: 0, retries: 0, estimated: false };
}

/** One-line diagnostic, safe for stdout-adjacent output: no newlines, bounded. */
function shortDiagnostic(message: string): string {
  return message.replace(/[\r\n\t]+/g, ' ').trim().slice(0, 140) || 'unknown error';
}

function toCandidate(s: SkillEntry): SkillCandidate {
  return { id: s.id, name: s.name, path: s.path, description: s.description };
}

export async function runSkillSearch(options: SkillSearchOptions): Promise<{ result: SkillSearchResult; diagnostics: SkillSearchDiagnostics }> {
  const { roots, request } = options;
  if (!Array.isArray(roots) || roots.length === 0) throw new CliError('Provide at least one skill root');
  for (const r of roots) {
    if (typeof r !== 'string' || !r || !isAbsolute(r)) throw new CliError(`Skill roots must be absolute paths: ${JSON.stringify(r)}`);
  }
  if (typeof request !== 'string') throw new CliError('Provide a request string');
  if (request.length > MAX_REQUEST_CHARS) throw new CliError(`The request exceeds ${MAX_REQUEST_CHARS} characters; shorten it and retry`);
  const context = (options.context ?? []).filter(c => typeof c === 'string').slice(-DEFAULT_CONTEXT_TURNS);
  // Bound the combined input before anything reaches the judge. Context
  // bytes count too -- oversized input fails explicitly instead of being
  // silently rewritten; the caller shortens the wording and retries.
  const inputChars = request.length + context.reduce((n, c) => n + c.length, 0);
  if (inputChars > MAX_INPUT_CHARS) throw new CliError(`The request and context together exceed ${MAX_INPUT_CHARS} characters; shorten them and retry`);

  const { skills, skipped, duplicates } = await scanSkillRoots(roots);
  const diagnostics: SkillSearchDiagnostics = { rootsScanned: roots.length, skillsFound: skills.length, skipped, duplicates };

  // 1. Empty catalog: nothing to judge, no call made.
  if (!skills.length) {
    return {
      result: { status: 'no_match', candidates: [], source: 'local', metadataSkipped: skipped, duplicateCount: duplicates, error: 'No skills found under the given roots' },
      diagnostics
    };
  }
  // 2. Exact local resolution: zero network.
  const exact = resolveExactSkill(skills, request);
  if (exact) {
    return { result: { status: 'exact', candidates: [toCandidate(exact)], source: 'local', metadataSkipped: skipped, duplicateCount: duplicates }, diagnostics };
  }
  // 3. Underspecified: clarify, never invent a task.
  if (isUnderspecified(request, context)) {
    return {
      result: {
        status: 'clarify', candidates: [], source: 'local', metadataSkipped: skipped, duplicateCount: duplicates,
        error: 'The request has no usable content to search on; say what you need the skill to do'
      },
      diagnostics
    };
  }
  const localTop = scoreLocal(skills, request, context, LOCAL_NARROW_N).slice(0, JUDGE_TOP_K).map(toCandidate);
  // 4. Local-only: never call the judge.
  if (options.localOnly) {
    return { result: { status: 'suggestions', candidates: localTop, source: 'local', metadataSkipped: skipped, duplicateCount: duplicates }, diagnostics };
  }
  // 5. Judge path. A missing key is a clearly-marked local fallback, never
  //    a no_match and never a thrown error.
  const apiKey = options.apiKey ?? process.env.TYPESAFE_API_KEY ?? '';
  if (!apiKey) {
    return {
      result: {
        status: 'fallback', candidates: localTop, source: 'local', metadataSkipped: skipped, duplicateCount: duplicates,
        usage: zeroUsage(), error: 'No TYPESAFE_API_KEY in the environment; local-only results'
      },
      diagnostics
    };
  }
  // A fallback after the judge was attempted carries the measured call costs:
  // the provider failed, but the dependent calls still happened.
  const fallback = (error: string, usage: SkillSearchUsage): { result: SkillSearchResult; diagnostics: SkillSearchDiagnostics } => ({
    result: { status: 'fallback', candidates: localTop, source: 'local', metadataSkipped: skipped, duplicateCount: duplicates, usage, error },
    diagnostics
  });
  // Hoisted so the catch path can report whatever the judge already cost.
  let usage: SkillSearchUsage = zeroUsage();
  try {
    const transport: Evaluator = options.transport ?? new Jev({ apiKey });
    const run = await runFetch(toFetchEntries(skills), request, {
      context,
      k: JUDGE_TOP_K,
      prefilter: LOCAL_NARROW_N,
      // Fixed and bounded: one attempt per batch, existing timeout option.
      maxRetries: 0,
      timeoutMs: options.timeoutMs ?? SKILL_SEARCH_TIMEOUT_MS,
      transport
    });
    usage = {
      calls: run.calls,
      inputTokens: run.cost.inputTokens,
      outputTokens: run.cost.outputTokens,
      retries: run.cost.retries,
      estimated: run.cost.estimated
    };
    if (!run.manifest.complete) {
      return fallback('Judge coverage incomplete; local-only results', usage);
    }
    if (run.noMatch) {
      return { result: { status: 'no_match', candidates: [], source: 'jev', metadataSkipped: skipped, duplicateCount: duplicates, model: run.model, usage }, diagnostics };
    }
    if (!run.ranked.length) {
      // The judge returned no usable answers: a service failure, never a no-match.
      return fallback('Judge returned no usable answers; local-only results', usage);
    }
    const byId = new Map(skills.map(s => [s.id, s]));
    const hydrate = (list: Array<{ id: string }>): SkillCandidate[] =>
      list.map(r => byId.get(r.id)).filter((s): s is SkillEntry => !!s).map(toCandidate);
    const gate = applyNoneGate(run, DEFAULT_FETCH_FLOOR, DEFAULT_FETCH_MARGIN);
    if (gate.noMatch) {
      return {
        result: { status: 'clarify', candidates: hydrate(gate.candidates), source: 'jev', metadataSkipped: skipped, duplicateCount: duplicates, model: run.model, usage, error: gate.ask },
        diagnostics
      };
    }
    return {
      result: { status: 'suggestions', candidates: hydrate(gate.ranked), source: 'jev', metadataSkipped: skipped, duplicateCount: duplicates, model: run.model, usage },
      diagnostics
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return fallback(`Judge unavailable (${shortDiagnostic(message)}); local-only results`, usage);
  }
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

function parseRoots(text: string): string[] {
  let parsed: unknown;
  try { parsed = JSON.parse(text); }
  catch { throw new CliError('The roots file is not valid JSON'); }
  if (!Array.isArray(parsed) || !parsed.every(v => typeof v === 'string' && v)) {
    throw new CliError('The roots file must be a JSON array of absolute skill-directory paths');
  }
  return parsed as string[];
}

function parseRequestFile(text: string): { request: string; context: string[] } {
  let parsed: unknown;
  try { parsed = JSON.parse(text); }
  catch { throw new CliError('The request file is not valid JSON'); }
  if (!parsed || typeof parsed !== 'object') throw new CliError('The request file must be a JSON object {"request": "...", "context": [...]}');
  const { request, context } = parsed as { request?: unknown; context?: unknown };
  if (typeof request !== 'string') throw new CliError('The request file needs a "request" string');
  if (context !== undefined && (!Array.isArray(context) || !context.every(c => typeof c === 'string'))) {
    throw new CliError('The request file "context", when present, must be an array of strings');
  }
  return { request, context: (context ?? []) as string[] };
}

const usage = `skill-search --roots-file ROOTS.json --request-file REQUEST.json [--local-only]

Suggest which installed skills serve one plain request, without executing
anything. Advisory only: the caller loads the chosen SKILL.md itself and
decides what to run. Skill files are read only to extract frontmatter
metadata; bodies are never sent to the judge and never printed.

  --roots-file FILE   JSON array of trusted absolute skill-directory paths
                      (caller-owned). Each root is scanned for SKILL.md /
                      skill.md in the root itself and in first-level
                      subdirectories only; nothing deeper is read. An
                      unreadable root is a hard error, never a partial search.
  --request-file FILE JSON object {"request": "<original user wording>",
                      "context": ["<recent turn>", ...]}. Context carries the
                      necessary recent turns, oldest first.
  --local-only        Never call the judge; rank locally only.

One compact JSON object is printed to stdout and nothing else:
  {status, candidates, source, error?, model?, usage?}
status is exact | suggestions | no_match | fallback | clarify. candidates
are at most 3 {id, name, path, description} with short descriptions.
source is "local" when no judge call was made, "jev" when one was.

An exact /slash-name or exact skill name resolves locally with no
network. Anything else goes through the bounded judge path over the top
40 local matches (one attempt per batch under a fixed timeout), with
all dependent call costs collected into usage. A missing API key, a
timeout, a transport error, or unusable judge answers is a "fallback" with
local-only candidates — a service failure is never reported as no_match.
Retrieval returns suggestions, never permission to execute.

Live mode needs TYPESAFE_API_KEY. Exit codes: 0 ok (including advisory
fallback/no_match/clarify), 1 usage or input error.`;

async function main(): Promise<number> {
  const args = process.argv.slice(2);
  if (!args.length || args.includes('--help')) { console.log(usage); return 0; }

  let rootsFile = '', requestFile = '', localOnly = false;
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    const next = () => { const v = args[++i]; if (!v || v.startsWith('--')) throw new CliError(`${flag} needs a value`); return v; };
    if (flag === '--roots-file') { if (rootsFile) throw new CliError('Repeated --roots-file'); rootsFile = resolve(next()); }
    else if (flag === '--request-file') { if (requestFile) throw new CliError('Repeated --request-file'); requestFile = resolve(next()); }
    else if (flag === '--local-only') localOnly = true;
    else throw new CliError(`Unknown argument ${flag}\n\n${usage}`);
  }
  if (!rootsFile || !requestFile) throw new CliError(usage);

  const roots = parseRoots(await readSmallFile(rootsFile, MAX_FILE_BYTES, 'roots'));
  const { request, context } = parseRequestFile(await readSmallFile(requestFile, MAX_FILE_BYTES, 'request'));
  const { result, diagnostics } = await runSkillSearch({ roots, request, context, localOnly });

  // The one compact stdout object. Everything else is stderr.
  process.stdout.write(JSON.stringify(result) + '\n');
  console.error(
    `skill-search: ${diagnostics.skillsFound} skill(s) from ${diagnostics.rootsScanned} root(s); ` +
    `skipped=${diagnostics.skipped} duplicates=${diagnostics.duplicates}; ` +
    `status=${result.status} source=${result.source}${result.usage ? ` calls=${result.usage.calls}` : ''}`
  );
  return 0;
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  main().then(code => { process.exitCode = code; }).catch(error => {
    // Only deliberate, local diagnostics are printed. A raw parser or
    // filesystem exception can embed file contents or sensitive paths.
    console.error(error instanceof CliError ? error.message : 'Skill search failed; check the input files');
    process.exitCode = 1;
  });
}
