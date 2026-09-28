import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JUDGE_PROFILE, loadJudgeProfile } from '../src/judge-profile.ts';
import { MAX_QUESTIONS_PER_CALL } from '../src/enhance/sweep.ts';
import { BATCH_TOKEN_BUDGET } from '../src/enhance/coalesce.ts';

test('default judge profile derives every old number exactly', () => {
  assert.equal(JUDGE_PROFILE.name, 'typesafe-jev');
  assert.equal(MAX_QUESTIONS_PER_CALL, 255);
  assert.equal(BATCH_TOKEN_BUDGET, 30_000);
});

test('the Jev profile states the same limits as the Python loader', () => {
  const p = loadJudgeProfile('typesafe-jev');
  assert.equal(p.windowTokens, 32_768);
  assert.equal(p.inputCapTokens, 32_000);
  assert.equal(p.callTokens, 30_000);
  assert.equal(p.maxQuestionsPerCall, 255);
  assert.deepEqual([...p.questionKinds], ['noul', 'choice', 'score']);
  assert.equal(p.inputUsdPerMtok, 0.042);
  assert.equal(p.outputUsdPerMtok, 0);
});
