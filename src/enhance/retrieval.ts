/**
 * EXPERIMENTAL SOURCE RETRIEVAL — opt-in library, not a default path.
 *
 * Status: experimental. The lead is still measuring whether the neighbor
 * stage helps; nothing here carries an accuracy promise, and none of this
 * runs unless a caller opts in. It changes no CLI, no fleet default, and no
 * existing `runFetch` behaviour — it only reuses `runFetch`,
 * `prefilterCatalog` and `applyDirectFitGate` as library pieces.
 *
 * What it does: given a small set of source documents (each with a short
 * description and a full text), it runs a bounded async stage chain to pull
 * prepared passages out of the most relevant sources:
 *
 *   1. `descriptions` (provider) — `runFetch` over the source descriptions
 *      with the filter-8 local prefilter, gated by `applyDirectFitGate`, so
 *      only a source the relevance rubric called a direct fit survives.
 *   2. local BM25 (local, no provider call) — each surviving source is cut
 *      into deterministic heading-aware ~180-word chunks
 *      (`chunkSource`), and `prefilterCatalog` keeps the local top-4 chunks
 *      per source.
 *   3. `narrow-bundles` (provider) — one bundle per chunk, judged by
 *      `runFetch` and gated by `applyDirectFitGate`.
 *   4. `wide-bundles` (provider, ONLY on refusal) — when the narrow stage's
 *      gate refuses, each source's kept chunks are re-judged as one wide
 *      bundle per source.
 *   5. optional `neighbors` stage (OFF by default) — adds the immediately
 *      preceding/following chunk in the same source, deduped, never dropping
 *      the base selection.
 *   6. `prepare` (local) — every selected passage is bound to its source's
 *      content SHA and a preparation policy via an explicit
 *      callback/map. An unknown, stale, or unreviewed selection returns
 *      `preparation_required` — NEVER the raw text as a fallback. There is no
 *      automatic redaction and no automatic approval.
 *
 * Stages are bounded by `maxStages` (provider stages only — a stage's
 * provider calls may still batch; the bound is not an HTTP-call cap).
 * Incomplete answers, transport errors, and unknown judge ids fail closed:
 * the result is `refused`, never a guess.
 *
 * A minimal cache adapter seam (`RetrievalCache` + `cacheKeyFor`) is
 * accepted in options for a future PR; no cache is implemented here and
 * passing one changes nothing yet.
 */
import { createHash } from 'node:crypto';
import { applyDirectFitGate, prefilterCatalog, runFetch, type FetchCatalogEntry, type FetchRun } from './fetch.ts';
import type { Evaluator } from '../types.ts';

export class RetrievalError extends Error {}

