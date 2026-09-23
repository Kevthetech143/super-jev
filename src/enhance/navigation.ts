import type { Evaluator, Request } from '../types.ts';

/** A deliberately small, data-only hierarchy for bounded source navigation. */
export type NavigationNode = {
  id: string;
  label: string;
  description: string;
  children?: string[];
  sourceId?: string;
};
export type NavigationCatalog = {
  version: 1;
  structure: 'flat-files' | 'folder-tree';
  rootId: 'root';
  nodes: NavigationNode[];
};
export type NavigationLimits = { beamWidth?: number; maxRounds?: number; maxResults?: number };
export type NavigationTrace = {
  path: string[];
  choices: { nodeId: string; probabilities: Record<string, number>; none: number }[];
  discarded: number;
  remaining: number;
};
export type NavigationResult = {
  status: 'candidates' | 'no-candidates' | 'budget-exhausted';
  candidates: { sourceId: string; nodeId: string; path: string[]; score: number }[];
  calls: number;
  trace: NavigationTrace[];
  complete: false;
  message: string;
};
export type NavigationOptions = NavigationLimits & {
  transport: Evaluator;
  timeoutMs?: number;
  signal?: AbortSignal;
};

export const DEFAULT_NAVIGATION_LIMITS = { beamWidth: 3, maxRounds: 6, maxResults: 3 } as const;
const MAX_NODES = 200;
const MAX_LEAVES = 50;
const MAX_DESCRIPTION = 4000;
const MAX_QUESTION = 8000;
const NONE_ID = 'o_none';

export class NavigationError extends Error {}

type CheckedCatalog = { nodes: Map<string, NavigationNode> };
type Candidate = { nodeId: string; path: string[]; logTotal: number; decisions: number; score: number; trace: NavigationTrace[] };

/** A short, content-free cause for a provider failure: HTTP status, missing key or no network. */
export function providerFailureReason(error: unknown): string {
  const message = error instanceof Error ? error.message : '';
  const http = /Jev HTTP (\d{3})/.exec(message);
  if (http) return `Jev HTTP ${http[1]}${http[1] === '401' ? ' (TypeSafe rejected the API key)' : ''}`;
  if (message.endsWith('contains a secret; not sent')) return 'request contains a secret; not sent';
  if (message.startsWith('Set TYPESAFE_API_KEY')) return 'TYPESAFE_API_KEY is not set';
  if (error instanceof TypeError) return 'could not reach TypeSafe (network)';
  return 'unknown cause';
}

function cleanError(error: unknown): NavigationError {
  if (error instanceof NavigationError) return error;
  if (error instanceof Error && error.name === 'AbortError') return new NavigationError('Navigation provider timed out');
  return new NavigationError(`Navigation provider failed: ${providerFailureReason(error)}`);
}

function positiveInteger(value: unknown, name: string, min: number, max: number, fallback: number): number {
  if (value === undefined) return fallback;
  if (!Number.isInteger(value) || (value as number) < min || (value as number) > max) {
    throw new NavigationError(`${name} must be an integer from ${min} to ${max}`);
  }
  return value as number;
}

