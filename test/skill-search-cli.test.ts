/**
 * skill-search-cli tests: the default skill-discovery entry.
 *
 * Primary evidence is real entrypoint behavior: the tests spawn the actual
 * CLI (`node src/skill-search-cli.ts`) in child processes against temp skill
 * roots and assert on the one JSON object it prints. Unit tests inject fake
 * transports into `runSkillSearch` for the judge path (the lead runs live);
 * no test reaches the network.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtemp, mkdir, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  DESCRIPTION_CAP, JUDGE_TOP_K, MAX_INPUT_CHARS, isUnderspecified, normalizeSkillName,
  resolveExactSkill, runSkillSearch, scanSkillRoots,
  type SkillEntry, type SkillSearchResult
} from '../src/skill-search-cli.ts';
import { choiceAnswer } from '../src/enhance/stub.ts';
import type { Answer, Evaluation, Evaluator, Question, Request } from '../src/types.ts';

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CLI = join(REPO, 'src', 'skill-search-cli.ts');

function skillMd(name: string, description: string): string {
  return `---\nname: ${name}\ndescription: "${description}"\n---\n\n# ${name}\n\nBody text.\n`;
}

async function makeRoot(skills: Array<[string, string]>, opts: { fileName?: string } = {}): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'skill-search-root-'));
  for (const [name, desc] of skills) {
    const dir = join(root, name);
    await mkdir(dir, { recursive: true });
    await writeFile(join(dir, opts.fileName ?? 'SKILL.md'), skillMd(name, desc));
  }
  return root;
}

const roots: string[] = [];
async function tempRoot(skills: Array<[string, string]>, opts: { fileName?: string } = {}): Promise<string> {
  const r = await makeRoot(skills, opts);
  roots.push(r);
  return r;
}

test.after(async () => {
  for (const r of roots) await rm(r, { recursive: true, force: true });
});

function baseEnv(extra: NodeJS.ProcessEnv = {}): NodeJS.ProcessEnv {
  const { TYPESAFE_API_KEY: _dropped, ...rest } = process.env;
  return { ...rest, ...extra };
}

function runCli(args: string[], env: NodeJS.ProcessEnv = {}): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolvePromise) => {
    execFile(process.execPath, [CLI, ...args], { cwd: REPO, env: baseEnv(env), timeout: 90000 },
      (error, stdout, stderr) => {
        const code = error && typeof (error as { code?: unknown }).code === 'number'
          ? (error as { code: number }).code : 0;
        resolvePromise({ code, stdout: String(stdout), stderr: String(stderr) });
      });
  });
}

async function cliJson(rootsFile: string[], request: unknown, extraArgs: string[] = [], env: NodeJS.ProcessEnv = {}) {
  const dir = await mkdtemp(join(tmpdir(), 'skill-search-io-'));
  roots.push(dir);
  const rootsPath = join(dir, 'roots.json');
  const requestPath = join(dir, 'request.json');
  await writeFile(rootsPath, JSON.stringify(rootsFile));
  await writeFile(requestPath, JSON.stringify(request));
  const out = await runCli(['--roots-file', rootsPath, '--request-file', requestPath, ...extraArgs], env);
  return out;
}

function parseStdout(out: { code: number; stdout: string; stderr: string }): SkillSearchResult {
  assert.equal(out.code, 0, `CLI failed: ${out.stderr}`);
  const lines = out.stdout.trim().split('\n');
  assert.equal(lines.length, 1, 'stdout must be exactly one JSON object');
  return JSON.parse(lines[0]) as SkillSearchResult;
}

const ALLOWED_KEYS = ['status', 'candidates', 'source', 'metadataSkipped', 'duplicateCount', 'error', 'model', 'usage'];
const ALLOWED_CANDIDATE_KEYS = ['id', 'name', 'path', 'description'];

function assertCleanShape(result: SkillSearchResult): void {
  for (const k of Object.keys(result)) assert.ok(ALLOWED_KEYS.includes(k), `unexpected top-level key ${k}`);
  assert.ok(Array.isArray(result.candidates));
  assert.ok(result.candidates.length <= JUDGE_TOP_K, 'at most 3 candidates');
  for (const c of result.candidates) {
    assert.deepEqual(Object.keys(c).sort(), [...ALLOWED_CANDIDATE_KEYS].sort());
    assert.ok(c.description.length <= DESCRIPTION_CAP, 'descriptions are short');
  }
}

/**
 * Fake judge transport answering from a table keyed on skill id, mirroring
 * the repo's own table-transport pattern (wire key -> record id via the
 * request's named-reference state). Records every request it receives.
 */
