import type { CostAccount, Evaluation } from './types.ts';

/** Mutable accumulator; `snapshot()` returns the immutable report. */
export class CostMeter {
  private account: CostAccount = { calls: 0, inputTokens: 0, outputTokens: 0, retries: 0, wallMs: 0, perCallLatency: [], estimated: false };
  private started = performance.now();

  /**
   * Record one provider call. `usage` is used when the provider reports it;
   * otherwise the estimate is used and the whole account is marked estimated,
   * because a mixed total is not a billing figure.
   */
  call(evaluation: Evaluation | undefined, latencyMs: number, estimate: { input: number; output: number }): void {
    this.account.calls += 1;
    this.account.perCallLatency.push(Math.round(latencyMs));
    const usage = evaluation?.usage;
    if (usage && Number.isFinite(usage.input_tokens) && Number.isFinite(usage.output_tokens)) {
      this.account.inputTokens += usage.input_tokens;
      this.account.outputTokens += usage.output_tokens;
    } else {
      this.account.inputTokens += estimate.input;
      this.account.outputTokens += estimate.output;
      this.account.estimated = true;
    }
  }

  retry(): void { this.account.retries += 1; }

  snapshot(): CostAccount {
    return { ...this.account, wallMs: Math.round(performance.now() - this.started), perCallLatency: [...this.account.perCallLatency] };
  }
}

export function formatCost(cost: CostAccount): string {
  return [
    `cost: ${cost.calls} call(s), ${cost.retries} retry/retries, wall ${cost.wallMs}ms`,
    `  tokens: input ${cost.inputTokens}, output ${cost.outputTokens}${cost.estimated ? ' (ESTIMATED, not a billing figure)' : ' (provider-reported)'}`,
    `  per-call latency ms: [${cost.perCallLatency.join(', ')}]`
  ].join('\n');
}
