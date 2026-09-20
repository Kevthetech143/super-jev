import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_CHUNK_TARGET_WORDS, RetrievalError, addNeighbors, assertChunkOffsets, bm25EntryText,
  bundleProviderText, cacheKeyFor, chunkIdentity, chunkSource, preparePassages, retrieveSources,
  sha256Hex, topChunksGlobal, validateSelection,
  type PreparationRecord, type RetrievalSource, type SourceChunk
} from '../../src/enhance/retrieval.ts';
import { choiceAnswer } from '../../src/enhance/stub.ts';
import type { Answer, Evaluation, Evaluator, Question, Request } from '../../src/types.ts';

// ---------------------------------------------------------------------------
// Fixtures: scoped synthetic sources only. No private data, no live inputs.
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

/** Reviewed preparation for every chunk of every source, keyed by chunk identity. */
function reviewedPrepFor(
  sources: RetrievalSource[],
  policy = 'reviewed',
  targetWords = DEFAULT_CHUNK_TARGET_WORDS
): Map<string, PreparationRecord> {
  const m = new Map<string, PreparationRecord>();
  for (const s of sources) {
    for (const c of chunkSource(s.id, s.text, targetWords)) {
      m.set(chunkIdentity(c), {
        sourceId: c.sourceId,
        contentSHA: c.sourceSHA,
        chunkIndex: c.chunkIndex,
        startLine: c.startLine,
        endLine: c.endLine,
        reviewedText: `REVIEWED ${c.sourceId} ${chunkIdentity(c)}`,
        safeHeading: c.heading ? `Safe ${c.heading}` : 'Safe section',
        policy,
        status: 'reviewed'
      });
    }
  }
  return m;
}

const REVIEWED_OPTS = { descriptionsReviewed: true } as const;

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

/** One table per provider call, in call order (descriptions, narrow, wide, neighbors...). */
function phasedTransport(phases: { table?: Table; omit?: string[]; throwOn?: string[] }[]): { transport: Evaluator; requests: Request[] } {
  const requests: Request[] = [];
  let call = 0;
  const transport: Evaluator = {
    evaluate: async (request: Request): Promise<Evaluation> => {
      requests.push(request);
      const phase = phases[Math.min(call, phases.length - 1)];
      call += 1;
      const { transport: t } = tableTransport(phase.table ?? {}, { omit: phase.omit, throwOn: phase.throwOn });
      return t.evaluate(request);
    }
  };
  return { transport, requests };
}

function stagesOf(result: { trace: { stage: string }[] }): string[] {
  return result.trace.map(t => t.stage);
}

function requestBlobs(requests: Request[]): string[] {
  return requests.map(r => JSON.stringify(r));
}

// ---------------------------------------------------------------- chunking

test('chunkSource matches the experiment chunker: heading flush, breadcrumb, check-before-add flush', () => {
  const text = ['# Alpha', 'one two three', '', '## Beta', 'four five', 'six seven eight nine ten'].join('\n');
  const chunks = chunkSource('fx', text, 8);
  assert.equal(chunks.length, 3);
  // Exact fixture boundaries: heading flushes before the heading; the
  // 5-word line would push the open chunk over 8 words, so it flushes first.
  assert.deepEqual(chunks.map(c => [c.startLine, c.endLine]), [[1, 3], [4, 5], [6, 6]]);
  assert.deepEqual(chunks.map(c => c.heading), ['Alpha', 'Alpha > Beta', 'Alpha > Beta']);
  assert.equal(chunks[0].text, '# Alpha\none two three\n');
  assert.equal(chunks[1].text, '## Beta\nfour five');
  assert.equal(chunks[2].text, 'six seven eight nine ten');
  assert.deepEqual(chunks.map(chunkIdentity), ['fx:L1-3', 'fx:L4-5', 'fx:L6-6']);
  const sha = sha256Hex(text);
  for (const c of chunks) {
    assert.equal(c.sourceId, 'fx');
    assert.equal(c.sourceSHA, sha, 'exact original SHA preserved');
  }
  // Deterministic: same input, same chunks.
  assert.deepEqual(chunkSource('fx', text, 8), chunks);
  // Empty text chunks to nothing.
  assert.deepEqual(chunkSource('empty', '   \n  '), []);
});

