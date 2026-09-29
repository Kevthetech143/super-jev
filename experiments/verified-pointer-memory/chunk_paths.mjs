// Canonical local-only chunking; never calls a provider.
import { chunkSource } from '../../src/enhance/retrieval.ts';
let input = '';
for await (const part of process.stdin) input += part;
const sources = JSON.parse(input);
process.stdout.write(JSON.stringify(sources.map(s => chunkSource(s.id, s.text))));
