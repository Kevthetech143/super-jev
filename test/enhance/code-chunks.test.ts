import test from 'node:test';
import assert from 'node:assert/strict';
import { chunkSource, preparePassages, chunkIdentity } from '../../src/enhance/retrieval.ts';

test('code declarations split exact source lines with stable preparation offsets', () => {
  const text = '# comment\nclass Demo:\n    def run(self):\n        return 1\n\ndef second():\n    return 2';
  const chunks = chunkSource('code', text, 260, '.py');
  assert.deepEqual(chunks.map(c => c.startLine), [1, 2, 3, 6]);
  assert.deepEqual(chunks.map(c => c.heading), ['', 'class Demo:', 'def run(self):', 'def second():']);
  for (const c of chunks) assert.equal(c.text, text.split('\n').slice(c.startLine - 1, c.endLine).join('\n'));
  const records = new Map(chunks.map(c => [chunkIdentity(c), {
    sourceId: c.sourceId, contentSHA: c.sourceSHA, chunkIndex: c.chunkIndex,
    startLine: c.startLine, endLine: c.endLine, reviewedText: c.text,
    safeHeading: c.heading || 'module', policy: 'reviewed', status: 'reviewed'
  }]));
  const result = preparePassages(chunkSource('code', text, 260, '.py'), new Map([['code', 'module']]), records);
  assert.equal(result.pending.length, 0);
  assert.equal(result.prepared.length, 4);
});

test('JS/TS and shell declarations are headings; code is never evaluated', () => {
  for (const [ext, text, heads] of [
    ['.ts', 'export class Demo {}\nexport async function run() {}', ['export class Demo {}', 'export async function run() {}']],
    ['.sh', 'first() {\n  echo ok\n}\nfunction second {\n  echo ok\n}', ['first() {', 'function second {']]
  ] as const) {
    assert.deepEqual(chunkSource('code', text, 260, ext).map(c => c.heading), heads);
  }
});

test('Markdown default retains its original section behavior', () => {
  const chunks = chunkSource('doc', '# Title\ndef example():\n  pass\n## Next\nText');
  assert.deepEqual(chunks.map(c => c.heading), ['Title', 'Title > Next']);
});
