// The pure core of the terminal app: read a typed line, build the helper call, draw the helper's reply.
// Nothing here talks to a helper or a terminal. readLine only stats a path on disk, read-only.
import { statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { basename, dirname, join, resolve } from 'node:path';
import { styleText, stripVTControlCharacters } from 'node:util';

export const COMMANDS = ['/check', '/right', '/wrong', '/status', '/help', '/exit'];
/** How a user gets back to the window: the one wording every screen and message uses. */
export const OPEN = 'open the window (npm run jev, or superjev with no words)';

export type Turn =
  | { kind: 'empty' | 'help' | 'version' | 'exit' | 'status' | 'wrong' }
  | { kind: 'right'; rank: number }
  | { kind: 'ask' | 'check' | 'say'; text: string }
  | { kind: 'connect'; path: string; dir: boolean; pointer?: string /* the engine's own name for a set to refresh */ };
export type Session = { principal: string; skillDir: string; connected: Set<string>; last?: string /* the last question asked in this session */ };
export type Look = { width: number; color: boolean; home: string; keyEnv?: string; keySource?: 'env' | 'file' | 'none'; vendor?: string;
  door?: boolean /* the one-shot door: no window is open yet */ };
// noNext: no Next line: the turn was a status (a next step would only point back at it), or the window is about to do the fix itself.
export type Shown = { kind: 'ask' | 'check' | 'right' | 'wrong' | 'status' | 'connect' | 'crash' | 'help'; data: any; secs?: number; label?: string;
  refreshed?: boolean; declined?: boolean; noNext?: boolean; stopped?: boolean; hint?: boolean };

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

/** A first word shaped like an option (`-x`, `--name`) is never a question: --help and -h, --version, else unknown. A bullet (`- item`) or a number (`-5`) is text. */
const option = (w: string): Turn => w === '--help' || w === '-h' ? { kind: 'help' } : w === '--version' ? { kind: 'version' }
  : { kind: 'say', text: `Unknown option ${w}. Try --help.` };

/** A line is `?`, `exit`/`quit`, an option, a path, a /command, or a question, in that order. */
export function readLine(line: string, home: string = process.env.HOME ?? ''): Turn {
  const t = line.trim();
  if (!t) return { kind: 'empty' };
  if (t === '?') return { kind: 'help' };
  if (t === 'exit' || t === 'quit') return { kind: 'exit' };
  if (/^--?[A-Za-z]/.test(t)) return option(t.split(/\s+/)[0]);
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
  if (cmd === '/wrong') return { kind: 'wrong' };
  if (cmd === '/right') {
    const n = rest === '' ? 1 : /^\d$/.test(rest) ? Number(rest) : 0;
    return n >= 1 && n <= 5 ? { kind: 'right', rank: n } : { kind: 'say', text: 'Usage: /right [n]   n is the note number, 1 to 5' };
  }
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
  if (turn.kind === 'right') return [...ask, '--approve', s.last ?? '', '--rank', String(turn.rank)];
  if (turn.kind === 'wrong') return [...ask, '--miss', s.last ?? ''];
  if (turn.kind !== 'connect') throw new Error(`no helper call for ${turn.kind}`);
  const name = turn.pointer ?? pointerName(turn.path);
  return [join(s.skillDir, 'prepare_bulk.py'), '--root', turn.dir ? turn.path : dirname(turn.path), '--pointer', name,
    '--principal', s.principal, '--writer', 'builtin', '--json',
    ...(turn.dir ? [] : ['--no-recurse', '--name', literalName(basename(turn.path))]),
    ...(turn.pointer || s.connected.has(name) ? ['--refresh'] : [])];
}

/** The environment a helper gets: the key from the shell wins over the key file. */
export function childEnv(env: NodeJS.ProcessEnv, keyEnv: string, fileKey: string): NodeJS.ProcessEnv {
  return !keyEnv || env[keyEnv] || !fileKey ? env : { ...env, [keyEnv]: fileKey };
}

/** What a key press does in each mode (prompt, yes/no, hidden key). */
export function keyAction(s: { mode: 'prompt' | 'confirm' | 'secret'; line: string; armed: boolean }, key: string): string {
  if (s.mode === 'confirm') return key === 'enter' ? (/^(y|yes)?$/i.test(s.line.trim()) ? 'yes' : 'no') : key === 'escape' || key === 'ctrl-c' ? 'no' : 'none';
  if (s.mode === 'secret') return key === 'enter' ? 'send' : key === 'escape' || key === 'ctrl-c' || key === 'ctrl-d' ? 'cancel' : 'none';
  if (key === 'escape') return 'clear';
  return key === 'ctrl-c' ? (s.line ? 'clear' : s.armed ? 'quit' : 'hint') : 'none'; // Ctrl+D at the prompt is readline's own close
}

/** The one fix the window can offer for an answer (the fix table, DESIGN 4.3), or null when there is nothing the app can do:
 *  a rejected key from the key file gets a new key; a stale folder that nobody is refreshing gets a refresh. */
export function fixOf(d: any, keySource: string): { kind: 'key' } | { kind: 'refresh'; rows: any[] } | null {
  if (d?.outcome === 'error' && keySource === 'file' && (d.errors ?? []).some((e: any) => e.kind === 'auth-rejected')) return { kind: 'key' };
  const rows = (d?.unsearched ?? []).filter((u: any) => !u.healing && u.set && u.root);
  return d?.outcome === 'needs-setup' && d.next === 'refresh' && rows.length ? { kind: 'refresh', rows } : null;
}

// ---------------------------------------------------------------- drawing a reply
/** The one layout rule. A row is a list of pieces that never break (a path with its :line, a date, a tag; in prose, each word).
 *  They fill lines of at most `width` visible columns, so a line breaks only between pieces and a piece wider than the
 *  window stays whole. A piece that starts with a space asks for a wider gap, dropped when it starts a line. */
export function pack(pieces: string[], width: number, first = '  ', hang = '    '): string[] {
  const rows: string[] = [];
  let cur = '';
  for (const p of pieces) {
    const indent = rows.length ? hang : first;
    if (cur && stripVTControlCharacters(indent + cur + ' ' + p).length > width) { rows.push(indent + cur); cur = p.trimStart(); }
    else cur = cur ? cur + ' ' + p : p.trimStart();
  }
  return cur ? [...rows, (rows.length ? hang : first) + cur] : rows;
}
/** Prose: every word is a piece. */
export const prose = (s: string) => s.split(/\s+/).filter(Boolean);
const parts = (x: string | string[]) => (typeof x === 'string' ? prose(x) : x);
/** Text that follows a dot (` · says X`): the dot stays with its first word. */
const after = (s: string) => { const [w, ...rest] = prose(s); return [`· ${w}`, ...rest]; };
const gap = (s: string) => ' ' + s;

export const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;
const sentence = (s: string) => (/[.!?]$/.test(s) ? s : s + '.');
const uniq = (xs: string[]) => [...new Set(xs)];

export const HELP = [
  'Ask a question in plain words, or:',
  '  drag a folder in    connect its Markdown notes',
  '  /check <statement>  check it against your notes',
  '  /right [n]          the last answer was right: save it now',
  '  /wrong              the last answer was wrong: forget it',
  '  /status             show what is connected',
  '  /help or ?          show this list',
  '  /exit               leave (Ctrl+D works too)'].join('\n');
/** What `superjev --help` leads with, before the list. */
export const USAGE = 'Usage: superjev "your question"\n       superjev            open the window';

export function confirmText(label: string, refresh: boolean, vendor: string, width: number): string {
  const text = `${refresh ? 'Refresh' : 'Connect'} ${label}? It's free and stays on this Mac. When you ask, your question and matching passages go to ${vendor}.`;
  return pack([...prose(text), gap('enter yes · esc no')], width, '  ', '  ').join('\n');
}

export function launchLine(version: string, sets: { state: string }[], width: number): string {
  const by = new Map<string, number>();
  for (const s of sets) if (s.state !== 'ready') by.set(s.state, (by.get(s.state) ?? 0) + 1);
  const state = by.size ? [...by].map(([k, n]) => `${n} ${k}`).join(', ') : 'up to date';
  return pack([`Super Jev ${version}`, `· ${plural(sets.length, 'folder')}`, `· ${state}`, '· ? for help'], width, '', '').join('\n');
}

// What the engine left out and why it refused, worded from its stable `kind` (PR 2c). A kind not listed shows the engine's own words.
const LEFT: Record<string, (n: number) => string> = {
  link: (n) => `${plural(n, 'linked note')} pointing outside this folder`,
  name: (n) => `${plural(n, 'note')} with backup or key-style names; they are never connected`,
  folder: (n) => `${plural(n, 'note')} in folders skipped by default; drag a folder in on its own to connect it`,
  folder_other: (n) => `${plural(n, 'file')} of other types in folders skipped by default`,
  hidden: (n) => `${plural(n, 'hidden note')}; rename one to connect it`,
  dataset: (n) => `${plural(n, 'file')} in prepared dataset copies`,
  test: (n) => plural(n, 'test or scratch file'),
  worktree: (n) => `${plural(n, 'file')} in git worktree copies`,
  empty: (n) => plural(n, 'empty note'),
  types: (n) => `${plural(n, 'file')} of other types; only .md notes connect`,
};
const num = (n: number) => n.toLocaleString('en-US');
const REFUSED: Record<string, (r: any) => string | undefined> = {
  too_many: (r) => Number.isInteger(r.count) && Number.isInteger(r.max) ? `${num(r.count)} notes is more than one connect takes (${num(r.max)}).` : undefined,
  not_a_folder: () => 'That is not a folder.',
  not_markdown: () => "This folder was connected with other file types, so it can't be refreshed here.",
  usage: () => 'That request was not accepted.',
};

/** The outcome decides the screen; a connect has none. Whatever the helper reports is drawn, never a calmer screen. */
export function render(shown: Shown, look: Look): string {
  const d = shown.data ?? {}, o: string = d.outcome ?? '';
  const paint = (fmt: string, s: string) => (look.color && fmt ? styleText(fmt as any, s, { validateStream: false }) : s);
  const vendor = look.vendor ?? 'TypeSafe';
  const path = (p: string) => tilde(p, look.home);
  const tag = (s: string) => gap(paint('dim', s));
  const out: string[] = [];
  let title: string[] = [], tone = '', nextLine = '';
  const head = (text: string | string[], color = '') => { title = parts(text); tone = color; };
  // Every row goes through pack: prose as words, a path row as its parts. A row that fits stays as it is.
  const lay = (s: string | string[], fmt = '', first = '  ', hang = typeof s === 'string' ? '  ' : '    ') => pack(parts(s), look.width, first, hang).map((l) => paint(fmt, l));
  const body = (s: string | string[]) => out.push(...lay(s));
  const next = (s: string) => { nextLine = s; };
  // A step that is a drag: at the one-shot door there is no window yet, so the way in comes first.
  const drag = (s: string) => next(look.door ? `${OPEN}, then ${s}` : s);
  const quote = (text: string) => out.push(...lay(`"${text}"`, 'dim', '    ', '    '));
  const place = (f: { path: string; line?: number; date?: string; says?: string }, lead = '') =>
    [lead + path(f.path) + (f.line ? ':' + f.line : ''), ...(f.date ? [`· ${f.date}`] : []), ...(f.says ? [`· says ${f.says}`] : [])];
  const keyNext = () => next(look.keySource === 'env' ? `fix ${look.keyEnv ?? 'the key variable'} in your shell, then restart.`
    : look.keySource === 'file' ? `delete ~/.typesafe-api-key, then ${OPEN} to paste a new key.` : `${OPEN} to paste your key.`);
  // A saved answer as its writer laid it out: a blank line stays blank, each line keeps its own indent and wraps under itself.
  const saved = (answer: string) => answer.replace(/^\s*\n/, '').trimEnd().split('\n').flatMap((l) => {
    const at = '  ' + /^[ \t]*/.exec(l)![0].replace(/\t/g, '    ');
    return l.trim() ? lay(l, '', at, at) : [''];
  });
  // Notes that share a reason are said once: the reason, then each path whole on its own line.
  const said = (rows: any[], lead: (n: number) => string) => {
    const by = new Map<string, string[]>();
    for (const r of rows ?? []) by.set(r.why, [...(by.get(r.why) ?? []), path(r.path)]);
    for (const [why, paths] of by) { body(`${lead(paths.length)} ${why}`); for (const p of paths) out.push(...lay([p], '', '    ', '    ')); }
  };
  const leftOut = (rows: any[]) => uniq((rows ?? []).map((r) => LEFT[r.kind] && Number.isInteger(r.count) ? `Left out ${LEFT[r.kind](r.count)}.`
    : `Left out ${r.count} ${r.what}${r.way_in ? `; ${r.way_in}` : ''}.`)).forEach((l) => body(l));
  const empty = (setup: boolean) => {
    head(setup ? 'Not set up yet' : 'Nothing connected yet');
    if (setup) next(`${OPEN}; setup runs there.`); else drag('drag a folder of Markdown notes in here.');
  };
  const retry = () => { if (!shown.noNext && shown.kind !== 'status') next('/status, or ask again.'); };
  const crash = (why: string) => { head('Super Jev hit an error', 'red'); if (why) body(why); retry(); };
  // Folders the helper did not search, said once and added to every headline, so a partial search never reads as complete.
  const rows: any[] = d.unsearched ?? [];
  const lost = uniq([...rows.map((u) => `${u.root ? basename(u.root) : u.set} not searched (${u.healing ? 'refreshing' : u.state})`),
    ...(d.errors ?? []).filter((e: any) => !rows.some((u) => u.set === e.set)).map((e: any) => `${e.set} not searched (failed)`)]);
  const headline = (text: string | string[]) => [...parts(text), ...lost.flatMap(after)];

  if (shown.kind === 'help') return HELP;
  if (shown.stopped) { // Esc or Ctrl+C while a helper ran: a connect may have written some of its notes
    const finish = shown.kind === 'connect' ? ` Some notes may be connected; ${look.door ? OPEN + ', then ' : ''}drag the folder in again to finish.` : '';
    return pack(prose('Stopped.' + finish), look.width, '', '').join('\n');
  }
  if (shown.kind === 'crash') crash(d.line ?? '');
  else if (shown.kind === 'connect') {
    const word = shown.refreshed ? 'Refreshed' : 'Connected', none = shown.refreshed ? 'Not refreshed' : 'Not connected'; // a refresh leaves the folder connected
    if (shown.declined) head(none);
    else if (d.refused) {
      head(none, 'red');
      body(REFUSED[d.refused.kind]?.(d.refused) ?? d.refused.why ?? '');
      if (d.refused.kind === 'too_many') drag('drag in a smaller folder inside it.');
    } else {
      head(d.connected ? `${word} ${shown.label}: ${plural(d.connected, 'note')}` : none, d.connected ? 'green' : 'red');
      said(d.held, (n) => `Held back ${plural(n, 'note')}:`);
      said(d.failed, () => 'Failed:');
      leftOut(d.skipped);
    }
  } else if (shown.kind === 'right') {
    if (d.done) head('Saved. Ask it again and it comes back at once.', 'green');
    else {
      head('Not saved.', 'red');
      const why = d.why ?? '';
      if (/possible-tier/.test(why)) body('One of these notes is only a possible match. Use /check to test a claim against them.');
      else if (/out of range/.test(why)) body('There is no note with that number.');
      else if (/no (prior lookup|confirmed top)/.test(why)) body('There is nothing to save. Ask a question first.');
      else if (!/--/.test(why)) body(sentence(why));
    }
  }
  else if (shown.kind === 'wrong') head(d.removed?.length ? 'Forgotten. Searching fresh…' : "Noted. It won't be saved.");
  else if (o === 'error') {
    const why: Record<string, string> = { 'auth-rejected': `${vendor} rejected the key`, 'no-key': `no ${vendor} key was found`,
      unreachable: `${vendor} can't be reached`, overloaded: `${vendor} is busy` };
    const kind = Object.keys(why).find((k) => (d.errors ?? []).some((e: any) => e.kind === k)) ?? (d.next === 'key' ? 'auth-rejected' : '');
    if (kind) head(`Couldn't search: ${why[kind]}`, 'red');
    if (kind === 'auth-rejected' || kind === 'no-key') keyNext();
    else if (kind) body('Saved answers still work. Try again, or /status.');
    else if (lost.length) { head("Couldn't search", 'red'); lost.forEach((l) => body(sentence(l))); retry(); }
    else crash(d.why ?? '');
  } else if (o === 'needs-setup') {
    if (d.next === 'connect' || d.next === 'setup') empty(d.next === 'setup');
    else {
      head('Not sure yet');
      for (const u of rows) body(`${u.root ? basename(u.root) : u.set} ${u.healing ? 'changed; it is refreshing now. Ask again in a moment.' : 'was not refreshed, so it was not searched.'}`);
      leftOut(d.left_out);
      if (!rows.length && !(d.left_out ?? []).length && d.why) body(d.why);
      if (d.next === 'include') next('use the way in above, then ask again.');
      else if (rows.some((u) => !u.healing)) drag('drag the folder in again to refresh it.');
    }
  } else if (o === 'not-supported') {
    head('Not answered'); body(d.why ?? '');
    if (shown.kind !== 'status' && d.next === 'rephrase') next('ask one focused question.');
  } else if (shown.kind === 'status') {
    const sets: any[] = d.sets ?? [];
    if (d.next === 'setup' || !sets.length) empty(d.next === 'setup');
    else {
      head(`${plural(sets.length, 'folder')} connected`);
      const at = (s: any) => (s.roots ?? []).map(path).join(', ') || s.name;
      // Two sets with the same folder (a folder, then one note in it) are told apart by their name.
      for (const s of sets) body([at(s), gap(plural(s.notes ?? 0, 'note')), gap(s.state), ...(sets.filter((x) => at(x) === at(s)).length > 1 ? [tag(s.name)] : [])]);
    }
  } else if (d.claim) {
    const c = d.claim;
    if (c.verdict === 'NOT FOUND') { head(headline(`NOT FOUND in the ${plural(c.read ?? 0, 'note')} read`)); body('It may be in a note not read or not connected.'); }
    else {
      head(headline([c.verdict, ...(d.saved ? after('saved, proof file unchanged') : [])]), c.verdict === 'TRUE' ? 'green' : c.verdict === 'FALSE' ? 'red' : '');
      if (c.proof) { body(place(c.proof)); if (c.proof.text) quote(c.proof.text); }
      for (const f of c.files ?? []) body(place(f));
      if (c.verdict === 'CONFLICT') body('Read both before relying on either.');
    }
  } else if (o === 'found') {
    const files: any[] = d.files ?? [], skills: any[] = d.skills ?? [];
    const found = [skills.length && plural(skills.length, 'skill'), files.length && plural(files.length, 'note')].filter(Boolean).join(' and ') || '0 notes';
    head(d.saved ? ['Saved answer', ...after('notes unchanged')] : headline(`Found ${found}`), d.saved ? 'green' : '');
    if (d.saved?.by === 'you' && d.saved.answer) out.push(...saved(String(d.saved.answer)));
    for (const s of skills) body([s.name, gap(path(s.path)), ...(s.guess ? [tag('guess')] : [])]);
    files.forEach((f, i) => {
      body([...place(f, `${i + 1} `), ...(f.tier === 'possible' ? [tag('possible')] : f.tier === 'unchecked' ? [tag('not checked')] : [])]);
      if (i === 0 && f.text) quote(f.text);
    });
    if (d.leans_none) body('Jev leans toward none of these; the answer may not be here.');
    if (d.saved_now) body('Saved for next time.');
    else if (shown.hint && files.length && !d.saved && !files.some((f) => f.tier === 'possible')) body('Open them to check. /right saves this answer.');
    leftOut(d.left_out);
  } else if (o === 'not-found') {
    head('Not in your notes');
    if (d.searched) body(`Searched ${plural(d.searched.sets, 'folder')} (${plural(d.searched.notes, 'note')}); nothing matched.`);
    body("That doesn't prove it's nowhere: it may be in a folder you haven't connected.");
    drag('drag in the folder that has it.');
  } else crash(d.why ?? '');
  if (d.skills_off && shown.kind !== 'status') body(sentence(d.skills_off).replace(/^./, (c) => c.toUpperCase()));
  const time = shown.secs === undefined ? [] : [paint('dim', `· ${shown.secs.toFixed(1)}s`)];
  return [...pack([...title.map((w) => paint(tone, w)), ...time], look.width, paint(tone, '• '), '  '), ...out,
    ...(nextLine && !shown.noNext ? lay('Next: ' + nextLine, 'dim') : [])].join('\n');
}
