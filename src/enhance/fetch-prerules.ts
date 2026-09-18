/**
 * FETCH PRE-RULES: free, offline checks the fetch door runs BEFORE the judge,
 * ported from the offline-proven rules in
 * `super-jev-experiments/areas-20260918/skillpick/skillpick_facts.py`
 * (R1 unique-trigger-bypass, R2 negative-demote, R3 no-trigger-noMatch).
 *
 * The premise is the same one `derive-facts.ts` uses for worker-verify: read
 * what the evidence already says in code, before spending a judge call (or a
 * judge's own confidence) on a question code can already answer. Here the
 * "evidence" is the catalog's own `utterances`/`negatives` and the request
 * text — no provider call, no network, pure string matching.
 *
 *   R1 (bypass the judge): the request contains exactly one record's UNIQUE
 *      multi-word trigger phrase, and none of that record's `negatives` — so
 *      serve that record directly. Zero judge calls, `source: "trigger"`.
 *   R2 (demote): the judge's own top-1 pick has a negative-phrase hit in the
 *      request — do not serve it; treat it as `review`.
 *   R3 (noMatch): no trigger phrase from ANY record appears in the request
 *      at all, AND the judge's top-1 confidence is under the floor — serve
 *      nothing.
 *
 * The trigger set per record is built once from `utterances`, normalized,
 * EXCLUDING single-word utterances (too generic to trust blind) and any
 * utterance whose normalized text is shared by more than one record (an
 * ambiguity guard — a phrase two records both claim can never uniquely
 * resolve either one). Only what survives that filter ever fires R1, and
 * only that filtered set counts as "a trigger of a record" for R3's
 * catalog-wide check.
 */
import { DEFAULT_FETCH_FLOOR, runFetch, type FetchCatalogEntry, type FetchOptions, type FetchRankedEntry, type FetchRun } from './fetch.ts';
import type { Evaluator } from '../types.ts';

// --------------------------------------------------------------- normalize

/** Lowercase, strip punctuation (keep letters/digits/space), collapse whitespace. Same shape as `skillpick_facts.py`'s `normalize`. */
export function normalizePhrase(text: string): string {
  if (!text) return '';
  const lowered = text.toLowerCase();
  const stripped = lowered.replace(/[^a-z0-9\s]+/g, ' ');
  return stripped.replace(/\s+/g, ' ').trim();
}

function wordCount(normalized: string): number {
  return normalized ? normalized.split(' ').length : 0;
}

/** Does `haystackNorm` contain `phraseNorm` as a substring, on normalized text? Empty phrases never match. */
function contains(haystackNorm: string, phraseNorm: string): boolean {
  return !!phraseNorm && haystackNorm.includes(phraseNorm);
}

// --------------------------------------------------------------- trigger index (per catalog, request-independent)

export type SharedPhrase = { phrase: string; ids: string[] };

export type TriggerIndex = {
  /** record id -> the multi-word, non-shared utterance phrases usable as its triggers (normalized). */
  triggersByRecord: Map<string, string[]>;
  /** record id -> single-word utterances dropped by the ambiguity guard, for --explain. */
  droppedSingleWord: Map<string, string[]>;
  /** Utterances shared by more than one record's utterance list, dropped from every owner, for --explain. */
  droppedShared: SharedPhrase[];
};

/**
 * Build the per-record trigger set once for a catalog. Deterministic and
 * request-independent, so a caller can build it once and reuse it across
 * many requests (the CLI builds it once per run).
 */
