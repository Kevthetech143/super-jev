import type { CoverageManifest, OutcomeKind, RecordOutcome } from './types.ts';

const KINDS: OutcomeKind[] = ['accepted', 'review', 'abstain', 'insufficient_evidence', 'failed_validation', 'unanswered', 'unaccounted'];

/**
 * Account for every input record exactly once.
 *
 * A record the run never reached does not vanish: it is given an `unaccounted`
 * outcome so it shows up in the manifest, and `complete` goes false. A record
 * reported twice is also a problem, not a silent last-write-wins.
 */
export function buildManifest(recordIds: string[], outcomes: RecordOutcome[]): CoverageManifest {
  const problems: string[] = [];
  const byId = new Map<string, RecordOutcome>();
  const duplicates: string[] = [];
  for (const outcome of outcomes) {
    if (byId.has(outcome.id)) duplicates.push(outcome.id);
    else byId.set(outcome.id, outcome);
  }
  if (duplicates.length) problems.push(`records reported more than once: ${[...new Set(duplicates)].join(', ')}`);

  const inputSet = new Set(recordIds);
  if (inputSet.size !== recordIds.length) problems.push('the input contains duplicate record ids');
  const unknown = [...byId.keys()].filter(id => !inputSet.has(id));
  if (unknown.length) problems.push(`outcomes for records that were never input: ${unknown.join(', ')}`);

  const final: RecordOutcome[] = recordIds.map(id => byId.get(id) ?? {
    id, kind: 'unaccounted' as OutcomeKind,
    reason: 'the run produced no outcome for this record', passes: []
  });
  const missing = final.filter(o => o.kind === 'unaccounted').map(o => o.id);
  if (missing.length) problems.push(`records with no outcome: ${missing.join(', ')}`);

  const byKind = Object.fromEntries(KINDS.map(k => [k, [] as string[]])) as Record<OutcomeKind, string[]>;
  for (const outcome of final) byKind[outcome.kind].push(outcome.id);

  return { totalRecords: recordIds.length, byKind, outcomes: final, complete: problems.length === 0, problems };
}

/** Fail loudly. A run that cannot account for its own input must not report success. */
export function assertComplete(manifest: CoverageManifest): void {
  if (!manifest.complete) throw new Error(`Incomplete coverage manifest: ${manifest.problems.join('; ')}`);
}

export function formatManifest(manifest: CoverageManifest): string {
  const lines = [`coverage: ${manifest.totalRecords} record(s), complete=${manifest.complete}`];
  for (const kind of KINDS) {
    const ids = manifest.byKind[kind];
    if (ids.length) lines.push(`  ${kind}: ${ids.length} [${ids.join(', ')}]`);
  }
  for (const problem of manifest.problems) lines.push(`  PROBLEM: ${problem}`);
  return lines.join('\n');
}