test('chunkSource never emits blank-only blocks', () => {
  const chunks = chunkSource('s', '\n\n# H\nx');
  assert.equal(chunks.length, 1);
  assert.deepEqual([chunks[0].startLine, chunks[0].endLine], [3, 4]);
  assert.equal(chunks[0].text, '# H\nx');
});

test('chunkSource splits long sections at line boundaries via the check-before-add rule', () => {
  const lines = ['# Long', ...Array.from({ length: 10 }, (_, i) => words(`w${i}`, 30))];
  const chunks = chunkSource('s', lines.join('\n'), 100);
  assert.deepEqual(chunks.map(c => [c.startLine, c.endLine]), [[1, 4], [5, 7], [8, 10], [11, 11]]);
  for (const c of chunks) assert.equal(c.heading, 'Long');
  // Full line coverage, no gaps or overlaps.
  const covered: number[] = [];
  for (const c of chunks) for (let l = c.startLine; l <= c.endLine; l++) covered.push(l);
  assert.deepEqual(covered, Array.from({ length: 11 }, (_, i) => i + 1));
});

test('assertChunkOffsets rejects invalid offsets, broken SHAs, and duplicate identities', () => {
  const good: SourceChunk = {
    sourceId: 's', sourceSHA: 'a'.repeat(64), chunkIndex: 0,
    startLine: 1, endLine: 2, heading: '', text: 'hello'
  };
  assertChunkOffsets([good]);
  assert.throws(() => assertChunkOffsets([{ ...good, endLine: 0 }]), RetrievalError);
  assert.throws(() => assertChunkOffsets([{ ...good, startLine: 3, endLine: 2 }]), RetrievalError);
  assert.throws(() => assertChunkOffsets([{ ...good, sourceSHA: 'zzz' }]), RetrievalError);
  assert.throws(() => assertChunkOffsets([good, { ...good }]), RetrievalError); // duplicate chunk id
  assert.throws(() => assertChunkOffsets([{ ...good, text: '' }]), RetrievalError);
  // Same identity via different index is still a duplicate identity.
  assert.throws(() => assertChunkOffsets([good, { ...good, chunkIndex: 7 }]), RetrievalError);
});

test('validateSelection fails closed on foreign judge ids', () => {
  validateSelection(new Set(['a', 'b']), ['a', 'b']);
  assert.throws(() => validateSelection(new Set(['a', 'b']), ['a', 'zzz']), RetrievalError);
});

// ---------------------------------------------------------------- BM25

test('bm25EntryText matches the experiment format exactly', () => {
  assert.equal(
    bm25EntryText('the description', { heading: 'A > B', text: 'raw local text' }),
    'Document description: the description\nSection: A > B\nPassage: raw local text'
  );
});

test('topChunksGlobal: score descending, chunk-identity tie-break (not catalog order)', () => {
  const mk = (sourceId: string, startLine: number, text: string): SourceChunk => ({
    sourceId, sourceSHA: 'a'.repeat(64), chunkIndex: startLine - 1,
    startLine, endLine: startLine, heading: '', text
  });
  // c2 sorts before c1 in the catalog; the tie must still break by identity.
  const chunks = [mk('s', 2, 'zzz qqq'), mk('s', 1, 'zzz qqq'), mk('s', 3, 'zzz')];
  const top = topChunksGlobal(chunks, new Map([['s', '']]), 'zzz qqq', 2);
  assert.deepEqual(top.map(chunkIdentity), ['s:L1-1', 's:L2-2']);
  const all = topChunksGlobal(chunks, new Map([['s', '']]), 'zzz qqq', 10);
  assert.deepEqual(all.map(chunkIdentity), ['s:L1-1', 's:L2-2', 's:L3-3']);
});

