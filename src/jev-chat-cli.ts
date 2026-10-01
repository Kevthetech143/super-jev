#!/usr/bin/env node
// `superjev`: the terminal window and the one-shot door over the super-jev helpers.
// This file only does input and output: it reads a line, runs the helper, and prints what the helper
// reported (src/jev-chat-config.ts decides what each line means and how each reply is drawn).
// Helpers print one JSON object plus an exit code; the app never reads helper text, except setup.py's
// failure text, which it shows as it is.
import { spawn, type ChildProcess } from 'node:child_process';
import { chmodSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { createInterface } from 'node:readline';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadJudgeProfile } from './judge-profile.ts';
import {
  COMMANDS, HELP, OPEN, USAGE, childEnv, confirmText, helperCall, keyAction, launchLine, pack, pointerName, prose, readLine, render,
  type Session, type Turn,
} from './jev-chat-config.ts';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SKILL = join(ROOT, 'skills', 'super-jev');
const VERSION: string = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8')).version;
const TITLE = `Super Jev ${VERSION}`;
const NO_PYTHON = 'Super Jev needs Python 3.10 or newer, and python3 was not found. Install it (python.org/downloads), then run npm run jev again.';
const ESC = Symbol('esc');
const SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏';

type IO = { argv: string[]; env: NodeJS.ProcessEnv; stdin: any; stdout: any; stderr: any };
type Done = { code: number; out: string; err: string; stopped: boolean };
type Reply = { text: string; code: number; data?: any; stopped?: boolean };
const lastLine = (s: string) => s.trim().split('\n').pop()?.trim() ?? '';

