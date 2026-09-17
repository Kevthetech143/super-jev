export type Question =
  | { type: 'noul'; instructions: string; criteria?: { true: string; false: string } }
  | { type: 'choice'; instructions: string; criteria: Record<string, string | null> }
  | { type: 'score'; instructions: string; criteria: string[] };
export type Answer =
  | { type: 'noul'; noul: number }
  | { type: 'choice'; choice: string; probabilities: Record<string, number>; confidence: number }
  | { type: 'score'; score: number; probabilities: Record<string, number>; confidence: number };
export type Request = { state: unknown; questions: Record<string, Question> };
export type Evaluation = { model: string; answers: Record<string, Answer>; usage?: { input_tokens: number; output_tokens: number } };
export interface Evaluator { evaluate(request: Request, signal: AbortSignal): Promise<Evaluation> }
export type Action = { tool: string; args: unknown };
export type Decision = { kind: 'act'; action: Action } | { kind: 'stop'; reason: string };
export interface Tool {
  validate(args: unknown): boolean;
  execute(args: unknown, context: { signal: AbortSignal; idempotencyKey: string }): Promise<unknown>;
}
export interface Domain<S> {
  name: string;
  // Refresh data and select only the relevant context here.
  observe(state: S, signal: AbortSignal): Promise<unknown>;
  questions(state: S, evidence: unknown): Record<string, Question>;
  decide(state: S, evidence: unknown, evaluation: Evaluation): Decision;
  // Policy is authoritative even when Jev is highly confident.
  permit(state: S, action: Action): boolean;
  tools: Record<string, Tool>;
  reduce(state: S, action: Action, outcome: unknown): S;
  verify(state: S, signal: AbortSignal): Promise<boolean>;
}
export type Event = { runId: string; timestamp: string; type: string; step: number; data: unknown };
export interface Journal { append(event: Event): Promise<void> }
