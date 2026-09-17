import type { Evaluator, Request, Evaluation } from './types.ts';

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
    if (!probability(a.confidence) || !p || Object.keys(p).length !== keys.length ||
        keys.some(k => !Object.hasOwn(p, k) || !probability(p[k])) ||
        Math.abs(Object.values(p).reduce((x, y) => x + y, 0) - 1) > 0.001) throw new Error(`Invalid distribution: ${id}`);
    if (a.type === 'choice' && (!keys.includes(a.choice) || p[a.choice] + 0.001 < Math.max(...Object.values(p)))) throw new Error(`Invalid choice: ${id}`);
    if (a.type === 'score' && (!Number.isFinite(a.score) || a.score < 0 || a.score > keys.length - 1)) throw new Error(`Invalid score: ${id}`);
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
