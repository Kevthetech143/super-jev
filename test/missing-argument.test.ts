// A missing required argument names itself on the first stderr line, before the usage
// text, with the same exit code as before (1). Before: `permit-cli --action "..."` without
// --snapshot printed only the usage text, so a caller could not tell what was missing.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

const src = (p: string) => new URL(`../src/${p}`, import.meta.url).pathname;

const cases: Array<[string, string[], RegExp]> = [
  ['permit-cli.ts', ['--action', 'delete the old notes', '--dry-run'], /--snapshot/],
  ['skill-search-cli.ts', ['--roots-file', '/tmp/roots.json'], /--request-file/],
  ['sweep-cli.ts', ['--records', '/tmp/records.jsonl'], /--questions/],
  ['fetch-cli.ts', ['--k', '3'], /--catalog/],
  ['experimental/chain-cli.ts', ['--dry-run'], /--spec/],
  ['cli.ts', ['organize'], /INPUT\.json/],
  ['catalog-cli.ts', ['validate'], /<catalog\.json>/],
  ['catalog-cli.ts', ['learn', '/tmp/ledger.jsonl'], /<catalog\.json>/],
  ['catalog-cli.ts', ['build', '/tmp/skills'], /<out\.json>/],
  ['catalog-build-cli.ts', ['/tmp/skills'], /<out\.json>/],
];

for (const [cli, args, names] of cases) {
  test(`${cli} ${args.join(' ')}: the first line names what is missing`, () => {
    const r = spawnSync(process.execPath, [src(cli), ...args], { encoding: 'utf8', timeout: 10_000 });
    assert.equal(r.status, 1);
    const first = r.stderr.split('\n')[0];
    assert.match(first, /required|missing/i, `first stderr line: ${first}`);
    assert.match(first, names, `first stderr line: ${first}`);
    assert.match(r.stderr, /\n\n(super-jev|skill-search) /, 'the usage follows the message, after a blank line');
  });
}

test('cli.ts: an unknown command names the command', () => {
  const r = spawnSync(process.execPath, [src('cli.ts'), 'tidy', 'input.json'], { encoding: 'utf8', timeout: 10_000 });
  assert.equal(r.status, 1);
  assert.match(r.stderr.split('\n')[0], /tidy/);
});

test('permit-cli.ts: no arguments still prints the usage and exits 0', () => {
  const r = spawnSync(process.execPath, [src('permit-cli.ts')], { encoding: 'utf8', timeout: 10_000 });
  assert.equal(r.status, 0);
  assert.match(r.stdout.split('\n')[0], /^super-jev permit/);
});

test('catalog-cli.ts: an extra argument is named, not just the usage', () => {
  for (const [args, what] of [[['validate', 'a.json', 'b.json'], /validate takes one/], [['learn', 'a.jsonl', 'b.json', 'c.json'], /learn takes/]] as const) {
    const r = spawnSync(process.execPath, [src('catalog-cli.ts'), ...args], { encoding: 'utf8', timeout: 10_000 });
    assert.equal(r.status, 1);
    const first = r.stderr.split('\n')[0];
    assert.match(first, /too many arguments/i, `first stderr line: ${first}`);
    assert.match(first, what, `first stderr line: ${first}`);
  }
});