// ---------------------------------------------------------------- preparation

test('preparePassages binds reviewed text + safe heading to sourceId/SHA/offsets/policy', () => {
  const a = alpha();
  const chunks = chunkSource(a.id, a.text);
  const { prepared, pending } = preparePassages(chunks, new Map([[a.id, a.description]]), reviewedPrepFor([a], 'strict-v2'), 'strict-v2');
  assert.equal(pending.length, 0);
  assert.equal(prepared.length, chunks.length);
  for (let i = 0; i < prepared.length; i++) {
    const p = prepared[i];
    assert.equal(p.contentSHA, sha256Hex(a.text));
    assert.equal(p.policy, 'strict-v2');
    assert.equal(p.description, a.description);
    assert.equal(p.reviewedText, `REVIEWED alpha ${chunkIdentity(chunks[i])}`);
    assert.ok(p.safeHeading.startsWith('Safe '));
  }
});

test('preparePassages: stale on SHA, offset, sourceId, or policy mismatch; unreviewed on status', () => {
  const a = alpha();
  const chunks = chunkSource(a.id, a.text);
  const desc = new Map([[a.id, a.description]]);
  const base = reviewedPrepFor([a]);
  const mutate = (key: string, f: (r: PreparationRecord) => void): Map<string, PreparationRecord> => {
    const m = new Map(base);
    const r = { ...m.get(key)! };
    f(r);
    m.set(key, r);
    return m;
  };
  const key = chunkIdentity(chunks[0]);
  for (const [label, f, reason] of [
    ['sha', (r: PreparationRecord) => { r.contentSHA = '0'.repeat(64); }, 'stale'],
    ['offset', (r: PreparationRecord) => { r.endLine += 5; }, 'stale'],
    ['sourceId', (r: PreparationRecord) => { r.sourceId = 'other'; }, 'stale'],
    ['policy', (r: PreparationRecord) => { r.policy = 'other-policy'; }, 'unreviewed']
  ] as [string, (r: PreparationRecord) => void, string][]) {
    const { pending } = preparePassages([chunks[0]], desc, mutate(key, f));
    assert.equal(pending.length, 1, label);
    assert.equal(pending[0].reason, reason, label);
    assert.ok(!('text' in pending[0]), 'pending passages never carry text');
  }
  const unrev = mutate(key, r => { r.status = 'pending'; });
  assert.equal(preparePassages([chunks[0]], desc, unrev).pending[0].reason, 'unreviewed');
  const missing = preparePassages([chunks[0]], desc, new Map());
  assert.equal(missing.pending[0].reason, 'unknown');
  // Callback form receives (sourceId, chunkIndex, contentSHA).
  const seen: [string, number, string][] = [];
  const cb = (sourceId: string, chunkIndex: number, contentSHA: string): PreparationRecord | undefined => {
    seen.push([sourceId, chunkIndex, contentSHA]);
    return base.get(chunkIdentity(chunks[0]));
  };
  const { prepared } = preparePassages([chunks[0]], desc, cb);
  assert.equal(prepared.length, 1);
  assert.deepEqual(seen, [[a.id, 0, sha256Hex(a.text)]]);
});

test('bundleProviderText matches the experiment format exactly', () => {
  const text = bundleProviderText('the description', [
    { sourceId: 's', description: 'the description', contentSHA: 'a'.repeat(64), policy: 'p', chunkIndex: 0, startLine: 1, endLine: 2, reviewedText: 'safe one', safeHeading: 'Safe H1' },
    { sourceId: 's', description: 'the description', contentSHA: 'a'.repeat(64), policy: 'p', chunkIndex: 1, startLine: 3, endLine: 4, reviewedText: 'safe two', safeHeading: 'Safe H2' }
  ]);
  assert.equal(
    text,
    'Description: the description\nAutomatically retrieved source passages:\nSafe H1: safe one\nSafe H2: safe two'
  );
});