function tableTransport(
  table: Record<string, { level: string; confidence: number }>,
  seen: Request[] = [],
  model = 'fake-judge'
): Evaluator {
  return {
    evaluate: async (request: Request, _signal: AbortSignal): Promise<Evaluation> => {
      seen.push(request);
      const state = (request.state as { records?: Record<string, { id?: string }> } | undefined)?.records ?? {};
      const keys = Object.keys(state);
      const answers: Record<string, Answer> = {};
      for (const [wireKey, question] of Object.entries(request.questions) as [string, Question][]) {
        if (question.type !== 'choice') continue;
        const recordKey = keys.find(k => wireKey === `q_relevance_${k}` || wireKey === `q0_r${keys.indexOf(k)}`);
        assert.ok(recordKey, `wire key ${wireKey} does not name a record`);
        const id = state[recordKey].id!;
        const entry = table[id] ?? { level: 'none', confidence: 0.9 };
        answers[wireKey] = choiceAnswer(entry.level, entry.confidence, Object.keys(question.criteria));
      }
      return { model, answers };
    }
  };
}

const DEMO_SKILLS: Array<[string, string]> = [
  ['pdf-editor', 'Edit and merge PDF documents from the command line.'],
  ['image-resize', 'Resize and convert images in bulk.'],
  ['web-search', 'Search the public web and fetch page text.']
];

// ---------------------------------------------------------------- exact: no network

test('exact /slash-name resolves locally with zero transport calls', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const seen: Request[] = [];
  const { result } = await runSkillSearch({
    roots: [root], request: '/pdf-editor', apiKey: 'dummy', transport: tableTransport({}, seen)
  });
  assert.equal(result.status, 'exact');
  assert.equal(result.source, 'local');
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].name, 'pdf-editor');
  assert.ok(result.candidates[0].path.startsWith(root));
  assert.equal(result.model, undefined);
  assert.equal(seen.length, 0);
});

test('CLI: exact bare name is case-insensitive and makes no judge call', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const out = await cliJson([root], { request: 'PDF-Editor' });
  const result = parseStdout(out);
  assertCleanShape(result);
  assert.equal(result.status, 'exact');
  assert.equal(result.source, 'local');
  assert.equal(result.candidates[0].id, 'pdf-editor');
  assert.ok(out.stderr.includes('status=exact'));
});

test('ambiguous duplicate normalized names do not resolve exact', () => {
  const skills: SkillEntry[] = [
    { id: 'Foo', name: 'Foo', path: '/r1/Foo/SKILL.md', description: 'd' },
    { id: 'foo', name: 'foo', path: '/r2/foo/SKILL.md', description: 'd' }
  ];
  assert.equal(resolveExactSkill(skills, 'foo'), null);
});

// ---------------------------------------------------------------- exact means exact (no fuzzy near-miss)

test('exact means an actual exact normalized name match, never a near-miss', () => {
  const skills: SkillEntry[] = [
    { id: 'card', name: 'card', path: '/r/card/SKILL.md', description: 'd' },
    { id: 'image-resize', name: 'image-resize', path: '/r/image-resize/SKILL.md', description: 'd' }
  ];
  // Regression: 'care' is one edit from 'card' but must NOT resolve exact.
  assert.equal(resolveExactSkill(skills, 'care'), null);
  assert.equal(resolveExactSkill(skills, '/care'), null);
  // A typo never resolves as exact; a whole sentence never does either.
  assert.equal(resolveExactSkill(skills, 'cardd'), null);
  assert.equal(resolveExactSkill(skills, 'please show me the card skill'), null);
  // The real name still resolves, slash or bare, case-insensitive.
  assert.equal(resolveExactSkill(skills, '/card')?.id, 'card');
  assert.equal(resolveExactSkill(skills, 'Card')?.id, 'card');
});

