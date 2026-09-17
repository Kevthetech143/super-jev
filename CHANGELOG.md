# Changelog

## Unreleased

No version bump.

- Stress-test follow-ups. `fetch`: every batch question carries a "none of these" option; a record ranks only if it beats it, and a run where `none` wins everywhere returns `ranked: []` with `noMatch: true` and the confidence of `none`. New `--prefilter N` (default 40, `0` disables): a local token-overlap (BM25-lite) pass keeps the top N records before the one provider call; `calls` is reported and is 1 when the prefilter is at or under the per-call size. Fetch's own token window default is 16000 so 40 short records fit one call. Five no-match cases added to `bench/fetch-cases.json`. Hook gate: a Stop turn with no tool_result evidence no longer passes silently; the gate runs against the user's last prompt, prints one advisory line only if it reports a checkable claim, never blocks, and the ledger line carries `unchecked: true`. Permit: `format` is destructive only with a storage target (disk, drive, volume, partition, SD card, USB, `mkfs`, `diskutil erase`); "format the code" and "preview the changes a formatter would make" no longer trip. Wishlist items 4, 5, 6 marked built (6 experimental).

- Add an installable Claude Code agent skill at `skills/super-jev/` (`superjev.py`, `SKILL.md`, tests): one front door — `gate`, `verify`, `sweep`, `bench`, `ask`, `status`, plus `permit`/`chain`/`fetch` stubbed to name their own wishlist item instead of failing silently. `gate` and `verify` wrap external claim-gate and report-verify tools via `SUPERJEV_GATE_CMD`/`SUPERJEV_VERIFY_CMD` (no default tool ships in this repo); `sweep` and `bench` default `SUPERJEV_REPO` to this checkout's own root, so they need no env at all. Add `docs/wishlist.md` (the roadmap behind the stubbed doors, with every measured figure and internal path stripped) and a "Agent front door" section in the README and in `docs/agents.md`. Tests run with `python3 -m pytest skills/super-jev/tests -q` or `npm run test:skill`; CI runs them on `ubuntu-latest` via `actions/setup-python`.

Fixes:

- Bound journaling by the run deadline. A journal whose append never resolved held a run open past its `timeoutMs`; every append, including the opening and closing records, now goes through the same abort signal. A failed or timed-out intent record stops the step before the effect and reports "action not attempted"; if the effect ran and only its completion record failed, the run reports "action outcome unknown". The run result gains a `journaled` flag.
- Reject special files in the organizer CLI without blocking. Input is opened with `O_RDONLY | O_NONBLOCK` and its type is taken from `fstat` on the descriptor already held, so a named pipe with no writer is refused immediately instead of parking the process inside `open()`. This also closes the check-then-reopen window. Byte limits, no-echo error messages and descriptor cleanup are unchanged.
- Check a `score` against its own distribution. The provider documents `score` as the probability-weighted mean of the level indices, which may land between levels, so `validateEvaluation` now compares it to that expectation. It previously accepted `score: 0` alongside probabilities `{0: 0, 1: 1}`.
- Accept a rounding-sized shortfall in a probability total. The previous 0.001 tolerance discarded all 32 answers of a recorded batch because two distributions totalled 0.99. The band is now one rounding step of 0.01; inside it the values are renormalized and the provider's untouched values are retained on the answer as `rawProbabilities`; outside it the answer is rejected. A total of 0, a total below 0.99 or above 1.01, a negative value, a `NaN`, a missing level and an extra level all still fail, nothing is substituted, and a validation failure still triggers no retry of any kind.
- **Do not** reject a `confidence` above its distribution peak. An earlier draft of this change added that rule as an inferred one, flagging it as the single rule a live probe could overturn. One live call on 2026-09-17 overturned it: `jev-1.13.0` returned a `score` answer with `confidence` 0.71 over a peak probability of 0.66. The rule is gone rather than narrowed to `choice` answers, because the docs give no formula for `confidence` and the score counterexample leaves no basis for asserting the relationship anywhere. `confidence` is now checked only for what the provider documents, a finite number in `[0, 1]`. The live response is checked in as `test/fixtures/live-score-2026-09-17.json`.
- `validateEvaluation` no longer writes to the response it is given. It returns the evaluation to use downstream, the provider's parsed reply is left as it arrived, and a frozen response validates. The loop journals and decides on the returned object. Validating an already-validated evaluation returns an equal object.
- Never trust a provider-supplied `rawProbabilities`. That field is this harness's audit record of what was actually returned. On the renormalizing path an incoming value is overwritten with the provider's real probabilities; otherwise it is accepted only if it renormalizes to them, and rejected as `Untrusted rawProbabilities` if not.
- Add `docs/provider-contract.md`, which labels every claim about the response contract DOCUMENTED, OBSERVED or INFERRED, and `scripts/reproduce-findings.py`, an offline reproducer for the three findings.

Validation: 50 automated tests (26 before, 24 added), all three offline demos, and the three offline reproducers now passing.

Limitations:

- **One live call only.** A single call to `jev-1.13.0` on 2026-09-17 returned one `score`, one `choice` and one `noul` answer; it is checked in as a fixture. Every other measured number cited here and in `docs/provider-contract.md` comes from 30 previously recorded calls to the same model version.
- The 0.01 rounding band is INFERRED, not promised by the provider, which documents no rounding contract at all. The live response totalled exactly 1.00 and so neither strengthens nor weakens it.
- The score expectation rule has now met real provider output exactly once, and matched it exactly: score 1.85 is the exact probability-weighted mean of the live distribution. One answer from one model version is a confirmation, not a guarantee.
- **Mitigations are still pending.** The classification order-sensitivity, the context and evidence-completeness work, named record references, bounded batching and source-completeness checks are not in this change. The robustness report's outstanding-benchmarks list stands in full.

## 0.2.0

- Rename package metadata to super-jev to match the repository.
- Add a configurable data-organizer domain pack that runs through the existing loop.
- Add a JSON CLI with explicit live/demo modes, output protection and a review queue.
- Document input/output contracts for terminal-equipped LLM agents.
- Add tests for grouping, review routing, validation, CLI output and overwrite protection.

Validation: 26 automated tests, both original offline demos, and the organizer offline demo. Regression coverage includes private error handling, review-policy enforcement, output permissions, failure cleanup, preventing inference when output exists, excluding incidental record metadata, bounded input reads, and the successful mocked live-response contract. One live organizer smoke test on `jev-1.13.0` classified all four synthetic records as expected, grouped three records, and routed the `other` record to review. Low-confidence routing is covered by mocked tests. See [organizer validation](docs/organizer-validation.md) for results and limits. This release remains experimental; no npm publication, MCP server, or exchange integration is included.

## 0.1.0

Initial harness, Jev adapter, two domain demos, JSONL logs and 13 tests.
