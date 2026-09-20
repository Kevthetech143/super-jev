import { createHash } from 'node:crypto';
import type { Answer, Evaluator, Question, Request } from '../types.ts';

export type EvidenceRequirement = { id: string; description: string };
export type EvidenceQuote = { sourceId: string; quote: string };
export type EvidenceCandidate = { id: string; requirementIds: string[]; quotes: EvidenceQuote[] };
export type EvidencePassage = { sourceId: string; text: string; contentSHA?: string };
export type EvidenceAdjudicationStatus = 'complete' | 'partial' | 'conflicting' | 'insufficient' | 'error';
export type EvidenceFinding = EvidenceCandidate & { judgment: 'supports' | 'conflicts'; reason: string };
export type EvidenceAdjudicationResult = {
  status: EvidenceAdjudicationStatus;
  requirementsOrigin: 'caller_supplied';
  supportingEvidence: EvidenceFinding[];
  conflicts: EvidenceFinding[];
  coveredRequirementIds: string[];
  uncoveredRequirementIds: string[];
  rejected: { id: string; reason: string }[];
  calls: number;
  usage: { inputTokens: number | null; outputTokens: number | null };
  overallJudgment?: 'complete' | 'partial' | 'insufficient' | 'conflicting';
  overallConflict?: { sourceIds: string[]; reason: string };
  error?: string;
};
export type EvidenceAdjudicationInput = {
  question: string;
  requirements: EvidenceRequirement[];
  passages: EvidencePassage[];
  candidates: EvidenceCandidate[];
};

export class EvidenceAdjudicationError extends Error {}

const MAX_PASSAGES = 80;
const MAX_CANDIDATES = 128;
const MAX_TEXT_CHARS = 200_000;

function choice(answer: Answer | undefined): string | null {
  return answer?.type === 'choice' ? answer.choice : null;
}

function validate(input: EvidenceAdjudicationInput): Map<string, string> {
  if (!input || typeof input.question !== 'string' || !input.question.trim()) throw new EvidenceAdjudicationError('question must be non-empty');
  if (!Array.isArray(input.requirements) || !input.requirements.length) throw new EvidenceAdjudicationError('requirements must be a non-empty caller-supplied array');
  if (!Array.isArray(input.passages) || input.passages.length > MAX_PASSAGES) throw new EvidenceAdjudicationError(`passages must contain at most ${MAX_PASSAGES} items`);
  if (!Array.isArray(input.candidates) || input.candidates.length > MAX_CANDIDATES) throw new EvidenceAdjudicationError(`candidates must contain at most ${MAX_CANDIDATES} items`);
  if (JSON.stringify(input).length > MAX_TEXT_CHARS) throw new EvidenceAdjudicationError(`input exceeds ${MAX_TEXT_CHARS} characters`);
  const requirementIds = new Set<string>();
  for (const r of input.requirements) {
    if (!r?.id || !r.description) throw new EvidenceAdjudicationError('every requirement needs id and description');
    if (requirementIds.has(r.id)) throw new EvidenceAdjudicationError('duplicate requirement id');
    requirementIds.add(r.id);
  }
  const passages = new Map<string, string>();
  for (const p of input.passages) {
    if (!p?.sourceId || typeof p.text !== 'string') throw new EvidenceAdjudicationError('every passage needs sourceId and text');
    if (passages.has(p.sourceId)) throw new EvidenceAdjudicationError('duplicate sourceId');
    if (p.contentSHA !== undefined && p.contentSHA !== createHash('sha256').update(p.text, 'utf8').digest('hex')) {
      throw new EvidenceAdjudicationError('a passage contentSHA does not bind its supplied text');
    }
    passages.set(p.sourceId, p.text);
  }
  const candidateIds = new Set<string>();
  for (const c of input.candidates) {
    if (!c?.id || candidateIds.has(c.id)) throw new EvidenceAdjudicationError('invalid or duplicate candidate id');
    candidateIds.add(c.id);
    if (!Array.isArray(c.requirementIds) || !c.requirementIds.length || c.requirementIds.some(id => !requirementIds.has(id))) {
      throw new EvidenceAdjudicationError('a candidate has an empty or unknown requirement id');
    }
    if (!Array.isArray(c.quotes) || !c.quotes.length) throw new EvidenceAdjudicationError('a candidate needs at least one quote');
    for (const q of c.quotes) {
      const source = passages.get(q.sourceId);
      if (source === undefined) throw new EvidenceAdjudicationError('a candidate cites an unknown source');
      if (!q.quote || !source.includes(q.quote)) throw new EvidenceAdjudicationError('a candidate quote is not an exact span of its source');
    }
  }
  return passages;
}

