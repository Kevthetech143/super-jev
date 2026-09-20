import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_CHUNK_TARGET_WORDS, RetrievalError, addNeighbors, assertChunkOffsets, cacheKeyFor, chunkSource,
  preparePassages, retrieveSources, sha256Hex, validateSelection,
  type PreparationRecord, type RetrievalSource, type SourceChunk
} from '../../src/enhance/retrieval.ts';
import { choiceAnswer } from '../../src/enhance/stub.ts';
import type { Answer, Evaluation, Evaluator, Question, Request } from '../../src/types.ts';

// ---------------------------------------------------------------------------
// Fixtures: scoped synthetic sources only.
// ---------------------------------------------------------------------------

function words(prefix: string, n: number): string {
  return Array.from({ length: n }, (_, i) => `${prefix}${i}`).join(' ');
}

function threeSectionText(prefix: string): string {
  return [
    '# Overview',
    words(`${prefix}-ov`, 60),
    '# Details',
    words(`${prefix}-det`, 60),
    '# Notes',
    words(`${prefix}-nt`, 60)
  ].join('\n');
}

const REQUEST = 'how do I restart the widget service';

function alpha(): RetrievalSource {
  return {
    id: 'alpha',
    description: 'Alpha guide: restarting the widget service safely, step by step.',
    text: threeSectionText('alpha')
  };
}

function beta(): RetrievalSource {
  return {
    id: 'beta',
    description: 'Beta guide: baking sourdough bread from a starter culture.',
    text: threeSectionText('beta')
  };
}

function reviewedPrep(sources: RetrievalSource[], policy = 'test-policy'): Map<string, PreparationRecord> {
  const m = new Map<string, PreparationRecord>();
  for (const s of sources) {
    const sha = sha256Hex(s.text);
    m.set(sha, { contentSHA: sha, policy, status: 'reviewed' });
  }
  return m;
}

// ---------------------------------------------------------------------------
// Mocked evaluator chain: answers by record id, recovered from the request's
// own named-reference state (same pattern as fetch.test.ts). Table values are
// [level, confidence] so floor/margin behaviour is under test control.
// ---------------------------------------------------------------------------

type Table = Record<string, [string, number]>;

function tableTransport(table: Table, options: { omit?: string[]; throwOn?: string[] } = {}): { transport: Evaluator; requests: Request[] } {
  const requests: Request[] = [];
  const omit = new Set(options.omit ?? []);
  const throwOn = new Set(options.throwOn ?? []);
  const transport: Evaluator = {
    evaluate: async (request: Request): Promise<Evaluation> => {
      requests.push(request);
      const state = (request.state as { records: Record<string, { id: string }> }).records;
      const answers: Record<string, Answer> = {};
      for (const [wireKey, question] of Object.entries(request.questions) as [string, Question][]) {
        const recordKey = Object.keys(state).find(k => wireKey === `q_relevance_${k}` || wireKey === `q0_r${Object.keys(state).indexOf(k)}`);
        assert.ok(recordKey, `wire key ${wireKey} does not name a record in the state`);
        const id = state[recordKey].id;
        if (throwOn.has(id)) throw new Error(`synthetic transport failure for ${id}`);
        if (omit.has(id)) continue;
        if (question.type !== 'choice') throw new Error('expected a choice question');
        const [level, confidence] = table[id] ?? ['none', 0.9];
        answers[wireKey] = choiceAnswer(level, confidence, Object.keys(question.criteria));
      }
      return { model: 'table-offline', answers };
    }
  };
  return { transport, requests };
}

function stagesOf(result: { trace: { stage: string }[] }): string[] {
  return result.trace.map(t => t.stage);
}

// ---------------------------------------------------------------- chunking

