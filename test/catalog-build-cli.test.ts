import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import {
  buildCatalog,
  buildRecord,
  extractDescription,
  extractTriggerPhrases
} from '../src/catalog-build-cli.ts';

const fixtureDir = new URL('./fixtures/catalog-skills/', import.meta.url).pathname;

test('extractDescription reads a single-line quoted frontmatter value', async () => {
  const md = await readFile(join(fixtureDir, 'alpha-skill/SKILL.md'), 'utf8');
  const description = extractDescription(md);
  assert.equal(description, 'Restart the alpha service and confirm it is healthy. Triggers: /alpha-skill, "restart alpha", "bounce the alpha service"');
});

test('extractDescription reads a block-scalar frontmatter value', async () => {
  const md = await readFile(join(fixtureDir, 'beta-skill/SKILL.md'), 'utf8');
  const description = extractDescription(md);
  assert.equal(description, 'Post a status update to the team channel, formatted the standard way. Use when a task finishes or a milestone is hit.');
});

test('extractTriggerPhrases pulls quoted and backtick/slash phrases from Trigger lines', async () => {
  const md = await readFile(join(fixtureDir, 'alpha-skill/SKILL.md'), 'utf8');
  const phrases = extractTriggerPhrases(md);
  assert.deepEqual(phrases, ['restart alpha', 'bounce the alpha service', 'kick alpha back up', '/alpha-skill']);
});

test('extractTriggerPhrases works on a plain "Trigger:" line with no bold', async () => {
  const md = await readFile(join(fixtureDir, 'beta-skill/SKILL.md'), 'utf8');
  const phrases = extractTriggerPhrases(md);
  assert.deepEqual(phrases, ['post an update', 'send a status update', '/beta-skill']);
});

test('buildRecord shapes a full catalog v2 record with dedup and auto variants', async () => {
  const md = await readFile(join(fixtureDir, 'alpha-skill/SKILL.md'), 'utf8');
  const record = buildRecord('alpha-skill', md);
  assert.equal(record.id, 'alpha-skill');
  assert.ok(record.text.startsWith('Restart the alpha service'));
  assert.ok(record.text.length <= 300);
  assert.deepEqual(record.negatives, []);
  assert.deepEqual(record.tags, ['alpha-skill']);
  // Trigger phrases first, slash name already present so not duplicated.
  assert.deepEqual(record.utterances.slice(0, 4), ['restart alpha', 'bounce the alpha service', 'kick alpha back up', '/alpha-skill']);
  // Two auto variants appended: lowercase-no-punct, then first clause.
  const tail = record.utterances.slice(4);
  assert.equal(tail.length, 2);
  assert.ok(tail[0].startsWith('restart the alpha service'));
  assert.equal(tail[1], 'Restart the alpha service and confirm it is healthy');
});

test('buildCatalog walks a directory, skips non-skill subdirs, and caps text at 300 chars', async () => {
  const records = await buildCatalog(fixtureDir);
  const ids = records.map((r) => r.id).sort();
  assert.deepEqual(ids, ['alpha-skill', 'beta-skill']);
  for (const r of records) assert.ok(r.text.length <= 300);
});

test('CLI writes a JSON catalog file end to end', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'catalog-build-'));
  try {
    const outPath = join(dir, 'catalog.json');
    const result = spawnSync('node', [new URL('../src/catalog-build-cli.ts', import.meta.url).pathname, fixtureDir, outPath], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    const parsed = JSON.parse(await readFile(outPath, 'utf8'));
    assert.equal(parsed.length, 2);
    assert.ok(parsed.every((r: { id: string; text: string; utterances: string[]; negatives: string[]; tags: string[] }) =>
      typeof r.id === 'string' && typeof r.text === 'string' && Array.isArray(r.utterances) && Array.isArray(r.negatives) && Array.isArray(r.tags)));
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('CLI with no args prints usage and exits 0', () => {
  const result = spawnSync('node', [new URL('../src/catalog-build-cli.ts', import.meta.url).pathname], { encoding: 'utf8' });
  assert.equal(result.status, 0);
});

test('CLI exits 1 when only one arg is given (missing out.json)', () => {
  const result = spawnSync('node', [new URL('../src/catalog-build-cli.ts', import.meta.url).pathname, fixtureDir], { encoding: 'utf8' });
  assert.equal(result.status, 1);
});

test('CLI exits 1 on a nonexistent skills directory', () => {
  const result = spawnSync('node', [new URL('../src/catalog-build-cli.ts', import.meta.url).pathname, '/no/such/dir', '/tmp/out.json'], { encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /does not exist/);
});