// ---------------------------------------------------------------- no raw leak

test('no raw leak: sentinel raw text never reaches any transport payload, reviewed representation only', async () => {
  const source: RetrievalSource = {
    id: 's1',
    description: 'Restarting the widget service guide.',
    text: '# Raw Secret Heading\nSENTINEL_RAW_7f3a restart widget service instructions here'
  };
  const chunks = chunkSource(source.id, source.text);
  assert.equal(chunks.length, 1);
  const prep = new Map<string, PreparationRecord>([[
    chunkIdentity(chunks[0]),
    {
      sourceId: 's1', contentSHA: chunks[0].sourceSHA, chunkIndex: 0, startLine: 1, endLine: 2,
      reviewedText: 'REVIEWED SAFE PASSAGE TEXT', safeHeading: 'Safe Public Heading',
      policy: 'reviewed', status: 'reviewed'
    }
  ]]);
  const { transport, requests } = tableTransport({ s1: ['high', 0.96], 's1#bundle': ['high', 0.96] });
  const result = await retrieveSources([source], REQUEST, { transport, preparation: prep, ...REVIEWED_OPTS });
  assert.equal(result.status, 'ready');
  assert.ok(requests.length >= 2, 'descriptions + narrow bundles each made a provider call');
  const blobs = requestBlobs(requests);
  for (const blob of blobs) {
    assert.ok(!blob.includes('SENTINEL_RAW_7f3a'), 'raw chunk text must never reach the transport');
    assert.ok(!blob.includes('Raw Secret Heading'), 'raw heading must never reach the transport');
  }
  const bundleBlob = blobs[1];
  assert.ok(bundleBlob.includes('REVIEWED SAFE PASSAGE TEXT'), 'reviewed text is what the judge sees');
  assert.ok(bundleBlob.includes('Safe Public Heading'), 'safe heading is what the judge sees');
  // Exact provider text, checked on the parsed record (the blob JSON-escapes newlines).
  const narrowState = requests[1].state as { records: Record<string, { id: string; text: string }> };
  const bundleRecord = Object.values(narrowState.records).find(r => r.id === 's1#bundle');
  assert.equal(
    bundleRecord?.text,
    'Description: Restarting the widget service guide.\nAutomatically retrieved source passages:\nSafe Public Heading: REVIEWED SAFE PASSAGE TEXT'
  );
  assert.equal(result.passages[0].reviewedText, 'REVIEWED SAFE PASSAGE TEXT');
});

test('no raw leak without preparation: preparation-required BEFORE any provider passage call', async () => {
  const source: RetrievalSource = {
    id: 's1',
    description: 'Restarting the widget service guide.',
    text: '# Raw Secret Heading\nSENTINEL_RAW_7f3a restart widget service instructions here'
  };
  const { transport, requests } = tableTransport({ s1: ['high', 0.96], 's1#bundle': ['high', 0.96] });
  const result = await retrieveSources([source], REQUEST, { transport, ...REVIEWED_OPTS });
  assert.equal(result.status, 'preparation-required');
  assert.deepEqual(result.passages, []);
  assert.equal(result.pending.length, 1);
  assert.equal(result.pending[0].reason, 'unknown');
  assert.ok(!('text' in result.pending[0]), 'pending passages never carry text');
  assert.equal(requests.length, 1, 'only the descriptions call ran — no provider passage call');
  assert.ok(!requestBlobs(requests)[0].includes('SENTINEL_RAW_7f3a'));
});

// ---------------------------------------------------------------- full chain

