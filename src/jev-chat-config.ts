// The pure core of the terminal app: read a typed line, build the helper call, draw the helper's reply.
// Nothing here talks to a helper or a terminal. readLine only stats a path on disk, read-only.
import { statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { basename, dirname, join, resolve } from 'node:path';
import { styleText } from 'node:util';

export const COMMANDS = ['/check', '/status', '/help', '/exit'];

export type Turn =
  | { kind: 'empty' | 'help' | 'exit' | 'status' }
  | { kind: 'ask' | 'check' | 'say'; text: string }
  | { kind: 'connect'; path: string; dir: boolean };
export type Session = { principal: string; skillDir: string; connected: Set<string> };
export type Look = { width: number; color: boolean; home: string; keyEnv?: string; keySource?: 'env' | 'file' | 'none'; vendor?: string };
// noNext: the turn was a status, so a next step would only point back at it.
export type Shown = { kind: 'ask' | 'check' | 'status' | 'connect' | 'crash' | 'help'; data: any; secs?: number; label?: string;
  refreshed?: boolean; declined?: boolean; noNext?: boolean };

// ---------------------------------------------------------------- reading a line
/** Shell-style words: single quotes literal, double quotes allow \" \\ \$ \`, a backslash escapes one character. */
function words(input: string): string[] {
  const out: string[] = [];
  let cur = '', quote = '', has = false;
  for (let i = 0; i < input.length; i++) {
    const c = input[i];
    if (quote === "'") { if (c === "'") quote = ''; else cur += c; continue; }
    if (quote === '"') {
      if (c === '"') quote = '';
      else if (c === '\\' && '"\\$`'.includes(input[i + 1] ?? '')) cur += input[++i];
      else cur += c;
      continue;
    }
    if (c === "'" || c === '"') { quote = c; has = true; }
    else if (c === '\\' && i + 1 < input.length) { cur += input[++i]; has = true; }
    else if (/\s/.test(c)) { if (has || cur) out.push(cur); cur = ''; has = false; }
    else { cur += c; has = true; }
  }
  if (has || cur) out.push(cur);
  return out;
}

const pathy = (w: string) => /^(\/|~|\.\.?\/)/.test(w);
const tilde = (p: string, home: string) => (home && (p === home || p.startsWith(home + '/')) ? '~' + p.slice(home.length) : p);
const absolute = (w: string, home: string) => (w === '~' || w.startsWith('~/') ? join(home, w.slice(1)) : resolve(w));

function distance(a: string, b: string): number {
  let row = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const next = [i];
    for (let j = 1; j <= b.length; j++) next[j] = Math.min(row[j] + 1, next[j - 1] + 1, row[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    row = next;
  }
  return row[b.length];
}

/** A line is `?`, `exit`/`quit`, a path, a /command, or a question, in that order. */
export function readLine(line: string, home: string = process.env.HOME ?? ''): Turn {
  const t = line.trim();
  if (!t) return { kind: 'empty' };
  if (t === '?') return { kind: 'help' };
  if (t === 'exit' || t === 'quit') return { kind: 'exit' };
  const ws = /^\/[a-z]+(\s|$)/i.test(t) ? [] : words(t);
  const typed = ws.length > 0 && pathy(ws[0]);
  if (typed && ws.length > 1 && ws.every(pathy)) return { kind: 'say', text: 'One at a time: drag in one folder or note, then the next.' };
  // The words as parsed first, then the line as typed: a path can hold an apostrophe, or be a one-segment folder like /notes.
  const tries = [...(typed ? [ws.length === 1 ? ws[0] : t] : []), ...(pathy(t) ? [t] : [])].map((w) => absolute(w, home));
  const hit = tries.map((p) => [p, statSync(p, { throwIfNoEntry: false })] as const).find(([, st]) => st);
  if (hit || typed) {
    const [p, st] = hit ?? [tries[0], undefined];
    if (!st) return { kind: 'say', text: `Can't find ${tilde(p, home)}. Drag the folder in, or check the path.` };
    if (st.isDirectory() || /\.md$/i.test(p)) return { kind: 'connect', path: p, dir: st.isDirectory() };
    return { kind: 'say', text: `${tilde(p, home)} is not a folder or a Markdown note, so it can't be connected.` };
  }
  const m = /^\/([a-z]+)(?:\s+([\s\S]*))?$/i.exec(t);
  if (!m) return { kind: 'ask', text: t };
  const cmd = '/' + m[1].toLowerCase(), rest = (m[2] ?? '').trim();
  if (cmd === '/help') return { kind: 'help' };
  if (cmd === '/exit') return { kind: 'exit' };
  if (cmd === '/status') return { kind: 'status' };
  if (cmd === '/check') return rest ? { kind: 'check', text: rest } : { kind: 'say', text: 'Usage: /check <a statement to check against your notes>' };
  const [far, near] = COMMANDS.map((c) => [distance(cmd, c), c] as const).sort((a, b) => a[0] - b[0])[0];
  return { kind: 'say', text: `Unknown command ${cmd}.` + (far <= 2 ? ` Did you mean ${near}?` : '') };
}

// ---------------------------------------------------------------- helper calls
/** Stable per path: <folder-slug>-<first 6 hex of sha1(absolute path)>. */
export function pointerName(abs: string): string {
  const slug = basename(abs).replace(/\.md$/i, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return `${slug || 'notes'}-${createHash('sha1').update(abs).digest('hex').slice(0, 6)}`;
}

/** Escapes [ ] * ? so --name matches exactly one file. */
const literalName = (n: string) => n.replace(/[[\]*?]/g, (c) => `[${c}]`);

/** The helper's argv, script path first. Never run through a shell. */
export function helperCall(turn: Turn, s: Session): string[] {
  const ask = [join(s.skillDir, 'ask.py'), '--principal', s.principal, '--json'];
  if (turn.kind === 'ask') return [...ask, '--', turn.text];
  if (turn.kind === 'check') return [...ask, '--claim', turn.text];
  if (turn.kind === 'status') return [...ask, '--status'];
  if (turn.kind !== 'connect') throw new Error(`no helper call for ${turn.kind}`);
  const name = pointerName(turn.path);
  return [join(s.skillDir, 'prepare_bulk.py'), '--root', turn.dir ? turn.path : dirname(turn.path), '--pointer', name,
    '--principal', s.principal, '--writer', 'builtin', '--json',
    ...(turn.dir ? [] : ['--no-recurse', '--name', literalName(basename(turn.path))]),
    ...(s.connected.has(name) ? ['--refresh'] : [])];
}

/** The environment a helper gets: the key from the shell wins over the key file. */
export function childEnv(env: NodeJS.ProcessEnv, keyEnv: string, fileKey: string): NodeJS.ProcessEnv {
  return !keyEnv || env[keyEnv] || !fileKey ? env : { ...env, [keyEnv]: fileKey };
}

/** What a key press does in each mode (prompt, yes/no, hidden key). */
export function keyAction(s: { mode: 'prompt' | 'confirm' | 'secret'; line: string; armed: boolean }, key: string): string {
  if (s.mode === 'confirm') return key === 'enter' ? (/^(y|yes)?$/i.test(s.line.trim()) ? 'yes' : 'no') : key === 'escape' || key === 'ctrl-c' ? 'no' : 'none';
  if (s.mode === 'secret') return key === 'enter' ? 'send' : key === 'escape' || key === 'ctrl-c' ? 'cancel' : 'none';
  if (key === 'escape') return 'clear';
  if (key === 'ctrl-c') return s.line ? 'clear' : s.armed ? 'quit' : 'hint';
  return key === 'ctrl-d' && !s.line ? 'quit' : 'none';
}

// ---------------------------------------------------------------- drawing a reply
export function wrap(text: string, width: number): string[] {
  const out: string[] = [];
  let cur = '';
  for (const w of text.split(/\s+/).filter(Boolean)) {
    if (cur && cur.length + 1 + w.length > width) { out.push(cur); cur = w; } else cur = cur ? cur + ' ' + w : w;
  }
  return cur ? [...out, cur] : out;
}

export const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;
const sentence = (s: string) => (/[.!?]$/.test(s) ? s : s + '.');
const uniq = (xs: string[]) => [...new Set(xs)];

export const HELP = [
  'Ask a question in plain words, or:',
  '  drag a folder of Markdown notes in here  connect it',
  '  /check <statement>  check a statement against your notes',
  '  /status             show what is connected',
  '  /help or ?          show this list',
  '  /exit               leave (Ctrl+D works too)'].join('\n');

export function confirmText(label: string, refresh: boolean, vendor: string, width: number): string {
  const text = `${refresh ? 'Refresh' : 'Connect'} ${label}? It's free and stays on this Mac. When you ask, your question and matching passages go to ${vendor}.`;
  const rows = wrap(text, width);
  const hint = 'enter yes · esc no';
  return rows.length && rows[rows.length - 1].length + 2 + hint.length <= width ? [...rows.slice(0, -1), rows[rows.length - 1] + '  ' + hint].join('\n') : [...rows, hint].join('\n');
}

export function launchLine(version: string, sets: { state: string }[]): string {
  const by = new Map<string, number>();
  for (const s of sets) if (s.state !== 'ready') by.set(s.state, (by.get(s.state) ?? 0) + 1);
  const state = by.size ? [...by].map(([k, n]) => `${n} ${k}`).join(', ') : 'up to date';
  return `Super Jev ${version} · ${plural(sets.length, 'folder')} · ${state} · ? for help`;
}

/** The outcome decides the screen; a connect has none. Whatever the helper reports is drawn, never a calmer screen. */
export function render(shown: Shown, look: Look): string {
  const d = shown.data ?? {}, o: string = d.outcome ?? '';
  const paint = (fmt: string, s: string) => (look.color ? styleText(fmt as any, s, { validateStream: false }) : s);
  const vendor = look.vendor ?? 'TypeSafe';
  const path = (p: string) => tilde(p, look.home);
  const tag = (s: string) => '  ' + paint('dim', s);
  const out: string[] = [];
  let title = '', tone = '', nextLine = '';
  const head = (text: string, color = '') => { title = text; tone = color; };
  const body = (s: string) => out.push('  ' + s);
  const next = (s: string) => { nextLine = s; };
  const quote = (text: string) => { for (const l of wrap(`"${text}"`, look.width - 4)) out.push('    ' + paint('dim', l)); };
  const place = (f: { path: string; line?: number; date?: string; says?: string }) =>
    path(f.path) + (f.line ? ':' + f.line : '') + (f.date ? ` · ${f.date}` : '') + (f.says ? ` · says ${f.says}` : '');
  const keyNext = () => next(look.keySource === 'env' ? `fix ${look.keyEnv ?? 'the key variable'} in your shell, then restart.`
    : look.keySource === 'file' ? 'delete ~/.typesafe-api-key, then run superjev again to paste a new key.' : 'run superjev again to paste your key.');
  const leftOut = (rows: any[]) => uniq((rows ?? []).map((r) => `Left out ${r.count} ${r.what}${r.way_in ? `; ${r.way_in}` : ''}.`)).forEach(body);
  const empty = (setup: boolean) => { head(setup ? 'Not set up yet' : 'Nothing connected yet'); next(setup ? 'run superjev again; setup runs at launch.' : 'drag a folder of Markdown notes in here.'); };
  const crash = (why: string) => { head('Super Jev hit an error', 'red'); if (why) body(why); if (!shown.noNext && shown.kind !== 'status') next('/status, or ask again.'); };
  // Folders the helper did not search, said once and added to every headline, so a partial search never reads as complete.
  const rows: any[] = d.unsearched ?? [];
  const lost = uniq([...rows.map((u) => `${u.root ? basename(u.root) : u.set} not searched (${u.healing ? 'refreshing' : u.state})`),
    ...(d.errors ?? []).filter((e: any) => !rows.some((u) => u.set === e.set)).map((e: any) => `${e.set} not searched (failed)`)]);
  const headline = (text: string) => [text, ...lost].join(' · ');

  if (shown.kind === 'help') return HELP;
  if (shown.kind === 'crash') crash(d.line ?? '');
  else if (shown.kind === 'connect') {
    const word = shown.refreshed ? 'Refreshed' : 'Connected';
    if (shown.declined) head('Not connected');
    else if (d.refused) { head(`Not connected: ${d.refused.why}`, 'red'); next('drag in a smaller folder inside it.'); }
    else {
      head(d.connected ? `${word} ${shown.label}: ${plural(d.connected, 'note')}` : 'Not connected', d.connected ? 'green' : 'red');
      for (const h of d.held ?? []) body(`Held back ${h.path}: ${h.why}`);
      for (const f of d.failed ?? []) body(`${d.connected ? 'Failed ' : ''}${f.path}: ${f.why}`);
      leftOut(d.skipped);
    }
  } else if (o === 'error') {
    const why: Record<string, string> = { 'auth-rejected': `${vendor} rejected the key`, 'no-key': `no ${vendor} key was found`,
      unreachable: `${vendor} can't be reached`, overloaded: `${vendor} is busy` };
    const kind = Object.keys(why).find((k) => (d.errors ?? []).some((e: any) => e.kind === k)) ?? (d.next === 'key' ? 'auth-rejected' : '');
    if (kind) head(`Couldn't search: ${why[kind]}`, 'red');
    if (kind === 'auth-rejected' || kind === 'no-key') keyNext();
    else if (kind) body('Saved answers still work. Try again, or /status.');
    else if (lost.length) { head("Couldn't search", 'red'); lost.forEach((l) => body(sentence(l))); }
    else crash(d.why ?? '');
  } else if (o === 'needs-setup') {
    if (d.next === 'connect' || d.next === 'setup') empty(d.next === 'setup');
    else {
      head('Not sure yet');
      for (const u of rows) body(`${u.root ? basename(u.root) : u.set} ${u.healing ? 'changed; it is refreshing now. Ask again in a moment.' : 'was not refreshed, so it was not searched.'}`);
      leftOut(d.left_out);
      if (!rows.length && !(d.left_out ?? []).length && d.why) body(d.why);
      if (d.next === 'include') next('use the way in above, then ask again.');
      else if (rows.some((u) => !u.healing)) next('drag the folder in again to refresh it.');
    }
  } else if (o === 'not-supported') {
    head('Not answered'); body(d.why ?? '');
    if (shown.kind !== 'status') next('ask one focused question.');
  } else if (shown.kind === 'status') {
    const sets: any[] = d.sets ?? [];
    if (d.next === 'setup' || !sets.length) empty(d.next === 'setup');
    else {
      head(`${plural(sets.length, 'folder')} connected`);
      for (const s of sets) body(`${s.name}  ${(s.roots ?? []).map(path).join(', ')}  ${plural(s.notes ?? 0, 'note')}  ${s.state}`);
    }
  } else if (d.claim) {
    const c = d.claim, saved = d.saved ? ' · saved, proof file unchanged' : '';
    if (c.verdict === 'NOT FOUND') { head(headline(`NOT FOUND in the ${plural(c.read ?? 0, 'note')} read`)); body('It may be in a note not read or not connected.'); }
    else {
      head(headline(c.verdict + saved), c.verdict === 'TRUE' ? 'green' : c.verdict === 'FALSE' ? 'red' : '');
      if (c.proof) { body(place(c.proof)); if (c.proof.text) quote(c.proof.text); }
      for (const f of c.files ?? []) body(place(f));
      if (c.verdict === 'CONFLICT') body('Read both before relying on either.');
    }
  } else if (o === 'found') {
    const files: any[] = d.files ?? [], skills: any[] = d.skills ?? [];
    const found = [skills.length && plural(skills.length, 'skill'), files.length && plural(files.length, 'note')].filter(Boolean).join(' and ') || '0 notes';
    head(d.saved ? 'Saved answer · notes unchanged' : headline(`Found ${found}`), d.saved ? 'green' : '');
    if (d.saved?.by === 'you' && d.saved.answer) for (const l of wrap(d.saved.answer, look.width - 2)) body(l);
    for (const s of skills) body(`${s.name}  ${path(s.path)}` + (s.guess ? tag('guess') : ''));
    files.forEach((f, i) => {
      body(`${i + 1} ${place(f)}` + (f.tier === 'possible' ? tag('possible') : f.tier === 'unchecked' ? tag('not checked') : ''));
      if (i === 0 && f.text) quote(f.text);
    });
    if (d.leans_none) body('Jev leans toward none of these; the answer may not be here.');
    if (d.saved_now) body('Saved for next time.');
    leftOut(d.left_out);
  } else if (o === 'not-found') {
    head('Not in your notes');
    if (d.searched) body(`Searched ${plural(d.searched.sets, 'folder')} (${plural(d.searched.notes, 'note')}); nothing matched.`);
    body("That doesn't prove it's nowhere: it may be in a folder you haven't connected.");
    next('drag in the folder that has it.');
  } else crash(d.why ?? '');
  if (d.skills_off && shown.kind !== 'status') body(sentence(d.skills_off));
  const time = shown.secs === undefined ? '' : paint('dim', ` · ${shown.secs.toFixed(1)}s`);
  return [(tone ? paint(tone, `• ${title}`) : `• ${title}`) + time, ...out, ...(nextLine ? ['  ' + paint('dim', 'Next: ' + nextLine)] : [])].join('\n');
}
