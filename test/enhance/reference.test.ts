import test from 'node:test';
import assert from 'node:assert/strict';
import { arrayPairs, assignIds, buildArrayRequest, buildKeyedRequest, keyedPairs, mapAnswers, mappingIsExact, toRefs } from '../../src/enhance/reference.ts';
import { choiceAnswer } from '../../src/enhance/stub.ts';
import type { Question } from '../../src/types.ts';

const question = (): Question => ({ type: 'choice', instructions: 'x', criteria: { a: 'A', b: 'B' } });

test('records without ids get stable records/<n> ids', () => {
  const records = assignIds([{ text: 'one' }, { text: 'two' }, { id: 'custom', text: 'three' }]);
  assert.deepEqual(records.map(r => r.id), ['records/0', 'records/1', 'custom']);
});

test('duplicate and empty records are refused', () => {
  assert.throws(() => assignIds([{ id: 'a', text: 'x' }, { id: 'a', text: 'y' }]), /Duplicate record id: a/);
  assert.throws(() => assignIds([{ id: 'a', text: '  ' }]), /has no text/);
});

test('record ids become safe, unique question keys and the pairing is explicit', () => {
  const refs = toRefs(assignIds([{ text: 'one' }, { id: 'a/b', text: 'two' }, { id: 'a_b', text: 'three' }, { id: 'constructor', text: 'four' }]));
  assert.deepEqual(refs.map(r => r.id), ['records/0', 'a/b', 'a_b', 'constructor']);
  for (const ref of refs) assert.match(ref.key, /^[A-Za-z][A-Za-z0-9_]*$/);
  assert.equal(new Set(refs.map(r => r.key)).size, 4);
  assert.notEqual(refs[1].key, refs[2].key, 'a/b and a_b must not collapse onto one key');
});

test('the keyed request names each record by its own path', () => {
  const refs = toRefs(assignIds([{ id: 'D01', text: 'first' }, { id: 'D02', text: 'second' }]));
  const request = buildKeyedRequest(refs, ref => ({ type: 'choice', instructions: `Classify \`records.${ref.key}.text\``, criteria: { a: 'A', b: 'B' } }));
  const state = request.state as { records: Record<string, { id: string; text: string }> };
  assert.deepEqual(Object.keys(state.records), ['D01', 'D02']);
  assert.equal(state.records.D02.text, 'second');
  assert.deepEqual(Object.keys(request.questions), ['D01', 'D02']);
  assert.match((request.questions.D01 as { instructions: string }).instructions, /records\.D01\.text/);
});

test('answers map back by key, never by position', () => {
  const refs = toRefs(assignIds([{ id: 'D01', text: 'a' }, { id: 'D02', text: 'b' }]));
  const evaluation = { model: 'stub', answers: { D02: choiceAnswer('a', 0.9, ['a', 'b']), D01: choiceAnswer('b', 0.8, ['a', 'b']) } };
  const report = mapAnswers(keyedPairs(refs), evaluation);
  assert.ok(mappingIsExact(report));
  assert.equal(report.answered.find(a => a.id === 'D01')!.answer.type, 'choice');
  assert.equal((report.answered.find(a => a.id === 'D01')!.answer as { choice: string }).choice, 'b');
  assert.equal((report.answered.find(a => a.id === 'D02')!.answer as { choice: string }).choice, 'a');
});

test('a missing answer is reported missing and an unasked answer is rejected', () => {
  const refs = toRefs(assignIds([{ id: 'D01', text: 'a' }, { id: 'D02', text: 'b' }]));
  const evaluation = { model: 'stub', answers: { D01: choiceAnswer('a', 0.9, ['a', 'b']), D99: choiceAnswer('b', 0.9, ['a', 'b']) } };
  const report = mapAnswers(keyedPairs(refs), evaluation);
  assert.equal(mappingIsExact(report), false);
  assert.deepEqual(report.missing.map(m => m.id), ['D02']);
  assert.deepEqual(report.rejected, ['D99']);
  assert.deepEqual(report.answered.map(a => a.id), ['D01']);
});

test('sending the same key twice is a programming error, not a silent overwrite', () => {
  const refs = toRefs(assignIds([{ id: 'D01', text: 'a' }]));
  const pairs = [...keyedPairs(refs), { key: 'D01', id: 'other' }];
  assert.throws(() => mapAnswers(pairs, { model: 'stub', answers: {} }), /Asked key D01 was sent twice/);
});

test('the array framing pairs positional keys to the same records', () => {
  const refs = toRefs(assignIds([{ id: 'D01', text: 'a' }, { id: 'D02', text: 'b' }]));
  assert.deepEqual(arrayPairs(refs), [{ key: 'record_0', id: 'D01' }, { key: 'record_1', id: 'D02' }]);
  const evaluation = { model: 'stub', answers: { record_1: choiceAnswer('a', 0.9, ['a', 'b']), record_0: choiceAnswer('b', 0.8, ['a', 'b']) } };
  const report = mapAnswers(arrayPairs(refs), evaluation);
  assert.ok(mappingIsExact(report));
  assert.equal((report.answered.find(a => a.id === 'D02')!.answer as { choice: string }).choice, 'a');
});

test('the array framing is available for comparison and uses positional keys', () => {
  const refs = toRefs(assignIds([{ id: 'D01', text: 'a' }, { id: 'D02', text: 'b' }]));
  const request = buildArrayRequest(refs, question);
  assert.deepEqual(Object.keys(request.questions), ['record_0', 'record_1']);
  assert.ok(Array.isArray((request.state as { records: unknown[] }).records));
});
