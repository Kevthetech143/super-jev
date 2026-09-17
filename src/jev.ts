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
  async evaluate(request: Request, signal: AbortSignal): Promise<Evaluation> {
    // Never retry tools. Transient inference failures are surfaced for an explicit new run.
    const response = await this.transport('https://api.typesafe.ai/v1/systemone', {
      method: 'POST', signal,
      headers: { Authorization: `Bearer ${this.key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: this.model, ...request })
    });
    if (!response.ok) throw new Error(`Jev HTTP ${response.status}`);
    const result = await response.json() as Evaluation;
    // The validated copy is what the caller gets; the parsed reply is not touched.
    return validateEvaluation(request, result);
  }
}
