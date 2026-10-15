// Frozen tests (context setting 4/7): the terminal app's `/context`, how much of each note a search reads.
//
// `/context large` sets the size for the rest of the window and calls no helper; every helper call after it carries
// `--context large` (between --principal and --json), so it beats SUPERJEV_CONTEXT in the shell. `/context` alone asks the
// helper which size is in use and where that came from. `/context nope` says the usage. /status shows the size too.
//
// Same layers as test/jev-chat.test.ts, kept small and self-contained (no Jev, no network):
//   pure        readLine, helperCall, render, HELP
//   whole app   run() with fake terminal streams, or the one-shot door as a child process, with a fake python3 first on
//               PATH that records each call's argv and prints canned JSON.
// Data is made up: user sam, principal me.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { PassThrough } from 'node:stream';
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stripVTControlCharacters } from 'node:util';
import * as cfg from '../src/jev-chat-config.ts';
import { run } from '../src/jev-chat-cli.ts';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CLI = join(ROOT, 'src', 'jev-chat-cli.ts');
const NODE_BIN = dirname(process.execPath);
const KEY = 'quillbrook-test-key-0001';
const strip = (s: string) => stripVTControlCharacters(s).replace(/\r/g, '');

// ---------------------------------------------------------------- fake python3 and rig
const FAKE = `#!/usr/bin/env node
const fs = require('fs'), path = require('path');
const dir = process.env.FAKE_DIR, args = process.argv.slice(2);
const line = [path.basename(args[0] || ''), ...args.slice(1)].join(' ');
const rules = JSON.parse(fs.readFileSync(path.join(dir, 'replies.json'), 'utf8'));
const at = rules.findIndex((r) => line.includes(r.when));
fs.appendFileSync(path.join(dir, 'calls.jsonl'), JSON.stringify({ argv: args, rule: at }) + '\\n');
if (at < 0) process.exit(99);
const r = rules[at];
if (r.err) process.stderr.write(r.err);
if (r.out !== undefined) process.stdout.write(typeof r.out === 'string' ? r.out : JSON.stringify(r.out) + '\\n');
process.exit(r.code || 0);
`;

const made: string[] = [];
process.on('exit', () => { for (const d of made) rmSync(d, { recursive: true, force: true }); });

type Rule = { when: string; out?: unknown; code?: number; err?: string };
function rig(rules: Rule[]) {
  const dir = mkdtempSync(join(tmpdir(), 'jev-ctx-'));
  made.push(dir);
  const home = join(dir, 'home'), bin = join(dir, 'bin');
  mkdirSync(home);
  mkdirSync(bin);
  writeFileSync(join(bin, 'python3'), FAKE);
  chmodSync(join(bin, 'python3'), 0o755);
  writeFileSync(join(dir, 'replies.json'), JSON.stringify(rules));
  return {
    env: { PATH: `${bin}:${NODE_BIN}:/usr/bin:/bin`, HOME: home, FAKE_DIR: dir, TYPESAFE_API_KEY: KEY } as Record<string, string>,
    calls: () => {
      const f = join(dir, 'calls.jsonl');
      return !existsSync(f) ? [] : readFileSync(f, 'utf8').split('\n').filter(Boolean).map((l) => {
        const c = JSON.parse(l);
        return { script: basename(c.argv[0] || ''), args: c.argv.slice(1) as string[] };
      });
    },
  };
}
type Rig = ReturnType<typeof rig>;
const asks = (r: Rig) => r.calls().filter((c) => c.script === 'ask.py' && c.args.includes('--'));

const tty = () => Object.assign(new PassThrough(), { isTTY: true, columns: 60, setRawMode() { return this; } });

/** The window: run() with fake terminal streams. */
function win(r: Rig, env: Record<string, string> = {}) {
  let out = '', err = '';
  const stdin = tty(), stdout = tty(), stderr = tty();
  stdout.on('data', (d) => (out += d));
  stderr.on('data', (d) => (err += d));
  const done: Promise<number> = run({ argv: [], env: { ...r.env, ...env }, stdin, stdout, stderr });
  const w = {
    text: () => strip(out), err: () => strip(err),
    say: (line: string) => stdin.write(line + '\r'),
    async waitFor(what: string | RegExp | (() => boolean), ms = 8000) {
      const ok = () => (typeof what === 'function' ? what() : typeof what === 'string' ? w.text().includes(what) : what.test(w.text()));
      const end = Date.now() + ms;
      while (!ok()) {
        if (Date.now() > end) throw new Error(`timed out waiting for ${String(what)}; screen:\n${w.text()}\nstderr:\n${w.err()}`);
        await new Promise((x) => setTimeout(x, 15));
      }
    },
    ready: () => w.waitFor(/Ready\.|\? for help/),
    async quit() {
      stdin.write('\x04');
      return Promise.race([done, new Promise<number>((_, rej) => setTimeout(() => rej(new Error('run() did not finish; screen:\n' + w.text())), 8000))]);
    },
  };
  return w;
}

