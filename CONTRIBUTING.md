# Contributing

Use Node.js 24+. Run `npm test` and both documented demo commands before submitting a pull request.

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
