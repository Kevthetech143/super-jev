# Verified reuse and assisted recovery

The local pointer/cache experiment reuses only explicitly approved answers. Its durable config names a SQLite `db` and reviewed-dataset `registry`; a pointer is a constrained registry binding, never an unrestricted filesystem path. Use `dispatch.py memory --describe` before the opt-in experiment and [the experiment README](../../../experiments/verified-pointer-memory/README.md) for inputs and setup.

## Normal reuse

Submit the registered pointer, original question, principal and relevant context. A fresh retrieval always receives a durable `attemptId`; retain its original raw result and status. A `ready` response has reviewed passages, each with an `evidenceId`, and an approval ticket. Verify a complete answer yourself, then explicitly approve it. The approval evidence list may cite returned passages as `{"evidenceId":"..."}` instead of copying hand quotes. This does not make approval automatic. Copy tickets, attempt IDs and evidence IDs directly from parsed responses; never reconstruct them by hand.

Cache metadata records whether a ticket/result was resolved by `retrieval` or `agent-assisted`; assisted results also retain `originatingAttemptId`. A cache hit is usable only in its exact question, principal, context, generation and freshness scope.

## Bounded agent-assisted recovery

This is for useful growth of verified memory, not instant coverage of every new question. Available on every install; a principal label is still a local scope label, not authentication.

For a partial, `no-match`, or `refused` original result, inspect it with `attempt(attemptId, principal)`. Inspect registered reviewed preparation with `sources(pointer, principal, offset?, limit?)`; its default limit is 25 and maximum is 100. Independently investigate only within that authorized registered dataset. Select concrete reviewed lines, then call:

```text
assist(attemptId, principal, reason,
       references:[{sourceId, startLine, endLine}])
```

Assistance accepts only registered, reviewed preparation. It rechecks principal scope, pointer generation, source hashes and freshness, and returns a new `ready` ticket for explicit agent review. It neither retrieves raw files nor approves/trains on an answer. Verify every factual claim in the full answer and approve returned `evidenceId` values only when they support it. If original-source inspection reveals an extra fact absent from the returned passages, bring its reviewed lines into an assistance ticket before approving an answer that includes it. Absent or conflicting sources remain unresolved.

When the file is already known (ask.py ranked it, or the lead picked it), ask.py skips retrieval: the `open` action (`pointer`, `question`, `principal`) records an attempt with status `caller-ranked`, and `assist` cites that file's own reviewed lines. The evidence therefore comes from the same file ask() chose, never from the separate description-only search.

Use at most one assistance attempt per question. Then record the unresolved result unless there is a concrete, correctable input issue. An unregistered source needs reviewed onboarding, refresh and a new original attempt; it never bypasses the registry. The database keeps original attempt status/trace metadata across pointer replacement/removal; keep full raw responses and resolution receipts in your private work artifacts. Approved resolution metadata stays with the cache entry, which invalidation can remove. Old-generation attempts remain in the database for trusted audit but cannot be read through a newly registered pointer.

`refresh-required`, `preparation-required`, access denial, stale bindings and expired tickets stop the loop. Refresh/review through the ordinary lifecycle and submit a new attempt when appropriate. Never substitute ordinary search, another model, a different person's scope, or a new raw path as hidden recovery.

## Repeat questions (auto-save)

One rule saves a file-backed answer. When the same file wins the same question for the same principal N times in a row (the same words after lowercasing, collapsing spaces and dropping trailing punctuation; `SUPERJEV_SAVE_AFTER`, default 2) and passes the content check, `ask.py` saves it on that ask: it saves the whole ranked list of that search (up to 5 files, each with pointer and content hash, plus question, principal, date) and no answer text, so a right file that ranked 2-5 is still shown. The N wins are the evidence (each already passed the content check), so there is no extra claim check and no extra Jev call; the secret scan and unchanged-file check still run. Recorded as `approved_by: auto-save`. The next ask prints all the saved files in rank order at once (OUTCOME counts them), labelled `saved answer, from FILE, saved DATE`, with no Jev call and no search; you open the files and answer from them. The skill suggestions and the "Jev leans none" note of that search are saved and printed too, so a hit equals the live result. A search with a possible-tier or unfinished file in its top 5 records no win. If any listed file changed or cannot be read, or its pointer is no longer connected for the principal, the saved answer is withheld as STALE and the ask searches live.

Wins are read off `$STATE/lookups.jsonl` (each ordinary ask logs its confirmed top file and that file's hash); there is no separate store. Only an ordinary ask counts: not `--claim`, not a possible-tier file, not a replay, and only a complete search (an ask that says `partial: N sets not searched` records no win and does not reset the count, since a better file may sit in the unsearched set). A different winner or a `--miss` starts the count over; a file the content check rejects never wins, so it is never saved.

Guards that stay: a saved answer whose source file changed (or cannot be read) is withheld as STALE and the question is searched live, and the file can win its way back in; the answer never replaces opening the file; `--miss` removes it; another principal never sees it (saved answers are per principal). `--approve` is the manual way to meet the threshold at once; `--add` saves a fact that has no file. `SUPERJEV_AUTO_CACHE=0` or `--no-auto` turns auto-save off. `ask.py --principal P --trace-report --days 7` prints the weekly count of searches skipped by saved answers.
