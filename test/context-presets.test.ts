// The context presets (Small, Medium, Large) through the Node loader. Python twin: skills/super-jev/tests/test_context_presets.py,
// which also runs this loader and checks it against the Python one on every preset. No judge call: only the loader and the table.
// Small is the profile as written; Medium and Large are `contexts` rows that override only the label, the window, the characters
// read per note and the content-check timeout. To run this file alone:
//   node --import ./test/clean-env.ts --test test/context-presets.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JUDGE_PROFILE, loadJudgeProfile } from '../src/judge-profile.ts';

const TABLE = new URL('../skills/super-jev/judge_profiles.json', import.meta.url);
const raw = () => JSON.parse(readFileSync(TABLE, 'utf8')) as { profiles: Record<string, any> };
/** The profile loaded at a preset (the loader's fourth parameter). */
const at = (name: string, context?: string) => loadJudgeProfile(name, undefined, {}, context);

// preset: [shown name, window, input cap, call budget, characters read per note, content-check timeout in ms]
const PRESETS: Record<string, [string, number, number, number, number, number]> = {
  small: ['Small', 32_768, 32_000, 30_000, 14_000, 15_000],
  medium: ['Medium', 131_072, 130_304, 128_304, 56_000, 20_000],
  large: ['Large', 262_144, 261_376, 259_376, 112_000, 30_000]
};
// what the number of characters read costs on the wire (title prefix, per-question overhead), measured locally
const WIRE = 1.45;
// the only things a contexts row may change; every other number a preset reports is the profile's own
const ROW_FIELDS = new Set(['context', 'contextLabel', 'windowTokens', 'readChars', 'navTimeoutMs', 'inputCapTokens', 'callTokens']);
// the preset keys of the Jev profile as the table states them; seeds every edited copy below, so a variant is wrong in exactly one way
const GOOD = {
  default_context: 'small', read_chars: 14_000, nav_timeout_ms: 15_000,
  long_context_header: ['TypeSafe-Danger-Zone-Long-Context', 'true'], long_context_above_tokens: 65_536,
  contexts: {
    medium: { label: 'Medium', window_tokens: 131_072, read_chars: 56_000, nav_timeout_ms: 20_000 },
    large: { label: 'Large', window_tokens: 262_144, read_chars: 112_000, nav_timeout_ms: 30_000 }
  }
};
/** A copy of the real table, the Jev profile given the GOOD preset keys and then `edit`; returns its path. */
function edited(edit: (p: any) => void): string {
  const data = raw();
  Object.assign(data.profiles['typesafe-jev'], structuredClone(GOOD));
  edit(data.profiles['typesafe-jev']);
  const file = join(mkdtempSync(join(tmpdir(), 'context-presets-')), 'profiles.json');
  writeFileSync(file, JSON.stringify(data));
  return file;
}
/** [profile, preset] for every preset of every profile that has any. */
const presets = () => Object.entries(raw().profiles).filter(([, p]) => p.contexts).flatMap(([key, p]) => ['small', ...Object.keys(p.contexts)].map(c => [key, c] as const));

test('small is the base profile', () => {
  const base = loadJudgeProfile('typesafe-jev');
  const small = at('typesafe-jev', 'small');
  assert.deepEqual(small, base);                                   // the same profile, nothing overridden
  assert.deepEqual([small.context, small.contextLabel], ['small', 'Small']);
  assert.deepEqual([small.windowTokens, small.inputCapTokens, small.callTokens], [32_768, 32_000, 30_000]);
  assert.deepEqual([small.readChars, small.navTimeoutMs], [14_000, 15_000]);
  assert.ok(!('small' in raw().profiles['typesafe-jev'].contexts));  // no duplicate row: small is the profile itself
  assert.equal(JUDGE_PROFILE.context, 'small');                    // what every import-time constant is derived from
});

for (const name of ['medium', 'large']) {
  test(`${name} derives window, input cap and call budget`, () => {
    const [label, window, cap, call, read, nav] = PRESETS[name]!;
    const base = loadJudgeProfile('typesafe-jev');
    const p = at('typesafe-jev', name);
    assert.deepEqual([p.context, p.contextLabel], [name, label]);
    assert.deepEqual([p.windowTokens, p.inputCapTokens, p.callTokens], [window, cap, call]);
    assert.deepEqual([p.readChars, p.navTimeoutMs], [read, nav]);
    assert.equal(p.inputCapTokens, p.windowTokens - p.inputCapHeadroom);          // the profile's own rules
    assert.equal(p.callTokens, p.inputCapTokens - p.callHeadroom);
    assert.equal(p.baseCallTokens, 30_000);                                       // the header rule measures against Small
    assert.equal(base.baseCallTokens, 30_000);
    for (const field of Object.keys(base)) {                                      // nothing else moves
      if (!ROW_FIELDS.has(field)) assert.deepEqual((p as any)[field], (base as any)[field], field);
    }
  });
}