test('normalizeSkillName strips one slash, case and trailing punctuation', () => {
  assert.equal(normalizeSkillName('  /PDF-Editor! '), 'pdf-editor');
  assert.equal(normalizeSkillName('web-search'), 'web-search');
});

// ---------------------------------------------------------------- underspecified: clarify, never invent

test('isUnderspecified is narrow and deterministic', () => {
  assert.equal(isUnderspecified('it'), true);
  assert.equal(isUnderspecified('do that thing', []), true);
  assert.equal(isUnderspecified('', []), true);
  // Known vague cases: action + bare pronoun with no context clarifies.
  assert.equal(isUnderspecified('update it', []), true);
  assert.equal(isUnderspecified('check this', []), true);
  assert.equal(isUnderspecified('fix that', []), true);
  assert.equal(isUnderspecified('restart it', []), true);
  // Usable context resolves the referent: proceeds to the judge.
  assert.equal(isUnderspecified('update it', ['the pdf-editor config']), false);
  assert.equal(isUnderspecified('restart it', ['the agent is stuck']), false);
  // Substantive requests always proceed.
  assert.equal(isUnderspecified('update repository configuration', []), false);
  assert.equal(isUnderspecified('help me merge pdfs'), false);
  assert.equal(isUnderspecified('fix the login bug', []), false);
});

test('CLI: pronoun-only request with no context clarifies and never calls the judge', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const out = await cliJson([root], { request: 'it' }, [], { TYPESAFE_API_KEY: 'dummy-key-never-used' });
  const result = parseStdout(out);
  assert.equal(result.status, 'clarify');
  assert.equal(result.source, 'local');
  assert.deepEqual(result.candidates, []);
  assert.ok(result.error && result.error.length > 0);
  assert.ok(out.stderr.includes('status=clarify'));
});

test('CLI: action+pronoun "update it" clarifies locally, never invents a task', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const out = await cliJson([root], { request: 'update it' }, [], { TYPESAFE_API_KEY: 'dummy-key-never-used' });
  const result = parseStdout(out);
  assertCleanShape(result);
  assert.equal(result.status, 'clarify');
  assert.equal(result.source, 'local');
  assert.deepEqual(result.candidates, []);
});

test('CLI: "update it" with usable context proceeds past the clarify check', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const out = await cliJson(
    [root],
    { request: 'update it', context: ['the pdf-editor configuration file'] },
    ['--local-only'],
    { TYPESAFE_API_KEY: 'dummy-key-never-used' }
  );
  const result = parseStdout(out);
  assert.equal(result.status, 'suggestions');
  assert.equal(result.source, 'local');
  assert.ok(result.candidates.length > 0);
});

// ---------------------------------------------------------------- metadata handling

test('metadataSkipped and duplicateCount are visible on stdout', async () => {
  const rootA = await tempRoot([['dup-skill', 'First copy.']]);
  const rootB = await tempRoot([['dup-skill', 'Second copy.']]);
  const badDir = join(rootB, 'bad-skill');
  await mkdir(badDir, { recursive: true });
  await writeFile(join(badDir, 'SKILL.md'), '---\nname: bad-skill\n---\n\nBody.\n');
  const { result } = await runSkillSearch({ roots: [rootA, rootB], request: '/dup-skill' });
  assert.equal(result.status, 'exact');
  assert.equal(result.metadataSkipped, 1);
  assert.equal(result.duplicateCount, 1);
  assertCleanShape(result);
});