test('chunkSource cuts deterministic heading-aware chunks retaining sourceID, SHA, start/end', () => {
  const a = alpha();
  const chunks = chunkSource(a.id, a.text);
  assert.equal(chunks.length, 3);
  const sha = sha256Hex(a.text);
  assert.deepEqual(chunks.map(c => c.chunkIndex), [0, 1, 2]);
  assert.deepEqual(chunks.map(c => c.heading), ['Overview', 'Details', 'Notes']);
  assert.deepEqual(chunks.map(c => [c.startLine, c.endLine]), [[1, 2], [3, 4], [5, 6]]);
  for (const c of chunks) {
    assert.equal(c.sourceId, 'alpha');
    assert.equal(c.sourceSHA, sha);
    assert.ok(c.text.length > 0);
  }
  // Deterministic: same input, same chunks.
  assert.deepEqual(chunkSource(a.id, a.text), chunks);
  // Empty text chunks to nothing.
  assert.deepEqual(chunkSource('empty', '   \n  '), []);
});

test('chunkSource splits long sections at line boundaries near the target word count', () => {
  const lines = ['# Long', ...Array.from({ length: 10 }, (_, i) => words(`w${i}`, 30))];
  const chunks = chunkSource('s', lines.join('\n'), 100);
  assert.ok(chunks.length >= 3, `expected several chunks, got ${chunks.length}`);
  for (const c of chunks) {
    assert.ok(c.endLine >= c.startLine);
    assert.equal(c.heading, 'Long');
  }
  // Full line coverage, no gaps or overlaps.
  const covered: number[] = [];
  for (const c of chunks) for (let l = c.startLine; l <= c.endLine; l++) covered.push(l);
  assert.deepEqual(covered, Array.from({ length: 11 }, (_, i) => i + 1));
});

test('assertChunkOffsets rejects invalid offsets and broken sequences', () => {
  const good: SourceChunk = {
    sourceId: 's', sourceSHA: 'a'.repeat(64), chunkIndex: 0,
    startLine: 1, endLine: 2, heading: null, text: 'hello'
  };
  assertChunkOffsets([good]);
  assert.throws(() => assertChunkOffsets([{ ...good, endLine: 0 }]), RetrievalError);
  assert.throws(() => assertChunkOffsets([{ ...good, startLine: 3, endLine: 2 }]), RetrievalError);
  assert.throws(() => assertChunkOffsets([{ ...good, sourceSHA: 'zzz' }]), RetrievalError);
  assert.throws(() => assertChunkOffsets([good, { ...good }]), RetrievalError); // duplicate chunk id
  assert.throws(() => assertChunkOffsets([{ ...good, text: '' }]), RetrievalError);
});

test('validateSelection fails closed on foreign judge ids', () => {
  validateSelection(new Set(['a', 'b']), ['a', 'b']);
  assert.throws(() => validateSelection(new Set(['a', 'b']), ['a', 'zzz']), RetrievalError);
});

// ---------------------------------------------------------------- full chain

test('full chain: descriptions -> narrow bundles -> ready, passages grouped per source with description', async () => {
  const sources = [alpha(), beta()];
  const { transport, requests } = tableTransport({
    alpha: ['high', 0.96],
    beta: ['none', 0.9],
    'alpha#0': ['high', 0.96],
    'alpha#1': ['none', 0.9],
    'alpha#2': ['none', 0.9]
  });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrep(sources) });

  assert.equal(result.status, 'ready');
  assert.deepEqual(stagesOf(result), ['descriptions', 'local-bm25', 'narrow-bundles', 'prepare']);
  assert.equal(result.passages.length, 1);
  const p = result.passages[0];
  assert.equal(p.sourceId, 'alpha');
  assert.equal(p.description, sources[0].description);
  assert.equal(p.contentSHA, sha256Hex(sources[0].text));
  assert.equal(p.policy, 'test-policy');
  assert.equal(p.chunkIndex, 0);
  assert.deepEqual([p.startLine, p.endLine], [1, 2]);
  assert.equal(p.heading, 'Overview');
  assert.deepEqual(result.pending, []);
  // Original-source pointers for every source that entered the chain.
  assert.deepEqual(result.sources.map(s => s.id).sort(), ['alpha', 'beta']);
  for (const s of result.sources) assert.match(s.contentSHA, /^[0-9a-f]{64}$/);
  // Compact trace: provider calls counted per stage.
  const desc = result.trace.find(t => t.stage === 'descriptions');
  assert.equal(desc?.calls, 1);
  assert.ok(requests.length >= 2, 'descriptions + narrow bundles each made a provider call');
});

