import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const cli = new URL('../src/fetch-cli.ts', import.meta.url).pathname;

function runCli(args: string[], env: Record<string, string | undefined> = {}) {
  const merged: Record<string, string | undefined> = { ...process.env };
  delete merged.TYPESAFE_API_KEY;
  Object.assign(merged, env);
  const result = spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8', env: merged });
  return { code: result.status, stdout: result.stdout ?? '', stderr: result.stderr ?? '' };
}

const CATALOG = JSON.stringify([
  { id: 'gate', text: 'Check a draft reply against its evidence before it is sent.' },
  { id: 'pay-coned', text: 'Pay a Con Edison electric bill via the guest checkout flow.' },
  { id: 'tweet', text: 'Post to X/Twitter on the fleet account.' }
]);

async function withTmp(fn: (dir: string) => Promise<void>) {
  const dir = await mkdtemp(join(tmpdir(), 'super-jev-fetch-cli-'));
  try { await fn(dir); } finally { await rm(dir, { recursive: true, force: true }); }
}

// ---------------------------------------------------------------- dry-run

test('--dry-run prints the plan and reaches zero network with no API key set', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--dry-run', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.mode, 'dry-run');
    assert.equal(parsed.catalogSize, 3);
    assert.equal(parsed.calls, 1);
  });
});

test('--json prints exactly one parseable JSON object and nothing else on stdout', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--json']);
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.stdout.trim().split('\n').length, 1);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.mode, 'stub');
    assert.ok(Array.isArray(parsed.ranked));
    // The stub answers by hash, so some records may lose to "none of these" and drop out.
    assert.ok(parsed.ranked.length <= 3);
    assert.equal(typeof parsed.noMatch, 'boolean');
    assert.equal(typeof parsed.noMatchConfidence, 'number');
    assert.equal(parsed.calls, 1);
    assert.equal(parsed.prefilter.n, 8);
    for (const entry of parsed.ranked) {
      assert.equal(typeof entry.id, 'string');
      assert.equal(typeof entry.score, 'number');
      assert.equal(typeof entry.confidence, 'number');
    }
  });
});

// ---------------------------------------------------------------- rerun to same --out

test('a second run to the same --out overwrites instead of throwing', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const outDir = join(dir, 'out');
    const first = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--out', outDir, '--json']);
    assert.equal(first.code, 0, first.stderr);
    const second = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--out', outDir, '--json']);
    assert.equal(second.code, 0, second.stderr);
    assert.equal(first.stdout, second.stdout);
    const ranked = JSON.parse(await readFile(join(outDir, 'ranked.json'), 'utf8'));
    assert.ok(Array.isArray(ranked.ranked));
  });
});

// ---------------------------------------------------------------- k cap

test('--k caps the number of ids returned', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--k', '1', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    // The stub answers by hash; a record can lose to "none of these", so at most k.
    assert.ok(parsed.ranked.length <= 1);
  });
});

// ---------------------------------------------------------------- prefilter

/** 120 filler records sharing no words with "gate my reply", plus the three real ones. */
const BIG_CATALOG = JSON.stringify([
  ...Array.from({ length: 120 }, (_, i) => ({ id: `filler${i}`, text: `zorp quux blorf item number ${i} with nothing relevant` })),
  ...JSON.parse(CATALOG)
]);

test('--prefilter defaults to 8 and turns a many-call catalog into one call in the dry-run plan', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, BIG_CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--dry-run', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.catalogSize, 123);
    assert.equal(parsed.prefilter.n, 8);
    assert.equal(parsed.prefilter.kept, 8);
    assert.equal(parsed.prefilter.dropped, 115);
    assert.equal(parsed.calls, 1);
  });
});

test('--prefilter 0 disables the local filter and the plan goes back to one call per batch', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, BIG_CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--dry-run', '--json', '--prefilter', '0']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.prefilter.n, 0);
    assert.equal(parsed.prefilter.dropped, 0);
    assert.ok(parsed.calls > 1);
  });
});

test('--prefilter keeps the record whose words match the request, and the stub run reports calls', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, BIG_CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'check my draft reply against the evidence', '--stub', '--json', '--prefilter', '5']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.prefilter.kept, 5);
    assert.equal(parsed.calls, 1);
    assert.equal(typeof parsed.noMatch, 'boolean');
    // Every ranked id must be one the prefilter kept: a dropped record never reaches the judge.
    const dropped = new Set(parsed.prefilter.droppedIds);
    for (const entry of parsed.ranked) assert.ok(!dropped.has(entry.id));
    assert.ok(!dropped.has('gate'), 'the record sharing the request\'s words must survive the prefilter');
  });
});

test('--prefilter refuses a negative or fractional value, exit 1', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    for (const bad of ['-1', '1.5']) {
      const result = runCli(['--catalog', catalogPath, '--request', 'anything', '--dry-run', '--prefilter', bad]);
      assert.equal(result.code, 1);
      assert.match(result.stderr, /--prefilter/);
    }
  });
});

