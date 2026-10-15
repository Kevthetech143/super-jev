// A made-up judge table for the tests that need a `large` size. The shipped table (skills/super-jev/judge_profiles.json) offers Small
// and Medium only: Large waits for its own test. The mechanism behind a bigger size (the loaders, the long-context header, the check's
// timeout and batch budget) is still tested, against the shipped table plus one made-up `large` row on the Jev profile. The shipped
// table is never edited. Python twin: the large_row fixture in skills/super-jev/tests/conftest.py (same row, test/fixtures/large-row.json).
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SHIPPED = new URL('../skills/super-jev/judge_profiles.json', import.meta.url);
const LARGE_ROW = JSON.parse(readFileSync(new URL('./fixtures/large-row.json', import.meta.url), 'utf8'));
/** The preload that makes a child process read a table of ours: `--require` it and set SJ_TABLE to the table's path. */
export const TABLE_HOOK = fileURLToPath(new URL('./fixtures/judge-table-hook.cjs', import.meta.url));

const made: string[] = [];
process.on('exit', () => { for (const dir of made) rmSync(dir, { recursive: true, force: true }); });

/** The path of a copy of the shipped table whose Jev profile also states the made-up `large` row. */
export function largeTable(): string {
  const data = JSON.parse(readFileSync(SHIPPED, 'utf8'));
  data.profiles['typesafe-jev'].contexts.large = structuredClone(LARGE_ROW);
  const dir = mkdtempSync(join(tmpdir(), 'large-table-'));
  made.push(dir);
  const file = join(dir, 'table-with-large.json');
  writeFileSync(file, JSON.stringify(data));
  return file;
}