test('wide bundles run only on narrow refusal; skipped when narrow succeeds', async () => {
  const sources = [alpha(), beta()];
  // Narrow bundles refuse (no direct fit) -> wide stage runs and acts.
  const refusing = tableTransport({
    alpha: ['high', 0.96],
    beta: ['none', 0.9],
    'alpha#0': ['medium', 0.9],
    'alpha#1': ['medium', 0.85],
    'alpha#2': ['low', 0.8],
    'alpha#wide': ['high', 0.95]
  });
  const wide = await retrieveSources(sources, REQUEST, { transport: refusing.transport, preparation: reviewedPrep(sources) });
  assert.equal(wide.status, 'ready');
  assert.ok(stagesOf(wide).includes('wide-bundles'), 'wide stage ran after narrow refusal');
  assert.equal(wide.passages.length, 3, 'wide bundle resolves back to the source kept chunks');
  assert.ok(wide.passages.every(p => p.sourceId === 'alpha'));

  // Narrow bundles succeed -> wide stage never runs.
  const direct = tableTransport({
    alpha: ['high', 0.96],
    beta: ['none', 0.9],
    'alpha#0': ['high', 0.96],
    'alpha#1': ['none', 0.9],
    'alpha#2': ['none', 0.9]
  });
  const narrow = await retrieveSources(sources, REQUEST, { transport: direct.transport, preparation: reviewedPrep(sources) });
  assert.equal(narrow.status, 'ready');
  assert.ok(!stagesOf(narrow).includes('wide-bundles'), 'wide stage skipped when narrow succeeded');
  assert.equal(direct.requests.length, 2, 'exactly two provider calls: descriptions + narrow bundles');
});

test('all descriptions none -> no-match, nothing selected', async () => {
  const sources = [alpha(), beta()];
  const { transport } = tableTransport({ alpha: ['none', 0.9], beta: ['none', 0.85] });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrep(sources) });
  assert.equal(result.status, 'no-match');
  assert.deepEqual(result.passages, []);
  assert.deepEqual(result.pending, []);
  assert.ok(!stagesOf(result).includes('narrow-bundles'), 'no bundle stage after a description no-match');
});

// ---------------------------------------------------------------- fail closed

test('transport error fails closed -> refused, never a guess', async () => {
  const sources = [alpha()];
  const { transport } = tableTransport({ alpha: ['high', 0.96] }, { throwOn: ['alpha'] });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrep(sources) });
  assert.equal(result.status, 'refused');
  assert.deepEqual(result.passages, []);
});

test('incomplete judge answers fail closed -> refused, not no-match', async () => {
  const sources = [alpha()];
  const { transport } = tableTransport(
    { alpha: ['high', 0.96] },
    { omit: ['alpha#0', 'alpha#1', 'alpha#2'] } // narrow bundles never answered
  );
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrep(sources) });
  assert.equal(result.status, 'refused', 'unanswered bundles must not be reported as no-match');
});

test('duplicate source ids throw', async () => {
  const sources = [alpha(), alpha()];
  const { transport } = tableTransport({});
  await assert.rejects(() => retrieveSources(sources, REQUEST, { transport }), RetrievalError);
});

// ---------------------------------------------------------------- preparation

