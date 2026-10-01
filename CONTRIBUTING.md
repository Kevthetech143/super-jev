# Contributing

Use Node.js 24 or newer and Python 3.10 or newer (the install lines are in [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md), step 1). Run `npm test` and both documented demo commands before submitting a pull request.

Keep the core independent of business domains. Put domain rules and integrations in separate packs. New tools must validate inputs, describe their side effects, honor cancellation where possible, and explain how uncertain outcomes are reconciled.

Include a regression test for behavior changes. Never commit credentials, private traces, customer data, or generated run logs. Avoid claims about speed, accuracy, or profitability without reproducible measurements.

## Maturity

The `ask` front-door command and the harness `cached` action are proven in a live pilot: cache-first lookup, parallel pointer navigation with visible per-pointer errors, and one-file manual pointers all ran there before landing here.

This project uses Node's native TypeScript stripping. Avoid enums, parameter properties, and other TypeScript features that require code generation. No build toolchain is currently required.

## Merging with the gated merge script

`ci/merge_gated.sh <pr-number> [--repo-dir <path>]` waits for CI to go green,
scrubs the PR title and body for stray performance numbers with
`ci/scrub_numbers.py` (and refuses to merge when it finds any), squash-merges
the PR, deletes the branch, confirms the merged state, and optionally
fast-forwards a local checkout with `git pull --ff-only`. It prints a single
final line: `PASS <sha>` or `FAIL: <step>`. Keep measurement-style claims out
of PR titles and bodies so the scrub step stays quiet.

## Running the tests

```bash
python3 -m venv .venv && . .venv/bin/activate && python -m pip install pytest
npm test
python3 -m pytest skills/super-jev/tests -q
```

If `venv` says `ensurepip is not available` (Debian and Ubuntu system Python), run the uv lines from [GETTING-STARTED step 1](docs/GETTING-STARTED.md) first; no sudo needed.

Both runners start from a clean environment, so the result does not depend on what your shell exports. Before any test loads they clear every `SUPERJEV_*` setting except `SUPERJEV_TEST_*` (the developer test knobs), every judge profile's key and url variable (read from `skills/super-jev/judge_profiles.json`), and `SWEEP_BATCH`. `npm test` does this through `test/clean-env.ts`, preloaded with `node --import`; the Python suite does it in `skills/super-jev/tests/conftest.py`. To run one Node file the same way:

```bash
node --import ./test/clean-env.ts --test test/<file>.test.ts
```

A plain `node --test <file>` skips the preload and sees your shell as it is.

PRs fill in the PR template (`.github/PULL_REQUEST_TEMPLATE.md`), including the "Docs touched" section.
