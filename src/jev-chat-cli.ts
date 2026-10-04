#!/usr/bin/env node
// `superjev`: the terminal window and the one-shot door over the super-jev helpers.
// This file only does input and output: it reads a line, runs the helper, and prints what the helper
// reported (src/jev-chat-config.ts decides what each line means and how each reply is drawn).
// Helpers print one JSON object plus an exit code; the app never reads helper text, except setup.py's
// failure text, which it shows as it is.
import { spawn, type ChildProcess } from 'node:child_process';
import { chmodSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { constants, homedir } from 'node:os';
import { createInterface } from 'node:readline';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadJudgeProfile } from './judge-profile.ts';
import {
  COMMANDS, HELP, OPEN, USAGE, childEnv, confirmText, fixOf, helperCall, keyAction, launchLine, pack, pointerName, prose, readLine, render,
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
type Fix = NonNullable<ReturnType<typeof fixOf>>;
type Reply = { text: string; code: number; data?: any; stopped?: boolean; fix?: Fix | null };
const lastLine = (s: string) => s.trim().split('\n').pop()?.trim() ?? '';

export async function run(io: IO): Promise<number> {
  const { argv, env, stdin, stdout, stderr } = io;
  const home = env.HOME || homedir();
  const profile = loadJudgeProfile(undefined, undefined, env);
  const { keyEnv, vendor } = profile;
  // the same file rule as judges.key_file() in skills/super-jev/judges/__init__.py: ~/. + the key variable's name, lowercased, dashes
  const keyFile = join(home, keyEnv ? '.' + keyEnv.toLowerCase().replace(/_/g, '-') : '.no-key-file');
  const readKeyFile = () => { try { return readFileSync(keyFile, 'utf8').trim(); } catch { return ''; } };
  let fileKey = readKeyFile();
  const keySource = () => (keyEnv && env[keyEnv] ? 'env' : fileKey ? 'file' : 'none') as 'env' | 'file' | 'none';
  const needsKey = () => profile.keyRequired && keySource() === 'none';
  const session: Session = { principal: env.SUPERJEV_PRINCIPAL || 'me', skillDir: SKILL, connected: new Set() };
  const look = () => ({ width: Math.min(stdout.columns || 80, 80), color: !!stdout.isTTY && !env.NO_COLOR, home, keyEnv, keySource: keySource(), vendor,
    door: argv.length > 0 });
  const out = (text: string) => stdout.write(text + '\n');
  stdout.on('error', (e: NodeJS.ErrnoException) => { if (e.code !== 'EPIPE') throw e; }); // a reader that went away (superjev "q" | head) is not a crash
  let child: ChildProcess | null = null, stopped: ChildProcess | null = null, escStops = false, marking = false;
  /** While the window is open the terminal marks a paste (ESC[200~ ... ESC[201~), so the app can tell a paste from typing. */
  const marks = (on: boolean) => { if (on !== marking && stdout.isTTY) { marking = on; stdout.write(`\x1b[?2004${on ? 'h' : 'l'}`); } };

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
    if (!p?.pid || stopped === p) return; // a repeat while the stop is under way (npm forwards a second Ctrl+C) changes nothing
    stopped = p;
    const send = (sig: NodeJS.Signals) => { try { process.kill(-p.pid!, sig); } catch { /* the group is gone */ } };
    send('SIGTERM');
    const hard = setTimeout(() => send('SIGKILL'), 2000);
    p.once('close', () => clearTimeout(hard));
  }

  /** The app ends on a signal (the terminal closed, a kill): its helper's group ends first, so nothing is left spending. */
  function end(sig: NodeJS.Signals) {
    const p = child, code = 128 + constants.signals[sig];
    if (sig !== 'SIGHUP') marks(false); // a terminal that hung up has nothing to give back
    stopRun();
    if (p?.pid) p.once('close', () => process.exit(code)); else process.exit(code);
  }

  /** The working row goes to stderr, only on a terminal and only after 300 ms, so quick answers never flicker. It says how to stop where Esc works. */
  function working(label: string): () => void {
    if (!label || !stderr.isTTY) return () => {};
    const t0 = Date.now();
    let i = 0, shown = false, tick: NodeJS.Timeout | undefined;
    const draw = () => { shown = true; stderr.write(`\r\x1b[2K${SPINNER[i++ % SPINNER.length]} ${label} · ${Math.floor((Date.now() - t0) / 1000)}s${escStops ? ' · esc to stop' : ''}`); };
    const start = setTimeout(() => { draw(); tick = setInterval(draw, 120); }, 300);
    return () => { clearTimeout(start); clearInterval(tick); if (shown) stderr.write('\r\x1b[2K'); };
  }

  /** Runs one helper turn and draws its reply. data is null when the helper did not print one JSON object.
   *  offer (the window only): a reply the app can fix is returned with its fix, and its Next line is left out because the app does it. */
  let inWindow = false; // the window has /right; the one-shot door does not
  async function helper(t: Turn, extra: { label?: string; refreshed?: boolean; offer?: boolean } = {}): Promise<Reply & { data: any }> {
    const stop = working({ ask: 'Searching your notes', check: 'Checking your notes', connect: 'Connecting' }[t.kind as string] ?? '');
    const t0 = Date.now();
    let res: Done;
    try { res = await exec(helperCall(t, session)); }
    catch (e: any) { if (e.noPython) throw e; res = { code: 3, out: '', err: String(e.message ?? e), stopped: false }; }
    finally { stop(); }
    if (res.stopped) return { data: null, code: 130, stopped: true, text: render({ kind: t.kind as any, data: {}, stopped: true }, look()) };
    let data: any = null;
    try { const j = JSON.parse(res.out); if (j && typeof j === 'object') data = j; } catch { /* not JSON */ }
    const secs = t.kind === 'status' || t.kind === 'wrong' || t.kind === 'right' ? undefined : (Date.now() - t0) / 1000;
    // a miss that failed (exit 2 or more) is an error whatever it printed; exit 1 only means there was no saved answer to remove
    if (!data || ((t.kind === 'wrong' || t.kind === 'right') && res.code > 1)) return { data, code: res.code || 3, text: render({ kind: 'crash', data: { line: data?.why || lastLine(res.err) }, secs, noNext: t.kind === 'status' }, look()) };
    for (const s of data.sets ?? []) session.connected.add(s.name);
    const fix = extra.offer ? fixOf(data, keySource()) : null;
    return { data, code: res.code, fix, text: render({ kind: t.kind as any, data, secs, label: extra.label, refreshed: extra.refreshed, noNext: !!fix, hint: inWindow }, look()) };
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
    const name = t.pointer ?? pointerName(t.path), label = basename(t.path).replace(/\.md$/i, '');
    const refresh = !!t.pointer || session.connected.has(name);
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

  /** Asks for the key and saves it owner-only. False when cancelled. */
  async function getKey(): Promise<boolean> {
    stdout.write('key › ');
    const key = await readKey();
    if (!key) return false;
    writeFileSync(keyFile, key + '\n', { mode: 0o600 });
    chmodSync(keyFile, 0o600);
    fileKey = key;
    return true;
  }
  const NO_QUESTION = 'Ask a question first.';
  const NO_KEY = `No key entered. Open the window again to paste it, or set ${keyEnv}.`;

  /** The line editor: lines go to a queue, so the loop (and the yes/no) read them in order. Its history carries over when it is reopened. */
  function terminal(history: string[] = []) {
    const rl = createInterface({
      input: stdin, output: stdout, terminal: true, prompt: '› ', history,
      completer: (l: string): [string[], string] => [l.startsWith('/') ? COMMANDS.filter((c) => c.startsWith(l)) : [], l],
    });
    const queue: (string | symbol)[] = [];
    escStops = true;
    // readline still ends a line at every newline inside a paste's marks, so those lines are held and go out together with the line
    // the next Enter ends: a paste is one input.
    const held: string[] = [];
    let pasting = false, dropping = false; // dropping: a paste that began while a helper ran, thrown away whole when its closing mark comes
    const forget = () => { held.length = 0; pasting = false; dropping = false; }; // a paste that was not sent, or one whose closing mark never came
    const wipe = () => { rl.write(null as any, { ctrl: true, name: 'e' }); rl.write(null as any, { ctrl: true, name: 'u' }); }; // the half-typed line
    // busy until a prompt or a question is shown, so a key typed before that is never an answer
    let closed = false, wake: (() => void) | null = null, mode: 'prompt' | 'busy' | 'confirm' = 'busy', armed = false;
    const push = (x: string | symbol) => { queue.push(x); wake?.(); };
    const clear = () => { forget(); wipe(); };
    const pull = async (): Promise<string | symbol | null> => {
      // one rule: all that arrived while a helper ran is dropped (queued lines, held paste lines, the half-typed line, and the rest of a paste still arriving)
      queue.length = 0; held.length = 0; dropping = pasting; wipe();
      while (!queue.length && !closed) await new Promise<void>((r) => (wake = r));
      return queue.shift() ?? null;
    };
    marks(true);
    // Up recalls a paste whole: readline's own entries for its lines are replaced by the one question (joined by spaces so it redraws cleanly)
    rl.on('history', (h: string[]) => { if (pasting) h.shift(); });
    rl.on('line', (l) => {
      if (dropping) return;
      if (pasting) return void held.push(l);
      if (held.length) { const h = (rl as any).history as string[]; if (l.trim()) h.shift(); h.unshift([...held, l].join(' ')); }
      push([...held.splice(0), l].join('\n'));
    });
    rl.on('close', () => { closed = true; wake?.(); }); // Ctrl+D closes the window; a pending yes/no then counts as no
    const act = (a: string) => { if (a === 'clear') clear(); else if (a === 'quit') rl.close(); else if (a === 'no') { clear(); push(ESC); } };
    rl.on('SIGINT', () => {
      if (mode === 'busy') return stopRun();
      const a = keyAction({ mode, line: [...held, rl.line].join('\n'), armed }, 'ctrl-c'); // a pending paste is a line to clear, not an empty prompt
      if (a === 'hint') { armed = true; setTimeout(() => (armed = false), 2000).unref(); stdout.write('\nPress Ctrl+C again to quit\n'); rl.prompt(); }
      else act(a);
    });
    stdin.on('keypress', (_s: string, key: any) => {
      if (key?.name === 'paste-start') pasting = true;
      if (key?.name === 'paste-end') { pasting = false; if (dropping) { dropping = false; wipe(); } }
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
      close: () => { escStops = false; marks(false); rl.close(); return [...(rl as any).history] as string[]; },
    };
  }

  // ------------------------------------------------------------ the window
  async function window(): Promise<number> {
    const stop = (r: { text: string; code: number }) => { stdin.setRawMode?.(false); out(r.text); return r.code || 1; };
    if (needsKey()) stdin.setRawMode?.(true); // from the start, so a key pasted before the prompt shows is never echoed
    inWindow = true;
    let r = await helper({ kind: 'status' });
    if (r.data?.outcome) return stop(r); // the helper could not read the status: show why, never an empty window
    const fresh = !r.data || r.data.next === 'setup'; // no JSON at all (an old Python) goes to setup.py, which says what is missing
    if (!(r.data?.sets ?? []).length) out(`${TITLE}\nPoints you to the notes that answer your question.\n`);
    if (needsKey()) {
      out(pack(prose(`Paste your ${vendor} API key. It stays hidden and is saved only on this Mac.`), look().width, '', '').join('\n'));
      if (!(await getKey())) { out(NO_KEY); return 1; }
    }
    if (fresh) {
      const bad = await notReady();
      if (bad) return stop(bad);
      r = await helper({ kind: 'status' });
      if (!r.data || r.data.outcome) return stop(r);
    }
    const sets: { state: string }[] = r.data.sets ?? [];
    out(sets.length ? launchLine(VERSION, sets, look().width) : '• Ready. Nothing connected yet.\n  Drag a folder of Markdown notes in here.');

    let term = terminal();
    /** A question whose answer needs one fix: the app does it (a new key, a refresh) and asks again, once. */
    async function answer(t: Turn): Promise<string> {
      const r = await helper(t, { offer: true });
      if (!r.fix) return r.text;
      stdout.write(r.text + '\n');
      if (r.fix.kind === 'key') {
        const history = term.close(); // the hidden prompt reads raw keys, so the line editor steps aside and comes back with its history
        const got = await getKey();
        term = terminal(history);
        if (!got) return NO_KEY;
      } else {
        for (const u of r.fix.rows) {
          const c = await connectFlow({ kind: 'connect', path: u.root, dir: true, pointer: u.set }, term.confirm);
          stdout.write(c.text + '\n');
          if (!c.data?.connected || c.data.refused) return '';
        }
      }
      return (await helper(t)).text;
    }
    /** /wrong: the engine forgets the saved answer for the last question; if it had one, that question is searched fresh at once. */
    let afterCheck = false; // the last answered turn was a /check: its TRUE/FALSE saves itself
    /** The rating commands share their two guards: a /check saves itself, and a rating needs a question. */
    const noRating = () => afterCheck ? 'Sure TRUE/FALSE results save themselves; edit the note if it is wrong.' : session.last ? '' : NO_QUESTION;
    async function wrong(): Promise<string> {
      const no = noRating();
      if (no) return no;
      const r = await helper({ kind: 'wrong' });
      if (!r.data?.removed?.length) return r.text;
      stdout.write(r.text + '\n');
      return answer({ kind: 'ask', text: session.last });
    }
    /** /right [n]: the engine saves the last answer's ranked list now. */
    async function right(t: Turn): Promise<string> { return noRating() || (await helper(t)).text; }
    try {
      for (let line = await term.next(); line !== null; line = await term.next()) {
        const t = readLine(line, home);
        if (t.kind === 'exit') break;
        let text = '';
        if (t.kind === 'help') text = HELP;
        else if (t.kind === 'version') text = TITLE;
        else if (t.kind === 'say') text = t.text;
        else if (t.kind === 'connect') text = (await connectFlow(t, term.confirm)).text;
        else if (t.kind === 'ask' || t.kind === 'check') { afterCheck = t.kind === 'check'; if (t.kind === 'ask') session.last = t.text; text = await answer(t); }
        else if (t.kind === 'wrong') text = await wrong();
        else if (t.kind === 'right') text = await right(t);
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
    if (t.kind === 'wrong' || t.kind === 'right') { stderr.write(NO_QUESTION + '\n'); return 2; } // a one-shot line has no earlier question
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

  // One rule: whatever ends the app first ends its helper's group. A shell's Ctrl+C reaches only this process, never the helper's own group;
  // at the door it is a stop like Esc (Stopped., exit 130), and in the window it can only arrive before the keys are read.
  const signals = (['SIGHUP', 'SIGTERM', 'SIGINT'] as const).map((s) => [s, s === 'SIGINT' && argv.length ? stopRun : () => end(s)] as const);
  for (const [s, f] of signals) process.on(s, f);
  try {
    if (argv.length) return await door();
    if (stdin.isTTY) return await window();
    stderr.write('Usage: superjev "your question"   (run it in a terminal, with no words, for the window)\n');
    return 2;
  } catch (e: any) {
    if (!e.noPython) throw e;
    (argv.length ? stderr : stdout).write(e.message + '\n');
    return 1;
  } finally { for (const [s, f] of signals) process.off(s, f); }
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  run({ argv: process.argv.slice(2), env: process.env, stdin: process.stdin, stdout: process.stdout, stderr: process.stderr })
    .then((code) => { process.exitCode = code; })
    .catch((e) => { console.error(`Super Jev hit an error: ${e instanceof Error ? e.message : e}`); process.exitCode = 3; });
}
