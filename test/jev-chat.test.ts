// Frozen contract tests (2026-10-01), PR 3: the terminal app shows exactly what the helpers report.
//
// Layers (no Jev, no network):
//   pure        render, readLine, helperCall, childEnv, keyAction, pointerName on fixtures
//   whole app   run() with fake terminal streams, or the one-shot door as a child process, with a fake
//               python3 first on PATH. The fake records each call's argv and whether a key was set
//               (a short digest of it, never the key) and prints canned JSON with a canned exit code.
//   real engine setup.py, prepare_bulk --writer builtin and ask.py in a temp state folder. These need
//               ask.py --json, so they skip with a stated reason on a tree that does not have it yet.
// Data is made up: company Quillbrook, user sam, principal me.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { PassThrough } from 'node:stream';
import { createHash } from 'node:crypto';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
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
process.on('exit', () => { for (const d of made) rmSync(d, { recursive: true, force: true }); });

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
function win(r: Rig, o: { key?: boolean; env?: Record<string, string> } = {}) {
  const stdin = tty(), stdout = tty(), stderr = tty();
  let out = '', err = '';
  stdout.on('data', (d) => (out += d)); stderr.on('data', (d) => (err += d));
  const env = { ...r.env, ...(o.key === false ? {} : { TYPESAFE_API_KEY: KEY }), ...o.env };
  const done: Promise<number> = cli.run({ argv: [], env, stdin, stdout, stderr });
  const w = {
    done, raw: () => out, text: () => strip(out), err: () => strip(err),
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
  assert.match(lines(s).find((l) => l.includes('expenses.md')) || '', /FALSE.*2026-09-20|2026-09-20.*FALSE/);
  assert.match(lines(s).find((l) => l.includes('travel-old.md')) || '', /TRUE.*2026-08-01|2026-08-01.*TRUE/);
  assert.match(s, /Read both before relying on either\./);
});

test('S7 skills first, then the guess tag, then the file', () => {
  const s = R('ask', FOUND({ skills: [
    { name: 'summarize-meeting', path: '/Users/sam/skills/summarize-meeting/SKILL.md', guess: false },
    { name: 'draft-email', path: '/Users/sam/skills/draft-email/SKILL.md', guess: true }] }));
  const a = s.indexOf('summarize-meeting'), b = s.indexOf('draft-email'), g = s.indexOf('guess'), f = s.indexOf('handbook.md');
  assert.ok(a > -1 && a < b && b < g && g < f, s);
  assert.equal((s.match(/guess/g) || []).length, 1);
});

test('S14 status lists names, roots, note counts and states', () => {
  const s = R('status', { next: 'none', principal: 'me', sets: [
    SET('team-notes-3fa9c1', '/Users/sam/Team Notes', 28), SET('handbook-ab12cd', '/Users/sam/Handbook', 12, 'refreshing')] });
  for (const x of ['team-notes-3fa9c1', '~/Team Notes', '28 notes', 'ready', 'handbook-ab12cd', '~/Handbook', '12 notes', 'refreshing']) {
    assert.ok(s.includes(x), `${x} missing:\n${s}`);
  }
});

