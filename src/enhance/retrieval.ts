// EXPERIMENTAL SOURCE RETRIEVAL (opt-in).
//
// This module is EXPERIMENTAL: it never runs unless the caller explicitly
// opts in by calling `retrieveSources`. Nothing else in the package imports
// or invokes it; `index.ts` re-exports it so the experiment is discoverable,
// not so the default sweep path changes.
//
// What it does: replicate the evaluation of source description + retrieval
// passages for the model-eval harness, in this order:
//
//   1. Descriptions stage (provider; local filter of 8 like the experiment):
//      judge which sources matter for the request.
//   2. Passage stages (provider), each judging document-GROUPED bundles of
//      prepared passages — never isolated paragraphs:
//        a. narrow: bundles built from the GLOBAL top-4 BM25 chunks across
//           all chunks in the description shortlist (top-3 docs by default);
//        b. wide: same, but from the GLOBAL top-4 chunks across ALL
//           documents in the corpus — only after the narrow bundles refuse;
//        c. neighbors (opt-in, off by default): previous/next chunks are
//           added to the wide selection and judged again as bundles — only
//           after the wide bundles refuse.
//
// A description-stage direct-fit gate may DEFER (low confidence) without
// aborting the chain: the description ranking's top docK docs still continue
// to passage judging. Description-only can never produce `ready`.
//
// PREPARATION CONTRACT (caller-reviewed by the judge): preparation happens
// BEFORE any passage provider call. Every preparation record binds
// caller-REVIEWED passage text and a safe heading to:
//   - the source id,
//   - the source content SHA,
//   - the exact chunk offsets,
//   - the policy the text was reviewed under,
//   - the artifact identity (`doc:Lstart-end`).
// The judge sees ONLY reviewed text and safe headings; raw chunk text and
// raw headings never leave this module. Any selected chunk that lacks a
// valid reviewed record (missing, stale, unreviewed, or under the wrong
// policy) becomes preparation-required, the passage call never happens, and
// the run returns `preparation-required` instead of guessing.
//
// The BM25 bundle selection replicates the experiment format exactly:
//   Document description: <description>
//   Section: <heading breadcrumb>
//   Passage: <raw local text>
// Scores are sorted descending with a stable chunk-identity tie-break.
//
// Nothing here fetches a URL or touches the network; the only IO is the
// caller-provided transport (an `Evaluator`) for judging.

import {
  applyDirectFitGate, prefilterCatalog, runFetch,
  type FetchCatalogEntry, type FetchRun
} from './fetch.ts';
import { createHash } from 'node:crypto';
import type { Evaluator } from '../types.ts';

/** SHA-256 hex of the given UTF-8 text. */
export function sha256Hex(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

/** Local word count (whitespace-split, non-blank tokens). */
function wordCount(text: string): number {
  return text.split(/\s+/).filter(Boolean).length;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/** Fail-closed error for retrieval caller mistakes and invariant violations. */
export class RetrievalError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'RetrievalError';
  }
}

// ---------------------------------------------------------------------------
// Inputs: sources
// ---------------------------------------------------------------------------

/** One source document to retrieve from. */
export type RetrievalSource = {
  /** Stable document id; also the id the judge sees. */
  id: string;
  /** Caller-provided description; only sent to the judge when affirmed reviewed. */
  description: string;
  /** Full original text, chunked once, up front, with exact offsets and SHA. */
  text: string;
};

// ---------------------------------------------------------------------------
// Chunking: replicate the experiment's section chunker exactly
// ---------------------------------------------------------------------------

/** One exact-offset chunk of a source document, carrying its content SHA. */
export type SourceChunk = {
  sourceId: string;
  /** SHA-256 (hex) of the source's full original text. */
  sourceSHA: string;
  /** Zero-based chunk index within the source. */
  chunkIndex: number;
  /** Exact 1-based line numbers in the original text, inclusive. */
  startLine: number;
  endLine: number;
  /** Heading breadcrumb ("A > B"), or '' when the chunk has no heading. */
  heading: string;
  /** Exact raw local text of the chunk (never sent to a provider). */
  text: string;
};

/** Chunk target words, matching the experiment. */
export const DEFAULT_CHUNK_TARGET_WORDS = 180;

/** Artifact identity for a chunk: `doc:Lstart-end`. */
export function chunkIdentity(chunk: Pick<SourceChunk, 'sourceId' | 'startLine' | 'endLine'>): string {
  return `${chunk.sourceId}:L${chunk.startLine}-${chunk.endLine}`;
}

/**
 * Validate a chunk list before any provider call: exact offsets must be
 * well-formed (1-based, start <= end), every chunk must carry a plausible
 * content SHA, identities must be unique, and raw text must be non-empty.
 * Throws `RetrievalError` on any violation — fail closed, never guessed.
 */