test('skills with empty or malformed descriptions are excluded and counted', async () => {
  const root = await tempRoot([
    ['good-skill', 'Does good things well.'],
    ['empty-desc', ''],
  ]);
  // A skill whose frontmatter has no description at all.
  const dir = join(root, 'no-desc');
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, 'SKILL.md'), '---\nname: no-desc\n---\n\nBody.\n');
  const { skills, skipped } = await scanSkillRoots([root]);
  assert.equal(skills.length, 1);
  assert.equal(skills[0].id, 'good-skill');
  assert.equal(skipped, 2);
});

test('a symlinked skill folder is scanned like a real one', async () => {
  const root = await tempRoot([['real-skill', 'A real folder.']]);
  const elsewhere = await tempRoot([['linked-skill', 'Installed elsewhere and linked in.']]);
  await symlink(join(elsewhere, 'linked-skill'), join(root, 'linked-skill'));
  const { skills } = await scanSkillRoots([root]);
  assert.deepEqual(skills.map(s => s.id).sort(), ['linked-skill', 'real-skill']);
});

test('YAML-ism leaks and unmatched quotes are malformed and skipped', async () => {
  const root = await mkdtemp(join(tmpdir(), 'skill-search-front-'));
  roots.push(root);
  const raw: Array<[string, string]> = [
    ['null-desc', '---\nname: null-desc\ndescription: null\n---\n\nBody.\n'],
    ['tilde-desc', '---\nname: tilde-desc\ndescription: ~\n---\n\nBody.\n'],
    ['unquoted-desc', '---\nname: unquoted-desc\ndescription: "unclosed\n---\n\nBody.\n'],
    ['good-desc', '---\nname: good-desc\ndescription: A real description.\n---\n\nBody.\n'],
    ['empty-desc', '---\nname: empty-desc\ndescription: ""\n---\n\nBody.\n'],
  ];
  for (const [name, body] of raw) {
    const dir = join(root, name);
    await mkdir(dir, { recursive: true });
    await writeFile(join(dir, 'SKILL.md'), body);
  }
  const { skills, skipped } = await scanSkillRoots([root]);
  assert.equal(skills.length, 1);
  assert.equal(skills[0].id, 'good-desc');
  assert.equal(skipped, 4, 'null, tilde, unmatched quote, and empty all count as skipped');
});

test('CLI: lowercase skill.md is found and skipped count is visible', async () => {
  const root = await tempRoot([['lower-skill', 'Lowercase filename skill.']], { fileName: 'skill.md' });
  const out = await cliJson([root], { request: '/lower-skill' });
  const result = parseStdout(out);
  assert.equal(result.status, 'exact');
  assert.equal(result.candidates[0].name, 'lower-skill');
  assert.ok(out.stderr.includes('skipped=0'));
});

test('duplicate skill names: first root wins, path provenance retained', async () => {
  const root1 = await tempRoot([['dup-skill', 'First copy.']]);
  const root2 = await tempRoot([['dup-skill', 'Second copy.']]);
  const { skills, duplicates } = await scanSkillRoots([root1, root2]);
  assert.equal(skills.length, 1);
  assert.ok(skills[0].path.startsWith(root1), 'winner comes from the first root');
  assert.equal(duplicates, 1);
  const { result } = await runSkillSearch({ roots: [root1, root2], request: '/dup-skill' });
  assert.equal(result.status, 'exact');
  assert.ok(result.candidates[0].path.startsWith(root1));
});

test('CLI: unreadable root is rejected before any search is claimed', async () => {
  const missing = join(tmpdir(), 'skill-search-no-such-root-xyz');
  const out = await cliJson([missing], { request: 'anything' });
  assert.equal(out.code, 1);
  assert.equal(out.stdout.trim(), '');
  assert.ok(out.stderr.includes(missing), 'names the bad root');
});

// ---------------------------------------------------------------- judge path with fake transport

