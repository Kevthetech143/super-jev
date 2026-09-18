import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  deriveFacts, formatDerivedFacts, preRules, testSummaryLines, parseTestCounts,
  windowFacts, windowLines, draftClauses,
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

test('pre-rule fires: claims PR #N merged, but gh pr view reports it OPEN', () => {
  const facts = deriveFacts({
    prs: [{ number: 12, state: { state: 'OPEN', isDraft: false, headRefName: 'feat/x', mergedAt: null } }]
  });
  const verdicts = preRules(['PR #12 open, checks green, merged.'], facts);
  assert.equal(verdicts.length, 1);
  assert.match(verdicts[0].reason, /claims PR #12 is merged, but its state is OPEN/);
});

test('pre-rule does not fire: claims PR #N merged, and it genuinely is MERGED', () => {
  const facts = deriveFacts({
    prs: [{ number: 12, state: { state: 'MERGED', isDraft: false, headRefName: 'feat/x', mergedAt: '2026-09-17T00:00:00Z' } }]
  });
  const verdicts = preRules(['PR #12 is merged.'], facts);
  assert.equal(verdicts.length, 0);
});

test('pre-rule fires: claims PR #N checks are green, but a check failed', () => {
  const facts = deriveFacts({
    prs: [{ number: 34, checks: [{ name: 'build', state: 'SUCCESS' }, { name: 'lint', state: 'FAILURE' }] }]
  });
  const verdicts = preRules(['PR #34 checks are green, ready to merge.'], facts);
  assert.equal(verdicts.length, 1);
  assert.match(verdicts[0].reason, /failing: lint/);
});

test('pre-rule does not fire: claims PR #N checks green, and every check passes', () => {
  const facts = deriveFacts({
    prs: [{ number: 34, checks: [{ name: 'build', state: 'SUCCESS' }, { name: 'lint', state: 'SUCCESS' }] }]
  });
  const verdicts = preRules(['PR #34 checks are green.'], facts);
  assert.equal(verdicts.length, 0);
});

test('pre-rule does not fire: a claim naming a PR number no fact was gathered for', () => {
  const facts = deriveFacts({
    prs: [{ number: 12, state: { state: 'OPEN', isDraft: false, headRefName: 'feat/x', mergedAt: null } }]
  });
  const verdicts = preRules(['PR #99 is merged.'], facts);
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

// --------------------------------------------------------------- windowFacts
//
// The fixtures are the remaining misses of the 2026-09-17 gate analysis —
// l09, l17, t06, t12 — with every window line copied verbatim from
// super-jev-experiments/gate-bench-20260917/analysis/LAST-MISSES.md, plus
// no-op cases that must add nothing. The SAME JSON file pins the pure-Python
// mirror in skills/super-jev/superjev.py (see
// skills/super-jev/tests/test_superjev.py), which is what stops the two
// implementations from drifting. Nothing here reaches a judge, a shell or a
// network: window text and draft text in, sentences out.

type WindowFactCase = { id: string; why: string; window: string; draft: string; expect: string[] };
const windowFactCases: WindowFactCase[] = JSON.parse(
  readFileSync(new URL('../fixtures/gate-window-facts.json', import.meta.url), 'utf8')
).cases;

for (const c of windowFactCases) {
  test(`windowFacts fixture ${c.id}: ${c.why}`, () => {
    assert.deepEqual(windowFacts(c.window, c.draft).map(f => f.sentence), c.expect);
  });
}

const fixture = (id: string): WindowFactCase => {
  const c = windowFactCases.find(x => x.id === id);
  assert.ok(c, `fixture ${id} missing`);
  return c!;
};

test('l09: the deleted card is stated as still present, and the receipt that names a DIFFERENT card is stated too', () => {
  const facts = windowFacts(fixture('l09').window, fixture('l09').draft);
  assert.ok(facts.some(f => /fable_awareness still present/.test(f.sentence)));
  assert.ok(facts.some(f => /fable_operating_manual removed per/.test(f.sentence)));
  // The draft's prose-only claim ("the operating manual card is gone") must
  // not be turned into a refutation of a card that really was removed.
  assert.ok(!facts.some(f => /fable_operating_manual still present/.test(f.sentence)));
  assert.ok(facts.every(f => f.family === 'delete-claim'));
});

test('l17: the cadence line is quoted and the integer comparison is done in code', () => {
  const facts = windowFacts(fixture('l17').window, fixture('l17').draft);
  assert.equal(facts.length, 1);
  assert.equal(facts[0].family, 'cadence');
  assert.match(facts[0].sentence, /every 152 turns, not every turn/);
  // the other card on the same listing is never mentioned: the draft makes
  // no claim about it, so there is nothing to state.
  assert.ok(!facts[0].sentence.includes('fable_operating_manual'));
});

test('l17: with no cadence claim in the draft, the same window derives nothing', () => {
  const quiet = 'Created, Sir. fable_awareness is on my role file, Fable-only, no bus.';
  assert.deepEqual(windowFacts(fixture('l17').window, quiet), []);
});

test('t12: the mismatch table is counted, including how many rows were STRICTER than expected', () => {
  const facts = windowFacts(fixture('t12').window, fixture('t12').draft);
  const sentences = facts.map(f => f.sentence);
  assert.ok(sentences.includes('table shows 4 of 4 mismatch rows stricter than expected, 0 more permissive.'));
  assert.ok(sentences.includes('table shows 26 of 30 match.'));
  assert.ok(sentences.includes('merge receipt found for PR #11 in [current turn].'));
});

test('t12: the same table with no universal claim in the draft derives no counts', () => {
  const facts = windowFacts(fixture('t12').window, 'Permit is shipped, Sir. The rerun looks healthy.');
  assert.ok(!facts.some(f => f.family === 'result-table'));
});

test('t06: one merge receipt is named, the two with no receipt are not invented, and the session-wide total is stated as uncheckable', () => {
  const facts = windowFacts(fixture('t06').window, fixture('t06').draft);
  assert.deepEqual(facts.map(f => f.sentence), [
    'merge receipt found for PR #4 in [session receipts].',
    "merge receipts in window: 1; the draft's session-wide total cannot be checked here."
  ]);
});

test('merge count: a total the window already matches is checkable, so no uncheckable line is added', () => {
  const window = '[session receipts]\nMERGED (#4)\nMERGED (#5)\n';
  const facts = windowFacts(window, 'Two PRs merged today, Sir.');
  assert.ok(!facts.some(f => /cannot be checked here/.test(f.sentence)));
});

test('merge count: a universal merge claim with no number states the window count as uncheckable', () => {
  const window = '[session receipts]\nMERGED (#4)\n';
  const facts = windowFacts(window, 'Every item on the build list is merged, Sir.');
  assert.ok(facts.some(f => f.sentence === "merge receipts in window: 1; the draft's session-wide total cannot be checked here."));
});

test('merge claim: "either merged or on PR #3" is not read as a claim that #3 is merged', () => {
  const window = '[session receipts]\nMERGED (#4)\n';
  const facts = windowFacts(window, 'Every item is either merged or on PR #3, Sir.');
  assert.ok(!facts.some(f => /no merge receipt for PR #3/.test(f.sentence)));
});

test('a mismatch row with a label we cannot rank is counted but not read as stricter or looser', () => {
  const window = "[current turn]\n('x-01', 'weird_label', 'other_label')\n";
  const facts = windowFacts(window, 'Every case now refuses, Sir.');
  // no strictness sentence, because a rank we do not have is not guessed at
  assert.ok(!facts.some(f => /stricter than expected/.test(f.sentence)));
});

test('windowLines labels each line with the window section it sits under', () => {
  const lines = windowLines('[session receipts]\nMERGED 1\n\n===\n\n[current turn]\nWROTE  : x\n');
  assert.deepEqual(lines, [
    { label: '[session receipts]', line: 'MERGED 1' },
    { label: '[current turn]', line: 'WROTE  : x' }
  ]);
});

test('draftClauses isolates a trailing "and I also deleted X" clause from the true clauses it rides on', () => {
  const clauses = draftClauses(fixture('l09').draft);
  assert.ok(clauses.some(c => /^I also deleted the old fable_awareness duplicate\.$/.test(c)));
});

test('window facts ride at the HEAD of deriveFacts, above the structured atoms', () => {
  const facts = deriveFacts({
    window: { text: fixture('l17').window, draft: fixture('l17').draft },
    tests: { command: 'npm test', output: '10 passed, 0 failed in 1.0s' }
  });
  assert.equal(facts[0].kind, 'window');
  assert.ok(facts.some(f => f.kind === 'tests'));
  assert.match(formatDerivedFacts(facts).split('\n')[0], /every 152 turns/);
});

test('an evidence object with no window is unchanged by any of this', () => {
  const facts = deriveFacts({ branches: [{ name: 'feat/x', exists: true }] });
  assert.ok(!facts.some(f => f.kind === 'window'));
});
