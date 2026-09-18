import test from 'node:test';
import assert from 'node:assert/strict';
import {
  applyPreRules, buildTriggerIndex, factsForRequest, formatPreRuleExplain, negativeHits, normalizePhrase
} from '../../src/enhance/fetch-prerules.ts';
import { choiceAnswer } from '../../src/enhance/stub.ts';
import type { FetchCatalogEntry } from '../../src/enhance/fetch.ts';
import type { Answer, Evaluation, Evaluator, Question, Request } from '../../src/types.ts';

/** Same table-transport shape used by fetch.test.ts: keyed on catalog id, recovered from the request's own named state. */
function tableTransport(table: Record<string, string>): Evaluator {
  return {
    evaluate: async (request: Request): Promise<Evaluation> => {
      const state = (request.state as { records: Record<string, { id: string }> }).records;
      const answers: Record<string, Answer> = {};
      for (const [wireKey, question] of Object.entries(request.questions) as [string, Question][]) {
        const recordKey = Object.keys(state).find(k => wireKey === `q_relevance_${k}` || wireKey === `q0_r${Object.keys(state).indexOf(k)}`);
        assert.ok(recordKey, `wire key ${wireKey} does not name a record in the state`);
        const id = state[recordKey].id;
        if (question.type !== 'choice') throw new Error('expected a choice question');
        const level = table[id] ?? 'none';
        answers[wireKey] = choiceAnswer(level, 0.9, Object.keys(question.criteria));
      }
      return { model: 'table-offline', answers };
    }
  };
}

const fixtureCatalog: FetchCatalogEntry[] = [
  {
    id: 'pay-coned',
    text: 'Pay the Con Edison electric bill via the guest checkout flow.',
    utterances: ['pay the electric bill', 'pay my coned bill', 'electric'],
    negatives: ['gas bill']
  },
  {
    id: 'pay-water',
    text: 'Pay the water utility bill.',
    utterances: ['pay the water bill', 'pay my water bill'],
    negatives: []
  },
  {
    id: 'ambiguous-a',
    text: 'Record A, shares an utterance with record B on purpose.',
    utterances: ['do the thing now'],
    negatives: []
  },
  {
    id: 'ambiguous-b',
    text: 'Record B, shares an utterance with record A on purpose.',
    utterances: ['do the thing now'],
    negatives: []
  }
];

// --------------------------------------------------------------- trigger index construction

test('buildTriggerIndex excludes single-word utterances from every trigger set', () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  const conedTriggers = idx.triggersByRecord.get('pay-coned')!;
  assert.ok(!conedTriggers.includes('electric'), 'a single-word utterance must never be a trigger');
  assert.ok(conedTriggers.includes('pay the electric bill'));
  assert.deepEqual(idx.droppedSingleWord.get('pay-coned'), ['electric']);
});

test('buildTriggerIndex excludes an utterance shared by more than one record, from every owner', () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  assert.deepEqual(idx.triggersByRecord.get('ambiguous-a'), []);
  assert.deepEqual(idx.triggersByRecord.get('ambiguous-b'), []);
  assert.equal(idx.droppedShared.length, 1);
  assert.deepEqual(idx.droppedShared[0].ids, ['ambiguous-a', 'ambiguous-b']);
});

test('normalizePhrase lowercases, strips punctuation and collapses whitespace', () => {
  assert.equal(normalizePhrase('Pay the ELECTRIC, bill!!'), 'pay the electric bill');
  assert.equal(normalizePhrase(''), '');
});

test('factsForRequest finds a record whose trigger phrase is a substring of the request', () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  const facts = factsForRequest('can you please pay the electric bill today', idx);
  assert.deepEqual(facts.matchedRecordIds, ['pay-coned']);
  assert.equal(facts.matchedPhraseByRecord.get('pay-coned'), 'pay the electric bill');
  assert.equal(facts.anyTriggerHit, true);
});

test('factsForRequest reports no hit when nothing in the catalog is mentioned', () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  const facts = factsForRequest('order a pizza', idx);
  assert.deepEqual(facts.matchedRecordIds, []);
  assert.equal(facts.anyTriggerHit, false);
});

test('negativeHits reports the normalized negative phrases present in the request', () => {
  const record = fixtureCatalog[0];
  assert.deepEqual(negativeHits(record, normalizePhrase('pay the gas bill please')), ['gas bill']);
  assert.deepEqual(negativeHits(record, normalizePhrase('pay the electric bill')), []);
});

// --------------------------------------------------------------- R1: unique-trigger bypass

test('R1 fires: a unique multi-word trigger with no negative hit serves directly, zero judge calls', async () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  let called = false;
  const transport: Evaluator = { evaluate: async () => { called = true; throw new Error('the judge must not be called when R1 fires'); } };
  const { decision } = await applyPreRules(fixtureCatalog, 'pay the electric bill', idx, { transport });
  assert.equal(decision.kind, 'serve');
  if (decision.kind === 'serve') {
    assert.equal(decision.source, 'trigger');
    assert.equal(decision.id, 'pay-coned');
    assert.match(decision.reason, /R1/);
  }
  assert.equal(called, false, 'applyPreRules must not invoke the transport when R1 settles the request');
});

