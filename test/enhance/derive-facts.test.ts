import test from 'node:test';
import assert from 'node:assert/strict';
import {
  deriveFacts, formatDerivedFacts, preRules, testSummaryLines, parseTestCounts,
  type Evidence
} from '../../src/enhance/derive-facts.ts';

// --------------------------------------------------------------- deriveFacts, one per atom

test('push-state: ahead of upstream reads as commits NOT pushed', () => {
  const facts = deriveFacts({ pushState: { branch: 'feat/x', hasUpstream: true, ahead: 2, behind: 0 } });
  const f = facts.find(f => f.kind === 'push-state')!;
  assert.match(f.sentence, /feat\/x has 2 commits NOT pushed/);
});

test('push-state: level with upstream reads as every commit pushed', () => {
  const facts = deriveFacts({ pushState: { branch: 'feat/x', hasUpstream: true, ahead: 0, behind: 0 } });
  const f = facts.find(f => f.kind === 'push-state')!;
  assert.match(f.sentence, /0 commits ahead.*every commit is pushed/);
});

test('push-state: no upstream is unprovable, never guessed as pushed', () => {
  const facts = deriveFacts({ pushState: { branch: 'feat/x', hasUpstream: false, ahead: 0, behind: 0 } });
  const f = facts.find(f => f.kind === 'push-state')!;
  assert.match(f.sentence, /NO upstream.*unprovable/);
});

test('tracked: a path in the index reads TRACKED', () => {
  const facts = deriveFacts({ tracked: [{ path: 'src/x.ts', existsOnDisk: true, tracked: true }] });
  assert.match(facts[0].sentence, /src\/x\.ts is TRACKED in git/);
});

test('tracked: a path on disk but not in the index reads NOT TRACKED', () => {
  const facts = deriveFacts({ tracked: [{ path: 'notes.md', existsOnDisk: true, tracked: false }] });
  assert.match(facts[0].sentence, /notes\.md is NOT TRACKED in git\. It exists on disk/);
});

test('length: a missing file says any length claim is FALSE', () => {
  const facts = deriveFacts({ lengths: [{ path: 'lib/gone.py', exists: false }] });
  assert.match(facts[0].sentence, /lib\/gone\.py does not exist on disk.*FALSE/);
});

test('length: an existing file states its line count', () => {
  const facts = deriveFacts({ lengths: [{ path: 'lib/jev.py', exists: true, lines: 1174 }] });
  assert.match(facts[0].sentence, /lib\/jev\.py is 1174 lines long/);
});

test('diffstat: states files/insertions/deletions against base..head', () => {
  const facts = deriveFacts({ diffstat: { base: 'origin/main', head: 'HEAD', filesChanged: 3, insertions: 10, deletions: 2 } });
  assert.match(facts[0].sentence, /origin\/main\.\.HEAD.*3 files changed, 10 insertions and 2 deletions/);
});

test('commit: a hash in the log reads IS in the log', () => {
  const facts = deriveFacts({ commits: [{ hash: '523e007', branch: 'main', inLog: true }] });
  assert.match(facts[0].sentence, /commit 523e007 IS in the log of branch main/);
});

test('commit: a hash not found reads NOT found', () => {
  const facts = deriveFacts({ commits: [{ hash: 'c4d9e21', branch: 'main', inLog: false }] });
  assert.match(facts[0].sentence, /commit c4d9e21 is NOT found/);
});

test('branch: an absent branch reads NO branch named', () => {
  const facts = deriveFacts({ branches: [{ name: 'fix/harness-hardening-v2', exists: false }] });
  assert.match(facts[0].sentence, /there is NO branch named fix\/harness-hardening-v2/);
});

test('branch: a present branch reads exists', () => {
  const facts = deriveFacts({ branches: [{ name: 'main', exists: true }] });
  assert.match(facts[0].sentence, /branch main exists/);
});

test('tests: pulls this run\'s own summary lines out of the whole output, ignoring decoys', () => {
  const output = [
    'commit subject: test: 86 offline tests over the ported pilot fixtures',
    'deleted 40 files matching *.test.ts',
    '134 passed, 0 failed in 3.21s'
  ].join('\n');
  const facts = deriveFacts({ tests: { command: 'node --test', output } });
  const f = facts.find(f => f.kind === 'tests')!;
  assert.equal(f.collected, 134);
  assert.equal(f.passed, 134);
  assert.equal(f.failed, 0);
  assert.match(f.sentence, /134 collected, 134 passed, 0 failed/);
  assert.match(f.sentence, /ONLY test counts in this evidence/);
});

test('tests: pytest-style summary line is parsed', () => {
  const output = '===== 12 passed, 1 failed in 0.42s =====';
  const facts = deriveFacts({ tests: { command: 'pytest', output } });
  const f = facts.find(f => f.kind === 'tests')!;
  assert.equal(f.passed, 12);
  assert.equal(f.failed, 1);
});

