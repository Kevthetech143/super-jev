// Frozen contract tests (2026-10-01), PR 3: the terminal app shows exactly what the helpers report.
//
// Layers (no Jev, no network):
//   pure        render, readLine, helperCall, childEnv, keyAction, pointerName on fixtures
//   whole app   run() with fake terminal streams, or the one-shot door as a child process, with a fake
//               python3 first on PATH. The fake records each call's argv and whether a key was set
//               (a short digest of it, never the key) and prints canned JSON with a canned exit code.
//   real engine setup.py, prepare_bulk --writer builtin and ask.py in a temp state folder. These need
//               ask.py --json (PR 278), and FAIL, never skip, when a tree does not have it.
// Data is made up: company Quillbrook, user sam, principal me.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { PassThrough } from 'node:stream';
import { createHash } from 'node:crypto';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stripVTControlCharacters } from 'node:util';
import * as cfg from '../src/jev-chat-config.ts';

const cli: any = await import('../src/jev-chat-cli.ts').catch(() => ({}));

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CLI = join(ROOT, 'src', 'jev-chat-cli.ts');
const SKILL = join(ROOT, 'skills', 'super-jev');
const NODE_BIN = dirname(process.execPath);
const VERSION = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8')).version;
const KEY = 'quillbrook-test-key-0001';
const FILE_KEY = 'quillbrook-file-key-0002';
const sha = (s: string) => createHash('sha1').update(s).digest('hex').slice(0, 8);
const HANDBOOK_QUOTE = 'The canary holds at 5 percent of traffic for 40 minutes before it is promoted.';

// ---------------------------------------------------------------- global invariant
const BANNED = ['python3 ', '--principal', 'prepare_bulk', 'Want me to find it by hand', KEY, FILE_KEY];
function clean(s: string, allow: string[] = []): string {
  for (const b of BANNED) if (!allow.includes(b)) assert.ok(!s.includes(b), `output holds ${JSON.stringify(b)}:\n${s}`);
  return s;
}
const strip = (s: string) => s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').replace(/\r/g, '');
const flat = (s: string) => s.replace(/\s+/g, ' ');

// ---------------------------------------------------------------- fake python3 and rig
const FAKE = `#!/usr/bin/env node
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const dir = process.env.FAKE_DIR, args = process.argv.slice(2);
const key = process.env.TYPESAFE_API_KEY || '';
const log = path.join(dir, 'calls.jsonl');
const prior = fs.existsSync(log) ? fs.readFileSync(log, 'utf8').split('\\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
const line = [path.basename(args[0] || ''), ...args.slice(1)].join(' ');
const rules = JSON.parse(fs.readFileSync(path.join(dir, 'replies.json'), 'utf8'));
const at = rules.findIndex((r) => line.includes(r.when));
fs.appendFileSync(log, JSON.stringify({ argv: args, rule: at, key_set: key !== '',
  key_sha: key ? crypto.createHash('sha1').update(key).digest('hex').slice(0, 8) : '' }) + '\\n');
if (at < 0) process.exit(99);
const n = prior.filter((c) => c.rule === at).length;
const r = rules[at].replies ? rules[at].replies[Math.min(n, rules[at].replies.length - 1)] : rules[at];
setTimeout(() => {
  if (r.err) process.stderr.write(r.err);
  if (r.out !== undefined) process.stdout.write(typeof r.out === 'string' ? r.out : JSON.stringify(r.out) + '\\n');
  process.exit(r.code || 0);
}, r.sleep || 0);
`;

const made: string[] = [];
const cleanups: (() => void)[] = [];
process.on('exit', () => { for (const f of cleanups) f(); for (const d of made) rmSync(d, { recursive: true, force: true }); });

type Rule = { when: string; out?: unknown; code?: number; err?: string; sleep?: number; replies?: Rule[] };
function rig(rules: Rule[] = []) {
  const dir = mkdtempSync(join(tmpdir(), 'jev-app-'));
  made.push(dir);
  const home = join(dir, 'home'), bin = join(dir, 'bin'), notes = join(dir, 'notes');
  for (const d of [home, bin, notes]) mkdirSync(d);
  writeFileSync(join(bin, 'python3'), FAKE);
  chmodSync(join(bin, 'python3'), 0o755);
  const r = {
    dir, home, notes,
    env: { PATH: `${bin}:${NODE_BIN}:/usr/bin:/bin`, HOME: home, FAKE_DIR: dir } as Record<string, string>,
    rules(next: Rule[]) { writeFileSync(join(dir, 'replies.json'), JSON.stringify(next)); },
    calls() {
      const f = join(dir, 'calls.jsonl');
      if (!existsSync(f)) return [];
      return readFileSync(f, 'utf8').split('\n').filter(Boolean).map((l) => {
        const c = JSON.parse(l);
        return { ...c, script: basename(c.argv[0] || ''), args: c.argv.slice(1) as string[] };
      });
    },
    asks() { return r.calls().filter((c: any) => c.script === 'ask.py' && c.args.includes('--')); },
    keyFile() { return join(home, '.typesafe-api-key'); },
    folder(name: string) { const d = join(notes, name); mkdirSync(d, { recursive: true }); return d; },
  };
  r.rules(rules);
  return r;
}
type Rig = ReturnType<typeof rig>;

