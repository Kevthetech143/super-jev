import test from 'node:test';
import assert from 'node:assert/strict';
import { CatalogError, formatCatalogValidation, loadCatalog, parseCatalogText, validateCatalog } from '../../src/enhance/catalog.ts';

// ---------------------------------------------------------------- loadCatalog

test('loadCatalog accepts a plain v1 array of {id,text}', () => {
  const records = loadCatalog([{ id: 'a', text: 'Do A' }, { id: 'b', text: 'Do B' }]);
  assert.equal(records.length, 2);
  assert.deepEqual(records[0], { id: 'a', text: 'Do A' });
});

test('loadCatalog accepts a v2 record with utterances, negatives and tags', () => {
  const records = loadCatalog([{ id: 'a', text: 'Do A', utterances: ['do a', 'handle a'], negatives: ['do b'], tags: ['x'] }]);
  assert.deepEqual(records[0].utterances, ['do a', 'handle a']);
  assert.deepEqual(records[0].negatives, ['do b']);
  assert.deepEqual(records[0].tags, ['x']);
});

test('loadCatalog accepts a mix of v1 and v2 records in one array', () => {
  const records = loadCatalog([{ id: 'a', text: 'Do A' }, { id: 'b', text: 'Do B', utterances: ['do b'] }]);
  assert.equal(records[0].utterances, undefined);
  assert.deepEqual(records[1].utterances, ['do b']);
});

test('loadCatalog accepts {catalog:[...]} as well as a bare array', () => {
  const records = loadCatalog({ catalog: [{ id: 'a', text: 'Do A' }] });
  assert.equal(records.length, 1);
});

test('loadCatalog assigns record_<i> when id is absent', () => {
  const records = loadCatalog([{ text: 'Do A' }]);
  assert.equal(records[0].id, 'record_0');
});

test('loadCatalog rejects a non-array, non-{catalog} shape', () => {
  assert.throws(() => loadCatalog({ foo: 'bar' }), CatalogError);
  assert.throws(() => loadCatalog('nope'), CatalogError);
});

test('loadCatalog rejects a record missing text, a non-string id, or a non-string-array utterances/negatives/tags', () => {
  assert.throws(() => loadCatalog([{ id: 'a' }]), /no text/);
  assert.throws(() => loadCatalog([{ id: 1, text: 'x' }]), /non-string id/);
  assert.throws(() => loadCatalog([{ id: 'a', text: 'x', utterances: ['ok', 1] }]), /utterances/);
  assert.throws(() => loadCatalog([{ id: 'a', text: 'x', negatives: 'not an array' }]), /negatives/);
  assert.throws(() => loadCatalog([{ id: 'a', text: 'x', tags: [1, 2] }]), /tags/);
});

test('parseCatalogText rejects invalid JSON', () => {
  assert.throws(() => parseCatalogText('{not json'), /not valid JSON/);
});

// ---------------------------------------------------------------- validateCatalog

test('validateCatalog reports records with fewer than three utterances, including zero', () => {
  const report = validateCatalog(loadCatalog([
    { id: 'a', text: 'x', utterances: ['one', 'two'] },
    { id: 'b', text: 'x', utterances: ['one', 'two', 'three'] },
    { id: 'c', text: 'x' }
  ]));
  assert.deepEqual(report.thinUtterances, [{ id: 'a', count: 2 }, { id: 'c', count: 0 }]);
  assert.equal(report.clean, false);
});

test('validateCatalog reports the same phrasing (case/space-normalized) claimed by two records', () => {
  const report = validateCatalog(loadCatalog([
    { id: 'a', text: 'x', utterances: ['restart it', 'do a', 'handle a'] },
    { id: 'b', text: 'x', utterances: ['Restart  IT', 'do b', 'handle b'] }
  ]));
  assert.equal(report.duplicateUtterances.length, 1);
  assert.equal(report.duplicateUtterances[0].utterance, 'restart it');
  assert.deepEqual(report.duplicateUtterances[0].ids, ['a', 'b']);
});

test('validateCatalog reports a negative that is word-for-word another record\'s utterance', () => {
  const report = validateCatalog(loadCatalog([
    { id: 'a', text: 'x', utterances: ['do a', 'handle a', 'run a'], negatives: ['do b'] },
    { id: 'b', text: 'x', utterances: ['do b', 'handle b', 'run b'] }
  ]));
  assert.equal(report.negativeCollisions.length, 1);
  assert.deepEqual(report.negativeCollisions[0], { id: 'a', negative: 'do b', collidesWithId: 'b' });
});

test('validateCatalog reports clean for a well-formed v2 catalog, and for a plain v1 catalog', () => {
  const v2 = validateCatalog(loadCatalog([
    { id: 'a', text: 'x', utterances: ['one', 'two', 'three'] },
    { id: 'b', text: 'x', utterances: ['four', 'five', 'six'] }
  ]));
  assert.equal(v2.clean, true);
  assert.deepEqual(v2.thinUtterances, []);
  assert.deepEqual(v2.duplicateUtterances, []);
  assert.deepEqual(v2.negativeCollisions, []);

  // A v1 catalog with no utterances at all is still "clean" by this validator's
  // own definition of clean only when it also has >= 3 utterances; a bare v1
  // catalog is expected to show up as thin, which is the whole point of the check.
  const v1 = validateCatalog(loadCatalog([{ id: 'a', text: 'x' }]));
  assert.equal(v1.thinUtterances.length, 1);
});

test('formatCatalogValidation prints one line per finding and a clean message when there are none', () => {
  const clean = formatCatalogValidation(validateCatalog(loadCatalog([{ id: 'a', text: 'x', utterances: ['one', 'two', 'three'] }])));
  assert.match(clean, /clean/);
  const dirty = formatCatalogValidation(validateCatalog(loadCatalog([{ id: 'a', text: 'x' }])));
  assert.match(dirty, /thin utterances/);
});