test('the default context is small', () => {
  assert.equal(raw().profiles['typesafe-jev'].default_context, 'small');
  assert.equal(JUDGE_PROFILE.defaultContext, 'small');
  assert.equal(loadJudgeProfile().context, 'small');                // no preset named: the profile as written
  assert.equal(loadJudgeProfile('laya').defaultContext, 'small');   // a profile with no presets says nothing: small
});

test('a default other than small does not move the profile everything imports', () => {
  const p = loadJudgeProfile('typesafe-jev', edited(p => { p.default_context = 'large'; }));
  assert.deepEqual([p.defaultContext, p.context, p.windowTokens, p.readChars], ['large', 'small', 32_768, 14_000]);
  assert.equal(loadJudgeProfile('typesafe-jev', edited(() => {})).defaultContext, 'small');
});

test('an unknown context stops with one line naming the three', () => {
  for (const bad of ['xl', 'Small', 'LARGE', ' large', 'medium ', '', 'constructor']) {
    assert.throws(() => at('typesafe-jev', bad), (e: Error) => {
      assert.equal(e.message, `unknown context ${JSON.stringify(bad)} (choose small, medium or large)`);
      assert.ok(!e.message.includes('\n'));
      return true;
    }, JSON.stringify(bad));
  }
});

test('a profile without contexts accepts only small', () => {
  const laya = at('laya', 'small');
  assert.deepEqual(laya, loadJudgeProfile('laya'));
  assert.deepEqual([laya.context, laya.contextLabel, laya.windowTokens, laya.readChars], ['small', 'Small', 1024, 14_000]);
  assert.deepEqual([laya.longContextHeader, laya.longContextAboveTokens], [null, null]);
  for (const name of ['medium', 'large']) {
    assert.throws(() => at('laya', name), { message: `unknown context "${name}" (choose small)` });
  }
});

test('every preset read fits its call room', () => {
  assert.deepEqual(presets().filter(([key]) => key === 'typesafe-jev').map(([, c]) => c), ['small', 'medium', 'large']);
  assert.ok(!presets().some(([key]) => key === 'laya'));            // a profile with no presets has none to walk
  for (const [key, ctx] of presets()) {
    const p = at(key, ctx);
    assert.ok(p.readChars * WIRE <= p.callTokens * p.bytesPerToken, `${key}/${ctx}: what is read goes out in one call`);
    assert.ok(p.readChars <= p.fileCeilingBytes, `${key}/${ctx}: never more than a note can be`);
  }
});

test('the header name and value live only in the profile', () => {
  const [name, value] = at('typesafe-jev', 'large').longContextHeader!;
  assert.deepEqual([name, value], ['TypeSafe-Danger-Zone-Long-Context', 'true']);
  assert.deepEqual(raw().profiles['typesafe-jev'].long_context_header, [name, value]);
  for (const preset of Object.keys(PRESETS)) {                       // the rule is the profile's, not the preset's
    const p = at('typesafe-jev', preset);
    assert.deepEqual([p.longContextHeader, p.longContextAboveTokens], [[name, value], 65_536], preset);
  }
  const files = ['src', 'bench'].flatMap(dir => readdirSync(new URL(`../${dir}/`, import.meta.url), { recursive: true, withFileTypes: true })
    .filter(e => e.isFile() && e.name.endsWith('.ts')).map(e => join(e.parentPath, e.name)));
  assert.ok(files.length > 30, `${files.length} files`);
  assert.deepEqual(files.filter(f => readFileSync(f, 'utf8').includes(name)), []);
});

const BROKEN: Record<string, (p: any) => void> = {
  'a small row': p => { p.contexts.small = { read_chars: 1 }; },
  'a row key a row may not override': p => { p.contexts.medium.max_parts = 5; },
  'a default that names no preset': p => { p.default_context = 'huge'; },
  'a header that is not a name and a value': p => { p.long_context_header = ['only-one']; },
  'a header with no threshold': p => { delete p.long_context_above_tokens; },
  'a row number that is not a number': p => { p.contexts.large.read_chars = 'lots'; },
  'no read_chars': p => { delete p.read_chars; },
  'no nav_timeout_ms': p => { delete p.nav_timeout_ms; }
};
for (const [what, edit] of Object.entries(BROKEN)) {
  test(`a broken presets table stops with one line: ${what}`, () => {
    assert.throws(() => loadJudgeProfile('typesafe-jev', edited(edit)), (e: Error) => {
      assert.match(e.message, /judge limits table/);
      assert.ok(!e.message.trim().includes('\n'));
      return true;
    });
  });
}
