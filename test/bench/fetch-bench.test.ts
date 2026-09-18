import test from 'node:test';
import assert from 'node:assert/strict';
import { augmentCatalogWithTrainUtterances, mulberry32, seededShuffle, splitHoldout, type Case } from '../../bench/fetch-bench.ts';
import type { FetchCatalogEntry } from '../../src/enhance/fetch.ts';

// ---------------------------------------------------------------- mulberry32 / seededShuffle

test('mulberry32 is deterministic for a fixed seed and produces values in [0,1)', () => {
  const a = mulberry32(42);
  const b = mulberry32(42);
  for (let i = 0; i < 10; i++) {
    const va = a(), vb = b();
    assert.equal(va, vb);
    assert.ok(va >= 0 && va < 1);
  }
});

test('seededShuffle is deterministic for a fixed seed, and differs (almost always) for a different seed', () => {
  const items = Array.from({ length: 20 }, (_, i) => i);
  assert.deepEqual(seededShuffle(items, 1), seededShuffle(items, 1));
  assert.notDeepEqual(seededShuffle(items, 1), seededShuffle(items, 2));
});

test('seededShuffle does not mutate its input and preserves the multiset of elements', () => {
  const items = [1, 2, 3, 4, 5];
  const copy = items.slice();
  const shuffled = seededShuffle(items, 7);
  assert.deepEqual(items, copy);
  assert.deepEqual(shuffled.slice().sort(), items.slice().sort());
});

// ---------------------------------------------------------------- splitHoldout

test('splitHoldout splits by the given fraction, and train+holdout cover every item exactly once', () => {
  const items = Array.from({ length: 20 }, (_, i) => i);
  const { train, holdout } = splitHoldout(items, 0.3, 42);
  assert.equal(holdout.length, 6);
  assert.equal(train.length, 14);
  assert.deepEqual([...train, ...holdout].sort((a, b) => a - b), items);
});

test('splitHoldout is reproducible for a fixed seed and fraction', () => {
  const items = Array.from({ length: 25 }, (_, i) => i);
  const a = splitHoldout(items, 0.3, 42);
  const b = splitHoldout(items, 0.3, 42);
  assert.deepEqual(a, b);
});

test('splitHoldout refuses a fraction outside [0,1]', () => {
  assert.throws(() => splitHoldout([1, 2, 3], -0.1, 1));
  assert.throws(() => splitHoldout([1, 2, 3], 1.1, 1));
});

// ---------------------------------------------------------------- augmentCatalogWithTrainUtterances

test('augmentCatalogWithTrainUtterances adds each train case\'s own request text as an utterance on its expected record', () => {
  const catalog: FetchCatalogEntry[] = [{ id: 'a', text: 'Do A' }, { id: 'b', text: 'Do B' }];
  const train: Case[] = [{ request: 'handle a please', expected_id: 'a' }, { request: 'do a now', expected_id: 'a' }];
  const augmented = augmentCatalogWithTrainUtterances(catalog, train);
  assert.deepEqual(augmented.find(r => r.id === 'a')?.utterances?.sort(), ['do a now', 'handle a please']);
  assert.equal(augmented.find(r => r.id === 'b')?.utterances, undefined);
});

test('augmentCatalogWithTrainUtterances does not mutate the input catalog', () => {
  const catalog: FetchCatalogEntry[] = [{ id: 'a', text: 'Do A' }];
  const before = JSON.parse(JSON.stringify(catalog));
  augmentCatalogWithTrainUtterances(catalog, [{ request: 'handle a', expected_id: 'a' }]);
  assert.deepEqual(catalog, before);
});

test('augmentCatalogWithTrainUtterances dedupes against existing utterances and ignores no-match cases', () => {
  const catalog: FetchCatalogEntry[] = [{ id: 'a', text: 'Do A', utterances: ['handle a'] }];
  const train: Case[] = [{ request: 'Handle A', expected_id: 'a' }, { request: 'anything else', expected_id: null }];
  const augmented = augmentCatalogWithTrainUtterances(catalog, train);
  assert.deepEqual(augmented[0].utterances, ['handle a']); // "Handle A" case/space-normalizes to an existing utterance
});