/** The one-shot door as a child process: superjev <args>. */
async function door(r: Rig, args: string[], env: Record<string, string> = {}) {
  return new Promise<{ code: number; out: string; err: string }>((done) => {
    const p = spawn(process.execPath, [CLI, ...args], { env: { ...r.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '', err = '';
    const kill = setTimeout(() => p.kill('SIGKILL'), 20000);
    p.stdout.on('data', (d) => (out += d));
    p.stderr.on('data', (d) => (err += d));
    p.on('close', (code) => { clearTimeout(kill); done({ code: code ?? -1, out: strip(out), err: strip(err) }); });
  });
}

const SET = { name: 'team-notes-3fa9c1', roots: ['/Users/sam/Team Notes'], notes: 28, state: 'ready' };
const STATUS = (context?: object): Rule => ({ when: '--status', out: { v: 1, next: 'none', principal: 'me', sets: [SET], ...(context ? { context } : {}) } });
const FOUND: Rule = { when: '--json -- ', out: { v: 1, outcome: 'found', why: '1 file', next: 'none',
  files: [{ path: '/Users/sam/Team Notes/eng/handbook.md', tier: 'confirmed', line: 39 }] } };
const OPTS = { width: 60, color: false, home: '/Users/sam', keyEnv: 'TYPESAFE_API_KEY', keySource: 'env' as const };
const SKILL_DIR = '/skill';
const session = { principal: 'me', skillDir: SKILL_DIR, connected: new Set<string>() };

// ---------------------------------------------------------------- /context
test('/context large sets the session and helperCall adds --context large before --json', async () => {
  // the turn: a name, read as a person types it
  assert.deepEqual(cfg.readLine('/context large'), { kind: 'context', name: 'large' });
  assert.deepEqual(cfg.readLine('/context   Medium '), { kind: 'context', name: 'medium' });
  assert.deepEqual(cfg.readLine('/CONTEXT small'), { kind: 'context', name: 'small' });
  // the helper's argv: --context goes between --principal and --json; with no choice made the argv is what it always was
  const ask = (s: object) => cfg.helperCall({ kind: 'ask', text: 'canary?' }, { ...session, ...s });
  assert.deepEqual(ask({}), [`${SKILL_DIR}/ask.py`, '--principal', 'me', '--json', '--', 'canary?']);
  assert.deepEqual(ask({ context: 'large' }), [`${SKILL_DIR}/ask.py`, '--principal', 'me', '--context', 'large', '--json', '--', 'canary?']);
  assert.deepEqual(cfg.helperCall({ kind: 'status' }, { ...session, context: 'medium' }),
    [`${SKILL_DIR}/ask.py`, '--principal', 'me', '--context', 'medium', '--json', '--status']);
  // the window: nothing is sent for /context large itself; every later helper call carries it, /context small included
  const r = rig([STATUS(), FOUND]);
  const w = win(r);
  await w.ready();
  w.say('first?');
  await w.waitFor(() => asks(r).length === 1);
  const before = r.calls().length;
  w.say('/context large');
  await w.waitFor('Context: Large (this window)');
  assert.equal(r.calls().length, before, 'setting the size calls no helper');
  w.say('second?');
  await w.waitFor(() => asks(r).length === 2);
  w.say('/context small');
  await w.waitFor('Context: Small (this window)');
  w.say('third?');
  await w.waitFor(() => asks(r).length === 3);
  const [one, two, three] = asks(r).map((c) => c.args);
  assert.deepEqual(one, ['--principal', 'me', '--json', '--', 'first?']);
  assert.deepEqual(two, ['--principal', 'me', '--context', 'large', '--json', '--', 'second?']);
  assert.deepEqual(three, ['--principal', 'me', '--context', 'small', '--json', '--', 'third?'], 'small is passed too: it beats a bigger size set in the shell');
  assert.equal(await w.quit(), 0);
});

test('/context alone shows the current one', async () => {
  // the helper's own answer is what is shown: the size, and where it came from, in the app's words
  const showing = (source: string, preset = 'medium') => strip(cfg.render({ kind: 'context', data: { v: 1, ...STATUS().out as object, context: { preset, default: 'small', source } } }, OPTS));
  assert.match(showing('flag'), /^• Context: Medium \(this window\)$/m);
  assert.match(showing('env', 'large'), /^• Context: Large \(SUPERJEV_CONTEXT\)$/m);
  assert.match(showing('settings'), /^• Context: Medium \(saved default\)$/m);
  assert.match(showing('default', 'small'), /^• Context: Small \(the default\)$/m);
  assert.match(showing('flag'), /\/context small, medium or large/, 'and how to change it');
  // /status shows it too, under the folders; a helper that says nothing of it adds no row
  const status = (context?: object) => strip(cfg.render({ kind: 'status', data: { v: 1, ...STATUS().out as object, ...(context ? { context } : {}) } }, OPTS));
  assert.match(status({ preset: 'large', default: 'small', source: 'env' }), /^ {2}Context: Large \(SUPERJEV_CONTEXT\)$/m);
  assert.ok(!status().includes('Context:'));
  // the window asks the helper for it (a status call, free), with the choice made in the window if there is one
  const r = rig([
    { when: '--context large --json --status', out: { v: 1, next: 'none', principal: 'me', sets: [SET], context: { preset: 'large', default: 'small', source: 'flag' } } },
    STATUS({ preset: 'medium', default: 'small', source: 'env' })]);
  const w = win(r);
  await w.ready();
  w.say('/context');
  await w.waitFor('Context: Medium (SUPERJEV_CONTEXT)');
  assert.ok(!w.text().includes('0.'), 'no timing on a setting');
  w.say('/context large');
  await w.waitFor('Context: Large (this window)');
  w.say('/context');
  await w.waitFor(() => (w.text().match(/Context: Large \(this window\)/g) ?? []).length === 2);
  w.say('/status');
  await w.waitFor(() => /^ {2}Context: Large \(this window\)$/m.test(w.text()));
  assert.ok(!w.text().includes('--principal') && !w.text().includes('python3'), 'the screen never shows the helper call');
  assert.equal(await w.quit(), 0);
  // the one-shot door has no window to set a size for, but it can say which one is in use
  const d = await door(r, ['/context'], { SUPERJEV_CONTEXT: 'medium' });
  assert.equal(d.code, 0);
  assert.match(d.out, /Context: Medium \(SUPERJEV_CONTEXT\)/);
});

test('/context nope says the usage', async () => {
  for (const bad of ['/context nope', '/context large small', '/context 2', '/context --large']) {
    const t = cfg.readLine(bad) as { kind: string; text: string };
    assert.equal(t.kind, 'say', bad);
    assert.equal(t.text, 'Usage: /context [small|medium|large]   how much of each note is read', bad);
  }
  const r = rig([STATUS(), FOUND]);
  const w = win(r);
  await w.ready();
  const before = r.calls().length;
  w.say('/context nope');
  await w.waitFor('Usage: /context [small|medium|large]');
  assert.equal(r.calls().length, before, 'the usage calls no helper');
  w.say('canary?');
  await w.waitFor(() => asks(r).length === 1);
  assert.ok(!asks(r)[0].args.includes('--context'), 'a refused name sets nothing');
  assert.equal(await w.quit(), 0);
  // a judge profile that states only small refuses a bigger size in the window, so no ask is ever sent one it would refuse
  const laya = win(r, { SUPERJEV_JUDGE: 'laya' });
  await laya.ready();
  laya.say('/context large');
  await laya.waitFor('Unknown context "large" (choose small)');
  laya.say('canary again?');
  await laya.waitFor(() => asks(r).length === 2);
  assert.ok(!asks(r)[1].args.includes('--context'));
  await laya.quit();
  // the door cannot keep a size between runs: it says so and points at the variable
  const d = await door(r, ['/context', 'large']);
  assert.equal(d.code, 2);
  assert.match(d.err, /works in the window.*SUPERJEV_CONTEXT/);
});

test('HELP lists /context', () => {
  const rows = cfg.HELP.split('\n');
  assert.ok(rows.includes('  /context [name]     how much of each note is read'), cfg.HELP);
  assert.ok(rows.indexOf('  /context [name]     how much of each note is read') < rows.indexOf('  /help or ?          show this list'));
  assert.ok(strip(cfg.render({ kind: 'help', data: {} }, OPTS)).includes('/context [name]'));
  // Tab completes it, and a near miss is told what it meant
  assert.ok(cfg.COMMANDS.includes('/context'));
  assert.match((cfg.readLine('/contxt') as { text: string }).text, /Did you mean \/context\?/);
});
