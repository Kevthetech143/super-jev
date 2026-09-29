// Config + pure-logic helpers for the `superjev` chat CLI. Kept dependency-free
// (no clack, no child_process) so it is cheap to unit test in isolation from the
// interactive TUI in jev-chat-cli.ts.
import { mkdirSync, chmodSync, existsSync, readFileSync, writeFileSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, dirname, basename } from 'node:path';
import { createHash } from 'node:crypto';

export type SuperJevConfig = {
  typesafeApiKey?: string;
  principal?: string;
  folders?: string[];
};

/** XDG-respecting config dir: $XDG_CONFIG_HOME/superjev or ~/.config/superjev. */
export function configDir(): string {
  const base = process.env.XDG_CONFIG_HOME || join(homedir(), '.config');
  return join(base, 'superjev');
}

export function configPath(): string {
  return join(configDir(), 'config.json');
}

/** Loads the config, or {} if none exists yet / it fails to parse. Never throws. */
export function loadConfig(path: string = configPath()): SuperJevConfig {
  try {
    if (!existsSync(path)) return {};
    const raw = readFileSync(path, 'utf8');
    const parsed = JSON.parse(raw);
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

/** Writes the config as 0600 (owner read/write only), creating the parent dir
 * (also locked to 0700) if needed. The API key never touches stdout/stderr. */
export function saveConfig(config: SuperJevConfig, path: string = configPath()): void {
  const dir = join(path, '..');
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  // mkdir's mode only applies on create; lock a pre-existing dir too.
  chmodSync(dir, 0o700);
  // writeFileSync's mode only applies on create; lock an existing file BEFORE
  // the key is written into it, not after.
  if (existsSync(path)) chmodSync(path, 0o600);
  writeFileSync(path, JSON.stringify(config, null, 2), { mode: 0o600 });
  chmodSync(path, 0o600);
}

export function configFileMode(path: string = configPath()): number {
  return statSync(path).mode & 0o777;
}

export function hasApiKey(config: SuperJevConfig): boolean {
  return typeof config.typesafeApiKey === 'string' && config.typesafeApiKey.length > 0;
}

// ---------------------------------------------------------------------------
// Canned small-talk replies -- pre-installed answers so the chat feels alive
// with zero lookups/API calls for the common openers.
// ---------------------------------------------------------------------------
const CANNED: Array<{ patterns: RegExp[]; reply: string }> = [
  { patterns: [/^hi$/i, /^hey$/i, /^hello$/i, /^yo$/i], reply: 'Hey, I\'m Super Jev. Ask me anything and I\'ll check what we already know first.' },
  { patterns: [/^who are you$/i, /^what are you$/i], reply: 'I\'m Super Jev -- a cache-first lookup chat over your connected notes and files. Fast answers when we\'ve seen the question before, honest misses when we haven\'t.' },
  { patterns: [/^help$/i, /^what can you do$/i], reply: 'Ask a question and I\'ll look it up. Slash commands: /help /setup /folders /quit.' },
  { patterns: [/^thanks$/i, /^thank you$/i, /^thx$/i], reply: 'Anytime.' },
];

/** Returns a canned reply for common small talk, or null if the input should
 * go to a real lookup. Case-insensitive, tolerant of trailing punctuation. */
export function cannedReply(input: string): string | null {
  const trimmed = input.trim().replace(/[!.?]+$/, '');
  if (!trimmed) return null;
  for (const { patterns, reply } of CANNED) {
    if (patterns.some((p) => p.test(trimmed))) return reply;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Slash command parsing
// ---------------------------------------------------------------------------
export type SlashCommand = { command: 'help' | 'setup' | 'folders' | 'quit'; args: string[] };

const SLASH_COMMANDS: SlashCommand['command'][] = ['help', 'setup', 'folders', 'quit'];

/** Parses a leading-slash command line, or null if the input is not a slash
 * command (plain chat, or an unrecognized slash word -- caller decides how to
 * report that). Returns { command: null-like } via throwing is avoided: an
 * unrecognized `/word` still parses, callers check membership themselves. */
export function parseSlashCommand(input: string): { command: string; args: string[] } | null {
  const trimmed = input.trim();
  if (!trimmed.startsWith('/')) return null;
  const parts = trimmed.slice(1).split(/\s+/).filter(Boolean);
  const command = (parts[0] || '').toLowerCase();
  return { command, args: parts.slice(1) };
}

export function isKnownSlashCommand(command: string): command is SlashCommand['command'] {
  return (SLASH_COMMANDS as string[]).includes(command);
}

/** argv for an ask.py lookup. ask.py dispatches on a leading `--miss`/`--add`/
 * `--approve`/`--principal`/`--no-auto` token, so user text that starts with a
 * dash is stripped of its leading dashes to stay a plain question. */
export function askLookupArgs(principal: string, question: string): string[] {
  const q = question.replace(/^[\s-]+/, '') || '?';
  return ['--principal', principal, q];
}

export const MISS_LINE = "Super Jev: I didn't have this. Want me to find it by hand and save it for next time?";

// ---------------------------------------------------------------------------
// Miss-with-candidates parsing -- ask.py's `cached` action can miss (no
// approved answer) while still surfacing ranked candidate files, printed as
// lines like " 0.85  /path/to/file.md  [pointer-name]  possible-note". The
// chat CLI must not dump that raw line to the user: it turns it into a real
// short reply (top file name + one-line why).
// ---------------------------------------------------------------------------
export type MissCandidate = { score: number; path: string; pointer: string };

const CANDIDATE_LINE = /^\s*([\d.]+)\s+(\S+)\s+\[([^\]]+)\]/;

/** Top ranked candidate from a miss (no CACHE HIT) ask.py run, or null when
 * there truly are none (no-candidates, all pointers errored, etc). Never
 * called on a cache hit -- callers check parseHit first. */
export function parseMissCandidate(stdout: string): MissCandidate | null {
  if (stdout.startsWith('CACHE HIT')) return null;
  for (const line of stdout.split('\n')) {
    const m = line.match(CANDIDATE_LINE);
    if (m) return { score: Number(m[1]), path: m[2], pointer: m[3] };
  }
  return null;
}

/** Short human reply for a miss-with-candidates: top file name + one-line why
 * -- never the raw score/pointer line. */
export function formatMissCandidateReply(candidate: MissCandidate): string {
  return `Top file: ${candidate.path} -- closest match we found (score ${candidate.score.toFixed(2)}), but no approved answer is saved for this yet.`;
}

// ---------------------------------------------------------------------------
// Setup-missing parsing -- when a principal has no connected pointers at all,
// ask.py's lookup() starts with "OUTCOME: needs-setup - ...; next: <command>"
// (exit 4), before it ever reaches the "not-found" miss report. Show that block instead of
// the generic MISS_LINE so the user knows this is a setup problem, not a
// real miss.
// ---------------------------------------------------------------------------
export function parseSetupMissing(stdout: string): string[] | null {
  const lines = stdout.split('\n');
  const idx = lines.findIndex((l) => l.trim().startsWith('OUTCOME: needs-setup'));
  return idx === -1 ? null : [lines[idx].trim()];
}

// ---------------------------------------------------------------------------
// Pointer-error parsing -- when ask.py's providers themselves fail (auth
// errors, network errors, etc.) every pointer can error out with no
// candidates and no "no-candidates" miss report either; ask.py prints
// an "OUTCOME: error - ..." line first, then the "[pointer] error: ..." lines. Surface that instead of
// letting the chat CLI look like a silent, unexplained miss.
// ---------------------------------------------------------------------------
const POINTER_ERROR_LINE = /^\[[^\]]+\]\s+error:/;

export function parseErrorReport(stdout: string): string[] | null {
  const lines = stdout.split('\n');
  const errorLines = lines.filter((l) => POINTER_ERROR_LINE.test(l.trim())).slice(0, 3);
  const outcome = lines.find((l) => l.trim().startsWith('OUTCOME: error'));
  if (!errorLines.length && !outcome) return null;
  return outcome ? [outcome.trim(), ...errorLines] : errorLines;
}

// ---------------------------------------------------------------------------
// Panel-check parsing -- after /setup saves a principal, the chat CLI runs a
// cheap local `dispatch.py memory --principal X` (panel action, no network)
// to warn right away if that principal has no connected pointers yet (e.g. a
// principal name typo or case mismatch -- pointers are matched exact-case).
// ---------------------------------------------------------------------------
export function parseAnyPointers(stdout: string): boolean | null {
  try {
    const parsed = JSON.parse(stdout);
    if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.pointers)) return null;
    return parsed.pointers.length > 0;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// True-miss report parsing -- ask.py's no-candidates miss prints a
// "What was searched:" / "Next step (pick one):" block (see ask.py's
// miss_report()) before its closing voice line. The chat CLI shows that
// block to the user instead of making a paid live call that cannot answer
// anything on a true miss. Returns null if ask.py's output predates that
// block (older ask.py) so callers can fall back cleanly.
// ---------------------------------------------------------------------------
export function parseMissReport(stdout: string): string[] | null {
  const lines = stdout.split('\n');
  const startIdx = lines.findIndex((l) => l.trim() === 'What was searched:');
  if (startIdx === -1) return null;
  const report = lines.slice(startIdx).filter((l) => l.trim() !== MISS_LINE.trim());
  while (report.length && report[report.length - 1].trim() === '') report.pop();
  return report.length ? report : null;
}

// ---------------------------------------------------------------------------
// Drag-and-drop onboarding -- macOS terminals paste a dropped file/folder's
// path as plain text: shell-escaped ("My\ Folder/x.md"), quoted
// ("My Folder/x.md"), or several paths separated by spaces for a multi-
// select drop. Detect that shape before treating a line as a question, then
// connect it through the existing prepare_bulk.py bulk connector.
// ---------------------------------------------------------------------------

/** Splits a line into shell-style tokens: backslash escapes the next char,
 * single quotes are fully literal, double quotes allow \" \\ \$ \` escapes,
 * unescaped whitespace separates tokens. Good enough for what macOS
 * terminals paste on a drop -- not a full shell parser. */
export function splitPathTokens(input: string): string[] {
  const tokens: string[] = [];
  let cur = '';
  let inSingle = false;
  let inDouble = false;
  let i = 0;
  while (i < input.length) {
    const c = input[i];
    if (inSingle) {
      if (c === "'") inSingle = false; else cur += c;
      i++; continue;
    }
    if (inDouble) {
      if (c === '"') inDouble = false;
      else if (c === '\\' && i + 1 < input.length && '"\\$`'.includes(input[i + 1])) { cur += input[i + 1]; i += 2; continue; }
      else cur += c;
      i++; continue;
    }
    if (c === "'") { inSingle = true; i++; continue; }
    if (c === '"') { inDouble = true; i++; continue; }
    if (c === '\\' && i + 1 < input.length) { cur += input[i + 1]; i += 2; continue; }
    if (/\s/.test(c)) {
      if (cur.length) { tokens.push(cur); cur = ''; }
      i++; continue;
    }
    cur += c; i++;
  }
  if (cur.length) tokens.push(cur);
  return tokens;
}

function expandTilde(p: string): string {
  if (p === '~') return homedir();
  if (p.startsWith('~/')) return join(homedir(), p.slice(2));
  return p;
}

/** A dropped-path token must be unambiguously a path, not a word that merely
 * happens to name something in the cwd: an absolute path (`/...`, more than
 * just `/`) or a `~/...` home-relative path. Bare `~`, `.`, `..`, `/`, and
 * plain relative words like "docs" are never treated as a drop, even if they
 * exist on disk. */
function looksLikeDropPathToken(raw: string): boolean {
  if (raw === '/' || raw === '~' || raw === '.' || raw === '..') return false;
  if (raw.startsWith('~/')) return true;
  return raw.startsWith('/') && raw.length > 1;
}

export type DroppedPath = { raw: string; path: string; isDirectory: boolean };

/** Detects whether the WHOLE input is one or more existing local paths
 * (after unescaping/unquoting/tilde-expansion) -- the shape a terminal
 * drag-drop pastes. Returns null (treat as a normal question) unless every
 * token is shaped like an absolute or ~/-prefixed path AND actually exists
 * on disk -- a single non-path-shaped or non-existent token anywhere in the
 * line means it's not a clean drop. `existsFn`/`statFn` are injectable for
 * tests. */
export function detectDroppedPaths(
  input: string,
  existsFn: (p: string) => boolean = existsSync,
  statFn: (p: string) => { isDirectory(): boolean } = statSync,
): DroppedPath[] | null {
  const trimmed = input.trim();
  if (!trimmed) return null;
  const tokens = splitPathTokens(trimmed);
  if (!tokens.length) return null;
  if (!tokens.every(looksLikeDropPathToken)) return null;
  const resolved = tokens.map((raw) => ({ raw, path: expandTilde(raw) }));
  const allExist = resolved.every(({ path }) => {
    try { return existsFn(path); } catch { return false; }
  });
  if (!allExist) return null;
  return resolved.map(({ raw, path }) => {
    let isDirectory = false;
    try { isDirectory = statFn(path).isDirectory(); } catch { isDirectory = false; }
    return { raw, path, isDirectory };
  });
}

/** Slug for one pointer-name component: lowercase, alnum and dashes only. */
export function slugify(name: string): string {
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return slug || 'drop';
}

/** `<principal>-drop-<slug>-<6hex>` pointer name for a dropped file or
 * folder. The trailing 6 hex chars are a short sha256 of the absolute
 * location being connected, so two different folders/files that happen to
 * share a basename (e.g. two "notes.md") never collide on the same pointer
 * name and silently overwrite each other. */
export function buildDropPointerName(principal: string, label: string, location: string): string {
  const hash = createHash('sha256').update(location).digest('hex').slice(0, 6);
  return `${slugify(principal)}-drop-${slugify(label)}-${hash}`;
}

/** Escapes fnmatch-magic characters (`[ ] * ?`) in a filename so
 * prepare_bulk.py's `--name GLOB` matches only that literal file -- not an
 * unintended glob, and not a same-named file in a different subfolder (paired
 * with --no-recurse, which keeps the scan out of subfolders in the first
 * place). Same escaping Python's own glob.escape uses. */
export function escapeNameGlob(name: string): string {
  return name.replace(/[[\]*?]/g, (c) => {
    if (c === '[') return '[[]';
    if (c === ']') return '[]]';
    if (c === '*') return '[*]';
    return '[?]';
  });
}

export type DropPlan = {
  root: string;
  names: string[] | null; // --name filters for prepare_bulk.py, null = whole folder
  pointer: string;
  fileCount: number; // known file count for the confirm message (0 = folder, counted at connect time)
  label: string; // human label for the confirm message
};

/** Turns detected dropped paths into a prepare_bulk.py connect plan, or an
 * error message when the drop is not something we can connect in one shot
 * (paths spanning different folders, or a folder mixed with loose files). */
export function buildDropPlan(paths: DroppedPath[], principal: string): DropPlan | { error: string } {
  if (!paths.length) return { error: 'Nothing to connect.' };

  if (paths.length === 1) {
    const [p] = paths;
    if (p.isDirectory) {
      const label = basename(p.path) || p.path;
      return { root: p.path, names: null, pointer: buildDropPointerName(principal, label, p.path), fileCount: 0, label: `folder ${p.path}` };
    }
    const root = dirname(p.path);
    const name = basename(p.path);
    return { root, names: [name], pointer: buildDropPointerName(principal, name, p.path), fileCount: 1, label: `file ${p.path}` };
  }

  // Multiple paths: only a flat multi-file drop from the same folder (a
  // Finder multi-select) is supported in one shot -- mixed folders/files or
  // paths from different parents need one drop at a time.
  const dirsOf = paths.map((p) => (p.isDirectory ? p.path : dirname(p.path)));
  const uniqueDirs = [...new Set(dirsOf)];
  if (uniqueDirs.length !== 1 || paths.some((p) => p.isDirectory)) {
    return { error: `${paths.length} items span different folders or include a folder alongside files -- drop one file, or one folder, at a time.` };
  }
  const root = uniqueDirs[0];
  const names = paths.map((p) => basename(p.path));
  return { root, names, pointer: buildDropPointerName(principal, basename(root), root), fileCount: names.length, label: `${paths.length} files in ${root}` };
}

/** Whole-folder drops default the confirm to No (Enter = no) since they can
 * pull in far more than intended; single-file (and same-folder multi-file)
 * drops default to Yes (Enter = yes). */
export function dropConfirmDefault(plan: DropPlan): boolean {
  return plan.names !== null;
}

/** One-key confirm message. Connecting makes paid judge calls, so the chat
 * must never connect without an explicit yes. `pointerAlreadyExists` (from a
 * pre-confirm local panel check) adds a plain warning that connecting will
 * replace that pointer's existing approved answers. */
export function formatDropConfirm(plan: DropPlan, principal: string, pointerAlreadyExists: boolean = false): string {
  const what = plan.names ? `${plan.fileCount} file${plan.fileCount === 1 ? '' : 's'}` : 'the whole folder';
  let msg = `Connect ${what} from ${plan.root} as pointer "${plan.pointer}" for ${principal}? This makes paid judge calls.`;
  if (pointerAlreadyExists) msg += ` Pointer "${plan.pointer}" already exists and will be REPLACED.`;
  return msg;
}

/** The confirm gate itself: only an explicit `true` proceeds. A cancel
 * (symbol) or an explicit `false` must never connect. */
export function shouldConnect(confirmed: unknown): boolean {
  return confirmed === true;
}

/** argv (after the script path) for running prepare_bulk.py on a drop plan.
 * `--no-recurse` accompanies any `--name` filter so a same-named file in a
 * subfolder is never picked up alongside the intended one. */
export function buildConnectArgs(plan: DropPlan, principal: string): string[] {
  const args = ['--root', plan.root, '--pointer', plan.pointer, '--principal', principal];
  if (plan.names) {
    args.push('--no-recurse');
    for (const n of plan.names) args.push('--name', escapeNameGlob(n));
  }
  return args;
}

/** Pointer names for a principal from `dispatch.py memory --principal X`'s
 * panel JSON (`{"pointers": [{"pointer": "name", ...}, ...]}`), or null if
 * the output isn't that shape. No network -- a local panel action. */
export function parsePointerNames(stdout: string): string[] | null {
  try {
    const parsed = JSON.parse(stdout);
    if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.pointers)) return null;
    return parsed.pointers
      .map((p: unknown) => (p && typeof p === 'object' ? (p as { pointer?: unknown }).pointer : p))
      .filter((n: unknown): n is string => typeof n === 'string');
  } catch {
    return null;
  }
}

