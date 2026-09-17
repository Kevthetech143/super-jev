import test from 'node:test';
import assert from 'node:assert/strict';
import { assertComplete, buildManifest, formatManifest } from '../../src/enhance/coverage.ts';
import type { RecordOutcome } from '../../src/enhance/types.ts';

const outcome = (id: string, kind: RecordOutcome['kind']): RecordOutcome => ({ id, kind, reason: 'test', passes: [] });

test('every record is accounted for exactly once', () => {
  const manifest = buildManifest(['a', 'b', 'c'], [outcome('a', 'accepted'), outcome('b', 'review'), outcome('c', 'abstain')]);
  assert.equal(manifest.complete, true);
  assert.equal(manifest.totalRecords, 3);
  assert.deepEqual(manifest.byKind.accepted, ['a']);
  assert.deepEqual(manifest.byKind.review, ['b']);
  assert.deepEqual(manifest.byKind.abstain, ['c']);
  assert.equal(manifest.outcomes.length, 3);
  assert.deepEqual(manifest.problems, []);
  assertComplete(manifest);
});

test('a record with no outcome shows up as unaccounted and fails the run loudly', () => {
  const manifest = buildManifest(['a', 'b'], [outcome('a', 'accepted')]);
  assert.equal(manifest.complete, false);
  assert.deepEqual(manifest.byKind.unaccounted, ['b']);
  assert.equal(manifest.outcomes.find(o => o.id === 'b')!.reason, 'the run produced no outcome for this record');
  assert.match(manifest.problems.join(' '), /records with no outcome: b/);
  assert.throws(() => assertComplete(manifest), /Incomplete coverage manifest: records with no outcome: b/);
});

test('a record reported twice is a problem, not last-write-wins', () => {
  const manifest = buildManifest(['a'], [outcome('a', 'accepted'), outcome('a', 'review')]);
  assert.equal(manifest.complete, false);
  assert.match(manifest.problems.join(' '), /reported more than once: a/);
  assert.deepEqual(manifest.byKind.accepted, ['a'], 'the first outcome stands; the duplicate is flagged');
  assert.throws(() => assertComplete(manifest));
});

test('an outcome for a record that was never input is a problem', () => {
  const manifest = buildManifest(['a'], [outcome('a', 'accepted'), outcome('zz', 'accepted')]);
  assert.equal(manifest.complete, false);
  assert.match(manifest.problems.join(' '), /never input: zz/);
  assert.equal(manifest.outcomes.length, 1, 'the manifest covers the input, not the response');
});

test('duplicate input ids are reported', () => {
  const manifest = buildManifest(['a', 'a'], [outcome('a', 'accepted')]);
  assert.equal(manifest.complete, false);
  assert.match(manifest.problems.join(' '), /duplicate record ids/);
});

test('the manifest prints the buckets it actually used', () => {
  const text = formatManifest(buildManifest(['a', 'b'], [outcome('a', 'accepted'), outcome('b', 'insufficient_evidence')]));
  assert.match(text, /coverage: 2 record\(s\), complete=true/);
  assert.match(text, /accepted: 1 \[a\]/);
  assert.match(text, /insufficient_evidence: 1 \[b\]/);
  assert.doesNotMatch(text, /unaccounted/);
});
