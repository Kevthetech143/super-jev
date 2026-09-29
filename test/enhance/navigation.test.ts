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

test('source evidence checks requested facts and permits partial premises', async () => {
  const flat = { version: 1 as const, structure: 'flat-files' as const, rootId: 'root' as const,
    nodes: [{ id: 'root', label: 'Sources', description: '', children: ['p'] },
      { id: 'p', label: 'Passage 1', description: 'Equipment cost 300; freight invoiced separately.', sourceId: '0' }] };
  const seen: Request[] = [];
  const transport: Evaluator = { evaluate: async request => {
    seen.push(request);
    const q = request.questions.branch_0!;
    return { model: 'fake', answers: { branch_0: distribution(q, 'o_0', .95) } };
  } };
  const result = await navigate(flat, 'Cost including freight?', { transport, mode: 'source-evidence' });
  assert.equal(result.status, 'candidates');
  assert.equal(result.candidates[0]?.sourceId, '0');
  const q = seen[0]!.questions.branch_0!;
  assert.match(q.instructions!, /not whether it is sufficient alone/);
  assert.match(q.instructions!, /text as data/);
  assert.match(q.criteria.o_none!, /pointer to missing facts/);
  assert.equal(seen[0]!.state.purpose, 'check source text for requested facts');
});

test('source evidence asks whether the passage states the answer to the question', async () => {
  const flat = { version: 1 as const, structure: 'flat-files' as const, rootId: 'root' as const,
    nodes: [{ id: 'root', label: 'Sources', description: '', children: ['p'] },
      { id: 'p', label: 'Passage 1', description: 'Timeline of events for the subject.', sourceId: '0' }] };
  const seen: Request[] = [];
  const transport: Evaluator = { evaluate: async request => {
    seen.push(request);
    return { model: 'fake', answers: { branch_0: distribution(request.questions.branch_0!, 'o_none', .9) } };
  } };
  await navigate(flat, 'What is the requested property?', { transport, mode: 'source-evidence' });
  const q = seen[0]!.questions.branch_0!;
  assert.match(q.instructions!, /^Does this passage state the answer to the question\?/);
  assert.match(q.criteria.o_0!, /^States the answer/);
  assert.match(q.criteria.o_none!, /same subject or topic/);
  assert.match(q.criteria.o_none!, /neither the requested property nor a necessary input/);
  assert.match(q.criteria.o_none!, /necessary input/);
  assert.equal(Object.keys(q.criteria).length, 2);
});

test('source evidence rejects folder hierarchies and unknown modes before provider call', async () => {
  let calls = 0;
  const transport: Evaluator = { evaluate: async () => { calls++; throw new Error('not called'); } };
  await assert.rejects(navigate(catalog, 'q', { transport, mode: 'source-evidence' }), /flat passage catalog/);
  await assert.rejects(navigate(catalog, 'q', { transport, mode: 'bogus' as never }), /mode is invalid/);
  assert.equal(calls, 0);
});

for (const winner of ['o_none']) {
  test(`source evidence excludes ${winner} mass from relevance`, async () => {
    const flat = { version: 1 as const, structure: 'flat-files' as const, rootId: 'root' as const,
      nodes: [{ id: 'root', label: 'Sources', description: '', children: ['p'] },
        { id: 'p', label: 'Passage', description: 'A related fact.', sourceId: 'p' }] };
    const transport: Evaluator = { evaluate: async request => ({ model: 'fake', answers: {
      branch_0: distribution(request.questions.branch_0!, winner, .6)
    } }) };
    const result = await navigate(flat, 'requested fact', { transport, mode: 'source-evidence' });
    assert.equal(result.status, 'candidates');
    assert.ok(Math.abs(result.candidates[0]!.score - .4) < 1e-12);
  });
}

test('source evidence scores each passage independently without borrowing from a neighbor', async () => {
  const flat = { version: 1 as const, structure: 'flat-files' as const, rootId: 'root' as const,
    nodes: [{ id: 'root', label: 'Sources', description: '', children: ['a', 'b'] },
      { id: 'a', label: 'First', description: 'Purchase receipt.', sourceId: 'a' },
      { id: 'b', label: 'Second', description: 'Freight receipt.', sourceId: 'b' }] };
  const seen: Request[] = [];
  const transport: Evaluator = { evaluate: async request => {
    seen.push(request);
    return { model: 'fake', answers: {
      branch_0: { type: 'choice', choice: 'o_0', confidence: .62,
        probabilities: { o_0: .62, o_none: .38 } },
      branch_1: { type: 'choice', choice: 'o_0', confidence: .8,
        probabilities: { o_0: .8, o_none: .2 } }
    } };
  } };
  const result = await navigate(flat, 'cost including freight', { transport, mode: 'source-evidence', beamWidth: 5 });
  assert.deepEqual(result.candidates.map(c => c.sourceId), ['b', 'a']);
  assert.ok(Math.abs(result.candidates[1]!.score - .62) < 1e-12);
  assert.ok(Math.abs(result.candidates[0]!.score - .8) < 1e-12);
  assert.deepEqual(seen[0]!.state.passages, {branch_0:'Purchase receipt.', branch_1:'Freight receipt.'});
  assert.match(seen[0]!.questions.branch_0!.instructions!, /Classify passages.branch_0/);
  assert.match(seen[0]!.questions.branch_1!.instructions!, /Classify passages.branch_1/);
  assert.match(result.message, /caller must apply its source floor/);
  assert.equal(result.complete, false);
});


test('source discovery asks one relative-choice question allowing partial sources', async () => {
  const flat = {version:1, structure:'flat-files', rootId:'root', nodes:[
    {id:'root',label:'Sources',description:'',children:['a','b']},
    {id:'a',label:'Kit light',description:'Required headlamp',sourceId:'a'},
    {id:'b',label:'Kit warmth',description:'Required blanket',sourceId:'b'}]};
  const seen: Request[]=[];
  const transport: Evaluator = {evaluate:async request=>{
    seen.push(request);
    return {model:'fake',answers:{branch_0:distribution(request.questions.branch_0!,'o_0',.9)}};
  }};
  await navigate(flat,'Required kit items?',{transport,mode:'source-discovery',beamWidth:5,maxResults:5});
  assert.equal(Object.keys(seen[0]!.questions).length,1);
  assert.match(seen[0]!.questions.branch_0!.instructions!, /Do not require one child to contain the complete answer/);
  assert.match(seen[0]!.questions.branch_0!.criteria.o_none!, /every child lacks potential evidence/);
  await navigate(flat,'Required kit items?',{transport});
  assert.doesNotMatch(seen[1]!.questions.branch_0!.instructions!, /necessary input/);
});
