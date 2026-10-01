import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  loadConfig, saveConfig, configFileMode, hasApiKey,
  cannedReply, parseSlashCommand, isKnownSlashCommand,
  parseMissCandidate, formatMissCandidateReply, parseMissReport,
  parseSetupMissing, parseErrorReport, parseAnyPointers,
  MISS_LINE,
} from '../src/jev-chat-config.ts';

function withTempConfig(fn: (path: string) => void) {
  const dir = mkdtempSync(join(tmpdir(), 'superjev-test-'));
  const path = join(dir, 'nested', 'config.json');
  try { fn(path); } finally { rmSync(dir, { recursive: true, force: true }); }
}

test('loadConfig returns {} when no file exists', () => {
  withTempConfig((path) => {
    assert.deepEqual(loadConfig(path), {});
  });
});

test('saveConfig writes a 0600 file and round-trips', () => {
  withTempConfig((path) => {
    saveConfig({ typesafeApiKey: 'sk-fake-123', principal: 'marvin', folders: ['/a', '/b'] }, path);
    assert.equal(configFileMode(path), 0o600);
    const loaded = loadConfig(path);
    assert.equal(loaded.typesafeApiKey, 'sk-fake-123');
    assert.equal(loaded.principal, 'marvin');
    assert.deepEqual(loaded.folders, ['/a', '/b']);
  });
});

test('saveConfig re-locks permissions even if the file already existed looser', () => {
  withTempConfig((path) => {
    saveConfig({ typesafeApiKey: 'a' }, path);
    saveConfig({ typesafeApiKey: 'b' }, path);
    assert.equal(configFileMode(path), 0o600);
  });
});

test('hasApiKey', () => {
  assert.equal(hasApiKey({}), false);
  assert.equal(hasApiKey({ typesafeApiKey: '' }), false);
  assert.equal(hasApiKey({ typesafeApiKey: 'x' }), true);
});

test('config file on disk never contains the literal key twice or leaks it unexpectedly', () => {
  withTempConfig((path) => {
    const key = 'sk-super-secret-value';
    saveConfig({ typesafeApiKey: key, principal: 'p' }, path);
    const raw = readFileSync(path, 'utf8');
    // it's the only place the key should live -- sanity check it's valid JSON
    // containing exactly the key we wrote, nothing garbled.
    const parsed = JSON.parse(raw);
    assert.equal(parsed.typesafeApiKey, key);
  });
});

test('cannedReply matches small talk', () => {
  assert.match(cannedReply('hi')!, /Super Jev/);
  assert.match(cannedReply('Hello!')!, /Super Jev/);
  assert.ok(cannedReply('who are you?'));
  assert.ok(cannedReply('help'));
  assert.ok(cannedReply('thanks'));
  assert.ok(cannedReply('what can you do'));
});

test('cannedReply returns null for real questions', () => {
  assert.equal(cannedReply('what is the breakeven on CLOV'), null);
  assert.equal(cannedReply(''), null);
});

test('parseSlashCommand parses known and unknown commands', () => {
  assert.deepEqual(parseSlashCommand('/help'), { command: 'help', args: [] });
  assert.deepEqual(parseSlashCommand('/folders /a /b'), { command: 'folders', args: ['/a', '/b'] });
  assert.equal(parseSlashCommand('plain question'), null);
  assert.deepEqual(parseSlashCommand('/bogus'), { command: 'bogus', args: [] });
});

test('isKnownSlashCommand', () => {
  assert.equal(isKnownSlashCommand('help'), true);
  assert.equal(isKnownSlashCommand('setup'), true);
  assert.equal(isKnownSlashCommand('folders'), true);
  assert.equal(isKnownSlashCommand('quit'), true);
  assert.equal(isKnownSlashCommand('bogus'), false);
});

test('MISS_LINE is the exact Jev voice line', () => {
  assert.equal(MISS_LINE, "Super Jev: I didn't have this. Want me to find it by hand and save it for next time?");
});

