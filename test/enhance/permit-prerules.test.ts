import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  decide,
  parseActionObject,
  reversibilityFacts,
  scopeFacts,
  DEFAULT_MONEY_THRESHOLD
} from '../../src/enhance/permit-prerules.ts';

function git(args: string[], cwd: string) {
  const p = spawnSync('git', args, { cwd, encoding: 'utf8' });
  if (p.status !== 0) throw new Error(`git ${args.join(' ')} failed: ${p.stderr}`);
  return p.stdout;
}

function makeRepo(): string {
  const dir = mkdtempSync(join(tmpdir(), 'super-jev-prerules-'));
  git(['init', '-q'], dir);
  git(['config', 'user.email', 'test@example.com'], dir);
  git(['config', 'user.name', 'Test'], dir);
  writeFileSync(join(dir, 'tracked.txt'), 'hello\n');
  git(['add', 'tracked.txt'], dir);
  git(['commit', '-q', '-m', 'init'], dir);
  return dir;
}

// ---------------------------------------------------------------------------
// The invariant the brief names explicitly: no pre-rule may ever return
// safe_to_auto. Only refuse, needs_approval, or defer_to_judge.
// ---------------------------------------------------------------------------
test('decide() never returns safe_to_auto for any action, in any options combination', () => {
  const actions = [
    'rm -rf /tmp/scratch', 'refresh the cache', 'pay $5 to a friend', 'pay $5000 to a new vendor',
    'run the formatter', 'format the disk', 'git push --force origin feature-x',
    'drop table staging.records', 'send an email to the team', 'wipe the drive',
    'delete the file', 'do nothing at all'
  ];
  for (const action of actions) {
    for (const opts of [{}, { newPayee: true }, { moneyThreshold: 0 }, { cwd: '/tmp' }]) {
      const result = decide(action, undefined, opts);
      assert.notEqual(result.verdict, 'safe_to_auto' as any, `action "${action}" with opts ${JSON.stringify(opts)} must never be safe_to_auto`);
      assert.ok(['refuse', 'needs_approval', 'defer_to_judge'].includes(result.verdict));
    }
  }
});

// ---------------------------------------------------------------------------
// Rule: destructive verb + irreversible target -> refuse, no judge call.
// ---------------------------------------------------------------------------
test('rule fires: rm -rf with no --cwd refuses outright, settled_by hard_rule_destructive', () => {
  const result = decide('rm -rf ~/.adspin ~/.vscode after uninstalling', '~/.adspin');
  assert.equal(result.verdict, 'refuse');
  assert.equal(result.settledBy, 'hard_rule_destructive');
  assert.ok(result.facts.matchedKeywords.includes('rm -rf'));
});

test('rule fires: git reset --hard refuses outright', () => {
  const result = decide('git reset --hard origin/main', 'origin/main');
  assert.equal(result.verdict, 'refuse');
  assert.equal(result.settledBy, 'hard_rule_destructive');
});

test('rule defers: a plain, non-hard-rule action is deferred to judge', () => {
  const result = decide('refresh the local cache', 'cache/main');
  assert.equal(result.verdict, 'defer_to_judge');
  assert.equal(result.settledBy, null);
});

// ---------------------------------------------------------------------------
// Rule: format only refuses when the object is a disk/volume/partition --
// the fix for the formatter false-refuse ("format the code" must defer).
// ---------------------------------------------------------------------------
test('rule fires: diskutil erase / format a disk refuses (destructive)', () => {
  const diskResult = decide('diskutil erase the external USB drive before donating it', '/dev/disk4');
  assert.equal(diskResult.verdict, 'refuse');
  assert.equal(diskResult.settledBy, 'hard_rule_destructive');

  const formatDisk = decide('format the disk before reinstalling', '/dev/disk2');
  assert.equal(formatDisk.verdict, 'refuse');
});

test('rule defers: "format the code" / "run the formatter" is not a disk wipe, no refuse', () => {
  const codeResult = decide('run the formatter over the file before committing', 'src/index.ts');
  assert.notEqual(codeResult.verdict, 'refuse');
  assert.equal(codeResult.facts.hardRuleClass, undefined);

  const previewResult = decide('preview the changes a formatter would make', undefined);
  assert.notEqual(previewResult.verdict, 'refuse');
});

// ---------------------------------------------------------------------------
// Rule: money above threshold, or a new payee, -> needs_approval regardless
// of judge, with no model call.
// ---------------------------------------------------------------------------
test('rule fires: amount over the default $200 threshold needs approval, no judge', () => {
  const result = decide("pay the tenant's rent through the property portal, $450, to a payee already on file", 'tenant-portal.example');
  assert.equal(result.verdict, 'needs_approval');
  assert.equal(result.settledBy, 'money_rule');
  assert.equal(result.facts.money.amountUsd, 450);
  assert.equal(result.facts.money.overThreshold, true);
});

test('rule defers: amount under threshold with no new payee is left to the judge', () => {
  const result = decide('pay the electric bill, $184.50', 'utility payment processor');
  assert.equal(result.verdict, 'defer_to_judge');
  assert.equal(result.facts.money.overThreshold, false);
  assert.equal(result.facts.money.newPayee, false);
});