/** SHA-256 hex of a UTF-8 string. */
export function sha256Hex(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

// ---------------------------------------------------------------------------
// Sources and chunks
// ---------------------------------------------------------------------------

/** One source document: a short description for the judge, and the full text to chunk locally. */
export type RetrievalSource = {
  id: string;
  /** One-paragraph description, judged in the descriptions stage. */
  description: string;
  /** Full source text, chunked locally — never sent whole to the judge. */
  text: string;
};

/**
 * One deterministic chunk of a source. `sourceSHA` is the SHA-256 of the
 * full source text the chunk was cut from, so a passage can always be bound
 * back to the exact bytes it came from. Lines are 1-based; `startLine` is
 * inclusive and `endLine` is inclusive.
 */
export type SourceChunk = {
  sourceId: string;
  sourceSHA: string;
  /** 0-based chunk index within the source. */
  chunkIndex: number;
  startLine: number;
  endLine: number;
  /** Nearest preceding markdown heading, `#`s stripped; null when the chunk precedes the first heading. */
  heading: string | null;
  text: string;
};

/** Default chunk target: ~180 words, per the brief. */
export const DEFAULT_CHUNK_TARGET_WORDS = 180;

const HEADING_RE = /^(#{1,6})\s+(.*\S)\s*$/;

function wordCount(text: string): number {
  return text.split(/\s+/).filter(Boolean).length;
}

/**
 * Cut a source into deterministic heading-aware ~180-word chunks at line
 * boundaries. A markdown heading opens a new chunk (the heading line leads
 * it); a section longer than `targetWords` is split at line boundaries once
 * it reaches the target, so chunks run ~180 words and never split a line.
 * The section heading carries over to continuation chunks, so every passage
 * stays attributable. Pure function of (sourceId, text, targetWords): no
 * randomness, no I/O, stable order.
 */
export function chunkSource(sourceId: string, text: string, targetWords: number = DEFAULT_CHUNK_TARGET_WORDS): SourceChunk[] {
  if (typeof sourceId !== 'string' || !sourceId) throw new RetrievalError('chunkSource needs a non-empty sourceId');
  if (typeof text !== 'string') throw new RetrievalError('chunkSource needs source text as a string');
  if (!Number.isInteger(targetWords) || targetWords < 1) throw new RetrievalError('targetWords must be a positive integer');
  if (!text.trim()) return [];
  const lines = text.split('\n');
  const sourceSHA = sha256Hex(text);
  const chunks: SourceChunk[] = [];
  let heading: string | null = null;
  let start = 0;
  let words = 0;
  let index = 0;
  const close = (endExclusive: number): void => {
    if (endExclusive <= start) return;
    chunks.push({
      sourceId, sourceSHA, chunkIndex: index++,
      startLine: start + 1, endLine: endExclusive,
      heading, text: lines.slice(start, endExclusive).join('\n')
    });
  };
  for (let i = 0; i < lines.length; i++) {
    const m = HEADING_RE.exec(lines[i]);
    if (m) {
      if (i > start) { close(i); start = i; words = 0; }
      heading = m[2].trim();
    }
    words += wordCount(lines[i]);
    if (words >= targetWords) { close(i + 1); start = i + 1; words = 0; }
  }
  close(lines.length);
  return chunks;
}

/**
 * Validate chunks: 1-based lines, start <= end, non-empty text, a valid
 * sourceSHA, and no duplicate (sourceId, chunkIndex) pairs. Selections are
 * routinely subsets of a source's full chunk list, so the chunk indexes are
 * NOT required to form a 0-based sequence. Throws `RetrievalError` — invalid
 * offsets never flow silently into a result.
 */
export function assertChunkOffsets(chunks: SourceChunk[]): void {
  if (!Array.isArray(chunks)) throw new RetrievalError('chunks must be an array');
  const seen = new Set<string>();
  for (const c of chunks) {
    if (!c || typeof c.sourceId !== 'string' || !c.sourceId) throw new RetrievalError('chunk has no sourceId');
    if (typeof c.sourceSHA !== 'string' || !/^[0-9a-f]{64}$/.test(c.sourceSHA)) throw new RetrievalError(`chunk ${c.sourceId}#${c.chunkIndex} has no valid sourceSHA`);
    if (!Number.isInteger(c.chunkIndex) || c.chunkIndex < 0) throw new RetrievalError(`chunk has invalid chunkIndex`);
    if (!Number.isInteger(c.startLine) || !Number.isInteger(c.endLine) || c.startLine < 1 || c.endLine < c.startLine) {
      throw new RetrievalError(`chunk ${c.sourceId}#${c.chunkIndex} has invalid offsets startLine=${c.startLine} endLine=${c.endLine}`);
    }
    if (typeof c.text !== 'string' || !c.text) throw new RetrievalError(`chunk ${c.sourceId}#${c.chunkIndex} has empty text`);
    const key = `${c.sourceId}#${c.chunkIndex}`;
    if (seen.has(key)) throw new RetrievalError(`duplicate chunk ${key}`);
    seen.add(key);
  }
}

// ---------------------------------------------------------------------------
// Preparation: explicit binding of passages to reviewed source bytes
// ---------------------------------------------------------------------------

export type PreparationStatus = 'reviewed' | 'pending' | 'rejected';

/**
 * A record that a source's exact bytes were prepared under a policy.
 * Binding is by `contentSHA`: a record only clears a passage when its
 * `contentSHA` equals the chunk's `sourceSHA`.
 */
export type PreparationRecord = {
  contentSHA: string;
  /** The preparation policy this record was reviewed under. */
  policy: string;
  status: PreparationStatus;
};

/** Explicit preparation source: a contentSHA-keyed map, or a callback the caller owns. */
export type PreparationSource =
  | Map<string, PreparationRecord>
  | ((sourceId: string, contentSHA: string) => PreparationRecord | undefined);

function lookupPreparation(prep: PreparationSource | undefined, sourceId: string, contentSHA: string): PreparationRecord | undefined {
  if (!prep) return undefined;
  if (prep instanceof Map) return prep.get(contentSHA);
  return prep(sourceId, contentSHA);
}

/** A passage cleared for use: bound to its source bytes and a reviewed preparation policy. */
export type PreparedPassage = {
  sourceId: string;
  description: string;
  /** The source content SHA this passage was prepared against. */
  contentSHA: string;
  /** The preparation policy that reviewed it. */
  policy: string;
  chunkIndex: number;
  startLine: number;
  endLine: number;
  heading: string | null;
  text: string;
};

export type UnpreparedReason = 'unknown' | 'stale' | 'unreviewed';

/**
 * A selected passage that may NOT be used. Carries no text — never a raw
 * fallback. `unknown`: no preparation record; `stale`: the record's
 * contentSHA does not match the chunk's bytes (source changed since review);
 * `unreviewed`: a record exists but its status is not `reviewed`.
 */
export type UnpreparedPassage = {
  sourceId: string;
  chunkIndex: number;
  startLine: number;
  endLine: number;
  reason: UnpreparedReason;
  /** The SHA the chunk was cut from, for the audit trail. */
  expectedSHA: string;
};

/**
 * Bind selected chunks to preparation records. Every prepared passage is
 * bound to its source contentSHA and preparation policy; anything else lands
 * in `pending` with its reason and no text. No automatic redaction, no
 * automatic approval — an unreviewed selection is reported, not laundered.
 */
export function preparePassages(
  chunks: SourceChunk[],
  descriptions: Map<string, string>,
  preparation: PreparationSource | undefined
): { prepared: PreparedPassage[]; pending: UnpreparedPassage[] } {
  assertChunkOffsets(chunks);
  const prepared: PreparedPassage[] = [];
  const pending: UnpreparedPassage[] = [];
  for (const c of chunks) {
    const base = { sourceId: c.sourceId, chunkIndex: c.chunkIndex, startLine: c.startLine, endLine: c.endLine, expectedSHA: c.sourceSHA };
    const record = lookupPreparation(preparation, c.sourceId, c.sourceSHA);
    if (!record) { pending.push({ ...base, reason: 'unknown' }); continue; }
    if (record.contentSHA !== c.sourceSHA) { pending.push({ ...base, reason: 'stale' }); continue; }
    if (record.status !== 'reviewed') { pending.push({ ...base, reason: 'unreviewed' }); continue; }
    prepared.push({
      sourceId: c.sourceId, description: descriptions.get(c.sourceId) ?? '',
      contentSHA: c.sourceSHA, policy: record.policy,
      chunkIndex: c.chunkIndex, startLine: c.startLine, endLine: c.endLine,
      heading: c.heading, text: c.text
    });
  }
  return { prepared, pending };
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
  /** Sources kept after the descriptions stage. Default 3 ("ranked top3 docs"). */
  docK?: number;
  /** Local BM25 chunks kept per gated source. Default 4 ("top4 chunks"). */
  chunkK?: number;
  /** Local prefilter width inside the descriptions stage. Default 8 ("filter8"). */
  prefilter?: number;
  /** Max provider stages (descriptions, narrow-bundles, wide-bundles). Default 3. */
  maxStages?: number;
  /** Retries per provider call. Default 0 — retrieval never retries a judge call. */
  maxRetries?: number;
  /** Per-call timeout in ms, passed through to the sweep engine. */
  timeoutMs?: number;
  /** Optional fourth stage: add the immediately adjacent chunk(s) in the same source. Default false (OFF). */
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
  /** `ready` only: prepared passages, grouped per source in source-rank order, each with its description. */
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
 * records, failed validation, or bookkeeping gaps. A `noMatch` on top of an
 * incomplete run is not "nothing matched" — it is unknown, and fails closed
 * to `refused`.
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

type Bundle = { id: string; chunk: SourceChunk };

async function judgeBundles(
  bundles: Bundle[],
  request: string,
  options: RetrievalOptions
): Promise<FetchRun> {
  const catalog: FetchCatalogEntry[] = bundles.map(b => ({ id: b.id, text: b.chunk.text }));
  return runFetch(catalog, request, {
    transport: options.transport,
    k: bundles.length,
    prefilter: 0, // bundles are already narrowed; the judge sees all of them
    maxRetries: options.maxRetries ?? 0,
    timeoutMs: options.timeoutMs
  });
}

/**
 * Optional fourth stage (OFF by default): for each selected chunk, add the
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
 * problems (bad options, duplicate source ids, foreign judge ids, invalid
 * offsets) — all fail-closed, never guessed around.
 */
export async function retrieveSources(
  sources: RetrievalSource[],
  request: string,
  options: RetrievalOptions
): Promise<RetrievalResult> {
  if (!options?.transport) throw new RetrievalError('Provide a transport (an Evaluator) to retrieve sources');
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
  const maxStages = options.maxStages ?? 3;
  const targetWords = options.targetWords ?? DEFAULT_CHUNK_TARGET_WORDS;
  const prefilter = options.prefilter ?? 8;
  if (!Number.isInteger(docK) || docK < 1) throw new RetrievalError('docK must be a positive integer');
  if (!Number.isInteger(chunkK) || chunkK < 1) throw new RetrievalError('chunkK must be a positive integer');
  if (!Number.isInteger(maxStages) || maxStages < 1) throw new RetrievalError('maxStages must be a positive integer');
  if (!Number.isInteger(prefilter) || prefilter < 0) throw new RetrievalError('prefilter must be a non-negative integer');

  const trace: StageTrace[] = [];
  const byId = new Map(sources.map(s => [s.id, s]));
  const descriptions = new Map(sources.map(s => [s.id, s.description]));
  const sourcePointers = sources.map(s => ({ id: s.id, contentSHA: sha256Hex(s.text), description: s.description }));
  const refused = (note: string): RetrievalResult =>
    ({ status: 'refused', request, passages: [], pending: [], trace, sources: sourcePointers });

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
  const descGate = applyDirectFitGate(descRun);
  trace.push({
    stage: 'descriptions', calls: descRun.calls,
    kept: descGate.noMatch ? [] : descGate.ranked.map(r => r.id),
    dropped: sources.map(s => s.id).filter(id => descGate.noMatch || !descGate.ranked.some(r => r.id === id)),
    note: descGate.noMatch ? 'gate refused: no direct fit among descriptions' : `direct-fit top ${descGate.ranked.length}`
  });
  if (descGate.noMatch) {
    // A no-match WITH judge errors is not "nothing matched" — it is unknown. Fail closed.
    if (runIncomplete(descRun)) return refused(`descriptions stage judge coverage incomplete: ${descRun.errors.join('; ')}`);
    return { status: 'no-match', request, passages: [], pending: [], trace, sources: sourcePointers };
  }
  const rankedSourceIds = descGate.ranked.map(r => r.id);
  validateSelection(new Set(sources.map(s => s.id)), rankedSourceIds);

  // -- Local: chunk the gated sources, BM25 top chunkK per source ----------------
  const chunksBySource = new Map<string, SourceChunk[]>();
  const keptChunks: SourceChunk[] = [];
  const bm25Kept: string[] = [];
  for (const id of rankedSourceIds) {
    const src = byId.get(id);
    if (!src) throw new RetrievalError(`unknown source id ${JSON.stringify(id)} — failing closed`);
    const chunks = chunkSource(src.id, src.text, targetWords);
    chunksBySource.set(id, chunks);
    if (!chunks.length) continue;
    const entries: FetchCatalogEntry[] = chunks.map(c => ({ id: `${c.sourceId}#${c.chunkIndex}`, text: c.text }));
    const top = prefilterCatalog(entries, request, chunkK);
    const keepIds = new Set(top.kept.map(e => e.id));
    for (const c of chunks) {
      if (keepIds.has(`${c.sourceId}#${c.chunkIndex}`)) { keptChunks.push(c); bm25Kept.push(`${c.sourceId}#${c.chunkIndex}`); }
    }
  }
  trace.push({
    stage: 'local-bm25', calls: 0, kept: bm25Kept,
    dropped: [],
    note: `chunked ${rankedSourceIds.length} gated source(s), kept local BM25 top-${chunkK} chunks per source`
  });
  if (!keptChunks.length) {
    return { status: 'no-match', request, passages: [], pending: [], trace, sources: sourcePointers };
  }

  // -- Stage 2: narrow bundles (one chunk per bundle), provider ------------------
  if (stagesUsed >= maxStages) return refused('narrow-bundles stage exceeds maxStages');
  const narrowBundles: Bundle[] = keptChunks.map(c => ({ id: `${c.sourceId}#${c.chunkIndex}`, chunk: c }));
  let narrowRun: FetchRun;
  try {
    narrowRun = await judgeBundles(narrowBundles, request, options);
  } catch (err) {
    trace.push({ stage: 'narrow-bundles', calls: 0, kept: [], dropped: [], note: `transport error: ${errMessage(err)}` });
    return refused('narrow-bundles stage transport error');
  }
  stagesUsed += 1;
  const narrowGate = applyDirectFitGate(narrowRun);
  validateSelection(new Set(narrowBundles.map(b => b.id)), narrowGate.noMatch ? [] : narrowGate.ranked.map(r => r.id));
  trace.push({
    stage: 'narrow-bundles', calls: narrowRun.calls,
    kept: narrowGate.noMatch ? [] : narrowGate.ranked.map(r => r.id),
    dropped: narrowBundles.map(b => b.id).filter(id => narrowGate.noMatch || !narrowGate.ranked.some(r => r.id === id)),
    note: narrowGate.noMatch ? 'gate refused narrow bundles' : `direct-fit top ${narrowGate.ranked.length} narrow bundle(s)`
  });

  // -- Stage 3: wide bundles (one per source), ONLY on refusal, provider --------
  let selectedBundles: Bundle[];
  if (!narrowGate.noMatch) {
    const byBundleId = new Map(narrowBundles.map(b => [b.id, b]));
    selectedBundles = narrowGate.ranked.map(r => {
      const b = byBundleId.get(r.id);
      if (!b) throw new RetrievalError(`judge selected unknown bundle id ${JSON.stringify(r.id)} — failing closed`);
      return b;
    });
  } else {
    if (runIncomplete(narrowRun)) return refused(`narrow-bundles stage judge coverage incomplete: ${narrowRun.errors.join('; ')}`);
    if (stagesUsed >= maxStages) return refused('wide-bundles stage exceeds maxStages (narrow bundles refused)');
    const perSource = new Map<string, SourceChunk[]>();
    for (const c of keptChunks) {
      const list = perSource.get(c.sourceId) ?? [];
      list.push(c);
      perSource.set(c.sourceId, list);
    }
    const wideBundles: Bundle[] = [...perSource.entries()].map(([sourceId, chunks]) => ({
      // The wide bundle's "chunk" is a synthetic view over the source's kept
      // chunks; its text is what the judge sees. Offsets stay per-chunk below.
      id: `${sourceId}#wide`,
      chunk: {
        sourceId, sourceSHA: chunks[0].sourceSHA, chunkIndex: -1,
        startLine: Math.min(...chunks.map(c => c.startLine)),
        endLine: Math.max(...chunks.map(c => c.endLine)),
        heading: chunks[0].heading,
        text: chunks.map(c => c.text).join('\n\n')
      }
    }));
    let wideRun: FetchRun;
    try {
      wideRun = await judgeBundles(wideBundles, request, options);
    } catch (err) {
      trace.push({ stage: 'wide-bundles', calls: 0, kept: [], dropped: [], note: `transport error: ${errMessage(err)}` });
      return refused('wide-bundles stage transport error');
    }
    stagesUsed += 1;
    const wideGate = applyDirectFitGate(wideRun);
    validateSelection(new Set(wideBundles.map(b => b.id)), wideGate.noMatch ? [] : wideGate.ranked.map(r => r.id));
    trace.push({
      stage: 'wide-bundles', calls: wideRun.calls,
      kept: wideGate.noMatch ? [] : wideGate.ranked.map(r => r.id),
      dropped: wideBundles.map(b => b.id).filter(id => wideGate.noMatch || !wideGate.ranked.some(r => r.id === id)),
      note: wideGate.noMatch ? 'gate refused wide bundles' : `direct-fit top ${wideGate.ranked.length} wide bundle(s)`
    });
    if (wideGate.noMatch) {
      if (runIncomplete(wideRun)) return refused(`wide-bundles stage judge coverage incomplete: ${wideRun.errors.join('; ')}`);
      return { status: 'no-match', request, passages: [], pending: [], trace, sources: sourcePointers };
    }
    // A selected wide bundle resolves back to the source's kept chunks —
    // the unit the judge saw, the chunks the preparation binds to.
    selectedBundles = [];
    for (const r of wideGate.ranked) {
      const sourceId = r.id.slice(0, -'#wide'.length);
      const chunks = perSource.get(sourceId);
      if (!chunks) throw new RetrievalError(`judge selected unknown wide bundle ${JSON.stringify(r.id)} — failing closed`);
      for (const c of chunks) selectedBundles.push({ id: `${c.sourceId}#${c.chunkIndex}`, chunk: c });
    }
  }

  // -- Optional stage 4: neighbors (OFF by default) ------------------------------
  let finalChunks = selectedBundles.map(b => b.chunk);
  if (options.includeNeighbors) {
    finalChunks = addNeighbors(finalChunks, chunksBySource);
    trace.push({
      stage: 'neighbors', calls: 0,
      kept: finalChunks.map(c => `${c.sourceId}#${c.chunkIndex}`),
      dropped: [],
      note: `neighbor stage on: ${finalChunks.length - selectedBundles.length} adjacent chunk(s) added, base selection kept`
    });
  }

  // -- Prepare: bind every selected passage, or report preparation_required ------
  const { prepared, pending } = preparePassages(finalChunks, descriptions, options.preparation);
  trace.push({
    stage: 'prepare', calls: 0,
    kept: prepared.map(p => `${p.sourceId}#${p.chunkIndex}`),
    dropped: pending.map(p => `${p.sourceId}#${p.chunkIndex}`),
    note: `${prepared.length} prepared, ${pending.length} preparation-required`
  });
  if (pending.length) {
    // Withhold the whole selection: a partially-cleared bundle is not a
    // cleared answer, and unprepared text is never returned as a fallback.
    return { status: 'preparation-required', request, passages: [], pending, trace, sources: sourcePointers };
  }
  // Group prepared passages per source, in source-rank order, with description.
  const rank = new Map(rankedSourceIds.map((id, i) => [id, i]));
  prepared.sort((a, b) => (rank.get(a.sourceId) ?? 0) - (rank.get(b.sourceId) ?? 0) || a.chunkIndex - b.chunkIndex);
  return { status: 'ready', request, passages: prepared, pending: [], trace, sources: sourcePointers };
}
