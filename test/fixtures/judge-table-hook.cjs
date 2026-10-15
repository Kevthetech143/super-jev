// A --require preload for tests. The Node loader reads skills/super-jev/judge_profiles.json by URL and a child process has no way to be
// handed another table, so when SJ_TABLE names a file, every read of judge_profiles.json gets that file instead. The file is a made-up
// table with a `large` row (see test/large-table.ts and the large_row fixture in skills/super-jev/tests/conftest.py): the shipped table
// does not offer Large, which waits for its own test. Without SJ_TABLE this does nothing.
const fs = require('node:fs');
const read = fs.readFileSync;
fs.readFileSync = function (path, ...rest) {
  return read.call(this, process.env.SJ_TABLE && String(path).endsWith('judge_profiles.json') ? process.env.SJ_TABLE : path, ...rest);
};
