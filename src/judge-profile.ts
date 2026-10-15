/** Node twin of skills/super-jev/judge_profile.py: the judge's limits, key name and calibration,
 * stated once. Both read skills/super-jev/judge_profiles.json and derive the same numbers from it.
 * SUPERJEV_JUDGE picks the profile (default: the file's default; 'fake' is the test judge and uses
 * the default's numbers); an unknown name is an error.
 *
 * A profile may also state context presets, how much of each note one content check reads. The profile
 * as written is the preset 'small'; each row of its `contexts` table is a larger one and overrides only
 * the label, the window, the characters read per note and the check's timeout (the input cap and call
 * budget follow from the row's window). loadJudgeProfile(..., context) reads the profile at a preset;
 * without one, and so JUDGE_PROFILE, it is always 'small', which keeps everything derived from
 * JUDGE_PROFILE at start-up at small's numbers. */
import { readFileSync } from 'node:fs';

/** The one setting that picks the judge, in both languages. */
export const JUDGE_ENV = 'SUPERJEV_JUDGE';
const FAKE_JUDGE = 'fake';
/** The preset that is the profile exactly as written; the rows of its `contexts` table are the others. */
export const SMALL = 'small';
/** What a `contexts` row may override (label first, then whole numbers). Everything else a preset reports is the profile's own. */
const ROW_KEYS = ['label', 'window_tokens', 'read_chars', 'nav_timeout_ms'];
const PROFILES_URL = new URL('../skills/super-jev/judge_profiles.json', import.meta.url);

export type JudgeProfile = {
  name: string;
  /** The implementation that speaks to this judge ('jev'). */
  kind: string;
  /** Environment variable holding the judge's API key ('' for a keyless judge). */
  keyEnv: string;
  /** False for a judge that takes no key: no key check, no Authorization header. */
  keyRequired: boolean;
  /** Environment variable that, when set, replaces apiUrl ('' = none). */
  apiUrlEnv: string;
  /** The reply field the confidence numbers read; the lines are only meaningful for it. */
  confidenceField: string;
  /** False until the calibration is measured on this judge: it may answer, never save or approve. */
  calibrated: boolean;
  /** The judge's short name in error text (default: the profile name) and its vendor name (default: that name). */
  judgeName: string;
  vendor: string;
  apiUrl: string;
  model: string;
  windowTokens: number;
  maxQuestionsPerCall: number;
  inputUsdPerMtok: number;
  maxParts: number;
  fileCeilingBytes: number;
  gateWindowTokens: number;
  /** The one size rule: judge tokens = UTF-8 bytes / this, rounded up. */
  bytesPerToken: number;
  inputCapHeadroom: number;
  callHeadroom: number;
  /** HTTP statuses that mean try again, retried retryAttempts times, retryFirstDelayMs apart, doubling. */
  overloadedStatuses: number[];
  retryAttempts: number;
  retryFirstDelayMs: number;
  /** The status that means the request was over the window. */
  tooBigStatus: number;
  /** Most characters of one note a single content check reads, and how long (ms) that check may take. */
  readChars: number;
  navTimeoutMs: number;
  /** The context preset this profile is read at ('small' = as written), the name shown for it, and the preset
   * used when no one picks one (the table's default_context; 'small' when it says nothing). */
  context: string;
  contextLabel: string;
  defaultContext: string;
  /** The [name, value] request header that lifts the judge's input limit past longContextAboveTokens (its normal
   * limit); null = the profile never sends one. */
  longContextHeader: readonly [string, string] | null;
  longContextAboveTokens: number | null;
  // calibration: measured for one judge, re-measured for another
  confidenceLine: number;
  sureLine: number;
  confirmFloor: number;
  sourceFloor: number;
  claimContentFloor: number;
  preflightStrong: number;
  outcomeLine: number;
  maxConfidenceAbovePeak: number;
  /** Window less the question battery's overhead. */
  inputCapTokens: number;
  /** One call's budget for state plus longest question, in judge tokens. */
  callTokens: number;
  /** One call's budget at small, whatever preset this is: what the long-context header rule measures a request against. */
  baseCallTokens: number;
};