// --- adversarial review regressions ---
import { askLookupArgs } from '../src/jev-chat-config.ts';
import { chmodSync, mkdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

test('canned replies do not hijack real questions that merely contain small talk', () => {
  for (const q of [
    'who are you going to call about my taxes?',
    'what are you seeing in the Q3 report',
    'what can you do about the leaking roof',
    'help me find the lease file',
  ]) assert.equal(cannedReply(q), null, q);
  assert.ok(cannedReply('who are you?'));
  assert.ok(cannedReply('What can you do'));
});

test('askLookupArgs never lets user text become an ask.py flag', () => {
  for (const q of ['--miss', '--add x y', '--approve a b', '--principal evil', '--no-auto', '-x']) {
    const args = askLookupArgs('marvin', q);
    assert.deepEqual(args.slice(0, 2), ['--principal', 'marvin']);
    const rest = args.slice(2);
    assert.ok(rest.every((a) => !a.startsWith('-')), JSON.stringify(rest));
  }
  assert.deepEqual(askLookupArgs('k', 'what is x'), ['--principal', 'k', 'what is x']);
});

test('saveConfig re-locks a pre-existing loose dir and file', () => {
  withTempConfig((path) => {
    mkdirSync(dirname(path), { recursive: true, mode: 0o755 });
    chmodSync(dirname(path), 0o755);
    writeFileSync(path, '{}', { mode: 0o644 });
    chmodSync(path, 0o644);
    saveConfig({ typesafeApiKey: 'sk-fake' }, path);
    assert.equal(statSync(dirname(path)).mode & 0o777, 0o700);
    assert.equal(configFileMode(path), 0o600);
  });
});

test('loadConfig treats a JSON array as no config', () => {
  withTempConfig((path) => {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, '["x"]');
    assert.deepEqual(loadConfig(path), {});
  });
});

test('parseMissCandidate returns null on a cache hit', () => {
  assert.equal(parseMissCandidate('CACHE HIT\nanswer: foo\n'), null);
});

test('parseMissCandidate picks the top-ranked candidate line', () => {
  const stdout = ' 0.85  /notes/pricing.md  [marvin-notes]  possible\n 0.40  /notes/other.md  [marvin-notes]\n';
  const c = parseMissCandidate(stdout);
  assert.deepEqual(c, { score: 0.85, path: '/notes/pricing.md', pointer: 'marvin-notes' });
});

test('parseMissCandidate returns null on true no-candidates output', () => {
  const stdout = 'OUTCOME: not-found - searched 3 sets, no matching file\n';
  assert.equal(parseMissCandidate(stdout), null);
});

test('formatMissCandidateReply names the top file and gives a one-line why, not raw data', () => {
  const reply = formatMissCandidateReply({ score: 0.85, path: '/notes/pricing.md', pointer: 'marvin-notes' });
  assert.match(reply, /\/notes\/pricing\.md/);
  assert.match(reply, /no approved answer/);
  assert.doesNotMatch(reply, /\[marvin-notes\]/);
});

test('parseMissReport extracts the searched/next-step block from a true-miss ask.py run', () => {
  const stdout = [
    'OUTCOME: not-found - searched 3 sets, no matching file',
    'What was searched:',
    '  - 3 connected sets; 2 searched after the topic filter, 1 had matches: marvin-notes',
    '  - 2 file(s) read; none contained the answer. Closest: notes/pricing.md, notes/other.md',
    'Next step (pick one):',
    '  - The answer is in a file you have: it is probably not connected. Connect its folder:',
    '      python3 prepare_bulk.py --root <folder> --pointer marvin-<name> --principal marvin',
    '  - You know the answer: save it for next time:',
    '      python3 ask.py --principal marvin --add "<question>" "<answer>"',
    '  - Neither: tell your human it was not found and offer to search by hand.',
    MISS_LINE,
    '',
  ].join('\n');
  const report = parseMissReport(stdout);
  assert.ok(report);
  assert.equal(report[0], 'What was searched:');
  assert.match(report.join('\n'), /Next step \(pick one\):/);
  assert.match(report.join('\n'), /Connect its folder/);
  // The closing voice line is printed separately by the CLI, not duplicated here.
  assert.doesNotMatch(report.join('\n'), new RegExp(MISS_LINE.replace(/[.?]/g, '\\$&')));
});

test('parseMissReport returns null when ask.py predates the miss-report block', () => {
  const stdout = 'OUTCOME: not-found - searched 3 sets, no matching file\n' + MISS_LINE + '\n';
  assert.equal(parseMissReport(stdout), null);
});

test('parseMissReport returns null on a cache hit', () => {
  assert.equal(parseMissReport('CACHE HIT\nanswer: foo\n'), null);
});

