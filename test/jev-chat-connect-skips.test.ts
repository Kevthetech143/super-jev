// The chat's connect summary passes on what connect left out (its SKIP lines), the same
// way it already passes on HELD and EXCEPTION lines.
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseConnectSummary, formatConnectSummary } from '../src/jev-chat-config.ts';

test('formatConnectSummary shows the SKIP lines connect printed', () => {
  const stdout = [
    'writer: builtin -- descriptions quoted from each file\'s headings, no model call',
    '  SKIP  3 .md file(s) in folders skipped by default: documents/ (2), profile/ (1); to connect one, connect that folder as its own set (--root FOLDER --pointer NEW-NAME)',
    '  SKIP  2 file(s) of other types (.py 1, .csv 1); only .md files connect',
    '  SKIP  2 file(s) of other types (.py 1, .csv 1); only .md files connect',
    'inventory: 1 files to prepare, 0 held',
    '',
    'approved: 1  exceptions: 0  held: 0',
    'CONNECTED 1, HELD 0, FAILED 0',
  ].join('\n');
  const summary = parseConnectSummary(stdout);
  assert.ok(summary);
  const text = formatConnectSummary(summary!);
  assert.match(text, /documents\/ \(2\)/);
  assert.match(text, /only \.md files connect/);
  assert.equal(text.match(/only \.md files connect/g)?.length, 1, 'a repeated SKIP line is shown once');
});
