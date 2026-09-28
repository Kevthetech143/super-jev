/** Node twin of skills/super-jev/judge_profile.py: the judge model's limits, stated once.
 * Both read skills/super-jev/judge_profiles.json and derive the same numbers from it.
 * SUPERJEV_JUDGE_PROFILE picks a profile; unset, the file's default (TypeSafe Jev). */
import { readFileSync } from 'node:fs';

export const PROFILE_ENV = 'SUPERJEV_JUDGE_PROFILE';
// The two headrooms are the gaps Jev's old constants had (32,768 -> 32,000 -> 30,000), kept as
// fixed amounts: exact for Jev, an assumption for another window until it is measured there.
/** Room left under the window for the question battery's own overhead. */
export const INPUT_CAP_HEADROOM = 768;
/** A call's budget sits this far under the input cap, since its token count is only an estimate. */
export const CALL_HEADROOM = 2_000;

export type JudgeProfile = {
  name: string;
  label: string;
  windowTokens: number;
  maxQuestionsPerCall: number;
  questionKinds: readonly string[];
  inputUsdPerMtok: number;
  outputUsdPerMtok: number;
  /** Window less the question battery's overhead. */
  inputCapTokens: number;
  /** One call's budget for state plus longest question, estimated high (bytes/2). */
  callTokens: number;
};

type Raw = {
  label: string; window_tokens: number; max_questions_per_call: number;
  question_kinds: string[]; input_usd_per_mtok: number; output_usd_per_mtok?: number;
};

export function loadJudgeProfile(name?: string): JudgeProfile {
  const data = JSON.parse(readFileSync(new URL('../skills/super-jev/judge_profiles.json', import.meta.url), 'utf8')) as
    { default: string; profiles: Record<string, Raw> };
  let want = name ?? (process.env[PROFILE_ENV] || data.default);
  if (!Object.hasOwn(data.profiles, want)) {
    process.stderr.write(`super-jev: unknown ${PROFILE_ENV}=${JSON.stringify(want)}; using ${JSON.stringify(data.default)} ` +
      `(known: ${Object.keys(data.profiles).sort().join(', ')})\n`);
    want = data.default;
  }
  const p = data.profiles[want];
  const inputCapTokens = p.window_tokens - INPUT_CAP_HEADROOM;
  return {
    name: want, label: p.label, windowTokens: p.window_tokens, maxQuestionsPerCall: p.max_questions_per_call,
    questionKinds: Object.freeze([...p.question_kinds]), inputUsdPerMtok: p.input_usd_per_mtok,
    outputUsdPerMtok: p.output_usd_per_mtok ?? 0, inputCapTokens, callTokens: inputCapTokens - CALL_HEADROOM
  };
}

export const JUDGE_PROFILE: JudgeProfile = Object.freeze(loadJudgeProfile());
