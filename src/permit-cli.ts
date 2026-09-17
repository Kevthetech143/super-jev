#!/usr/bin/env node
/**
 * `npm run permit` — before the harness clicks, pays, sends or deletes: is
 * this one action safe to run automatically?
 *
 * One question, one verdict. The plan (the exact snapshot that would be sent)
 * is always printed before any call, so `--dry-run` reaches no network by
 * construction. See src/enhance/permit.ts for the gate and the hard rule.
 */
import { stat, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { Jev } from './jev.ts';
import { StubEvaluator, choiceAnswer } from './enhance/stub.ts';
import { decidePermit, permitRequest, PERMIT_CONFIDENCE_THRESHOLD, type PermitSnapshot } from './enhance/permit.ts';
import type { Evaluator } from './types.ts';

class CliError extends Error {}

const usage = `super-jev permit --snapshot SNAPSHOT.json [--action "TEXT"] [options]

Asks one question: is this action safe to run without a human? Never returns
safe_to_auto for an action matching a hard-rule keyword (delete, rm -rf,
force push, payment, wire, send email, post), whatever the model says.

  --snapshot  FILE   JSON: {"action","target","reversible","reversibilityNotes",
                     "policyLines"}. All fields optional except that an action
                     must end up set, from here or from --action.
  --action    TEXT   The action, in plain words. Overrides snapshot.action when
                     both are given. Required if the snapshot has none.
  --id        ID     Label for this decision. Default "action".
  --min-confidence N Escalation threshold, 0..1. Below it, never auto. Default ${PERMIT_CONFIDENCE_THRESHOLD}.
  --json             Emit one JSON object instead of plain text.
  --dry-run          Print the request that would be sent. Zero network.
  --stub             Run against a fixed offline stub (always answers
                     safe_to_auto at 0.95 confidence, before the hard rule
                     runs). Synthetic padding for exercising the plumbing;
                     never evidence about a real action.

Live mode is the default and needs TYPESAFE_API_KEY.
Exit codes: 0 safe_to_auto, 2 needs_approval, 3 refuse, 1 usage/failure.`;

const MAX_SNAPSHOT_BYTES = 64 * 1024;

async function readSmallFile(path: string, limit: number, what: string): Promise<string> {
  let info;
  try { info = await stat(path); }
  catch { throw new CliError(`Cannot read the ${what} file; provide a readable regular file`); }
  if (!info.isFile()) throw new CliError(`The ${what} path must be a regular file`);
  if (info.size > limit) throw new CliError(`The ${what} file exceeds ${limit} bytes`);
  try { return await readFile(path, 'utf8'); }
  catch { throw new CliError(`Cannot read the ${what} file`); }
}

export function parseSnapshot(text: string): PermitSnapshot {
  let parsed: unknown;
  try { parsed = JSON.parse(text); }
  catch { throw new CliError('The snapshot file is not valid JSON'); }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new CliError('The snapshot file must be a JSON object');
  const s = parsed as Record<string, unknown>;
  if (s.action !== undefined && typeof s.action !== 'string') throw new CliError('snapshot.action must be a string');
  if (s.target !== undefined && typeof s.target !== 'string') throw new CliError('snapshot.target must be a string');
  if (s.reversible !== undefined && typeof s.reversible !== 'boolean') throw new CliError('snapshot.reversible must be a boolean');
  if (s.reversibilityNotes !== undefined && typeof s.reversibilityNotes !== 'string') throw new CliError('snapshot.reversibilityNotes must be a string');
  if (s.policyLines !== undefined && (!Array.isArray(s.policyLines) || s.policyLines.some(l => typeof l !== 'string'))) throw new CliError('snapshot.policyLines must be an array of strings');
  return {
    action: s.action as string | undefined,
    target: s.target as string | undefined,
    reversible: s.reversible as boolean | undefined,
    reversibilityNotes: s.reversibilityNotes as string | undefined,
    policyLines: s.policyLines as string[] | undefined
  };
}

function fixedStub(): Evaluator {
  // Always the riskiest usable answer, at high confidence, so the hard rule
  // and the escalation threshold are the only things able to change the
  // verdict downward when this stub is used. It records nothing about any
  // real action.
  return new StubEvaluator({ script: () => ({ model: 'stub-offline-fixed', answers: { permit: choiceAnswer('safe_to_auto', 0.95, ['safe_to_auto', 'needs_approval', 'refuse']) } }) });
}

function number(raw: string | undefined, flag: string): number {
  const value = Number(raw);
  if (!raw || !Number.isFinite(value)) throw new CliError(`${flag} needs a number`);
  return value;
}

const EXIT_BY_VERDICT = { safe_to_auto: 0, needs_approval: 2, refuse: 3 } as const;

try {
  const args = process.argv.slice(2);
  if (!args.length || args.includes('--help')) { console.log(usage); process.exit(0); }

  let snapshotPath = '', actionFlag: string | undefined, id = 'action';
  let minConfidence: number | undefined;
  let json = false, dryRun = false, stub = false;
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    const next = () => { const v = args[++i]; if (v === undefined || v.startsWith('--')) throw new CliError(`${flag} needs a value`); return v; };
    if (flag === '--snapshot') { if (snapshotPath) throw new CliError('Repeated --snapshot'); snapshotPath = resolve(next()); }
    else if (flag === '--action') { if (actionFlag !== undefined) throw new CliError('Repeated --action'); actionFlag = next(); }
    else if (flag === '--id') { id = next(); }
    else if (flag === '--min-confidence') { if (minConfidence !== undefined) throw new CliError('Repeated --min-confidence'); minConfidence = number(next(), '--min-confidence'); }
    else if (flag === '--json') json = true;
    else if (flag === '--dry-run') dryRun = true;
    else if (flag === '--stub') stub = true;
    else throw new CliError(`Unknown argument ${flag}\n\n${usage}`);
  }
  if (!snapshotPath) throw new CliError(usage);
  if (minConfidence !== undefined && (minConfidence <= 0 || minConfidence > 1)) throw new CliError('--min-confidence must be greater than 0 and at most 1');
  if (!dryRun && !stub && !process.env.TYPESAFE_API_KEY) throw new CliError('Set TYPESAFE_API_KEY to run live, or use --dry-run or --stub');

  const snapshot = parseSnapshot(await readSmallFile(snapshotPath, MAX_SNAPSHOT_BYTES, 'snapshot'));
  const action = actionFlag ?? snapshot.action;
  if (!action || !action.trim()) throw new CliError('No action: set "action" in the snapshot or pass --action "TEXT"');
  const fullSnapshot = { ...snapshot, action };

  if (dryRun) {
    const request = permitRequest(id, fullSnapshot);
    if (json) console.log(JSON.stringify({ mode: 'dry-run', id, request }, null, 2));
    else { console.log(`Would ask one permit question for "${action}"${fullSnapshot.target ? ` on "${fullSnapshot.target}"` : ''}.`); console.log(JSON.stringify(request, null, 2)); }
    console.error('Dry run: nothing was sent, no provider was called.');
    process.exit(0);
  }

  const evaluator: Evaluator = stub ? fixedStub() : new Jev();
  const gate = minConfidence !== undefined ? { minConfidence } : {};
  const result = await decidePermit(id, fullSnapshot, evaluator, { gate });

  if (json) {
    console.log(JSON.stringify({
      id: result.id, action: result.action, target: result.target,
      verdict: result.verdict, confidence: result.confidence, reason: result.reason,
      hardRuleApplied: result.hardRuleApplied, matchedKeywords: result.matchedKeywords,
      softCueApplied: result.softCueApplied, matchedCues: result.matchedCues,
      noDistributionApplied: result.noDistributionApplied,
      outcomeKind: result.outcome.kind
    }, null, 2));
  } else {
    console.log(`${result.verdict.toUpperCase()}: ${result.reason}`);
    if (result.hardRuleApplied) console.log(`Hard rule applied: matched ${result.matchedKeywords.join(', ')}`);
    if (result.softCueApplied) console.log(`Soft cue applied: matched ${result.matchedCues.join(', ')}`);
    if (result.noDistributionApplied) console.log('No distribution to cross-check confidence against; treated as needs_approval.');
  }
  process.exit(EXIT_BY_VERDICT[result.verdict]);
} catch (error) {
  console.error(error instanceof CliError ? error.message : 'Permit check failed; check the snapshot file and try again');
  process.exitCode = 1;
}
