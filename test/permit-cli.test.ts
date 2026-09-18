import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const cli = new URL('../src/permit-cli.ts', import.meta.url).pathname;

async function runCli(args: string[], timeoutMs = 5_000) {
  const child = spawn(process.execPath, [cli, ...args], { stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '', stderr = '';
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  child.stdout.on('data', c => { stdout += c; });
  child.stderr.on('data', c => { stderr += c; });
  let killed = false;
  const timer = setTimeout(() => { killed = true; child.kill('SIGKILL'); }, timeoutMs);
  const code = await new Promise<number | null>(resolve => child.on('close', resolve));
  clearTimeout(timer);
  return { code, stdout, stderr, killed };
}

async function withSnapshot(body: unknown, fn: (dir: string, path: string) => Promise<void>) {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-permit-'));
  try {
    const path = join(dir, 'snapshot.json');
    await writeFile(path, JSON.stringify(body));
    await fn(dir, path);
  } finally { await rm(dir, { recursive: true, force: true }); }
}

test('no arguments prints usage and exits 0, same convention as sweep', async () => {
  const result = await runCli([]);
  assert.equal(result.code, 0);
  assert.match(result.stdout, /super-jev permit/);
});

test('--snapshot with no value is a usage error, exit 1', async () => {
  const result = await runCli(['--snapshot']);
  assert.equal(result.code, 1);
});

test('--help exits 0', async () => {
  const result = await runCli(['--help']);
  assert.equal(result.code, 0);
  assert.match(result.stdout, /super-jev permit/);
});

test('a missing snapshot file is a usage error, exit 1', async () => {
  const result = await runCli(['--snapshot', '/no/such/file.json', '--dry-run']);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /Cannot read the snapshot file/);
});

test('a snapshot with no action and no --action is a usage error, exit 1', async () => {
  await withSnapshot({ target: 'x' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--dry-run']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /No action/);
  });
});

test('--dry-run reaches no network and exits 0, printing the request it would send', async () => {
  await withSnapshot({ action: 'refresh the cache', target: 'cache/main' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--dry-run', '--json']);
    assert.equal(result.code, 0);
    assert.equal(result.killed, false);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.mode, 'dry-run');
    assert.equal(parsed.request.state.action, 'refresh the cache');
    assert.match(result.stderr, /nothing was sent, no provider was called/);
  });
});

test('--action overrides a snapshot action', async () => {
  await withSnapshot({ action: 'snapshot action' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--action', 'overridden action', '--dry-run', '--json']);
    assert.equal(result.code, 0);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.request.state.action, 'overridden action');
  });
});

test('--stub with a non-irreversible action returns safe_to_auto, exit 0, valid json shape', async () => {
  await withSnapshot({ action: 'refresh the local cache' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json']);
    assert.equal(result.code, 0);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'safe_to_auto');
    assert.equal(parsed.hardRuleApplied, false);
    assert.ok(typeof parsed.confidence === 'number');
    assert.ok(typeof parsed.reason === 'string');
    assert.equal(parsed.id, 'action');
  });
});

test('--stub with an irreversible action (hard rule) is downgraded to needs_approval, exit 2', async () => {
  await withSnapshot({ action: 'delete the production record' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json']);
    assert.equal(result.code, 2);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'needs_approval');
    assert.equal(parsed.hardRuleApplied, true);
    assert.equal(parsed.class, 'irreversible_routine');
    assert.deepEqual(parsed.matchedKeywords, ['delete']);
  });
});

test('--stub with a destructive-class action in the target field refuses outright, exit 3', async () => {
  await withSnapshot({ action: 'run cleanup', target: 'wire the funds' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json']);
    assert.equal(result.code, 3);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'refuse');
    assert.equal(parsed.class, 'destructive');
    assert.deepEqual(parsed.matchedKeywords, ['wire']);
  });
});