test('full chain: descriptions -> narrow bundles -> ready carries ONLY the top bundle reviewed passages', async () => {
  const sources = [alpha(), beta()];
  const { transport, requests } = tableTransport({
    alpha: ['high', 0.96],
    beta: ['none', 0.9],
    'alpha#bundle': ['high', 0.96],
    'beta#bundle': ['none', 0.9]
  });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), ...REVIEWED_OPTS });

  assert.equal(result.status, 'ready');
  assert.deepEqual(stagesOf(result), ['descriptions', 'local-bm25', 'narrow-bundles', 'prepare']);
  // Ready = the top-ranked bundle's supporting passages only (all three
  // alpha chunks), never every low-ranked candidate.
  assert.equal(result.passages.length, 3);
  assert.ok(result.passages.every(p => p.sourceId === 'alpha'));
  for (const p of result.passages) {
    assert.ok(p.reviewedText.startsWith('REVIEWED alpha'), 'reviewed representation only');
    assert.ok(!p.reviewedText.includes('alpha-ov'), 'no raw chunk text in the result');
    assert.equal(p.description, sources[0].description);
    assert.equal(p.contentSHA, sha256Hex(sources[0].text));
    assert.equal(p.policy, 'reviewed');
  }
  assert.deepEqual(result.pending, []);
  assert.deepEqual(result.sources.map(s => s.id).sort(), ['alpha', 'beta']);
  for (const s of result.sources) assert.match(s.contentSHA, /^[0-9a-f]{64}$/);
  const desc = result.trace.find(t => t.stage === 'descriptions');
  assert.equal(desc?.calls, 1);
  assert.equal(requests.length, 2, 'positive early stop: exactly descriptions + narrow, wide never runs');
});

test('description gate deferral does not abort: ranking top3 continues to passage accept', async () => {
  const sources = [alpha(), beta()];
  // 'medium' beats none but cannot pass the direct-fit gate (and the margin
  // is too tight) — low confidence, complete run: the chain must continue.
  const { transport } = tableTransport({
    alpha: ['medium', 0.95],
    beta: ['medium', 0.9],
    'alpha#bundle': ['high', 0.96],
    'beta#bundle': ['none', 0.9]
  });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), ...REVIEWED_OPTS });
  assert.equal(result.status, 'ready', 'deferral continues to passage judging instead of aborting');
  const desc = result.trace.find(t => t.stage === 'descriptions');
  assert.ok(desc?.note.includes('deferred'), `trace names the deferral, got: ${desc?.note}`);
  assert.deepEqual(desc?.kept, ['alpha', 'beta'], 'description ranking chose the docs');
  assert.ok(result.passages.every(p => p.sourceId === 'alpha'));
});

test('all descriptions none still ranks top3 and continues; terminal no-match only after bundles refuse', async () => {
  const sources = [alpha(), beta()];
  const { transport } = tableTransport({
    alpha: ['none', 0.9],
    beta: ['none', 0.85],
    'alpha#bundle': ['none', 0.9],
    'beta#bundle': ['none', 0.9]
  });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), ...REVIEWED_OPTS });
  assert.equal(result.status, 'no-match');
  assert.ok(stagesOf(result).includes('narrow-bundles'), 'the chain continued past a deferred description gate');
  assert.deepEqual(result.passages, []);
});