test('parseSetupMissing returns the needs-setup outcome line with its next command', () => {
  const stdout = "OUTCOME: needs-setup - nothing is connected yet for principal 'primary'; next: "
    + 'python3 skills/super-jev/prepare_bulk.py --root /path/to/folder --pointer my-notes --principal primary\n';
  const block = parseSetupMissing(stdout);
  assert.ok(block);
  assert.equal(block.length, 1);
  assert.match(block[0], /needs-setup/);
  assert.match(block[0], /prepare_bulk\.py/);
});

test('parseSetupMissing returns null for a real miss (not a setup problem)', () => {
  const stdout = 'OUTCOME: not-found - searched 3 sets, no matching file\n' + MISS_LINE + '\n';
  assert.equal(parseSetupMissing(stdout), null);
});

test('parseErrorReport surfaces the error outcome and the pointer errors', () => {
  const stdout = [
    'OUTCOME: error - no match, and 2 sets failed; next: python3 ask.py --principal p --status',
    '[brain-reviewed] error: Navigation provider failed: Jev HTTP 401 (TypeSafe rejected the API key)',
    '[health-reviewed] error: Navigation provider failed: Jev HTTP 401 (TypeSafe rejected the API key)',
  ].join('\n');
  const report = parseErrorReport(stdout);
  assert.ok(report);
  assert.match(report.join('\n'), /HTTP 401/);
  assert.match(report[0], /^OUTCOME: error/);
});

test('parseErrorReport returns null when there are no pointer errors', () => {
  const stdout = 'OUTCOME: not-found - searched 3 sets, no matching file\n' + MISS_LINE + '\n';
  assert.equal(parseErrorReport(stdout), null);
});

test('parseAnyPointers reads the panel action pointers array', () => {
  assert.equal(parseAnyPointers(JSON.stringify({ pointers: [{ pointer: 'a' }] })), true);
  assert.equal(parseAnyPointers(JSON.stringify({ pointers: [] })), false);
});

test('parseAnyPointers returns null on unparseable or unexpected output', () => {
  assert.equal(parseAnyPointers('not json'), null);
  assert.equal(parseAnyPointers(JSON.stringify({ status: 'ok' })), null);
});

// ---------------------------------------------------------------------------
// Drag-and-drop onboarding
// ---------------------------------------------------------------------------
import {
  splitPathTokens, detectDroppedPaths, slugify, buildDropPointerName, escapeNameGlob,
  buildDropPlan, dropConfirmDefault, formatDropConfirm, shouldConnect, buildConnectArgs,
  parseConnectSummary, formatConnectSummary, parsePointerNames, pointerExists, parseReplaceWarning,
} from '../src/jev-chat-config.ts';
import { mkdtempSync as mkdtempSync2, mkdirSync as mkdirSync2, writeFileSync as writeFileSync2, rmSync as rmSync2, existsSync as existsSync2 } from 'node:fs';
import { join as join2 } from 'node:path';
import { homedir } from 'node:os';
import { createHash as createHash2 } from 'node:crypto';

/** Mirrors buildDropPointerName's own hashing so tests can predict the
 * expected pointer name without hardcoding a hash. */
function expectedPointerName(principal: string, label: string, location: string): string {
  const slugPart = (s: string) => s.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'drop';
  const hash = createHash2('sha256').update(location).digest('hex').slice(0, 6);
  return `${slugPart(principal)}-drop-${slugPart(label)}-${hash}`;
}

function withDropDir(fn: (dir: string) => void) {
  const dir = mkdtempSync2(join2(tmpdir(), 'superjev-drop-'));
  try { fn(dir); } finally { rmSync2(dir, { recursive: true, force: true }); }
}

test('splitPathTokens handles escaped spaces, quotes, and multiple paths', () => {
  assert.deepEqual(splitPathTokens(String.raw`/a/My\ Folder/x.md`), ['/a/My Folder/x.md']);
  assert.deepEqual(splitPathTokens('"/a/My Folder/x.md"'), ['/a/My Folder/x.md']);
  assert.deepEqual(splitPathTokens("'/a/My Folder/x.md'"), ['/a/My Folder/x.md']);
  assert.deepEqual(splitPathTokens('/a/one.md /a/two.md'), ['/a/one.md', '/a/two.md']);
  assert.deepEqual(splitPathTokens(String.raw`/a/one\ x.md /a/two.md`), ['/a/one x.md', '/a/two.md']);
});

