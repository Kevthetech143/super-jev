import test from 'node:test';
import assert from 'node:assert/strict';
import { checkCompleteness, gatherEvidence, parseProseReferences, traverse } from '../../src/enhance/evidence.ts';
import { investigations } from './fixtures/cases.ts';
import { poolFrom, returnPolicySpec, returnPolicySpecWithProse } from './fixtures/support.ts';

const pool = poolFrom(investigations);
const caseOf = (id: string) => investigations.find(c => c.id === id)!;

test('structured traversal reaches the ticket, order and policy and stops there', () => {
  const report = traverse('T1', pool, returnPolicySpec);
  assert.deepEqual(report.visitedIds, ['T1', 'O100', 'P1']);
  assert.equal(report.maxDepthReached, 2);
  assert.equal(report.truncated, false);
  assert.deepEqual(report.danglingIds, []);
});

test('unlinked noise documents are never reached, because this walks links and does not search', () => {
  const report = traverse('T1', pool, returnPolicySpec);
  assert.equal(report.visitedIds.some(id => id.startsWith('noise-')), false);
  assert.equal(pool.some(d => d.id === 'noise-0'), true, 'the noise document is in the pool, just unreferenced');
});

test('an ordinary chain passes the completeness check with its source ids kept', () => {
  const report = gatherEvidence('T1', pool, returnPolicySpec);
  assert.equal(report.complete, true);
  assert.deepEqual(report.problems, []);
  assert.deepEqual(report.sourceIds, ['T1', 'O100', 'P1']);
  assert.deepEqual(report.assignments.map(a => a.role), ['ticket', 'order', 'policy']);
});

test('a missing policy blocks: the required role is unfilled and the reference dangles', () => {
  const report = gatherEvidence('T9', pool, returnPolicySpec);
  assert.equal(report.complete, false);
  assert.match(report.problems.join(' '), /required role "policy" is not filled/);
  assert.deepEqual(report.traversal.danglingIds, ['P9']);
  assert.equal(report.sourceIds.includes('P9'), false);
});

test('two live policies with no precedence is a conflict, not a vote', () => {
  const report = gatherEvidence('T10', pool, returnPolicySpec);
  assert.equal(report.complete, false);
  assert.match(report.problems.join(' '), /role "policy" is filled by 2 live documents \(P10, P10-conflict\) with no stated precedence/);
});

test('a superseded policy is dropped, so v2-supersedes-v1 resolves to one live policy', () => {
  const report = gatherEvidence('T11', pool, returnPolicySpec);
  assert.equal(report.complete, true);
  const policy = report.assignments.find(a => a.role === 'policy')!;
  assert.deepEqual(policy.docs.map(d => d.id), ['P11']);
  assert.deepEqual(policy.supersededDocs.map(d => d.id), ['P11-old']);
  assert.match(report.warnings.join(' '), /ignored superseded document\(s\): P11-old/);
});

test('a cycle is detected, recorded and not followed', () => {
  const report = gatherEvidence('T12', pool, returnPolicySpec);
  assert.deepEqual(report.traversal.cycleEdges, [{ from: 'P12', to: 'T12' }]);
  assert.equal(report.traversal.visitedIds.filter(id => id === 'T12').length, 1);
  assert.equal(report.complete, true, 'a handled cycle does not itself make the evidence incomplete');
  assert.match(report.warnings.join(' '), /cycles detected and not followed: P12->T12/);
});

test('a prose-only reference is invisible to structured traversal', () => {
  const report = gatherEvidence('T7', pool, returnPolicySpec);
  assert.deepEqual(report.traversal.visitedIds, ['T7']);
  assert.equal(report.complete, false);
  assert.match(report.problems.join(' '), /required role "order" is not filled/);
});