type Row = { label?: unknown; window_tokens?: number; read_chars?: number; nav_timeout_ms?: number };

type Raw = {
  kind: string; key_env: string; key_required?: boolean; api_url_env?: string; confidence_field?: string; calibrated?: boolean; judge_name?: string; vendor?: string; aliases?: string[]; api_url: string; model: string;
  window_tokens: number; max_questions_per_call: number; input_usd_per_mtok: number; max_parts: number;
  file_ceiling_bytes: number; gate_window_tokens: number; bytes_per_token: number;
  input_cap_headroom: number; call_headroom: number; overloaded_statuses: number[]; retry_attempts: number;
  retry_first_delay_ms: number; too_big_status: number; confidence_line: number; sure_line: number;
  confirm_floor: number; source_floor: number; claim_content_floor: number;
  preflight_strong: number; outcome_line: number; max_confidence_above_peak: number;
  read_chars: number; nav_timeout_ms: number;
  // checked as the table is read, so typed as anything a table might hold
  long_context_header?: unknown; long_context_above_tokens?: unknown; default_context?: unknown; contexts?: unknown;
};

/** What the presets set per note, besides what a `contexts` row sets: whole numbers, in the profile as written. */
const WHOLE_KEYS = ['read_chars', 'nav_timeout_ms'] as const;
const isTable = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
/** The names as a choice: 'small', 'small or medium', 'small, medium or large'. */
const choose = (names: string[]) => names.length === 1 ? names[0]! : `${names.slice(0, -1).join(', ')} or ${names.at(-1)}`;

/** The profile named `name`; else SUPERJEV_JUDGE; else the table's default. `file` is for tests. `context` picks one of
 * its presets; undefined is 'small', the profile as written, never defaultContext (that one is for the setting to read).
 * A preset the profile does not state is an error that names the ones it does. */