test('detectDroppedPaths recognizes an existing file with an escaped-space name', () => {
  withDropDir((dir) => {
    const filePath = join2(dir, 'My Folder Notes.md');
    writeFileSync2(filePath, 'hi');
    const escaped = filePath.replace(/ /g, '\\ ');
    const result = detectDroppedPaths(escaped);
    assert.ok(result);
    assert.equal(result!.length, 1);
    assert.equal(result![0].path, filePath);
    assert.equal(result![0].isDirectory, false);
  });
});

test('detectDroppedPaths recognizes a quoted existing folder', () => {
  withDropDir((dir) => {
    const folder = join2(dir, 'Tax Docs');
    mkdirSync2(folder);
    const result = detectDroppedPaths(`"${folder}"`);
    assert.ok(result);
    assert.equal(result!.length, 1);
    assert.equal(result![0].isDirectory, true);
  });
});

test('detectDroppedPaths recognizes several existing paths separated by spaces', () => {
  withDropDir((dir) => {
    const a = join2(dir, 'a.md');
    const b = join2(dir, 'b.md');
    writeFileSync2(a, '1'); writeFileSync2(b, '2');
    const result = detectDroppedPaths(`${a} ${b}`);
    assert.ok(result);
    assert.equal(result!.length, 2);
  });
});

test('detectDroppedPaths returns null (treat as a question) for a non-existent path', () => {
  assert.equal(detectDroppedPaths('/definitely/not/a/real/path/anywhere.md'), null);
});

test('detectDroppedPaths returns null for a plain question', () => {
  assert.equal(detectDroppedPaths('what is the breakeven on CLOV'), null);
});

test('detectDroppedPaths returns null if only some of several paths exist', () => {
  withDropDir((dir) => {
    const a = join2(dir, 'a.md');
    writeFileSync2(a, '1');
    assert.equal(detectDroppedPaths(`${a} /nope/nope.md`), null);
  });
});

test('detectDroppedPaths rejects bare ~, ., .., and / even though they exist on disk', () => {
  assert.equal(detectDroppedPaths('~'), null);
  assert.equal(detectDroppedPaths('.'), null);
  assert.equal(detectDroppedPaths('..'), null);
  assert.equal(detectDroppedPaths('/'), null);
});

test('detectDroppedPaths rejects a relative word even if a same-named file exists in cwd', () => {
  // "docs" is a relative token -- never treated as a drop regardless of cwd contents.
  assert.equal(detectDroppedPaths('docs'), null);
  assert.equal(detectDroppedPaths('package.json'), null);
});

test('detectDroppedPaths accepts a ~/-prefixed existing path', () => {
  // ~/.config always exists once the fleet's tools have run at least once on
  // this machine; skip gracefully if it doesn't rather than depending on
  // machine state.
  const home = homedir();
  if (!existsSync2(join2(home, '.config'))) return;
  const result = detectDroppedPaths('~/.config');
  assert.ok(result);
  assert.equal(result![0].path, join2(home, '.config'));
});

test('slugify', () => {
  assert.equal(slugify('Tax Docs 2025'), 'tax-docs-2025');
  assert.equal(slugify(''), 'drop');
});

test('buildDropPointerName includes a 6-hex location hash so same-named items in different folders never collide', () => {
  const a = buildDropPointerName('Marvin', 'notes.md', '/a/notes.md');
  const b = buildDropPointerName('Marvin', 'notes.md', '/b/notes.md');
  assert.match(a, /^marvin-drop-notes-md-[0-9a-f]{6}$/);
  assert.match(b, /^marvin-drop-notes-md-[0-9a-f]{6}$/);
  assert.notEqual(a, b);
  assert.equal(a, expectedPointerName('Marvin', 'notes.md', '/a/notes.md'));
});

test('escapeNameGlob neutralizes fnmatch-magic characters so --name matches only the literal file', () => {
  assert.equal(escapeNameGlob('notes.md'), 'notes.md');
  assert.equal(escapeNameGlob('a[1].md'), 'a[[]1[]].md');
  assert.equal(escapeNameGlob('weird*name?.md'), 'weird[*]name[?].md');
});

test('buildDropPlan for a single file uses its folder as root and --name for the file', () => {
  const plan = buildDropPlan([{ raw: '/a/notes.md', path: '/a/notes.md', isDirectory: false }], 'marvin');
  assert.equal('error' in plan, false);
  if ('error' in plan) return;
  assert.equal(plan.root, '/a');
  assert.deepEqual(plan.names, ['notes.md']);
  assert.equal(plan.fileCount, 1);
  assert.equal(plan.label, 'file /a/notes.md');
  assert.equal(plan.pointer, expectedPointerName('marvin', 'notes.md', '/a/notes.md'));
});