export async function run(io: IO): Promise<number> {
  const { argv, env, stdin, stdout, stderr } = io;
  const home = env.HOME || homedir();
  const profile = loadJudgeProfile(undefined, undefined, env);
  const { keyEnv, vendor } = profile;
  const keyFile = join(home, '.typesafe-api-key');
  const readKeyFile = () => { try { return readFileSync(keyFile, 'utf8').trim(); } catch { return ''; } };
  let fileKey = readKeyFile();
  const keySource = () => (keyEnv && env[keyEnv] ? 'env' : fileKey ? 'file' : 'none') as 'env' | 'file' | 'none';
  const needsKey = () => profile.keyRequired && keySource() === 'none';
  const session: Session = { principal: env.SUPERJEV_PRINCIPAL || 'me', skillDir: SKILL, connected: new Set() };
  const look = () => ({ width: Math.min(stdout.columns || 80, 80), color: !!stdout.isTTY && !env.NO_COLOR, home, keyEnv, keySource: keySource(), vendor,
    door: argv.length > 0 });
  const out = (text: string) => stdout.write(text + '\n');
  let child: ChildProcess | null = null, stopped: ChildProcess | null = null;

  // ------------------------------------------------------------ helpers
  // Each helper runs in its own process group, so one stop reaches everything it started.
  const exec = (args: string[]) => new Promise<Done>((done, fail) => {
    const p = spawn('python3', args, { env: childEnv(env, keyEnv, fileKey), stdio: ['ignore', 'pipe', 'pipe'], detached: true });
    child = p;
    let o = '', e = '';
    p.stdout.on('data', (d) => (o += d));
    p.stderr.on('data', (d) => (e += d));
    p.on('error', (err: any) => fail(err.code === 'ENOENT' ? Object.assign(new Error(NO_PYTHON), { noPython: true }) : err));
    p.on('close', (code) => { child = null; done({ code: code ?? 1, out: o, err: e, stopped: stopped === p }); });
  });

  /** Esc and Ctrl+C while a helper runs: SIGTERM to its whole group, SIGKILL two seconds later if anything is left. */
  function stopRun() {
    const p = child;
    if (!p?.pid) return;
    stopped = p;
    const send = (sig: NodeJS.Signals) => { try { process.kill(-p.pid!, sig); } catch { /* the group is gone */ } };
    send('SIGTERM');
    const hard = setTimeout(() => send('SIGKILL'), 2000);
    p.once('close', () => clearTimeout(hard));
  }

  /** The working row goes to stderr, only on a terminal and only after 300 ms, so quick answers never flicker. */
  function working(label: string): () => void {
    if (!label || !stderr.isTTY) return () => {};
    const t0 = Date.now();
    let i = 0, shown = false, tick: NodeJS.Timeout | undefined;
    const draw = () => { shown = true; stderr.write(`\r\x1b[2K${SPINNER[i++ % SPINNER.length]} ${label} · ${Math.floor((Date.now() - t0) / 1000)}s`); };
    const start = setTimeout(() => { draw(); tick = setInterval(draw, 120); }, 300);
    return () => { clearTimeout(start); clearInterval(tick); if (shown) stderr.write('\r\x1b[2K'); };
  }

  /** Runs one helper turn and draws its reply. data is null when the helper did not print one JSON object. */
  async function helper(t: Turn, extra: { label?: string; refreshed?: boolean } = {}): Promise<Reply & { data: any }> {
    const stop = working({ ask: 'Searching your notes', check: 'Checking your notes', connect: 'Connecting' }[t.kind as string] ?? '');
    const t0 = Date.now();
    let res: Done;
    try { res = await exec(helperCall(t, session)); }
    catch (e: any) { if (e.noPython) throw e; res = { code: 3, out: '', err: String(e.message ?? e), stopped: false }; }
    finally { stop(); }
    if (res.stopped) return { data: null, code: 130, stopped: true, text: render({ kind: t.kind as any, data: {}, stopped: true }, look()) };
    let data: any = null;
    try { const j = JSON.parse(res.out); if (j && typeof j === 'object') data = j; } catch { /* not JSON */ }
    const secs = t.kind === 'status' ? undefined : (Date.now() - t0) / 1000;
    if (!data) return { data, code: res.code || 3, text: render({ kind: 'crash', data: { line: lastLine(res.err) }, secs, noNext: t.kind === 'status' }, look()) };
    for (const s of data.sets ?? []) session.connected.add(s.name);
    return { data, code: res.code, text: render({ kind: t.kind as any, data, secs, ...extra }, look()) };
  }

  const askedKey = (t: Turn) => (t.kind === 'ask' || t.kind === 'check') && needsKey();
  const noKey = `No ${vendor} key: ${OPEN} to paste it, or set ${keyEnv}.`;

  /** A helper gave no JSON before any session: setup.py says what is missing (an old Python, no setup). Null when setup is fine. */
  async function notReady(): Promise<Reply | null> {
    const s = await exec([join(SKILL, 'setup.py')]);
    return s.code === 0 ? null : { text: [s.out, s.err].map((x) => x.trim()).filter(Boolean).join('\n'), code: s.code };
  }

  /** Connect or refresh a folder or note after a yes. askYes shows the question and returns the answer. */
  async function connectFlow(t: Extract<Turn, { kind: 'connect' }>, askYes: (text: string) => Promise<boolean>) {
    const name = pointerName(t.path), label = basename(t.path).replace(/\.md$/i, '');
    const refresh = session.connected.has(name);
    if (!(await askYes(confirmText(label, refresh, vendor, look().width)))) return { text: render({ kind: 'connect', data: {}, declined: true, refreshed: refresh }, look()), code: 1, data: {} };
    const r = await helper(t, { label, refreshed: refresh });
    if (r.data?.connected && !r.data.refused) session.connected.add(name);
    return r;
  }

  // ------------------------------------------------------------ keyboard
  /** The hidden key prompt: raw keys, shown as dots, never echoed or logged. Null means cancelled. */
  function readKey(): Promise<string | null> {
    const names: Record<string, string> = { '\r': 'enter', '\n': 'enter', '\x1b': 'escape', '\x03': 'ctrl-c', '\x04': 'ctrl-d' };
    return new Promise((done) => {
      let key = '';
      const end = (v: string | null) => { stdin.off('data', onData); stdin.setRawMode?.(false); stdin.pause(); stdout.write('\n'); done(v); };
      const onData = (chunk: Buffer | string) => {
        const s = String(chunk).replace(/\x1b\[20[01]~/g, '');
        if (s === '\x1b') return end(null);
        for (const ch of s.replace(/\x1b\[[0-9;]*[A-Za-z~]/g, '')) {
          const a = keyAction({ mode: 'secret', line: key, armed: false }, names[ch] ?? '');
          if (a === 'cancel') return end(null);
          if (a === 'send') { if (key.trim()) return end(key.trim()); }
          else if (ch === '\x7f' || ch === '\b') { if (key) { key = key.slice(0, -1); stdout.write('\b \b'); } }
          else if (ch >= ' ') { key += ch; stdout.write('•'); }
        }
      };
      stdin.setRawMode?.(true);
      stdin.on('data', onData);
      stdin.resume();
    });
  }

  /** The line editor: lines go to a queue, so the loop (and the yes/no) read them in order. */
  function terminal() {
    const rl = createInterface({
      input: stdin, output: stdout, terminal: true, prompt: '› ',
      completer: (l: string): [string[], string] => [l.startsWith('/') ? COMMANDS.filter((c) => c.startsWith(l)) : [], l],
    });
    const queue: (string | symbol)[] = [];
    // busy until a prompt or a question is shown, so a key typed before that is never an answer
    let closed = false, wake: (() => void) | null = null, mode: 'prompt' | 'busy' | 'confirm' = 'busy', armed = false;
    const push = (x: string | symbol) => { queue.push(x); wake?.(); };
    const clear = () => { rl.write(null as any, { ctrl: true, name: 'e' }); rl.write(null as any, { ctrl: true, name: 'u' }); };
    const pull = async (): Promise<string | symbol | null> => {
      queue.length = 0; // a line typed before the question was asked (while a helper ran) is not its answer
      while (!queue.length && !closed) await new Promise<void>((r) => (wake = r));
      return queue.shift() ?? null;
    };
    rl.on('line', (l) => push(l));
    rl.on('close', () => { closed = true; wake?.(); }); // Ctrl+D closes the window; a pending yes/no then counts as no
    const act = (a: string) => { if (a === 'clear') clear(); else if (a === 'quit') rl.close(); else if (a === 'no') { clear(); push(ESC); } };
    rl.on('SIGINT', () => {
      if (mode === 'busy') return stopRun();
      const a = keyAction({ mode, line: rl.line, armed }, 'ctrl-c');
      if (a === 'hint') { armed = true; setTimeout(() => (armed = false), 2000).unref(); stdout.write('\nPress Ctrl+C again to quit\n'); rl.prompt(); }
      else act(a);
    });
    stdin.on('keypress', (_s: string, key: any) => {
      if (key?.name !== 'escape') return;
      if (mode === 'busy') stopRun(); else act(keyAction({ mode, line: rl.line, armed }, 'escape'));
    });
    return {
      /** The next line, or null once the window is closed. */
      async next(): Promise<string | null> {
        for (;;) {
          mode = 'prompt';
          if (closed) return null;
          rl.setPrompt('› ');
          rl.prompt();
          const x = await pull();
          if (typeof x === 'string' || x === null) { mode = 'busy'; return x; }
        }
      },
      async confirm(text: string): Promise<boolean> {
        out(text);
        rl.setPrompt(''); // so clearing the line on Esc does not redraw a prompt
        mode = 'confirm';
        const x = await pull();
        mode = 'busy';
        return typeof x === 'string' && keyAction({ mode: 'confirm', line: x, armed: false }, 'enter') === 'yes';
      },
      close: () => rl.close(),
    };
  }

  // ------------------------------------------------------------ the window
  async function window(): Promise<number> {
    const stop = (r: { text: string; code: number }) => { stdin.setRawMode?.(false); out(r.text); return r.code || 1; };
    if (needsKey()) stdin.setRawMode?.(true); // from the start, so a key pasted before the prompt shows is never echoed
    let r = await helper({ kind: 'status' });
    if (r.data?.outcome) return stop(r); // the helper could not read the status: show why, never an empty window
    const fresh = !r.data || r.data.next === 'setup'; // no JSON at all (an old Python) goes to setup.py, which says what is missing
    if (!(r.data?.sets ?? []).length) out(`${TITLE}\nPoints you to the notes that answer your question.\n`);
    if (needsKey()) {
      out(pack(prose(`Paste your ${vendor} API key. It stays hidden and is saved only on this Mac.`), look().width, '', '').join('\n'));
      stdout.write('key › ');
      const key = await readKey();
      if (!key) { out(`No key entered. Open the window again to paste it, or set ${keyEnv}.`); return 1; }
      writeFileSync(keyFile, key + '\n', { mode: 0o600 });
      chmodSync(keyFile, 0o600);
      fileKey = key;
    }
    if (fresh) {
      const bad = await notReady();
      if (bad) return stop(bad);
      r = await helper({ kind: 'status' });
      if (!r.data || r.data.outcome) return stop(r);
    }
    const sets: { state: string }[] = r.data.sets ?? [];
    out(sets.length ? launchLine(VERSION, sets, look().width) : '• Ready. Nothing connected yet.\n  Drag a folder of Markdown notes in here.');

    const term = terminal();
    try {
      for (let line = await term.next(); line !== null; line = await term.next()) {
        const t = readLine(line, home);
        if (t.kind === 'exit') break;
        let text = '';
        if (t.kind === 'help') text = HELP;
        else if (t.kind === 'version') text = TITLE;
        else if (t.kind === 'say') text = t.text;
        else if (t.kind === 'connect') text = (await connectFlow(t, term.confirm)).text;
        else if (t.kind !== 'empty') text = (await helper(t)).text;
        if (text) stdout.write(text + '\n\n');
      }
    } finally { term.close(); }
    stdout.write('\n');
    return 0;
  }

  // ------------------------------------------------------------ the one-shot door
  async function door(): Promise<number> {
    const t = readLine(argv.join(' '), home);
    if (t.kind === 'help' || t.kind === 'version') { out(t.kind === 'help' ? `${USAGE}\n\n${HELP}` : TITLE); return 0; }
    if (t.kind === 'exit') return 0;
    if (t.kind === 'say') { stderr.write(t.text + '\n'); return 2; }
    if (t.kind === 'empty') { stderr.write('Usage: superjev "your question"\n'); return 2; }
    if (askedKey(t)) { stderr.write(noKey + '\n'); return 4; }
    if (t.kind === 'connect') {
      let r: Reply;
      if (stdin.isTTY) {
        const term = terminal(); // first, so a line typed while the status runs is dropped like any other, never taken as the yes
        try {
          const s = await helper({ kind: 'status' });
          if (!s.data || s.data.outcome || s.data.next === 'setup') return finish(s); // never set up: the status says what to do next, and no question is asked
          r = await connectFlow(t, term.confirm);
        } finally { term.close(); }
      } else {
        r = await connectFlow(t, async () => false);
        stderr.write(`Connecting needs a keyboard to confirm: ${OPEN}, then drag the folder in.\n`);
      }
      return finish(r);
    }
    return finish(await helper(t));
  }
  async function finish(r: Reply): Promise<number> {
    const bad = r.data || r.stopped ? null : await notReady(); // a stopped helper printed nothing on purpose: that says nothing about Python
    out((bad ?? r).text);
    return (bad ?? r).code;
  }

  try {
    if (argv.length) {
      process.once('SIGINT', stopRun); // a shell's Ctrl+C reaches this process only, never the helper's own group; a second one ends it
      try { return await door(); } finally { process.off('SIGINT', stopRun); }
    }
    if (stdin.isTTY) return await window();
    stderr.write('Usage: superjev "your question"   (run it in a terminal, with no words, for the window)\n');
    return 2;
  } catch (e: any) {
    if (!e.noPython) throw e;
    (argv.length ? stderr : stdout).write(e.message + '\n');
    return 1;
  }
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  run({ argv: process.argv.slice(2), env: process.env, stdin: process.stdin, stdout: process.stdout, stderr: process.stderr })
    .then((code) => { process.exitCode = code; })
    .catch((e) => { console.error(`Super Jev hit an error: ${e instanceof Error ? e.message : e}`); process.exitCode = 3; });
}