test('S15 help lists exactly the commands that exist, and ? is the same help', () => {
  const s = clean(cfg.render({ kind: 'help', data: {} }, OPTS));
  assert.deepEqual([...new Set(s.match(/\/[a-z]+/g))].sort(), ['/check', '/exit', '/help', '/status']);
  assert.match(s, /\?/);
  assert.equal(cfg.readLine('?').kind, 'help');
  assert.equal(cfg.readLine('/help').kind, 'help');
  for (const c of ['/check x', '/status', '/help', '/exit']) assert.notEqual(cfg.readLine(c).kind, 'say', c);
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

test('I3 needs include: names documents and the engine\'s own way in', () => {
  const s = R('ask', { outcome: 'needs-setup', why: 'no match, but the search was incomplete: 2 files skipped at setup', next: 'include',
    left_out: [{ what: 'file(s) in folders skipped by default: documents (2)', count: 2, where: '/Users/sam/Team Notes/documents', way_in: 'drag documents/ in on its own' }] });
  assert.ok(s.includes('documents'));
  assert.ok(s.includes('drag documents/ in on its own'));
});

test('I4 connect with a held note and skipped files: Connected, a held line, left-out lines with their way in, not a failure', () => {
  const skip = { what: '.md file(s) in folders skipped by default: documents/ (2), profile/ (1)', count: 3,
    way_in: 'to connect one, connect that folder as its own set (--root FOLDER --pointer NEW-NAME)' };
  const other = { what: 'file(s) of other types (.py 1, .csv 1)', count: 2, way_in: 'only .md files connect' };
  const s = R('connect', { connected: 25, held: [{ path: 'ops/creds.md', why: 'looks like it holds a secret' }], skipped: [skip, other, other] },
    { label: 'Team Notes' });
  assert.match(s, /^• Connected Team Notes: 25 notes · 1\.4s$/m);
  assert.match(s, /Held back.*ops\/creds\.md/);
  assert.ok(s.includes('documents/ (2)') && s.includes('only .md files connect'));
  assert.equal((s.match(/only \.md files connect/g) || []).length, 1, 'a repeated left-out row is shown once');
  assert.ok(!/Not connected|failed/i.test(s), s);
});

test('I5 connect refused over the cap: Not connected, the reason, drag in a smaller folder', () => {
  const s = R('connect', { connected: 0, refused: { why: '1,230 notes is more than one connect takes (250)' } }, { label: 'Everything' });
  assert.match(s, /^• Not connected/m);
  assert.ok(s.includes('1,230 notes is more than one connect takes (250)'));
  assert.match(s, /drag in a smaller folder/i);
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
  assert.ok(s.includes('no .md file left to connect'));
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
  assert.deepEqual(cfg.readLine('--help me'), { kind: 'ask', text: '--help me' });
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
  assert.equal(ka({ mode: 'prompt', line: '', armed: false }, 'ctrl-d'), 'quit');
  assert.equal(ka({ mode: 'prompt', line: 'abc', armed: false }, 'ctrl-d'), 'none');
  assert.equal(ka({ mode: 'confirm', line: '', armed: false }, 'enter'), 'yes');
  assert.equal(ka({ mode: 'confirm', line: '', armed: false }, 'escape'), 'no');
  assert.equal(ka({ mode: 'confirm', line: '', armed: false }, 'ctrl-c'), 'no');
  assert.equal(ka({ mode: 'secret', line: '', armed: false }, 'enter'), 'send');
  assert.equal(ka({ mode: 'secret', line: '', armed: false }, 'escape'), 'cancel');
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

test('I13 whole app: a question that starts with --help reaches the helper after --', async () => {
  const r = rig([{ when: '--json', out: FOUND(), code: 0 }]);
  await door(r, ['--help', 'me']);
  assert.deepEqual(r.asks()[0].args.slice(-2), ['--', '--help me']);
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
  assert.match(d.out + d.err, /No TypeSafe key: run superjev once to paste it, or set TYPESAFE_API_KEY\./);
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
  const r = rig([{ when: '--json', out: 'Traceback (most recent call last):\n', err: 'File "x.py", line 3\nValueError: bad registry\n', code: 3 }]);
  const d = await door(r, ['canary?']);
  assert.equal(d.code, 3);
  assert.match(d.out, /Super Jev hit an error/);
  assert.ok(d.out.includes('ValueError: bad registry'));
  assert.ok(!d.out.includes('line 3'), 'only the last stderr line');
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
  assert.ok(d.out.includes('team-notes-3fa9c1') && d.out.includes('28 notes') && d.out.includes('stale'));
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
  assert.match(w.text(), /No key/);
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

test('E6 window: no python3 on PATH gives the Python 3.10 message and exit 1', async () => {
  const empty = join(rig().dir, 'empty'); mkdirSync(empty);
  const r = rig();
  const w = win(r, { env: { PATH: empty } });
  assert.equal(await w.exit(), 1);
  assert.match(w.text(), /Super Jev needs Python 3\.10 or newer, and python3 was not found\./);
});

test('S8 whole app: drop a folder, confirm with Enter, connect runs with the writer builtin and --json', async () => {
  const r = rig([STATUS_EMPTY, { when: 'prepare_bulk.py', out: { v: 1, connected: 28, skipped: [{ what: 'file(s) of other types (.py 1)', count: 1, way_in: 'only .md files connect' }] }, code: 0 }]);
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
  assert.match(w.text(), /Left out 1 .*only \.md files connect/);
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
    skipped: [{ what: 'file(s) of other types (.py 1)', count: 1, way_in: 'only .md files connect' }] } }]);
  const w = win(r);
  await w.ready();
  w.say(FOLDER);
  await w.waitFor(/Connect Team Notes\?/);
  w.send('\r');
  await w.waitFor('Connected Team Notes: 25 notes');
  assert.match(w.text(), /Held back.*ops\/creds\.md/);
  assert.match(w.text(), /only \.md files connect/);
  assert.ok(!/Not connected/.test(w.text()));
  await w.quit();
});

test('S14 whole app: /status in the window', async () => {
  const r = rig([{ when: '--status', out: { v: 1, next: 'none', principal: 'me', sets: [SET('team-notes-3fa9c1', '/Users/sam/Team Notes', 28), SET('handbook-ab12cd', '/Users/sam/Handbook', 12, 'refreshing')] } }]);
  const w = win(r);
  await w.ready();
  w.say('/status');
  await w.waitFor('handbook-ab12cd');
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
  w.say('first question?');
  w.say('second question?');
  await w.waitFor(() => r.asks().length === 2);
  w.send('\x1b[A');
  w.send('\r');
  await w.waitFor(() => r.asks().length === 3);
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

// ---------------------------------------------------------------- real engine (free, no key)
function engineHasAskJson(): boolean {
  const tmp = mkdtempSync(join(tmpdir(), 'jev-probe-')); made.push(tmp);
  const p = spawnSync('python3', [join(SKILL, 'ask.py'), '--principal', 'me', '--json', '--status'],
    { env: { PATH: process.env.PATH ?? '', HOME: tmp, SUPERJEV_STATE_DIR: join(tmp, 's') }, encoding: 'utf8' });
  try { return JSON.parse((p.stdout || '').trim()).v === 1; } catch { return false; }
}
const HAS_JSON = engineHasAskJson();
const SKIP = HAS_JSON ? false : 'this tree has no ask.py --json yet (PR 278); proven on an overlay, see the build report';

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
  return { dir, home, state, env, folder, setup };
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

test('R1 real setup.py: status says next: setup, the app runs setup, then it is ready', { skip: SKIP, timeout: 120000 }, async () => {
  const rr = realRig();
  const w = realWin(rr, false);
  await w.waitFor('key ›');
  w.send(KEY + '\r');
  await w.waitFor(/Ready\./);
  assert.ok(existsSync(join(rr.state, '_memory', 'config.json')), 'the real setup made the state folder');
  assert.ok(!/NOT READY|REFUSED/.test(w.text()));
  await w.quit();
});

test('R2 real engine, set up, nothing connected: next: connect is the drag hint', { skip: SKIP, timeout: 120000 }, async () => {
  const rr = realRig();
  assert.equal(rr.setup().status, 0);
  const w = realWin(rr);
  await w.waitFor(/Ready\./);
  assert.match(w.text(), /Nothing connected yet/);
  assert.match(w.text(), /Drag a folder of Markdown notes in here\./);
  assert.ok(!/unavailable/i.test(w.text()));
  await w.quit();
});

test('R3 real connect with the builtin writer and no key: 2 notes, one left-out line, /status shows ready', { skip: SKIP, timeout: 120000 }, async () => {
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

test('R3 real connect, one-shot door: a folder with no keyboard connects nothing', { skip: SKIP, timeout: 120000 }, async () => {
  const rr = realRig();
  assert.equal(rr.setup().status, 0);
  const p = spawnSync(process.execPath, [CLI, rr.folder], { env: rr.env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  assert.equal(p.status, 1);
  assert.match(p.stdout, /Not connected/);
});