test('R1 does not fire on a single-word utterance alone', async () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  const transport = tableTransport({ 'pay-coned': 'medium' });
  const { decision } = await applyPreRules(fixtureCatalog, 'electric', idx, { transport });
  assert.notEqual(decision.kind, 'serve');
});

test('R1 does not fire on a trigger shared by more than one record (ambiguity guard)', async () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  const transport = tableTransport({ 'ambiguous-a': 'high' });
  const { decision, facts } = await applyPreRules(fixtureCatalog, 'do the thing now', idx, { transport });
  assert.equal(facts.matchedRecordIds.length, 0);
  assert.equal(decision.kind, 'fallThrough');
});

test('R1 does not fire when the request also matches one of the record\'s own negatives', async () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  const transport = tableTransport({ 'pay-water': 'high' });
  const { decision } = await applyPreRules(fixtureCatalog, 'pay the electric gas bill', idx, { transport });
  assert.notEqual(decision.kind, 'serve');
});

// --------------------------------------------------------------- R2: negative-demote

test('R2 fires: the judge\'s own top-1 has a negative-phrase hit in the request, and is demoted', async () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  // The request itself carries no catalog trigger word combination that
  // resolves R1 (only "gas bill", which is pay-coned's negative, not a
  // trigger of any record), so this exercises the judge + R2 path.
  const transport = tableTransport({ 'pay-coned': 'high' });
  const { decision } = await applyPreRules(fixtureCatalog, 'can you pay the gas bill', idx, { transport });
  assert.equal(decision.kind, 'demote');
  if (decision.kind === 'demote') {
    assert.equal(decision.id, 'pay-coned');
    assert.match(decision.reason, /R2/);
  }
});

test('R2 does not fire when the judge\'s top-1 has no negative hit', async () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  const transport = tableTransport({ 'pay-water': 'high' });
  const { decision } = await applyPreRules(fixtureCatalog, 'settle my water utility charge', idx, { transport }, 0.5);
  assert.notEqual(decision.kind, 'demote');
});

// --------------------------------------------------------------- R3: no-trigger noMatch

test('R3 fires: no record\'s trigger appears anywhere, and the judge top-1 confidence is under the floor', async () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  const transport: Evaluator = {
    evaluate: async (request: Request): Promise<Evaluation> => {
      const state = (request.state as { records: Record<string, { id: string }> }).records;
      const answers: Record<string, Answer> = {};
      for (const [wireKey, question] of Object.entries(request.questions) as [string, Question][]) {
        if (question.type !== 'choice') continue;
        const recordKey = Object.keys(state).find(k => wireKey === `q_relevance_${k}` || wireKey === `q0_r${Object.keys(state).indexOf(k)}`);
        const id = state[recordKey!].id;
        answers[wireKey] = id === 'pay-coned'
          ? choiceAnswer('low', 0.3, Object.keys(question.criteria))
          : choiceAnswer('none', 0.5, Object.keys(question.criteria));
      }
      return { model: 'table-offline', answers };
    }
  };
  const { decision } = await applyPreRules(fixtureCatalog, 'order a pizza for dinner', idx, { transport }, 0.6);
  assert.equal(decision.kind, 'noMatch');
  if (decision.kind === 'noMatch') assert.match(decision.reason, /R3/);
});

test('R3 does not fire when a trigger is present, even if the judge confidence is low', async () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  const transport = tableTransport({ 'pay-coned': 'low' });
  const { decision } = await applyPreRules(fixtureCatalog, 'pay the electric bill maybe', idx, { transport }, 0.9);
  // R1 should have already served this before the judge is even asked.
  assert.equal(decision.kind, 'serve');
});

test('R3 does not fire when the judge top-1 confidence clears the floor', async () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  const transport = tableTransport({ 'pay-coned': 'high' });
  const { decision } = await applyPreRules(fixtureCatalog, 'order a pizza for dinner', idx, { transport }, 0.5);
  assert.equal(decision.kind, 'fallThrough');
});

// --------------------------------------------------------------- explain

test('formatPreRuleExplain prints the matched trigger, the ambiguity count and the verdict', () => {
  const idx = buildTriggerIndex(fixtureCatalog);
  const facts = factsForRequest('pay the electric bill', idx);
  const text = formatPreRuleExplain({
    facts, droppedSingleWord: idx.droppedSingleWord, droppedShared: idx.droppedShared,
    decision: { kind: 'serve', source: 'trigger', id: 'pay-coned', reason: 'R1: unique multi-word trigger "pay the electric bill" matched record pay-coned; no negatives hit; no judge call made' }
  });
  assert.match(text, /trigger hit: record pay-coned/);
  assert.match(text, /ambiguity guard: 1 utterance/);
  assert.match(text, /R1/);
});