test('missing preparation -> preparation_required with reason unknown, never raw text', async () => {
  const sources = [alpha()];
  const { transport } = tableTransport({
    alpha: ['high', 0.96],
    'alpha#0': ['high', 0.96],
    'alpha#1': ['none', 0.9],
    'alpha#2': ['none', 0.9]
  });
  const result = await retrieveSources(sources, REQUEST, { transport });
  assert.equal(result.status, 'preparation-required');
  assert.deepEqual(result.passages, []);
  assert.equal(result.pending.length, 1);
  assert.equal(result.pending[0].reason, 'unknown');
  assert.ok(!('text' in result.pending[0]), 'pending passages must never carry raw text');
});

test('stale preparation record -> preparation_required with reason stale', async () => {
  const sources = [alpha()];
  const { transport } = tableTransport({
    alpha: ['high', 0.96],
    'alpha#0': ['high', 0.96],
    'alpha#1': ['none', 0.9],
    'alpha#2': ['none', 0.9]
  });
  const stale = (_sourceId: string, _sha: string): PreparationRecord => ({
    contentSHA: '0'.repeat(64), // reviewed bytes are not the current bytes
    policy: 'test-policy',
    status: 'reviewed'
  });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: stale });
  assert.equal(result.status, 'preparation-required');
  assert.equal(result.pending[0].reason, 'stale');
});

test('unreviewed preparation record -> preparation_required with reason unreviewed', async () => {
  const sources = [alpha()];
  const { transport } = tableTransport({
    alpha: ['high', 0.96],
    'alpha#0': ['high', 0.96],
    'alpha#1': ['none', 0.9],
    'alpha#2': ['none', 0.9]
  });
  const prep = reviewedPrep(sources);
  for (const r of prep.values()) r.status = 'pending';
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: prep });
  assert.equal(result.status, 'preparation-required');
  assert.equal(result.pending[0].reason, 'unreviewed');
});

test('preparePassages binds every prepared passage to contentSHA and policy', () => {
  const a = alpha();
  const chunks = chunkSource(a.id, a.text);
  const { prepared, pending } = preparePassages(chunks, new Map([[a.id, a.description]]), reviewedPrep([a], 'strict-v2'));
  assert.equal(pending.length, 0);
  assert.equal(prepared.length, chunks.length);
  for (const p of prepared) {
    assert.equal(p.contentSHA, sha256Hex(a.text));
    assert.equal(p.policy, 'strict-v2');
    assert.equal(p.description, a.description);
  }
});

// ---------------------------------------------------------------- neighbors

test('neighbor stage: bounds, dedupe, base never dropped', () => {
  const chunks = chunkSource('s', threeSectionText('s'), 50);
  assert.equal(chunks.length, 3);
  const bySource = new Map([['s', chunks]]);

  // First chunk: only the following neighbor exists.
  let out = addNeighbors([chunks[0]], bySource);
  assert.deepEqual(out.map(c => c.chunkIndex), [0, 1]);

  // Middle chunk: both sides.
  out = addNeighbors([chunks[1]], bySource);
  assert.deepEqual(out.map(c => c.chunkIndex), [1, 0, 2]);

  // Last chunk: only the preceding neighbor exists.
  out = addNeighbors([chunks[2]], bySource);
  assert.deepEqual(out.map(c => c.chunkIndex), [2, 1]);

  // Dedupe: adjacent base chunks share neighbors; base never dropped.
  out = addNeighbors([chunks[0], chunks[1]], bySource);
  assert.deepEqual(out.map(c => c.chunkIndex), [0, 1, 2]);
});

test('neighbor stage is OFF by default and opt-in via includeNeighbors', async () => {
  const sources = [alpha()];
  const table: Table = {
    alpha: ['high', 0.96],
    'alpha#0': ['high', 0.96],
    'alpha#1': ['none', 0.9],
    'alpha#2': ['none', 0.9]
  };
  const off = tableTransport(table);
  const without = await retrieveSources(sources, REQUEST, { transport: off.transport, preparation: reviewedPrep(sources) });
  assert.equal(without.status, 'ready');
  assert.deepEqual(without.passages.map(p => p.chunkIndex), [0]);
  assert.ok(!stagesOf(without).includes('neighbors'));

  const on = tableTransport(table);
  const withNeighbors = await retrieveSources(sources, REQUEST, {
    transport: on.transport, preparation: reviewedPrep(sources), includeNeighbors: true
  });
  assert.equal(withNeighbors.status, 'ready');
  assert.deepEqual(withNeighbors.passages.map(p => p.chunkIndex), [0, 1], 'base chunk 0 kept, following neighbor added');
  assert.ok(stagesOf(withNeighbors).includes('neighbors'));
});