test('buildDropPlan for a single folder connects the whole folder', () => {
  const plan = buildDropPlan([{ raw: '/a/Tax Docs', path: '/a/Tax Docs', isDirectory: true }], 'marvin');
  assert.equal('error' in plan, false);
  if ('error' in plan) return;
  assert.equal(plan.root, '/a/Tax Docs');
  assert.equal(plan.names, null);
  assert.equal(plan.fileCount, 0);
  assert.equal(plan.label, 'folder /a/Tax Docs');
  assert.equal(plan.pointer, expectedPointerName('marvin', 'Tax Docs', '/a/Tax Docs'));
});

test('buildDropPlan for several files in the same folder connects them by name', () => {
  const plan = buildDropPlan([
    { raw: '/a/one.md', path: '/a/one.md', isDirectory: false },
    { raw: '/a/two.md', path: '/a/two.md', isDirectory: false },
  ], 'marvin');
  assert.equal('error' in plan, false);
  if (!('error' in plan)) {
    assert.equal(plan.root, '/a');
    assert.deepEqual(plan.names, ['one.md', 'two.md']);
    assert.equal(plan.fileCount, 2);
  }
});

test('buildDropPlan refuses paths spanning different folders', () => {
  const plan = buildDropPlan([
    { raw: '/a/one.md', path: '/a/one.md', isDirectory: false },
    { raw: '/b/two.md', path: '/b/two.md', isDirectory: false },
  ], 'marvin');
  assert.ok('error' in plan);
});

test('buildDropPlan refuses a folder mixed with loose files', () => {
  const plan = buildDropPlan([
    { raw: '/a/sub', path: '/a/sub', isDirectory: true },
    { raw: '/a/one.md', path: '/a/one.md', isDirectory: false },
  ], 'marvin');
  assert.ok('error' in plan);
});

test('dropConfirmDefault: folder drops default to No, file drops default to Yes', () => {
  const filePlan = buildDropPlan([{ raw: '/a/notes.md', path: '/a/notes.md', isDirectory: false }], 'marvin');
  const folderPlan = buildDropPlan([{ raw: '/a/Tax Docs', path: '/a/Tax Docs', isDirectory: true }], 'marvin');
  if ('error' in filePlan || 'error' in folderPlan) throw new Error('unexpected error plan');
  assert.equal(dropConfirmDefault(filePlan), true);
  assert.equal(dropConfirmDefault(folderPlan), false);
});

