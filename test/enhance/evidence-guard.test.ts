import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { isBlockedPath, redactCounted, blockedPathFact, GuardTally, SKIPPED_FACT } from '../../src/enhance/evidence-guard.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = join(HERE, 'fixtures', 'evidence-guard-cases.json');
const fixture = JSON.parse(readFileSync(FIXTURE_PATH, 'utf8'));

// -------------------------------------------------------------- blocked paths, from the shared fixture (kept in sync with Python via test_evidence_guard.py)

for (const c of fixture.blockedPaths) {
  test(`isBlockedPath: ${c.path} -> ${c.blocked} (${c.why})`, () => {
    assert.equal(isBlockedPath(c.path), c.blocked);
  });
}

for (const c of fixture.allowOverride) {
  test(`isBlockedPath allow-override: ${c.path} with ${JSON.stringify(c.allow)} -> ${c.blocked} (${c.why})`, () => {
    assert.equal(isBlockedPath(c.path, c.allow), c.blocked);
  });
}

// ------------------------------------------------------------------ redactor

for (const c of fixture.redactions) {
  test(`redact: ${JSON.stringify(c.text)} (${c.why ?? ''})`, () => {
    const r = redactCounted(c.text);
    assert.equal(r.text, c.expect);
    assert.equal(r.count, c.count);
  });
}

for (const c of fixture.redactionsWithEmails) {
  test(`redact with redactEmails: ${JSON.stringify(c.text)}`, () => {
    const r = redactCounted(c.text, true);
    assert.equal(r.text, c.expect);
    assert.equal(r.count, c.count);
  });
}

// --------------------------------------------------------------- blockedPathFact

test('blockedPathFact: ok is false and exists is null, never guessed missing', () => {
  const fact = blockedPathFact('/Users/admin/agents/global/profile/logins.md');
  assert.equal(fact.ok, false);
  assert.equal(fact.exists, null);
  assert.equal(fact.blocked, true);
  assert.equal(fact.cmd, `(${SKIPPED_FACT}: /Users/admin/agents/global/profile/logins.md)`);
});

// -------------------------------------------------------------------- tally

test('GuardTally: counts paths skipped and redactions made across a call', () => {
  const tally = new GuardTally();
  tally.checkPath('/Users/admin/agents/global/profile/logins.md');
  tally.checkPath('/Users/admin/super-jev/README.md');
  tally.redact('key is sk-abcdefghijklmnopqrstuvwxyz123456 in the log');
  tally.redact('token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789');
  assert.equal(tally.pathsSkipped, 1);
  assert.equal(tally.redactions, 2);
  assert.equal(tally.summary(), 'guard: 1 path(s) skipped, 2 redaction(s)');
});
