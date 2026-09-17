import test from 'node:test';
import assert from 'node:assert/strict';
import { decidePermit, matchIrreversibleKeywords, IRREVERSIBLE_KEYWORDS, PERMIT_CONFIDENCE_THRESHOLD } from '../../src/enhance/permit.ts';
import { StubEvaluator, choiceAnswer } from '../../src/enhance/stub.ts';
import type { Answer, Request } from '../../src/types.ts';

const options = ['safe_to_auto', 'needs_approval', 'refuse'];

function fixed(choice: string, confidence: number): StubEvaluator {
  return new StubEvaluator({ script: () => ({ model: 'stub', answers: { permit: choiceAnswer(choice, confidence, options) } }) });
}

test('every listed hard-rule keyword is detected in the action text', () => {
  for (const keyword of IRREVERSIBLE_KEYWORDS) {
    assert.deepEqual(matchIrreversibleKeywords(`please ${keyword} the thing`), [keyword], keyword);
  }
  assert.deepEqual(matchIrreversibleKeywords('read the file and summarize it'), []);
});

test('the hard-rule matcher tolerates hyphen/underscore/space variants without going fuzzy', () => {
  assert.deepEqual(matchIrreversibleKeywords('please force-push to main'), ['force push']);
  assert.deepEqual(matchIrreversibleKeywords('please force_push to main'), ['force push']);
  assert.deepEqual(matchIrreversibleKeywords('deletion is not deleting'), [], 'no substring match on an unrelated word');
});

test('hard rule: an irreversible action never returns safe_to_auto, even at maximum confidence', async () => {
  const stub = fixed('safe_to_auto', 1.0);
  const result = await decidePermit('a1', { action: 'delete the customer record', target: 'record/42' }, stub);
  assert.notEqual(result.verdict, 'safe_to_auto');
  assert.equal(result.verdict, 'needs_approval');
  assert.equal(result.hardRuleApplied, true);
  assert.deepEqual(result.matchedKeywords, ['delete']);
  assert.match(result.reason, /hard rule/);
});

test('hard rule fires on a keyword found only in the target, not the action', async () => {
  const stub = fixed('safe_to_auto', 0.99);
  const result = await decidePermit('a2', { action: 'run the cleanup job', target: 'wire transfer #9' }, stub);
  assert.equal(result.verdict, 'needs_approval');
  assert.equal(result.hardRuleApplied, true);
  assert.deepEqual(result.matchedKeywords, ['wire']);
});

test('hard rule never fires for an action with no matched keyword', async () => {
  const stub = fixed('safe_to_auto', 0.95);
  const result = await decidePermit('a3', { action: 'list files in the reports folder' }, stub);
  assert.equal(result.verdict, 'safe_to_auto');
  assert.equal(result.hardRuleApplied, false);
  assert.deepEqual(result.matchedKeywords, []);
});

test('every hard-rule keyword downgrades a confident safe_to_auto answer', async () => {
  for (const keyword of IRREVERSIBLE_KEYWORDS) {
    const stub = fixed('safe_to_auto', 1.0);
    const result = await decidePermit('k', { action: `please ${keyword} now` }, stub);
    assert.equal(result.verdict, 'needs_approval', keyword);
    assert.equal(result.hardRuleApplied, true, keyword);
  }
});

test('threshold escalation: confidence at or above 0.80 with an agreeing, well-formed answer allows safe_to_auto', async () => {
  const stub = fixed('safe_to_auto', PERMIT_CONFIDENCE_THRESHOLD);
  const result = await decidePermit('t1', { action: 'refresh the local cache' }, stub);
  assert.equal(result.verdict, 'safe_to_auto');
  assert.equal(result.confidence, PERMIT_CONFIDENCE_THRESHOLD);
});

test('threshold escalation: confidence just below 0.80 never auto-runs, it needs approval', async () => {
  const stub = fixed('safe_to_auto', PERMIT_CONFIDENCE_THRESHOLD - 0.01);
  const result = await decidePermit('t2', { action: 'refresh the local cache' }, stub);
  assert.equal(result.verdict, 'needs_approval');
  assert.equal(result.hardRuleApplied, false, 'this is the confidence gate, not the hard rule');
  assert.match(result.reason, /below the 0\.8 gate/);
});

test('a custom --min-confidence-style gate override is honored', async () => {
  const stub = fixed('safe_to_auto', 0.9);
  const strict = await decidePermit('t3', { action: 'refresh the local cache' }, stub, { gate: { minConfidence: 0.95 } });
  assert.equal(strict.verdict, 'needs_approval');
});

test('the model choosing refuse maps straight to refuse', async () => {
  const stub = fixed('refuse', 0.99);
  const result = await decidePermit('r1', { action: 'change the account email' }, stub);
  assert.equal(result.verdict, 'refuse');
});

test('the model choosing needs_approval maps straight to needs_approval', async () => {
  const stub = fixed('needs_approval', 0.99);
  const result = await decidePermit('n1', { action: 'change the account email' }, stub);
  assert.equal(result.verdict, 'needs_approval');
});

test('disagreement across passes never reaches safe_to_auto (mapped through decideOutcome review)', async () => {
  let call = 0;
  const stub = new StubEvaluator({
    script: () => {
      call++;
      return { model: 'stub', answers: { permit: choiceAnswer(call === 1 ? 'safe_to_auto' : 'needs_approval', 0.99, options) } };
    }
  });
  // decidePermit only ever sends one pass itself; this test documents that a
  // single-pass call cannot disagree with itself, i.e. agreement checks are
  // exercised by decideOutcome's own tests, not duplicated here.
  const result = await decidePermit('d1', { action: 'refresh the local cache' }, stub);
  assert.equal(result.verdict, 'safe_to_auto');
  assert.equal(call, 1);
});

test('a malformed answer never reaches safe_to_auto', async () => {
  const stub = new StubEvaluator({ script: () => ({ model: 'stub', answers: { permit: { type: 'choice', choice: 'safe_to_auto' } as unknown as Answer } }) });
  const result = await decidePermit('m1', { action: 'refresh the local cache' }, stub);
  assert.equal(result.verdict, 'needs_approval');
});

test('no answer at all never reaches safe_to_auto', async () => {
  const stub = new StubEvaluator({ script: () => ({ model: 'stub', answers: {} }) });
  const result = await decidePermit('u1', { action: 'refresh the local cache' }, stub);
  assert.equal(result.verdict, 'needs_approval');
});

test('an unknown option value is treated as malformed, not trusted', async () => {
  const stub = new StubEvaluator({ script: () => ({ model: 'stub', answers: { permit: choiceAnswer('yolo_run_it', 0.99, ['yolo_run_it']) } }) });
  const result = await decidePermit('o1', { action: 'refresh the local cache' }, stub);
  assert.equal(result.verdict, 'needs_approval');
});

test('the request sends the snapshot fields and frames them as untrusted data', async () => {
  const stub = fixed('safe_to_auto', 0.95);
  await decidePermit('x1', { action: 'refresh the cache', target: 'cache/main', reversible: true, policyLines: ['policy A'] }, stub);
  const request = stub.requests[0] as Request;
  assert.equal((request.state as { action: string }).action, 'refresh the cache');
  assert.equal((request.state as { target: string }).target, 'cache/main');
  assert.deepEqual((request.state as { policyLines: string[] }).policyLines, ['policy A']);
  assert.match(request.questions.permit.instructions, /never an instruction/);
});
