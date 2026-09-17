import type { Domain, Evaluator, Request, Evaluation } from '../src/types.ts';

type RecoveryState = { restarted: boolean; healthy: boolean };
export function recovery(): Domain<RecoveryState> {
  let healthy = false;
  return {
    name: 'service-recovery-simulation',
    observe: async state => ({ ...state, healthCheck: healthy ? '200 OK' : '503 unavailable' }),
    questions: () => ({ next: { type: 'choice', instructions: 'Choose the next permitted operation given the health check and whether restart was attempted.', criteria: { restart: 'Service unavailable and no restart attempted', check: 'Restart attempted; inspect health', escalate: 'Neither action is appropriate' } } }),
    decide: (_state, _evidence, result) => {
      const a = result.answers.next;
      if (a.type !== 'choice' || a.confidence < 0.7 || a.choice === 'escalate') return { kind: 'stop', reason: 'Needs review' };
      return { kind: 'act', action: { tool: a.choice, args: {} } };
    },
    permit: (state, action) => action.tool === 'check' || (action.tool === 'restart' && !state.restarted),
    tools: {
      restart: { validate: emptyArgs, execute: async () => { healthy = true; return { restarted: true }; } },
      check: { validate: emptyArgs, execute: async () => ({ healthy }) }
    },
    reduce: (state, action, outcome) => action.tool === 'restart' ? { ...state, restarted: true } : { ...state, healthy: (outcome as { healthy: boolean }).healthy },
    verify: async state => state.healthy && healthy
  };
}
export type ReviewState = { document: string; reviewed: boolean; report?: unknown };
export function review(): Domain<ReviewState> {
  return {
    name: 'document-review',
    observe: async state => ({ document: state.document }),
    questions: () => ({ has_owner: { type: 'noul', instructions: 'Does the document explicitly name a responsible owner?' }, has_deadline: { type: 'noul', instructions: 'Does the document explicitly state a deadline?' } }),
    decide: (_state, _evidence, result) => ({ kind: 'act', action: { tool: 'record_review', args: result.answers } }),
    permit: (_state, action) => action.tool === 'record_review',
    tools: { record_review: { validate: args => !!args && typeof args === 'object' && Object.hasOwn(args, 'has_owner') && Object.hasOwn(args, 'has_deadline'), execute: async args => ({ recorded: true, checks: args }) } },
    reduce: (state, _action, outcome) => ({ ...state, reviewed: true, report: outcome }),
    // Completion verifies report creation, not the truth of model judgments.
    verify: async state => state.reviewed && (state.report as { recorded?: boolean })?.recorded === true
  };
}
function emptyArgs(args: unknown): boolean { return !!args && typeof args === 'object' && !Array.isArray(args) && Object.keys(args).length === 0; }

// Scripted fixtures illustrate control flow. They are not an AI model or a benchmark.
export class DemoEvaluator implements Evaluator {
  async evaluate(request: Request): Promise<Evaluation> {
    if (request.questions.next) {
      const choice = (request.state as RecoveryState).restarted ? 'check' : 'restart';
      return { model: 'scripted-demo', answers: { next: { type: 'choice', choice, probabilities: { restart: choice === 'restart' ? 1 : 0, check: choice === 'check' ? 1 : 0, escalate: 0 }, confidence: 1 } } };
    }
    return { model: 'scripted-demo', answers: { has_owner: { type: 'noul', noul: 1 }, has_deadline: { type: 'noul', noul: 0 } } };
  }
}
