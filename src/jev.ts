import type { Evaluator, Request, Evaluation, Answer } from './types.ts';

// Every rule below is sourced in docs/provider-contract.md, which labels each
// claim DOCUMENTED (stated by TypeSafe), OBSERVED (measured from recorded
// responses) or INFERRED (this harness's own choice).
//
// One rounding step. TypeSafe documents that a distribution's "floats sum to 1"
// but documents no rounding contract at all. Across 337 recorded answers every
// probability carried at most two decimals and the totals ran from 0.99 to
// 1.00, so a single cent is the widest shortfall the observed output produces.
// INFERRED, and deliberately narrow: tightening it is safe, widening it is not.
export const ROUNDING_STEP = 0.01;
// The observed worst case is exactly one rounding step, so a bare `> 0.01`
// comparison would be decided by IEEE-754 noise: the recorded 0.99 total sums
// to 0.9900000000000001 or 0.9899999999999999 depending on key order, and only
// one of those is under the limit. This slack is nine orders of magnitude below
// the step, so it widens the accepted band by nothing that matters.
const FLOAT_SLACK = 1e-9;

// Returns the evaluation to use downstream. The caller's response object is
// never written to: a normalized answer is returned on a fresh object, so the
// provider's reply stays exactly as it arrived and a frozen response validates.
export function validateEvaluation(request: Request, response: Evaluation): Evaluation {
  if (!response || typeof response.model !== 'string' || !response.answers) throw new Error('Invalid model response');
  const probability = (n: unknown) => typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 1;
  // True when `raw` is a distribution over the same levels that renormalizes to
  // `target`, which is what this validator's own output looks like on a second
  // pass. Anything else claiming to be rawProbabilities is not that.
  const renormalizesTo = (raw: Record<string, number>, target: Record<string, number>, keys: string[]) => {
    if (Object.keys(raw).length !== keys.length || keys.some(k => !Object.hasOwn(raw, k) || !probability(raw[k]))) return false;
    const total = raw ? keys.reduce((sum, k) => sum + raw[k], 0) : 0;
    if (!Number.isFinite(total) || total <= 0 || Math.abs(total - 1) > ROUNDING_STEP + FLOAT_SLACK) return false;
    return keys.every(k => Math.abs(raw[k] / total - target[k]) <= FLOAT_SLACK);
  };
  const answers: Record<string, Answer> = { ...response.answers };
  for (const [id, q] of Object.entries(request.questions)) {
    const a = response.answers[id];
    if (!a || a.type !== q.type) throw new Error(`Missing or mismatched answer: ${id}`);
    if (a.type === 'noul') {
      // DOCUMENTED, and confirmed by the live probe of 2026-09-17: a noul
      // answer carries the single value and nothing else, no confidence and no
      // probabilities, so only that value is checked.
      if (!probability(a.noul)) throw new Error(`Invalid probability: ${id}`);
      continue;
    }
    const keys = q.type === 'choice' ? Object.keys(q.criteria) : q.type === 'score' ? q.criteria.map((_, i) => String(i)) : [];
    const provided = a.probabilities;
    // Shape first: exactly the levels we asked about, each a real probability.
    // This rejects a missing level, an extra level, a negative value and a NaN.
    // Confidence is checked for one thing only, the documented one: a finite
    // number in [0, 1]. See section 4 of the contract doc.
    if (!probability(a.confidence) || !provided || Object.keys(provided).length !== keys.length ||
        keys.some(k => !Object.hasOwn(provided, k) || !probability(provided[k]))) throw new Error(`Invalid distribution: ${id}`);
    // Then the total. Only a rounding-sized error is tolerated; a total of 0,
    // a total above 1.01 or any other gross malformation is rejected outright.
    // Nothing is ever substituted or retried in its place.
    const total = keys.reduce((sum, k) => sum + provided[k], 0);
    if (!Number.isFinite(total) || Math.abs(total - 1) > ROUNDING_STEP + FLOAT_SLACK) throw new Error(`Invalid distribution total ${total}: ${id}`);
    // Inside the band, renormalize so downstream arithmetic sees a true
    // distribution, and keep exactly what the provider sent for the audit
    // trail. rawProbabilities is this harness's field, never the provider's: on
    // the renormalizing path anything arriving under that name is overwritten
    // with the values actually sent, and on the pass-through path it is kept
    // only if it renormalizes to these probabilities, which is what revalidating
    // this validator's own output looks like. Otherwise the answer is rejected,
    // because a trusted rawProbabilities would forge the audit trail.
    const shifted = Math.abs(total - 1) > FLOAT_SLACK;
    if (!shifted && a.rawProbabilities && !renormalizesTo(a.rawProbabilities, provided, keys)) {
      throw new Error(`Untrusted rawProbabilities: ${id}`);
    }
    const p = shifted ? Object.fromEntries(keys.map(k => [k, provided[k] / total])) : { ...provided };
    const raw = shifted ? { ...provided } : a.rawProbabilities;
    const validated: Record<string, unknown> = { ...a, probabilities: p };
    if (raw) validated.rawProbabilities = { ...raw }; else delete validated.rawProbabilities;
    const peak = Math.max(...keys.map(k => p[k]));
    // DOCUMENTED: choice is "the highest-probability option".
    if (a.type === 'choice' && (!keys.includes(a.choice) || p[a.choice] + ROUNDING_STEP + FLOAT_SLACK < peak)) throw new Error(`Invalid choice: ${id}`);
    if (a.type === 'score') {
      if (!Number.isFinite(a.score) || a.score < 0 || a.score > keys.length - 1) throw new Error(`Invalid score: ${id}`);
      // DOCUMENTED: score is "the probability-weighted answer across the
      // levels; can land between levels", that is the expectation of the level
      // index. So it is checked against that expectation and NOT against the
      // argmax: requiring score to sit on a level carrying probability mass
      // would reject the provider's own documented example, score 1.30 over
      // {0: 0.0, 1: 0.70, 2: 0.30}. It does catch the observed gap, a reported
      // score of 0 with all mass on level 1. Confirmed live on 2026-09-17:
      // score 1.85 over {0:.01, 1:.23, 2:.66, 3:.10, 4:0} is the exact mean.
      const expected = keys.reduce((sum, k) => sum + Number(k) * p[k], 0);
      // INFERRED tolerance: one rounding step per unit of level span for the
      // probabilities that feed the sum, plus one for the score's own rounding.
      const tolerance = ROUNDING_STEP * (keys.length - 1) + ROUNDING_STEP;
      if (Math.abs(a.score - expected) > tolerance + FLOAT_SLACK) throw new Error(`Score ${a.score} inconsistent with its distribution (expected about ${expected.toFixed(2)}): ${id}`);
    }
    answers[id] = validated as Answer;
  }
  return { ...response, answers };
}

