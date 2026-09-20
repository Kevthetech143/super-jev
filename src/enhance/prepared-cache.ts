import { createHash, randomBytes } from 'node:crypto';
import { chmodSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Opt-in persistent cache for REVIEWED prepared artifacts.
 *
 * A "prepared artifact" is the output of an expensive preparation step over a
 * source document: the reviewed prepared text plus the reviewed reference IDs
 * it cites. The cache stores the reviewed artifact keyed by (source identity,
 * source content SHA, preparation-policy version), so an unchanged source
 * under an unchanged policy reuses the reviewed artifact without re-preparing.
 *
 * What is stored: reviewed preparedText + refs (reference identifiers).
 * What is NEVER stored: raw source bytes, query answers, or anything derived
 * from a live query. The source is hashed, never persisted.
 *
 * Trust boundary (read this before relying on the cache):
 * - The per-entry digest detects ACCIDENTAL corruption (bit rot, truncated
 *   writes, hand edits). It is NOT authentication: anyone with write access
 *   to the cache directory can rewrite an entry and recompute its digest.
 * - The cache directory is mode 0700 and entry files are mode 0600, so the
 *   effective trust boundary is "whoever can read or write this user's files".
 * - A ReviewReceipt is the caller's audit claim that a review happened
 *   out-of-band. The cache only validates that the receipt is BOUND to the
 *   exact source hash, policy version and artifact digest being stored; it
 *   cannot verify that a human actually reviewed anything.
 *
 * Prototype scope: local file I/O and cache-reuse behavior only. No measured
 * LLM or lead-time savings are claimed; preparation callbacks in tests are
 * synthetic counters.
 */

/** On-disk schema version. Bumped only by a deliberate migration. */
export const PREPARED_CACHE_SCHEMA = 1;

/** Cache key derivation domain separator. */
export const CACHE_KEY_DOMAIN = 'prepared-cache/v1';

/** Directory mode for the cache root: owner only. */
export const CACHE_DIR_MODE = 0o700;

/** Entry file mode: owner read/write only. */
export const CACHE_FILE_MODE = 0o600;

/**
 * The reviewed artifact that may be cached. preparedText is the reviewed
 * preparation output; refs are the reviewed reference identifiers it cites
 * (opaque strings, e.g. "record:<id>"). Raw sources and query answers are
 * never part of this shape.
 */
export interface PreparedArtifact {
  preparedText: string;
  refs: string[];
}

/**
 * The three bindings that form a cache key: which source, exactly which
 * bytes of it, and under which preparation policy it was prepared.
 */
export interface CacheBindings {
  sourceId: string;
  /** Lowercase hex sha256 of the exact source bytes. */
  contentSha: string;
  policyVersion: string;
}

/**
 * Caller-supplied audit claim that the artifact was reviewed. Every field is
 * checked on put against the exact values being stored; a receipt bound to
 * older values (a changed source, a bumped policy, a different artifact) is
 * rejected. Approval is never minted from old IDs.
 */
export interface ReviewReceipt {
  /** Must equal the contentSha of the source bytes being stored. */
  sourceHash: string;
  /** Must equal the policyVersion being stored. */
  policyVersion: string;
  /** Must equal artifactDigestOf(artifact) for the exact artifact stored. */
  artifactDigest: string;
  /** Caller-supplied identity label for the audit trail (not verified). */
  reviewer: string;
  /** ISO-8601 timestamp of the review (validated as a parseable date). */
  reviewedAt: string;
}

export type CacheMissReason =
  | 'missing'
  | 'schema'
  | 'corrupt'
  | 'stale-source'
  | 'stale-policy'
  | 'binding-mismatch';

export type CacheLookup =
  | { hit: true; artifact: PreparedArtifact }
  | { hit: false; reason: CacheMissReason };

const HEX64 = /^[0-9a-f]{64}$/;

/** sha256 of source bytes, hex-encoded. The source is hashed, never stored. */
export function contentShaOf(data: string | Uint8Array): string {
  return createHash('sha256').update(data).digest('hex');
}

/** Canonical bytes of an artifact for digesting. */
function canonicalArtifact(a: PreparedArtifact): string {
  return JSON.stringify({ text: a.preparedText, refs: a.refs });
}

/** Digest of the exact artifact bytes; corruption detection, not authentication. */
export function artifactDigestOf(a: PreparedArtifact): string {
  return createHash('sha256').update(canonicalArtifact(a), 'utf8').digest('hex');
}

/**
 * Derive the cache key from the full binding triple. The NUL separators make
 * the encoding unambiguous, so ("ab","c") can never collide with ("a","bc").
 */
export function deriveCacheKey(b: CacheBindings): string {
  return createHash('sha256')
    .update([CACHE_KEY_DOMAIN, b.sourceId, b.contentSha, b.policyVersion].join('\0'), 'utf8')
    .digest('hex');
}

/**
 * Build a review receipt bound to the exact values it will be stored with.
 * This is a pure constructor: it records the caller's claim, it does not
 * perform or verify a review.
 */
export function makeReviewReceipt(
  bindings: CacheBindings,
  artifact: PreparedArtifact,
  reviewer: string,
  reviewedAt: string = new Date().toISOString()
): ReviewReceipt {
  return {
    sourceHash: bindings.contentSha,
    policyVersion: bindings.policyVersion,
    artifactDigest: artifactDigestOf(artifact),
    reviewer,
    reviewedAt
  };
}

function checkBindings(b: CacheBindings): void {
  if (typeof b.sourceId !== 'string' || b.sourceId.length === 0) {
    throw new Error('sourceId must be a non-empty string');
  }
  if (typeof b.contentSha !== 'string' || !HEX64.test(b.contentSha)) {
    throw new Error('contentSha must be a 64-char lowercase hex sha256');
  }
  if (typeof b.policyVersion !== 'string' || b.policyVersion.length === 0) {
    throw new Error('policyVersion must be a non-empty string');
  }
}

function checkArtifact(a: PreparedArtifact): void {
  if (typeof a.preparedText !== 'string') throw new Error('artifact.preparedText must be a string');
  if (!Array.isArray(a.refs) || !a.refs.every((r) => typeof r === 'string')) {
    throw new Error('artifact.refs must be an array of strings');
  }
}

function checkReceipt(r: ReviewReceipt, b: CacheBindings, a: PreparedArtifact): void {
  if (r.sourceHash !== b.contentSha) {
    throw new Error('review receipt sourceHash does not match the source contentSha being stored');
  }
  if (r.policyVersion !== b.policyVersion) {
    throw new Error('review receipt policyVersion does not match the policyVersion being stored');
  }
  if (r.artifactDigest !== artifactDigestOf(a)) {
    throw new Error('review receipt artifactDigest does not match the artifact being stored');
  }
  if (typeof r.reviewer !== 'string' || r.reviewer.length === 0) {
    throw new Error('review receipt reviewer must be a non-empty string');
  }
  if (typeof r.reviewedAt !== 'string' || Number.isNaN(Date.parse(r.reviewedAt))) {
    throw new Error('review receipt reviewedAt must be a parseable ISO-8601 timestamp');
  }
}

interface StoredEntry {
  schema: number;
  bindings: CacheBindings;
  artifact: PreparedArtifact;
  receipt: ReviewReceipt;
  digest: string;
}

function entryDigest(e: Omit<StoredEntry, 'digest'>): string {
  return createHash('sha256')
    .update(
      JSON.stringify({ schema: e.schema, bindings: e.bindings, artifact: e.artifact, receipt: e.receipt }),
      'utf8'
    )
    .digest('hex');
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null;
}

/**
 * Opt-in on-disk cache. No network, no database, no services: one JSON file
 * per key under `dir`. Writes are atomic (unique temp file + rename), files
 * are 0600, the directory is 0700. Entry filenames are the 64-hex cache key,
 * so different keys can never overwrite each other and no path traversal is
 * possible.
 */
export class PreparedCache {
  private readonly dir: string;

  constructor(dir: string) {
    if (typeof dir !== 'string' || dir.length === 0) throw new Error('cache dir must be a non-empty string');
    this.dir = dir;
  }

  private entryPath(key: string): string {
    if (!HEX64.test(key)) throw new Error('internal error: cache key is not 64-hex');
    return join(this.dir, `${key}.json`);
  }

  private ensureDir(): void {
    mkdirSync(this.dir, { recursive: true, mode: CACHE_DIR_MODE });
    chmodSync(this.dir, CACHE_DIR_MODE);
  }

  /**
   * Look up the artifact for an exact (sourceId, source bytes, policy)
   * triple. Returns { hit: false, reason } for missing files, schema
   * mismatches, digest failures (corruption), or binding drift.
   */
  lookup(sourceId: string, sourceData: string | Uint8Array, policyVersion: string): CacheLookup {
    const contentSha = contentShaOf(sourceData);
    const bindings: CacheBindings = { sourceId, contentSha, policyVersion };
    checkBindings(bindings);
    const key = deriveCacheKey(bindings);
    let raw: string;
    try {
      raw = readFileSync(this.entryPath(key), 'utf8');
    } catch (err) {
      if (isRecord(err) && (err as { code?: string }).code === 'ENOENT') return { hit: false, reason: 'missing' };
      throw err;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return { hit: false, reason: 'corrupt' };
    }
    if (!isRecord(parsed) || parsed['schema'] !== PREPARED_CACHE_SCHEMA) {
      return { hit: false, reason: 'schema' };
    }
    const entry = parsed as StoredEntry;
    if (entry.digest !== entryDigest(entry)) return { hit: false, reason: 'corrupt' };
    const eb = entry.bindings;
    if (!isRecord(eb) || eb['sourceId'] !== sourceId) return { hit: false, reason: 'binding-mismatch' };
    if (eb['contentSha'] !== contentSha) return { hit: false, reason: 'stale-source' };
    if (eb['policyVersion'] !== policyVersion) return { hit: false, reason: 'stale-policy' };
    return { hit: true, artifact: { preparedText: entry.artifact.preparedText, refs: [...entry.artifact.refs] } };
  }

  /**
   * Store a reviewed artifact. Requires an explicit caller ReviewReceipt
   * bound to the exact source hash, policy version and artifact digest;
   * mismatched receipts are rejected, never repaired. Returns the cache key.
   */
  put(
    sourceId: string,
    sourceData: string | Uint8Array,
    policyVersion: string,
    artifact: PreparedArtifact,
    receipt: ReviewReceipt
  ): string {
    const bindings: CacheBindings = { sourceId, contentSha: contentShaOf(sourceData), policyVersion };
    checkBindings(bindings);
    checkArtifact(artifact);
    checkReceipt(receipt, bindings, artifact);
    const key = deriveCacheKey(bindings);
    const entry: Omit<StoredEntry, 'digest'> = {
      schema: PREPARED_CACHE_SCHEMA,
      bindings,
      artifact: { preparedText: artifact.preparedText, refs: [...artifact.refs] },
      receipt: { ...receipt }
    };
    const stored: StoredEntry = { ...entry, digest: entryDigest(entry) };
    this.ensureDir();
    const tmp = join(this.dir, `.tmp-${key}-${randomBytes(8).toString('hex')}`);
    writeFileSync(tmp, JSON.stringify(stored), { mode: CACHE_FILE_MODE });
    chmodSync(tmp, CACHE_FILE_MODE);
    renameSync(tmp, this.entryPath(key));
    return key;
  }

  /** Best-effort read of an entry's file mode, for the 0600 invariant test. */
  entryModeForTest(key: string): number {
    return statSync(this.entryPath(key)).mode & 0o777;
  }

  /** Best-effort read of the cache directory mode, for the 0700 invariant test. */
  dirModeForTest(): number {
    return statSync(this.dir).mode & 0o777;
  }
}