test('rule fires: a new payee needs approval even under the money threshold', () => {
  const result = decide('pay $50 to a brand new contractor', 'new-contractor@example.com', { newPayee: true });
  assert.equal(result.verdict, 'needs_approval');
  assert.equal(result.settledBy, 'money_rule');
});

test('--money-threshold is configurable', () => {
  const underDefault = decide('pay $500 to the usual vendor', 'usual-vendor', { moneyThreshold: 1000 });
  assert.equal(underDefault.verdict, 'defer_to_judge');

  const overCustom = decide('pay $500 to the usual vendor', 'usual-vendor', { moneyThreshold: 100 });
  assert.equal(overCustom.verdict, 'needs_approval');
  assert.equal(overCustom.settledBy, 'money_rule');
});

test('DEFAULT_MONEY_THRESHOLD is 200, matching the risk-scaled payments rule', () => {
  assert.equal(DEFAULT_MONEY_THRESHOLD, 200);
});

// ---------------------------------------------------------------------------
// Rule: destructive verb on a tracked, committed file inside the task
// worktree is passed to the judge (not refused, not auto-allowed) with a
// "recoverable from git" fact.
// ---------------------------------------------------------------------------
test('rule fires (carve-out): destructive verb on a tracked file inside the worktree defers to judge with a recoverable-from-git fact, not a refuse', () => {
  const repo = makeRepo();
  try {
    const result = decide('rm -rf tracked.txt to clean up before the test run', 'tracked.txt', { cwd: repo, worktree: repo });
    assert.equal(result.verdict, 'defer_to_judge');
    assert.equal(result.settledBy, null);
    assert.match(result.reason, /recoverable from the index/);
    assert.equal(result.facts.reversibility.gitTracked, true);
    assert.equal(result.facts.scope.insideTaskWorktree, true);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test('destructive verb on an UNTRACKED file inside the worktree still refuses (no carve-out)', () => {
  const repo = makeRepo();
  try {
    writeFileSync(join(repo, 'scratch.txt'), 'not tracked\n');
    const result = decide('rm -rf scratch.txt', 'scratch.txt', { cwd: repo, worktree: repo });
    assert.equal(result.verdict, 'refuse');
    assert.equal(result.settledBy, 'hard_rule_destructive');
    assert.equal(result.facts.reversibility.gitTracked, false);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test('destructive verb on a tracked file OUTSIDE the given worktree still refuses (scope matters, not just git tracking)', () => {
  const repo = makeRepo();
  try {
    const result = decide('rm -rf tracked.txt', 'tracked.txt', { cwd: repo, worktree: '/some/other/worktree' });
    assert.equal(result.verdict, 'refuse');
    assert.equal(result.settledBy, 'hard_rule_destructive');
    assert.equal(result.facts.scope.insideTaskWorktree, false);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Reversibility facts: unknown (null) without --cwd, never a guessed false.
// ---------------------------------------------------------------------------
test('reversibilityFacts stays unknown (null) without --cwd, never guesses', () => {
  const facts = reversibilityFacts('/tmp/whatever', undefined);
  assert.equal(facts.exists, null);
  assert.equal(facts.gitTracked, null);
  assert.match(facts.note ?? '', /unverifiable/);
});

test('reversibilityFacts reads real filesystem/git state with --cwd', () => {
  const repo = makeRepo();
  try {
    const facts = reversibilityFacts('tracked.txt', repo);
    assert.equal(facts.exists, true);
    assert.equal(facts.gitTracked, true);
    assert.equal(facts.inGitRepo, true);

    const missing = reversibilityFacts('does-not-exist.txt', repo);
    assert.equal(missing.exists, false);
    assert.equal(missing.isNoopDelete, true);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Verb + target parse.
// ---------------------------------------------------------------------------
test('parseActionObject extracts rm -rf path, force-push branch, drop target, format target, amount + payee', () => {
  assert.equal(parseActionObject('rm -rf /tmp/scratch').rmRfPath, '/tmp/scratch');
  assert.equal(parseActionObject('git push --force origin feature-x').forcePushBranch, 'feature-x');
  const drop = parseActionObject('drop table staging.records');
  assert.equal(drop.dropTarget, 'staging.records');
  assert.equal(drop.dropKind, 'table');
  assert.equal(parseActionObject('format the external disk').formatTarget, 'external');
  assert.equal(parseActionObject('pay $184.50 to the utility').amountUsd, 184.5);
  const payee = parseActionObject('wire $500 to newvendor');
  assert.equal(payee.payee, 'newvendor');
});

test('scopeFacts: inside vs outside the given worktree', () => {
  const inside = scopeFacts('/Users/example/wt/task/file.txt', '/Users/example/wt/task');
  assert.equal(inside.insideTaskWorktree, true);
  const outside = scopeFacts('/Users/example/elsewhere/file.txt', '/Users/example/wt/task');
  assert.equal(outside.insideTaskWorktree, false);
});