// Judge pinning (gate pivot, 2026-09-19): the harness can pin the judge's
// sampling so two runs of the same case stop flipping. The TypeSafe API shape
// for these fields is not documented anywhere in this repo, so they are sent
// under the plain names `temperature` and `seed`. The pin is OPT-IN: when
// SUPERJEV_JUDGE_TEMPERATURE is unset, no pin fields are sent at all and the
// request is exactly the pre-pin call. When a pin IS sent and the call gets
// ANY non-2xx, the call is retried ONCE without the pin - no body matching
// is involved - and the failure is detected once per evaluate: the remaining
// runs reuse the outcome instead of retrying again. A stderr note is logged
// only when that retry SUCCEEDS; when the retry also fails, the thrown error
// keeps both texts ("pin rejected: <original>; unpinned retry: <second>") so
// the original failure is never masked. The pin path only exists because the
// operator opted in; with the env unset the pin can never be the cause of a
// failure.
//
// Judge multi-run (2026-09-19): SUPERJEV_JUDGE_RUNS=N calls the judge N times
// per evaluate. Run 1 is canonical (its validated Evaluation is returned);
// a failure on a later run keeps run 1's evaluation, is recorded as an error,
// and never throws. Every run's decision summary is recorded, and the case is
// marked unstable when the runs disagree or a run failed. decisions[0] is the
// canonical run's digest; decisions[1..] map to the `digest_2` .. `digest_N`
// columns of decisions.tsv (the `decision` column keeps the bench's verdict).
export const JUDGE_TEMPERATURE_ENV = 'SUPERJEV_JUDGE_TEMPERATURE';
export const JUDGE_SEED_ENV = 'SUPERJEV_JUDGE_SEED';
export const JUDGE_RUNS_ENV = 'SUPERJEV_JUDGE_RUNS';