export function validateNavigationCatalog(catalog: unknown): CheckedCatalog {
  if (!catalog || typeof catalog !== 'object') throw new NavigationError('Navigation catalog is invalid');
  const c = catalog as Partial<NavigationCatalog>;
  if (Object.keys(c).some(key => !['version', 'structure', 'rootId', 'nodes'].includes(key))) throw new NavigationError('Navigation catalog is invalid');
  if (c.version !== 1 || (c.structure !== 'flat-files' && c.structure !== 'folder-tree') || c.rootId !== 'root' || !Array.isArray(c.nodes) || c.nodes.length === 0 || c.nodes.length > MAX_NODES) {
    throw new NavigationError('Navigation catalog is invalid');
  }
  const nodes = new Map<string, NavigationNode>();
  const sourceIds = new Set<string>();
  for (const value of c.nodes) {
    if (!value || typeof value !== 'object') throw new NavigationError('Navigation catalog is invalid');
    const node = value as NavigationNode;
    if (Object.keys(node).some(key => !['id', 'label', 'description', 'children', 'sourceId'].includes(key)) || typeof node.id !== 'string' || !node.id || typeof node.label !== 'string' || !node.label || typeof node.description !== 'string' || node.description.length > MAX_DESCRIPTION || nodes.has(node.id)) throw new NavigationError('Navigation catalog is invalid');
    const hasChildren = node.children !== undefined;
    if (hasChildren && (!Array.isArray(node.children) || node.children.length === 0 || !node.children.every(id => typeof id === 'string' && id))) throw new NavigationError('Navigation catalog is invalid');
    if (hasChildren && node.sourceId !== undefined) throw new NavigationError('Navigation catalog is invalid');
    if (!hasChildren && (typeof node.sourceId !== 'string' || !node.sourceId || sourceIds.has(node.sourceId))) throw new NavigationError('Navigation catalog is invalid');
    if (node.sourceId !== undefined) sourceIds.add(node.sourceId);
    nodes.set(node.id, { id: node.id, label: node.label, description: node.description, ...(hasChildren ? { children: [...node.children!] } : {}), ...(node.sourceId !== undefined ? { sourceId: node.sourceId } : {}) });
  }
  const root = nodes.get('root');
  if (!root || !root.children || root.sourceId !== undefined || sourceIds.size > MAX_LEAVES) throw new NavigationError('Navigation catalog is invalid');
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const visit = (id: string) => {
    if (visiting.has(id)) throw new NavigationError('Navigation catalog is invalid');
    if (visited.has(id)) throw new NavigationError('Navigation catalog is invalid'); // a tree has one parent
    const node = nodes.get(id);
    if (!node) throw new NavigationError('Navigation catalog is invalid');
    visiting.add(id);
    if (node.children) for (const child of node.children) visit(child);
    visiting.delete(id);
    visited.add(id);
  };
  visit('root');
  if (visited.size !== nodes.size) throw new NavigationError('Navigation catalog is invalid');
  return { nodes };
}

function checkedLimits(limits: NavigationLimits | undefined) {
  return {
    beamWidth: positiveInteger(limits?.beamWidth, 'beamWidth', 1, 5, DEFAULT_NAVIGATION_LIMITS.beamWidth),
    maxRounds: positiveInteger(limits?.maxRounds, 'maxRounds', 1, 10, DEFAULT_NAVIGATION_LIMITS.maxRounds),
    maxResults: positiveInteger(limits?.maxResults, 'maxResults', 1, 10, DEFAULT_NAVIGATION_LIMITS.maxResults)
  };
}

function score(logTotal: number, decisions: number): number { return decisions ? Math.exp(logTotal / decisions) : 1; }
function compareCandidates(a: Candidate, b: Candidate): number { return b.score - a.score || a.path.join('\u0000').localeCompare(b.path.join('\u0000')); }

/**
 * Follow only catalog edges. Every provider option is generated locally; data
 * can supply labels/descriptions but can never supply an executable option id.
 */