/** One-shot door as a child process: superjev <args>. The invariant is checked on the way out. */
async function door(r: Rig, args: string[], o: { key?: boolean; env?: Record<string, string>; allow?: string[] } = {}) {
  const env = { ...r.env, ...(o.key === false ? {} : { TYPESAFE_API_KEY: KEY }), ...o.env };
  const res = await new Promise<{ code: number; out: string; err: string }>((done) => {
    const p = spawn(process.execPath, [CLI, ...args], { env, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '', err = '';
    const kill = setTimeout(() => p.kill('SIGKILL'), 20000);
    p.stdout.on('data', (d) => (out += d)); p.stderr.on('data', (d) => (err += d));
    p.on('close', (code) => { clearTimeout(kill); done({ code: code ?? -1, out, err }); });
  });
  clean(res.out + res.err, o.allow);
  return res;
}

const tty = () => Object.assign(new PassThrough(), { isTTY: true, columns: 60, setRawMode() { return this; } });

/** The window: run() with fake terminal streams. */
function win(r: Rig, o: { key?: boolean; env?: Record<string, string>; argv?: string[] } = {}) {
  let out = '', err = '';
  const rawLog: { on: boolean; screen: string }[] = []; // each raw-mode switch, with what was on screen at that moment
  const stdin = Object.assign(tty(), { setRawMode(on: boolean) { rawLog.push({ on, screen: out }); return this; } });
  const stdout = tty(), stderr = tty();
  stdout.on('data', (d) => (out += d)); stderr.on('data', (d) => (err += d));
  const env = { ...r.env, ...(o.key === false ? {} : { TYPESAFE_API_KEY: KEY }), ...o.env };
  const done: Promise<number> = cli.run({ argv: o.argv ?? [], env, stdin, stdout, stderr });
  const w = {
    done, rawLog, raw: () => out, text: () => strip(out), err: () => strip(err),
    send: (s: string) => stdin.write(s),
    say: (line: string) => stdin.write(line + '\r'),
    async waitFor(what: string | RegExp | (() => boolean), ms = 8000) {
      const ok = () => (typeof what === 'function' ? what() : typeof what === 'string' ? w.text().includes(what) : what.test(w.text()));
      const end = Date.now() + ms;
      while (!ok()) {
        if (Date.now() > end) throw new Error(`timed out waiting for ${String(what)}; screen:\n${w.text()}\nstderr:\n${w.err()}`);
        await new Promise((x) => setTimeout(x, 15));
      }
    },
    async exit(ms = 8000): Promise<number> {
      const t = new Promise<number>((_, rej) => setTimeout(() => rej(new Error('run() did not finish; screen:\n' + w.text())), ms));
      return Promise.race([done, t]);
    },
    async quit() { w.send('\x04'); return w.exit(); },
    ready: () => w.waitFor(/Ready\.|\? for help/),
    all: () => clean(w.text() + w.err()),
  };
  return w;
}

const STATUS_EMPTY: Rule = { when: '--status', out: { v: 1, next: 'connect', principal: 'me', sets: [] } };
const SET = (name: string, root: string, notes: number, state = 'ready') => ({ name, roots: [root], notes, state });
const FOUND = (extra: object = {}) => ({
  v: 1, outcome: 'found', why: '1 file', next: 'none',
  files: [{ path: '/Users/sam/Team Notes/eng/handbook.md', tier: 'confirmed', line: 39, text: HANDBOOK_QUOTE }], ...extra,
});
const session = { principal: 'me', skillDir: SKILL, connected: new Set<string>() };

// ---------------------------------------------------------------- pure layer: render
const OPTS = { width: 60, color: false, home: '/Users/sam', keyEnv: 'TYPESAFE_API_KEY', keySource: 'env' };
const R = (kind: string, data: object, extra: object = {}, o: object = {}) =>
  clean(cfg.render({ kind, data: { v: 1, ...data }, secs: 1.4, ...extra }, { ...OPTS, ...o }));
const HB = '/Users/sam/Team Notes/eng/handbook.md';
const RC = '/Users/sam/Team Notes/eng/release-checklist.md';
const RB = '/Users/sam/Team Notes/runbooks/rollback.md';
const lines = (s: string) => s.split('\n');

test('S1 found: files in rank order with ~ and :line, possible on #3, quote under #1 only, no score digits', () => {
  const s = R('ask', FOUND({ files: [
    { path: HB, tier: 'confirmed', line: 39, text: HANDBOOK_QUOTE },
    { path: RC, tier: 'confirmed', line: 12, text: 'Run the smoke test before promotion.' },
    { path: RB, tier: 'possible' }] }));
  const at = (x: string) => s.indexOf(x);
  assert.ok(at('~/Team Notes/eng/handbook.md:39') > -1, s);
  assert.ok(at('~/Team Notes/eng/handbook.md') < at('~/Team Notes/eng/release-checklist.md:12'));
  assert.ok(at('release-checklist.md') < at('runbooks/rollback.md'));
  assert.match(s, /rollback\.md\s+possible/);
  assert.equal((s.match(/possible/g) || []).length, 1, 'only #3 is tagged');
  assert.ok(flat(s).includes(`"${HANDBOOK_QUOTE}"`), 'the quote sits under file 1');
  assert.ok(!s.includes('smoke test'), 'no quote under file 2');
  assert.ok(!/\d\.\d\d/.test(s), 'no score digits');
  assert.match(s, /^• Found 3 notes · 1\.4s$/m);
});

test('S2 saved answer by auto: says Saved answer and lists the files', () => {
  const s = R('ask', FOUND({ saved: { by: 'auto', date: '2026-09-12' }, files: [{ path: HB, tier: 'confirmed' }, { path: RC, tier: 'confirmed' }] }));
  assert.match(s, /^• Saved answer/m);
  assert.ok(s.includes('~/Team Notes/eng/handbook.md') && s.includes('~/Team Notes/eng/release-checklist.md'));
  assert.ok(!/Found \d/.test(s));
});

test('S3 saved answer by you: shows the answer text and the file', () => {
  const s = R('ask', FOUND({ saved: { by: 'you', date: '2026-09-12', answer: 'Forty minutes at 5 percent.' }, files: [{ path: HB, tier: 'confirmed' }] }));
  assert.match(s, /^• Saved answer/m);
  assert.ok(s.includes('Forty minutes at 5 percent.'));
  assert.ok(s.includes('~/Team Notes/eng/handbook.md'));
});

test('S3b a saved answer keeps its own line breaks (a list stays a list); each line still wraps to the window', () => {
  const s = R('ask', FOUND({ saved: { by: 'you', date: '2026-09-12', answer: 'Steps:\n1. hold at 5 percent\n2. wait forty minutes, then promote it to everyone unless a rollback ticket is open' }, files: [{ path: HB, tier: 'confirmed' }] }), {}, { width: 60 });
  const lines = s.split('\n');
  assert.ok(lines.includes('  Steps:') && lines.includes('  1. hold at 5 percent'), s);
  assert.ok(lines.some((l) => l.startsWith('  2. wait forty minutes')), s);
  assert.ok(!lines.some((l) => l.includes('1. hold') && l.includes('2. wait')), 'the list was joined into one paragraph');
  assert.ok(lines.every((l) => l.length <= 60), s);
});

test('S4 claim TRUE: verdict, path:line · date, and the quote', () => {
  const s = R('check', { outcome: 'found', why: 'ok', next: 'none', claim: { verdict: 'TRUE', read: 4,
    proof: { path: HB, line: 39, text: HANDBOOK_QUOTE, date: '2026-09-12' }, files: [{ path: HB, says: 'TRUE', line: 39, date: '2026-09-12' }] } });
  assert.match(s, /^• TRUE · 1\.4s$/m);
  assert.ok(s.includes('~/Team Notes/eng/handbook.md:39 · 2026-09-12'));
  assert.ok(flat(s).includes(`"${HANDBOOK_QUOTE}"`));
});

test('S5 claim FALSE: shows FALSE and the proof line, never framed as an error', () => {
  const s = R('check', { outcome: 'found', why: 'ok', next: 'none', claim: { verdict: 'FALSE', read: 4,
    proof: { path: '/Users/sam/Team Notes/policies/expenses.md', line: 4, text: 'Hotels: up to $220 per night.', date: '2026-08-30' }, files: [] } });
  assert.match(s, /^• FALSE/m);
  assert.ok(s.includes('policies/expenses.md:4 · 2026-08-30') && s.includes('Hotels: up to $220 per night.'));
  assert.ok(!/error|couldn't|hit an/i.test(s), s);
});

test('S6 claim CONFLICT: both files, newest first, each with its own verdict', () => {
  const s = R('check', { outcome: 'found', why: 'ok', next: 'none', claim: { verdict: 'CONFLICT', proof: null, read: 2, files: [
    { path: '/Users/sam/Team Notes/policies/expenses.md', says: 'FALSE', line: 4, date: '2026-09-20' },
    { path: '/Users/sam/Team Notes/policies/travel-old.md', says: 'TRUE', line: 9, date: '2026-08-01' }] } });
  assert.match(s, /^• CONFLICT/m);
  const a = s.indexOf('expenses.md:4'), b = s.indexOf('travel-old.md:9');
  assert.ok(a > -1 && b > a, 'newest first');
  assert.match(s, /Read both before relying on either\./);
  // Each file is one row: path, date and its own verdict on the same line (a window wide enough for it; W3 sweeps the narrow ones).
  const wide = lines(R('check', { outcome: 'found', why: 'ok', next: 'none', claim: { verdict: 'CONFLICT', proof: null, read: 2, files: [
    { path: '/Users/sam/Team Notes/policies/expenses.md', says: 'FALSE', line: 4, date: '2026-09-20' },
    { path: '/Users/sam/Team Notes/policies/travel-old.md', says: 'TRUE', line: 9, date: '2026-08-01' }] } }, {}, { width: 80 }));
  assert.ok(wide.includes('  ~/Team Notes/policies/expenses.md:4 · 2026-09-20 · says FALSE'), wide.join('\n'));
  assert.ok(wide.includes('  ~/Team Notes/policies/travel-old.md:9 · 2026-08-01 · says TRUE'), wide.join('\n'));
});

test('S7 skills first, then the guess tag, then the file', () => {
  const s = R('ask', FOUND({ skills: [
    { name: 'summarize-meeting', path: '/Users/sam/skills/summarize-meeting/SKILL.md', guess: false },
    { name: 'draft-email', path: '/Users/sam/skills/draft-email/SKILL.md', guess: true }] }));
  const a = s.indexOf('summarize-meeting'), b = s.indexOf('draft-email'), g = s.indexOf('guess'), f = s.indexOf('handbook.md');
  assert.ok(a > -1 && a < b && b < g && g < f, s);
  assert.equal((s.match(/guess/g) || []).length, 1);
});

test('S14 status leads each row with the folder, then note count and state, never the internal name', () => {
  const s = R('status', { next: 'none', principal: 'me', sets: [
    SET('team-notes-3fa9c1', '/Users/sam/Team Notes', 28), SET('handbook-ab12cd', '/Users/sam/Handbook', 12, 'refreshing'),
    { name: 'loose-000000', notes: 1, state: 'ready' }] });
  assert.match(s, /^ {2}~\/Team Notes {2}28 notes {2}ready$/m);
  assert.match(s, /^ {2}~\/Handbook {2}12 notes {2}refreshing$/m);
  assert.match(s, /^ {2}loose-000000 {2}1 note {2}ready$/m, 'a set with no folder falls back to its name');
  assert.ok(!s.includes('team-notes-3fa9c1') && !s.includes('handbook-ab12cd'), s);
});

test('S14b two sets with the same folder are told apart by their name; a folder shown once never shows one', () => {
  const s = R('status', { next: 'none', principal: 'me', sets: [
    SET('plan-draft-v2-ae3e74', '/Users/sam/Team Notes', 1), SET('team-notes-b5391a', '/Users/sam/Team Notes', 3), SET('handbook-ab12cd', '/Users/sam/Handbook', 12)] });
  assert.match(s, /^ {2}~\/Team Notes {2}1 note {2}ready {2}plan-draft-v2-ae3e74$/m);
  assert.match(s, /^ {2}~\/Team Notes {2}3 notes {2}ready {2}team-notes-b5391a$/m);
  assert.match(s, /^ {2}~\/Handbook {2}12 notes {2}ready$/m);
});

test('S15 help lists exactly the commands that exist, and ? is the same help', () => {
  const s = clean(cfg.render({ kind: 'help', data: {} }, OPTS));
  assert.deepEqual([...new Set(s.match(/\/[a-z]+/g))].sort(), ['/check', '/exit', '/help', '/status']);
  assert.match(s, /\?/);
  assert.equal(cfg.readLine('?').kind, 'help');
  assert.equal(cfg.readLine('/help').kind, 'help');
  for (const c of ['/check x', '/status', '/help', '/exit']) assert.notEqual(cfg.readLine(c).kind, 'say', c);
});

test('S15b help rows line up: every description starts in the same column, and a row fits 60 columns', () => {
  const rows = cfg.HELP.split('\n').slice(1);
  assert.equal(rows.length, 5, 'the drag row and the four commands');
  const col = rows.map((l) => { const m = /^( {2}.+?\S)( {2,})(\S.*)$/.exec(l); assert.ok(m, `no description column in ${JSON.stringify(l)}`); return m![1].length + m![2].length; });
  assert.equal(new Set(col).size, 1, `descriptions start in columns ${col.join(', ')}:\n${cfg.HELP}`);
  for (const l of rows) assert.ok(l.length <= 60, l);
});

test('S16 saved_now: says Saved for next time.', () => {
  assert.match(R('ask', FOUND({ saved_now: true })), /Saved for next time\./);
  assert.ok(!R('ask', FOUND()).includes('Saved for next time.'));
});

test('A1 not found, one set searched: Not in your notes, does not prove it, one Next line, no voice line', () => {
  const s = R('ask', { outcome: 'not-found', why: 'searched 1 set, no matching file (it may still exist)', next: 'connect',
    searched: { sets: 1, notes: 28 }, voice: "Super Jev: I didn't have this. Want me to find it by hand and save it for next time?" });
  assert.match(s, /^• Not in your notes/m);
  assert.ok(flat(s).includes("doesn't prove it's nowhere"), s);
  assert.equal((s.match(/Next:/g) || []).length, 1);
  assert.match(s, /Next: .*drag/i);
});

test('A2 claim NOT FOUND: names how many notes were read, never says FALSE', () => {
  const s = R('check', { outcome: 'not-found', why: 'none', next: 'none', claim: { verdict: 'NOT FOUND', proof: null, read: 4, files: [] } });
  assert.match(s, /NOT FOUND in the 4 notes read/);
  assert.ok(!s.includes('FALSE'));
});

test('A3 leans none: the files plus "may not be here"', () => {
  const s = R('ask', FOUND({ leans_none: true }));
  assert.ok(s.includes('handbook.md') && flat(s).includes('may not be here'));
});

test('A4 found with a healing set: the files plus "not searched (refreshing)", never "not found"', () => {
  const s = R('ask', FOUND({ unsearched: [{ set: 'handbook-ab12cd', root: '/Users/sam/Handbook', state: 'stale', healing: true }] }));
  assert.match(s, /Found 1 note · Handbook not searched \(refreshing\) · 1\.4s/);
  assert.ok(!/not found/i.test(s));
});

test('E4 found with errors on 1 of 2 sets: the files plus "not searched", never "didn\'t have this"', () => {
  const s = R('ask', FOUND({ errors: [{ set: 'handbook-ab12cd', kind: 'unknown' }],
    unsearched: [{ set: 'handbook-ab12cd', root: '/Users/sam/Handbook', state: 'failed', healing: false }] }));
  assert.ok(s.includes('handbook.md'));
  assert.match(s, /Handbook not searched \(failed\)/);
  assert.ok(!/didn't have this|not in your notes/i.test(s));
});

test('I1 needs connect: the drag hint, no command text', () => {
  const s = R('ask', { outcome: 'needs-setup', why: 'nothing is connected for me', next: 'connect' });
  assert.match(s, /^• Nothing connected yet/m);
  assert.match(s, /Next: drag a folder of Markdown notes in here\./i);
});

test('I2 needs refresh while healing: refreshing now, Ask again', () => {
  const s = R('ask', { outcome: 'needs-setup', why: 'no match, but the search was incomplete', next: 'refresh',
    unsearched: [{ set: 'team-notes-3fa9c1', root: '/Users/sam/Team Notes', state: 'stale', healing: true }] });
  assert.match(s, /refreshing now/);
  assert.match(s, /Ask again/);
});

test('I3 needs include: ask.py\'s left-out rows have no kind, so they show the engine\'s own words and way in', () => {
  const s = R('ask', { outcome: 'needs-setup', why: 'no match, but the search was incomplete: 2 files skipped at setup', next: 'include',
    left_out: [{ what: 'file(s) in folders skipped by default: documents (2)', count: 2, where: '/Users/sam/Team Notes/documents', way_in: 'drag documents/ in on its own' }] });
  assert.ok(s.includes('documents'));
  assert.ok(s.includes('drag documents/ in on its own'));
});

test('I4 connect with a held note and skipped files: Connected, a held line, left-out lines worded from kind, not a failure', () => {
  const skip = { kind: 'folder', what: '.md file(s) in folders skipped by default: documents/ (2), profile/ (1)', count: 3,
    way_in: 'to connect one, connect that folder as its own set (--root FOLDER --pointer NEW-NAME)' };
  const other = { kind: 'types', what: 'file(s) of other types (.py 1, .csv 1)', count: 2, way_in: 'only .md files connect' };
  const link = { kind: 'link', what: 'linked file(s) point outside every --root', count: 1, way_in: 'add --allow-target FOLDER to admit them' };
  const s = R('connect', { connected: 25, held: [{ path: 'ops/creds.md', why: 'looks like it holds a secret' }], skipped: [skip, other, other, link] },
    { label: 'Team Notes' });
  assert.match(s, /^• Connected Team Notes: 25 notes · 1\.4s$/m);
  assert.match(s, /^ {2}Held back 1 note: looks like it holds a secret\n {4}ops\/creds\.md$/m);
  assert.match(flat(s), /Left out 3 notes in folders skipped by default; drag a folder in on its own to connect it\./);
  assert.match(flat(s), /Left out 2 files of other types; only \.md notes connect\./);
  assert.match(flat(s), /Left out 1 linked note pointing outside this folder\./);
  assert.equal((s.match(/only \.md notes connect/g) || []).length, 1, 'a repeated left-out row is shown once');
  assert.ok(!/--|\(s\)|FOLDER|NEW-NAME/.test(s), 'no command-line words and no "file(s)" from the engine text:\n' + s);
  assert.ok(!/Not connected|failed/i.test(s), s);
});

test('I4b every skipped kind the engine documents has its own wording; a kind the app does not know shows the engine\'s words', () => {
  const kinds = ['link', 'name', 'folder', 'folder_other', 'hidden', 'dataset', 'test', 'worktree', 'empty', 'types'];
  const seen = new Set<string>();
  for (const kind of kinds) {
    const s = R('connect', { connected: 5, skipped: [{ kind, what: 'ENGINE TEXT (--root X)', count: 2, way_in: 'ENGINE WAY (--name Y)' }] }, { label: 'Team Notes' }, { width: 200 });
    const row = lines(s).filter((l) => /^ {2}Left out/.test(l)).join(' ');
    assert.match(row, /^ {2}Left out 2 .+\.$/, kind);
    assert.ok(!/ENGINE|--/.test(s), `${kind} is worded from its kind:\n${s}`);
    seen.add(row);
  }
  assert.equal(seen.size, kinds.length, 'each kind has its own wording');
  const odd = R('connect', { connected: 5, skipped: [{ kind: 'future_kind', what: 'file(s) of a new sort', count: 4, way_in: 'rename them' }] }, { label: 'Team Notes' });
  assert.ok(flat(odd).includes('Left out 4 file(s) of a new sort; rename them.'), odd);
});

test('I5 connect refused over the cap: Not connected, the numbers from the engine, drag in a smaller folder, no command-line words', () => {
  const why = '1230 files exceed --max-files 250; narrow --root/--exclude/--no-recurse or raise --max-files';
  const s = R('connect', { connected: 0, refused: { kind: 'too_many', why, count: 1230, max: 250 } }, { label: 'Everything' });
  assert.match(s, /^• Not connected · 1\.4s$/m, 'the headline stays short');
  assert.match(s, /^ {2}That folder has 1,230 notes; one connect takes up to 250\.$/m);
  assert.match(s, /Next: drag in a smaller folder inside it\./);
  assert.ok(!/--|exceed/.test(s), s);
});

test('I5b every refusal kind has its own wording; a kind the app does not know shows the engine\'s reason', () => {
  const said = new Set<string>();
  for (const kind of ['not_a_folder', 'not_markdown', 'usage']) {
    const s = R('connect', { connected: 0, refused: { kind, why: 'ENGINE WHY (--root X)' } }, { label: 'Notes' });
    assert.match(s, /^• Not connected/m, kind);
    assert.ok(!/ENGINE|--/.test(s), `${kind} is worded from its kind:\n${s}`);
    said.add(lines(s)[1]);
  }
  assert.equal(said.size, 3);
  const odd = R('connect', { connected: 0, refused: { kind: 'future_kind', why: 'the folder is on a slow disk' } }, { label: 'Notes' });
  assert.match(odd, /^• Not connected/m);
  assert.ok(odd.includes('the folder is on a slow disk'), odd);
});

test('I6 not supported: the engine reason plus Ask one focused question.', () => {
  const s = R('ask', { outcome: 'not-supported', why: 'the question has several parts', next: 'rephrase' });
  assert.ok(s.includes('the question has several parts'));
  assert.match(s, /ask one focused question\./i);
});

test('E2 AuthRejected with an env key: says fix it in the shell and names the variable', () => {
  const s = R('ask', { outcome: 'error', why: 'no match, and 1 set failed', next: 'key', errors: [{ set: 'team-notes-3fa9c1', kind: 'auth-rejected' }] });
  assert.match(s, /rejected the key/);
  assert.match(s, /fix .*TYPESAFE_API_KEY.* in your shell/i);
});

test('E2b AuthRejected with a file key: names the key file instead', () => {
  const s = R('ask', { outcome: 'error', why: 'x', next: 'key', errors: [{ set: 'a', kind: 'auth-rejected' }] }, {}, { keySource: 'file' });
  assert.match(s, /rejected the key/);
  assert.ok(s.includes('~/.typesafe-api-key'));
  assert.ok(!s.includes('in your shell'));
});

test('E3 Unreachable: can\'t be reached, Saved answers still work', () => {
  const s = R('ask', { outcome: 'error', why: 'no match, and 1 set failed', next: 'none', errors: [{ set: 'a', kind: 'unreachable' }] });
  assert.match(s, /can't be reached/);
  assert.match(s, /Saved answers still work/);
});

test('E5 a crash screen: Super Jev hit an error, its last line, a next step', () => {
  const s = R('crash', { line: 'ValueError: bad registry' });
  assert.match(s, /^• Super Jev hit an error/m);
  assert.ok(s.includes('ValueError: bad registry'));
  assert.match(s, /Next: /);
});

test('E8 connect failed: Not connected plus the reasons', () => {
  const s = R('connect', { connected: 0, failed: [{ path: '/Users/sam/Team Notes', why: 'no .md file left to connect' }] }, { label: 'Team Notes' });
  assert.match(s, /^• Not connected/m);
  assert.match(s, /^ {2}Failed: no \.md file left to connect\n {4}~\/Team Notes$/m);
});

// ---------------------------------------------------------------- review round 1: the helper's outcome decides the screen
const SET_LOST = { errors: [{ set: 'team-notes-3fa9c1', kind: 'unknown' }],
  unsearched: [{ set: 'team-notes-3fa9c1', root: '/Users/sam/Team Notes', state: 'failed', healing: false }] };
const NOT_FOUND_CLAIM = { verdict: 'NOT FOUND', proof: null, read: 1, files: [] };

test('H1 a claim whose search failed is never NOT FOUND: it names the folder that was not searched', () => {
  const s = R('check', { outcome: 'error', why: 'no match, and 1 set failed', next: 'none', claim: NOT_FOUND_CLAIM, ...SET_LOST });
  assert.ok(!/NOT FOUND/.test(s), s);
  assert.ok(s.includes('Team Notes not searched (failed)'), s);
});

test('H1b a claim on a folder that is refreshing says so, and is never NOT FOUND', () => {
  const s = R('check', { outcome: 'needs-setup', why: 'no match, but the search was incomplete', next: 'refresh', claim: NOT_FOUND_CLAIM,
    unsearched: [{ set: 'team-notes-3fa9c1', root: '/Users/sam/Team Notes', state: 'stale', healing: true }] });
  assert.ok(!/NOT FOUND/.test(s), s);
  assert.match(s, /Team Notes changed; it is refreshing now/);
  assert.match(s, /Ask again/);
});

test('H1c a claim the judge rejected or could not reach says that, and is never NOT FOUND', () => {
  const key = R('check', { outcome: 'error', why: 'x', next: 'key', claim: NOT_FOUND_CLAIM, errors: [{ set: 'a', kind: 'auth-rejected' }] });
  assert.match(key, /rejected the key/);
  assert.match(key, /fix .*TYPESAFE_API_KEY.* in your shell/i);
  const down = R('check', { outcome: 'error', why: 'x', next: 'none', claim: NOT_FOUND_CLAIM, errors: [{ set: 'a', kind: 'unreachable' }] });
  assert.match(down, /can't be reached/);
  for (const s of [key, down]) assert.ok(!/NOT FOUND/.test(s), s);
});

test('H1d a TRUE claim still names a folder that was not searched, on the same line as the verdict', () => {
  const s = R('check', { outcome: 'found', why: 'ok', next: 'none', ...SET_LOST,
    claim: { verdict: 'TRUE', read: 3, proof: { path: HB, line: 39, text: HANDBOOK_QUOTE }, files: [] } });
  assert.match(s, /^• TRUE · Team Notes not searched \(failed\)/m);
});

test('H1e a status the helper could not read says why: never "Nothing connected" or "Ready", and no Next', () => {
  const why = 'Connection status unavailable. Next: check the memory connector configuration.';
  const s = R('status', { outcome: 'error', why, next: 'none' }, { secs: undefined });
  assert.ok(flat(s).includes(why), s);
  assert.ok(!/Nothing connected|Ready|ask again/.test(s), s);
  assert.equal((s.match(/Next:/g) || []).length, 1, 'only the engine\'s own words, no second Next line');
});

test('H1f a status the helper refused shows its reason and no "ask one focused question"', () => {
  const s = R('status', { outcome: 'not-supported', why: 'that principal name is not valid: use letters and digits', next: 'rephrase' });
  assert.ok(s.includes('that principal name is not valid'), s);
  assert.ok(!/Nothing connected|focused question|Next:/.test(s), s);
});

test('H1g a crash after a status turn has no Next that points back at the status', () => {
  const s = R('crash', { line: 'ValueError: bad registry' }, { noNext: true });
  assert.ok(s.includes('ValueError: bad registry'));
  assert.ok(!/Next:/.test(s), s);
});

test('M2 skills_off: one line says no skill catalog was searched, before the Next line', () => {
  const off = 'no skill catalog was searched: skill search is off (SUPERJEV_SKILLS=0)';
  const found = R('ask', FOUND({ skills_off: off }));
  const none = R('ask', { outcome: 'not-found', why: 'x', next: 'connect', searched: { sets: 1, notes: 3 }, skills_off: off });
  for (const s of [found, none]) {
    assert.equal((s.match(/skill catalog/g) || []).length, 1, s);
    assert.ok(s.includes('SUPERJEV_SKILLS=0'), s);
    assert.ok(flat(s).includes('No skill catalog was searched'), 'the line starts with a capital');
  }
  assert.ok(none.indexOf('skill catalog') < none.indexOf('Next:'), 'Next stays last');
  assert.ok(!R('ask', FOUND()).includes('skill catalog'));
});

test('L2 counts read as numbers: 1 note, 0 notes; and a not-found names folders, not sets', () => {
  const nf = (read: number) => R('check', { outcome: 'not-found', why: 'none', next: 'none', claim: { ...NOT_FOUND_CLAIM, read } });
  assert.match(nf(1), /NOT FOUND in the 1 note read/);
  assert.match(nf(0), /NOT FOUND in the 0 notes read/);
  const s = R('ask', { outcome: 'not-found', why: 'x', next: 'connect', searched: { sets: 1, notes: 28 } });
  assert.match(s, /Searched 1 folder \(28 notes\)/);
  assert.ok(!/\bsets?\b/.test(s), s);
});

test('colour: off gives no escape bytes; on gives green TRUE and red FALSE', () => {
  const claim = (verdict: string) => ({ outcome: 'found', why: 'ok', next: 'none', claim: { verdict, read: 1, files: [],
    proof: { path: HB, line: 1, text: 'x', date: '2026-09-12' } } });
  assert.ok(!R('check', claim('TRUE')).includes('\x1b'));
  assert.match(R('check', claim('TRUE'), {}, { color: true }), /\x1b\[32m/);
  assert.match(R('check', claim('FALSE'), {}, { color: true }), /\x1b\[31m/);
  assert.ok(!/\x1b\[(33|36)m/.test(R('ask', FOUND(), {}, { color: true })), 'no cyan or yellow');
});

// ---------------------------------------------------------------- pure layer: readLine, helperCall, key, name
const FOLDER = rig().folder('Team Notes');
const FILE = join(FOLDER, 'plan[1].md');
writeFileSync(FILE, '# Plan\n');

test('S8 drops: quoted, backslash-escaped, and trailing-space forms all read as the same folder', () => {
  const esc = FOLDER.replace(/ /g, '\\ ');
  for (const line of [`'${FOLDER}'`, `"${FOLDER}"`, esc, `${esc} `, ` ${FOLDER}`]) {
    assert.deepEqual(cfg.readLine(line), { kind: 'connect', path: FOLDER, dir: true }, JSON.stringify(line));
  }
  const n = cfg.pointerName(FOLDER);
  assert.deepEqual(cfg.helperCall({ kind: 'connect', path: FOLDER, dir: true }, session),
    [join(SKILL, 'prepare_bulk.py'), '--root', FOLDER, '--pointer', n, '--principal', 'me', '--writer', 'builtin', '--json']);
});

test('S8b ~ is expanded against the given home', () => {
  const home = mkdtempSync(join(tmpdir(), 'jev-home-')); made.push(home);
  mkdirSync(join(home, 'Team Notes'));
  assert.deepEqual(cfg.readLine('~/Team\\ Notes', home), { kind: 'connect', path: join(home, 'Team Notes'), dir: true });
});

test('S9 a single .md file connects its parent with --no-recurse and an escaped --name', () => {
  assert.deepEqual(cfg.readLine(FILE), { kind: 'connect', path: FILE, dir: false });
  const argv = cfg.helperCall({ kind: 'connect', path: FILE, dir: false }, session);
  const root = argv[argv.indexOf('--root') + 1];
  assert.equal(root, FOLDER);
  assert.ok(argv.includes('--no-recurse'));
  assert.equal(argv[argv.indexOf('--name') + 1], 'plan[[]1[]].md');
  assert.equal(argv[argv.indexOf('--pointer') + 1], cfg.pointerName(FILE));
});

test('pointerName is <folder-slug>-<6 hex of sha1(path)>, stable per path', () => {
  assert.equal(cfg.pointerName(FOLDER), `team-notes-${createHash('sha1').update(FOLDER).digest('hex').slice(0, 6)}`);
  assert.equal(cfg.pointerName(FOLDER), cfg.pointerName(FOLDER));
  assert.notEqual(cfg.pointerName(FOLDER), cfg.pointerName(FOLDER + '2'));
  assert.match(cfg.pointerName(FILE), /^plan-1-[0-9a-f]{6}$/);
});

test('S10 helperCall adds --refresh when the pointer is already connected', () => {
  const n = cfg.pointerName(FOLDER);
  const argv = cfg.helperCall({ kind: 'connect', path: FOLDER, dir: true }, { ...session, connected: new Set([n]) });
  assert.ok(argv.includes('--refresh'));
  assert.ok(!cfg.helperCall({ kind: 'connect', path: FOLDER, dir: true }, session).includes('--refresh'));
});

test('helperCall: ask, check and status all carry --principal and --json; ask ends with -- and the literal text (I13)', () => {
  const ask = join(SKILL, 'ask.py');
  assert.deepEqual(cfg.helperCall({ kind: 'ask', text: '--help me' }, session), [ask, '--principal', 'me', '--json', '--', '--help me']);
  assert.deepEqual(cfg.helperCall({ kind: 'check', text: 'Hotels are capped at $300 a night.' }, session),
    [ask, '--principal', 'me', '--json', '--claim', 'Hotels are capped at $300 a night.']);
  assert.deepEqual(cfg.helperCall({ kind: 'status' }, session), [ask, '--principal', 'me', '--json', '--status']);
});

test('I7 a path that does not exist: Can\'t find, nothing to call', () => {
  const t: any = cfg.readLine('~/nope/folder', FOLDER);
  assert.equal(t.kind, 'say');
  assert.match(t.text, /Can't find/);
  assert.equal(cfg.readLine('/no/such/folder').kind, 'say');
});

test('I8 unknown commands: Unknown command, and a near miss says what it meant', () => {
  for (const c of ['/foo', '/folders', '/stauts']) {
    const t: any = cfg.readLine(c);
    assert.equal(t.kind, 'say', c);
    assert.match(t.text, new RegExp(`Unknown command ${c}`));
  }
  assert.match((cfg.readLine('/stauts') as any).text, /Did you mean \/status\?/);
  assert.ok(!/Did you mean/.test((cfg.readLine('/foo') as any).text));
});

test('I9 /check with no text gives a usage line', () => {
  const t: any = cfg.readLine('/check');
  assert.equal(t.kind, 'say');
  assert.match(t.text, /\/check/);
  assert.equal(cfg.readLine('/check   ').kind, 'say');
});

test('I10 two paths on one line: One at a time', () => {
  const t: any = cfg.readLine(`${FOLDER.replace(/ /g, '\\ ')} ${dirname(FOLDER)}`);
  assert.equal(t.kind, 'say');
  assert.match(t.text, /One at a time/);
});

test('L3 a path with an unescaped apostrophe, or a one-segment folder, still reads as a folder', () => {
  const sams = rig().folder("Sam's Notes");
  assert.deepEqual(cfg.readLine(sams), { kind: 'connect', path: sams, dir: true });
  assert.deepEqual(cfg.readLine('/usr'), { kind: 'connect', path: '/usr', dir: true }, 'not "Unknown command /usr"');
  assert.equal((cfg.readLine('/nosuchdir') as any).kind, 'say');
  assert.match((cfg.readLine('/nosuchdir') as any).text, /Unknown command \/nosuchdir/);
});

test('grammar order: bare words, then a path, then /command, then a question', () => {
  assert.equal(cfg.readLine('exit').kind, 'exit');
  assert.equal(cfg.readLine('quit').kind, 'exit');
  assert.equal(cfg.readLine('/exit').kind, 'exit');
  assert.equal(cfg.readLine('').kind, 'empty');
  assert.equal(cfg.readLine('   ').kind, 'empty');
  assert.deepEqual(cfg.readLine('/check Hotels are $220 a night.'), { kind: 'check', text: 'Hotels are $220 a night.' });
  assert.deepEqual(cfg.readLine('/status'), { kind: 'status' });
  assert.deepEqual(cfg.readLine('How long does the canary hold?'), { kind: 'ask', text: 'How long does the canary hold?' });
  assert.deepEqual(cfg.readLine('exit the building plan'), { kind: 'ask', text: 'exit the building plan' });
  assert.deepEqual(cfg.readLine('--help me'), { kind: 'help' });
});

test('S12 childEnv: the env key wins over the file key; with only a file key the child gets it; neither adds nothing', () => {
  assert.equal(cfg.childEnv({ PATH: '/x', K: 'from-env' }, 'K', 'from-file').K, 'from-env');
  assert.equal(cfg.childEnv({ PATH: '/x' }, 'K', 'from-file').K, 'from-file');
  assert.ok(!('K' in cfg.childEnv({ PATH: '/x' }, 'K', '')));
  assert.equal(cfg.childEnv({ PATH: '/x' }, 'K', 'from-file').PATH, '/x');
});

test('K3 keyAction: Ctrl+C clears a line, hints on an empty one, quits when armed; Esc clears; yes/no and key prompt keys', () => {
  const ka = cfg.keyAction;
  assert.equal(ka({ mode: 'prompt', line: 'abc', armed: false }, 'ctrl-c'), 'clear');
  assert.equal(ka({ mode: 'prompt', line: '', armed: false }, 'ctrl-c'), 'hint');
  assert.equal(ka({ mode: 'prompt', line: '', armed: true }, 'ctrl-c'), 'quit');
  assert.equal(ka({ mode: 'prompt', line: 'abc', armed: false }, 'escape'), 'clear');
  assert.equal(ka({ mode: 'confirm', line: '', armed: false }, 'enter'), 'yes');
  assert.equal(ka({ mode: 'confirm', line: '', armed: false }, 'escape'), 'no');
  assert.equal(ka({ mode: 'confirm', line: '', armed: false }, 'ctrl-c'), 'no');
  assert.equal(ka({ mode: 'secret', line: '', armed: false }, 'enter'), 'send');
  assert.equal(ka({ mode: 'secret', line: '', armed: false }, 'escape'), 'cancel');
  assert.equal(ka({ mode: 'secret', line: '', armed: false }, 'ctrl-d'), 'cancel', 'Ctrl+D leaves the key prompt like Esc');
});

test('no runtime dependencies: package.json lists none and no source file imports a package', () => {
  const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'));
  assert.deepEqual(Object.keys(pkg.dependencies || {}), []);
  for (const f of ['jev-chat-cli.ts', 'jev-chat-config.ts']) {
    const src = readFileSync(join(ROOT, 'src', f), 'utf8');
    assert.ok(!/@clack|picocolors/.test(src), `${f} imports a package`);
  }
});

// ---------------------------------------------------------------- whole app: the one-shot door
test('S17 one-shot, piped: the app exits with the helper\'s own code, no ANSI, nothing else on stdout', async () => {
  const bodies = [
    FOUND(),
    { v: 1, outcome: 'not-found', why: 'no file', next: 'connect', searched: { sets: 1, notes: 3 } },
    { v: 1, outcome: 'not-supported', why: 'too many parts', next: 'rephrase' },
    { v: 1, outcome: 'error', why: 'it broke', next: 'none' },
    { v: 1, outcome: 'needs-setup', why: 'not set up', next: 'setup' },
    { v: 1, outcome: 'error', why: 'odd code', next: 'none' },
  ];
  for (const [code, body] of bodies.entries()) {
    const r = rig([{ when: '--json', out: body, code }]);
    const d = await door(r, ['How', 'long', 'does', 'the', 'canary', 'hold?']);
    assert.equal(d.code, code, `stub exit ${code}\n${d.out}${d.err}`);
    assert.ok(!d.out.includes('\x1b'), 'no ANSI when piped');
    assert.match(d.out, /^• /m);
    assert.ok(!/Searching|⠴|esc to stop/.test(d.out), 'the working row is never on stdout');
    assert.equal(r.asks().length, 1);
    assert.deepEqual(r.asks()[0].args.slice(-2), ['--', 'How long does the canary hold?'], 'the words are joined into one line');
  }
});

test('S1 whole app: a found answer prints through the door with exit 0', async () => {
  const r = rig();
  r.rules([{ when: '--json', out: FOUND({ files: [{ path: join(r.home, 'Team Notes/eng/handbook.md'), tier: 'confirmed', line: 39, text: HANDBOOK_QUOTE }] }), code: 0 }]);
  const d = await door(r, ['canary?']);
  assert.equal(d.code, 0);
  assert.match(d.out, /~\/Team Notes\/eng\/handbook\.md:39/);
});

test('S5 whole app: a FALSE claim exits with the stub\'s code and is not framed as an error', async () => {
  const body = { v: 1, outcome: 'found', why: 'ok', next: 'none', claim: { verdict: 'FALSE', read: 2, files: [],
    proof: { path: '/Users/sam/Team Notes/policies/expenses.md', line: 4, text: 'Hotels: up to $220 per night.', date: '2026-08-30' } } };
  const r = rig([{ when: '--claim', out: body, code: 1 }]);
  const d = await door(r, ['/check', 'Hotels are capped at $300 a night.']);
  assert.equal(d.code, 1);
  assert.match(d.out, /^• FALSE/m);
  assert.ok(!/hit an error|couldn't/i.test(d.out));
  assert.deepEqual(r.calls()[0].args.slice(-2), ['--claim', 'Hotels are capped at $300 a night.']);
});

test('I13 a first word that starts with - is never a question: --help and -h print the usage, --version the version, anything else is an unknown option', () => {
  assert.equal(cfg.readLine('--help').kind, 'help');
  assert.equal(cfg.readLine('-h').kind, 'help');
  assert.equal(cfg.readLine('--help me').kind, 'help');
  assert.equal(cfg.readLine('--version').kind, 'version');
  for (const line of ['--bogus', '-x what is this', '-5 degrees in fahrenheit', '--principal evil']) {
    const t: any = cfg.readLine(line);
    assert.equal(t.kind, 'say', line);
    assert.match(t.text, new RegExp(`^Unknown option ${line.split(' ')[0]}\\.`), line);
    assert.match(t.text, /--help/, 'the usage hint');
    assert.ok(!t.text.includes('\n'), 'one line');
  }
  assert.equal(cfg.readLine('what is -h?').kind, 'ask', 'a dash after the first word is part of the question');
});

test('I13 whole app: --help and -h print the usage, --version the version, exit 0, no helper; an unknown option exits 2, no helper', async () => {
  const r = rig([{ when: '--json', out: FOUND(), code: 0 }]);
  for (const args of [['--help'], ['-h'], ['--help', 'me']]) {
    const d = await door(r, args);
    assert.equal(d.code, 0, args.join(' '));
    assert.match(d.out, /^Usage: superjev "your question"$/m, 'the usage leads');
    assert.match(d.out, /^ +superjev +open the window$/m, 'and says that no words opens the window');
    assert.match(d.out, /\/check/, 'then the list of commands');
    assert.ok(d.out.indexOf('Usage:') < d.out.indexOf('/check'), 'the usage comes first');
  }
  const v = await door(r, ['--version']);
  assert.equal(v.code, 0);
  assert.ok(v.out.includes(VERSION), v.out);
  for (const args of [['--bogus'], ['-x', 'what', 'is', 'this']]) {
    const d = await door(r, args);
    assert.equal(d.code, 2, args.join(' '));
    assert.equal(d.out, '');
    assert.match(d.err, new RegExp(`^Unknown option ${args[0]}\\..*--help`));
    assert.equal(d.err.trim().split('\n').length, 1, 'one line');
  }
  assert.equal(r.calls().length, 0, 'no helper was called, so nothing was asked');
});

test('I13 window: --version prints the version and -x says Unknown option; no helper runs and the window stays open', async () => {
  const r = rig([STATUS_EMPTY, { when: '--json', out: FOUND() }]);
  const w = win(r);
  await w.ready();
  const n = r.calls().length;
  w.say('--version');
  await w.waitFor(() => w.text().split(`Super Jev ${VERSION}`).length > 2); // the launch banner is the first one
  w.say('-x what');
  await w.waitFor('Unknown option -x.');
  assert.equal(r.calls().length, n);
  assert.equal(await w.quit(), 0);
});

test('I11 no terminal and no line: one usage line on stderr, exit 2, no python3 text, no helper called', async () => {
  const r = rig([STATUS_EMPTY]);
  const d = await door(r, []);
  assert.equal(d.code, 2);
  assert.match(d.err, /superjev/);
  assert.equal(d.err.trim().split('\n').length, 1);
  assert.equal(d.out, '');
  assert.equal(r.calls().length, 0);
});

test('S15 whole app: /help through the door prints the help and calls nothing', async () => {
  const r = rig();
  const d = await door(r, ['/help']);
  assert.equal(d.code, 0);
  assert.match(d.out, /\/check/);
  assert.equal(r.calls().length, 0);
});

test('I7/I8/I9 whole app: bad lines print a message, exit 2 and call nothing', async () => {
  for (const line of ['/stauts', '/foo', '/check', '/right', '~/nope/folder']) {
    const r = rig();
    const d = await door(r, [line]);
    assert.equal(d.code, 2, line);
    assert.ok((d.out + d.err).trim().length > 0, line);
    assert.equal(r.calls().length, 0, line);
  }
});

test('S11 door: no key anywhere for an ask prints the key message, exit 4, no helper called', async () => {
  const r = rig([{ when: '--json', out: FOUND() }]);
  const d = await door(r, ['canary?'], { key: false });
  assert.equal(d.code, 4);
  assert.match(d.out + d.err, /No TypeSafe key: open the window \(npm run jev, or superjev with no words\) to paste it, or set TYPESAFE_API_KEY\./);
  assert.equal(r.calls().length, 0);
});

test('S12 door: the child gets the env key when the file holds another; the file is untouched', async () => {
  const r = rig([{ when: '--json', out: FOUND() }]);
  writeFileSync(r.keyFile(), FILE_KEY + '\n', { mode: 0o600 });
  await door(r, ['canary?']);
  assert.equal(r.asks()[0].key_sha, sha(KEY));
  assert.equal(readFileSync(r.keyFile(), 'utf8'), FILE_KEY + '\n');
});

test('S12b door: with no env key, the file key reaches the child', async () => {
  const r = rig([{ when: '--json', out: FOUND() }]);
  writeFileSync(r.keyFile(), FILE_KEY + '\n', { mode: 0o600 });
  await door(r, ['canary?'], { key: false });
  assert.equal(r.asks()[0].key_sha, sha(FILE_KEY));
});

test('E2 door: an env key the judge rejected says fix it in the shell, writes no file, and never prompts', async () => {
  const r = rig([{ when: '--json', out: { v: 1, outcome: 'error', why: 'no match, and 1 set failed', next: 'key', errors: [{ set: 'a', kind: 'auth-rejected' }] }, code: 3 }]);
  const d = await door(r, ['canary?']);
  assert.equal(d.code, 3);
  assert.match(d.out, /rejected the key/);
  assert.match(d.out, /in your shell/);
  assert.ok(!existsSync(r.keyFile()));
  assert.ok(!/key ›/.test(d.out + d.err));
});

test('E5 door: a helper that exits 3 with non-JSON stdout gives Super Jev hit an error plus its last stderr line, exit 3', async () => {
  const r = rig([{ when: '--json', out: 'Traceback (most recent call last):\n', err: 'File "x.py", line 3\nValueError: bad registry\n', code: 3 },
    { when: 'setup.py', out: 'READY.\n', code: 0 }]);
  const d = await door(r, ['canary?']);
  assert.equal(d.code, 3);
  assert.match(d.out, /Super Jev hit an error/);
  assert.ok(d.out.includes('ValueError: bad registry'));
  assert.ok(!d.out.includes('line 3'), 'only the last stderr line');
  assert.deepEqual(r.calls().map((c: any) => c.script), ['ask.py', 'setup.py'], 'setup was fine, so the crash stays as it is');
});

test('E6 door: no python3 on PATH gives the Python 3.10 message and exit 1', async () => {
  const empty = join(rig().dir, 'empty'); mkdirSync(empty);
  const r = rig();
  const d = await door(r, ['canary?'], { env: { PATH: empty }, allow: ['python3 '] });
  assert.equal(d.code, 1);
  assert.match(d.out + d.err, /Super Jev needs Python 3\.10 or newer, and python3 was not found\. Install it \(python\.org\/downloads\), then run npm run jev again\./);
});

test('S14 door: /status shows what the helper reported and passes its exit code', async () => {
  const r = rig([{ when: '--status', out: { v: 1, next: 'refresh', principal: 'me', sets: [SET('team-notes-3fa9c1', '/Users/sam/Team Notes', 28, 'stale')] }, code: 0 }]);
  const d = await door(r, ['/status']);
  assert.equal(d.code, 0);
  assert.ok(d.out.includes('/Users/sam/Team Notes') && d.out.includes('28 notes') && d.out.includes('stale'));
  assert.ok(!d.out.includes('team-notes-3fa9c1'), 'the folder leads the row, not the internal name');
});

const STATUS_ERR = { v: 1, outcome: 'error', why: 'Connection status unavailable. Next: check the memory connector configuration.', next: 'none' };

test('H1 door: a /status the helper could not read prints why and its exit code, never "Nothing connected"', async () => {
  const r = rig([{ when: '--status', out: STATUS_ERR, code: 1 }]);
  const d = await door(r, ['/status']);
  assert.equal(d.code, 1);
  assert.ok(flat(d.out).includes(STATUS_ERR.why), d.out);
  assert.ok(!/Nothing connected|ask again/.test(d.out), d.out);
});

test('H1 door: a /check whose judge could not be reached says so, never NOT FOUND, and exits 1', async () => {
  const r = rig([{ when: '--claim', code: 1, out: { v: 1, outcome: 'error', why: 'no match, and 1 set failed', next: 'none',
    claim: NOT_FOUND_CLAIM, errors: [{ set: 'team-notes-3fa9c1', kind: 'unreachable' }],
    unsearched: [{ set: 'team-notes-3fa9c1', root: '/Users/sam/Team Notes', state: 'failed', healing: false }] } }]);
  const d = await door(r, ['/check', 'The canary holds 40 minutes.']);
  assert.equal(d.code, 1);
  assert.match(d.out, /can't be reached/);
  assert.ok(!/NOT FOUND/.test(d.out), d.out);
});

test('L3 door: a folder whose name holds an apostrophe is a folder, not "Can\'t find"', async () => {
  const r = rig();
  const d = await door(r, [r.folder("Sam's Notes")]);
  assert.ok(!/Can't find/.test(d.out + d.err), d.out + d.err);
  assert.match(d.out, /Not connected/, 'no keyboard, so a no');
});

test('door: a folder path with no keyboard counts as no, and connects nothing', async () => {
  const r = rig([STATUS_EMPTY, { when: 'prepare_bulk.py', out: { v: 1, connected: 3 } }]);
  const d = await door(r, [FOLDER]);
  assert.equal(d.code, 1);
  assert.match(d.out, /Not connected/);
  assert.ok(!r.calls().some((c: any) => c.script === 'prepare_bulk.py'));
});

// ---------------------------------------------------------------- whole app: the window
test('launch, first time: version from package.json, key prompt, Ready, nothing connected, no banner, no config file', async () => {
  const r = rig([STATUS_EMPTY, { when: '--json', out: FOUND() }]);
  const w = win(r, { key: false });
  await w.waitFor('key ›');
  w.send(KEY + '\r');
  await w.ready();
  assert.match(w.text(), new RegExp(`Super Jev ${VERSION.replace(/\./g, '\\.')}`));
  assert.ok(!/v0\.1\.0|model:|Tips|____/.test(w.text()), 'old banner and labels are gone');
  assert.ok(!/\x1b(c|\[2J|\[3J)/.test(w.raw()), 'the screen is not cleared');
  assert.match(w.text(), /Nothing connected yet/);
  assert.match(w.text(), /Drag a folder of Markdown notes in here\./);
  assert.ok(!existsSync(join(r.home, '.config')), 'the old config.json is not used');
  assert.equal(await w.quit(), 0);
  w.all();
});

test('launch, later: one line with the folder count and state', async () => {
  const r = rig([{ when: '--status', out: { v: 1, next: 'none', principal: 'me', sets: [SET('a-111111', '/Users/sam/A', 3), SET('b-222222', '/Users/sam/B', 4)] } }]);
  const w = win(r);
  await w.ready();
  assert.match(w.text(), new RegExp(`Super Jev ${VERSION.replace(/\./g, '\\.')} · 2 folders · up to date · \\? for help`));
  assert.equal(await w.quit(), 0);
});

test('launch with a refreshing folder says so instead of up to date', async () => {
  const r = rig([{ when: '--status', out: { v: 1, next: 'none', principal: 'me', sets: [SET('a-111111', '/Users/sam/A', 3), SET('b-222222', '/Users/sam/B', 4, 'refreshing')] } }]);
  const w = win(r);
  await w.ready();
  assert.match(w.text(), /2 folders · 1 refreshing/);
  assert.ok(!w.text().includes('up to date'));
  await w.quit();
});

test('S11 a typed key: the helper sees a key, the key file is 0600, and the key is in no argv and no output', async () => {
  const r = rig([STATUS_EMPTY, { when: '--json', out: FOUND() }]);
  const w = win(r, { key: false });
  await w.waitFor('key ›');
  w.send(KEY + '\r');
  await w.ready();
  w.say('How long does the canary hold?');
  await w.waitFor('Found 1 note');
  assert.equal(statSync(r.keyFile()).mode & 0o777, 0o600);
  assert.equal(readFileSync(r.keyFile(), 'utf8').trim(), KEY);
  assert.ok(r.asks()[0].key_set);
  assert.equal(r.asks()[0].key_sha, sha(KEY));
  assert.ok(!JSON.stringify(r.calls()).includes(KEY), 'the key is in no argv');
  assert.ok(!w.raw().includes(KEY) && !w.err().includes(KEY), 'the key is in no output');
  await w.quit();
});

test('S11b a pasted key with bracketed-paste markers is cleaned before it is saved', async () => {
  const r = rig([STATUS_EMPTY]);
  const w = win(r, { key: false });
  await w.waitFor('key ›');
  w.send(`\x1b[200~${KEY}\x1b[201~\r`);
  await w.ready();
  assert.equal(readFileSync(r.keyFile(), 'utf8').trim(), KEY);
  await w.quit();
});

test('S12 window: with the env key set and another in the file, no prompt, the child gets the env key, the file is untouched', async () => {
  const r = rig([STATUS_EMPTY, { when: '--json', out: FOUND() }]);
  writeFileSync(r.keyFile(), FILE_KEY + '\n', { mode: 0o600 });
  const w = win(r);
  await w.ready();
  w.say('canary?');
  await w.waitFor('Found 1 note');
  assert.ok(!w.text().includes('key ›'));
  assert.equal(r.asks()[0].key_sha, sha(KEY));
  assert.equal(readFileSync(r.keyFile(), 'utf8'), FILE_KEY + '\n');
  await w.quit();
});

test('I12 Esc at the key prompt: a No key message, exit 1, no key file written', async () => {
  const r = rig([STATUS_EMPTY]);
  const w = win(r, { key: false });
  await w.waitFor('key ›');
  w.send('\x1b');
  assert.equal(await w.exit(), 1);
  assert.match(w.text(), /No key entered\. Open the window again to paste it, or set TYPESAFE_API_KEY\./);
  assert.ok(!existsSync(r.keyFile()));
});

test('S13 first launch with next: setup runs setup.py once, then status again, and prints nothing from it', async () => {
  const r = rig([
    { when: '--status', replies: [{ when: '', out: { v: 1, next: 'setup', principal: 'me', sets: [] }, code: 1 }, { when: '', out: { v: 1, next: 'connect', principal: 'me', sets: [] } }] },
    { when: 'setup.py', out: 'made  memory config: /x\nok    state folder: /x\n\nREADY. Next step: connect a folder of .md files\n', code: 0 },
  ]);
  const w = win(r);
  await w.ready();
  assert.deepEqual(r.calls().map((c: any) => c.script + ' ' + (c.args.includes('--status') ? 'status' : c.args.join(' '))),
    ['ask.py status', 'setup.py ', 'ask.py status']);
  assert.ok(!/READY|memory config|state folder/.test(w.text()), 'setup\'s own text is not shown on success');
  assert.equal(r.calls()[1].key_sha, sha(KEY), 'setup gets the key');
  await w.quit();
});

test('E7 setup.py exits 1: its text verbatim and exit 1', async () => {
  const text = 'NOT READY:\n  - Node 24 or newer is required (found: v20.0.0). Install it, then run setup again.\n\nFix the above, then run setup again.\n';
  const r = rig([{ when: '--status', out: { v: 1, next: 'setup', principal: 'me', sets: [] }, code: 1 }, { when: 'setup.py', out: text, code: 1 }]);
  const w = win(r);
  assert.equal(await w.exit(), 1);
  assert.ok(w.text().includes(text.trim()), w.text());
});

test('H1 launch: a status the helper could not read is shown with its reason and exit code, never an empty window', async () => {
  const r = rig([{ when: '--status', out: STATUS_ERR, code: 1 }]);
  const w = win(r);
  assert.equal(await w.exit(), 1);
  assert.ok(flat(w.text()).includes(STATUS_ERR.why), w.text());
  assert.ok(!/Ready|Nothing connected|for help/.test(w.text()), w.text());
  assert.equal(r.calls().length, 1, 'no setup and no second status');
  w.all();
});

test('H1 launch: a status the helper refused (a bad principal) shows its reason, exit 2, and no Next', async () => {
  const r = rig([{ when: '--status', out: { v: 1, outcome: 'not-supported', why: 'that principal name is not valid: use letters and digits', next: 'rephrase' }, code: 2 }]);
  const w = win(r, { env: { SUPERJEV_PRINCIPAL: 'sam smith' } });
  assert.equal(await w.exit(), 2);
  assert.ok(w.text().includes('that principal name is not valid'), w.text());
  assert.ok(!/Ready|Nothing connected|Next:/.test(w.text()), w.text());
  assert.equal(r.calls()[0].args[r.calls()[0].args.indexOf('--principal') + 1], 'sam smith', 'the app passes the name as set');
});

test('H1 /status in the window: a status the helper could not read says why, and the window stays open', async () => {
  const r = rig([{ when: '--status', replies: [{ when: '', out: { v: 1, next: 'connect', principal: 'me', sets: [] } }, { when: '', out: STATUS_ERR, code: 1 }] }]);
  const w = win(r);
  await w.ready();
  w.say('/status');
  await w.waitFor('Connection status unavailable');
  assert.ok(!/ask again/.test(w.text()), 'no Next that points back at /status');
  assert.equal(w.text().match(/Nothing connected yet/g)?.length, 1, 'only the launch screen says it');
  assert.equal(await w.quit(), 0);
});

test('M1 launch with an old Python: the status gives no JSON, so setup.py runs and says what is missing, with no Next', async () => {
  const text = 'NOT READY:\n  - Python 3.10 or newer is required (found: 3.9.6). Install it, then run setup again.\n';
  const r = rig([
    { when: '--status', out: 'Traceback (most recent call last):\n', err: "TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'\n", code: 1 },
    { when: 'setup.py', out: text, code: 1 }]);
  const w = win(r);
  assert.equal(await w.exit(), 1);
  assert.ok(w.text().includes(text.trim()), w.text());
  assert.ok(!/hit an error|Next:|Ready/.test(w.text()), w.text());
  assert.deepEqual(r.calls().map((c: any) => c.script), ['ask.py', 'setup.py']);
});

test('M1b launch: no JSON before or after setup shows the error line with no Next, and exits with the helper\'s code', async () => {
  const r = rig([
    { when: '--status', out: 'oops\n', err: 'ValueError: bad registry\n', code: 3 },
    { when: 'setup.py', out: 'READY.\n', code: 0 }]);
  const w = win(r);
  assert.equal(await w.exit(), 3);
  assert.match(w.text(), /Super Jev hit an error/);
  assert.ok(w.text().includes('ValueError: bad registry'));
  assert.ok(!/Next:|Ready/.test(w.text()), w.text());
  assert.deepEqual(r.calls().map((c: any) => c.script), ['ask.py', 'setup.py', 'ask.py']);
});

test('L1 the key prompt: raw mode is on before it is written, so a key pasted early is never echoed', async () => {
  const r = rig([STATUS_EMPTY]);
  const w = win(r, { key: false });
  await w.waitFor('key ›');
  const first = w.rawLog[0];
  assert.ok(first?.on, 'raw mode was turned on');
  assert.ok(!first.screen.includes('key ›'), `raw mode came after the prompt was written:\n${first.screen}`);
  w.send(KEY + '\r');
  await w.ready();
  await w.quit();
});

test('E6 window: no python3 on PATH gives the Python 3.10 message and exit 1', async () => {
  const empty = join(rig().dir, 'empty'); mkdirSync(empty);
  const r = rig();
  const w = win(r, { env: { PATH: empty } });
  assert.equal(await w.exit(), 1);
  assert.match(w.text(), /Super Jev needs Python 3\.10 or newer, and python3 was not found\./);
});

test('S8 whole app: drop a folder, confirm with Enter, connect runs with the writer builtin and --json', async () => {
  const r = rig([STATUS_EMPTY, { when: 'prepare_bulk.py', out: { v: 1, connected: 28, skipped: [{ kind: 'types', what: 'file(s) of other types (.py 1)', count: 1, way_in: 'only .md files connect' }] }, code: 0 }]);
  const w = win(r);
  await w.ready();
  w.say(FOLDER.replace(/ /g, '\\ '));
  await w.waitFor(/Connect Team Notes\?/);
  assert.match(w.text(), /free/i);
  assert.match(w.text(), /enter yes/);
  w.send('\r');
  await w.waitFor('Connected Team Notes: 28 notes');
  const c = r.calls().find((x: any) => x.script === 'prepare_bulk.py');
  assert.deepEqual(c.args, ['--root', FOLDER, '--pointer', cfg.pointerName(FOLDER), '--principal', 'me', '--writer', 'builtin', '--json']);
  assert.match(flat(w.text()), /Left out 1 file of other types; only \.md notes connect\./);
  w.all();
  await w.quit();
});

test('S8 whole app: Esc at the confirm says Not connected and calls nothing', async () => {
  const r = rig([STATUS_EMPTY, { when: 'prepare_bulk.py', out: { v: 1, connected: 1 } }]);
  const w = win(r);
  await w.ready();
  w.say(`'${FOLDER}'`);
  await w.waitFor(/Connect Team Notes\?/);
  w.send('\x1b');
  await w.waitFor('Not connected');
  assert.ok(!r.calls().some((c: any) => c.script === 'prepare_bulk.py'));
  await w.quit();
});

test('L4 Esc at the connect confirm: a plain "Not connected", no stray prompt before it, not red', async () => {
  const r = rig([STATUS_EMPTY]);
  const w = win(r);
  await w.ready();
  w.say(FOLDER);
  await w.waitFor(/Connect Team Notes\?/);
  w.send('\x1b');
  await w.waitFor('Not connected');
  const raw = w.raw();
  assert.ok(!strip(raw.slice(raw.indexOf('esc no'), raw.indexOf('Not connected'))).includes('›'), `a prompt was redrawn before the answer:\n${w.text()}`);
  assert.ok(!/\x1b\[31m[^\n]*Not connected/.test(w.raw()), 'your own no is not drawn as a failure');
  w.send('?');
  w.send('\r');
  await w.waitFor('/exit');
  assert.match(w.text(), /› \?/, 'the normal prompt is back for the next line');
  await w.quit();
});

test('S10 whole app: a folder already in status asks to Refresh and adds --refresh', async () => {
  const n = cfg.pointerName(FOLDER);
  const r = rig([{ when: '--status', out: { v: 1, next: 'none', principal: 'me', sets: [SET(n, FOLDER, 3)] } },
    { when: 'prepare_bulk.py', out: { v: 1, connected: 4 } }]);
  const w = win(r);
  await w.ready();
  w.say(FOLDER);
  await w.waitFor(/Refresh Team Notes\?/);
  w.send('\r');
  await w.waitFor('Refreshed Team Notes: 4 notes');
  assert.ok(r.calls().find((x: any) => x.script === 'prepare_bulk.py').args.includes('--refresh'));
  await w.quit();
});

test('S10b whole app: dropping the same folder twice in a session refreshes the second time', async () => {
  const r = rig([STATUS_EMPTY, { when: 'prepare_bulk.py', out: { v: 1, connected: 3 } }]);
  const w = win(r);
  await w.ready();
  w.say(FOLDER);
  await w.waitFor(/Connect Team Notes\?/);
  w.send('\r');
  await w.waitFor('Connected Team Notes: 3 notes');
  w.say(FOLDER);
  await w.waitFor(/Refresh Team Notes\?/);
  w.send('\r');
  await w.waitFor('Refreshed Team Notes');
  const bulk = r.calls().filter((x: any) => x.script === 'prepare_bulk.py');
  assert.equal(bulk.length, 2);
  assert.ok(!bulk[0].args.includes('--refresh') && bulk[1].args.includes('--refresh'));
  await w.quit();
});

test('I4 whole app: connect exits 3 with a held note and left-out files: shown as Connected, not a failure', async () => {
  const r = rig([STATUS_EMPTY, { when: 'prepare_bulk.py', code: 3, out: { v: 1, connected: 25,
    held: [{ path: 'ops/creds.md', why: 'looks like it holds a secret' }],
    skipped: [{ kind: 'types', what: 'file(s) of other types (.py 1)', count: 1, way_in: 'only .md files connect' }] } }]);
  const w = win(r);
  await w.ready();
  w.say(FOLDER);
  await w.waitFor(/Connect Team Notes\?/);
  w.send('\r');
  await w.waitFor('Connected Team Notes: 25 notes');
  assert.match(w.text(), /Held back 1 note: looks like it holds a secret\n {4}ops\/creds\.md/);
  assert.match(flat(w.text()), /Left out 1 file of other types; only \.md notes connect\./);
  assert.ok(!/Not connected/.test(w.text()));
  await w.quit();
});

test('S14 whole app: /status in the window', async () => {
  const r = rig([{ when: '--status', out: { v: 1, next: 'none', principal: 'me', sets: [SET('team-notes-3fa9c1', '/Users/sam/Team Notes', 28), SET('handbook-ab12cd', '/Users/sam/Handbook', 12, 'refreshing')] } }]);
  const w = win(r);
  await w.ready();
  w.say('/status');
  await w.waitFor('/Users/sam/Handbook');
  assert.ok(w.text().includes('12 notes') && w.text().includes('refreshing'));
  await w.quit();
});

test('A1 whole app: not found keeps the window open and asks nothing', async () => {
  const r = rig([STATUS_EMPTY, { when: '--json -- ', out: { v: 1, outcome: 'not-found', why: 'x', next: 'connect', searched: { sets: 1, notes: 28 },
    voice: "Super Jev: I didn't have this. Want me to find it by hand and save it for next time?" }, code: 1 }]);
  const w = win(r);
  await w.ready();
  w.say('What is the office wifi password?');
  await w.waitFor('Not in your notes');
  assert.match(w.text(), /drag in the folder that has it/i);
  w.all();
  await w.quit();
});

test('E5 whole app: a crash is shown and the window stays open for the next line', async () => {
  const r = rig([STATUS_EMPTY, { when: '--json -- ', replies: [
    { when: '', out: 'Traceback\n', err: 'ValueError: bad registry\n', code: 3 }, { when: '', out: FOUND() }] }]);
  const w = win(r);
  await w.ready();
  w.say('canary?');
  await w.waitFor('Super Jev hit an error');
  assert.ok(w.text().includes('ValueError: bad registry'));
  w.say('canary again?');
  await w.waitFor('Found 1 note');
  assert.equal(await w.quit(), 0);
});

test('E2 whole app: an env key the judge rejected says fix it in the shell, no prompt, no file', async () => {
  const r = rig([STATUS_EMPTY, { when: '--json -- ', out: { v: 1, outcome: 'error', why: 'x', next: 'key', errors: [{ set: 'a', kind: 'auth-rejected' }] }, code: 3 }]);
  const w = win(r);
  await w.ready();
  w.say('canary?');
  await w.waitFor(/in your shell/);
  assert.ok(!w.text().includes('key ›'));
  assert.ok(!existsSync(r.keyFile()));
  await w.quit();
});

test('E3 whole app: unreachable', async () => {
  const r = rig([STATUS_EMPTY, { when: '--json -- ', out: { v: 1, outcome: 'error', why: 'x', next: 'none', errors: [{ set: 'a', kind: 'unreachable' }] }, code: 3 }]);
  const w = win(r);
  await w.ready();
  w.say('canary?');
  await w.waitFor("can't be reached");
  assert.match(w.text(), /Saved answers still work/);
  await w.quit();
});

test('working row: goes to stderr after 300 ms, never to stdout, and a fast answer shows none', async () => {
  const r = rig([STATUS_EMPTY, { when: '--json -- ', replies: [{ when: '', out: FOUND() }, { when: '', out: FOUND(), sleep: 900 }] }]);
  const w = win(r);
  await w.ready();
  w.say('fast one?');
  await w.waitFor('Found 1 note');
  assert.equal(w.err(), '', 'no flicker for a fast answer');
  w.say('slow one?');
  await w.waitFor(() => w.err().includes('Searching'));
  assert.ok(!w.text().includes('Searching'));
  await w.waitFor(() => (w.text().match(/Found 1 note/g) || []).length === 2);
  await w.quit();
});

test('K3 whole app: Ctrl+C twice on an empty line shows the hint, then exits 0', async () => {
  const r = rig([STATUS_EMPTY]);
  const w = win(r);
  await w.ready();
  w.send('abc');
  w.send('\x03');
  await new Promise((x) => setTimeout(x, 100));
  assert.ok(!w.text().includes('Press Ctrl+C again'), 'a line with text is cleared first');
  w.send('\x03');
  await w.waitFor('Press Ctrl+C again to quit');
  w.send('\x03');
  assert.equal(await w.exit(), 0);
});

test('K4 whole app: Up recalls the last question; Tab completes /st to /status', async () => {
  const r = rig([STATUS_EMPTY, { when: '--json -- ', out: FOUND() }]);
  const w = win(r);
  await w.ready();
  const found = () => w.text().split('Found 1 note').length - 1;
  w.say('first question?');
  await w.waitFor(() => found() === 1); // a line typed while a search runs is ignored (K8), so wait for the prompt
  w.say('second question?');
  await w.waitFor(() => found() === 2);
  w.send('\x1b[A');
  w.send('\r');
  await w.waitFor(() => found() === 3);
  assert.equal(r.asks()[2].args.at(-1), 'second question?');
  const before = r.calls().filter((c: any) => c.args.includes('--status')).length;
  w.send('/st\t');
  w.send('\r');
  await w.waitFor(() => r.calls().filter((c: any) => c.args.includes('--status')).length === before + 1);
  await w.quit();
});

test('S15 whole app: /help and ? print the same help', async () => {
  const r = rig([STATUS_EMPTY]);
  const w = win(r);
  await w.ready();
  w.say('/help');
  await w.waitFor('/check');
  const first = w.text().length;
  w.say('?');
  await w.waitFor(() => (w.text().match(/\/check/g) || []).length === 2);
  assert.ok(w.text().length > first);
  w.all();
  await w.quit();
});

test('I7 whole app: a missing path prints Can\'t find and calls nothing more', async () => {
  const r = rig([STATUS_EMPTY]);
  const w = win(r);
  await w.ready();
  const n = r.calls().length;
  w.say('~/notes/Team Notse');
  await w.waitFor("Can't find");
  assert.equal(r.calls().length, n);
  await w.quit();
});

// ---------------------------------------------------------------- review round 3 cases
test('D1 door on an old Python: a helper with no JSON runs setup.py and shows what is missing, never "hit an error"', async () => {
  const text = 'NOT READY:\n  - Python 3.10 or newer is required (found: 3.9.6). Install it, then run setup again.\n';
  const r = rig([
    { when: '--json', out: 'Traceback (most recent call last):\n', err: "TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'\n", code: 1 },
    { when: 'setup.py', out: text, code: 1 }]);
  for (const args of [['canary?'], ['/status'], ['/check', 'the canary holds at 5 percent']]) {
    const d = await door(r, args);
    assert.equal(d.code, 1, args.join(' '));
    assert.ok(d.out.includes(text.trim()), d.out);
    assert.ok(!/hit an error|Next:|TypeError/.test(d.out + d.err), d.out + d.err);
  }
  assert.deepEqual(r.calls().map((c: any) => c.script), ['ask.py', 'setup.py', 'ask.py', 'setup.py', 'ask.py', 'setup.py']);
});

test('D1b a door that got JSON never runs setup.py, and a declined connect does not either', async () => {
  const r = rig([STATUS_EMPTY, { when: '--json', out: FOUND() }, { when: 'setup.py', out: 'NOT READY\n', code: 1 }]);
  assert.equal((await door(r, ['canary?'])).code, 0);
  assert.equal((await door(r, [FOLDER])).code, 1);
  assert.deepEqual(r.calls().map((c: any) => c.script), ['ask.py']);
});

test('W1 every line fits the window: long rows and the Next line wrap, a row that fits is untouched', () => {
  const miss = R('ask', { outcome: 'not-found', why: 'x', next: 'connect', searched: { sets: 1, notes: 28 } });
  const left = R('connect', { connected: 25, skipped: [{ what: '.md file(s) in folders skipped by default: documents/ (2), profile/ (1)', count: 3,
    way_in: 'to connect one, connect that folder on its own and ask again later' }] }, { label: 'Team Notes' });
  const setup = R('ask', { outcome: 'needs-setup', why: 'x', next: 'setup' });
  for (const s of [miss, left, setup]) for (const l of lines(s)) assert.ok(l.length <= 60, `${l.length} wide:\n${s}`);
  assert.ok(lines(miss).includes('  Searched 1 folder (28 notes); nothing matched.'), 'a row that fits is one line');
  assert.match(miss, /^ {2}That doesn't prove it's nowhere: it may be in a folder you\n {2}haven't connected\.$/m, 'a line is filled up to the window width');
  assert.match(setup, /^ {2}Next: open the window \(npm run jev, or superjev with no\n {2}words\); setup runs there\.$/m);
  assert.equal(left.split('\n').filter((l) => /^ {2}Left out/.test(l)).length, 1, 'one Left out row, wrapped under itself');
});

// ---------------------------------------------------------------- round 4: one layout rule (prose wraps, a path row never breaks inside the path)
const LONG = '~/Quillbrook Team Notes/Engineering Handbook/Release Checklist for the Canary Rollout.md'; // 88 wide with ~, over both windows
const LONG_ABS = '/Users/sam' + LONG.slice(1);
const LONG_DIR = '~/Quillbrook Team Notes/Engineering Handbook/Release Checklists and Rollback Plans';
const LONG_SKILL = '~/Quillbrook Team Notes/skills/summarize-meeting/SKILL.md';
const LONG_HELD = 'Quillbrook Team Notes/Engineering Handbook/Credentials and Access Notes for Contractors.md';

/** The screen at widths 60 and 80, colour off and on: each path is whole on one line, every other line fits, and colour changes nothing. */
function everywhere(kind: string, data: object, paths: string[], extra: object = {}) {
  for (const width of [60, 80]) {
    const plain = R(kind, data, extra, { width });
    const colour = R(kind, data, extra, { width, color: true });
    const at = `width ${width}:\n${plain}`;
    assert.equal(stripVTControlCharacters(colour), plain, `colour moved the layout at ${at}`);
    for (const p of paths) assert.ok(lines(plain).some((l) => l.includes(p)), `${p} is broken across lines at ${at}`);
    for (const l of lines(plain)) assert.ok(l.length <= width || paths.some((p) => l.includes(p)), `${l.length} wide, not a path row, at ${at}`);
  }
}

test('W1 pack: any pieces, any width: lines fit unless one piece is alone and too wide, order is kept, no piece is split', () => {
  let seed = 7;
  const rnd = (n: number) => (seed = (seed * 1103515245 + 12345) % 2147483648) % n;
  const bits = ['a', 'note', '~/Team Notes/runbooks/rollback-steps-2.md:12', '\x1b[2mdim\x1b[0m', 'possible', 'x'.repeat(90), '· 3', '12:30'];
  for (let k = 0; k < 300; k++) {
    const pieces = Array.from({ length: 1 + rnd(12) }, () => (rnd(4) === 0 ? ' ' : '') + bits[rnd(bits.length)]);
    const width = 20 + rnd(70);
    const lines = cfg.pack(pieces, width);
    const plain = (x: string) => stripVTControlCharacters(x).trim();
    let i = 0;
    for (const l of lines) {
      let j = i + 1;
      const row = (to: number) => plain(pieces.slice(i, to).map((p, n) => (n ? ' ' + p : p.trimStart())).join(''));
      while (j < pieces.length && row(j) !== plain(l)) j++;
      assert.equal(row(j), plain(l), `a line is not whole pieces in order: ${JSON.stringify(l)}`);
      assert.ok(stripVTControlCharacters(l).length <= width || j === i + 1, `too wide with several pieces: ${JSON.stringify(l)}`);
      i = j;
    }
    assert.equal(i, pieces.length, 'a piece was lost');
  }
});

test('W1 a path with a space, wider than the window, is never broken: found file and skill rows, widths 60 and 80, colour on and off', () => {
  everywhere('ask', FOUND({ skills: [{ name: 'summarize-meeting', path: '/Users/sam' + LONG_SKILL.slice(1), guess: true }],
    files: [{ path: LONG_ABS, tier: 'possible', line: 39, date: '2026-09-12', text: HANDBOOK_QUOTE }, { path: LONG_ABS, tier: 'unchecked', line: 7 }] }),
  [LONG + ':39', LONG + ':7', LONG_SKILL]);
});

test('W1 a path with a space, wider than the window, is never broken: claim proof and claim file rows, widths 60 and 80, colour on and off', () => {
  everywhere('check', { outcome: 'found', why: 'ok', next: 'none', claim: { verdict: 'CONFLICT', read: 2,
    proof: { path: LONG_ABS, line: 39, text: HANDBOOK_QUOTE, date: '2026-09-12' },
    files: [{ path: LONG_ABS, says: 'FALSE', line: 39, date: '2026-09-12' }, { path: LONG_ABS, says: 'a quarter of all traffic for an hour', line: 12, date: '2026-08-01' }] } },
  [LONG + ':39', LONG + ':12']);
});

test('W1 a path with a space, wider than the window, is never broken: held, failed and status rows, widths 60 and 80, colour on and off', () => {
  everywhere('connect', { connected: 25, held: [{ path: LONG_HELD, why: 'looks like it holds a secret' }], failed: [{ path: LONG_ABS, why: 'could not be read' }] },
    [LONG_HELD, LONG], { label: 'Team Notes' });
  everywhere('status', { next: 'none', principal: 'me', sets: [SET('release-3fa9c1', '/Users/sam' + LONG_DIR.slice(1), 28), SET('handbook-ab12cd', '/Users/sam/Handbook', 12, 'refreshing')] },
    [LONG_DIR]);
});

test('W1 prose rows, the quote, the saved answer and the Next line fit both windows with colour on and off', () => {
  const answer = 'Forty minutes at five percent of traffic, then the release owner promotes it, unless a rollback ticket is open for the same service.';
  everywhere('ask', FOUND({ saved: { by: 'you', date: '2026-09-12', answer }, skills_off: 'no skill catalog was searched: skill search is off (SUPERJEV_SKILLS=0)',
    left_out: [{ what: 'file(s) in folders skipped by default: documents (2), profile (1)', count: 3, way_in: 'drag documents/ in on its own, or profile/' }] }), []);
  everywhere('ask', { outcome: 'not-found', why: 'x', next: 'connect', searched: { sets: 2, notes: 280 } }, []);
  everywhere('crash', { line: 'ValueError: the registry file is not valid JSON and could not be read by the helper at all' }, []);
});

test('W1 a 55-column tagged row stays one line with colour on, and its two-space gap stays', () => {
  const row = { path: '/Users/sam/Team Notes/runbooks/rollback-plan.md', tier: 'possible', line: 12 };
  for (const color of [false, true]) {
    const s = R('ask', FOUND({ files: [{ path: HB, tier: 'confirmed' }, { path: RC, tier: 'confirmed' }, row] }), {}, { color });
    const l = lines(stripVTControlCharacters(s)).filter((x) => x.includes('rollback-plan.md'));
    assert.deepEqual(l, ['  3 ~/Team Notes/runbooks/rollback-plan.md:12  possible'], `colour ${color}\n${s}`);
    assert.equal(l[0].length, 55);
  }
});

test('W1 a headline longer than the window wraps between its parts and keeps the time on its last line', () => {
  const data = { outcome: 'found', why: 'ok', next: 'none', claim: { verdict: 'TRUE', read: 3, proof: { path: HB, line: 39, text: HANDBOOK_QUOTE }, files: [] },
    unsearched: [{ set: 'a', root: '/Users/sam/Quillbrook Engineering Handbook', state: 'stale', healing: true }] };
  for (const color of [false, true]) {
    const s = stripVTControlCharacters(R('check', data, {}, { color }));
    const head = lines(s).slice(0, lines(s).findIndex((l) => l.startsWith('  ~/')));
    assert.ok(head.length > 1 && head.every((l) => l.length <= 60), s);
    assert.ok(head[0].startsWith('• TRUE · Quillbrook'), head[0]);
    assert.ok(head.slice(1).every((l) => l.startsWith('  ') && !l.startsWith('   ')), 'a continuation is indented two columns');
    assert.ok(head[head.length - 1].endsWith('(refreshing) · 1.4s'), head.join('\n'));
  }
});

const CONFLICT = { outcome: 'found', why: 'ok', next: 'none', claim: { verdict: 'CONFLICT', proof: null, read: 2, files: [
  { path: '/Users/sam/Team Notes/policies/expenses.md', says: 'FALSE', line: 4, date: '2026-09-20' },
  { path: '/Users/sam/Team Notes/policies/travel-old.md', says: 'TRUE', line: 9, date: '2026-08-01' }] } };

test('W3 a verdict is never left alone on a line: "· says X" is one piece at every width from 30 to 80', () => {
  for (const color of [false, true]) for (let width = 30; width <= 80; width++) {
    const s = stripVTControlCharacters(R('check', CONFLICT, {}, { width, color }));
    assert.ok(!lines(s).some((l) => /^\s*(TRUE|FALSE|PARTLY)$/.test(l)), `a verdict is alone on its line at width ${width}:\n${s}`);
    assert.equal(lines(s).filter((l) => /· says (FALSE|TRUE)$/.test(l)).length, 2, `a verdict lost its "says" at width ${width}:\n${s}`);
  }
});

test('W4 a wrapped sentence hangs under its own first column (two spaces); a wrapped path row and a quote hang at four', () => {
  const miss = R('ask', { outcome: 'not-found', why: 'x', next: 'connect', searched: { sets: 1, notes: 28 } });
  const setup = R('ask', { outcome: 'needs-setup', why: 'x', next: 'setup' });
  const left = R('connect', { connected: 25, skipped: [{ kind: 'folder', what: 'x', count: 3, way_in: 'y' }] }, { label: 'Team Notes' });
  for (const s of [miss, setup, left]) {
    assert.ok(lines(s).length > 2, s);
    for (const l of lines(s).slice(1)) assert.match(l, /^ {2}\S/, `a sentence line is not at two spaces:\n${s}`);
  }
  assert.ok(lines(R('check', CONFLICT)).some((l) => /^ {4}· says (FALSE|TRUE)$/.test(l)), 'a path row continues at four spaces');
  const found = lines(R('ask', FOUND()));
  assert.ok(found.some((l) => l.startsWith('    "The canary')) && found.some((l) => /^ {4}\S/.test(l) && l.endsWith('promoted."')), found.join('\n'));
});

test('S8c the confirm is indented two columns like the rest of a screen, wraps under itself, and ends with its hint', () => {
  for (const refresh of [false, true]) for (const width of [40, 60, 80]) {
    const t = cfg.confirmText('Team Notes', refresh, 'TypeSafe', width);
    for (const l of t.split('\n')) assert.ok(/^ {2}\S/.test(l) && l.length <= width, `${JSON.stringify(l)} at width ${width}`);
    assert.ok(t.endsWith('enter yes · esc no'), t);
    assert.ok(t.startsWith(`  ${refresh ? 'Refresh' : 'Connect'} Team Notes?`), t);
  }
});

test('W2 one way back to the window: every Next and message names how to open it, never "run superjev again"', () => {
  const setup = flat(R('ask', { outcome: 'needs-setup', why: 'x', next: 'setup' }));
  assert.match(setup, /Next: open the window \(npm run jev, or superjev with no words\); setup runs there\./);
  const gone = { outcome: 'error', why: 'x', next: 'key', errors: [{ set: 'a', kind: 'auth-rejected' }] };
  assert.match(flat(R('ask', gone, {}, { keySource: 'none' })), /Next: open the window \(npm run jev, or superjev with no words\) to paste your key\./);
  assert.match(flat(R('ask', gone, {}, { keySource: 'file' })), /Next: delete ~\/\.typesafe-api-key, then open the window \(npm run jev, or superjev with no words\) to paste a new key\./);
  for (const s of [setup, R('ask', gone, {}, { keySource: 'none' }), R('ask', gone, {}, { keySource: 'file' })]) assert.ok(!/run superjev/i.test(s), s);
});

test('X1 Esc at "Refresh Team Notes?" says Not refreshed (the folder is still connected) and calls nothing', async () => {
  const n = cfg.pointerName(FOLDER);
  const r = rig([{ when: '--status', out: { v: 1, next: 'none', principal: 'me', sets: [SET(n, FOLDER, 3)] } }, { when: 'prepare_bulk.py', out: { v: 1, connected: 4 } }]);
  const w = win(r);
  await w.ready();
  w.say(FOLDER);
  await w.waitFor(/Refresh Team Notes\?/);
  w.send('\x1b');
  await w.waitFor('Not refreshed');
  assert.ok(!/Not connected/.test(w.text()), w.text());
  assert.ok(!r.calls().some((c: any) => c.script === 'prepare_bulk.py'));
  await w.quit();
});

test('E9 a search lost with no reason ends with a next step, except on a status', () => {
  const lost = R('check', { outcome: 'error', why: 'no match, and 1 set failed', next: 'none', claim: NOT_FOUND_CLAIM, ...SET_LOST });
  assert.match(lost, /^• Couldn't search/m);
  assert.match(lost, /^ {2}Next: \/status, or ask again\.$/m);
  assert.match(R('ask', { outcome: 'error', why: 'x', next: 'none', ...SET_LOST }), /Next: \/status, or ask again\./);
  assert.ok(!/Next:/.test(R('status', { outcome: 'error', why: 'x', next: 'none', ...SET_LOST }, { secs: undefined })));
});

test('I6b not supported draws the rephrase hint only when the engine says next: rephrase', () => {
  assert.match(R('ask', { outcome: 'not-supported', why: 'the question has several parts', next: 'rephrase' }), /Next: ask one focused question\./);
  const bad = R('ask', { outcome: 'not-supported', why: 'that name is not valid', next: 'none' });
  assert.ok(bad.includes('that name is not valid') && !/Next:/.test(bad), bad);
});

test('K5 Ctrl+D at a yes/no closes the window like anywhere else: the question counts as no, nothing connects, exit 0', async () => {
  const r = rig([STATUS_EMPTY, { when: 'prepare_bulk.py', out: { v: 1, connected: 3 } }]);
  const w = win(r);
  await w.ready();
  w.say(FOLDER);
  await w.waitFor(/Connect Team Notes\?/);
  w.send('\x04');
  assert.equal(await w.exit(), 0, w.text());
  assert.ok(!r.calls().some((c: any) => c.script === 'prepare_bulk.py'), 'a pending yes/no is a no');
});

test('K5b the window uses no private readline API: Ctrl+D is the public close event', () => {
  const src = readFileSync(CLI, 'utf8');
  assert.ok(!/_ttyWrite|as any\)\._/.test(src), 'a private readline member is used');
});

test('K7 Ctrl+D at the key prompt leaves it like Esc: a No key message, exit 1, no key file written', async () => {
  const r = rig([STATUS_EMPTY]);
  const w = win(r, { key: false });
  await w.waitFor('key ›');
  w.send('\x04');
  assert.equal(await w.exit(), 1, w.text());
  assert.match(w.text(), /No key entered\./);
  assert.ok(!existsSync(r.keyFile()));
});

test('D2 Ctrl+C during a door connect exits 130 and does not run setup.py (an interrupt is not an old Python)', async () => {
  const r = rig([STATUS_EMPTY, { when: 'prepare_bulk.py', sleep: 5000, out: { v: 1, connected: 3 } },
    { when: 'setup.py', out: 'NOT READY:\n  - TYPESAFE_API_KEY is not set\n', code: 1 }]);
  const w = win(r, { argv: [FOLDER] });
  await w.waitFor(/Connect Team Notes\?/);
  w.send('\r');
  await w.waitFor(() => r.calls().some((c: any) => c.script === 'prepare_bulk.py'));
  w.send('\x03');
  assert.equal(await w.exit(), 130, w.text());
  assert.ok(!r.calls().some((c: any) => c.script === 'setup.py'), 'setup.py was run for an interrupt');
  assert.ok(!/NOT READY|not set/.test(w.text()), w.text());
});

test('K6 a line typed ahead is not an answer to the next yes/no: an early Enter connects nothing', async () => {
  const r = rig([STATUS_EMPTY, { when: 'prepare_bulk.py', out: { v: 1, connected: 3 } }]);
  const w = win(r);
  await w.ready();
  w.send(FOLDER + '\r\r'); // the drop, then an Enter typed before the question was on screen
  await w.waitFor(/Connect Team Notes\?/);
  await new Promise((x) => setTimeout(x, 200));
  assert.ok(!r.calls().some((c: any) => c.script === 'prepare_bulk.py'), 'the early Enter was not a yes');
  assert.ok(!/Connected|Not connected/.test(w.text()), w.text());
  w.send('\r');
  await w.waitFor('Connected Team Notes: 3 notes');
  await w.quit();
});

test('K8 a line typed while a search runs is ignored: no second paid ask, and the window answers the next line', async () => {
  const r = rig([STATUS_EMPTY, { when: '-- slow', sleep: 600, out: FOUND() }, { when: '-- again', out: FOUND() }]);
  const w = win(r);
  await w.ready();
  w.say('slow question');
  await w.waitFor(() => r.asks().length === 1);
  w.say('typed while it searched');
  await w.waitFor(/Found 1 note/);
  await new Promise((x) => setTimeout(x, 300));
  assert.equal(r.asks().length, 1, 'the line typed while busy was sent as a second ask');
  w.say('again');
  await w.waitFor(() => r.asks().length === 2);
  assert.ok(r.asks()[1].args.includes('again'), r.asks()[1].args.join(' '));
  assert.equal(await w.quit(), 0);
});

// ---------------------------------------------------------------- round 6: a reason is said once; the door drops typed-ahead lines too
const WHY_HELD = 'card/password-like text; held for secret-like text; Super Jev never sends that text. Remove or move the value, then reconnect.';
const nine = (why: string) => Array.from({ length: 9 }, (_, i) => ({ path: `/Users/sam/Ops Notes/creds-${i}.md`, why }));

test('G1 nine held notes with one reason say it once: every path whole on its own line, at most held+3 body lines, widths 60 and 80', () => {
  for (const width of [60, 80]) for (const color of [false, true]) {
    const s = stripVTControlCharacters(R('connect', { connected: 1, held: nine(WHY_HELD) }, { label: 'Ops Notes' }, { width, color }));
    const at = `width ${width}, colour ${color}:\n${s}`;
    assert.equal(flat(s).split(WHY_HELD).length - 1, 1, `the reason is not said exactly once at ${at}`);
    assert.ok(flat(s).includes('Held back 9 notes: ' + WHY_HELD), at);
    for (let i = 0; i < 9; i++) assert.ok(lines(s).includes(`    ~/Ops Notes/creds-${i}.md`), `path ${i} is not whole on its own line at ${at}`);
    assert.ok(lines(s).slice(1).length <= 9 + 3, `${lines(s).slice(1).length} body lines at ${at}`);
    assert.ok(lines(s).every((l) => l.length <= width), at);
  }
});

test('G1b nine failed notes with one reason say it once, the same way', () => {
  const why = 'could not be read: the file changed while it was being connected, so nothing of it was kept';
  for (const width of [60, 80]) for (const color of [false, true]) {
    const s = stripVTControlCharacters(R('connect', { connected: 1, failed: nine(why) }, { label: 'Ops Notes' }, { width, color }));
    const at = `width ${width}, colour ${color}:\n${s}`;
    assert.equal(flat(s).split(why).length - 1, 1, `the reason is not said exactly once at ${at}`);
    assert.ok(flat(s).includes('Failed: ' + why), at);
    for (let i = 0; i < 9; i++) assert.ok(lines(s).includes(`    ~/Ops Notes/creds-${i}.md`), `path ${i} is not whole on its own line at ${at}`);
    assert.ok(lines(s).slice(1).length <= 9 + 3, `${lines(s).slice(1).length} body lines at ${at}`);
  }
});

test('G1c two different reasons are two groups, each with its own count and its own paths, in the order the engine gave them', () => {
  const a = '/Users/sam/Ops Notes/', big = 'too big, split it', bin = 'binary file, not text';
  const s = R('connect', { connected: 5, held: [{ path: a + 'one.md', why: big }, { path: a + 'two.md', why: bin }, { path: a + 'three.md', why: big }, { path: a + 'four.md', why: big }],
    failed: [{ path: a + 'five.md', why: 'could not be read' }] }, { label: 'Ops Notes' }, { width: 80 });
  assert.deepEqual(lines(s), ['• Connected Ops Notes: 5 notes · 1.4s',
    '  Held back 3 notes: too big, split it', '    ~/Ops Notes/one.md', '    ~/Ops Notes/three.md', '    ~/Ops Notes/four.md',
    '  Held back 1 note: binary file, not text', '    ~/Ops Notes/two.md',
    '  Failed: could not be read', '    ~/Ops Notes/five.md']);
});

test('S3c a saved answer keeps its blank lines and its indentation: a sub-item is not a sibling, and a wrapped one stays under itself', () => {
  const answer = 'Steps:\n\n1. hold at 5 percent\n   a. watch the error rate and the latency for the whole forty minutes of the hold\n2. wait 40 minutes\n';
  const l = lines(R('ask', FOUND({ saved: { by: 'you', date: '2026-09-12', answer }, files: [{ path: HB, tier: 'confirmed' }] }), {}, { width: 60 }));
  const at = l.indexOf('  Steps:');
  assert.deepEqual(l.slice(at, at + 4), ['  Steps:', '', '  1. hold at 5 percent', '     a. watch the error rate and the latency for the whole']);
  assert.equal(l[at + 4], '     forty minutes of the hold', 'a wrapped sub-item continues under itself');
  assert.equal(l[at + 5], '  2. wait 40 minutes', 'a trailing newline adds no empty row');
});

test('W5 at the one-shot door, a Next that points at dragging says how to open the window first; in the window it is unchanged', () => {
  const lonely = { outcome: 'needs-setup', why: 'nothing is connected for me', next: 'connect' };
  const miss = { outcome: 'not-found', why: 'x', next: 'connect', searched: { sets: 1, notes: 28 } };
  const stale = { outcome: 'needs-setup', why: 'x', next: 'refresh', unsearched: [{ set: 'a', root: '/Users/sam/Handbook', state: 'stale', healing: false }] };
  const way = 'open the window \\(npm run jev, or superjev with no words\\), then';
  for (const [d, rest] of [[lonely, 'drag a folder of Markdown notes in here'], [miss, 'drag in the folder that has it'], [stale, 'drag the folder in again to refresh it']] as const) {
    assert.match(flat(R('ask', d, {}, { door: true })), new RegExp(`Next: ${way} ${rest}\\.`));
    assert.match(flat(R('ask', d)), new RegExp(`Next: ${rest}\\.`), 'the window says it as before');
    assert.ok(!new RegExp(way).test(flat(R('ask', d))), 'the window never points at itself');
  }
  assert.match(flat(R('status', { next: 'connect', principal: 'me', sets: [] }, {}, { door: true })), new RegExp(`Next: ${way} drag a folder`));
  const big = { connected: 0, refused: { kind: 'too_many', why: 'x', count: 1230, max: 250 } };
  assert.match(flat(R('connect', big, { label: 'Everything' }, { door: true })), new RegExp(`Next: ${way} drag in a smaller folder inside it\\.`));
  assert.match(flat(R('connect', big, { label: 'Everything' })), /Next: drag in a smaller folder inside it\./, 'the window says it as before');
});

test('W6 the one-shot door on an install that was never set up shows the status reply and its Next, and asks nothing', async () => {
  const r = rig([{ when: '--status', out: { v: 1, next: 'setup', principal: 'me', sets: [] } }, { when: 'prepare_bulk.py', out: { v: 1, connected: 3 } }]);
  const w = win(r, { argv: [FOLDER] });
  await w.exit();
  assert.match(flat(w.text()), /Not set up yet\s+Next: open the window \(npm run jev, or superjev with no words\); setup runs there\./, w.text());
  assert.ok(!/Connect Team Notes\?/.test(w.text()), w.text());
  assert.ok(!r.calls().some((c: any) => c.script === 'prepare_bulk.py'), 'a connect ran on a never-set-up install');
});

test('W5 whole app: the one-shot door with nothing connected tells a shell user to open the window, not to drag', async () => {
  const r = rig([{ when: '--status', out: { v: 1, next: 'connect', principal: 'me', sets: [] } }, { when: '-- canary', code: 1, out: { v: 1, outcome: 'not-found', why: 'x', next: 'connect', searched: { sets: 1, notes: 4 } } }]);
  for (const args of [['/status'], ['canary question']]) {
    const d = await door(r, args);
    assert.match(flat(d.out), /Next: open the window \(npm run jev, or superjev with no words\), then drag /, d.out);
  }
});

test('K9 at the door, a line typed while the status runs is dropped like any other: it is not the answer to the yes/no', async () => {
  const r = rig([{ when: '--status', sleep: 700, out: { v: 1, next: 'connect', principal: 'me', sets: [] } }, { when: 'prepare_bulk.py', out: { v: 1, connected: 3 } }]);
  const w = win(r, { argv: [FOLDER] });
  await w.waitFor(() => r.calls().some((c: any) => c.args.includes('--status')));
  w.send('\r'); // an Enter typed before the question was on screen
  await w.waitFor(/Connect Team Notes\?/);
  await new Promise((x) => setTimeout(x, 300));
  assert.ok(!r.calls().some((c: any) => c.script === 'prepare_bulk.py'), 'the early Enter was taken as the yes');
  w.send('\r');
  await w.waitFor('Connected Team Notes: 3 notes');
  assert.equal(await w.exit(), 0);
});

test('K9b Ctrl+C while the door checks its status exits 130, asks nothing and connects nothing', async () => {
  const r = rig([{ when: '--status', sleep: 5000, out: { v: 1, next: 'connect', principal: 'me', sets: [] } }, { when: 'prepare_bulk.py', out: { v: 1, connected: 3 } }]);
  const w = win(r, { argv: [FOLDER] });
  await w.waitFor(() => r.calls().some((c: any) => c.args.includes('--status')));
  w.send('\x03');
  assert.equal(await w.exit(), 130, w.text());
  assert.ok(!/Connect Team Notes\?/.test(w.text()), w.text());
  assert.ok(!r.calls().some((c: any) => c.script === 'prepare_bulk.py' || c.script === 'setup.py'), 'a helper ran after the interrupt');
});

// ---------------------------------------------------------------- the docs say where the key lives
const DOC = (f: string) => readFileSync(join(ROOT, f), 'utf8');
const between = (text: string, from: RegExp, to: RegExp) => { const i = text.search(from); assert.ok(i >= 0, `${from} not found`); const rest = text.slice(i + 1); const j = rest.search(to); return text.slice(i, j < 0 ? undefined : i + 1 + j); };

test('U1 the docs say what the app writes and what uninstall does with it: the key file is named and kept, never "removed with the chat config"', () => {
  const readme = DOC('README.md'), agents = DOC('AGENTS.md'), start = DOC('docs/GETTING-STARTED.md');
  const writes = readme.split('\n').find((l) => l.startsWith('| Writes |'))!;
  assert.ok(writes.includes('~/.typesafe-api-key'), 'the Writes row names the key file the app saves');
  const parts = { 'README Uninstall': between(readme, /^## Uninstall/m, /^## (?!Uninstall)/m), 'AGENTS step 8': between(agents, /^8\. \*\*Uninstall/m, /^## /m),
    'GETTING-STARTED 10': between(start, /^## 10\. Uninstall/m, /^## (?!10)/m) };
  for (const [name, text] of Object.entries(parts)) {
    assert.ok(text.includes('~/.typesafe-api-key'), `${name} does not name the key file`);
    assert.match(flat(text), /(keeps|kept|stays|leaves)[^.]*\.typesafe-api-key|\.typesafe-api-key[^.]*(is kept|stays|is left)/, `${name} does not say the key file is kept`);
  }
  for (const text of [readme, agents, start]) assert.ok(!/config\.json`?,? (\()?it holds your API key/.test(flat(text)), 'a doc still says the key lives in config.json');
});

// ---------------------------------------------------------------- real engine (free, no key)
function engineHasAskJson(): boolean {
  const tmp = mkdtempSync(join(tmpdir(), 'jev-probe-')); made.push(tmp);
  const p = spawnSync('python3', [join(SKILL, 'ask.py'), '--principal', 'me', '--json', '--status'],
    { env: { PATH: process.env.PATH ?? '', HOME: tmp, SUPERJEV_STATE_DIR: join(tmp, 's') }, encoding: 'utf8' });
  try { return JSON.parse((p.stdout || '').trim()).v === 1; } catch { return false; }
}
const HAS_JSON = engineHasAskJson();

/** A real connect writes its cache into the skill folder (gitignored): take this test's files out again. */
const CACHE = join(SKILL, 'prepare-cache');
function sweepCache(pointer: string) {
  if (!existsSync(CACHE)) return;
  for (const f of readdirSync(CACHE)) if (f.startsWith(pointer)) rmSync(join(CACHE, f), { force: true });
  const manifest = join(CACHE, '.superjev-written');
  if (existsSync(manifest)) {
    const keep = readFileSync(manifest, 'utf8').split('\n').filter((l) => l && !l.startsWith(pointer));
    if (keep.length) writeFileSync(manifest, keep.join('\n') + '\n'); else rmSync(manifest);
  }
  if (!readdirSync(CACHE).length) rmSync(CACHE, { recursive: true });
}

function realRig() {
  const dir = mkdtempSync(join(tmpdir(), 'jev-real-')); made.push(dir);
  const home = join(dir, 'home'); mkdirSync(home);
  const state = join(dir, 'state');
  const env = { PATH: process.env.PATH ?? '', HOME: home, SUPERJEV_STATE_DIR: state, TYPESAFE_API_KEY: KEY };
  const folder = join(dir, 'Team Notes'); mkdirSync(folder);
  writeFileSync(join(folder, 'handbook.md'), '# Handbook\n\nThe canary holds at 5 percent of traffic for 40 minutes.\n');
  writeFileSync(join(folder, 'runbook.md'), '# Runbook\n\nRoll back with the release tool.\n');
  writeFileSync(join(folder, 'tool.py'), 'print("hi")\n');
  const setup = () => spawnSync('python3', [join(SKILL, 'setup.py')], { env, encoding: 'utf8' });
  const pointer = cfg.pointerName(folder);
  cleanups.push(() => sweepCache(pointer));
  const connect = () => spawnSync('python3', [join(SKILL, 'prepare_bulk.py'), '--root', folder, '--pointer', pointer, '--principal', 'me',
    '--writer', 'builtin', '--json'], { env, encoding: 'utf8' });
  return { dir, home, state, env, folder, setup, connect };
}
function realWin(rr: ReturnType<typeof realRig>, withKey = true) {
  const stdin = tty(), stdout = tty(), stderr = tty();
  let out = '', err = '';
  stdout.on('data', (d) => (out += d)); stderr.on('data', (d) => (err += d));
  const env: Record<string, string> = { ...rr.env };
  if (!withKey) delete env.TYPESAFE_API_KEY;
  const done: Promise<number> = cli.run({ argv: [], env, stdin, stdout, stderr });
  const w = {
    done, text: () => strip(out), send: (s: string) => stdin.write(s), say: (l: string) => stdin.write(l + '\r'),
    async waitFor(what: string | RegExp, ms = 30000) {
      const end = Date.now() + ms;
      while (!(typeof what === 'string' ? w.text().includes(what) : what.test(w.text()))) {
        if (Date.now() > end) throw new Error(`timed out waiting for ${String(what)}; screen:\n${w.text()}\n${strip(err)}`);
        await new Promise((x) => setTimeout(x, 25));
      }
    },
    quit() { stdin.write('\x04'); return done; },
  };
  return w;
}

test('R0 the engine under the app has ask.py --json (PR 278): without it the app cannot run, so this fails and never skips', () => {
  assert.ok(HAS_JSON, 'ask.py --json is missing from this tree: merge PR 278 first');
});

test('R1 real setup.py: status says next: setup, the app runs setup, then it is ready', { timeout: 120000 }, async () => {
  const rr = realRig();
  const w = realWin(rr, false);
  await w.waitFor('key ›');
  w.send(KEY + '\r');
  await w.waitFor(/Ready\./);
  assert.ok(existsSync(join(rr.state, '_memory', 'config.json')), 'the real setup made the state folder');
  assert.ok(!/NOT READY|REFUSED/.test(w.text()));
  await w.quit();
});

test('R2 real engine, set up, nothing connected: next: connect is the drag hint', { timeout: 120000 }, async () => {
  const rr = realRig();
  assert.equal(rr.setup().status, 0);
  const w = realWin(rr);
  await w.waitFor(/Ready\./);
  assert.match(w.text(), /Nothing connected yet/);
  assert.match(w.text(), /Drag a folder of Markdown notes in here\./);
  assert.ok(!/unavailable/i.test(w.text()));
  await w.quit();
});

test('R3 real connect with the builtin writer and no key: 2 notes, one left-out line, /status shows ready', { timeout: 120000 }, async () => {
  const rr = realRig();
  assert.equal(rr.setup().status, 0);
  const w = realWin(rr);
  await w.waitFor(/Ready\./);
  w.say(rr.folder.replace(/ /g, '\\ '));
  await w.waitFor(/Connect Team Notes\?/);
  w.send('\r');
  await w.waitFor('Connected Team Notes: 2 notes');
  assert.match(w.text(), /Left out 1 .*other types/);
  w.say('/status');
  await w.waitFor(/ready/);
  assert.ok(w.text().includes('2 notes'));
  await w.quit();
});

test('R3 real connect, one-shot door: a folder with no keyboard connects nothing', { timeout: 120000 }, async () => {
  const rr = realRig();
  assert.equal(rr.setup().status, 0);
  const p = spawnSync(process.execPath, [CLI, rr.folder], { env: rr.env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  assert.equal(p.status, 1);
  assert.match(p.stdout, /Not connected/);
});

test('R4 real engine, registry unreadable: /status and the launch say why, never "Nothing connected" or "Ready"', { timeout: 120000 }, async () => {
  const rr = realRig();
  assert.equal(rr.setup().status, 0);
  assert.equal(JSON.parse(rr.connect().stdout).connected, 2);
  writeFileSync(join(rr.state, '_memory', 'registry.json'), '{{{\n');
  const p = spawnSync(process.execPath, [CLI, '/status'], { env: rr.env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  assert.equal(p.status, 1);
  assert.match(p.stdout, /Connection status unavailable/);
  assert.ok(!/Nothing connected|ask again/.test(p.stdout), p.stdout);
  const w = realWin(rr);
  assert.equal(await w.done, 1);
  assert.match(w.text(), /Connection status unavailable/);
  assert.ok(!/Ready|Nothing connected/.test(w.text()), w.text());
});

test('R5 real connect with three notes the secret scan holds: one Held back row, the reason once, each path whole', { timeout: 120000 }, async () => {
  const rr = realRig();
  assert.equal(rr.setup().status, 0);
  for (const n of ['wifi', 'database', 'deploy']) writeFileSync(join(rr.folder, `${n}.md`), `# ${n}\n\nThe ${n} login for the office.\npassword: hunter2\n`);
  const w = realWin(rr);
  await w.waitFor(/Ready\./);
  w.say(rr.folder.replace(/ /g, '\\ '));
  await w.waitFor(/Connect Team Notes\?/);
  w.send('\r');
  await w.waitFor('Connected Team Notes: 2 notes');
  const s = w.text();
  assert.equal((s.match(/Held back/g) || []).length, 1, s);
  assert.match(s, /^ {2}Held back 3 notes: /m);
  assert.equal((flat(s).match(/never sends that text/g) || []).length, 1, 'the reason is said once:\n' + s);
  for (const n of ['wifi', 'database', 'deploy']) assert.ok(lines(s).some((l) => /^ {4}\S/.test(l) && l.endsWith(`/Team Notes/${n}.md`)), `${n}.md is not whole on its own line:\n${s}`);
  await w.quit();
});