export function assertChunkOffsets(chunks: SourceChunk[]): void {
  const seen = new Set<string>();
  for (const chunk of chunks) {
    if (!Number.isInteger(chunk.startLine) || !Number.isInteger(chunk.endLine) ||
        chunk.startLine < 1 || chunk.endLine < chunk.startLine) {
      throw new RetrievalError(
        `invalid chunk offsets for ${JSON.stringify(chunk.sourceId)}: ` +
        `startLine=${chunk.startLine} endLine=${chunk.endLine} — failing closed`
      );
    }
    if (typeof chunk.sourceSHA !== 'string' || !/^[0-9a-f]{64}$/.test(chunk.sourceSHA)) {
      throw new RetrievalError(`chunk for ${JSON.stringify(chunk.sourceId)} lacks a valid content SHA — failing closed`);
    }
    if (typeof chunk.text !== 'string' || !chunk.text) {
      throw new RetrievalError(`chunk ${JSON.stringify(chunkIdentity(chunk))} has empty raw text — failing closed`);
    }
    const id = chunkIdentity(chunk);
    if (seen.has(id)) throw new RetrievalError(`duplicate chunk identity ${JSON.stringify(id)} — failing closed`);
    seen.add(id);
  }
}

/**
 * Chunk one source document EXACTLY like the experiment's section chunker
 * (chunk-parity-reference.mjs) — boundary parity point for point:
 * - a heading line flushes the open block BEFORE the heading; the heading
 *   line itself then starts the next block and enters the breadcrumb stack;
 * - the breadcrumb stack is sparse across depths: `slice(0, depth - 1)` then
 *   direct index assignment, so a depth-3 heading under a depth-1 parent
 *   yields `A >  > C` (the skipped level renders empty) — never compacted;
 * - heading text is kept UNTRIMMED (the regex's `(.*)` capture, as-is);
 * - BEFORE appending a line, the open block flushes when
 *   openWords + nextLineWords > targetWords, with NO blank-line exemption:
 *   an oversize line followed by a blank line flushes the oversize line
 *   alone, and the blank line opens the next block;
 * - blank lines are kept inside the block and count toward its offsets —
 *   only a block with no non-blank line at all is dropped (never emitted);
 * - every chunk records its exact original 1-based line offsets and the
 *   source's content SHA.
 */