test('wide bundles run only on narrow refusal and recover a chunk outside the description shortlist', async () => {
  const filler = (prefix: string): string =>
    ['# A1', words(`${prefix}a`, 20), '# A2', words(`${prefix}b`, 20)].join('\n');
  const sources: RetrievalSource[] = [
    { id: 'A', description: 'A handbook of unrelated filler topics.', text: filler('fa') },
    { id: 'B', description: 'B handbook of unrelated filler topics.', text: filler('fb') },
    { id: 'C', description: 'C handbook of unrelated filler topics.', text: filler('fc') },
    {
      id: 'D',
      description: 'D handbook of unrelated filler topics.',
      text: '# D1\n' + 'restart widget service '.repeat(12) + words('dd', 10)
    }
  ];
  // A high but the margin is too tight -> gate defers; D loses to none so
  // the description shortlist is exactly [A, B, C].
  const { transport, requests } = tableTransport({
    A: ['high', 0.96], B: ['medium', 0.9], C: ['medium', 0.85], D: ['none', 0.9],
    'A#bundle': ['none', 0.9], 'B#bundle': ['none', 0.9], 'C#bundle': ['none', 0.9],
    'D#bundle': ['high', 0.96]
  });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), ...REVIEWED_OPTS });
  assert.equal(result.status, 'ready');
  const desc = result.trace.find(t => t.stage === 'descriptions');
  assert.deepEqual(desc?.kept, ['A', 'B', 'C'], 'D was outside the description shortlist');
  assert.ok(stagesOf(result).includes('wide-bundles'), 'wide stage ran after narrow refusal');
  // The wide stage searched the GLOBAL corpus: D's term-packed chunk won
  // BM25 globally and its bundle was accepted.
  assert.equal(result.passages.length, 1);
  assert.equal(result.passages[0].sourceId, 'D');
  assert.ok(result.passages[0].reviewedText.startsWith('REVIEWED D'));
  assert.equal(requests.length, 3, 'descriptions + narrow + wide, then stop');
});

test('wide refusal with neighbors off ends in no-match, never a guess', async () => {
  const sources = [alpha()];
  const { transport } = tableTransport({
    alpha: ['high', 0.96],
    'alpha#bundle': ['medium', 0.9]
  });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), ...REVIEWED_OPTS });
  assert.equal(result.status, 'no-match');
  assert.deepEqual(stagesOf(result), ['descriptions', 'local-bm25', 'narrow-bundles', 'local-bm25', 'wide-bundles']);
  assert.ok(!stagesOf(result).includes('neighbors'), 'neighbor stage is OFF by default');
});

// ---------------------------------------------------------------- neighbors

test('neighbors are a fourth JUDGE stage only after wide refusal, OFF by default', async () => {
  const source: RetrievalSource = {
    id: 'w1',
    description: 'W1 guide: restarting the widget service safely.',
    text: threeSectionText('w1')
  };
  const phases = [
    { table: { w1: ['high', 0.96] } },                    // descriptions
    { table: { 'w1#bundle': ['medium', 0.9] } },          // narrow refuses
    { table: { 'w1#bundle': ['medium', 0.85] } },         // wide refuses
    { table: { 'w1#bundle': ['high', 0.96] } }           // neighbors judged: accept
  ];
  const { transport, requests } = phasedTransport(phases);
  // chunkK 2: narrow/wide see only L1-2 and L3-4; neighbors add L5-6 back.
  const result = await retrieveSources([source], REQUEST, {
    transport, preparation: reviewedPrepFor([source]), includeNeighbors: true, chunkK: 2, ...REVIEWED_OPTS
  });
  assert.equal(result.status, 'ready');
  assert.deepEqual(
    stagesOf(result),
    ['descriptions', 'local-bm25', 'narrow-bundles', 'local-bm25', 'wide-bundles', 'neighbors', 'neighbors', 'prepare']
  );
  assert.equal(requests.length, 4, 'four provider stages ran');
  // The neighbor chunk L5-6 was judged as part of the bundle and accepted —
  // all three chunks are present, base selection kept.
  assert.deepEqual(result.passages.map(p => [p.startLine, p.endLine]), [[1, 2], [3, 4], [5, 6]]);
});

test('neighbors are never added after an acceptance without judging', async () => {
  const source: RetrievalSource = {
    id: 'w1',
    description: 'W1 guide: restarting the widget service safely.',
    text: threeSectionText('w1')
  };
  const { transport, requests } = tableTransport({
    w1: ['high', 0.96],
    'w1#bundle': ['high', 0.96]
  });
  const result = await retrieveSources([source], REQUEST, {
    transport, preparation: reviewedPrepFor([source]), includeNeighbors: true, chunkK: 2, ...REVIEWED_OPTS
  });
  assert.equal(result.status, 'ready');
  assert.ok(!stagesOf(result).includes('neighbors'), 'no neighbor stage after narrow acceptance');
  assert.equal(requests.length, 2, 'positive early stop: descriptions + narrow only');
  // Only the judged top bundle's passages — the unjudged L5-6 never sneaks in.
  assert.deepEqual(result.passages.map(p => [p.startLine, p.endLine]), [[1, 2], [3, 4]]);
});