export type JudgePin = { temperature?: number; seed?: number };

/** The pin fields to attempt, from env. OPT-IN: when SUPERJEV_JUDGE_TEMPERATURE
 * is unset or empty, no temperature is sent at all - the default request
 * carries no pin fields, so the pin can never be the cause of a failure.
 * Exported for tests. */
export function judgePinFields(env: NodeJS.ProcessEnv = process.env): JudgePin {
  const fields: JudgePin = {};
  const rawT = env[JUDGE_TEMPERATURE_ENV];
  if (rawT !== undefined && rawT !== '') {
    const t = Number(rawT);
    if (Number.isFinite(t)) {
      fields.temperature = t;
    } else {
      console.error(`judge pin: ${JUDGE_TEMPERATURE_ENV}=${JSON.stringify(rawT)} is not a number - sending without temperature`);
    }
  }
  const rawS = env[JUDGE_SEED_ENV];
  if (rawS !== undefined && rawS !== '') {
    const n = Number(rawS);
    if (Number.isInteger(n)) {
      fields.seed = n;
    } else {
      console.error(`judge pin: ${JUDGE_SEED_ENV}=${JSON.stringify(rawS)} is not an integer - sending without seed`);
    }
  }
  return fields;
}

/** How many times to call the judge per evaluate (default 1). Malformed or
 * non-positive values degrade to 1 with a stderr warning. Exported for tests. */
export function judgeRuns(env: NodeJS.ProcessEnv = process.env): number {
  const raw = env[JUDGE_RUNS_ENV];
  if (raw === undefined || raw === '') return 1;
  const n = Number(raw);
  if (Number.isInteger(n) && n >= 1) return n;
  console.error(`judge runs: ${JUDGE_RUNS_ENV}=${JSON.stringify(raw)} is not a positive integer - running the judge once`);
  return 1;
}

/** Canonical one-line summary of one judge run's validated answers, for flip
 * detection. Tab/newline-free so it is safe as a decisions.tsv cell.
 * Exported for tests. */
export function judgeDecisionSummary(evaluation: Evaluation): string {
  const parts: string[] = [];
  const clean = (s: string) => s.replace(/[\t\n\r]/g, ' ');
  for (const key of Object.keys(evaluation.answers).sort()) {
    const a = evaluation.answers[key];
    if (a.type === 'choice') parts.push(`${clean(key)}=choice:${clean(a.choice)}`);
    else if (a.type === 'noul') parts.push(`${clean(key)}=noul:${a.noul}`);
    else parts.push(`${clean(key)}=score:${a.score}`);
  }
  return parts.join(' ');
}

export type JudgeRuns = {
  runs: number;
  /** decisions[0] is the canonical run's answers digest; decisions[1..] map
   * to the `digest_2` .. `digest_N` columns of decisions.tsv. A run that
   * failed carries an empty digest; its error is in runErrors. */
  decisions: string[];
  /** True when the runs disagreed with each other, or when any run failed. */
  unstable: boolean;
  /** runErrors[i] is null when run i+1 succeeded, else that run's error text. */
  runErrors: (string | null)[];
};

/** Per-evaluate pin state, shared across the N judge runs: a pin rejection
 * is detected exactly once per evaluate, and the remaining runs reuse the
 * outcome instead of retrying again. */
type PinState = { pin: JudgePin; rejected: boolean };