test('--stub with an irreversible-but-routine action in the target field is downgraded to needs_approval, exit 2', async () => {
  await withSnapshot({ action: 'run cleanup', target: 'settle the balance' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json']);
    assert.equal(result.code, 2);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'needs_approval');
    assert.equal(parsed.class, 'irreversible_routine');
    assert.deepEqual(parsed.matchedKeywords, ['transfer/settle']);
  });
});

test('--min-confidence set above the fixed stub confidence forces needs_approval', async () => {
  await withSnapshot({ action: 'refresh the local cache' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json', '--min-confidence', '0.99']);
    assert.equal(result.code, 2);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'needs_approval');
  });
});

test('live mode without TYPESAFE_API_KEY, --dry-run or --stub is a usage error, exit 1', async () => {
  await withSnapshot({ action: 'refresh the local cache' }, async (_dir, path) => {
    const child = spawn(process.execPath, [cli, '--snapshot', path], {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, TYPESAFE_API_KEY: '' }
    });
    let stderr = '';
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', c => { stderr += c; });
    const code = await new Promise<number | null>(resolve => child.on('close', resolve));
    assert.equal(code, 1);
    assert.match(stderr, /Set TYPESAFE_API_KEY/);
  });
});

// ---------------------------------------------------------------------------
// Pre-rules: default ON, deterministic, zero-network layer in front of the
// judge. See src/enhance/permit-prerules.ts for the rule logic itself;
// these tests are about the CLI wiring -- flags, exit codes, calls: 0 when
// settled, --no-prerules bypass, --explain.
// ---------------------------------------------------------------------------

test('pre-rules default ON: a destructive action refuses with zero model calls', async () => {
  await withSnapshot({ action: 'rm -rf /tmp/some/scratch/dir' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json']);
    assert.equal(result.code, 3);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'refuse');
    assert.equal(parsed.calls, 0);
    assert.equal(parsed.settledBy, 'hard_rule_destructive');
  });
});

test('pre-rules: format the disk refuses; format the code (formatter) does not', async () => {
  await withSnapshot({ action: 'diskutil erase the external usb drive', target: '/dev/disk4' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json']);
    assert.equal(result.code, 3);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'refuse');
    assert.equal(parsed.calls, 0);
  });
  await withSnapshot({ action: 'run the formatter over the file before committing' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json']);
    // No hard-rule keyword and no disk in scope: falls through to the
    // (stubbed) judge, which always answers safe_to_auto for a
    // non-irreversible action.
    assert.equal(result.code, 0);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'safe_to_auto');
    assert.equal(parsed.calls, 1);
  });
});

test('pre-rules: money over --money-threshold needs approval with zero calls, regardless of the stubbed judge answer', async () => {
  await withSnapshot({ action: 'pay the vendor invoice, $1450, to the usual vendor' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json']);
    assert.equal(result.code, 2);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.verdict, 'needs_approval');
    assert.equal(parsed.calls, 0);
    assert.equal(parsed.settledBy, 'money_rule');
  });
});

test('pre-rules: --money-threshold raises the bar for the money rule', async () => {
  await withSnapshot({ action: 'send $500 to the usual vendor' }, async (_dir, path) => {
    const under = await runCli(['--snapshot', path, '--stub', '--json', '--money-threshold', '1000']);
    // Under the raised threshold: pre-rules do not settle it on the money
    // rule, so it falls through to the (still hard-rule-matching "send")
    // path -- exit 2, but via the existing hard rule, not the money rule.
    const parsedUnder = JSON.parse(under.stdout);
    assert.notEqual(parsedUnder.settledBy, 'money_rule');

    const over = await runCli(['--snapshot', path, '--stub', '--json', '--money-threshold', '100']);
    const parsedOver = JSON.parse(over.stdout);
    assert.equal(parsedOver.verdict, 'needs_approval');
    assert.equal(parsedOver.settledBy, 'money_rule');
    assert.equal(parsedOver.calls, 0);
  });
});

test('pre-rules: --new-payee needs approval even under the money threshold', async () => {
  await withSnapshot({ action: 'pay $50 to', target: 'a brand new contractor' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json', '--new-payee']);
    assert.equal(result.code, 2);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.settledBy, 'money_rule');
    assert.equal(parsed.calls, 0);
  });
});