export function buildTriggerIndex(catalog: FetchCatalogEntry[]): TriggerIndex {
  const ownerMap = new Map<string, Set<string>>();
  const perRecordCandidates = new Map<string, string[]>();
  const droppedSingleWord = new Map<string, string[]>();

  for (const record of catalog) {
    const seen = new Set<string>();
    const singles: string[] = [];
    for (const raw of record.utterances ?? []) {
      const normalized = normalizePhrase(raw);
      if (!normalized) continue;
      if (wordCount(normalized) < 2) { singles.push(normalized); continue; }
      seen.add(normalized);
    }
    if (singles.length) droppedSingleWord.set(record.id, [...new Set(singles)]);
    const list = [...seen];
    perRecordCandidates.set(record.id, list);
    for (const phrase of list) {
      const owners = ownerMap.get(phrase) ?? new Set<string>();
      owners.add(record.id);
      ownerMap.set(phrase, owners);
    }
  }

  const triggersByRecord = new Map<string, string[]>();
  const droppedShared: SharedPhrase[] = [];
  const sharedAlreadyReported = new Set<string>();
  for (const [id, phrases] of perRecordCandidates) {
    const kept: string[] = [];
    for (const phrase of phrases) {
      const owners = ownerMap.get(phrase)!;
      if (owners.size > 1) {
        if (!sharedAlreadyReported.has(phrase)) {
          sharedAlreadyReported.add(phrase);
          droppedShared.push({ phrase, ids: [...owners].sort() });
        }
        continue;
      }
      kept.push(phrase);
    }
    triggersByRecord.set(id, kept);
  }
  droppedShared.sort((a, b) => a.phrase.localeCompare(b.phrase));

  return { triggersByRecord, droppedSingleWord, droppedShared };
}

// --------------------------------------------------------------- per-request facts

export type RequestTriggerFacts = {
  requestNorm: string;
  /** Records whose (filtered) trigger set has a phrase found in the request, in catalog order. */
  matchedRecordIds: string[];
  /** record id -> the specific matched trigger phrase. */
  matchedPhraseByRecord: Map<string, string>;
  /** True when at least one record's trigger phrase is present anywhere in the request. */
  anyTriggerHit: boolean;
};

/** Which records' triggers appear in this request, against an already-built `TriggerIndex`. Pure string matching, no I/O. */
export function factsForRequest(request: string, triggerIndex: TriggerIndex): RequestTriggerFacts {
  const requestNorm = normalizePhrase(request);
  const matchedRecordIds: string[] = [];
  const matchedPhraseByRecord = new Map<string, string>();
  for (const [id, phrases] of triggerIndex.triggersByRecord) {
    for (const phrase of phrases) {
      if (contains(requestNorm, phrase)) { matchedRecordIds.push(id); matchedPhraseByRecord.set(id, phrase); break; }
    }
  }
  return { requestNorm, matchedRecordIds, matchedPhraseByRecord, anyTriggerHit: matchedRecordIds.length > 0 };
}

/** Does this record's `negatives` list have a phrase present in the (already normalized) request? Returns the matched negatives. */
export function negativeHits(record: FetchCatalogEntry | undefined, requestNorm: string): string[] {
  if (!record) return [];
  const hits: string[] = [];
  for (const raw of record.negatives ?? []) {
    const normalized = normalizePhrase(raw);
    if (normalized && contains(requestNorm, normalized)) hits.push(normalized);
  }
  return hits;
}

// --------------------------------------------------------------- decision

export type PreRuleServe = { kind: 'serve'; source: 'trigger'; id: string; reason: string };
export type PreRuleDemote = { kind: 'demote'; id: string; reason: string; run: FetchRun };
export type PreRuleNoMatch = { kind: 'noMatch'; reason: string; run: FetchRun };
/** No pre-rule settled this request: fall through to the ordinary judge + none-gate flow. */
export type PreRuleFallThrough = { kind: 'fallThrough'; run: FetchRun };

export type PreRuleDecision = PreRuleServe | PreRuleDemote | PreRuleNoMatch | PreRuleFallThrough;

export type PreRuleExplain = {
  facts: RequestTriggerFacts;
  droppedSingleWord: Map<string, string[]>;
  droppedShared: SharedPhrase[];
  decision: PreRuleDecision;
};

/**
 * Run the three pre-rules against one request. R1 needs no judge call at all
 * — when it fires, `runFetch` is never invoked. R2 and R3 need the judge's
 * own top-1 pick, so they call `runFetch` once (same as the door would have
 * anyway) and post-process its result. When none of the three settle it,
 * `{kind: 'fallThrough', run}` hands back the ordinary run for the caller's
 * usual none-gate handling — pre-rules never changes that path's behavior.
 */