/**
 * Opt-in semantic check after retrieval. Requirements and reviewed passages
 * are caller-owned trust inputs; this function does not retrieve, prepare, or
 * approve them. Exact quote binding and coverage bookkeeping are checked in
 * code. Semantic relevance and conflict judgments remain model judgments.
 */
export async function adjudicateEvidence(
  input: EvidenceAdjudicationInput,
  options: { transport: Evaluator; timeoutMs?: number; threshold?: number }
): Promise<EvidenceAdjudicationResult> {
  validate(input);
  if (!options?.transport) throw new EvidenceAdjudicationError('transport is required');
  const threshold = options.threshold ?? 0.80;
  if (typeof threshold !== 'number' || threshold < 0 || threshold > 1) throw new EvidenceAdjudicationError('threshold must be between 0 and 1');
  const dedupe = new Set<string>();
  const duplicateRejected: { id: string; reason: string }[] = [];
  const candidates = input.candidates.filter(c => {
    const key = JSON.stringify([c.requirementIds.slice().sort(), c.quotes]);
    if (dedupe.has(key)) { duplicateRejected.push({ id: c.id, reason: 'exact duplicate candidate' }); return false; }
    dedupe.add(key); return true;
  });
  const state = {
    originalQuestion: input.question,
    requirements: input.requirements,
    sourcePassages: input.passages,
    proposedEvidence: candidates
  };
  const questions: Record<string, Question> = {};
  for (const c of candidates) questions[`candidate:${c.id}`] = {
    type: 'choice',
    instructions: `Judge proposed evidence ${JSON.stringify(c.id)} against the ORIGINAL question, its named caller-supplied requirements, and the FULL source passages in state. SUPPORTS only if every quote is relevant to the same entity, status, and time and together directly supports every named requirement. Treat a question, possibility, plan, order, recommendation, and completed result as different statuses. CONFLICTS only when the quoted evidence directly gives incompatible answers for the same entity/status/time; attribute the conflict to these sources and do not resolve it. Otherwise choose INSUFFICIENT. Exact quote presence is checked separately in code.`,
    criteria: {
      SUPPORTS: 'directly supports every named requirement with matching entity, status, and time',
      CONFLICTS: 'directly conflicts for the same entity, status, and time',
      INSUFFICIENT: 'irrelevant, ambiguous, wrong status/time/entity, or only partly supports the named requirements'
    }
  };
  if (!candidates.length) return finish(input, candidates, {}, 0, { inputTokens: null, outputTokens: null }, threshold, duplicateRejected);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? 30_000);
  let attemptedCalls = 0;
  try {
    attemptedCalls++;
    const evaluation = await options.transport.evaluate({ state, questions } satisfies Request, controller.signal);
    const first = finish(input, candidates, evaluation.answers, 1, {
      inputTokens: evaluation.usage?.input_tokens ?? null,
      outputTokens: evaluation.usage?.output_tokens ?? null
    }, threshold, duplicateRejected);
    if (first.status === 'error' || !first.supportingEvidence.length || first.conflicts.length) return first;
    const keptSourceIds = new Set(first.supportingEvidence.flatMap(x => x.quotes.map(q => q.sourceId)));
    attemptedCalls++;
    const overall = await options.transport.evaluate({
      state: { originalQuestion: input.question, requirements: input.requirements, sourcePassages: input.passages.filter(p => keptSourceIds.has(p.sourceId)), keptSupportingEvidence: first.supportingEvidence },
      questions: { overall: { type: 'choice', instructions: 'Considering ONLY keptSupportingEvidence and its full source passages, does it fully answer the ORIGINAL question? COMPLETE requires every part of the original question, matching entity/status/time. PARTIAL answers only part. CONFLICTING contains incompatible answers for the same entity/status/time. Otherwise INSUFFICIENT. Caller-supplied requirements are an aid and may omit part of the original question.', criteria: { COMPLETE: 'fully answers every part of the original question', PARTIAL: 'answers some but not all', CONFLICTING: 'contains a directly relevant unresolved conflict', INSUFFICIENT: 'does not answer the question' } } }
    }, controller.signal);
    const a = overall.answers.overall;
    if (a?.type !== 'choice' || !['COMPLETE', 'PARTIAL', 'CONFLICTING', 'INSUFFICIENT'].includes(a.choice) || !Number.isFinite(a.confidence) || a.confidence < 0 || a.confidence > 1) {
      return { ...first, status: 'error', calls: 2, usage: addUsage(first.usage, overall.usage), error: 'missing or malformed overall judge answer' };
    }
    if (a.confidence < threshold) {
      return { ...first, status: 'error', calls: 2, usage: addUsage(first.usage, overall.usage), error: `overall judge answer below threshold ${threshold}` };
    }
    const overallJudgment = a.choice.toLowerCase() as 'complete' | 'partial' | 'conflicting' | 'insufficient';
    const status: EvidenceAdjudicationStatus = overallJudgment === 'conflicting' ? 'conflicting'
      : overallJudgment === 'complete' && first.uncoveredRequirementIds.length === 0 ? 'complete'
      : overallJudgment === 'insufficient' ? 'insufficient' : 'partial';
    return { ...first, status, overallJudgment, calls: 2, usage: addUsage(first.usage, overall.usage), ...(overallJudgment === 'conflicting' ? { overallConflict: { sourceIds: [...keptSourceIds], reason: 'whole-question judge found an unresolved conflict within kept support' } } : {}) };
  } catch (err) {
    return {
      status: 'error', requirementsOrigin: 'caller_supplied', supportingEvidence: [], conflicts: [],
      coveredRequirementIds: [], uncoveredRequirementIds: input.requirements.map(r => r.id), rejected: [], calls: attemptedCalls,
      usage: { inputTokens: null, outputTokens: null }, error: err instanceof Error ? err.message : String(err)
    };
  } finally { clearTimeout(timer); }
}