test('--no-prerules bypasses the pre-rule layer entirely: same behavior as before it existed', async () => {
  await withSnapshot({ action: 'rm -rf /tmp/some/scratch/dir' }, async (_dir, path) => {
    const withRules = await runCli(['--snapshot', path, '--stub', '--json']);
    const withoutRules = await runCli(['--snapshot', path, '--stub', '--json', '--no-prerules']);
    // Both refuse at 0 calls either way -- decidePermit's OWN hard rule
    // still applies with --no-prerules, since that rule predates and is
    // independent of this pre-rule layer. What --no-prerules actually
    // removes is this file's own settledBy label and derived facts.
    assert.equal(withRules.code, 3);
    assert.equal(withoutRules.code, 3);
    const parsedWith = JSON.parse(withRules.stdout);
    const parsedWithout = JSON.parse(withoutRules.stdout);
    assert.equal(parsedWith.calls, 0);
    assert.equal(parsedWith.settledBy, 'hard_rule_destructive');
    assert.equal(parsedWithout.calls, 0);
    assert.equal(parsedWithout.settledBy, undefined);
    assert.equal(parsedWithout.preRules, undefined);
  });
});

test('--explain lists the rule that fired', async () => {
  await withSnapshot({ action: 'rm -rf /tmp/some/scratch/dir' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json', '--explain']);
    const parsed = JSON.parse(result.stdout);
    assert.match(parsed.explain, /hard_rule_destructive/);
  });
});

test('--explain reports "no pre-rule fired" when everything is deferred to the judge', async () => {
  await withSnapshot({ action: 'refresh the local cache' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--stub', '--json', '--explain']);
    const parsed = JSON.parse(result.stdout);
    assert.match(parsed.explain, /no pre-rule fired/);
  });
});

test('a destructive action on a tracked, committed file inside --worktree is passed to the judge, not auto-refused', async () => {
  const repo = await mkdtemp(join(tmpdir(), 'super-jev-permit-repo-'));
  const { spawnSync } = await import('node:child_process');
  const run = (args: string[]) => spawnSync('git', args, { cwd: repo });
  try {
    run(['init', '-q']);
    run(['config', 'user.email', 'test@example.com']);
    run(['config', 'user.name', 'Test']);
    await writeFile(join(repo, 'tracked.txt'), 'hello\n');
    run(['add', 'tracked.txt']);
    run(['commit', '-q', '-m', 'init']);

    await withSnapshot({ action: 'rm -rf tracked.txt to clean up before the run', target: 'tracked.txt' }, async (_dir, path) => {
      const result = await runCli(['--snapshot', path, '--stub', '--json', '--cwd', repo, '--worktree', repo]);
      // decidePermit's OWN hard rule (unaffected by pre-rules deferring)
      // still classifies "rm -rf" as destructive and refuses without a
      // model call -- the pre-rule layer's job here is only to not have
      // refused it itself, sight unseen, before that hard rule/judge ran.
      const parsed = JSON.parse(result.stdout);
      assert.equal(parsed.preRulesSettledBy, null);
      assert.equal(parsed.verdict, 'refuse');
      assert.equal(parsed.calls, 0);
      assert.ok(parsed.preRules);
      assert.equal(parsed.preRules.reversibility.gitTracked, true);
      assert.equal(parsed.preRules.scope.insideTaskWorktree, true);
    });
  } finally {
    await rm(repo, { recursive: true, force: true });
  }
});

test('dry-run still prints derived facts when pre-rules are enabled, and still exits 0 regardless of what they would settle', async () => {
  await withSnapshot({ action: 'rm -rf /tmp/some/scratch/dir' }, async (_dir, path) => {
    const result = await runCli(['--snapshot', path, '--dry-run', '--json']);
    assert.equal(result.code, 0);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.mode, 'dry-run');
    assert.equal(parsed.preRulesVerdict, 'refuse');
  });
});