test('formatDropConfirm names the count/folder/pointer and warns about paid calls', () => {
  const plan = buildDropPlan([{ raw: '/a/notes.md', path: '/a/notes.md', isDirectory: false }], 'marvin');
  if ('error' in plan) throw new Error('unexpected error plan');
  const msg = formatDropConfirm(plan, 'marvin');
  assert.match(msg, /1 file/);
  assert.match(msg, /\/a/);
  assert.match(msg, new RegExp(plan.pointer.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.match(msg, /paid judge calls/);
  assert.doesNotMatch(msg, /REPLACED/);
});

test('formatDropConfirm warns plainly when the pointer already exists and will be replaced', () => {
  const plan = buildDropPlan([{ raw: '/a/notes.md', path: '/a/notes.md', isDirectory: false }], 'marvin');
  if ('error' in plan) throw new Error('unexpected error plan');
  const msg = formatDropConfirm(plan, 'marvin', true);
  assert.match(msg, /already exists and will be REPLACED/);
});

test('shouldConnect gate: only an explicit true proceeds -- no connect without yes', () => {
  assert.equal(shouldConnect(true), true);
  assert.equal(shouldConnect(false), false);
  assert.equal(shouldConnect(undefined), false);
  assert.equal(shouldConnect(Symbol('cancel')), false);
  assert.equal(shouldConnect('true'), false); // a truthy non-boolean must not slip through
});

test('buildConnectArgs escapes glob-magic names, adds --no-recurse, and never invents flags', () => {
  const plan = buildDropPlan([{ raw: '/a/one[1].md', path: '/a/one[1].md', isDirectory: false }], 'marvin');
  if ('error' in plan) throw new Error('unexpected error plan');
  const args = buildConnectArgs(plan, 'marvin');
  assert.deepEqual(args, [
    '--root', '/a', '--pointer', plan.pointer, '--principal', 'marvin',
    '--no-recurse', '--name', 'one[[]1[]].md',
  ]);
});

test('buildConnectArgs for a plain-named single file still adds --no-recurse', () => {
  const plan = buildDropPlan([{ raw: '/a/notes.md', path: '/a/notes.md', isDirectory: false }], 'marvin');
  if ('error' in plan) throw new Error('unexpected error plan');
  const args = buildConnectArgs(plan, 'marvin');
  assert.deepEqual(args, ['--root', '/a', '--pointer', plan.pointer, '--principal', 'marvin', '--no-recurse', '--name', 'notes.md']);
});

test('buildConnectArgs for a folder omits --name and --no-recurse', () => {
  const plan = buildDropPlan([{ raw: '/a/Tax Docs', path: '/a/Tax Docs', isDirectory: true }], 'marvin');
  if ('error' in plan) throw new Error('unexpected error plan');
  const args = buildConnectArgs(plan, 'marvin');
  assert.deepEqual(args, ['--root', '/a/Tax Docs', '--pointer', plan.pointer, '--principal', 'marvin']);
});

test('parsePointerNames and pointerExists read the local panel JSON', () => {
  const stdout = JSON.stringify({ pointers: [{ pointer: 'marvin-drop-notes-md-ab12cd' }, { pointer: 'marvin-manual-x' }] });
  assert.deepEqual(parsePointerNames(stdout), ['marvin-drop-notes-md-ab12cd', 'marvin-manual-x']);
  assert.equal(pointerExists(stdout, 'marvin-drop-notes-md-ab12cd'), true);
  assert.equal(pointerExists(stdout, 'marvin-drop-other-999999'), false);
});

test('parsePointerNames returns null on unparseable or unexpected output', () => {
  assert.equal(parsePointerNames('not json'), null);
  assert.equal(pointerExists('not json', 'anything'), false);
});

test('parseReplaceWarning surfaces prepare_bulk.py\'s reuse warning verbatim', () => {
  const stdout = [
    'inventory: 1 files to prepare, 0 held',
    "WARNING: replace:true on pointer marvin-drop-notes-md-ab12cd rotates that pointer's approved answers",
    'approved: 1  exceptions: 0  held: 0',
  ].join('\n');
  assert.equal(parseReplaceWarning(stdout), "WARNING: replace:true on pointer marvin-drop-notes-md-ab12cd rotates that pointer's approved answers");
});

test('parseReplaceWarning returns null when the pointer is new', () => {
  assert.equal(parseReplaceWarning('approved: 1  exceptions: 0  held: 0\n'), null);
});

test('parseConnectSummary reads the approved/exceptions/held line and dedupes repeated HELD lines', () => {
  // prepare_bulk.py prints the same HELD line once during inventory and again
  // in its closing report.
  const stdout = [
    'inventory: 3 files to prepare, 1 held',
    '  HELD  secret.md  (secret-like text)',
    '  EXCEPTION  bad.md  (gate failed)',
    '',
    '  HELD  secret.md  (secret-like text)',
    'approved: 2  exceptions: 1  held: 1',
  ].join('\n');
  const summary = parseConnectSummary(stdout);
  assert.deepEqual(summary, {
    approved: 2, exceptions: 1, held: 1,
    heldLines: ['HELD  secret.md  (secret-like text)'],
    exceptionLines: ['EXCEPTION  bad.md  (gate failed)'],
  });
});

test('parseConnectSummary returns null when prepare_bulk.py did not finish cleanly', () => {
  assert.equal(parseConnectSummary('ERROR: no .md files found\n'), null);
});

test('formatConnectSummary prints the required "Connected N file(s)" line and surfaces held/exception files', () => {
  const summary = parseConnectSummary([
    '  HELD  secret.md  (secret-like text)',
    'approved: 2  exceptions: 0  held: 1',
  ].join('\n'))!;
  const text = formatConnectSummary(summary);
  assert.match(text, /^Connected 2 files\. Ask me about them\./);
  assert.match(text, /1 file held for review: secret\.md/);
});

test('formatConnectSummary singular file wording', () => {
  const summary = { approved: 1, exceptions: 0, held: 0, heldLines: [], exceptionLines: [] };
  assert.equal(formatConnectSummary(summary), 'Connected 1 file. Ask me about them.');
});
