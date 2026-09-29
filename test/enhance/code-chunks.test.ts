import test from 'node:test';
import assert from 'node:assert/strict';
import { chunkSource } from '../../src/enhance/retrieval.ts';

test('Markdown default retains its original section behavior', () => {
  const chunks = chunkSource('doc', '# Title\ndef example():\n  pass\n## Next\nText');
  assert.deepEqual(chunks.map(c => c.heading), ['Title', 'Title > Next']);
});
