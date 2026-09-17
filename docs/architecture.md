# Architecture and extension notes

The domain owns meaning; the loop owns ordering and execution boundaries. An evaluator is replaceable: live Jev, a test fixture, or a separately implemented model adapter can satisfy the same contract.

Each iteration refreshes evidence, obtains one validated batch of judgments, applies the domain decision function, checks the registered tool and policy, persists action intent, executes, records the result, updates state, and verifies completion. Verification can also complete a task before any inference.

Task state and observed context are separate. State retains workflow history; `observe` should provide the selected current evidence and whichever state fields matter for the next judgment. Jev has no implicit cross-request memory in this design.

Tools are the only intended effect boundary. Do not hide effects inside `observe`, `questions`, `decide`, `permit`, or `verify`. Keep `reduce` synchronous and deterministic. An idempotency key identifies a specific action attempt; an external system must actually implement deduplication for it to help.

For live signals, use a separate feed consumer that maintains a timestamped snapshot. Let `observe` read that snapshot; reject stale data in domain policy. This V1 provides hooks, not the feed connector, scheduling, clock synchronization, order reconciliation, or latency guarantees.

The default guardrails are deliberately modest: step count, total run timeout, request bytes, cumulative repeats of an identical serialized action, known tools, and domain permissions. Action repeat detection uses JSON serialization; domain packs should construct arguments in a stable order. Costs are visible through returned token usage when provided, but no monetary cap is implemented.

Trace inspection is safe to rerun because it does not call any tools. Process resumption is deliberately separate: loading a prior state without reconciling incomplete effects can duplicate actions. A future durable runner needs explicit checkpoints, leases, persistent deduplication, and reconciliation hooks.

Build domain evaluations against external ground truth. Adjust questions and thresholds using held-out examples; self-agreement alone does not measure improvement.