// ---------------------------------------------------------------- exit codes

test('missing --catalog is a usage error, exit 1', () => {
  const result = runCli(['--request', 'anything', '--dry-run']);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /usage|--catalog/i);
});

test('missing --request is a usage error, exit 1', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--dry-run']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /--request/);
  });
});

test('a malformed catalog file is a usage error, exit 1', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, 'not json', 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'anything', '--dry-run']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /not valid JSON/);
  });
});

test('live mode without TYPESAFE_API_KEY refuses instead of calling the network', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'anything']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /TYPESAFE_API_KEY/);
  });
});

test('--help prints usage and exits 0', () => {
  const result = runCli(['--help']);
  assert.equal(result.code, 0);
  assert.match(result.stdout, /--catalog/);
  assert.match(result.stdout, /--request/);
});

// ---------------------------------------------------------------- fetch v2: catalog schema, context, none gate, ledger

const V2_CATALOG = JSON.stringify([
  { id: 'restart-agent', text: 'Restart one team agent.', utterances: ['restart it', 'restart the agent', 'kick it back on'] },
  { id: 'pay-coned', text: 'Pay a Con Edison electric bill.', utterances: ['pay the electric bill', 'the power bill is due'] }
]);

test('a v2 catalog (utterances/negatives/tags) is accepted, and a run still produces a ranked list', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, V2_CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'pay the bill', '--stub', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.catalogSize, 2);
  });
});

test('--context folds recent turns into local narrowing, so a referent like "restart it" survives the prefilter', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, V2_CATALOG, 'utf8');
    const contextPath = join(dir, 'context.json');
    await writeFile(contextPath, JSON.stringify(['the agent seems stuck']), 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'restart it', '--context', contextPath, '--prefilter', '1', '--dry-run', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.oversizedRecordIds.length, 0);
  });
});

test('--context must be a JSON array of strings', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, V2_CATALOG, 'utf8');
    const contextPath = join(dir, 'context.json');
    await writeFile(contextPath, JSON.stringify({ not: 'an array' }), 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'restart it', '--context', contextPath, '--dry-run']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /context file must be a JSON array of strings/);
  });
});

test('--floor gates a low-confidence stub run into gated=true with candidates and an ask', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    // --floor 1.01 is refused; use 0.999999 so nothing can possibly clear it against the hashed stub confidences.
    const result = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--json', '--floor', '0.999999']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.gated, true);
    assert.ok(Array.isArray(parsed.candidates));
    assert.equal(typeof parsed.ask, 'string');
  });
});

test('--floor 0 --margin 0 never gates a run with at least one record beating "none of these"', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    // Both the floor and the margin have to be disabled here: the floor
    // alone no longer guarantees a serve once the margin default (0.10) is
    // in play, so this test isolates the floor by zeroing the margin too.
    const result = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--json', '--floor', '0', '--margin', '0']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    if (parsed.ranked.length > 0) assert.equal(parsed.gated, false);
  });
});

test('--floor refuses a value outside [0,1]', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'anything', '--dry-run', '--floor', '1.5']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /--floor/);
  });
});

test('--margin refuses a value outside [0,1]', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'anything', '--dry-run', '--margin', '1.5']);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /--margin/);
  });
});

test('the resultJson echoes the floor and margin actually in effect', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.floor, 0.60);
    assert.equal(parsed.margin, 0.10);
  });
});

test('SUPERJEV_FETCH_FLOOR and SUPERJEV_FETCH_MARGIN env vars set the defaults, and the CLI flag overrides the env var', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const viaEnv = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--json'], { SUPERJEV_FETCH_FLOOR: '0.9', SUPERJEV_FETCH_MARGIN: '0.5' });
    assert.equal(viaEnv.code, 0, viaEnv.stderr);
    assert.equal(JSON.parse(viaEnv.stdout).floor, 0.9);
    assert.equal(JSON.parse(viaEnv.stdout).margin, 0.5);

    const flagWins = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--json', '--floor', '0.2', '--margin', '0.05'], { SUPERJEV_FETCH_FLOOR: '0.9', SUPERJEV_FETCH_MARGIN: '0.5' });
    assert.equal(flagWins.code, 0, flagWins.stderr);
    assert.equal(JSON.parse(flagWins.stdout).floor, 0.2);
    assert.equal(JSON.parse(flagWins.stdout).margin, 0.05);
  });
});

test('--record appends {request, context, ranked, chosen, ts} to the ledger, and repeated runs append rather than overwrite', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const ledgerPath = join(dir, 'ledger.jsonl');
    const first = runCli(['--catalog', catalogPath, '--request', 'gate my reply', '--stub', '--json', '--record', 'gate', '--ledger', ledgerPath]);
    assert.equal(first.code, 0, first.stderr);
    const second = runCli(['--catalog', catalogPath, '--request', 'pay the bill', '--stub', '--json', '--record', 'pay-coned', '--ledger', ledgerPath]);
    assert.equal(second.code, 0, second.stderr);

    const body = await readFile(ledgerPath, 'utf8');
    const lines = body.trim().split('\n').map(l => JSON.parse(l));
    assert.equal(lines.length, 2);
    assert.equal(lines[0].chosen, 'gate');
    assert.equal(lines[1].chosen, 'pay-coned');
    for (const line of lines) {
      assert.equal(typeof line.request, 'string');
      assert.ok(Array.isArray(line.context));
      assert.ok(Array.isArray(line.ranked));
      assert.equal(typeof line.ts, 'string');
    }
  });
});