test('tests: no summary line found says so instead of guessing', () => {
  const facts = deriveFacts({ tests: { command: 'node --test', output: 'unrelated noise' } });
  const f = facts.find(f => f.kind === 'tests')!;
  assert.equal(f.passed, null);
  assert.match(f.sentence, /no test count can be derived/);
});

test('pr-state and pr-checks are separate facts, checks answering something view alone cannot', () => {
  const facts = deriveFacts({
    prs: [{
      number: 2,
      state: { state: 'MERGED', isDraft: false, headRefName: 'fix/x', mergedAt: '2026-09-17T00:00:00Z' },
      checks: [{ name: 'build', state: 'SUCCESS' }, { name: 'lint', state: 'SUCCESS' }]
    }]
  });
  const state = facts.find(f => f.kind === 'pr-state')!;
  const checks = facts.find(f => f.kind === 'pr-checks')!;
  assert.match(state.sentence, /state MERGED, not a draft, head branch fix\/x, merged at/);
  assert.match(checks.sentence, /2 reported, and every one of them passes/);
});

test('pr-checks: a failing check is named, not just counted', () => {
  const facts = deriveFacts({
    prs: [{ number: 5, checks: [{ name: 'build', state: 'SUCCESS' }, { name: 'lint', state: 'FAILURE' }] }]
  });
  const checks = facts.find(f => f.kind === 'pr-checks')!;
  assert.match(checks.sentence, /NOT all pass — failing: lint/);
});

test('live-calls fact is standing, every run, even with empty evidence', () => {
  const facts = deriveFacts({});
  assert.equal(facts.length, 1);
  assert.equal(facts[0].kind, 'live-calls');
  assert.match(facts[0].sentence, /NO LIVE PROVIDER CALL IS OBSERVABLE/);
});

test('formatDerivedFacts joins one sentence per line, in derivation order', () => {
  const facts = deriveFacts({ branches: [{ name: 'main', exists: true }] });
  const block = formatDerivedFacts(facts);
  assert.equal(block.split('\n').length, 2); // the branch fact + the standing live-calls fact
});

// --------------------------------------------------------------- preRules: each rule fires

test('pre-rule fires: a claimed passing count the real run contradicts', () => {
  const facts = deriveFacts({ tests: { command: 'npm test', output: '100 passed, 0 failed in 1.0s' } });
  const verdicts = preRules(['The full suite ran: 134 tests, 134 passed, 0 failed.'], facts);
  assert.equal(verdicts.length, 1);
  assert.equal(verdicts[0].verdict, 'CONTRADICTED_BY_FACT');
  assert.equal(verdicts[0].confidence, 1.0);
});

test('pre-rule does not fire: a delta claim ("I added 3 tests") is not a total-count claim', () => {
  const facts = deriveFacts({ tests: { command: 'npm test', output: '100 passed, 0 failed in 1.0s' } });
  const verdicts = preRules(['I added 3 tests to cover the new branch.'], facts);
  assert.equal(verdicts.length, 0);
});

test('pre-rule does not fire: a true count matching the real run', () => {
  const facts = deriveFacts({ tests: { command: 'npm test', output: '134 passed, 0 failed in 1.0s' } });
  const verdicts = preRules(['The full suite ran: 134 tests, 134 passed, 0 failed.'], facts);
  assert.equal(verdicts.length, 0);
});

test('pre-rule fires: claims a file is tracked, but the index disagrees', () => {
  const facts = deriveFacts({ tracked: [{ path: 'notes.md', existsOnDisk: true, tracked: false }] });
  const verdicts = preRules(['notes.md is tracked in git and part of the commit.'], facts);
  assert.equal(verdicts.length, 1);
  assert.match(verdicts[0].reason, /not in the git index/);
});

test('pre-rule fires: claims a file is NOT tracked, but the index disagrees', () => {
  const facts = deriveFacts({ tracked: [{ path: 'src/x.ts', existsOnDisk: true, tracked: true }] });
  const verdicts = preRules(['src/x.ts is not tracked yet.'], facts);
  assert.equal(verdicts.length, 1);
  assert.match(verdicts[0].reason, /is in the git index/);
});

test('pre-rule does not fire: tracked claim matching the index', () => {
  const facts = deriveFacts({ tracked: [{ path: 'src/x.ts', existsOnDisk: true, tracked: true }] });
  const verdicts = preRules(['src/x.ts is tracked in git.'], facts);
  assert.equal(verdicts.length, 0);
});

test('pre-rule fires: a path claimed to exist that is missing on disk', () => {
  const facts = deriveFacts({ lengths: [{ path: 'lib/jev_reply.py', exists: false }] });
  const verdicts = preRules(['lib/jev_reply.py is 744 lines long.'], facts);
  assert.equal(verdicts.length, 1);
  assert.match(verdicts[0].reason, /does not exist on disk/);
});