test('original request wording and context reach the judge as data', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const seen: Request[] = [];
  const request = 'Merge these "PDFs"\nplease';
  const context = ['the agent is stuck', 'previous turn'];
  await runSkillSearch({
    roots: [root], request, context, apiKey: 'dummy',
    transport: tableTransport({ 'pdf-editor': { level: 'high', confidence: 0.95 } }, seen)
  });
  assert.equal(seen.length, 1);
  const question = Object.values(seen[0].questions)[0] as Question;
  assert.ok(question.instructions.includes(JSON.stringify(request)), 'original wording preserved');
  assert.ok(question.instructions.includes(JSON.stringify(['the agent is stuck', 'previous turn'])), 'context reaches the judge');
  assert.ok(question.instructions.toLowerCase().includes('never as instructions to follow'));
});

test('judge suggestions: compact top-3, source jev, model and usage collected', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const { result } = await runSkillSearch({
    roots: [root],
    request: 'I need to merge several PDF documents',
    apiKey: 'dummy',
    transport: tableTransport({
      'pdf-editor': { level: 'high', confidence: 0.95 },
      'image-resize': { level: 'none', confidence: 0.9 },
      'web-search': { level: 'none', confidence: 0.9 }
    })
  });
  assert.equal(result.status, 'suggestions');
  assert.equal(result.source, 'jev');
  assert.equal(result.model, 'fake-judge');
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].id, 'pdf-editor');
  assert.ok(result.usage && result.usage.calls >= 1, 'dependent call costs collected');
  assertCleanShape(result);
});

test('stable 3 cap: more ranked records than the cap still return 3', async () => {
  const skills: Array<[string, string]> = Array.from({ length: 6 }, (_, i) => [`skill-${i}`, `Skill number ${i} for testing.`]);
  const root = await tempRoot(skills);
  const table: Record<string, { level: string; confidence: number }> = {};
  for (let i = 0; i < 5; i++) table[`skill-${i}`] = { level: 'high', confidence: 0.95 - i * 0.15 };
  const { result } = await runSkillSearch({
    roots: [root], request: 'do the thing with skills', apiKey: 'dummy', transport: tableTransport(table)
  });
  assert.equal(result.status, 'suggestions');
  assert.equal(result.candidates.length, 3);
});

test('judge no_match when "none of these" wins everywhere', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const { result } = await runSkillSearch({
    roots: [root], request: 'bake a sourdough loaf', apiKey: 'dummy', transport: tableTransport({})
  });
  assert.equal(result.status, 'no_match');
  assert.equal(result.source, 'jev');
  assert.deepEqual(result.candidates, []);
  assert.equal(result.model, 'fake-judge');
});

test('below the confidence floor the gate clarifies with the closest candidates', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const { result } = await runSkillSearch({
    roots: [root],
    request: 'maybe something with documents',
    apiKey: 'dummy',
    transport: tableTransport({ 'pdf-editor': { level: 'high', confidence: 0.5 } })
  });
  assert.equal(result.status, 'clarify');
  assert.equal(result.source, 'jev');
  assert.ok(result.candidates.length >= 1, 'closest candidates are offered');
  assert.ok(result.error && result.error.length > 0);
});

test('empty catalog is a local no_match with no judge call', async () => {
  const root = await tempRoot([]);
  const seen: Request[] = [];
  const { result } = await runSkillSearch({
    roots: [root], request: 'anything at all', apiKey: 'dummy', transport: tableTransport({}, seen)
  });
  assert.equal(result.status, 'no_match');
  assert.equal(result.source, 'local');
  assert.deepEqual(result.candidates, []);
  assert.equal(seen.length, 0);
});

// ---------------------------------------------------------------- fallback: service failure is never no_match

test('missing API key is a clearly-marked local fallback', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const seen: Request[] = [];
  const { result } = await runSkillSearch({
    roots: [root], request: 'merge my pdfs', apiKey: '', transport: tableTransport({}, seen)
  });
  assert.equal(result.status, 'fallback');
  assert.equal(result.source, 'local');
  assert.ok(result.error && result.error.includes('TYPESAFE_API_KEY'));
  assert.ok(result.candidates.length > 0, 'local candidates still offered');
  assert.equal(seen.length, 0);
});

