# Reviewed assistance and advisory hints — 2026-09-20

Local trusted-caller experiment. This report measures retrieval, caller review and exact reuse separately; it does not establish autonomous accuracy or total token savings.

## Automated checks

53 memory tests, 31 stress checks (including 1,000 concurrent exact hits), 640 JavaScript tests, and 1,124 skill tests passed. Independent review accepted the implementation after fixes for old-attempt visibility across pointer generations, exact v2 schema validation, and accurate audit-retention documentation. Seven advisory hint states preserve actual result statuses; cache hits get no extra hint. A rehearsal migrated a copy of the installed v2 database to v3 while preserving five pointers, twelve cache entries and zero pending tickets.

## Real repository trial

Frozen implementation: `94b6f904fe4f858f2b2c1cc30cbde7b051e72db9`. Reviewed scope: all 192 nonempty tracked UTF-8 files, 4,439 preparations. One empty fixture had no text; four public synthetic credential fixtures received exact-commit/path review waivers. Private archives were excluded. Ten source-aware development questions were written separately from the answering worker; they are not a held-out benchmark.

The worker first bypassed the configured credential adapter: ten requests stopped before Jev. These setup failures were retained. After correcting the adapter, one fresh run per question used 22 reported judge calls: eight returned `ready`, two returned `no-match`. Calls took roughly 2.4–3 seconds; returned traces did not include token usage.

Independent claim-level review found:

- Six complete, supported answers approved directly from retrieved evidence.
- One ambiguous question spanning different gates: the scoped Python code-pattern answer was supported, but the original reference label described another component. Excluded from clean accuracy scoring.
- One overextended approval: an answer listed miss reasons absent from its returned excerpts. Its initial approval and cache hit are retained as a review failure, not counted as success. The lead supplied the missing reviewed lines through `assist`, approved a narrower supported answer and confirmed exact reuse. The worker also mistyped an attempt ID during repair; correcting that input succeeded. The attempt had not been deleted.
- Two unsupported requests remained unapproved. Assistance for one produced counterevidence, which still could not establish the requested global deployment or reliability guarantee.

All eight final supported/scoped answers reused exactly. Initial repeats took 340–355 ms; the repaired answer reused in 350 ms with `resolution: agent-assisted`. These are exact scoped hits, not semantic matching. Six clean initial approvals plus one scoped answer and one lead-assisted repair must not be presented as eight unattended successes.

The skill now says to copy IDs from parsed outputs and obtain reviewed evidence for every added factual claim before approval. Evidence IDs prevent transcription errors; they do not prove semantic support. The same trusted agent still judges whether an answer is correct.

## Scope and evidence

Local raw artifacts: `.local/assisted-eval/` in the candidate worktree, including original setup failures, corrected results, approvals, repeats, root repair, full inventory, and independent grading. Public source identity/line references and questions are reproducible from the frozen commit; local paths and keys are not portable configuration.

The registered-public-repo coverage is not coverage of every private project archive, and it does not pre-cache every possible answer. Live installed checks and rollout receipts are separate from this pre-merge trial. The database retains compact original status/trace metadata; callers retain full raw evidence artifacts. Assistance remains opt-in and bounded, with explicit approval, original-source checks, freshness policies and trusted-local scope labels.
