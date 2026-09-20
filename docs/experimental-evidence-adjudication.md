# Experimental evidence adjudication (opt-in)

This is a small post-retrieval experiment. It does not change retrieval,
production defaults, caches, or approved answers. A caller supplies a fixed
evidence pool and explicitly invokes:

```sh
npm run evidence:adjudicate -- --input /absolute/path/to/pool.json
# or: ... --input - < pool.json
```

The JSON input is:

```json
{
  "question": "What action was recommended, and what completed result supports it?",
  "requirements": [
    {"id":"recommendation", "description":"the action explicitly recommended"},
    {"id":"completed-result", "description":"the completed result supporting it"}
  ],
  "passages": [
    {"sourceId":"source-a:L10-18", "text":"Full caller-reviewed surrounding passage...", "contentSHA":"optional SHA-256 of exactly this supplied text"}
  ],
  "candidates": [
    {"id":"pair-1", "requirementIds":["recommendation","completed-result"],
     "quotes":[{"sourceId":"source-a:L10-18","quote":"Exact text copied from the passage"}]}
  ]
}
```

`requirements` is a generic, per-request decomposition supplied by the caller;
the output labels its origin `caller_supplied`. The tool has no domain-specific
predicates. Passage text and requirements are trust inputs: the caller must
review them for provider safety, completeness, and correct source boundaries.
The tool checks locally that every quote is an exact substring of its named
passage and rejects stale or unknown pointers before a provider call. Optional
`contentSHA` binds the submitted text; it does not verify an external file.

Jev first receives the original question, all surrounding passages, requirements,
and proposed evidence in one bounded call. Each proposal is judged as support,
conflict, or insufficient while explicitly distinguishing questions, plans,
orders, recommendations, and completed results. The result returns only
passing evidence in `supportingEvidence`; conflicts retain their quote and
source attribution in `conflicts` and are not resolved. Exact duplicate
candidates are removed locally; no ranking guess drops candidates. If support
survives, a second bounded call judges whole-question sufficiency using only
that kept support. Status is `complete` only when passing evidence covers every
caller requirement and the second judgment says the original question is fully
answered, `partial` for some,
`insufficient` for none, and `conflicting` whenever a relevant conflict is
reported. Missing or malformed answers use the distinct `error` status; a
below-threshold answer is recorded as uncertainty rather than support. Provider
failures also use `error`. `calls` and token `usage` make evaluator attempts
visible; unavailable usage is `null` rather than
an invented zero. Choice confidence must also meet the configurable threshold
(default `0.80`), but confidence never overrides the semantic verdict.

Exact quote binding and coverage are deterministic checks. Semantic judgments
are Jev outputs, not correctness guarantees, and confidence alone never changes
a status. Tests use mocked responses to verify this protocol; they do not
measure model accuracy. Inputs are bounded at 128 candidates and 200,000 JSON
characters and are rejected rather than truncated. For a fixed health pool,
keep the input in an ignored private path such as `.local/evidence-health/pool.json`.
The CLI requires `SUPERJEV_JUDGE_RUNS` to be unset or `1`, and temperature/seed
overrides to be unset, preventing Jev multi-runs and pin-fallback retries. One
CLI invocation therefore makes at most two physical calls: candidate filtering,
then post-filter whole-question sufficiency. Library callers receive the number
of `Evaluator.evaluate` attempts; custom evaluators may implement other transport
behavior.
