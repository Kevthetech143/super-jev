// Preloaded by `npm test` (node --import ./test/clean-env.ts --test ...), so the suite gives the same
// result whatever the caller's shell exports. Before any test loads it clears the product's own settings:
// every SUPERJEV_* except SUPERJEV_TEST_* (developer test knobs), every judge profile's key and url
// variable (read from judge_profiles.json), and SWEEP_BATCH (the one product setting without the prefix).
// The test files node spawns inherit the cleared environment. Python twin: conftest.py.
// To run a single file the same way: node --import ./test/clean-env.ts --test test/<file>.test.ts
import { readFileSync } from 'node:fs';

const profiles = JSON.parse(readFileSync(new URL('../skills/super-jev/judge_profiles.json', import.meta.url), 'utf8')).profiles as
  Record<string, { key_env?: string; api_url_env?: string }>;
const names = new Set<string>(['SWEEP_BATCH']);
for (const profile of Object.values(profiles)) {
  for (const name of [profile.key_env, profile.api_url_env]) if (name) names.add(name);
}
for (const name of Object.keys(process.env)) {
  if (name.startsWith('SUPERJEV_') && !name.startsWith('SUPERJEV_TEST_')) names.add(name);
}
for (const name of names) delete process.env[name];
