# Changelog

## Unreleased

Fixes only. No feature work, no version bump.

- Bound journaling by the run deadline. A journal whose append never resolved held a run open past its `timeoutMs`; every append, including the opening and closing records, now goes through the same abort signal. A failed or timed-out intent record stops the step before the effect and reports "action not attempted"; if the effect ran and only its completion record failed, the run reports "action outcome unknown". The run result gains a `journaled` flag.
- Reject special files in the organizer CLI without blocking. Input is opened with `O_RDONLY | O_NONBLOCK` and its type is taken from `fstat` on the descriptor already held, so a named pipe with no writer is refused immediately instead of parking the process inside `open()`. This also closes the check-then-reopen window. Byte limits, no-echo error messages and descriptor cleanup are unchanged.
- Check a `score` against its own distribution. The provider documents `score` as the probability-weighted mean of the level indices, which may land between levels, so `validateEvaluation` now compares it to that expectation. It previously accepted `score: 0` alongside probabilities `{0: 0, 1: 1}`.
- Accept a rounding-sized shortfall in a probability total. The previous 0.001 tolerance discarded all 32 answers of a recorded batch because two distributions totalled 0.99. The band is now one rounding step of 0.01; inside it the values are renormalized and the provider's untouched values are retained on the answer as `rawProbabilities`; outside it the answer is rejected. A total of 0, a total above 1.05, a negative value, a `NaN`, a missing level and an extra level all still fail, nothing is substituted, and a validation failure still triggers no retry of any kind.
- Reject a `confidence` above its distribution peak.
- Add `docs/provider-contract.md`, which labels every claim about the response contract DOCUMENTED, OBSERVED or INFERRED, and `scripts/reproduce-findings.py`, an offline reproducer for the three findings.

Validation: 47 automated tests (26 before, 21 added), all three offline demos, and the three offline reproducers now passing.

Limitations:

- **Not live-validated by this change.** No API call was made. Every measured number cited here and in `docs/provider-contract.md` comes from 30 previously recorded calls to `jev-1.13.0`.
- The 0.01 band and the confidence rule are INFERRED, not promised by the provider, which documents no rounding contract at all. The confidence rule is the most likely of these to be wrong and should be relaxed if a live probe shows the statistic legitimately exceeding the peak.
- The score expectation rule has never met real provider output: there is no recorded `score` answer to check it against, only `choice` answers.
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