test('CLI: missing key falls back without touching the network', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const out = await cliJson([root], { request: 'merge my pdfs' });
  const result = parseStdout(out);
  assertCleanShape(result);
  assert.equal(result.status, 'fallback');
  assert.equal(result.source, 'local');
  assert.ok(out.stderr.includes('status=fallback'));
});

test('transport error is a fallback, never a no_match', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const failing: Evaluator = {
    evaluate: async () => { throw new Error('boom: connection reset'); }
  };
  const { result } = await runSkillSearch({
    roots: [root], request: 'merge my pdfs', apiKey: 'dummy', transport: failing
  });
  assert.equal(result.status, 'fallback');
  assert.notEqual(result.status, 'no_match');
  assert.equal(result.source, 'local');
  assert.ok(result.candidates.length > 0);
  assert.ok(result.usage && result.usage.calls >= 1, 'the failed judge call is still counted');
  assert.equal(result.metadataSkipped, 0);
  assert.equal(result.duplicateCount, 0);
});

test('transport timeout is a fallback, never a no_match', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const hanging: Evaluator = {
    evaluate: async (_request: Request, signal: AbortSignal): Promise<Evaluation> => {
      await new Promise<void>((_resolve, reject) => {
        if (signal.aborted) { reject(new DOMException('The operation was aborted.', 'AbortError')); return; }
        signal.addEventListener('abort', () => reject(new DOMException('The operation was aborted.', 'AbortError')), { once: true });
      });
      throw new Error('unreachable');
    }
  };
  const { result } = await runSkillSearch({
    roots: [root], request: 'merge my pdfs', apiKey: 'dummy', transport: hanging, timeoutMs: 50
  });
  assert.equal(result.status, 'fallback');
  assert.equal(result.source, 'local');
  assert.ok(result.candidates.length > 0);
});

test('unusable judge answers are a fallback, never a no_match', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const empty: Evaluator = {
    evaluate: async () => ({ model: 'fake-empty', answers: {} })
  };
  const { result } = await runSkillSearch({
    roots: [root], request: 'merge my pdfs', apiKey: 'dummy', transport: empty
  });
  assert.equal(result.status, 'fallback');
  assert.equal(result.source, 'local');
  assert.ok(result.candidates.length > 0);
});

// ---------------------------------------------------------------- local-only and output safety

test('--local-only never calls the judge even with a key set', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const out = await cliJson([root], { request: 'merge my pdfs' }, ['--local-only'], { TYPESAFE_API_KEY: 'dummy-key-never-used' });
  const result = parseStdout(out);
  assert.equal(result.status, 'suggestions');
  assert.equal(result.source, 'local');
  assert.equal(result.model, undefined);
  assert.ok(result.candidates.length > 0);
});

test('stdout carries no secret and no large payload', async () => {
  const longDesc = 'L'.repeat(2000);
  const root = await tempRoot([['long-skill', longDesc]]);
  const out = await cliJson([root], { request: 'use the long skill please' }, ['--local-only'], { TYPESAFE_API_KEY: 'sk-test-secret-xyz-123' });
  const result = parseStdout(out);
  assert.ok(!out.stdout.includes('sk-test-secret-xyz-123'), 'no secret on stdout');
  assert.ok(!out.stderr.includes('sk-test-secret-xyz-123'), 'no secret on stderr');
  assertCleanShape(result);
  for (const c of result.candidates) assert.ok(c.description.length <= DESCRIPTION_CAP);
});

test('running the CLI creates no files and executes nothing', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const before = (await readdir(root)).sort();
  const out = await cliJson([root], { request: '/pdf-editor' });
  parseStdout(out);
  const after = (await readdir(root)).sort();
  assert.deepEqual(after, before);
});

