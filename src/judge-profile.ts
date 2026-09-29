/** Node twin of skills/super-jev/judge_profile.py: the judge model's limits, stated once.
 * Both read skills/super-jev/judge_profiles.json and derive the same numbers from it.
 * There is one judge, so nothing selects among profiles; an unknown name is an error. */
import { readFileSync } from 'node:fs';

// The two headrooms are the gaps Jev's old constants had (32,768 -> 32,000 -> 30,000), kept as
// fixed amounts: exact for Jev, an assumption for another window until it is measured there.
/** Room left under the window for the question battery's own overhead. */
export const INPUT_CAP_HEADROOM = 768;
/** A call's budget sits this far under the input cap, since its token count is only an estimate. */
export const CALL_HEADROOM = 2_000;

/** The one size unit: UTF-8 bytes / 2, rounded up, high on purpose. A non-string is counted as its
 * JSON. Same rule as judge_tokens in judge_profile.py. */
export function judgeTokens(value: unknown): number {
  if (value === undefined || value === null) return 0;
  const text = typeof value === 'string' ? value : JSON.stringify(value) ?? '';
  return Math.ceil(Buffer.byteLength(text, 'utf8') / 2);
}

export type JudgeProfile = {
  name: string;
  apiUrl: string;
  model: string;
  windowTokens: number;
  maxQuestionsPerCall: number;
  inputUsdPerMtok: number;
  maxParts: number;
  confidenceLine: number;
  fileCeilingBytes: number;
  gateWindowTokens: number;
  /** Window less the question battery's overhead. */
  inputCapTokens: number;
  /** One call's budget for state plus longest question, in judge tokens. */
  callTokens: number;
};

type Raw = {
  api_url: string; model: string; window_tokens: number; max_questions_per_call: number;
  input_usd_per_mtok: number; max_parts: number; confidence_line: number;
  file_ceiling_bytes: number; gate_window_tokens: number;
};

export function loadJudgeProfile(name?: string): JudgeProfile {
  const data = JSON.parse(readFileSync(new URL('../skills/super-jev/judge_profiles.json', import.meta.url), 'utf8')) as
    { default: string; profiles: Record<string, Raw> };
  const want = name ?? data.default;
  if (!Object.hasOwn(data.profiles, want)) {
    throw new Error(`unknown judge profile ${JSON.stringify(want)} (known: ${Object.keys(data.profiles).sort().join(', ')})`);
  }
  const p = data.profiles[want]!;
  const inputCapTokens = p.window_tokens - INPUT_CAP_HEADROOM;
  return {
    name: want, apiUrl: p.api_url, model: p.model, windowTokens: p.window_tokens,
    maxQuestionsPerCall: p.max_questions_per_call, inputUsdPerMtok: p.input_usd_per_mtok,
    maxParts: p.max_parts, confidenceLine: p.confidence_line, fileCeilingBytes: p.file_ceiling_bytes,
    gateWindowTokens: p.gate_window_tokens, inputCapTokens, callTokens: inputCapTokens - CALL_HEADROOM
  };
}

export const JUDGE_PROFILE: JudgeProfile = Object.freeze(loadJudgeProfile());
