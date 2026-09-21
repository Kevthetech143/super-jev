import test from 'node:test';
import assert from 'node:assert/strict';
import { navigate, validateNavigationCatalog, NavigationError } from '../../src/enhance/navigation.ts';
import type { Evaluator, Request } from '../../src/types.ts';

const catalog = {
  version: 1 as const, structure: 'folder-tree' as const, rootId: 'root' as const,
  nodes: [
    { id: 'root', label: 'root', description: 'root', children: ['work', 'home'] },
    { id: 'work', label: 'Work', description: 'work files', children: ['report', 'code'] },
    { id: 'home', label: 'Home', description: 'home files', children: ['receipt'] },
    { id: 'report', label: 'Report', description: 'monthly report', sourceId: 'source-report' },
    { id: 'code', label: 'Code', description: 'application code', sourceId: 'source-code' },
    { id: 'receipt', label: 'Receipt', description: 'store receipt', sourceId: 'source-receipt' }
  ]
};

function option(question: Request['questions'][string], label: string): string {
  assert.equal(question.type, 'choice');
  const found = Object.entries(question.criteria).find(([, value]) => value?.startsWith(`${label}:`));
  assert.ok(found, `missing ${label}`);
  return found[0];
}
function distribution(question: Request['questions'][string], selected: string, selectedP: number) {
  assert.equal(question.type, 'choice');
  const keys = Object.keys(question.criteria);
  const rest = (1 - selectedP) / (keys.length - 1);
  return { type: 'choice' as const, choice: selected, confidence: selectedP, probabilities: Object.fromEntries(keys.map(k => [k, k === selected ? selectedP : rest])) };
}

test('navigates two levels using direct-child choices and never sends source ids', async () => {
  const seen: Request[] = [];
  const transport: Evaluator = { evaluate: async request => {
    seen.push(request);
    const answers: Record<string, ReturnType<typeof distribution>> = {};
    for (const [key, question] of Object.entries(request.questions)) {
      answers[key] = distribution(question, option(question, Object.values(question.criteria).some(v => v?.startsWith('Work:')) ? 'Work' : 'Report'), .9);
    }
    return { model: 'fake', answers };
  } };
  const result = await navigate(catalog, 'find the monthly report', { transport, beamWidth: 1 });
  assert.equal(result.status, 'candidates');
  assert.deepEqual(result.candidates.map(c => c.sourceId), ['source-report']);
  assert.deepEqual(result.candidates[0]?.path, ['root', 'work', 'report']);
  assert.equal(result.calls, 2);
  assert.equal(JSON.stringify(seen).includes('source-report'), false);
});

test('beam retains a plausible alternate branch', async () => {
  const transport: Evaluator = { evaluate: async request => {
    const answers: Record<string, ReturnType<typeof distribution>> = {};
    for (const [key, question] of Object.entries(request.questions)) {
      const isRoot = Object.values(question.criteria).some(v => v?.startsWith('Work:'));
      const selected = option(question, isRoot ? 'Work' : Object.values(question.criteria).some(v => v?.startsWith('Report:')) ? 'Report' : 'Receipt');
      answers[key] = distribution(question, selected, isRoot ? .6 : .9);
    }
    return { model: 'fake', answers };
  } };
  const result = await navigate(catalog, 'ambiguous', { transport, beamWidth: 2, maxResults: 2 });
  assert.equal(result.status, 'candidates');
  assert.deepEqual(new Set(result.candidates.map(c => c.sourceId)), new Set(['source-report', 'source-receipt']));
});

test('missing none option and out-of-scope choice are rejected', async () => {
  for (const bad of ['missing-none', 'foreign-choice']) {
    const transport: Evaluator = { evaluate: async request => {
      const question = request.questions.branch_0!;
      assert.equal(question.type, 'choice');
      const probabilities = Object.fromEntries(Object.keys(question.criteria).filter(k => bad !== 'missing-none' || k !== 'o_none').map((k, i, keys) => [k, i === 0 ? 1 : 0]));
      return { model: 'fake', answers: { branch_0: { type: 'choice', choice: bad === 'foreign-choice' ? 'attacker_option' : Object.keys(probabilities)[0]!, confidence: 1, probabilities } } };
    } };
    await assert.rejects(() => navigate(catalog, 'anything', { transport }), NavigationError);
  }
});

test('a winning none option stops a branch and reports no-candidates', async () => {
  const transport: Evaluator = { evaluate: async request => {
    const answers = Object.fromEntries(Object.entries(request.questions).map(([key, question]) => [key, distribution(question, 'o_none', 1)]));
    return { model: 'fake', answers };
  } };
  const result = await navigate(catalog, 'nothing applicable', { transport });
  assert.equal(result.status, 'no-candidates');
  assert.deepEqual(result.candidates, []);
  assert.equal(result.trace.length, 1);
  assert.equal(result.trace[0]?.choices[0]?.none, 1);
  assert.match(result.message, /not proof/i);
});

test('an already-aborted caller signal never calls the provider', async () => {
  const controller = new AbortController();
  controller.abort();
  let called = false;
  await assert.rejects(() => navigate(catalog, 'anything', { signal: controller.signal, transport: { evaluate: async () => { called = true; throw new Error('unreachable'); } } }), /timed out/);
  assert.equal(called, false);
});

test('round budget is bounded and flat-files work', async () => {
  const chain = { version: 1 as const, structure: 'folder-tree' as const, rootId: 'root' as const, nodes: [
    { id: 'root', label: 'root', description: '', children: ['one'] }, { id: 'one', label: 'one', description: '', children: ['two'] }, { id: 'two', label: 'two', description: '', sourceId: 's' }
  ] };
  const alwaysFirst: Evaluator = { evaluate: async request => ({ model: 'fake', answers: Object.fromEntries(Object.entries(request.questions).map(([key, question]) => {
    assert.equal(question.type, 'choice');
    const first = Object.keys(question.criteria).find(id => id !== 'o_none')!;
    return [key, distribution(question, first, .9)];
  })) }) };
  const exhausted = await navigate(chain, 'x', { transport: alwaysFirst, maxRounds: 1 });
  assert.equal(exhausted.status, 'budget-exhausted');
  assert.equal(exhausted.calls, 1);
  const flat = { version: 1 as const, structure: 'flat-files' as const, rootId: 'root' as const, nodes: [
    { id: 'root', label: 'root', description: '', children: ['file'] }, { id: 'file', label: 'File', description: 'flat file', sourceId: 'flat-source' }
  ] };
  const flatResult = await navigate(flat, 'x', { transport: alwaysFirst });
  assert.equal(flatResult.status, 'candidates');
  assert.equal(flatResult.candidates[0]?.sourceId, 'flat-source');
});

test('rejects cyclic trees and bounded invalid catalog input', () => {
  assert.throws(() => validateNavigationCatalog({ version: 1, structure: 'folder-tree', rootId: 'root', nodes: [
    { id: 'root', label: '', description: '', children: ['a'] }, { id: 'a', label: '', description: '', children: ['root'] }
  ] }), NavigationError);
  assert.throws(() => validateNavigationCatalog({ ...catalog, nodes: [...catalog.nodes, { id: 'unused', label: '', description: '', sourceId: 'unused' }] }), NavigationError);
});
