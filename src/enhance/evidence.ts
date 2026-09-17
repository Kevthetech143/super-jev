/**
 * Required-evidence checks for linked-evidence tasks.
 *
 * The premise from the pilot: handing the model more documents does not make an
 * answer safe. Even an oracle evidence set produced a confident wrong answer
 * when the governing policy was simply absent. So completeness is checked in
 * code, before the final question is asked, and an incomplete or ambiguous
 * evidence set produces `insufficient evidence` rather than an action.
 */

export type SourceDoc = { id: string; text: string; links?: string[] };

export type EvidenceRole = {
  /** Role name used in the report, e.g. 'ticket', 'order', 'policy'. */
  name: string;
  /** Does this document fill the role? Roles are tried in declaration order. */
  identify: (doc: SourceDoc, rootId: string) => boolean;
  /** Default true. A required role left unfilled blocks the final question. */
  required?: boolean;
  /**
   * Default true. When more than one live document fills the role and no
   * supersession resolves it, that is a conflict, not a majority vote.
   */
  unique?: boolean;
};

export type EvidenceSpec = {
  name: string;
  /** Ordered roles, e.g. ticket then order then policy. */
  roles: EvidenceRole[];
  /** Bounded traversal limits. */
  maxDepth?: number;
  maxDocs?: number;
  /**
   * Opt-in narrow prose-reference parsing. OFF by default.
   * See parseProseReferences for exactly how narrow it is.
   */
  proseReferences?: boolean;
  /** Words that mark a document as retired. Default: the word "superseded". */
  supersededPattern?: RegExp;
  /**
   * What a dangling reference means when the role it pointed at ended up filled
   * another way. Default "incomplete", the safer reading.
   *
   * "incomplete" blocks: a reference the data itself makes to a document that
   * is not in the pool means the evidence set is, on the data's own account,
   * missing something. Whether the missing document would have changed the
   * answer is exactly what cannot be known without reading it.
   *
   * "warning" keeps the reference as a data-quality note that travels with the
   * result and does not block. Choose it for a corpus where broken links are
   * known to be routine noise, and accept that a genuinely relevant missing
   * document then reaches the model as a note instead of a block.
   */
  danglingReferenceIs?: 'warning' | 'incomplete';
};

/** The safer reading is the default: a reference to a document nobody holds blocks. */
export const DEFAULT_DANGLING_REFERENCE_IS = 'incomplete' as const;

export type TraversalReport = {
  rootId: string;
  /** Ids reached, in visit order. */
  visitedIds: string[];
  docs: SourceDoc[];
  /** Referenced ids with no document behind them. */
  danglingIds: string[];
  /** Edges that pointed back into an already-visited id. */
  cycleEdges: { from: string; to: string }[];
  /** Ids found only by the prose parser, never by a structured link. */
  proseFoundIds: string[];
  maxDepthReached: number;
  /** True when a limit stopped the walk, so the evidence set may be partial. */
  truncated: boolean;
};

export type RoleAssignment = {
  role: string;
  /** Live documents filling the role, after superseded ones are removed. */
  docs: SourceDoc[];
  /** Documents that filled the role but are marked superseded. */
  supersededDocs: SourceDoc[];
};

export type CompletenessReport = {
  spec: string;
  traversal: TraversalReport;
  assignments: RoleAssignment[];
  /** Ids of every live document that filled a role. Kept for the audit trail. */
  sourceIds: string[];
  /** Empty means the final question may be asked. */
  problems: string[];
  /**
   * Data-quality notes. Everything here is recorded whether or not it blocks:
   * a dangling reference appears here always, and also in `problems` when
   * `danglingReferenceIs` is "incomplete". Read `problems` to know what blocks;
   * read this to know what was odd about the evidence.
   */
  warnings: string[];
  complete: boolean;
};

const DEFAULT_SUPERSEDED = /\bsuperseded\b/i;

/**
 * Narrow prose-reference parsing. It matches ONLY the two explicit phrases
 * "<word> reference <ID>" for the words order and policy, for example
 * "Order reference O106" or "applicable policy reference P7".
 *
 * This is NOT general entity discovery and NOT relationship extraction. It was
 * written after seeing these two phrasings in the pilot corpus, it will miss
 * any other phrasing, and a corpus using different words needs its own parser.
 * It is off unless a spec sets proseReferences true.
 */
export function parseProseReferences(text: string): string[] {
  const found: string[] = [];
  const pattern = /\b(?:order|policy)\s+reference\s+([A-Za-z0-9][A-Za-z0-9_-]{0,63})\b/gi;
  for (const match of text.matchAll(pattern)) {
    const id = match[1].replace(/[.,;:]+$/, '');
    if (id && !found.includes(id)) found.push(id);
  }
  return found;
}

/**
 * Breadth-first walk of existing structured links from a single root, bounded
 * by depth and document count, with cycle detection. It follows references that
 * are already in the data. It does not search, rank or retrieve.
 */
