# Freshness baseline evaluation — 2026-09-20

## Deterministic lifecycle checks

36 Python tests pass across public CLI, storage regressions, freshness lifecycle and adversarial corruption cases. These tests use real temporary files and SQLite, injected retrieval callbacks/subprocesses, and controlled clocks. They validate harness behavior, not Jev accuracy or actual medical/software freshness.

Covered: changed/deleted/corrupt registered files; corrupted SQLite; malformed cache bodies; missing/future/expired upstream-check timestamps; freshness-policy and context separation; source refresh and re-registration; principal revocation; deadlines crossed during retrieval, cache validation and approval lock acquisition; no automatic discovery of unregistered new records. Existing snapshot keys remain compatible.

The existing stress suite passes all 31 checks, including 1,000 concurrent exact-cache hits with 16 workers and only the initial injected retrieval. The JavaScript suite passes 640 tests.

Bugs found and repaired:

- Explicit JSON null for a freshness policy silently selected snapshot mode. It now returns a structured input error.
- A parseable but malformed cached answer/evidence object was accepted. Invalid shapes now cause a cache miss.
- A currentness deadline could pass during source validation or SQLite lock waiting. Real time is rechecked at acceptance/commit boundaries, with regression tests.

## Live project-memory use

A real Jev request asked whether removing a project pointer deletes original files. Source: the public DATA-LIFECYCLE.md decision record in this branch, reviewed as one bounded project dataset. No private brain or health records were sent.

The initial preparation used paragraph offsets that did not match the runtime chunker. Result: `preparation-required` after one description call; no answer was saved. This onboarding error was retained. Rebuilding preparation with exported `chunkSource` fixed the boundary mismatch without changing the question or source.

The corrected request returned `ready` with supporting original-source locations using two Jev calls. The caller checked the answer, approved exact supporting evidence, then repeated the same request: `verified-cache-hit`. This demonstrates one real project decision moving through retrieval, review, persistence and reuse. It is not a broad accuracy result or proof of net token savings.

## Limits and next decisions

`checkedAt` represents a trusted whole-scope upstream check, not an event date or proof that an answer is current. No automatic fetching, new-file discovery, medical reconciliation, release-status inference, source editing/deletion, or multi-user authentication was added. The control panel and DATA-LIFECYCLE.md explicitly describe those boundaries. Source-aware project questions and synthetic fault injection must not be presented as external held-out evaluation.
