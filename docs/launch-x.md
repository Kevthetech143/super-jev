# X launch draft

Post after the repository is public and replace the bracketed link:

I built Jev Loop: a small open-source TypeScript harness for Jev.

Evidence → decisions → tools → feedback.

Domain packs, policy checks, bounded runs and replayable logs. Zero runtime dependencies. MIT.

Early V1; both demos tested with live Jev.

https://github.com/Kevthetech143/jev-loop

## Optional follow-up

The core works across domains. Included: simulated service recovery and document review.

13 tests pass. The offline demo needs no API key. Bring a TypeSafe key for live Jev decisions with the local demo tools.

Contributions welcome: domain packs, feed adapters and live API tests.

## Release handoff

Create a public repository, upload this directory without `runs/` or any filled `.env`, and let the included GitHub Actions workflow run. Set the description to “A small, extensible decision-to-action harness for TypeSafe Jev.”

Use V0.1.0 as the first release. Keep “experimental” in the release notes; the live smoke tests cover only the two included demos. The name is a working project name; no package-name or trademark availability claim has been made. `private: true` in package.json prevents accidental npm publishing; it does not restrict source sharing or the MIT license.