export class Jev implements Evaluator {
  private key: string;
  private model: string;
  private transport: typeof fetch;
  constructor(options: { apiKey?: string; model?: string; fetch?: typeof fetch } = {}) {
    this.key = options.apiKey ?? process.env.TYPESAFE_API_KEY ?? '';
    if (!this.key) throw new Error('Set TYPESAFE_API_KEY to use live Jev');
    this.model = options.model ?? 'jev-latest';
    this.transport = options.fetch ?? fetch;
  }
  private async _post(request: Request, signal: AbortSignal, pin: JudgePin) {
    return this.transport('https://api.typesafe.ai/v1/systemone', {
      method: 'POST', signal,
      headers: { Authorization: `Bearer ${this.key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: this.model, ...pin, ...request })
    });
  }
  private async _peekBody(response: Response): Promise<string> {
    try { return await response.text(); } catch { return ''; }
  }
  /** One judge call with the pin-retry rule. `state` is the evaluate's shared
   * pin state: when a PINNED request gets ANY non-2xx, it is retried ONCE
   * without the pin and the failure is recorded on `state`, so the remaining
   * runs send unpinned and never retry. No body matching is involved: any
   * non-2xx on a pinned call degrades to the unpinned call. When the unpinned
   * retry also fails, the thrown error carries BOTH texts. */
  private async _judgeOnce(request: Request, signal: AbortSignal, state: PinState): Promise<Evaluation> {
    const pin = state.rejected ? {} : state.pin;
    let response = await this._post(request, signal, pin);
    let pinRejection: string | null = null;
    if (!response.ok && Object.keys(pin).length > 0) {
      // Detected once per evaluate: mark the pin rejected before the retry
      // so the remaining runs share the outcome (one retry, not N).
      const bodyText = await this._peekBody(response);
      state.rejected = true;
      pinRejection = `Jev HTTP ${response.status}${bodyText ? `: ${bodyText}` : ''}`;
      response = await this._post(request, signal, {});
      if (response.ok) {
        // The ledger note fires only on the success path: a failed retry
        // keeps both error texts in the thrown error instead.
        console.error('judge pin: pinned request got a non-2xx; retried once without temperature/seed');
      }
    }
    if (!response.ok) {
      const retryError = `Jev HTTP ${response.status}`;
      if (pinRejection !== null) throw new Error(`pin rejected: ${pinRejection}; unpinned retry: ${retryError}`);
      throw new Error(retryError);
    }
    const result = await response.json() as Evaluation;
    // The validated copy is what the caller gets; the parsed reply is not touched.
    return validateEvaluation(request, result);
  }
  async evaluate(request: Request, signal: AbortSignal): Promise<Evaluation & { judgeRuns: JudgeRuns }> {
    // Never retry tools. Transient inference failures are surfaced for an explicit new run.
    // The one exception is the judge pin: when a PINNED request (opt-in via
    // SUPERJEV_JUDGE_TEMPERATURE) gets any non-2xx, the call is retried ONCE
    // without the pin (detected once per evaluate and shared across the N
    // runs), so an opted-in pin degrades to the unpinned call.
    const pinState: PinState = { pin: judgePinFields(), rejected: false };
    const n = judgeRuns();
    const decisions: string[] = [];
    const runErrors: (string | null)[] = [];
    let first: Evaluation | undefined;
    for (let i = 0; i < n; i++) {
      try {
        const evaluation = await this._judgeOnce(request, signal, pinState);
        if (i === 0) first = evaluation;
        decisions.push(judgeDecisionSummary(evaluation));
        runErrors.push(null);
      } catch (err) {
        if (i === 0) throw err; // run 1 is canonical: its failure still hard-fails
        // A later run's failure keeps run 1's canonical evaluation. The failed
        // run is recorded as an error (empty digest, fail-closed unstable)
        // instead of throwing, so one transient error cannot hard-fail a gate.
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`judge run ${i + 1} of ${n} failed (${msg}); keeping run 1's canonical evaluation`);
        decisions.push('');
        runErrors.push(msg);
      }
    }
    const unstable = decisions.some(d => d !== decisions[0]) || runErrors.some(e => e !== null);
    if (n > 1) {
      // Machine-readable for the bench driver: decisions[1..] are the
      // `digest_2` .. `digest_N` columns of decisions.tsv (the `decision`
      // column keeps the bench's verdict).
      console.error(`judge runs: N=${n} unstable=${unstable} decisions=${JSON.stringify(decisions)}`);
    }
    return { ...first!, judgeRuns: { runs: n, decisions, unstable, runErrors } };
  }
}
