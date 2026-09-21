# bench-1.0 — what was measured for 1.0.0-rc.1

Measured 2026-09-21 in a fresh copy of the worktree (`git ls-files` + untracked,
no `.git`), with `HOME` set to an empty temp dir and no TypeSafe API key set.
Node 24, pytest installed, no network needed except the skill-search local run.

## Suites

- `npm test` — node built-in runner over `test/*.test.ts`,
  `test/enhance/*.test.ts`, `test/bench/*.test.ts`:
  **647 pass, 0 fail, 0 skipped** (13.6 s).
- `python3 -m pytest skills/super-jev/tests -q` — skill front-door suite:
  **1222 passed, 10 skipped** (173 s).
- `npm run skill-search -- --roots-file <roots> --request-file <request> --local-only`:
  exit 0, **3 skills suggested from 1 root, `source: local`**, no network.
  roots file: `["<worktree>/skills"]`;
  request file: `{"request":"look up a saved fact","context":[]}`.
- `npm run demo` — the loop runs once, exits 0 (goal verified, healthy).

All four commands are the README "60-second quick start" verbatim and pass
without a key, without `~/.claude` state, and without a git checkout.

## Re-run

```bash
git clone <repo> && cd <repo>
npm test
python3 -m pytest skills/super-jev/tests -q
printf '["%s/skills"]' "$PWD" > /tmp/ROOTS.json
printf '{"request":"look up a saved fact","context":[]}' > /tmp/REQUEST.json
npm run skill-search -- --roots-file /tmp/ROOTS.json --request-file /tmp/REQUEST.json --local-only
npm run demo
```

Heavier benches (`npm run bench:live`, `bench/fetch-bench.ts`) are experimental
and intentionally excluded from the rc.1 gate; see `docs/experimental/`.