export function traverse(rootId: string, pool: Iterable<SourceDoc>, spec: Pick<EvidenceSpec, 'maxDepth' | 'maxDocs' | 'proseReferences'> = {}): TraversalReport {
  const maxDepth = spec.maxDepth ?? 3;
  const maxDocs = spec.maxDocs ?? 16;
  const index = new Map<string, SourceDoc>();
  for (const doc of pool) if (!index.has(doc.id)) index.set(doc.id, doc);

  const visited = new Set<string>();
  const visitedIds: string[] = [];
  const docs: SourceDoc[] = [];
  const danglingIds: string[] = [];
  const cycleEdges: { from: string; to: string }[] = [];
  const proseFoundIds: string[] = [];
  const queue: { id: string; depth: number; from?: string }[] = [{ id: rootId, depth: 0 }];
  let maxDepthReached = 0;
  let truncated = false;

  while (queue.length) {
    const { id, depth, from } = queue.shift()!;
    if (visited.has(id)) { if (from) cycleEdges.push({ from, to: id }); continue; }
    if (docs.length >= maxDocs) { truncated = true; break; }
    visited.add(id);
    visitedIds.push(id);
    const doc = index.get(id);
    if (!doc) { if (!danglingIds.includes(id)) danglingIds.push(id); continue; }
    docs.push(doc);
    maxDepthReached = Math.max(maxDepthReached, depth);
    if (depth >= maxDepth) { if ((doc.links?.length ?? 0) > 0) truncated = true; continue; }
    const structured = doc.links ?? [];
    const prose = spec.proseReferences ? parseProseReferences(doc.text).filter(x => !structured.includes(x)) : [];
    for (const id2 of prose) if (!proseFoundIds.includes(id2)) proseFoundIds.push(id2);
    for (const next of [...structured, ...prose]) queue.push({ id: next, depth: depth + 1, from: id });
  }
  return { rootId, visitedIds, docs, danglingIds, cycleEdges, proseFoundIds, maxDepthReached, truncated };
}

/**
 * Check the evidence set against the spec in code.
 *
 * Reported as a problem, each of which blocks the final question:
 * - a required role no live document fills;
 * - a unique role filled by two or more live documents with no supersession to
 *   break the tie (the conflicting-policy shape);
 * - a truncated walk, because a partial evidence set cannot be called complete.
 *
 * A dangling reference: when it is the reason a required role is empty, the
 * role check already blocks either way. When the role was filled another way,
 * `spec.danglingReferenceIs` decides. It defaults to "incomplete", so the
 * broken reference blocks, because the data itself says a document is missing
 * and nobody can tell from here whether that document mattered. Set it to
 * "warning" to get the other reading, a note that travels and does not block.
 *
 * A document marked superseded is removed from its role and listed separately,
 * so "v2 supersedes v1" resolves to one live policy rather than a conflict.
 */
export function checkCompleteness(spec: EvidenceSpec, traversal: TraversalReport): CompletenessReport {
  const superseded = spec.supersededPattern ?? DEFAULT_SUPERSEDED;
  const assignments: RoleAssignment[] = spec.roles.map(r => ({ role: r.name, docs: [], supersededDocs: [] }));
  const claimed = new Set<string>();

  for (const doc of traversal.docs) {
    for (let i = 0; i < spec.roles.length; i++) {
      if (claimed.has(doc.id)) break;
      if (!spec.roles[i].identify(doc, traversal.rootId)) continue;
      claimed.add(doc.id);
      if (superseded.test(doc.text)) assignments[i].supersededDocs.push(doc);
      else assignments[i].docs.push(doc);
    }
  }

  const problems: string[] = [];
  for (let i = 0; i < spec.roles.length; i++) {
    const role = spec.roles[i];
    const assignment = assignments[i];
    const required = role.required ?? true;
    const unique = role.unique ?? true;
    if (required && assignment.docs.length === 0) {
      problems.push(assignment.supersededDocs.length
        ? `required role "${role.name}" has only superseded documents (${assignment.supersededDocs.map(d => d.id).join(', ')})`
        : `required role "${role.name}" is not filled by any reachable document`);
    }
    if (unique && assignment.docs.length > 1) {
      problems.push(`role "${role.name}" is filled by ${assignment.docs.length} live documents (${assignment.docs.map(d => d.id).join(', ')}) with no stated precedence`);
    }
  }
  if (traversal.truncated) problems.push(`traversal hit a bound (maxDepth=${spec.maxDepth ?? 3}, maxDocs=${spec.maxDocs ?? 16}) so the evidence set may be partial`);

  const warnings: string[] = [];
  const danglingIs = spec.danglingReferenceIs ?? DEFAULT_DANGLING_REFERENCE_IS;
  if (traversal.danglingIds.length) {
    const note = `referenced documents are missing from the pool: ${traversal.danglingIds.join(', ')}`;
    // Recorded as a warning either way, so a reader scanning the data-quality
    // notes still sees it without having to know how the flag was set.
    warnings.push(note);
    if (danglingIs === 'incomplete') problems.push(`${note}; danglingReferenceIs is "incomplete", so a reference the data makes to a document nobody holds blocks the final question`);
  }
  if (traversal.cycleEdges.length) warnings.push(`cycles detected and not followed: ${traversal.cycleEdges.map(e => `${e.from}->${e.to}`).join(', ')}`);
  for (const assignment of assignments) {
    if (assignment.supersededDocs.length && assignment.docs.length) warnings.push(`role "${assignment.role}" ignored superseded document(s): ${assignment.supersededDocs.map(d => d.id).join(', ')}`);
  }
  if (traversal.proseFoundIds.length) warnings.push(`ids found only by the narrow prose parser, not by a structured link: ${traversal.proseFoundIds.join(', ')}`);

  const sourceIds = assignments.flatMap(a => a.docs.map(d => d.id));
  return { spec: spec.name, traversal, assignments, sourceIds, problems, warnings, complete: problems.length === 0 };
}

/** Gather and check in one step. */
export function gatherEvidence(rootId: string, pool: Iterable<SourceDoc>, spec: EvidenceSpec): CompletenessReport {
  return checkCompleteness(spec, traverse(rootId, pool, spec));
}
