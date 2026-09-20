# Freshness baseline evaluation — 2026-09-20

## Deterministic lifecycle checks

36 Python tests pass across public CLI, storage regressions, freshness lifecycle and adversarial corruption cases. These tests use real temporary files and SQLite, injected retrieval callbacks/subprocesses, and controlled clocks. They validate harness behavior, not Jev accuracy or actual medical/software freshness.

Covered: changed/deleted/corrupt registered files; corrupted SQLite; malformed cache bodies; missing/future/expired upstream-check timestamps; freshness-policy and context separation; source refresh and re-registration; principal revocation; deadlines crossed during retrieval, cache validation and approval lock acquisition; no automatic discovery of unregistered new records. Existing snapshot keys remain compatible.

The existing stress suite passes all 31 checks, including 1,000 concurrent exact-cache hits with 16 workers and only the initial injected retrieval. The JavaScript suite passes 640 tests; the bundled skill suite passes 1,124 tests.

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

## Whole-repository development trial

Inventoried committed main `9134aff`: 186 tracked files, 3,291,316 bytes. Included 181 UTF-8 text files (2,715,481 bytes), producing 3,688 exact runtime chunks. Four test/fixture files containing credential-like patterns were excluded pending review (not declared actual secrets); one empty file was excluded. Private `.local` data was outside scope. Originals and preparations are bound to the 181 included files. The index has broad coverage, but a request still uses filter-8 descriptions, a top-3 document shortlist and bounded wide recovery; Jev does not inspect every file per request.

A separate agent wrote six source-aware development questions (five answerable, one unsupported feature). An answering worker did not read the answer key. Initial retrieval: four `ready`, two `no-match`, totaling 14 Jev calls. Review found two ready outputs incomplete; they were not approved. One answerable privacy question was refused even though the expected document and relevant passages were selected. The absent hosted-deployment feature returned no match; that is not exhaustive proof of absence.

The other two ready outputs supported useful answers, but the worker submitted non-exact/out-of-window quotes, including text from the wrong checkout. Both approval attempts failed, so the initial agent loop saved zero answers. Initial failures are retained. The lead checked the already-returned evidence, corrected the two submissions without new retrieval, approved them, and verified two exact-cache repeats (0.337 and 0.341 seconds, wall-clock subprocess measurements). Thus two of five answerable cases completed after caller repair; two remained partial and one remained unresolved. Do not report four ready outputs as four complete answers.

Worker reporting also needed correction: it initially combined retrieval/review/approval statuses, estimated timings, and retained only a projection of the first raw ready response. Those limitations are preserved; the first engine result was recovered from its durable pending ticket and labeled as such. Estimated timings are excluded from performance claims. The skill now explicitly requires preserving the complete response and copying evidence from its returned version without changing quote formatting.

This is a small source-aware development test, not a general reliability benchmark. It exposes both caller errors and retrieval coverage/completeness limits. A refresh generator and all raw local artifacts are retained in the private in-repo onboarding directory; no general autonomous source updater or source-write interface was shipped.