test('--help exits 0 with usage on stdout', async () => {
  const out = await runCli(['--help']);
  assert.equal(out.code, 0);
  assert.ok(out.stdout.includes('--roots-file'));
});

// ---------------------------------------------------------------- combined input size cap

test('oversized request+context fails explicitly before any judge call', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const seen: Request[] = [];
  const big = 'x'.repeat(MAX_INPUT_CHARS + 1);
  await assert.rejects(
    runSkillSearch({
      roots: [root], request: 'short request', context: [big], apiKey: 'dummy',
      transport: tableTransport({}, seen)
    }),
    /together exceed/
  );
  assert.equal(seen.length, 0, 'no transport call was made');
});

test('request within cap but context pushing over the cap still fails', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const seen: Request[] = [];
  const over = 'y'.repeat(MAX_INPUT_CHARS - 10);
  await assert.rejects(
    runSkillSearch({
      roots: [root], request: 'a real request', context: [over], apiKey: 'dummy',
      transport: tableTransport({}, seen)
    }),
    /together exceed/
  );
  assert.equal(seen.length, 0, 'no transport call was made');
});

test('combined input just under the cap proceeds to the judge', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const seen: Request[] = [];
  const under = 'z'.repeat(MAX_INPUT_CHARS - 50);
  const { result } = await runSkillSearch({
    roots: [root], request: 'merge my pdfs', context: [under], apiKey: 'dummy',
    transport: tableTransport({ 'pdf-editor': { level: 'high', confidence: 0.95 } }, seen)
  });
  assert.equal(seen.length, 1, 'judge was called');
  assert.ok(['suggestions', 'clarify'].includes(result.status));
});

// ---------------------------------------------------------------- malformed inputs

test('malformed inputs fail closed with exit 1 and no stdout JSON', async () => {
  const root = await tempRoot(DEMO_SKILLS);
  const dir = await mkdtemp(join(tmpdir(), 'skill-search-bad-'));
  roots.push(dir);
  const rootsPath = join(dir, 'roots.json');
  const requestPath = join(dir, 'request.json');
  await writeFile(rootsPath, JSON.stringify([root]));
  await writeFile(requestPath, JSON.stringify({ request: 'x' }));

  const badRoots = join(dir, 'bad-roots.json');
  await writeFile(badRoots, JSON.stringify({ not: 'an array' }));
  let out = await runCli(['--roots-file', badRoots, '--request-file', requestPath]);
  assert.equal(out.code, 1);
  assert.equal(out.stdout.trim(), '');

  const badRequest = join(dir, 'bad-request.json');
  await writeFile(badRequest, JSON.stringify({ context: [] }));
  out = await runCli(['--roots-file', rootsPath, '--request-file', badRequest]);
  assert.equal(out.code, 1);
  assert.equal(out.stdout.trim(), '');

  const relativeRoots = join(dir, 'relative-roots.json');
  await writeFile(relativeRoots, JSON.stringify(['relative/path']));
  out = await runCli(['--roots-file', relativeRoots, '--request-file', requestPath]);
  assert.equal(out.code, 1);
  assert.equal(out.stdout.trim(), '');

  const hugeRequest = join(dir, 'huge-request.json');
  await writeFile(hugeRequest, JSON.stringify({ request: 'x'.repeat(5000) }));
  out = await runCli(['--roots-file', rootsPath, '--request-file', hugeRequest]);
  assert.equal(out.code, 1);
  assert.equal(out.stdout.trim(), '');

  const hugeContext = join(dir, 'huge-context.json');
  await writeFile(hugeContext, JSON.stringify({ request: 'a short request', context: ['y'.repeat(9000)] }));
  out = await runCli(['--roots-file', rootsPath, '--request-file', hugeContext]);
  assert.equal(out.code, 1);
  assert.equal(out.stdout.trim(), '');
  assert.ok(out.stderr.includes('together exceed'), 'diagnostic names the combined cap');

  out = await runCli(['--roots-file', rootsPath]);
  assert.equal(out.code, 1);
});