// ---------------------------------------------------------------- fail closed

test('transport error fails closed -> refused, never a guess', async () => {
  const sources = [alpha()];
  const { transport } = tableTransport({ alpha: ['high', 0.96] }, { throwOn: ['alpha'] });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), ...REVIEWED_OPTS });
  assert.equal(result.status, 'refused');
  assert.deepEqual(result.passages, []);
});

test('incomplete judge answers fail closed -> refused, not no-match', async () => {
  const sources = [alpha(), beta()];
  // The description gate defers (medium/medium), so BOTH sources stay in the
  // shortlist and the narrow stage judges both bundles.
  const { transport } = tableTransport(
    {
      alpha: ['medium', 0.95], beta: ['medium', 0.9],
      'alpha#bundle': ['none', 0.9]
    },
    { omit: ['beta#bundle'] } // one narrow bundle never answered
  );
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), ...REVIEWED_OPTS });
  assert.equal(result.status, 'refused', 'unanswered bundles must not be reported as no-match');
  const narrow = result.trace.find(t => t.stage === 'narrow-bundles');
  assert.ok(narrow?.note.includes('judge coverage incomplete'), `trace names the incompleteness, got: ${narrow?.note}`);
});

test('incomplete coverage fails closed BEFORE an accept path, even when the gate would accept', async () => {
  const sources = [alpha(), beta()];
  const { transport } = tableTransport(
    {
      alpha: ['medium', 0.95], beta: ['medium', 0.9],
      'alpha#bundle': ['high', 0.96] // gate would accept this...
    },
    { omit: ['beta#bundle'] } // ...but the run is incomplete
  );
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), ...REVIEWED_OPTS });
  assert.equal(result.status, 'refused', 'an accepted top pick on an incomplete run is unknown, not a match');
  assert.deepEqual(result.passages, []);
});

test('transport error at the wide stage fails closed -> refused', async () => {
  const sources = [alpha()];
  const phases = [
    { table: { alpha: ['high', 0.96] } },
    { table: { 'alpha#bundle': ['medium', 0.9] } }, // narrow refuses, complete
    { throwOn: ['alpha#bundle'] }                    // wide transport failure
  ];
  const { transport } = phasedTransport(phases);
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), ...REVIEWED_OPTS });
  assert.equal(result.status, 'refused');
  const wide = result.trace.find(t => t.stage === 'wide-bundles');
  // runFetch records the transport failure in run.errors; the completeness
  // check fails closed before any accept path.
  assert.ok(wide?.note.includes('judge coverage incomplete'), `trace names the incompleteness, got: ${wide?.note}`);
});

test('duplicate source ids throw', async () => {
  const sources = [alpha(), alpha()];
  const { transport } = tableTransport({});
  await assert.rejects(() => retrieveSources(sources, REQUEST, { transport, ...REVIEWED_OPTS }), RetrievalError);
});

test('unaffirmed descriptions throw before any provider call', async () => {
  const sources = [alpha()];
  const { transport, requests } = tableTransport({ alpha: ['high', 0.96] });
  await assert.rejects(
    () => retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources) }),
    RetrievalError
  );
  await assert.rejects(
    () => retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), descriptionsReviewed: false }),
    RetrievalError
  );
  assert.equal(requests.length, 0, 'no provider call before the affirmation gate');
});

// ---------------------------------------------------------------- neighbors (unit)

