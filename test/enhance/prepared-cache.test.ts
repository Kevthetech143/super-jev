import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  PreparedCache,
  contentShaOf,
  artifactDigestOf,
  deriveCacheKey,
  entryDigest,
  makeReviewReceipt,
  PREPARED_CACHE_SCHEMA,
  CACHE_DIR_MODE,
  CACHE_FILE_MODE,
  type PreparedArtifact
} from '../../src/enhance/prepared-cache.ts';

const POLICY_V1 = 'prep-policy/1';
const POLICY_V2 = 'prep-policy/2';

const tempDirs: string[] = [];

function freshCache(): { cache: PreparedCache; dir: string } {
  const dir = mkdtempSync(join(tmpdir(), 'prep-cache-test-'));
  tempDirs.push(dir);
  return { cache: new PreparedCache(dir), dir };
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

function bytes(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

const SOURCE_A = bytes('synthetic source document A — bytes, not prose');
const SOURCE_B = bytes('synthetic source document B — different bytes');

function artifactFor(tag: string): PreparedArtifact {
  return { preparedText: `prepared ${tag}`, refs: [`record:${tag}`] };
}

function receiptFor(cacheSourceId: string, data: Uint8Array, policy: string, artifact: PreparedArtifact) {
  return makeReviewReceipt(
    { sourceId: cacheSourceId, contentSha: contentShaOf(data), policyVersion: policy },
    artifact,
    'test-reviewer',
    '2026-09-19T00:00:00.000Z'
  );
}

test('cold miss, warm hit; the prepare callback runs exactly once', () => {
  const { cache } = freshCache();
  let prepares = 0;
  const prepare = () => {
    prepares += 1;
    return artifactFor('a');
  };

  const first = cache.lookup('src-a', SOURCE_A, POLICY_V1);
  assert.equal(first.hit, false);
  assert.equal(first.reason, 'missing');
  const artifact = prepare();
  cache.put('src-a', SOURCE_A, POLICY_V1, artifact, receiptFor('src-a', SOURCE_A, POLICY_V1, artifact));

  const second = cache.lookup('src-a', SOURCE_A, POLICY_V1);
  assert.equal(second.hit, true);
  assert.deepEqual(second.artifact, artifactFor('a'));
  assert.equal(prepares, 1, 'prepare callback ran once; the second lookup reused the cache');
});

test('changed source bytes miss; a receipt bound to the old bytes is rejected', () => {
  const { cache } = freshCache();
  const artifact = artifactFor('a');
  cache.put('src-a', SOURCE_A, POLICY_V1, artifact, receiptFor('src-a', SOURCE_A, POLICY_V1, artifact));

  const changed = cache.lookup('src-a', SOURCE_B, POLICY_V1);
  assert.equal(changed.hit, false);
  assert.equal(changed.reason, 'missing', 'a changed source hashes to a different key, so the lookup misses');

  const staleReceipt = receiptFor('src-a', SOURCE_A, POLICY_V1, artifactFor('b'));
  assert.throws(
    () => cache.put('src-a', SOURCE_B, POLICY_V1, artifactFor('b'), staleReceipt),
    /sourceHash does not match/,
    'never mint approval from old IDs: the old receipt cannot bless the changed source'
  );
});

test('a bumped policy version invalidates the entry', () => {
  const { cache } = freshCache();
  const artifact = artifactFor('a');
  cache.put('src-a', SOURCE_A, POLICY_V1, artifact, receiptFor('src-a', SOURCE_A, POLICY_V1, artifact));

  const miss = cache.lookup('src-a', SOURCE_A, POLICY_V2);
  assert.equal(miss.hit, false);
  assert.equal(miss.reason, 'missing', 'policy is part of the key, so a bumped policy misses');

  const oldReceipt = receiptFor('src-a', SOURCE_A, POLICY_V1, artifact);
  assert.throws(
    () => cache.put('src-a', SOURCE_A, POLICY_V2, artifact, oldReceipt),
    /policyVersion does not match/,
    'a receipt bound to the old policy cannot store under the new policy'
  );
});

test('a corrupted entry file misses instead of returning bad data', () => {
  const { cache, dir } = freshCache();
  const artifact = artifactFor('a');
  const key = cache.put('src-a', SOURCE_A, POLICY_V1, artifact, receiptFor('src-a', SOURCE_A, POLICY_V1, artifact));
  const path = join(dir, `${key}.json`);
  const parsed = JSON.parse(readFileSync(path, 'utf8')) as { artifact: { preparedText: string } };
  parsed.artifact.preparedText = 'tampered after review';
  writeFileSync(path, JSON.stringify(parsed));

  const lookup = cache.lookup('src-a', SOURCE_A, POLICY_V1);
  assert.equal(lookup.hit, false);
  assert.equal(lookup.reason, 'corrupt', 'digest mismatch on a valid-JSON tampered entry is a miss');
});

test('same bytes under different source IDs are isolated', () => {
  const { cache } = freshCache();
  const artA = artifactFor('a');
  const artB = artifactFor('b');
  const keyA = cache.put('src-a', SOURCE_A, POLICY_V1, artA, receiptFor('src-a', SOURCE_A, POLICY_V1, artA));
  const keyB = cache.put('src-b', SOURCE_A, POLICY_V1, artB, receiptFor('src-b', SOURCE_A, POLICY_V1, artB));
  assert.notEqual(keyA, keyB, 'the key binds source identity, not just bytes');

  const hitA = cache.lookup('src-a', SOURCE_A, POLICY_V1);
  const hitB = cache.lookup('src-b', SOURCE_A, POLICY_V1);
  assert.equal(hitA.hit, true);
  assert.equal(hitB.hit, true);
  assert.deepEqual(hitA.artifact, artA);
  assert.deepEqual(hitB.artifact, artB);
});

test('entries survive a fresh PreparedCache instance over the same directory', () => {
  const { dir } = freshCache();
  const writer = new PreparedCache(dir);
  const artifact = artifactFor('a');
  writer.put('src-a', SOURCE_A, POLICY_V1, artifact, receiptFor('src-a', SOURCE_A, POLICY_V1, artifact));

  const reader = new PreparedCache(dir);
  const hit = reader.lookup('src-a', SOURCE_A, POLICY_V1);
  assert.equal(hit.hit, true);
  assert.deepEqual(hit.artifact, artifact);
});

test('multiple keys coexist and do not overwrite each other', () => {
  const { cache } = freshCache();
  const ids = ['one', 'two', 'three', 'four', 'five'];
  for (const id of ids) {
    const artifact = artifactFor(id);
    cache.put(id, bytes(`source ${id}`), POLICY_V1, artifact, receiptFor(id, bytes(`source ${id}`), POLICY_V1, artifact));
  }
  for (const id of ids) {
    const hit = cache.lookup(id, bytes(`source ${id}`), POLICY_V1);
    assert.equal(hit.hit, true, `key ${id} hit`);
    assert.deepEqual(hit.artifact, artifactFor(id));
  }
});

test('cache directory is 0700 and entry files are 0600', () => {
  const { cache, dir } = freshCache();
  const artifact = artifactFor('a');
  const key = cache.put('src-a', SOURCE_A, POLICY_V1, artifact, receiptFor('src-a', SOURCE_A, POLICY_V1, artifact));
  assert.equal(cache.dirModeForTest(), CACHE_DIR_MODE, `dir ${dir} mode`);
  assert.equal(cache.entryModeForTest(key), CACHE_FILE_MODE, `entry ${key} mode`);
});

test('a receipt bound to a different artifact digest is rejected', () => {
  const { cache } = freshCache();
  const stored = artifactFor('stored');
  const other = artifactFor('other');
  const wrongDigestReceipt = receiptFor('src-a', SOURCE_A, POLICY_V1, other);
  assert.throws(
    () => cache.put('src-a', SOURCE_A, POLICY_V1, stored, wrongDigestReceipt),
    /artifactDigest does not match/,
    'the receipt must bind the exact artifact bytes being stored'
  );
});

test('raw source bytes are never persisted; only hash-bound metadata', () => {
  const { cache, dir } = freshCache();
  const secret = bytes('the raw source text must never appear on disk');
  const artifact = artifactFor('a');
  cache.put('src-secret', secret, POLICY_V1, artifact, receiptFor('src-secret', secret, POLICY_V1, artifact));

  const raw = readFileSync(join(dir, `${deriveCacheKey({ sourceId: 'src-secret', contentSha: contentShaOf(secret), policyVersion: POLICY_V1 })}.json`), 'utf8');
  assert.ok(!raw.includes('the raw source text must never appear on disk'), 'raw source absent from the entry file');
  assert.ok(raw.includes(contentShaOf(secret)), 'the content hash binding is present');
  assert.equal(PREPARED_CACHE_SCHEMA, 1);
});

/**
 * Rewrite a stored entry file: apply `mutate` to the parsed entry, then
 * recompute the integrity digest so the fixture has a VALID digest but
 * deliberately stale/malformed content.
 */
function recomputeDigest(dir: string, key: string, mutate: (entry: any) => void): void {
  const path = join(dir, `${key}.json`);
  const entry = JSON.parse(readFileSync(path, 'utf8'));
  mutate(entry);
  const { schema, bindings, artifact, receipt } = entry;
  entry.digest = entryDigest({ schema, bindings, artifact, receipt });
  writeFileSync(path, JSON.stringify(entry));
}

test('a stored receipt with a valid entry digest but stale bindings misses on read', () => {
  const { cache, dir } = freshCache();
  const artifact = artifactFor('a');
  const key = cache.put('src-a', SOURCE_A, POLICY_V1, artifact, receiptFor('src-a', SOURCE_A, POLICY_V1, artifact));

  // Lead repro: receipt sourceHash swapped, entry digest correctly recomputed.
  recomputeDigest(dir, key, (entry) => {
    entry.receipt.sourceHash = contentShaOf(SOURCE_B);
  });

  const lookup = cache.lookup('src-a', SOURCE_A, POLICY_V1);
  assert.equal(lookup.hit, false);
  assert.equal(lookup.reason, 'binding-mismatch', 'a stale receipt is a miss even with a valid entry digest');
});

test('a receipt bound to a different artifact misses on read even with a valid entry digest', () => {
  const { cache, dir } = freshCache();
  const stored = artifactFor('stored');
  const key = cache.put('src-a', SOURCE_A, POLICY_V1, stored, receiptFor('src-a', SOURCE_A, POLICY_V1, stored));

  // Swap the stored artifact; the receipt still binds the original digest.
  recomputeDigest(dir, key, (entry) => {
    entry.artifact = { preparedText: 'prepared other', refs: ['record:other'] };
  });

  const lookup = cache.lookup('src-a', SOURCE_A, POLICY_V1);
  assert.equal(lookup.hit, false);
  assert.equal(lookup.reason, 'binding-mismatch');
});

test('a malformed stored artifact misses instead of throwing', () => {
  const { cache, dir } = freshCache();
  const artifact = artifactFor('a');
  const key = cache.put('src-a', SOURCE_A, POLICY_V1, artifact, receiptFor('src-a', SOURCE_A, POLICY_V1, artifact));

  recomputeDigest(dir, key, (entry) => {
    entry.artifact = null;
  });

  const lookup = cache.lookup('src-a', SOURCE_A, POLICY_V1);
  assert.equal(lookup.hit, false);
  assert.equal(lookup.reason, 'corrupt', 'a null stored artifact is a miss, not a throw');
});

test('a stored artifact with non-string refs misses instead of throwing', () => {
  const { cache, dir } = freshCache();
  const artifact = artifactFor('a');
  const key = cache.put('src-a', SOURCE_A, POLICY_V1, artifact, receiptFor('src-a', SOURCE_A, POLICY_V1, artifact));

  recomputeDigest(dir, key, (entry) => {
    entry.artifact.refs = 42;
  });

  const lookup = cache.lookup('src-a', SOURCE_A, POLICY_V1);
  assert.equal(lookup.hit, false);
  assert.equal(lookup.reason, 'corrupt', 'non-array refs are a miss, not a throw');
});

test('embedded NUL characters in bindings are rejected', () => {
  const { cache } = freshCache();
  const artifact = artifactFor('a');
  const receipt = receiptFor('src-a', SOURCE_A, POLICY_V1, artifact);
  assert.throws(
    () => cache.put('src\0a', SOURCE_A, POLICY_V1, artifact, receipt),
    /NUL/,
    'put rejects a sourceId containing NUL'
  );
  assert.throws(
    () => cache.lookup('src\0a', SOURCE_A, POLICY_V1),
    /NUL/,
    'lookup rejects a sourceId containing NUL'
  );
  assert.throws(
    () =>
      deriveCacheKey({
        sourceId: 'src-a',
        contentSha: contentShaOf(SOURCE_A),
        policyVersion: 'policy\0v1'
      }),
    /NUL-free/,
    'deriveCacheKey rejects a policyVersion containing NUL'
  );
});
