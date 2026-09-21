# Local deployment hook contract — skill-search (NOT installed by default)

The launcher and the generic super-jev runtime are secret-free: they never
read a secret file, never invoke a provider, never place a key in argv or
logs, and never print any `*KEY*` value. The model key for live search
reaches the runtime ONLY through environment inheritance: the launcher's
`subprocess.Popen` call passes its own environment to the child unchanged,
so a key exported before the launcher runs arrives at the runtime with no
launcher-side secret handling at all.

This directory stages the EXACT contract the lead implements at local
deployment, plus a reference implementation (`hook-wrapper.sh`). The wrapper
is a local deployment artifact: it is never installed by default, runs only when search.sh is invoked (or explicitly selected), and the
secret-free launcher never references it.

## Contract (exact)

1. The wrapper exports `TYPESAFE_API_KEY` into the ENVIRONMENT ONLY.
   The key must never appear in argv, in logs, or on stdout.
2. Inherited environment wins: if `TYPESAFE_API_KEY` is already set when the
   wrapper runs, the wrapper MUST NOT call the provider and MUST NOT
   overwrite the value.
3. Provider failure is honest failure: if the provider command exits nonzero
   or prints an empty key, the wrapper prints a sanitized JSON fallback on
   stdout and exits nonzero WITHOUT running the launcher. It must never
   run the launcher keyless and let a later step claim a live search.
4. No keyless live claim: without a key in the environment, the runtime
   reports `fallback`/`local-unavailable` honestly. A fallback is never a
   successful live search.
5. `--local-only` never needs a provider or a key. The launcher never
   invokes any provider command itself, on any flag combination.
6. No CA environment is required by this wiring. If a deployment's provider
   endpoint needs a private CA, add the site's usual CA variables
   (e.g. `SSL_CERT_FILE`) inside the deployment wrapper only.

## Lead adapter point

`hook-wrapper.sh` reads the provider command from `SKILL_SEARCH_PROVIDER_CMD`
(e.g. `/usr/local/bin/local-provider api-key`). The production search.sh defaults that to local-key-provider.py, which reuses
the existing jev-check loader. An explicitly configured provider wins. The wrapper `exec`s the launcher so
the environment — including the key — is inherited, never re-typed.

## Verification (fake provider fixture, in tests/run-tests.sh)

- env reaches the child: `TYPESAFE_API_KEY=FAKE-...` exported before the
  launcher is visible to the fake runtime's environment.
- inherited precedence: pre-set `TYPESAFE_API_KEY` beats the provider's
  output; unset key falls back to the provider.
- provider failure: nonzero/empty provider output -> wrapper exits nonzero,
  the launcher is never invoked.
- no secret in outputs: the fake key value appears nowhere on stdout,
  stderr, or in any file the launcher/runtime writes.
- `--local-only` with no key and no provider configured completes against
  the local path.