export function chunkSource(
  sourceId: string,
  text: string,
  targetWords = DEFAULT_CHUNK_TARGET_WORDS
): SourceChunk[] {
  if (typeof sourceId !== 'string' || !sourceId) throw new RetrievalError('chunkSource needs a non-empty sourceId');
  if (typeof text !== 'string') throw new RetrievalError('chunkSource needs text as a string');
  if (!Number.isInteger(targetWords) || targetWords < 1) throw new RetrievalError('targetWords must be a positive integer');

  const sourceSHA = sha256Hex(text);
  const lines = text.split('\n');
  const chunks: SourceChunk[] = [];
  let headings: string[] = [];
  let block: string[] = [];
  let start = 0; // 1-based start line of the open block
  let words = 0;

  const flush = (): void => {
    if (!block.some(x => x.trim())) { block = []; words = 0; return; }
    const startLine = start;
    const endLine = start + block.length - 1;
    chunks.push({
      sourceId, sourceSHA, chunkIndex: chunks.length,
      startLine, endLine, heading: headings.join(' > '), text: block.join('\n')
    });
    block = []; words = 0;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const h = /^(#{1,6})\s+(.*)/.exec(line);
    if (h) {
      flush();
      headings = headings.slice(0, h[1].length - 1);
      headings[h[1].length - 1] = h[2];
    }
    const count = wordCount(line);
    if (words && words + count > targetWords) flush();
    if (!block.length) start = i + 1;
    block.push(line);
    words += count;
  }
  flush();

  assertChunkOffsets(chunks);
  return chunks;
}

// ---------------------------------------------------------------------------
// BM25 over local chunks (experiment format, global top-K)
// ---------------------------------------------------------------------------

/** Experiment BM25 input text for one chunk. Local only — never a provider payload. */
export function bm25EntryText(
  description: string,
  chunk: Pick<SourceChunk, 'heading' | 'text'>
): string {
  return `Document description: ${description}\nSection: ${chunk.heading}\nPassage: ${chunk.text}`;
}

/**
 * Global top-K BM25 chunks across a chunk pool: score descending, then a
 * stable chunk-identity tie-break (never catalog order). K is global across
 * the pool, not per source.
 *
 * Reuses `prefilterCatalog` for the BM25-lite scoring. Two of its quirks
 * are handled explicitly:
 * - its internal tie-break is catalog order, so the catalog is sorted by
 *   chunk identity first — making its tie-break exactly the required
 *   chunk-ID tie-break, including at the K boundary;
 * - it zeroes all scores when it keeps the whole catalog, so when K covers
 *   every chunk it keeps one fewer (scores stay real) and the dropped tail
 *   chunk is re-attached at the end, where it provably belongs.
 */
export function topChunksGlobal(
  chunks: SourceChunk[],
  descriptions: Map<string, string>,
  request: string,
  k: number
): SourceChunk[] {
  assertChunkOffsets(chunks);
  if (!Number.isInteger(k) || k < 1) throw new RetrievalError('topChunksGlobal k must be a positive integer');
  if (typeof request !== 'string' || !request.trim()) throw new RetrievalError('topChunksGlobal needs a non-empty request');
  const byId = (a: SourceChunk, b: SourceChunk) =>
    chunkIdentity(a) < chunkIdentity(b) ? -1 : chunkIdentity(a) > chunkIdentity(b) ? 1 : 0;
  const ordered = chunks.slice().sort(byId);
  const limit = Math.min(k, ordered.length);
  const entries: FetchCatalogEntry[] = ordered.map(chunk => ({
    id: chunkIdentity(chunk),
    text: bm25EntryText(descriptions.get(chunk.sourceId) ?? '', chunk)
  }));
  const byIdentity = new Map(ordered.map(c => [chunkIdentity(c), c]));
  const keepAll = limit >= ordered.length;
  // Keep one fewer when K covers everything so the scores stay real.
  const res = prefilterCatalog(entries, request, keepAll ? Math.max(ordered.length - 1, 0) : limit);
  const scored = res.kept
    .map(e => ({ id: e.id, score: res.scores[e.id] ?? 0 }))
    .sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const ranked = scored.map(s => byIdentity.get(s.id)!);
  const tail = keepAll ? res.droppedIds.map(id => byIdentity.get(id)!).filter(Boolean) : [];
  return [...ranked, ...tail].slice(0, limit);
}

// ---------------------------------------------------------------------------
// Preparation: caller-reviewed text and safe headings
// ---------------------------------------------------------------------------

/** One caller preparation record for a chunk. */
export type PreparationRecord = {
  sourceId: string;
  contentSHA: string;
  chunkIndex: number;
  startLine: number;
  endLine: number;
  /** Caller-REVIEWED passage text — the only text the judge ever sees. */
  reviewedText: string;
  /** Safe heading — the only heading the judge ever sees. */
  safeHeading: string;
  /** Policy the text was reviewed under; must equal the run's expectedPolicy. */
  policy: string;
  /** Must be 'reviewed' for the record to count. */
  status: string;
};

/**
 * Where preparation records come from: either a Map keyed by chunk identity
 * (`doc:Lstart-end`), or a callback `(sourceId, chunkIndex, contentSHA)`.
 */
export type PreparationSource =
  | Map<string, PreparationRecord>
  | ((sourceId: string, chunkIndex: number, contentSHA: string) => PreparationRecord | undefined);

/** One passage the judge may see: reviewed text + safe heading only, never raw text. */
export type PreparedPassage = {
  sourceId: string;
  description: string;
  contentSHA: string;
  policy: string;
  chunkIndex: number;
  startLine: number;
  endLine: number;
  /** Caller-reviewed text; raw chunk text is NEVER carried here. */
  reviewedText: string;
  /** Safe heading; the raw heading is NEVER carried here. */
  safeHeading: string;
};

/** One selected chunk with no usable preparation — id and offsets only, never text. */
export type UnpreparedPassage = Pick<SourceChunk, 'sourceId' | 'chunkIndex' | 'startLine' | 'endLine'> & {
  /** 'unknown' | 'stale' | 'unreviewed' */
  reason: string;
};

function isRecordUsable(
  record: PreparationRecord | undefined,
  chunk: SourceChunk,
  expectedPolicy: string
): { ok: true } | { ok: false; reason: 'unknown' | 'stale' | 'unreviewed' } {
  if (!record) return { ok: false, reason: 'unknown' };
  if (record.status !== 'reviewed' || record.policy !== expectedPolicy) return { ok: false, reason: 'unreviewed' };
  if (record.sourceId !== chunk.sourceId || record.contentSHA !== chunk.sourceSHA ||
      record.chunkIndex !== chunk.chunkIndex || record.startLine !== chunk.startLine ||
      record.endLine !== chunk.endLine) {
    return { ok: false, reason: 'stale' };
  }
  if (typeof record.reviewedText !== 'string' || !record.reviewedText ||
      typeof record.safeHeading !== 'string' || !record.safeHeading) {
    return { ok: false, reason: 'unreviewed' };
  }
  return { ok: true };
}

function lookupRecord(source: PreparationSource, chunk: SourceChunk): PreparationRecord | undefined {
  if (source instanceof Map) return source.get(chunkIdentity(chunk));
  return source(chunk.sourceId, chunk.chunkIndex, chunk.sourceSHA);
}

/**
 * Preparation gate: bind every selected chunk to a caller-reviewed record.
 * Returns the prepared passages in the input order plus the pending list
 * (id + exact offsets + reason only — never text). The caller decides whether
 * to run or stop; there is deliberately no raw fallback.
 */
export function preparePassages(
  chunks: SourceChunk[],
  descriptions: Map<string, string>,
  preparation: PreparationSource | undefined,
  expectedPolicy = 'reviewed'
): { prepared: PreparedPassage[]; pending: UnpreparedPassage[] } {
  assertChunkOffsets(chunks);
  if (typeof expectedPolicy !== 'string' || !expectedPolicy) throw new RetrievalError('expectedPolicy must be a non-empty string');
  const prepared: PreparedPassage[] = [];
  const pending: UnpreparedPassage[] = [];
  for (const chunk of chunks) {
    const record = preparation === undefined ? undefined : lookupRecord(preparation, chunk);
    const check = preparation === undefined
      ? { ok: false as const, reason: 'unknown' as const }
      : isRecordUsable(record, chunk, expectedPolicy);
    if (!check.ok) {
      pending.push({
        sourceId: chunk.sourceId, chunkIndex: chunk.chunkIndex,
        startLine: chunk.startLine, endLine: chunk.endLine, reason: check.reason
      });
      continue;
    }
    prepared.push({
      sourceId: chunk.sourceId,
      description: descriptions.get(chunk.sourceId) ?? '',
      contentSHA: chunk.sourceSHA,
      policy: record!.policy,
      chunkIndex: chunk.chunkIndex,
      startLine: chunk.startLine,
      endLine: chunk.endLine,
      reviewedText: record!.reviewedText,
      safeHeading: record!.safeHeading
    });
  }
  return { prepared, pending };
}

// ---------------------------------------------------------------------------
// Document bundles: what the judge actually sees
// ---------------------------------------------------------------------------

/**
 * The experiment's exact provider text for judging one document bundle: the
 * source description, then the prepared (reviewed) passages. Built ONLY
 * from caller-reviewed text — raw chunk text and raw headings never appear.
 */
export function bundleProviderText(description: string, passages: PreparedPassage[]): string {
  return 'Description: ' + description + '\nAutomatically retrieved source passages:\n' +
    passages.map(p => p.safeHeading + ': ' + p.reviewedText).join('\n');
}

/**
 * One document-grouped bundle of prepared passages, as the judge sees it.
 * Narrow, wide, and neighbor stages all judge bundles — never isolated
 * paragraphs.
 */
export type PassageBundle = {
  /** the original sourceId (experiment bundle identity) */
  id: string;
  sourceId: string;
  /** The raw chunks behind the bundle (for binding re-verification). */
  chunks: SourceChunk[];
  passages: PreparedPassage[];
  /** Exact text sent to the provider for this bundle. */
  providerText: string;
};

/** One preparation-checked chunk paired with its reviewed passage. */
type CheckedPair = { chunk: SourceChunk; passage: PreparedPassage };

/**
 * Group preparation-checked pairs into one document bundle per source,
 * exactly like the experiment: sources follow FIRST-SEEN order in the
 * BM25-selected pair list, passages inside a bundle keep BM25 selection
 * order (never re-sorted by start line), and the bundle id is the original
 * sourceId.
 */
function groupPairsIntoBundles(
  pairs: CheckedPair[],
  descriptions: Map<string, string>
): PassageBundle[] {
  const bySource = new Map<string, CheckedPair[]>();
  for (const pair of pairs) {
    const list = bySource.get(pair.chunk.sourceId);
    if (list) list.push(pair);
    else bySource.set(pair.chunk.sourceId, [pair]);
  }
  return [...bySource.entries()].map(([sourceId, group]) => ({
    id: sourceId,
    sourceId,
    chunks: group.map(g => g.chunk),
    passages: group.map(g => g.passage),
    providerText: bundleProviderText(descriptions.get(sourceId) ?? '', group.map(g => g.passage))
  }));
}

// ---------------------------------------------------------------------------
// Cache seam (minimal): interface + key format only. No cache is implemented
// here and passing one changes nothing yet — caching is a separate PR.
// ---------------------------------------------------------------------------

/** Minimal cache adapter seam. Accepted in options; not implemented, not used yet. */
export type RetrievalCache = {
  get(key: string): string | undefined;
  set(key: string, value: string): void;
};

/** Stable cache key for the future cache PR: `retrieval/v1/<sourceId>/<contentSHA>`. */
export function cacheKeyFor(sourceId: string, contentSHA: string): string {
  return `retrieval/v1/${sourceId}/${contentSHA}`;
}

// ---------------------------------------------------------------------------
// Options, trace, result
// ---------------------------------------------------------------------------

export type RetrievalOptions = {
  /** The judge transport. Required. */
  transport: Evaluator;
  /**
   * MUST be true: affirms the `description` strings are caller-reviewed,
   * provider-safe input. Anything else throws before any provider call —
   * descriptions are the one raw caller text the judge sees, so the
   * affirmation is explicit, never inferred.
   */
  descriptionsReviewed: boolean;
  /** Policy every preparation record must have been reviewed under. Default 'reviewed'. */
  expectedPolicy?: string;
  /** Sources kept after the descriptions stage. Default 3 ("ranked top3 docs"). */
  docK?: number;
  /** Local BM25 chunks kept GLOBALLY (across the pool, not per source). Default 4. */
  chunkK?: number;
  /** Local prefilter width inside the descriptions stage. Default 8 ("filter8"). */
  prefilter?: number;
  /**
   * Max provider stages. Default 3 without neighbors (descriptions,
   * narrow-bundles, wide-bundles), 4 when `includeNeighbors` is on (adds
   * the neighbors judge stage).
   */
  maxStages?: number;
  /** Retries per provider call. Default 0 — retrieval never retries a judge call. */
  maxRetries?: number;
  /** Per-call timeout in ms, passed through to the sweep engine. */
  timeoutMs?: number;
  /** Optional fourth judge stage, ONLY after wide refusal. Default false (OFF). */
  includeNeighbors?: boolean;
  /** Explicit preparation records; absent means every selected passage is preparation-required. */
  preparation?: PreparationSource;
  /** Cache seam — accepted, not used; a separate PR owns caching. */
  cache?: RetrievalCache;
  /** Chunk target words. Default 180. */
  targetWords?: number;
};

export type RetrievalStatus = 'ready' | 'no-match' | 'preparation-required' | 'refused';

export type StageTrace = {
  stage: 'descriptions' | 'local-bm25' | 'narrow-bundles' | 'wide-bundles' | 'prepare' | 'neighbors';
  /** Provider calls made in this stage; 0 for local stages. */
  calls: number;
  kept: string[];
  dropped: string[];
  note: string;
};

export type RetrievalResult = {
  status: RetrievalStatus;
  request: string;
  /** `ready` only: the top-ranked bundle's prepared passages. */
  passages: PreparedPassage[];
  /** `preparation-required` only: selected but unprepared passages — ids and offsets, never text. */
  pending: UnpreparedPassage[];
  /** Compact per-stage trace of what ran and what it kept/dropped. */
  trace: StageTrace[];
  /** Original-source pointers for every source that entered the chain. */
  sources: { id: string; contentSHA: string; description: string }[];
};

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * A run whose judge coverage is incomplete: transport errors, unanswered
 * records, failed validation, or bookkeeping gaps. A `noMatch` — or an
 * accepted top pick — on top of an incomplete run is not "nothing matched"
 * / "a match": it is unknown, and fails closed to `refused`. Checked BEFORE
 * any accept path, not only on no-match.
 */
function runIncomplete(run: FetchRun): boolean {
  if (run.errors.length) return true;
  const byKind = run.manifest.byKind ?? {};
  for (const kind of ['unanswered', 'failed_validation', 'unaccounted', 'insufficient_evidence'] as const) {
    if ((byKind[kind] ?? []).length) return true;
  }
  return false;
}

/**
 * Fail closed on a judge id that was never asked about in this stage.
 * `runFetch` only ranks asked records, so this is defensive — but a foreign
 * passage must never flow into a result silently.
 */
export function validateSelection(askedIds: Set<string>, selectedIds: string[]): void {
  for (const id of selectedIds) {
    if (!askedIds.has(id)) throw new RetrievalError(`judge selected unknown id ${JSON.stringify(id)} — failing closed`);
  }
}

async function judgeBundles(
  bundles: PassageBundle[],
  request: string,
  options: RetrievalOptions
): Promise<FetchRun> {
  const catalog: FetchCatalogEntry[] = bundles.map(b => ({ id: b.id, text: b.providerText }));
  return runFetch(catalog, request, {
    transport: options.transport,
    k: bundles.length,
    prefilter: 0, // bundles are already narrowed; the judge sees all of them
    maxRetries: options.maxRetries ?? 0,
    timeoutMs: options.timeoutMs
  });
}

/**
 * Neighbor lookup for the fourth judge stage: for each selected chunk, the
 * immediately preceding and following chunk in the same source. Dedupes by
 * (sourceId, chunkIndex) and never drops the base selection. Out-of-range
 * neighbors (first/last chunk) simply contribute nothing on that side.
 */
export function addNeighbors(selected: SourceChunk[], chunksBySource: Map<string, SourceChunk[]>): SourceChunk[] {
  assertChunkOffsets(selected);
  const out = selected.slice();
  const seen = new Set(selected.map(c => `${c.sourceId}#${c.chunkIndex}`));
  for (const c of selected) {
    const all = chunksBySource.get(c.sourceId) ?? [];
    for (const n of [c.chunkIndex - 1, c.chunkIndex + 1]) {
      const nb = all[n];
      if (!nb) continue;
      const key = `${nb.sourceId}#${nb.chunkIndex}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(nb);
    }
  }
  return out;
}

/**
 * Run the experimental retrieval chain. Opt-in only; see the module header.
 * Never throws for judge behaviour (refusals, no-matches, transport errors
 * become terminal statuses); throws `RetrievalError` only for caller input
 * problems (bad options, unaffirmed descriptions, duplicate source ids,
 * foreign judge ids, invalid chunk offsets) — all fail-closed, never
 * guessed around.
 */
export async function retrieveSources(
  sources: RetrievalSource[],
  request: string,
  options: RetrievalOptions
): Promise<RetrievalResult> {
  if (!options?.transport) throw new RetrievalError('Provide a transport (an Evaluator) to retrieve sources');
  if (options.descriptionsReviewed !== true) {
    throw new RetrievalError(
      'descriptionsReviewed must be true: descriptions are caller-reviewed provider-safe input sent to the judge'
    );
  }
  if (typeof request !== 'string' || !request.trim()) throw new RetrievalError('Provide a non-empty request');
  if (!Array.isArray(sources)) throw new RetrievalError('Provide a sources array');
  const seen = new Set<string>();
  for (const s of sources) {
    if (!s || typeof s.id !== 'string' || !s.id) throw new RetrievalError('Every source needs a non-empty id');
    if (seen.has(s.id)) throw new RetrievalError(`Duplicate source id ${JSON.stringify(s.id)}`);
    seen.add(s.id);
    if (typeof s.description !== 'string') throw new RetrievalError(`Source ${JSON.stringify(s.id)} needs a description string`);
    if (typeof s.text !== 'string') throw new RetrievalError(`Source ${JSON.stringify(s.id)} needs text as a string`);
  }
  const docK = options.docK ?? 3;
  const chunkK = options.chunkK ?? 4;
  const includeNeighbors = options.includeNeighbors ?? false;
  const maxStages = options.maxStages ?? (includeNeighbors ? 4 : 3);
  const targetWords = options.targetWords ?? DEFAULT_CHUNK_TARGET_WORDS;
  const prefilter = options.prefilter ?? 8;
  const expectedPolicy = options.expectedPolicy ?? 'reviewed';
  if (typeof expectedPolicy !== 'string' || !expectedPolicy) throw new RetrievalError('expectedPolicy must be a non-empty string');
  if (!Number.isInteger(docK) || docK < 1) throw new RetrievalError('docK must be a positive integer');
  if (!Number.isInteger(chunkK) || chunkK < 1) throw new RetrievalError('chunkK must be a positive integer');
  if (!Number.isInteger(maxStages) || maxStages < 1) throw new RetrievalError('maxStages must be a positive integer');
  if (!Number.isInteger(prefilter) || prefilter < 0) throw new RetrievalError('prefilter must be a non-negative integer');

  const trace: StageTrace[] = [];
  const descriptions = new Map(sources.map(s => [s.id, s.description]));
  const sourcePointers = sources.map(s => ({ id: s.id, contentSHA: sha256Hex(s.text), description: s.description }));
  const refused = (note: string): RetrievalResult =>
    ({ status: 'refused', request, passages: [], pending: [], trace, sources: sourcePointers });

  // Chunk ALL source documents once, up front. Everything downstream — the
  // narrow shortlist, the global wide recovery, the neighbor lookup —
  // reads this single chunking.
  const chunksBySource = new Map<string, SourceChunk[]>();
  const allChunks: SourceChunk[] = [];
  for (const s of sources) {
    const chunks = chunkSource(s.id, s.text, targetWords);
    assertChunkOffsets(chunks);
    chunksBySource.set(s.id, chunks);
    allChunks.push(...chunks);
  }

  // -- Stage 1: descriptions (filter8), provider --------------------------------
  let descRun: FetchRun;
  try {
    descRun = await runFetch(
      sources.map(s => ({ id: s.id, text: s.description })),
      request,
      {
        transport: options.transport, k: docK,
        prefilter,
        maxRetries: options.maxRetries ?? 0,
        timeoutMs: options.timeoutMs
      }
    );
  } catch (err) {
    trace.push({ stage: 'descriptions', calls: 0, kept: [], dropped: [], note: `transport error: ${errMessage(err)}` });
    return refused('descriptions stage transport error');
  }
  let stagesUsed = 1;
  // Completeness BEFORE any interpretation of the gate: a no-match on top
  // of an incomplete run is unknown, not "nothing matched".
  if (runIncomplete(descRun)) {
    trace.push({ stage: 'descriptions', calls: descRun.calls, kept: [], dropped: [], note: `judge coverage incomplete: ${descRun.errors.join('; ')}` });
    return refused('descriptions stage judge coverage incomplete');
  }
  // Experiment parity (first.mjs): the description shortlist is the
  // ranking's top docK — `ranked` when any record beat "none of these",
  // otherwise the top docK of `allScored`. The direct-fit gate only names
  // the deferral in the trace; a deferred gate never aborts the chain and
  // never swaps the list. A description-only run can never be `ready`.
  const descGate = applyDirectFitGate(descRun);
  const descShortlist = descRun.ranked.length ? descRun.ranked : descRun.allScored;
  const rankedSourceIds = descShortlist.slice(0, docK).map(r => r.id);
  if (!rankedSourceIds.length) {
    trace.push({ stage: 'descriptions', calls: descRun.calls, kept: [], dropped: sources.map(s => s.id), note: 'gate deferred and no descriptions were judged' });
    return { status: 'no-match', request, passages: [], pending: [], trace, sources: sourcePointers };
  }
  const descNote = descGate.noMatch
    ? `gate deferred (low confidence); description ranking chose top ${rankedSourceIds.length}`
    : `direct-fit top ${rankedSourceIds.length}`;
  validateSelection(new Set(sources.map(s => s.id)), rankedSourceIds);
  trace.push({
    stage: 'descriptions', calls: descRun.calls,
    kept: rankedSourceIds,
    dropped: sources.map(s => s.id).filter(id => !rankedSourceIds.includes(id)),
    note: descNote
  });

  // -- Local: BM25 global top chunkK across all chunks in the shortlist -------
  const shortlist = new Set(rankedSourceIds);
  const shortlistChunks = allChunks.filter(c => shortlist.has(c.sourceId));
  const narrowTop = topChunksGlobal(shortlistChunks, descriptions, request, chunkK);
  trace.push({
    stage: 'local-bm25', calls: 0, kept: narrowTop.map(chunkIdentity), dropped: [],
    note: `global top-${chunkK} chunks across all chunks in the top-${rankedSourceIds.length} doc(s)`
  });
  if (!narrowTop.length) {
    return { status: 'no-match', request, passages: [], pending: [], trace, sources: sourcePointers };
  }

  /**
   * Preparation gate for a provider passage pool: EVERY chunk the judge is
   * about to see must carry a valid reviewed record, or the whole run is
   * preparation-required — checked BEFORE any passage call, never a raw
   * fallback. Returns the checked pairs, or the terminal result.
   */
  const checkPool = (poolChunks: SourceChunk[], stage: string): CheckedPair[] | RetrievalResult => {
    const { prepared, pending } = preparePassages(poolChunks, descriptions, options.preparation, expectedPolicy);
    if (pending.length) {
      trace.push({
        stage: 'prepare', calls: 0, kept: [],
        dropped: pending.map(p => chunkIdentity(p)),
        note: `${stage}: ${pending.length} passage(s) missing/stale/unreviewed preparation — withheld before any provider passage call`
      });
      return { status: 'preparation-required', request, passages: [], pending, trace, sources: sourcePointers };
    }
    return poolChunks.map((c, i) => ({ chunk: c, passage: prepared[i] }));
  };
  const isTerminal = (v: CheckedPair[] | RetrievalResult): v is RetrievalResult => !Array.isArray(v);

  /**
   * Accept path: re-verify the artifact binding of the top-ranked bundle's
   * chunks, then return ONLY that bundle's supporting prepared passages —
   * never every low-ranked candidate.
   */
  const acceptTopBundle = (bundle: PassageBundle): RetrievalResult => {
    const { prepared, pending } = preparePassages(bundle.chunks, descriptions, options.preparation, expectedPolicy);
    trace.push({
      stage: 'prepare', calls: 0,
      kept: prepared.map(p => chunkIdentity(p)),
      dropped: pending.map(p => chunkIdentity(p)),
      note: `accepted bundle ${bundle.id}: binding re-verified, ${prepared.length} prepared, ${pending.length} preparation-required`
    });
    if (pending.length) {
      return { status: 'preparation-required', request, passages: [], pending, trace, sources: sourcePointers };
    }
    return { status: 'ready', request, passages: prepared, pending: [], trace, sources: sourcePointers };
  };

  type StageOutcome =
    | { done: true; result: RetrievalResult }
    | { done: false; accepted?: PassageBundle };

  /**
   * Run one document-bundle judge stage: judge, fail closed on foreign ids,
   * check completeness BEFORE any accept path, then gate. Returns the
   * terminal result, or the single top-ranked bundle on acceptance.
   */
  const runJudgeStage = async (
    stage: 'narrow-bundles' | 'wide-bundles' | 'neighbors',
    bundles: PassageBundle[]
  ): Promise<StageOutcome> => {
    let run: FetchRun;
    try {
      run = await judgeBundles(bundles, request, options);
    } catch (err) {
      trace.push({ stage, calls: 0, kept: [], dropped: [], note: `transport error: ${errMessage(err)}` });
      return { done: true, result: refused(`${stage} stage transport error`) };
    }
    stagesUsed += 1;
    const gate = applyDirectFitGate(run);
    const asked = new Set(bundles.map(b => b.id));
    validateSelection(asked, gate.noMatch ? [] : gate.ranked.map(r => r.id));
    if (runIncomplete(run)) {
      trace.push({ stage, calls: run.calls, kept: [], dropped: [], note: `judge coverage incomplete: ${run.errors.join('; ')}` });
      return { done: true, result: refused(`${stage} stage judge coverage incomplete`) };
    }
    trace.push({
      stage, calls: run.calls,
      kept: gate.noMatch ? [] : gate.ranked.map(r => r.id),
      dropped: bundles.map(b => b.id).filter(id => gate.noMatch || !gate.ranked.some(r => r.id === id)),
      note: gate.noMatch ? 'gate refused bundles' : `direct-fit top bundle ${gate.ranked[0].id}`
    });
    if (gate.noMatch) return { done: false };
    const top = bundles.find(b => b.id === gate.ranked[0].id);
    if (!top) throw new RetrievalError(`judge selected unknown bundle id ${JSON.stringify(gate.ranked[0].id)} — failing closed`);
    return { done: false, accepted: top };
  };

  // -- Stage 2: narrow bundles (document-grouped prepared passages), provider --
  if (stagesUsed >= maxStages) return refused('narrow-bundles stage exceeds maxStages');
  const narrowChecked = checkPool(narrowTop, 'narrow-bundles');
  if (isTerminal(narrowChecked)) return narrowChecked;
  const narrowBundles = groupPairsIntoBundles(narrowChecked, descriptions);
  const narrowOutcome = await runJudgeStage('narrow-bundles', narrowBundles);
  if (narrowOutcome.done) return narrowOutcome.result;
  if (narrowOutcome.accepted) return acceptTopBundle(narrowOutcome.accepted);

  // -- Stage 3: wide bundles (GLOBAL corpus top chunkK), ONLY on narrow refusal
  if (stagesUsed >= maxStages) return refused('wide-bundles stage exceeds maxStages (narrow bundles refused)');
  const wideTop = topChunksGlobal(allChunks, descriptions, request, chunkK);
  trace.push({
    stage: 'local-bm25', calls: 0, kept: wideTop.map(chunkIdentity), dropped: [],
    note: `wide selection: global top-${chunkK} chunks across the whole corpus (recovers outside the shortlist)`
  });
  const wideChecked = checkPool(wideTop, 'wide-bundles');
  if (isTerminal(wideChecked)) return wideChecked;
  const wideBundles = groupPairsIntoBundles(wideChecked, descriptions);
  const wideOutcome = await runJudgeStage('wide-bundles', wideBundles);
  if (wideOutcome.done) return wideOutcome.result;
  if (wideOutcome.accepted) return acceptTopBundle(wideOutcome.accepted);

  // -- Stage 4: neighbors — a FOURTH judge stage, ONLY after wide refusal -----
  if (includeNeighbors) {
    if (stagesUsed >= maxStages) return refused('neighbors stage exceeds maxStages (wide bundles refused)');
    const neighborChunks = addNeighbors(wideTop, chunksBySource);
    trace.push({
      stage: 'neighbors', calls: 0,
      kept: neighborChunks.map(chunkIdentity), dropped: [],
      note: `local: added ${neighborChunks.length - wideTop.length} adjacent chunk(s) for judging; base selection kept`
    });
    const neighborChecked = checkPool(neighborChunks, 'neighbors');
    if (isTerminal(neighborChecked)) return neighborChecked;
    const neighborBundles = groupPairsIntoBundles(neighborChecked, descriptions);
    const neighborOutcome = await runJudgeStage('neighbors', neighborBundles);
    if (neighborOutcome.done) return neighborOutcome.result;
    if (neighborOutcome.accepted) return acceptTopBundle(neighborOutcome.accepted);
  }

  return { status: 'no-match', request, passages: [], pending: [], trace, sources: sourcePointers };
}
