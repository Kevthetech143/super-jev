# super-jev wishlist

Ten items, no filler. super-jev is a Jev-tuned harness: one judge behind one
adapter, thresholds measured against Jev. Each module (batching, named
records, coverage, evidence chains, outcomes, cost, sweep) stands alone and
can be replaced or left out. A different judge is possible but means
re-measuring every threshold with the bench.

Test of admission: generic (works for any user of Jev, not just one fleet),
and there is a real failure it closes or a measurement that shows it works.
Measured figures live in the live bench output, not in this list — the
provider's preview terms restrict publishing performance numbers here.

1. **CLAIM GATE** — any text checked against its evidence before it is acted
   on or sent.
   Closes: an agent confidently stating what the documents do not say — a
   planted lie in a report caught on review, a clear overclaim flagged.
   Harness: a `gate` step in the loop: input is evidence and a draft, output
   is a per-claim verdict, exit CLEAN/READ/REJECT. Exists today as a claim-gate
   script; needs to live in super-jev as a step anyone can wire.
   Status: LIVE, wrapped by the `gate` door in this skill.

2. **REPORT VERIFICATION** — an agent says "done"; the harness proves it from
   free evidence before anyone trusts it.
   Closes: the trust cost of every handoff, where a human otherwise re-checks
   every report by hand.
   Harness: evidence collectors (git status/log/diff stat, a listing of every
   claimed path, test output, PR state) feeding the claim gate. Being built as
   a separate report-verification tool; then ported into super-jev as
   `verify(report, collectors[])`.
   Status: LIVE, wrapped by the `verify` door in this skill.

3. **PILE SWEEP** — a dataset bigger than one call, processed in full, with
   proof that nothing was skipped.
   Closes: the context-window ceiling and the silent-miss failure. Jev can
   give opposite answers across a chunk boundary in testing, so the harness
   must own the plan, the coverage manifest, and the merge.
   Harness: budget batching and a coverage manifest are merged. Missing: the
   two benchmarks the robustness report demanded — facts split across
   chunks, and duplicated or superseded records.
   Status: LIVE, wrapped by the `sweep` door in this skill (`npm run sweep`
   in this repo).

4. **EVIDENCE CHAIN** — ticket to order to policy, with completeness checked
   in code before the final question.
   Closes: a case scored mostly correct even with the right evidence in hand,
   because a missing required document was never noticed.
   Harness: a required-source spec and bounded traversal are merged, and the
   `chain` door runs them (`npm run chain` in this repo). Missing: a live
   run, and the "insufficient evidence" outcome wired to block action in
   the loop.
   Status: BUILT, wrapped by the `chain` door in this skill.

5. **ACTION PERMIT** — before the harness clicks, pays, sends or deletes, Jev
   answers "safe to do automatically?" and the code decides.
   Closes: an automated agent taking an irreversible step it should not have.
   Harness: a `permit` step taking the intended action plus context; anything
   under the threshold or needing approval goes to a human. Fits the existing
   "permitted actions" stage of the loop. A hard rule in code, not in the
   prompt, refuses destructive patterns outright and sends routine
   irreversible ones to a human before any model call; `format` counts as
   destructive only with a storage target (disk, drive, volume, partition,
   SD card, USB, mkfs, diskutil erase), so formatting code is not refused.
   Status: BUILT, wrapped by the `permit` door in this skill (`npm run
   permit` in this repo).

6. **FETCH LAYER** — pull, not push. Each turn Jev scores the whole catalog
   (skills, tools, knowledge, notes) against the request in one parallel
   call, and only the top few are loaded, instead of injecting the full maps
   every turn.
   Closes: the largest standing cost in a long-running agent's prompt, and
   the case where the right skill never surfaces because its description
   uses different words than the request.
   Gate to build: a bench of real past requests labelled with the tool that
   actually ran, with descriptions written closer to how a real user asks;
   the fetch layer must beat the injected map on both accuracy and tokens per
   turn.
   Status: BUILT, EXPERIMENTAL (v2) — wrapped by the `fetch` door in this
   skill (`npm run fetch` in this repo); the admission gate is not yet met.
   Built on the same `planSweep` / `runSweep` engine `sweep` uses. v2 adds
   a catalog schema (`src/enhance/catalog.ts`) drawn from the tool/skill
   retrieval literature (ToolRet, SkillRet, aurelio-labs/semantic-router's
   `Route(utterances=[...])`):
   a v1 record is `{id,text}`; a v2 record adds optional `utterances`
   (realistic user phrasings), `negatives` (near-miss phrasings that should
   NOT route here) and `tags`. The local narrowing pass (BM25-lite,
   `--prefilter`, default 8, 0 disables) scores `text` and `utterances`
   (utterances weighted higher), subtracts `negatives`, and folds recent
   turns (`--context`, lower weight) into the query so a referent like
   "restart it" inherits its subject — a v1-only catalog scores exactly as
   before. One relevance question per kept record, the request folded into
   its instructions, scored `high`/`medium`/`low`/`none` where `none` is
   "none of these". A record ranks only if it beats `none`. A **none gate**
   (`applyNoneGate`, `--floor`, default 0.60, and `--margin`, default 0.10)
   then asks a clarifying question — `{noMatch: true, candidates, ask}` —
   instead of acting, whenever nothing beat `none`, the top pick's
   confidence is below the floor, or the gap between the top pick and the
   runner-up is below the margin — a confident-looking top-1 with an
   almost-as-confident runner-up is still a guess. An offline replay of
   saved live fetch rankings (described in words, not benchmarked numbers)
   found the floor+margin pair served more correct top picks with fewer
   wrong serves than the old floor-only 0.80 rule, which is still reachable
   with `--floor 0.80 --margin 0`. A **feedback loop** (`--record`/`--ledger` on `fetch`, `catalog --
   learn`) mines corrected picks into proposed new utterances, written to a
   separate file, never silently merged into the catalog.
   The gate above — a bench of real past requests beating the injected map
   on accuracy and tokens per turn — is NOT yet met: only an offline
   admission bench has run so far (`npm run bench:fetch` against
   `bench/fetch-cases.json`, a synthetic fixture scored by a scripted stub
   that is told the answer ahead of time, over a seeded train/holdout split
   so a case's own text is never one of the utterances fed to its own
   narrowing pass). That proves the narrowing pass carries the true record
   through to the judge, the ranking, the k cap, the none gate and the
   coverage manifest work; it is not evidence of real judge accuracy or a
   real token saving. A live measurement against `bench/live-measure.ts`
   still needs to run before this item's own gate is closed.