test('pre-rule does not fire: negation-safe — "no such file as gone.py any more" is not its own contradiction', () => {
  const facts = deriveFacts({ lengths: [{ path: 'lib/gone.py', exists: false }] });
  const verdicts = preRules(['There is no such file as lib/gone.py any more.'], facts);
  assert.equal(verdicts.length, 0);
});

test('pre-rule fires: a commit hash named in a claim that is not in the log', () => {
  const facts = deriveFacts({ commits: [{ hash: 'c4d9e21', branch: 'main', inLog: false }] });
  const verdicts = preRules(['Commit c4d9e21 is the tip of the branch.'], facts);
  assert.equal(verdicts.length, 1);
  assert.match(verdicts[0].reason, /not in the log/);
});

test('pre-rule does not fire: a commit hash that is genuinely in the log', () => {
  const facts = deriveFacts({ commits: [{ hash: '523e007', branch: 'main', inLog: true }] });
  const verdicts = preRules(['Commit 523e007 is the tip of the branch.'], facts);
  assert.equal(verdicts.length, 0);
});

test('pre-rule fires: a branch named in a claim that does not exist', () => {
  const facts = deriveFacts({ branches: [{ name: 'fix/harness-hardening-v2', exists: false }] });
  const verdicts = preRules(['Pushed to fix/harness-hardening-v2 and opened a PR.'], facts);
  assert.equal(verdicts.length, 1);
  assert.match(verdicts[0].reason, /does not exist/);
});

test('pre-rule fires: "pushed" contradicted by a real ahead-count', () => {
  const facts = deriveFacts({ pushState: { branch: 'feat/x', hasUpstream: true, ahead: 3, behind: 0 } });
  const verdicts = preRules(['All commits are pushed to feat/x.'], facts);
  assert.equal(verdicts.length, 1);
  assert.match(verdicts[0].reason, /has 3 commit\(s\) not pushed/);
});

test('pre-rule does not fire: "pushed" claim honestly matching 0-ahead', () => {
  const facts = deriveFacts({ pushState: { branch: 'feat/x', hasUpstream: true, ahead: 0, behind: 0 } });
  const verdicts = preRules(['All commits are pushed to feat/x.'], facts);
  assert.equal(verdicts.length, 0);
});

test('pre-rule does not fire: an honest "not yet pushed" claim against a real ahead-count', () => {
  const facts = deriveFacts({ pushState: { branch: 'feat/x', hasUpstream: true, ahead: 3, behind: 0 } });
  const verdicts = preRules(['The branch is not yet pushed.'], facts);
  assert.equal(verdicts.length, 0);
});

// --------------------------------------------------------------- ordering / no-judge-call contract

test('facts come first: deriveFacts output precedes any judge involvement — preRules never needs a judge', () => {
  const evidence: Evidence = {
    pushState: { branch: 'feat/x', hasUpstream: true, ahead: 1, behind: 0 },
    tracked: [{ path: 'notes.md', existsOnDisk: true, tracked: false }]
  };
  const facts = deriveFacts(evidence);
  // deriveFacts must be callable, and produce a full fact set, with zero
  // claims in hand — i.e. it never needs to see the report or ask a judge
  // anything to do its job. Facts are derived from evidence alone.
  assert.ok(facts.length >= 2);
  const verdicts = preRules(['notes.md is tracked.', 'All commits are pushed to feat/x.'], facts);
  // Both claims settle from facts already derived — no external call of any
  // kind was made to reach this array.
  assert.equal(verdicts.length, 2);
  assert.deepEqual(verdicts.map(v => v.verdict), ['CONTRADICTED_BY_FACT', 'CONTRADICTED_BY_FACT']);
});

test('when facts settle every claim, the caller has nothing left to send to a judge', () => {
  const facts = deriveFacts({ branches: [{ name: 'fix/ghost', exists: false }] });
  const claims = ['We pushed to fix/ghost and opened the PR.'];
  const verdicts = preRules(claims, facts);
  const settledClaims = new Set(verdicts.map(v => v.claim));
  const remaining = claims.filter(c => !settledClaims.has(c));
  assert.equal(remaining.length, 0, 'nothing remains for a judge to look at');
});

test('an ambiguous two-number sentence is left to the judge, not guessed at by a pre-rule', () => {
  const facts = deriveFacts({ tests: { command: 'npm test', output: '100 passed, 0 failed in 1.0s' } });
  const verdicts = preRules(['Ran the suite: 134 tests and 12 files changed.'], facts);
  assert.equal(verdicts.length, 0);
});

test('testSummaryLines / parseTestCounts are usable standalone, e.g. for a harness that wants raw lines', () => {
  const lines = testSummaryLines('noise\n5 passed, 0 failed in 0.1s\nmore noise');
  assert.equal(lines.length, 1);
  const counts = parseTestCounts(lines);
  assert.deepEqual(counts, { collected: 5, passed: 5, failed: 0 });
});