test('the narrow prose parser recovers the prose-only reference', () => {
  const report = gatherEvidence('T7', pool, returnPolicySpecWithProse);
  assert.equal(report.complete, true);
  assert.deepEqual(report.sourceIds, ['T7', 'O106', 'P7']);
  assert.deepEqual(report.traversal.proseFoundIds, ['O106']);
  assert.match(report.warnings.join(' '), /found only by the narrow prose parser/);
});

test('the prose parser recovers a corrupted structured link without hiding it', () => {
  const structured = gatherEvidence('T8', pool, returnPolicySpec);
  assert.equal(structured.complete, false, 'the typo link leaves the policy role unfilled');
  const withProse = gatherEvidence('T8', pool, returnPolicySpecWithProse);
  assert.equal(withProse.complete, true);
  assert.deepEqual(withProse.sourceIds, ['T8', 'O107', 'P8']);
  assert.match(withProse.warnings.join(' '), /missing from the pool: P8-typo/);
});

test('a dangling reference alone is a warning, not a block, once the role is filled', () => {
  const report = gatherEvidence('T8', pool, returnPolicySpecWithProse);
  assert.deepEqual(report.problems, []);
  assert.ok(report.warnings.length > 0);
});

test('the prose parser matches only the two documented phrases', () => {
  assert.deepEqual(parseProseReferences('Order reference O106.'), ['O106']);
  assert.deepEqual(parseProseReferences('Applicable policy reference P7 applies.'), ['P7']);
  assert.deepEqual(parseProseReferences('order reference A1 and POLICY REFERENCE B2'), ['A1', 'B2']);
  assert.deepEqual(parseProseReferences('See order O106 for details.'), [], 'no "reference" keyword, no match');
  assert.deepEqual(parseProseReferences('The invoice reference INV9 is attached.'), [], 'only order and policy are parsed');
  assert.deepEqual(parseProseReferences('Related to ticket reference T4.'), [], 'not general entity discovery');
});

test('traversal bounds are enforced and a bound makes the evidence set partial', () => {
  const shallow = gatherEvidence('T1', pool, { ...returnPolicySpec, maxDepth: 1 });
  assert.deepEqual(shallow.traversal.visitedIds, ['T1', 'O100']);
  assert.equal(shallow.traversal.truncated, true);
  assert.match(shallow.problems.join(' '), /traversal hit a bound/);

  const narrow = traverse('T1', pool, { maxDocs: 2 });
  assert.equal(narrow.docs.length, 2);
  assert.equal(narrow.truncated, true);
});

test('a document fills at most one role, in declaration order', () => {
  const report = gatherEvidence('T10', pool, returnPolicySpec);
  const assigned = report.assignments.flatMap(a => [...a.docs, ...a.supersededDocs].map(d => d.id));
  assert.equal(new Set(assigned).size, assigned.length);
  assert.equal(report.assignments.find(a => a.role === 'order')!.docs.length, 1, 'P10-conflict mentions purchase age but is not the order');
});

test('an optional role may stay empty without blocking', () => {
  const spec = { ...returnPolicySpec, roles: [...returnPolicySpec.roles, { name: 'attachment', identify: () => false, required: false }] };
  assert.equal(gatherEvidence('T1', pool, spec).complete, true);
});

test('all twelve fixture cases resolve to a stable block-or-ask decision', () => {
  const decisions = investigations.map(c => ({ id: c.id, complete: gatherEvidence(c.id, pool, returnPolicySpecWithProse).complete }));
  const blocked = decisions.filter(d => !d.complete).map(d => d.id);
  assert.deepEqual(blocked, ['T9', 'T10'], 'only the missing-policy and conflicting-policy cases are blocked in code');
  assert.equal(decisions.length, 12);
  assert.equal(caseOf('T9').expected, 'unknown');
  assert.equal(caseOf('T10').expected, 'unknown');
});

test('checkCompleteness is pure: it takes a traversal report and adds no calls', () => {
  const walk = traverse('T1', pool, returnPolicySpec);
  assert.deepEqual(checkCompleteness(returnPolicySpec, walk).sourceIds, ['T1', 'O100', 'P1']);
});
