// Config + pure-logic helpers for the `superjev` chat CLI. Kept dependency-free
// (no clack, no child_process) so it is cheap to unit test in isolation from the
// interactive TUI in jev-chat-cli.ts.
import { mkdirSync, chmodSync, existsSync, readFileSync, writeFileSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

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

export const HELP_TEXT = `Super Jev commands:
  /help     show this help
  /setup    re-run first-time setup (API key, principal, folders)
  /folders  show or change the folders Super Jev searches
  /quit     exit the chat
Anything else is treated as a question.`;