export async function applyPreRules(
  catalog: FetchCatalogEntry[],
  request: string,
  triggerIndex: TriggerIndex,
  options: FetchOptions & { transport: Evaluator },
  floor: number = DEFAULT_FETCH_FLOOR
): Promise<{ decision: PreRuleDecision; facts: RequestTriggerFacts }> {
  const facts = factsForRequest(request, triggerIndex);
  const recordById = new Map(catalog.map(r => [r.id, r]));

  // R1: exactly one record's unique multi-word trigger present, and none of
  // that record's negatives. Two or more candidate records is ambiguous —
  // cannot auto-resolve, fall through to the judge.
  if (facts.matchedRecordIds.length === 1) {
    const id = facts.matchedRecordIds[0];
    const negatives = negativeHits(recordById.get(id), facts.requestNorm);
    if (negatives.length === 0) {
      const phrase = facts.matchedPhraseByRecord.get(id)!;
      return {
        facts,
        decision: {
          kind: 'serve', source: 'trigger', id,
          reason: `R1: unique multi-word trigger "${phrase}" matched record ${id}; no negatives hit; no judge call made`
        }
      };
    }
  }

  const run = await runFetch(catalog, request, options);
  const top1: FetchRankedEntry | undefined = run.ranked[0] ?? run.allScored[0];

  // R2: negative hit on the judge's own top-1 -> demote to review.
  if (top1) {
    const negatives = negativeHits(recordById.get(top1.id), facts.requestNorm);
    if (negatives.length) {
      return {
        facts,
        decision: {
          kind: 'demote', id: top1.id, run,
          reason: `R2: judge top-1 ${top1.id} matches negative phrase(s) [${negatives.join(', ')}] in the request; demoted to review, not served`
        }
      };
    }
  }

  // R3: no trigger from ANY record anywhere in the request, and the judge's
  // top-1 confidence is under the floor.
  if (!facts.anyTriggerHit) {
    const conf = top1?.confidence ?? 0;
    if (conf < floor) {
      return {
        facts,
        decision: {
          kind: 'noMatch', run,
          reason: `R3: no record's trigger phrase appears anywhere in the request, and the judge top-1 confidence ${conf.toFixed(2)} is under the floor ${floor.toFixed(2)}`
        }
      };
    }
  }

  return { facts, decision: { kind: 'fallThrough', run } };
}

/** `--explain` output: the derived facts plus the pre-rule verdict, in plain words. */
export function formatPreRuleExplain(explain: PreRuleExplain): string {
  const lines: string[] = [];
  lines.push(`request (normalized): ${JSON.stringify(explain.facts.requestNorm)}`);
  if (explain.facts.matchedRecordIds.length) {
    for (const id of explain.facts.matchedRecordIds) {
      lines.push(`  trigger hit: record ${id} via "${explain.facts.matchedPhraseByRecord.get(id)}"`);
    }
  } else {
    lines.push('  trigger hit: none (no record\'s unique multi-word trigger appears in the request)');
  }
  lines.push(`  ambiguity guard: ${explain.droppedShared.length} utterance(s) dropped as shared by more than one record`);
  for (const shared of explain.droppedShared.slice(0, 10)) {
    lines.push(`    "${shared.phrase}" claimed by: ${shared.ids.join(', ')}`);
  }
  let singleWordCount = 0;
  for (const words of explain.droppedSingleWord.values()) singleWordCount += words.length;
  lines.push(`  single-word utterances excluded from trigger sets: ${singleWordCount}`);
  const d = explain.decision;
  if (d.kind === 'serve') lines.push(`  pre-rule verdict: ${d.reason}`);
  else if (d.kind === 'demote') lines.push(`  pre-rule verdict: ${d.reason}`);
  else if (d.kind === 'noMatch') lines.push(`  pre-rule verdict: ${d.reason}`);
  else lines.push('  pre-rule verdict: no pre-rule fired; falling through to the judge + none-gate as usual');
  return lines.join('\n');
}
