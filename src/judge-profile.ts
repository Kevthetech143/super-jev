/** Node twin of skills/super-jev/judge_profile.py: the judge's limits, key name and calibration,
 * stated once. Both read skills/super-jev/judge_profiles.json and derive the same numbers from it.
 * SUPERJEV_JUDGE picks the profile (default: the file's default; 'fake' is the test judge and uses
 * the default's numbers); an unknown name is an error. */
import { readFileSync } from 'node:fs';

/** The one setting that picks the judge, in both languages. */
export const JUDGE_ENV = 'SUPERJEV_JUDGE';
const FAKE_JUDGE = 'fake';
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
  // calibration: measured for one judge, re-measured for another
  confidenceLine: number;
  sureLine: number;
  confirmFloor: number;
  sourceFloor: number;
  claimContentFloor: number;
  routeFloor: number;
  preflightStrong: number;
  outcomeLine: number;
  maxConfidenceAbovePeak: number;
  /** Window less the question battery's overhead. */
  inputCapTokens: number;
  /** One call's budget for state plus longest question, in judge tokens. */
  callTokens: number;
};

type Raw = {
  kind: string; key_env: string; key_required?: boolean; api_url_env?: string; confidence_field?: string; calibrated?: boolean; judge_name?: string; vendor?: string; aliases?: string[]; api_url: string; model: string;
  window_tokens: number; max_questions_per_call: number; input_usd_per_mtok: number; max_parts: number;
  file_ceiling_bytes: number; gate_window_tokens: number; bytes_per_token: number;
  input_cap_headroom: number; call_headroom: number; overloaded_statuses: number[]; retry_attempts: number;
  retry_first_delay_ms: number; too_big_status: number; confidence_line: number; sure_line: number;
  confirm_floor: number; source_floor: number; claim_content_floor: number; route_floor: number;
  preflight_strong: number; outcome_line: number; max_confidence_above_peak: number;
};

/** The profile named `name`; else SUPERJEV_JUDGE; else the table's default. `file` is for tests. */
export function loadJudgeProfile(name?: string, file: URL | string = PROFILES_URL, env: NodeJS.ProcessEnv = process.env): JudgeProfile {
  const data = JSON.parse(readFileSync(file, 'utf8')) as { default: string; profiles: Record<string, Raw> };
  const want = name ?? (env[JUDGE_ENV]?.trim() || data.default);
  const key = want === FAKE_JUDGE ? data.default
    : Object.hasOwn(data.profiles, want) ? want
    : Object.keys(data.profiles).find(k => data.profiles[k]!.aliases?.includes(want));
  if (key === undefined) {
    throw new Error(`unknown judge ${JSON.stringify(want)} (known: ${[...Object.keys(data.profiles), FAKE_JUDGE].sort().join(', ')})`);
  }
  const p = data.profiles[key]!;
  const inputCapTokens = p.window_tokens - p.input_cap_headroom;
  return {
    name: key, kind: p.kind, keyEnv: p.key_env, keyRequired: p.key_required ?? true,
    apiUrlEnv: p.api_url_env ?? '', confidenceField: p.confidence_field ?? 'confidence', calibrated: p.calibrated ?? true, judgeName: p.judge_name ?? key, vendor: p.vendor ?? p.judge_name ?? key, apiUrl: p.api_url, model: p.model, windowTokens: p.window_tokens,
    maxQuestionsPerCall: p.max_questions_per_call, inputUsdPerMtok: p.input_usd_per_mtok, maxParts: p.max_parts,
    fileCeilingBytes: p.file_ceiling_bytes, gateWindowTokens: p.gate_window_tokens, bytesPerToken: p.bytes_per_token,
    inputCapHeadroom: p.input_cap_headroom, callHeadroom: p.call_headroom, overloadedStatuses: [...p.overloaded_statuses],
    retryAttempts: p.retry_attempts, retryFirstDelayMs: p.retry_first_delay_ms, tooBigStatus: p.too_big_status,
    confidenceLine: p.confidence_line, sureLine: p.sure_line, confirmFloor: p.confirm_floor, sourceFloor: p.source_floor,
    claimContentFloor: p.claim_content_floor, routeFloor: p.route_floor, preflightStrong: p.preflight_strong,
    outcomeLine: p.outcome_line, maxConfidenceAbovePeak: p.max_confidence_above_peak,
    inputCapTokens, callTokens: inputCapTokens - p.call_headroom
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