test('addNeighbors: bounds, dedupe, base never dropped', () => {
  const chunks = chunkSource('s', threeSectionText('s'));
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

// ---------------------------------------------------------------- chain rules

test('the request reaches the judge unchanged', async () => {
  const sources = [alpha(), beta()];
  const { transport, requests } = tableTransport({
    alpha: ['high', 0.96],
    beta: ['none', 0.9],
    'alpha#bundle': ['high', 0.96],
    'beta#bundle': ['none', 0.9]
  });
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), ...REVIEWED_OPTS });
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

test('maxStages bounds the provider stages; neighbors default to a 4-stage budget', async () => {
  const sources = [alpha()];
  const table: Table = {
    alpha: ['high', 0.96],
    'alpha#bundle': ['medium', 0.9]
  };

  // maxStages 1: only descriptions may run; the chain cannot continue.
  const one = tableTransport(table);
  const r1 = await retrieveSources(sources, REQUEST, { transport: one.transport, preparation: reviewedPrepFor(sources), maxStages: 1, ...REVIEWED_OPTS });
  assert.equal(r1.status, 'refused');
  assert.deepEqual(stagesOf(r1), ['descriptions', 'local-bm25']);

  // maxStages 2: narrow may run and refuse, but wide would be stage 3.
  const two = tableTransport(table);
  const r2 = await retrieveSources(sources, REQUEST, { transport: two.transport, preparation: reviewedPrepFor(sources), maxStages: 2, ...REVIEWED_OPTS });
  assert.equal(r2.status, 'refused');
  assert.ok(stagesOf(r2).includes('narrow-bundles'));
  assert.ok(!stagesOf(r2).includes('wide-bundles'), 'wide stage blocked by the stage bound');
  assert.equal(two.requests.length, 2, 'no third provider call was made');

  // includeNeighbors with an explicit maxStages 3: wide refuses, neighbors would be stage 4.
  const three = phasedTransport([
    { table: { alpha: ['high', 0.96] } },
    { table: { 'alpha#bundle': ['medium', 0.9] } },
    { table: { 'alpha#bundle': ['medium', 0.85] } }
  ]);
  const r3 = await retrieveSources(sources, REQUEST, {
    transport: three.transport, preparation: reviewedPrepFor(sources), includeNeighbors: true, maxStages: 3, ...REVIEWED_OPTS
  });
  assert.equal(r3.status, 'refused');
  assert.ok(!stagesOf(r3).includes('neighbors'), 'neighbor stage blocked by the stage bound');
  assert.equal(three.requests.length, 3, 'no fourth provider call was made');
});

test('retrieval never retries a judge call by default', async () => {
  const sources = [alpha()];
  let calls = 0;
  const flaky: Evaluator = {
    evaluate: async (request: Request): Promise<Evaluation> => {
      calls += 1;
      if (calls === 1) throw new Error('synthetic first-call failure');
      const { transport } = tableTransport({ alpha: ['high', 0.96] });
      return transport.evaluate(request);
    }
  };
  const result = await retrieveSources(sources, REQUEST, { transport: flaky, preparation: reviewedPrepFor(sources), ...REVIEWED_OPTS });
  assert.equal(result.status, 'refused', 'a failed judge call is not retried (maxRetries 0)');
  assert.equal(calls, 1);
});

test('cache seam: stable key format, accepted but unused', async () => {
  assert.equal(cacheKeyFor('s', 'a'.repeat(64)), `retrieval/v1/s/${'a'.repeat(64)}`);
  const sources = [alpha()];
  const { transport } = tableTransport({
    alpha: ['high', 0.96],
    'alpha#bundle': ['high', 0.96]
  });
  const cache = { get: (_k: string) => undefined, set: (_k: string, _v: string) => {} };
  const result = await retrieveSources(sources, REQUEST, { transport, preparation: reviewedPrepFor(sources), cache, ...REVIEWED_OPTS });
  assert.equal(result.status, 'ready', 'passing a cache changes nothing yet');
});
