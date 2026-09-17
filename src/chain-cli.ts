#!/usr/bin/env node
/**
 * `npm run chain` — ticket to order to policy, with completeness checked in
 * code before the final question is ever asked.
 *
 * A thin CLI over the evidence-chain library that already exists:
 * gatherEvidence (src/enhance/evidence.ts) walks the pool from a root id and
 * checks the required roles in code; runInvestigation (src/enhance/
 * investigate.ts) does that for every case, blocks the ones missing required
 * evidence, and only asks the final question about the ones that are
 * complete. This file is the JSON-spec parsing and the printing; the gate
 * itself lives in those two modules.
 */
import { stat, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { Jev } from './jev.ts';
import { StubEvaluator, choiceAnswer } from './enhance/stub.ts';
import { planInvestigation, runInvestigation, type InvestigateConfig, type InvestigationCase } from './enhance/investigate.ts';
import type { EvidenceRole, EvidenceSpec, SourceDoc } from './enhance/evidence.ts';
import type { Evaluator } from './types.ts';

class CliError extends Error {}

const usage = `super-jev chain --spec SPEC.json [options]

Walks the evidence chain for one or more cases and checks it is complete in
code before any question is asked. A case missing a required role is blocked
and never sent; exit 2 names exactly which roles are missing. A complete case
is asked the one question and the completed chain is printed with its
per-role document ids.

  --spec  FILE   JSON role spec. See below for the shape.
  --json         Emit one JSON object instead of plain text.
  --dry-run      Run only the in-code completeness check. Zero network:
                 whether a case is blocked never depends on a model call.
  --stub         Run against a fixed offline stub (always answers the first
                 listed option at 0.95 confidence). Synthetic padding for
                 exercising the plumbing; never evidence about a real chain.

Spec shape:
  {
    "question": "is this return eligible?",   // or per-case, see "cases"
    "options": {"eligible": "...", "ineligible": "...", "unknown": "..."},
    "abstainOption": "unknown",                // optional
    "instructions": "...",                     // optional, shared framing
    "roles": [
      {"name": "ticket", "match": {"type": "self"}},
      {"name": "order",  "match": {"type": "idPrefix", "prefix": "O"}},
      {"name": "policy", "match": {"type": "idPattern", "pattern": "^P\\\\d+$"}},
      {"name": "policy", "match": {"type": "textPattern", "pattern": "\\\\beligible\\\\b", "flags": "i"}}
    ],
    "docs": [{"id": "T1", "text": "...", "links": ["O100", "P1"]}, ...],
    "maxDepth": 3, "maxDocs": 16, "proseReferences": false,
    "danglingReferenceIs": "incomplete",
    "cases": [{"id": "T1", "question": "...", "rootId": "T1"}]   // optional,
      // overrides the single top-level question/id/rootId with several cases
      // over the same docs/roles/options.
  }

Live mode is the default and needs TYPESAFE_API_KEY.
Exit codes: 0 every case resolved, 2 at least one case is blocked on
insufficient evidence, 1 usage/failure.`;

const MAX_SPEC_BYTES = 8 * 1024 * 1024;

async function readSmallFile(path: string, limit: number, what: string): Promise<string> {
  let info;
  try { info = await stat(path); }
  catch { throw new CliError(`Cannot read the ${what} file; provide a readable regular file`); }
  if (!info.isFile()) throw new CliError(`The ${what} path must be a regular file`);
  if (info.size > limit) throw new CliError(`The ${what} file exceeds ${limit} bytes`);
  try { return await readFile(path, 'utf8'); }
  catch { throw new CliError(`Cannot read the ${what} file`); }
}

type RoleMatch =
  | { type: 'self' }
  | { type: 'idPrefix'; prefix: string }
  | { type: 'idPattern'; pattern: string; flags?: string }
  | { type: 'textPattern'; pattern: string; flags?: string };

type RoleSpecJson = { name: string; match: RoleMatch; required?: boolean; unique?: boolean };

function buildIdentify(match: RoleMatch, roleName: string): EvidenceRole['identify'] {
  if (match.type === 'self') return (doc, rootId) => doc.id === rootId;
  if (match.type === 'idPrefix') {
    if (typeof match.prefix !== 'string' || !match.prefix) throw new CliError(`role "${roleName}": idPrefix match needs a non-empty "prefix"`);
    return doc => doc.id.startsWith(match.prefix);
  }
  if (match.type === 'idPattern') {
    const re = compilePattern(match.pattern, match.flags, roleName, 'idPattern');
    return doc => re.test(doc.id);
  }
  if (match.type === 'textPattern') {
    const re = compilePattern(match.pattern, match.flags, roleName, 'textPattern');
    return doc => re.test(doc.text);
  }
  throw new CliError(`role "${roleName}": unknown match type "${(match as { type?: unknown }).type}"`);
}

function compilePattern(pattern: unknown, flags: unknown, roleName: string, matchType: string): RegExp {
  if (typeof pattern !== 'string' || !pattern) throw new CliError(`role "${roleName}": ${matchType} match needs a non-empty "pattern"`);
  if (flags !== undefined && typeof flags !== 'string') throw new CliError(`role "${roleName}": ${matchType} match "flags" must be a string`);
  try { return new RegExp(pattern, flags); }
  catch { throw new CliError(`role "${roleName}": ${matchType} match "pattern" is not a valid regular expression`); }
}

function parseRoles(raw: unknown): EvidenceRole[] {
  if (!Array.isArray(raw) || !raw.length) throw new CliError('spec.roles must be a non-empty array');
  return (raw as RoleSpecJson[]).map((r, i) => {
    if (!r || typeof r !== 'object' || typeof r.name !== 'string' || !r.name.trim()) throw new CliError(`spec.roles[${i}] needs a non-empty "name"`);
    if (!r.match || typeof r.match !== 'object') throw new CliError(`spec.roles[${i}] ("${r.name}") needs a "match"`);
    if (r.required !== undefined && typeof r.required !== 'boolean') throw new CliError(`spec.roles[${i}] ("${r.name}") "required" must be a boolean`);
    if (r.unique !== undefined && typeof r.unique !== 'boolean') throw new CliError(`spec.roles[${i}] ("${r.name}") "unique" must be a boolean`);
    return { name: r.name, identify: buildIdentify(r.match, r.name), required: r.required, unique: r.unique };
  });
}

function parseDocs(raw: unknown): SourceDoc[] {
  if (!Array.isArray(raw) || !raw.length) throw new CliError('spec.docs must be a non-empty array');
  return raw.map((d, i) => {
    if (!d || typeof d !== 'object') throw new CliError(`spec.docs[${i}] must be an object`);
    const doc = d as Record<string, unknown>;
    if (typeof doc.id !== 'string' || !doc.id.trim()) throw new CliError(`spec.docs[${i}] needs a non-empty "id"`);
    if (typeof doc.text !== 'string') throw new CliError(`spec.docs[${i}] ("${doc.id}") needs a "text" string`);
    if (doc.links !== undefined && (!Array.isArray(doc.links) || doc.links.some(l => typeof l !== 'string'))) throw new CliError(`spec.docs[${i}] ("${doc.id}") "links" must be an array of strings`);
    return { id: doc.id, text: doc.text, links: doc.links as string[] | undefined };
  });
}

function parseOptions(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new CliError('spec.options must be an object mapping option id to description');
  const entries = Object.entries(raw as Record<string, unknown>);
  if (!entries.length) throw new CliError('spec.options must have at least one entry');
  for (const [k, v] of entries) if (typeof v !== 'string') throw new CliError(`spec.options["${k}"] must be a string`);
  return raw as Record<string, string>;
}

export function parseChainSpec(text: string): InvestigateConfig {
  let parsed: unknown;
  try { parsed = JSON.parse(text); }
  catch { throw new CliError('The spec file is not valid JSON'); }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new CliError('The spec file must be a JSON object');
  const spec = parsed as Record<string, unknown>;

  const roles = parseRoles(spec.roles);
  const docs = parseDocs(spec.docs);
  const options = parseOptions(spec.options);
  if (spec.abstainOption !== undefined && typeof spec.abstainOption !== 'string') throw new CliError('spec.abstainOption must be a string');
  if (spec.instructions !== undefined && typeof spec.instructions !== 'string') throw new CliError('spec.instructions must be a string');
  if (spec.maxDepth !== undefined && (typeof spec.maxDepth !== 'number' || !Number.isInteger(spec.maxDepth) || spec.maxDepth < 0)) throw new CliError('spec.maxDepth must be a non-negative integer');
  if (spec.maxDocs !== undefined && (typeof spec.maxDocs !== 'number' || !Number.isInteger(spec.maxDocs) || spec.maxDocs < 1)) throw new CliError('spec.maxDocs must be a positive integer');
  if (spec.proseReferences !== undefined && typeof spec.proseReferences !== 'boolean') throw new CliError('spec.proseReferences must be a boolean');
  if (spec.danglingReferenceIs !== undefined && spec.danglingReferenceIs !== 'warning' && spec.danglingReferenceIs !== 'incomplete') throw new CliError('spec.danglingReferenceIs must be "warning" or "incomplete"');
  let supersededPattern: RegExp | undefined;
  if (spec.supersededPattern !== undefined) {
    if (typeof spec.supersededPattern !== 'string') throw new CliError('spec.supersededPattern must be a string');
    try { supersededPattern = new RegExp(spec.supersededPattern, 'i'); }
    catch { throw new CliError('spec.supersededPattern is not a valid regular expression'); }
  }

  const evidenceSpec: EvidenceSpec = {
    name: typeof spec.name === 'string' && spec.name ? spec.name : 'chain',
    roles,
    maxDepth: spec.maxDepth as number | undefined,
    maxDocs: spec.maxDocs as number | undefined,
    proseReferences: spec.proseReferences as boolean | undefined,
    supersededPattern,
    danglingReferenceIs: spec.danglingReferenceIs as 'warning' | 'incomplete' | undefined
  };

  let cases: InvestigationCase[];
  if (spec.cases !== undefined) {
    if (!Array.isArray(spec.cases) || !spec.cases.length) throw new CliError('spec.cases must be a non-empty array when present');
    cases = (spec.cases as unknown[]).map((c, i) => {
      if (!c || typeof c !== 'object') throw new CliError(`spec.cases[${i}] must be an object`);
      const cc = c as Record<string, unknown>;
      if (typeof cc.id !== 'string' || !cc.id.trim()) throw new CliError(`spec.cases[${i}] needs a non-empty "id"`);
      if (typeof cc.question !== 'string' || !cc.question.trim()) throw new CliError(`spec.cases[${i}] ("${cc.id}") needs a non-empty "question"`);
      if (cc.rootId !== undefined && typeof cc.rootId !== 'string') throw new CliError(`spec.cases[${i}] ("${cc.id}") "rootId" must be a string`);
      return { id: cc.id, question: cc.question, rootId: cc.rootId as string | undefined };
    });
  } else {
    if (typeof spec.question !== 'string' || !spec.question.trim()) throw new CliError('spec.question is required when spec.cases is not given');
    const id = typeof spec.id === 'string' && spec.id ? spec.id : 'case';
    const rootId = typeof spec.rootId === 'string' && spec.rootId ? spec.rootId : id;
    cases = [{ id, question: spec.question, rootId }];
  }

  return {
    cases, pool: docs, spec: evidenceSpec, options,
    abstainOption: spec.abstainOption as string | undefined,
    instructions: spec.instructions as string | undefined
  };
}

function fixedStub(): Evaluator {
  // Always answers the first listed option, at high confidence, for every
  // case in the call. Deterministic plumbing exercise only.
  return new StubEvaluator({
    script: request => {
      const answers: Record<string, unknown> = {};
      for (const [key, question] of Object.entries(request.questions)) {
        if (question.type !== 'choice') continue;
        const options = Object.keys(question.criteria);
        answers[key] = choiceAnswer(options[0], 0.95, options);
      }
      return { model: 'stub-offline-fixed', answers: answers as Record<string, ReturnType<typeof choiceAnswer>> };
    }
  });
}

function printBlocked(config: InvestigateConfig, blocked: { id: string; problems: string[] }[], json: boolean): void {
  if (json) return;
  for (const b of blocked) {
    console.log(`BLOCKED ${b.id}: ${b.problems.join('; ')}`);
  }
}

try {
  const args = process.argv.slice(2);
  if (!args.length || args.includes('--help')) { console.log(usage); process.exit(0); }

  let specPath = '';
  let json = false, dryRun = false, stub = false;
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    const next = () => { const v = args[++i]; if (v === undefined || v.startsWith('--')) throw new CliError(`${flag} needs a value`); return v; };
    if (flag === '--spec') { if (specPath) throw new CliError('Repeated --spec'); specPath = resolve(next()); }
    else if (flag === '--json') json = true;
    else if (flag === '--dry-run') dryRun = true;
    else if (flag === '--stub') stub = true;
    else throw new CliError(`Unknown argument ${flag}\n\n${usage}`);
  }
  if (!specPath) throw new CliError(usage);
  if (!dryRun && !stub && !process.env.TYPESAFE_API_KEY) throw new CliError('Set TYPESAFE_API_KEY to run live, or use --dry-run or --stub');

  const config = parseChainSpec(await readSmallFile(specPath, MAX_SPEC_BYTES, 'spec'));

  if (dryRun) {
    const { evidence, blocked, askable, plan } = planInvestigation(config);
    const summary = {
      mode: 'dry-run',
      cases: config.cases.length,
      blocked: blocked.map(b => ({ id: b.id, problems: b.problems })),
      askable: askable.map(c => c.id),
      calls: plan.calls.length,
      estimatedInputTokens: plan.totalEstimatedInputTokens
    };
    if (json) console.log(JSON.stringify(summary, null, 2));
    else {
      console.log(`${blocked.length} of ${config.cases.length} case(s) blocked on incomplete evidence; ${askable.length} askable in ${plan.calls.length} call(s).`);
      printBlocked(config, blocked, json);
    }
    console.error('Dry run: nothing was sent, no provider was called.');
    process.exit(blocked.length ? 2 : 0);
  }

  const evaluator: Evaluator = stub ? fixedStub() : new Jev();
  const run = await runInvestigation(config, evaluator);

  const outcomesById = new Map(run.manifest.outcomes.map(o => [o.id, o]));
  const results = config.cases.map(c => {
    const outcome = outcomesById.get(c.id)!;
    const report = run.evidence[c.id];
    return {
      id: c.id,
      blocked: outcome.kind === 'insufficient_evidence',
      outcomeKind: outcome.kind,
      value: outcome.value,
      confidence: outcome.confidence,
      reason: outcome.reason,
      roles: report.assignments.map(a => ({ role: a.role, docIds: a.docs.map(d => d.id), supersededDocIds: a.supersededDocs.map(d => d.id) })),
      warnings: report.warnings
    };
  });
  const blockedResults = results.filter(r => r.blocked);

  if (json) {
    console.log(JSON.stringify({ mode: stub ? 'stub' : 'live', cases: config.cases.length, blocked: blockedResults.length, results }, null, 2));
  } else {
    for (const r of results) {
      if (r.blocked) { console.log(`BLOCKED ${r.id}: ${r.reason}`); continue; }
      console.log(`${r.id}: ${r.outcomeKind}${r.value !== undefined ? ` = ${r.value}` : ''} (${r.reason})`);
      for (const role of r.roles) console.log(`  ${role.role}: ${role.docIds.join(', ') || '(none)'}`);
    }
  }
  if (run.errors.length) console.error(`${run.errors.length} validation or mapping problem(s): ${run.errors.join('; ')}`);
  process.exit(blockedResults.length ? 2 : 0);
} catch (error) {
  console.error(error instanceof CliError ? error.message : 'Chain check failed; check the spec file and try again');
  process.exitCode = 1;
}