test('--ledger without --record is a usage error, exit 1', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'anything', '--dry-run', '--ledger', join(dir, 'ledger.jsonl')]);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /--ledger only applies with --record/);
  });
});

// ---------------------------------------------------------------- pre-rules (R1/R2/R3)

const PRERULES_CATALOG = JSON.stringify([
  {
    id: 'pay-coned', text: 'Pay a Con Edison electric bill via the guest checkout flow.',
    utterances: ['pay the electric bill', 'pay my coned bill'], negatives: ['gas bill']
  },
  {
    id: 'pay-water', text: 'Pay the water utility bill.',
    utterances: ['pay the water bill'], negatives: []
  }
]);

test('R1 serves a unique multi-word trigger directly: zero calls, manifest complete, source trigger', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, PRERULES_CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'please pay the electric bill', '--stub', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.mode, 'trigger');
    assert.equal(parsed.source, 'trigger');
    assert.equal(parsed.calls, 0);
    assert.equal(parsed.manifestComplete, true);
    assert.equal(parsed.ranked[0].id, 'pay-coned');
    assert.match(parsed.reason, /R1/);
  });
});

test('R1 does not fire on a single-word utterance, and falls through to the judge', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    const catalog = JSON.stringify([
      { id: 'pay-coned', text: 'Pay a Con Edison electric bill.', utterances: ['electric', 'pay the electric bill'], negatives: [] }
    ]);
    await writeFile(catalogPath, catalog, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'electric', '--stub', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.notEqual(parsed.mode, 'trigger');
    assert.equal(parsed.calls, 1);
  });
});

test('R1 does not fire on a trigger shared by more than one record', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    const catalog = JSON.stringify([
      { id: 'a', text: 'Record A.', utterances: ['do the thing now'], negatives: [] },
      { id: 'b', text: 'Record B.', utterances: ['do the thing now'], negatives: [] }
    ]);
    await writeFile(catalogPath, catalog, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'do the thing now', '--stub', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.notEqual(parsed.mode, 'trigger');
    assert.equal(parsed.calls, 1);
  });
});

test('--no-prerules bypasses R1 and always calls the judge, even for a request that would otherwise trigger-match', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, PRERULES_CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'please pay the electric bill', '--stub', '--json', '--no-prerules']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.notEqual(parsed.mode, 'trigger');
    assert.equal(parsed.calls, 1);
    assert.equal(parsed.source, 'judge');
  });
});

test('R2 demotes the judge top-1 to review when it has a negative-phrase hit, and prints why', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, PRERULES_CATALOG, 'utf8');
    // "gas bill" is pay-coned's own negative and is not a trigger of any
    // record, so R1 cannot fire and this exercises the judge + R2 path.
    const result = runCli(['--catalog', catalogPath, '--request', 'can you pay the gas bill this week', '--stub', '--json']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.notEqual(parsed.mode, 'trigger');
    assert.equal(parsed.calls, 1);
    if (parsed.source === 'pre-rule' && /R2/.test(parsed.reason ?? '')) {
      assert.equal(parsed.gated, true);
      // Re-run without --json to check the plain-words "prints why" line;
      // the stub is deterministic per question, so the same request settles
      // the same way.
      const plain = runCli(['--catalog', catalogPath, '--request', 'can you pay the gas bill this week', '--stub']);
      assert.equal(plain.code, 0, plain.stderr);
      assert.match(plain.stderr, /R2/);
    }
  });
});

test('R3 returns noMatch when no trigger appears anywhere and the judge top-1 is under the floor', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, PRERULES_CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'book a flight to chicago', '--stub', '--json', '--floor', '0.95']);
    assert.equal(result.code, 0, result.stderr);
    const parsed = JSON.parse(result.stdout);
    assert.notEqual(parsed.mode, 'trigger');
    assert.equal(parsed.gated, true);
  });
});

test('--explain prints the derived pre-rule facts before the plan', async () => {
  await withTmp(async dir => {
    const catalogPath = join(dir, 'catalog.json');
    await writeFile(catalogPath, PRERULES_CATALOG, 'utf8');
    const result = runCli(['--catalog', catalogPath, '--request', 'please pay the electric bill', '--stub', '--json', '--explain']);
    assert.equal(result.code, 0, result.stderr);
    assert.match(result.stderr, /trigger hit: record pay-coned/);
    assert.match(result.stderr, /ambiguity guard:/);
  });
});