export function loadJudgeProfile(name?: string, file: URL | string = PROFILES_URL, env: NodeJS.ProcessEnv = process.env, context?: string): JudgeProfile {
  const data = JSON.parse(readFileSync(file, 'utf8')) as { default: string; profiles: Record<string, Raw> };
  const want = name ?? (env[JUDGE_ENV]?.trim() || data.default);
  const key = want === FAKE_JUDGE ? data.default
    : Object.hasOwn(data.profiles, want) ? want
    : Object.keys(data.profiles).find(k => data.profiles[k]!.aliases?.includes(want));
  if (key === undefined) {
    throw new Error(`unknown judge ${JSON.stringify(want)} (known: ${[...Object.keys(data.profiles), FAKE_JUDGE].sort().join(', ')})`);
  }
  const p = data.profiles[key]!;
  // A table that cannot be read stops the run with one line naming the file that was read. The checks below are
  // the Python loader's (judge_profile.py _bad, _whole, _presets), word for word where the wording is shared.
  const bad = (why: string) => new Error(`cannot read the judge limits table ${String(file).split('/').pop()}: ${why}`);
  const whole = (what: string, v: unknown) => { if (!Number.isInteger(v)) throw bad(`${what} must be a whole number`); };
  for (const k of WHOLE_KEYS) whole(k, p[k]);
  const rows = Object.hasOwn(p, 'contexts') ? p.contexts : {};
  if (!isTable(rows)) throw bad('contexts must be a table of rows');
  if (Object.hasOwn(rows, SMALL)) throw bad(`contexts has a ${SMALL} row, but ${SMALL} is the profile as written`);
  for (const [n, row] of Object.entries(rows)) {
    if (!isTable(row) || Object.keys(row).some(k => !ROW_KEYS.includes(k))) throw bad(`contexts.${n} may only set ${ROW_KEYS.join(', ')}`);
    for (const k of ROW_KEYS.slice(1)) if (Object.hasOwn(row, k)) whole(`contexts.${n}.${k}`, row[k]);
  }
  const presets = new Map<string, Row>([[SMALL, {}], ...Object.entries(rows) as [string, Row][]]);
  const names = [...presets.keys()];
  const defaultContext = Object.hasOwn(p, 'default_context') ? p.default_context : SMALL;
  if (typeof defaultContext !== 'string' || !presets.has(defaultContext)) throw bad(`default_context ${JSON.stringify(defaultContext)} is not one of ${choose(names)}`);
  const ctx = context ?? SMALL;
  const row = presets.get(ctx);
  if (row === undefined) throw new Error(`unknown context ${JSON.stringify(ctx)} (choose ${choose(names)})`);
  let header: readonly [string, string] | null = null;
  let above: number | null = null;
  if (Object.hasOwn(p, 'long_context_header')) {
    const h = p.long_context_header;
    if (!(Array.isArray(h) && h.length === 2 && h.every(s => typeof s === 'string' && s))) throw bad('long_context_header must be [name, value]');
    if (!Object.hasOwn(p, 'long_context_above_tokens')) throw bad('long_context_header needs long_context_above_tokens');
    whole('long_context_above_tokens', p.long_context_above_tokens);
    header = [h[0], h[1]];
    above = p.long_context_above_tokens as number;
  }
  const windowTokens = row.window_tokens ?? p.window_tokens;
  const inputCapTokens = windowTokens - p.input_cap_headroom;
  return {
    name: key, kind: p.kind, keyEnv: p.key_env, keyRequired: p.key_required ?? true,
    apiUrlEnv: p.api_url_env ?? '', confidenceField: p.confidence_field ?? 'confidence', calibrated: p.calibrated ?? true, judgeName: p.judge_name ?? key, vendor: p.vendor ?? p.judge_name ?? key, apiUrl: p.api_url, model: p.model, windowTokens,
    maxQuestionsPerCall: p.max_questions_per_call, inputUsdPerMtok: p.input_usd_per_mtok, maxParts: p.max_parts,
    fileCeilingBytes: p.file_ceiling_bytes, gateWindowTokens: p.gate_window_tokens, bytesPerToken: p.bytes_per_token,
    inputCapHeadroom: p.input_cap_headroom, callHeadroom: p.call_headroom, overloadedStatuses: [...p.overloaded_statuses],
    retryAttempts: p.retry_attempts, retryFirstDelayMs: p.retry_first_delay_ms, tooBigStatus: p.too_big_status,
    readChars: row.read_chars ?? p.read_chars, navTimeoutMs: row.nav_timeout_ms ?? p.nav_timeout_ms,
    context: ctx, contextLabel: typeof row.label === 'string' && row.label ? row.label : ctx.charAt(0).toUpperCase() + ctx.slice(1), defaultContext,
    longContextHeader: header, longContextAboveTokens: above,
    confidenceLine: p.confidence_line, sureLine: p.sure_line, confirmFloor: p.confirm_floor, sourceFloor: p.source_floor,
    claimContentFloor: p.claim_content_floor, preflightStrong: p.preflight_strong,
    outcomeLine: p.outcome_line, maxConfidenceAbovePeak: p.max_confidence_above_peak,
    inputCapTokens, callTokens: inputCapTokens - p.call_headroom,
    baseCallTokens: p.window_tokens - p.input_cap_headroom - p.call_headroom
  };
}

function fromEnv(): JudgeProfile {
  try { return Object.freeze(loadJudgeProfile()); } catch (e) {
    // A typo in SUPERJEV_JUDGE stops the run with one line, never a stack trace and never another judge.
    console.error(`super-jev: ${JUDGE_ENV}: ${e instanceof Error ? e.message : String(e)}`);
    process.exit(1);
  }
}

export const JUDGE_PROFILE: JudgeProfile = fromEnv();

/** The one size unit: UTF-8 bytes / bytesPerToken, rounded up, high on purpose. A non-string is counted
 * as its JSON. Same rule as judge_tokens in judge_profile.py. */
export function judgeTokens(value: unknown): number {
  if (value === undefined || value === null) return 0;
  const text = typeof value === 'string' ? value : JSON.stringify(value) ?? '';
  return Math.ceil(Buffer.byteLength(text, 'utf8') / JUDGE_PROFILE.bytesPerToken);
}
