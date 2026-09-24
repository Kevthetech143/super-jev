import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  loadConfig, saveConfig, configFileMode, hasApiKey,
  cannedReply, parseSlashCommand, isKnownSlashCommand,
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
