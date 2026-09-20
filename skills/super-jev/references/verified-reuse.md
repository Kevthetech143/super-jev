# Verified reuse and assisted recovery

The local pointer/cache experiment reuses only explicitly approved answers. Its durable config names a SQLite `db` and reviewed-dataset `registry`; a pointer is a constrained registry binding, never an unrestricted filesystem path. Use `dispatch.py memory --describe` before the opt-in experiment and [the experiment README](../../../experiments/verified-pointer-memory/README.md) for inputs and setup.

## Normal reuse

Submit the registered pointer, original question, principal and relevant context. A fresh retrieval always receives a durable `attemptId`; retain its original raw result and status. A `ready` response has reviewed passages, each with an `evidenceId`, and an approval ticket. Verify a complete answer yourself, then explicitly approve it. The approval evidence list may cite returned passages as `{"evidenceId":"..."}` instead of copying hand quotes. This does not make approval automatic.

Cache metadata records whether a ticket/result was resolved by `retrieval` or `agent-assisted`; assisted results also retain `originatingAttemptId`. A cache hit is usable only in its exact question, principal, context, generation and freshness scope.

## Bounded agent-assisted recovery

This is for useful growth of verified memory, not instant coverage of every new question. It is disabled by default. A trusted operator may set `allowAgentAssist: true` in the persistent experiment config for an authorized normal-search recovery; a principal label is still a local scope label, not authentication.

For a partial, `no-match`, or `refused` original result, inspect it with `attempt(attemptId, principal)`. Inspect registered reviewed preparation with `sources(pointer, principal, offset?, limit?)`; its default limit is 25 and maximum is 100. Independently investigate only within that authorized registered dataset. Select concrete reviewed lines, then call:

```text
assist(attemptId, principal, reason,
       references:[{sourceId, startLine, endLine}])
```

Assistance accepts only registered, reviewed preparation. It rechecks principal scope, pointer generation, source hashes and freshness, and returns a new `ready` ticket for explicit agent review. It neither retrieves raw files nor approves/trains on an answer. Verify the full answer and approve returned `evidenceId` values only when they support it. Absent or conflicting sources remain unresolved.

Use at most one assistance attempt per question. Then record the unresolved result unless there is a concrete, correctable input issue. An unregistered source needs reviewed onboarding, refresh and a new original attempt; it never bypasses the registry. The database keeps original attempt status/trace metadata across pointer replacement/removal; keep full raw responses and resolution receipts in your private work artifacts. Approved resolution metadata stays with the cache entry, which invalidation can remove. Old-generation attempts remain in the database for trusted audit but cannot be read through a newly registered pointer.

`refresh-required`, `preparation-required`, access denial, stale bindings and expired tickets stop the loop. Refresh/review through the ordinary lifecycle and submit a new attempt when appropriate. Never substitute ordinary search, another model, a different person's scope, or a new raw path as hidden recovery.
