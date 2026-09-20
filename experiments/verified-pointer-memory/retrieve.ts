/** Portable bridge to the existing engine; no private fleet paths or fallback model. */
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { isAbsolute } from 'node:path';
import { retrieveSources } from '../../src/enhance/retrieval.ts';
import { Jev } from '../../src/jev.ts';

const hash = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');
const checkedFile = async (path: string, expected: string) => {
  if (typeof path !== 'string' || !isAbsolute(path) || !/^[a-f0-9]{64}$/.test(expected)) {
    throw new Error('invalid local binding');
  }
  const bytes = await readFile(path);
  if (hash(bytes) !== expected) throw new Error('stale binding');
  return bytes;
};

async function main() {
  let raw = '';
  for await (const part of process.stdin) raw += part;
  const input = JSON.parse(raw);
  const registry = JSON.parse(await readFile(input.registry, 'utf8'));
  const entry = registry.datasets[input.dataset];
  let manifest;
  const sources = [];
  try {
    if (!entry || !Array.isArray(entry.originals) || !entry.originals.length) throw new Error('missing originals');
    for (const original of entry.originals) await checkedFile(original.path, original.sha256);
    // Parse exactly the bytes whose hash was checked.
    manifest = JSON.parse((await checkedFile(entry.manifestPath, entry.manifestSHA256)).toString('utf8'));
    if (manifest.descriptionsAffirmed !== true || manifest.expectedPolicy !== 'reviewed') throw new Error('review needed');
    const ids = new Set();
    for (const source of manifest.sources) {
      if (typeof source.id !== 'string' || !source.id || ids.has(source.id) || typeof source.description !== 'string') throw new Error('invalid source');
      ids.add(source.id);
      const text = (await checkedFile(source.path, source.contentSHA)).toString('utf8');
      sources.push({ id: source.id, description: source.description, text });
    }
  } catch {
    return { status: 'preparation-required', reason: 'Missing, stale or unreviewed dataset; no provider call made.' };
  }
  if (!process.env.TYPESAFE_API_KEY) return { status: 'error', reason: 'Set TYPESAFE_API_KEY for live retrieval.' };
  const preparations = new Map((manifest.preparations ?? []).map((p: any) => [`${p.sourceId}:L${p.startLine}-${p.endLine}`, p]));
  const result = await retrieveSources(sources, input.question, {
    transport: new Jev(), descriptionsReviewed: true, expectedPolicy: 'reviewed',
    preparation: preparations, maxRetries: 0,
  });
  const paths = new Map(manifest.sources.map((s: any) => [s.id, s.path]));
  return {
    status: result.status, request: result.request, trace: result.trace,
    passages: result.status === 'ready' ? result.passages.map(p => ({ ...p, path: paths.get(p.sourceId) })) : [],
    pending: result.pending,
  };
}

main().then(result => console.log(JSON.stringify(result))).catch(() => {
  console.log(JSON.stringify({ status: 'error', reason: 'Retrieval failed; check local dataset and provider configuration.' }));
  process.exitCode = 1;
});