function addUsage(a: { inputTokens: number | null; outputTokens: number | null }, b?: { input_tokens: number; output_tokens: number }): { inputTokens: number | null; outputTokens: number | null } {
  return { inputTokens: a.inputTokens === null || !b ? null : a.inputTokens + b.input_tokens, outputTokens: a.outputTokens === null || !b ? null : a.outputTokens + b.output_tokens };
}

function finish(input: EvidenceAdjudicationInput, candidates: EvidenceCandidate[], answers: Record<string, Answer>, calls: number, usage: { inputTokens: number | null; outputTokens: number | null }, threshold: number, initialRejected: { id: string; reason: string }[]): EvidenceAdjudicationResult {
  const supportingEvidence: EvidenceFinding[] = [];
  const conflicts: EvidenceFinding[] = [];
  const rejected = initialRejected.slice();
  let invalid = false;
  for (const c of candidates) {
    const raw = answers[`candidate:${c.id}`];
    const verdict = choice(raw);
    const malformed = raw?.type !== 'choice' || !['SUPPORTS', 'CONFLICTS', 'INSUFFICIENT'].includes(verdict ?? '') || !Number.isFinite(raw.confidence) || raw.confidence < 0 || raw.confidence > 1;
    if (malformed) {
      invalid = true;
      rejected.push({ id: c.id, reason: 'missing or malformed judge answer' });
      continue;
    }
    if (!malformed && raw.confidence < threshold) {
      rejected.push({ id: c.id, reason: `judge answer below threshold ${threshold}` });
      continue;
    }
    const finding = { ...c, judgment: verdict === 'CONFLICTS' ? 'conflicts' as const : 'supports' as const, reason: verdict ?? 'missing judge answer' };
    if (verdict === 'SUPPORTS') supportingEvidence.push(finding);
    else if (verdict === 'CONFLICTS') conflicts.push(finding);
    else rejected.push({ id: c.id, reason: verdict === 'INSUFFICIENT' ? 'insufficient semantic support' : 'missing or malformed judge answer' });
  }
  const covered = new Set(supportingEvidence.flatMap(x => x.requirementIds));
  const coveredRequirementIds = input.requirements.map(r => r.id).filter(id => covered.has(id));
  const uncoveredRequirementIds = input.requirements.map(r => r.id).filter(id => !covered.has(id));
  const status: EvidenceAdjudicationStatus = invalid ? 'error' : conflicts.length ? 'conflicting'
    : !coveredRequirementIds.length ? 'insufficient'
    : uncoveredRequirementIds.length ? 'partial' : 'complete';
  return { status, requirementsOrigin: 'caller_supplied', supportingEvidence, conflicts, coveredRequirementIds, uncoveredRequirementIds, rejected, calls, usage, ...(invalid ? { error: 'missing, malformed, or below-threshold candidate judge answer' } : {}) };
}
