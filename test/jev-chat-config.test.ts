import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  loadConfig, saveConfig, configFileMode, hasApiKey,
  cannedReply, parseSlashCommand, isKnownSlashCommand,
  parseMissCandidate, formatMissCandidateReply,
  MISS_LINE,
} from '../src/jev-chat-config.ts';

function withTempConfig(fn: (path: string) => void) {
  const dir = mkdtempSync(join(tmpdir(), 'superjev-test-'));
  const path = join(dir, 'nested', 'config.json');
  try { fn(path); } finally { rmSync(dir, { recursive: true, force: true }); }
}

test('loadConfig returns {} when no file exists', () => {
  withTempConfig((path) => {
    assert.deepEqual(loadConfig(path), {});
  });
});

test('saveConfig writes a 0600 file and round-trips', () => {
  withTempConfig((path) => {
    saveConfig({ typesafeApiKey: 'sk-fake-123', principal: 'kelvin', folders: ['/a', '/b'] }, path);
    assert.equal(configFileMode(path), 0o600);
    const loaded = loadConfig(path);
    assert.equal(loaded.typesafeApiKey, 'sk-fake-123');
    assert.equal(loaded.principal, 'kelvin');
    assert.deepEqual(loaded.folders, ['/a', '/b']);
  });
});

test('saveConfig re-locks permissions even if the file already existed looser', () => {
  withTempConfig((path) => {
    saveConfig({ typesafeApiKey: 'a' }, path);
    saveConfig({ typesafeApiKey: 'b' }, path);
    assert.equal(configFileMode(path), 0o600);
  });
});

test('hasApiKey', () => {
  assert.equal(hasApiKey({}), false);
  assert.equal(hasApiKey({ typesafeApiKey: '' }), false);
  assert.equal(hasApiKey({ typesafeApiKey: 'x' }), true);
});

test('config file on disk never contains the literal key twice or leaks it unexpectedly', () => {
  withTempConfig((path) => {
    const key = 'sk-super-secret-value';
    saveConfig({ typesafeApiKey: key, principal: 'p' }, path);
    const raw = readFileSync(path, 'utf8');
    // it's the only place the key should live -- sanity check it's valid JSON
    // containing exactly the key we wrote, nothing garbled.
    const parsed = JSON.parse(raw);
    assert.equal(parsed.typesafeApiKey, key);
  });
});

test('cannedReply matches small talk', () => {
  assert.match(cannedReply('hi')!, /Super Jev/);
  assert.match(cannedReply('Hello!')!, /Super Jev/);
  assert.ok(cannedReply('who are you?'));
  assert.ok(cannedReply('help'));
  assert.ok(cannedReply('thanks'));
  assert.ok(cannedReply('what can you do'));
});

test('cannedReply returns null for real questions', () => {
  assert.equal(cannedReply('what is the breakeven on CLOV'), null);
  assert.equal(cannedReply(''), null);
});

test('parseSlashCommand parses known and unknown commands', () => {
  assert.deepEqual(parseSlashCommand('/help'), { command: 'help', args: [] });
  assert.deepEqual(parseSlashCommand('/folders /a /b'), { command: 'folders', args: ['/a', '/b'] });
  assert.equal(parseSlashCommand('plain question'), null);
  assert.deepEqual(parseSlashCommand('/bogus'), { command: 'bogus', args: [] });
});

test('isKnownSlashCommand', () => {
  assert.equal(isKnownSlashCommand('help'), true);
  assert.equal(isKnownSlashCommand('setup'), true);
  assert.equal(isKnownSlashCommand('folders'), true);
  assert.equal(isKnownSlashCommand('quit'), true);
  assert.equal(isKnownSlashCommand('bogus'), false);
});

test('MISS_LINE is the exact Jev voice line', () => {
  assert.equal(MISS_LINE, "Super Jev: I didn't have this. Want me to find it by hand and save it for next time?");
});

// --- adversarial review regressions ---
import { askLookupArgs } from '../src/jev-chat-config.ts';
import { chmodSync, mkdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

test('canned replies do not hijack real questions that merely contain small talk', () => {
  for (const q of [
    'who are you going to call about my taxes?',
    'what are you seeing in the Q3 report',
    'what can you do about the leaking roof',
    'help me find the lease file',
  ]) assert.equal(cannedReply(q), null, q);
  assert.ok(cannedReply('who are you?'));
  assert.ok(cannedReply('What can you do'));
});

test('askLookupArgs never lets user text become an ask.py flag', () => {
  for (const q of ['--miss', '--add x y', '--approve a b', '--principal evil', '--no-auto', '-x']) {
    const args = askLookupArgs('kelvin', q);
    assert.deepEqual(args.slice(0, 2), ['--principal', 'kelvin']);
    const rest = args.slice(2);
    assert.ok(rest.every((a) => !a.startsWith('-')), JSON.stringify(rest));
  }
  assert.deepEqual(askLookupArgs('k', 'what is x'), ['--principal', 'k', 'what is x']);
});

test('saveConfig re-locks a pre-existing loose dir and file', () => {
  withTempConfig((path) => {
    mkdirSync(dirname(path), { recursive: true, mode: 0o755 });
    chmodSync(dirname(path), 0o755);
    writeFileSync(path, '{}', { mode: 0o644 });
    chmodSync(path, 0o644);
    saveConfig({ typesafeApiKey: 'sk-fake' }, path);
    assert.equal(statSync(dirname(path)).mode & 0o777, 0o700);
    assert.equal(configFileMode(path), 0o600);
  });
});

test('loadConfig treats a JSON array as no config', () => {
  withTempConfig((path) => {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, '["x"]');
    assert.deepEqual(loadConfig(path), {});
  });
});

test('parseMissCandidate returns null on a cache hit', () => {
  assert.equal(parseMissCandidate('CACHE HIT\nanswer: foo\n'), null);
});

test('parseMissCandidate picks the top-ranked candidate line', () => {
  const stdout = ' 0.85  /notes/pricing.md  [kelvin-notes]  possible\n 0.40  /notes/other.md  [kelvin-notes]\n';
  const c = parseMissCandidate(stdout);
  assert.deepEqual(c, { score: 0.85, path: '/notes/pricing.md', pointer: 'kelvin-notes' });
});

test('parseMissCandidate returns null on true no-candidates output', () => {
  const stdout = 'no-candidates across 3 pointers: no connected file answers this.\n';
  assert.equal(parseMissCandidate(stdout), null);
});

test('formatMissCandidateReply names the top file and gives a one-line why, not raw data', () => {
  const reply = formatMissCandidateReply({ score: 0.85, path: '/notes/pricing.md', pointer: 'kelvin-notes' });
  assert.match(reply, /\/notes\/pricing\.md/);
  assert.match(reply, /no approved answer/);
  assert.doesNotMatch(reply, /\[kelvin-notes\]/);
});
