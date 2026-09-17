import type { Evaluator, Request, Evaluation } from './types.ts';

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

export function validateEvaluation(request: Request, response: Evaluation): void {
  if (!response || typeof response.model !== 'string' || !response.answers) throw new Error('Invalid model response');
  const probability = (n: unknown) => typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 1;
  for (const [id, q] of Object.entries(request.questions)) {
    const a = response.answers[id];
    if (!a || a.type !== q.type) throw new Error(`Missing or mismatched answer: ${id}`);
    if (a.type === 'noul') {
      if (!probability(a.noul)) throw new Error(`Invalid probability: ${id}`);
      continue;
    }
    const keys = q.type === 'choice' ? Object.keys(q.criteria) : q.type === 'score' ? q.criteria.map((_, i) => String(i)) : [];
    const p = a.probabilities;
    // Shape first: exactly the levels we asked about, each a real probability.
    // This rejects a missing level, an extra level, a negative value and a NaN.
    if (!probability(a.confidence) || !p || Object.keys(p).length !== keys.length ||
        keys.some(k => !Object.hasOwn(p, k) || !probability(p[k]))) throw new Error(`Invalid distribution: ${id}`);
    // Then the total. Only a rounding-sized error is tolerated; a total of 0,
    // a total above 1.05 or any other gross malformation is rejected outright.
    // Nothing is ever substituted or retried in its place.
    const total = keys.reduce((sum, k) => sum + p[k], 0);
    if (!Number.isFinite(total) || Math.abs(total - 1) > ROUNDING_STEP + FLOAT_SLACK) throw new Error(`Invalid distribution total ${total}: ${id}`);
    // Inside the band, renormalize so downstream arithmetic sees a true
    // distribution, and keep exactly what the provider sent for the audit
    // trail. Idempotent: a normalized answer re-validates without changing.
    if (Math.abs(total - 1) > FLOAT_SLACK) {
      a.rawProbabilities ??= { ...p };
      for (const k of keys) p[k] = p[k] / total;
    }
    const peak = Math.max(...keys.map(k => p[k]));
    // INFERRED. TypeSafe documents confidence only as a number in [0, 1]
    // "derived from probabilities", with no formula, and never states that it
    // equals the peak. It never exceeded the peak in 337 recorded answers, and
    // a confidence above the peak contradicts every documented reading of it.
    // This is the one rule here that a live probe could overturn.
    if (a.confidence > peak + ROUNDING_STEP + FLOAT_SLACK) throw new Error(`Confidence exceeds its distribution peak: ${id}`);
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
      // score of 0 with all mass on level 1.
      const expected = keys.reduce((sum, k) => sum + Number(k) * p[k], 0);
      // INFERRED tolerance: one rounding step per unit of level span for the
      // probabilities that feed the sum, plus one for the score's own rounding.
      const tolerance = ROUNDING_STEP * (keys.length - 1) + ROUNDING_STEP;
      if (Math.abs(a.score - expected) > tolerance + FLOAT_SLACK) throw new Error(`Score ${a.score} inconsistent with its distribution (expected about ${expected.toFixed(2)}): ${id}`);
    }
  }
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
    validateEvaluation(request, result);
    return result;
  }
}
