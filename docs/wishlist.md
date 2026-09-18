# super-jev wishlist

Seven items, no filler. super-jev is a Jev-tuned harness: one judge behind one
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
   (`applyNoneGate`, `--floor`, default 0.80) then asks a clarifying
   question — `{noMatch: true, candidates, ask}` — instead of acting,
   whenever nothing beat `none` or the top pick's confidence is below the
   floor. A **feedback loop** (`--record`/`--ledger` on `fetch`, `catalog --
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