// ---------------------------------------------------------------- chain rules

test('the request reaches the judge unchanged', async () => {
  const sources = [alpha(), beta()];
  const { transport, requests } = tableTransport({
    alpha: ['high', 0.96],
    beta: ['none', 0.9],
    'alpha#0': ['high', 0.96],
    'alpha#1': ['none', 0.9],
    'alpha#2': ['none', 0.9]
  });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrep(sources) });
  assert.equal(result.status, 'ready');
  assert.ok(requests.length >= 2);
  for (const req of requests) {
    for (const question of Object.values(req.questions) as Question[]) {
      assert.ok(
        question.instructions.includes(JSON.stringify(REQUEST)),
        'every judged question carries the original request verbatim'
      );
    }
  }
});

test('maxStages bounds the provider stages', async () => {
  const sources = [alpha()];
  const table: Table = {
    alpha: ['high', 0.96],
    'alpha#0': ['medium', 0.9],
    'alpha#1': ['medium', 0.85],
    'alpha#2': ['low', 0.8],
    'alpha#wide': ['high', 0.95]
  };

  // maxStages 1: only descriptions may run; the chain cannot continue.
  const one = tableTransport(table);
  const r1 = await retrieveSources(sources, REQUEST, { transport: one.transport, preparation: reviewedPrep(sources), maxStages: 1 });
  assert.equal(r1.status, 'refused');
  assert.deepEqual(stagesOf(r1), ['descriptions', 'local-bm25']);

  // maxStages 2: narrow may run and refuse, but wide would be stage 3.
  const two = tableTransport(table);
  const r2 = await retrieveSources(sources, REQUEST, { transport: two.transport, preparation: reviewedPrep(sources), maxStages: 2 });
  assert.equal(r2.status, 'refused');
  assert.ok(stagesOf(r2).includes('narrow-bundles'));
  assert.ok(!stagesOf(r2).includes('wide-bundles'), 'wide stage blocked by the stage bound');
  assert.equal(two.requests.length, 2, 'no third provider call was made');
});

test('retrieval never retries a judge call by default', async () => {
  const sources = [alpha()];
  let calls = 0;
  const flaky: Evaluator = {
    evaluate: async (request: Request): Promise<Evaluation> => {
      calls += 1;
      if (calls === 1) throw new Error('synthetic first-call failure');
      const { transport } = tableTransport({ alpha: ['high', 0.96] });
      return transport.evaluate(request, new AbortController().signal);
    }
  };
  const result = await retrieveSources(sources, REQUEST, { transport: flaky, preparation: reviewedPrep(sources) });
  assert.equal(result.status, 'refused', 'a failed judge call is not retried (maxRetries 0)');
  assert.equal(calls, 1);
});

test('cache seam: stable key format, accepted but unused', async () => {
  assert.equal(cacheKeyFor('s', 'a'.repeat(64)), `retrieval/v1/s/${'a'.repeat(64)}`);
  const sources = [alpha()];
  const { transport } = tableTransport({
    alpha: ['high', 0.96],
    'alpha#0': ['high', 0.96],
    'alpha#1': ['none', 0.9],
    'alpha#2': ['none', 0.9]
  });
  const cache = { get: (_k: string) => undefined, set: (_k: string, _v: string) => {} };
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrep(sources), cache });
  assert.equal(result.status, 'ready', 'passing a cache changes nothing yet');
});
