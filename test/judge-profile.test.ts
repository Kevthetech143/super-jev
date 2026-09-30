import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JUDGE_PROFILE, loadJudgeProfile, judgeTokens } from '../src/judge-profile.ts';
import { MAX_QUESTIONS_PER_CALL } from '../src/enhance/sweep.ts';
import { BATCH_TOKEN_BUDGET, estimateTokens } from '../src/enhance/coalesce.ts';

test('default judge profile derives every old number exactly', () => {
  assert.equal(JUDGE_PROFILE.name, 'typesafe-jev');
  assert.equal(MAX_QUESTIONS_PER_CALL, 255);
  assert.equal(BATCH_TOKEN_BUDGET, 30_000);
});

test('the Jev profile states the same limits as the Python loader, and nothing speculative', () => {
  const p = loadJudgeProfile('typesafe-jev');
  assert.equal(p.windowTokens, 32_768);
  assert.equal(p.inputCapTokens, 32_000);
  assert.equal(p.callTokens, 30_000);
  assert.equal(p.maxQuestionsPerCall, 255);
  assert.equal(p.inputUsdPerMtok, 0.042);
  assert.equal(p.maxParts, 40);
  assert.equal(p.confidenceLine, 0.8);
  assert.equal(p.fileCeilingBytes, 250_000);
  assert.equal(p.gateWindowTokens, 16_000);
  for (const dead of ['questionKinds', 'outputUsdPerMtok', 'label']) assert.ok(!(dead in p), dead);
});

test('there is no second selector: an unknown name is an error and the old variable is ignored', () => {
  assert.throws(() => loadJudgeProfile('no-such-judge'), /no-such-judge/);
  const before = process.env.SUPERJEV_JUDGE_PROFILE;
  process.env.SUPERJEV_JUDGE_PROFILE = 'no-such-judge';
  try { assert.equal(loadJudgeProfile().name, 'typesafe-jev'); }
  finally { if (before === undefined) delete process.env.SUPERJEV_JUDGE_PROFILE; else process.env.SUPERJEV_JUDGE_PROFILE = before; }
});

// The one unit (bytes/2, rounded up). The Python test asserts this same table.
const TOKENS: Array<[string, number]> = [['', 0], ['a', 1], ['abc', 2], ['abcd', 2], ['ééé', 3],
  ['中文', 3], ['12345678901234567890', 10]];

test('judgeTokens is the one estimator and matches the Python table', () => {
  for (const [text, want] of TOKENS) assert.equal(judgeTokens(text), want, text);
  assert.equal(judgeTokens(undefined), 0);
  assert.equal(judgeTokens({ a: 'bc' }), judgeTokens('{"a":"bc"}'));
  assert.equal(estimateTokens('abc'), 2);
  assert.equal(estimateTokens({ a: 'bc' }), judgeTokens({ a: 'bc' }));
});

test('the laya profile is keyless, reads answer_confidence and takes its URL from its own variable', () => {
  const p = loadJudgeProfile('laya');
  assert.equal(p.keyRequired, false);
  assert.equal(p.confidenceField, 'answer_confidence');
  assert.equal(p.apiUrlEnv, 'SUPERJEV_LAYA_URL');
  assert.equal(p.windowTokens, 1024);
  assert.ok(p.callTokens > 0);
  assert.equal(loadJudgeProfile('typesafe-jev').keyRequired, true);
});
