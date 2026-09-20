# Prepared artifact cache (`src/enhance/prepared-cache.ts`)

Opt-in, on-disk cache for **reviewed** preparation outputs. A preparation step
turns a source document into prepared text plus the reference IDs it cites;
this cache lets an unchanged source under an unchanged policy reuse the
reviewed artifact without re-preparing.

## What is stored, what is not

Stored per entry: schema version, the key bindings (source ID, content SHA,
policy version), the reviewed `preparedText` and `refs` (reference identifier
strings), the caller's review receipt, and a digest.

Never stored: raw source bytes, query answers, or anything derived from a
live query. The source is hashed (sha256), never persisted.

## Key derivation

`key = sha256("prepared-cache/v1" || 0x00 || sourceId || 0x00 || contentSha || 0x00 || policyVersion)`

The NUL separators make the encoding unambiguous. The filename is the
64-hex key, so different keys can never overwrite each other and no path
traversal is possible. Bindings containing NUL characters are rejected
(`put`, `lookup` and `deriveCacheKey` throw): the unambiguous-separator
claim holds only for NUL-free bindings.

## Read validation

`lookup(sourceId, sourceBytes, policyVersion)` returns a miss for:

- `missing` — no entry file for the derived key
- `schema` — entry written by an unknown schema version
- `corrupt` — JSON unparseable, digest mismatch, or the stored artifact is
  malformed (missing/null/non-string `preparedText`, `refs` not a string
  array). Malformed stored data is always a miss, never a throw.
- `stale-source` — content SHA drifted (a hand-copied entry no longer matches
  the bytes it claims to describe)
- `stale-policy` — policy version drifted
- `binding-mismatch` — source ID drifted, or the stored review receipt no
  longer binds the current source bytes, the current policy version, and the
  exact stored artifact. The receipt is re-validated on every read, even when
  the entry digest is valid: a receipt with a correctly recomputed digest but
  wrong bindings is a miss, not a hit.

## Write validation

`put(...)` requires an explicit caller `ReviewReceipt` bound to the exact
values being stored: `sourceHash` must equal the content SHA of the source
bytes, `policyVersion` must match, `artifactDigest` must equal the digest of
the exact artifact bytes, `reviewer` must be a non-empty label, `reviewedAt`
must be a parseable timestamp. Violations throw; nothing is repaired or
re-minted. Approval is never minted from old IDs.

Writes are atomic per key: unique temp file, mode 0600, then rename.
The cache directory is created and enforced at mode 0700.

## Trust boundary

The digest is **corruption detection, not authentication**: anyone who can
write the cache directory can rewrite an entry and recompute its digest. The
directory being 0700 and files 0600 makes the effective trust boundary
"whoever can read or write this user's files". A `ReviewReceipt` is the
caller's audit claim that a review happened out-of-band; the cache validates
the binding, not the review itself.

## Usage

```ts
import { PreparedCache, contentShaOf, makeReviewReceipt } from './enhance/prepared-cache.ts';

const cache = new PreparedCache('/var/lib/jev/prepared-cache'); // opt-in: only used when constructed
const policy = 'prep-policy/1';

const hit = cache.lookup('source-42', sourceBytes, policy);
if (hit.hit) {
  use(hit.artifact);
} else {
  const artifact = prepare(sourceBytes); // expensive, synthetic in tests
  // ... review happens out-of-band ...
  cache.put('source-42', sourceBytes, policy, artifact,
    makeReviewReceipt(
      { sourceId: 'source-42', contentSha: contentShaOf(sourceBytes), policyVersion: policy },
      artifact, 'reviewer-name'));
  use(artifact);
}
```

No database, no services, no dependencies beyond node builtins, no default
wiring: the cache only acts when the caller constructs it and calls it.

## Tested scope (honesty note)

`test/enhance/prepared-cache.test.ts` covers local file I/O and reuse
behavior with synthetic source bytes: cold/warm callback counts, changed
source, old-receipt rejection, policy invalidation, corruption, ID isolation,
reload, multiple keys, file modes, and the no-raw-sources invariant. The
attempt-2 correction adds: valid-digest-but-stale-receipt and
valid-digest-but-swapped-artifact read misses, malformed stored artifact read
misses (never a throw), embedded-NUL binding rejection, and automatic
cleanup of the synthetic tempdirs after each test. No
measured LLM or lead-time savings are claimed — the preparation callbacks in
tests are counters, not models.