7. **STEERING CABIN** — one front door for every check: gate, verify, sweep,
   fetch, permit, chain, and bench live; a door that is ever missing names
   its own wishlist item instead of failing silently; `ask "<one sentence>"`
   routes a plain request to the right door with no model call.
   Closes: checks that used to be scattered across several places, so a
   fresh session had to remember where each one lived.
   Later, if shelling out becomes the friction: the same subcommands exposed
   as a stateless tool list. Decision: skill first, a service layer only when
   earned.
   Status: LIVE — this is the `skills/super-jev` skill in this repo.

Proof for all seven: the live bench (`bench/live-measure.ts`) reports
accuracy, accepted-error rate, review rate, coverage, tokens and latency
together. A feature ships only once measured, and the numbers live in the
bench output, not in this document.

8. **AUTO-CATCH** — a verified hit is kept for reuse without the agent
   remembering to approve it, on any model, with or without harness hooks.
   Closes: the approved-answer cache staying empty because approval is a
   manual command nobody runs; the same lookup paid for again every day.
   Harness: `ask` leaves a receipt (question, attempt, top files); `--done
   "<answer>"` picks the opened evidence file, runs the claim gate on the
   cited sentences, and approves the FILE'S QUOTES (never the agent's prose)
   into both the exact lane and the searchable lane only when SUPPORTED at
   the line, honest, not time-sensitive, and the top file clearly beats the
   second. One config switch, default off; the Claude Code Stop hook is an
   optional shortcut that calls the same `--done`. Every decision logged.
   Status: PLANNED — build brief and tests in progress (feat/auto-catch).

9. **RECIPES** — cache the HOW, not the answer: a live question ("any Back
   Market orders today?") resolves to a registered read-only script the
   agent already found once, and the harness runs it and returns fresh data.
   Closes: agents re-hunting the same API or command every time a live
   question repeats; the cache being useless for anything that changes.
   Harness: a manual entry whose answer is `RECIPE: <path> sha256=<h>` with
   the script as `--source`; `ask --run` executes it only when the path is
   inside one allowlisted recipes folder, the hash still matches, argv is a
   list (no shell), and the output is non-empty with exit 0 — otherwise it
   hands the recipe to the agent instead. Read-only recipes only in v1; per
   principal; `autoRun` switch default off; every run logged. Ties to item 5
   (ACTION PERMIT) the day a recipe is allowed to change anything. Meant to
   grow slowly: one recipe per proven lookup, never a generic executor.
   Status: PROTOTYPE PASSED 2026-09-21 — exact hit ran live, a paraphrase
   found and ran it via the search lane, a tampered script was withheld as
   STALE by the existing source-hash check. Build after item 8.

10. **BROWSER DRIVER** — Jev decides the next browser action; a long-lived
    Playwright process does the typing. Cache the HOW of a login as a recipe.
    Closes: agents burning a full LLM turn per click, and the 1 s-per-command
    cost of spawning `playwright-cli` 6-8 times per step (measured: a 2-pick
    Jev login took 33.6 s wall, 1.3 s of it Jev).
    Harness: (a) code enumerates 2-14 candidate actions from the accessibility
    snapshot (click/fill/DONE); (b) Jev picks one as a Choice question;
    (c) a code permit gate runs before any action — deny list, per-site allow
    list, confidence floor, credentials filled from the vault and never sent to
    Jev, credential guard on every payload; (d) goal check + extraction are JS
    predicates owned by code; (e) `actions.jsonl` stays the audit trail.
    Speed: one long-lived browser process with the same persistent profiles
    (steps become function calls), batching as the interim step; after one
    Jev-approved login, replay it as a recipe (item 9) and call Jev only when
    the page differs. Headless is a flag, not the fix.
    Status: PROVEN 2026-09-21 — click bench 5/5 at 320 ms median vs a full
    LLM turn at 5.7 s; full Back Market SSC login from logged-out with 2 Jev
    picks (0.92, 0.99), goal check passed, case list reached; credential guard
    refused a poisoned payload. Prototype: primary-brain/ops/jev-click-bench-
    20260921/ (jevdrive.py). Build window: usage resets Thu 2026-09-24; target
    fixed and set up by Fri 2026-09-25. Owner: Primary. Basis note from
    businessfi: businessfi-brain/ops/jev-click-decider-basis-20260921.md.