/** Whether a specific pointer name is already registered for this
 * principal, per the local panel check above. */
export function pointerExists(stdout: string, pointerName: string): boolean {
  const names = parsePointerNames(stdout);
  return !!names && names.includes(pointerName);
}

/** prepare_bulk.py's own `WARNING: replace:true on pointer ...` line, printed
 * when connecting reuses an existing pointer name -- surfaced verbatim
 * instead of being swallowed by the summary parsing below. */
export function parseReplaceWarning(stdout: string): string | null {
  const line = stdout.split('\n').find((l) => l.trim().startsWith('WARNING: replace:true'));
  return line ? line.trim() : null;
}

export type ConnectSummary = { approved: number; exceptions: number; held: number; heldLines: string[]; exceptionLines: string[] };

const SUMMARY_LINE = /^approved:\s*(\d+)\s+exceptions:\s*(\d+)\s+held:\s*(\d+)/;

/** Parses prepare_bulk.py's closing `approved: N  exceptions: N  held: N`
 * line (plus any HELD/EXCEPTION detail lines above it) out of its stdout. */
export function parseConnectSummary(stdout: string): ConnectSummary | null {
  const lines = stdout.split('\n');
  const summaryLine = lines.find((l) => SUMMARY_LINE.test(l.trim()));
  if (!summaryLine) return null;
  const m = summaryLine.trim().match(SUMMARY_LINE)!;
  // prepare_bulk.py prints a HELD line once during inventory and again in
  // its closing report for the same file -- dedupe so the chat doesn't list
  // it twice.
  const heldLines = [...new Set(lines.filter((l) => l.trim().startsWith('HELD')).map((l) => l.trim()))];
  const exceptionLines = [...new Set(lines.filter((l) => l.trim().startsWith('EXCEPTION')).map((l) => l.trim()))];
  return { approved: Number(m[1]), exceptions: Number(m[2]), held: Number(m[3]), heldLines, exceptionLines };
}

/** Plain-words final report -- held/exception files are surfaced, never
 * hidden, alongside the required "Connected N file(s)." line. */
export function formatConnectSummary(summary: ConnectSummary): string {
  const lines = [`Connected ${summary.approved} file${summary.approved === 1 ? '' : 's'}. Ask me about them.`];
  if (summary.held > 0) {
    lines.push(`${summary.held} file${summary.held === 1 ? '' : 's'} held for review: ${summary.heldLines.map((l) => l.replace(/^HELD\s+/, '')).join(', ')}`);
  }
  if (summary.exceptions > 0) {
    lines.push(`${summary.exceptions} file${summary.exceptions === 1 ? '' : 's'} failed the check: ${summary.exceptionLines.map((l) => l.replace(/^EXCEPTION\s+/, '')).join(', ')}`);
  }
  return lines.join('\n');
}

export const HELP_TEXT = `Super Jev commands:
  /help     show this help
  /setup    re-run first-time setup (API key, principal, folders)
  /folders  show or change the folders Super Jev searches
  /quit     exit the chat
Anything else is treated as a question.`;