export async function navigate(catalogInput: unknown, question: unknown, options: NavigationOptions): Promise<NavigationResult> {
  const catalog = validateNavigationCatalog(catalogInput);
  if (typeof question !== 'string' || !question.trim() || question.length > MAX_QUESTION) throw new NavigationError('Navigation question is invalid');
  if (!options?.transport) throw new NavigationError('Navigation transport is required');
  const limits = checkedLimits(options);
  const timeoutMs = options.timeoutMs ?? 5_000;
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 120_000) throw new NavigationError('Navigation timeout is invalid');

  let calls = 0;
  const trace: NavigationTrace[] = [];
  let beam: Candidate[] = [{ nodeId: 'root', path: ['root'], logTotal: 0, decisions: 0, score: 1, trace: [] }];
  const leaves: Candidate[] = [];
  try {
    for (let round = 0; round < limits.maxRounds; round++) {
      if (options.signal?.aborted) throw new NavigationError('Navigation provider timed out');
      const expandable = beam.filter(candidate => !!catalog.nodes.get(candidate.nodeId)?.children);
      const finished = beam.filter(candidate => !catalog.nodes.get(candidate.nodeId)?.children);
      leaves.push(...finished);
      if (!expandable.length) break;

      const questions: Request['questions'] = {};
      const mappings = new Map<string, { candidate: Candidate; childIds: string[] }>();
      for (let i = 0; i < expandable.length; i++) {
        const candidate = expandable[i]!;
        const branch = catalog.nodes.get(candidate.nodeId)!;
        const childIds = branch.children!;
        const criteria: Record<string, string> = { [NONE_ID]: 'None of these direct children fits the question.' };
        childIds.forEach((childId, childIndex) => {
          const child = catalog.nodes.get(childId)!;
          criteria[`o_${childIndex}`] = `${child.label}: ${child.description}`;
        });
        const key = `branch_${i}`;
        questions[key] = { type: 'choice', instructions: 'Choose the most relevant direct child for navigation. This is routing only; it does not establish that any source answers the question.', criteria };
        mappings.set(key, { candidate, childIds });
      }
      const request: Request = { state: { question, purpose: 'bounded source navigation; select direct catalog children only' }, questions };
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      const abort = () => controller.abort();
      options.signal?.addEventListener('abort', abort, { once: true });
      let evaluation;
      try { evaluation = await options.transport.evaluate(request, controller.signal); }
      finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort); }
      calls++;
      if (!evaluation || typeof evaluation !== 'object' || typeof evaluation.model !== 'string' || !evaluation.answers || typeof evaluation.answers !== 'object') throw new NavigationError('Navigation provider returned an invalid response');

      const expanded: Candidate[] = [];
      const roundTrace: NavigationTrace[] = [];
      for (const [key, mapping] of mappings) {
        const answer = evaluation.answers[key];
        if (!answer || answer.type !== 'choice') throw new NavigationError('Navigation provider returned an invalid response');
        const keys = [NONE_ID, ...mapping.childIds.map((_, i) => `o_${i}`)];
        if (!keys.includes(answer.choice) || !Number.isFinite(answer.confidence) || answer.confidence < 0 || answer.confidence > 1 || !answer.probabilities || Object.keys(answer.probabilities).length !== keys.length || keys.some(k => !Number.isFinite(answer.probabilities[k]) || answer.probabilities[k] < 0 || answer.probabilities[k] > 1)) throw new NavigationError('Navigation provider returned an invalid response');
        const total = keys.reduce((sum, key) => sum + answer.probabilities[key]!, 0);
        if (Math.abs(total - 1) > 0.010000001) throw new NavigationError('Navigation provider returned an invalid response');
        const peak = Math.max(...keys.map(key => answer.probabilities[key]!));
        if (answer.probabilities[answer.choice]! + 0.010000001 < peak) throw new NavigationError('Navigation provider returned an invalid response');
        const probabilities = Object.fromEntries(mapping.childIds.map((id, i) => [id, answer.probabilities[`o_${i}`]!])) as Record<string, number>;
        const branchTrace: NavigationTrace = { path: mapping.candidate.path, choices: [{ nodeId: mapping.candidate.nodeId, probabilities, none: answer.probabilities[NONE_ID]! }], discarded: 0, remaining: 0 };
        trace.push(branchTrace);
        roundTrace.push(branchTrace);
        // `choice` is the provider's argmax (checked above). When none wins,
        // this branch has no selected catalog edge and cannot become a source
        // candidate merely because its losing children carry small mass.
        if (answer.choice === NONE_ID) continue;
        for (const [i, childId] of mapping.childIds.entries()) {
          const p = answer.probabilities[`o_${i}`]!;
          const decisions = mapping.candidate.decisions + 1;
          expanded.push({ nodeId: childId, path: [...mapping.candidate.path, childId], logTotal: mapping.candidate.logTotal + Math.log(Math.max(p, Number.MIN_VALUE)), decisions, score: score(mapping.candidate.logTotal + Math.log(Math.max(p, Number.MIN_VALUE)), decisions), trace: [...mapping.candidate.trace, branchTrace] });
        }
      }
      const ranked = [...leaves, ...expanded].sort(compareCandidates);
      const kept = ranked.slice(0, limits.beamWidth);
      const discarded = ranked.length - kept.length;
      for (const branchTrace of roundTrace) { branchTrace.discarded = discarded; branchTrace.remaining = kept.length; }
      beam = kept.filter(candidate => !!catalog.nodes.get(candidate.nodeId)?.children);
      leaves.length = 0;
      leaves.push(...kept.filter(candidate => !catalog.nodes.get(candidate.nodeId)?.children));
      if (!beam.length) break;
    }
  } catch (error) { throw cleanError(error); }

  const remaining = beam.length > 0;
  const candidates = leaves.sort(compareCandidates).slice(0, limits.maxResults).map(candidate => ({ sourceId: catalog.nodes.get(candidate.nodeId)!.sourceId!, nodeId: candidate.nodeId, path: candidate.path, score: candidate.score }));
  if (remaining) return { status: 'budget-exhausted', candidates, calls, trace, complete: false, message: 'Navigation reached its round budget; returned only reached source candidates.' };
  if (!candidates.length) return { status: 'no-candidates', candidates: [], calls, trace, complete: false, message: 'No source candidates were reached; this is not proof that no source can answer the question.' };
  return { status: 'candidates', candidates, calls, trace, complete: false, message: 'Source candidates are navigation leads only; they have not been read or verified.' };
}
