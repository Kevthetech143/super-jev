# Changelog


## 1.0.135 — 2026-10-07

- The PR-merge check no longer blocks a true "PR N merged" because a same-numbered PR is open in a sibling repo. The evidence text carries no repo, so when it reports a mismatch the gate asks `gh pr view N --repo <repo>` and drops the block only if one says MERGED. The repos come from `SUPERJEV_GATE_PR_REPOS` (`keyword=owner/repo,keyword=owner/repo`); a keyword next to the number in the draft picks that repo; no keyword, or keywords for more than one repo, keeps the block (no gh call). Unset means no gh check and the old behaviour. A gh error or timeout (6 s or what is left of the Stop budget) keeps the block. Tests: `skills/super-jev/tests/test_superjev.py`.

- An ask's local work no longer grows with the number of connected pointers when one of them is stale or not indexed. Before, one such pointer made the engine snapshot every pointer (each re-read and re-hashed its manifest and re-checked every source: about 1.4-1.9 s with 165 pointers). Now the index rows serve the indexed pointers and only the fallback pointers are snapshotted (the engine's `panel` action takes an optional `names` list), and the engine remembers a manifest's sha by the file's stat key, like the source files. Tests: `skills/super-jev/tests/test_panel_flat.py`; `scripts/scale_harness.py --stale` reports snapshots per ask.

## Unreleased

## 1.0.134 — 2026-10-07

- A newly connected set is indexed at once instead of staying "not indexed" for 10 minutes or more (every ask on it re-read and re-scanned all its files meanwhile). A connect now counts a pointer the registry did not hold before the run as changed, so it starts the index updater despite the 10-minute stamp (before, only a refresh that changed a generation did); and an ask that finds a searched set "not indexed" starts it too, as a generation mismatch already did. A burst of connects folds into one extra pass through the existing lock and rerun marker. Both stand down for 2 minutes after a failed pass and stay off in a replay and under pytest; a plain re-connect of an unchanged pointer still goes through the throttle. Test: `tests/test_index_newpointer.py`.

## 1.0.133 — 2026-10-06

- **Honest outcome: the outcome comes from the sets, not from files.** A set that failed to search is `error` (3); a set that needs setup or a refresh, or nothing connected, is `needs-setup` (4); otherwise no match is `not-found` (1). A file no searched set checked (label failed, held for a secret, too big, edited and not yet re-admitted, not UTF-8) never changes the outcome: it is always named in `left_out` and in a `partial: N files not checked` note in the reason, and a file another connected set admitted is not counted as unchecked. Removed the held-file word-coverage and routing-pick heuristic that turned some held files into `needs-setup`. Tests: `tests/test_honest_outcome.py` (two tests of the removed heuristic retired).

- The checked connect no longer misses an unpaid judge call when the call's stdout ends without a newline: a stderr `HTTP 402` could glue onto the skipped `$ ` echo line and hide. Stdout and stderr are now joined with a newline. Test: `skills/super-jev/tests/test_connect_checked.py`.

- `prepare_bulk.py --json` no longer reports a payment refusal as "internal error: PaymentRequired" with a traceback; it prints the same single payment message as text mode, exit code unchanged. Test: `skills/super-jev/tests/test_json_unpaid.py`.

## 1.0.132 — 2026-10-06

- A refresh no longer stops with a false "payment required" when a file's description mentions `HTTP 402`. The checked connect searched all of the judge call's output for `HTTP 402`, and that output echoes the full command including every claim, so a normal verdict looked like a payment refusal. It now ignores the echoed command line and the verdict rows, which are the only places claim text appears. Test: `skills/super-jev/tests/test_connect_checked.py`.

- **Person folders work in any layout.** A person folder is any connected folder whose PROFILE file has a `Relation:` line (it was only one fixed path layout, so another layout had no people and "my dad" filtered nothing). The folder is the PROFILE's own one, or the one above it when the PROFILE's first heading names that one and not its own; only the PROFILE's first 80 lines are read. A PROFILE on disk beside connected files but not connected itself still counts (only its heading and Relation line are read, locally; only the folder name is stored, in index meta `person_profiles`). The index rewrites only the rows whose person moved (no version bump). Tests: `skills/super-jev/tests/test_kin_word.py`.

- A file the secret scan holds no longer leaves its words in the local stores: the word index entry keeps `"secret": true` and no words, and the pointer word list skips the file, so a key-shaped token from such a file is never written to `word-index.json` or `pointer-words.json`. Files written earlier are cleaned on first load with no re-read and no re-index: `pointer-words.json` entries saved before `WORDS_VERSION` 4 are dropped and the file rewritten, and `word-index.json` items flagged secret have their words emptied and the file saved once. The word index version stamp is unchanged, so an upgrade does not re-index every file. Search ranking is unchanged (a held file was already skipped). Tests: `tests/test_word_index.py`.

- A failure to remove `index-fail.stamp` after a successful index pass no longer turns that pass into an exception.

- The index's table-of-contents label row is built by one helper for seeding and for the label compare, so editing one alone cannot make every pass rewrite every file's row. Test: `tests/test_file_index.py`. The folder-link walk test now sets its own home folder, so it no longer depends on where the temp folder lives.

## 1.0.131 — 2026-10-06

- After a refresh bumps a pointer's generation, the index is re-seeded at once instead of waiting for the next throttled start (asks on that pointer took the slow path for roughly 17-25 minutes). An ask that finds a generation mismatch now starts the updater inside the 10-minute throttle, as a served-file sha mismatch already did; a `--refresh` that changed a pointer's generation (read from the registry before and after) starts it despite the stamp too (a plain re-connect still goes through the throttle), still skipped in a replay and under pytest. Both bypasses stand down for 2 minutes after an updater pass raised (`index-fail.stamp`; a successful pass removes it), so a failing pass is not respawned by every ask. The updater is still one per principal: a second one that finds the lock held touches `index-rerun.marker`, and the running one, when its pass ends, clears it and makes one more pass (at most one queued rerun, never a loop). The walk throttle is unchanged. Test: `tests/test_index_catchup.py`.

- A refresh that changes nothing no longer starts a new generation for its pointer. Every `register` minted a new generation, and a connect with `replace:true` always wrote a new prepared folder, so a refresh of a sharded set re-generationed every shard even when no file, description, label or view changed: each ask then fell back to the slow path for those sets ("generation mismatch") and the index updater purged and re-seeded them. Now a reconnect whose prepared bytes would be identical keeps its prepared folder, and a `register` with the same dataset, agents and snapshot fingerprint keeps the generation. Any change to a file, description (labels ride in it), view, navigation, dataset or agents still starts a new one. Saved answers and pending tickets are still cleared on every register, as before. The index now also refreshes a file's labels (description, question, kind, status, as_of, subject) from its prepare cache when only those changed, since no purge re-seeds them any more.

- A connect, refresh or heal scan no longer walks into a folder symlink that leads outside everything the user connected. The inventory walk (`walk_md`, shared by connect, refresh and the new-file scan, plus the index updater's and the ask-side coverage walks) followed every folder link and only guarded against loops, so a link to an unrelated folder (a test fixture holding `escape_link -> /tmp`) made every file there look new: the set re-refreshed about hourly with nothing changed, and every refresh walked that whole folder. A folder link is now followed when its target is under the connected folder, another connected root, an `--allow-target` folder or the user's home folder (installed skills are links into a release folder under home); a link to a temp folder, `/Applications` or another volume is skipped. Every skipped link is said: a `SKIP  N folder link(s) lead outside your home folder and were not looked at` line in connect and refresh, and a HELD line naming the link in a heal scan. The check compares file identity, so a link spelled in another case on a case-insensitive disk still counts as inside; links inside still work and a link loop still ends. The index and coverage walks now also follow a pointer's `--allow-target` folders, as connect does. A file symlink is unchanged (`inventory` already skips one whose target is outside every root and allow-target folder). Tests: `skills/super-jev/tests/test_prepare_bulk.py`, `test_ask.py`.

- The all-zero nil uuid (`00000000-0000-0000-0000-000000000000`) is no longer held as a card number. It is version 0, so the RFC uuid scrub missed it, and it is Luhn-valid; it is common in logs and JSON defaults. The shared `uuid` pattern in `secret_patterns.json` now also scrubs the exact nil uuid (same word boundaries, Python and Node), nothing else is loosened: a card in uuid shape that is not all zeros (`40000566-5566-0000-0000-000000000000`) is still held. Tests: `skills/super-jev/tests/test_uuid_not_card.py`, `test/secret-scan.test.ts`.

- A refresh where some files are held or scored under the line no longer marks the whole set failed. A file the judge scored low (a real verdict such as SUPPORTED 0.35 under the pass line) is now reported as held, so a run whose every other file is connected or held exits 3 (partial success), which auto-heal already settles as ok; before, each such file counted as a failure, the run exited 1, the set got retry backoff and stayed stale for its connected files. A judge call that fails (network, 5xx, unparseable output) still counts as a failure (exit 1), and a payment refusal (HTTP 402) still stops the run with nothing written. The text summary lists such a file as HELD with its rerun hint, and a held file that is then edited is picked up by the next refresh (an unchanged one is not retried). Test: `tests/test_partial_ok.py`.

- An edited held file now heals when its set is asked about. A file the judge held is no registered source, so its set never read stale and the edit was only picked up when the set refreshed for another reason. An ask now also checks the set's few held entries (a hash of each) and, when one was edited, starts the same bounded background heal (cooldown, hourly cap, reclaim stamp); the answer and exit code are unchanged and never wait. A held entry with no recorded hash is never counted as edited, so it cannot start a paid loop. Test: `tests/test_held_edit_heal.py`.

- A crashed table-of-contents (TOC) stage is retried once, and no longer ends as a clean not-found. A payment refusal (HTTP 402) or a size refusal is not retried. If the retry also fails and nothing is found, the outcome is `error` (the same exit 3 a failed content check already gets) with "not found, but the contents check failed (<error>), so this may be a miss; ask again"; if files are confirmed the answer is unchanged and the trace's `toc` entry carries the error, `retried` and a note. Test: `skills/super-jev/tests/test_toc_retry.py`.

- An ask no longer opens a set's prepare cache to see whether a held file was edited. The report now records the judged sha of each judge-held file (`heldSha`, written with the cache entry), and the ask checks those shas alone. A report written before this change falls back to the cache until its next refresh rewrites it. On a copy of the primary agent's 37 sets (27 with held rows, 32 judge-held files) the check took 36.7 ms per ask, now 10.3 ms (26 ms saved). Tests: `tests/test_held_edit_heal.py`, `tests/test_partial_ok.py`.

- A refresh no longer pays again for a held file that has not changed. A file the judge held (a real low verdict) is kept with its earlier verdict and the same report rows when its bytes match the cache, so it makes no writer or judge call; before, every refresh re-judged each one. An edited held file is still judged again. `--rejudge` (added to the rerun hint printed for a refresh) judges unchanged held files again, for a manual retry; a non-refresh connect also judges them again. The cache entry now also keeps the judge's reason. Estimate from a copy of the primary agent's caches: 21 of its 32 held files are unchanged, so about 21 to 42 judge calls (a low verdict gets one rewrite and a second check) and about 21 rewrite calls are saved per full refresh of its sets. Test: `tests/test_held_reuse.py`.

- A set is no longer left stale for good by files it never reviewed. The index updater marked a set stale when its folder walk found a new, unreviewed file (or one changed or went), and only a refresh that changed the set's generation cleared it; such a file is in no prepare cache, so neither the index nor the fallback path serves it, yet the set was searched by the slower fallback path on every ask. Now only a change to a file the set reviewed (edited, held, gone, or changed back) makes it stale, and a later pass with no such change and no edited or held file left clears the flag. A stale set that still has an edited or held file stays stale and still shows refresh-required. A served file that no longer matched at read time still keeps its set stale until a refresh.

- An ask decides once per file whether an edited file may be read at its current text. The check (secret scan, size ceiling, scope) ran up to three times per edited file per ask (TOC pick, edited count, word search); it is now remembered per file, bytes, set and review within one ask, and never carried into the next ask.

- The background index updater no longer re-walks every connected folder on every run. It walks the roots at most once per 10 minutes per principal (a stamp, `index-walk.stamp`) and otherwise only re-checks the files it already knows and the refreshed prepare-cache, which is where a refresh's changed files are listed; a set new to the index is always walked. One run on an unchanged large copy spent most of its time in that walk (8.8M stat calls). The updater that a connect or refresh starts now has the same 10-minute throttle as the one an ask starts, and is skipped in a replay. Tests: `tests/test_walk_throttle.py`.

- The index file gives back freed pages. A refresh drops and re-adds a set's rows, which left about a third of the file as free pages. At the end of an updater run, when over a fifth of the file is free, a one-time rebuild turns on incremental auto-vacuum and later runs return only the freed pages. Test: `tests/test_walk_throttle.py`.

- The index and today's search path now agree on a held file: one that still matches its review but whose text the current secret scan flags. Today's path searched its raw text by words and then held it when picked; the index never served it; and a held file could leave its set stale for good (on the slow path) or never stale (served without it), switching on each refresh. Now neither path searches such a file, both name it as held ("contains a secret; not sent"), and it never makes a set stale. The word index records the secret flag once per file version (its version is bumped, so it is rebuilt once). Held files are still never written to the full-text index.

## 1.0.130 — 2026-10-05

- A uuid is never read as a card number. About 1 random uuid4 in 5,000-8,000 has digit groups that form a Luhn-valid 16-digit number, so a uuid in a question, a uuid-named path or file content at prepare time was held at random (a log or JSON export with 500 uuids about 6-9% of the time). The card check now scrubs an RFC uuid (8-4-4-4-12 hex, version 1-8, variant 8-b; shared `uuid` pattern in `secret_patterns.json`, Python and Node) before testing, as it does dates and URLs: 0 of 300,000 random uuid4s held, was 56; 0 of 100,000 v7 (time-ordered) uuids. A card written in uuid shape without a uuid version or variant (`40000566-5566-5556-0000-000000000000`) is still held. Known limit: a card deliberately written in uuid shape with a valid version and variant (e.g. 8-4-4 digits followed by -8xxx-xxxxxxxxxxxx) is no longer held; cards written normally are. Tests: `skills/super-jev/tests/test_uuid_not_card.py`, `test/secret-scan.test.ts`.
- A background refresh no longer sends each file to the paid judge twice. When a combined content check (gate pack) fails it is retried as its two halves before any per-file call, and the per-file fallback sends the description and label claims in one call (11 files after a failed pack: 6 calls, was 24). A payment refusal (HTTP 402, an empty judge balance) stops the refresh at once with "top up, then refresh again": no split, no per-file retries, nothing written, so the set stays stale. Cause of the 2026-10-05 20:04 UTC pile-up: every judge call failed that hour (ledger exit 1 from 19:53 to 20:06, success from 20:07) while the balance was empty, and the per-file fallback then doubled each file. Test: `tests/test_gate_pack.py`.
- A queued heal is no longer orphaned. A drain that stops on a queued set's cooldown or the hourly cap leaves it pending, and an ask whose own set only cools down started no drain, so nothing healed it. Such an ask now starts one drain for the principal's queue when no lock holder is left to drain it, for sets that are due and within the hourly cap only; a drain that cannot start puts the set on its retry timer, and a principal is reclaimed at most once per retry interval, so a drain that dies is not respawned by every ask. Each reclaim writes one `action: "reclaim"` line to `autoheal.log`. Test: `tests/test_auto_heal_reclaim.py`.
- The test suite's session-end isolation check now compares the real `locks` and `autoheal-state` folders (names and mtimes) with a snapshot taken at session start, so a test that touches them fails the run even when the folders already exist; it only caught a newly created folder before.
- A saved answer or an assisted ask no longer fails at random with "memory request contains a secret; not sent". The engine's `ticket` and `attemptId` (uuid4) were scanned as user text, and about 1 uuid in 7,700 (387 of 3,000,000 measured) has digit groups that form a Luhn-valid 16-digit number; both keys now count as tool-built fields (`MACHINE_KEYS`, Python and Node). This was the occasional CI failure of the R6 test. Tests: `skills/super-jev/tests/test_hash_false_hold.py`, `test/secret-scan.test.ts`.
- Fewer judge calls per ask: the table-of-contents file pick and part pick size their batches by the judge's per-call budget (`call_tokens` from `judge_profiles.json`) and the pick pool (capped by `max_questions_per_call`), so a whole pick pool fits one call. Test: `tests/test_toc_search.py`.

## 1.0.129 — 2026-10-05

- An ask answers from a note's original, not from the redacted reviewed-view copy of it, when the asker can read that original through a raw (non-view) set: the view copy is dropped from the result and the original's own path is listed once. A view whose original is not connected for that asker still answers. Test: `tests/test_raw_over_view.py`.
- Heal state now survives a release. The auto-heal state (cooldowns, hourly limit, the per-principal scan lock, the per-set refresh locks and the log) lives in `<state root>/autoheal-state`, and the per-set prepare lock in `<state root>/locks`, where the state root is `SUPERJEV_STATE_DIR` or `~/.local/state/super-jev`. They were inside the versioned release folder, so after every release the cooldowns started empty (a rescan storm) and the old release's still-running scan ran beside the new release's for the same principal. The prepare cache itself does not move. On the first run the old folder's cooldown files are copied over once (never a lock).

## 1.0.128 — 2026-10-05

- A replay (`SUPERJEV_REPLAY=1`, the paid-replay and brains-check harness) no longer starts the detached index updater after an ask, so it cannot write into the replay's throwaway state while that folder is removed. Test: `tests/test_index_read_path.py`.
- Two refreshes of the same set no longer run at once: `prepare_bulk` takes a per-pointer lock in the prepare-cache and a second run for that pointer prints "refresh already running for <pointer>; skipping" and exits 0, so principals sharing a set cannot pile up refreshes; the skipping principal cools down as if it had refreshed. With `--json` the skip is one object, `{"skipped": true, "pointer": ..., "reason": "refresh-running"}`. The new-file scan also walks each report owner once per scan, not once per split part.

## 1.0.127 — 2026-10-05

- An ask never reconnects a stale set inline, so its time no longer grows with the number of stale sets (12 stale sets cost 139 s). A stale set is searched from its last prepared file list, or reported as refreshing; the heal starts in the background (`auto_heal.heal_in_background`, gated read-only before any spawn: no recipe, held and cooldown start nothing, the concurrency cap queues). Only a real start says "refreshing in the background; ask again in a minute"; a set with no recipe never claims a refresh.
- Release note: the squash commit `4fceab0` (#338, the no-inline-heal change above) carries a wrong title, "Replay never starts the detached index updater"; its content is the no-inline-heal change.
- File index build on a large connected folder: reads no longer fail while an update runs (the index uses WAL with a 2 s busy timeout, and a locked read says "index busy (update running)" and answers by the normal path; "index corrupt" is kept for a damaged file). Only one updater runs per principal (`index-update.lock`), the FTS pass commits every 200 files, and only the sets being rewritten fall back to the normal path during a build, not the whole principal.
- File index walk: cost follows the files, not the links (one parents walk per folder instead of one test per link per file), a root inside another root of the same round is cut from the outer walk, a set that shares a file with another no longer re-reads it each round, and `vbigram(word)` is indexed.
- File index walk: the updater applies the `--exclude` list connect recorded for a set, so an excluded folder is never walked or recorded.

## 1.0.126 — 2026-10-05

- File index and its word-search shortlist are now on by default. Set `SUPERJEV_INDEX=0` (or `off`/`false`, or `"indexRead": false` in the engine config) to turn them off.

## 1.0.125 — 2026-10-05

- File index (`SUPERJEV_INDEX`, still off by default): a file shared by two sets keeps both sets complete (#326).
- File index: a fallback file reuses the index's stored word items and table-of-contents pages when its sha matches, so it is not read and split again (#329).
- File index: the updater walks each root once per round instead of once per pointer (#330).

## 1.0.124 — 2026-10-04

- A file is hashed once per version: a stat-keyed memo shares one hash between the held-file check, the table-of-contents build and the engine snapshot, so repeated asks on a large set stop re-reading every file.
- A reviewed view's sources are read from the source's own path when `originalPath` is absent, so local rows are built for those views too.
- `prepare_bulk.py` saves drafts after every batch, resumes on retry, and falls back to the built-in writer for a batch whose reply is invalid JSON, so one bad batch no longer loses a large folder.
- The terminal app finds the saved key file by the same rule as the key provider (`judges.key_file_path`, honoring `<KEY_ENV>_FILE`).
- The prepare-cache follows `SUPERJEV_STATE_DIR`, including refresh and GitHub connect.

## 1.0.123 — 2026-10-04

- Jev calls on a plain question no longer grow with the number of connected sets. Routing never asks Jev about a set: every set is found through local rows (its own cache, its split parent's cache, or rows built from its reviewed sources for a reviewed view or a manual note, kept per generation in `set-rows.json`). Only a set with no local rows at all is still asked, in one batched call of at most 10 sets, named in the trace as `routing_fallback`. The claim path is unchanged. Test: `tests/test_routing_fixed_calls.py`.
- The secret scan holds more shapes: a password or PIN value after its label (also in a table), a seed phrase, a US Social Security number, and a card number split by spaces or dashes. One rule in `secret_patterns.json` is read everywhere a file's text is packaged (word search passages, table-of-contents parts, content check, claim check, saved answers), so every path holds the same lines. Short plain values, count-like values and placeholders stay unheld.
- `SUPERJEV_STATE_DIR` is honored by every state writer: `memory`, `prepare_bulk.py`, the file index, GitHub connect and pointer sharing resolve the state folder through one shared resolver, so a run with its own state folder never writes into the default one. Test: `tests/test_state_dir_isolation.py`.
- `auto_heal.py --scan` runs once per principal at a time: a second scan that finds the scan lock held exits 0 with a one-line note instead of starting a parallel refresh. The lock is released when the process ends, and `--uninstall` removes the lock file. Test: `tests/test_auto_heal_scan_single_flight.py`.
- One version source: `package.json`. `superjev --version` (now listed in `superjev --help`) and the new `ask.py --version` both print `Super Jev 1.0.123`.

## 1.0.122 — 2026-10-04

- Faster asks on large sets of files. A word's tokens are computed once per file version, not on every ask; the first panel fetched for a question is reused instead of fetched twice; an ask no longer takes a snapshot of pointers that have no saved answer; and the engine actions an ask needs run in the same process instead of a chain of four interpreters per call. Answers and rankings are unchanged.
- The word index drops the entries of a file that left its pointer, and its version stamp now covers the stop-word list and the tokenizer, so a change to either rebuilds it.
- A per-principal file index (a small sqlite file, updated by comparing file stats) records each file's reviewed hash and metadata. It is the base for the index read path below. The updater runs detached after an ask, and at connect and refresh.
- An optional index read path, off by default (`SUPERJEV_INDEX=1`, or `indexRead: true` in the engine config). When on, the pointer list, status and word-search and table-of-contents corpus come from the file index, only the files served are checked against their hash, and an ask falls back to the normal path with the reason in the trace. A second optional step shortlists passages through an FTS5 index so the local part of an ask stays flat as the number of files grows. Leave both off until the fallback for pointers that are not fully indexed lands.
- `skills/super-jev/scripts/scale_harness.py` builds a synthetic corpus of any size with a stub judge, to measure how an ask's local time grows with the number of files. It also runs on Python 3.10.

## 1.0.11 — 2026-09-24
- Picker noise: test/scratch output (`ops/sj*/` except `ops/sj-manual/`, `*superjev-test*`, `*-hand-test-*`) is skipped by connect and word search; word search drops already-routed files before taking its top 3, so no slot is wasted; the secret scan's password/api-key `:`/`=` value is held unless it starts with a pointer/placeholder word (in, see, none, vault, `<...>`, `${...}`...) (`PASSWORD: in other-file.md` is prose). (#153)
- Big notes: a file too long to read whole, routed >= 0.85 and judged on topic (>= 0.5), stays possible with a "start at section" pointer (an 80 KB pending.md was dropped at 0.57). It never outranks a file with a real content score; live-value asks unchanged. (#152)
- "What is on ... list/to-do/backlog" asks use the open "does it answer" wording, not exact-value. (#152)

## 1.0.10 — 2026-09-24
- Per-stage ask trace: each traces.jsonl line carries `stages` (cache, routing + none-probability, word-search top 10 with fates, read list, per-file chunks/wording/score, near-twin tie-break, final rule); `ask.py --trace-show <id|last>` prints it. No answer changes, no extra Jev calls. (#151)

## 1.0.9 — 2026-09-24
- #150 prepare_bulk: legacy-report refresh keeps its recorded file set.

## 1.0.8 — 2026-09-24
- #149 auto-cache gates on claim verdict; a confirmed README ranks as evidence.
- #148 refresh replays stored recipe; STALE falls through to live search; skills --request.

## 1.0.7 — 2026-09-24
- #144 `--approve` can pick any listed candidate (`--rank N` / `--file PATH`).
- #145 GitHub connector: export a repo's PR/issue/commit/release history and connect it.
- #138 prepare_bulk: a big folder refresh can pass --max-files on cache reuse.
- #137 circuit breaker no longer benches stale (preparation-required) pointers.
- #136 --followup requires the proposed file to match the question subject.
- #139 CI: HOL plugin scanner. #147 manuals for v1.0.7.

## 1.0.6 — 2026-09-24
- #126 live decision traces + Super Jev voice.
- #128 auto-heal stale pointers.
- #129 superjev terminal chat app.
- #130 v1.0.6 blocker fixes (symlink --add crash, secret-shaped filenames redacted/held, SUPERJEV_TRACES switch).
- #131 --followup miss queue.
- #132 audit-visibility.
- #133 failing-pointer circuit breaker.
- #134 near-twin tie-break.

## 1.0.5 — 2026-09-23
- Retrieval recall toward 80%: Improved on internal eval (details kept private).
- Review found and fixed 3 false-hit holes.

## 1.0.4 — 2026-09-23
- Better recall for casual questions: word search over each pointer's own cache (this principal's pointers only), long files are content-checked instead of kept on routing score, and near-misses print as "possible" (never for value questions, never approvable).
- Navigate runs at most 6 pointers at once (SUPERJEV_NAV_CONCURRENCY) with a 15s provider timeout (SUPERJEV_NAV_TIMEOUT_MS). Fixes most "Navigation provider timed out" misses.
- Bench (casual + no-answer questions): Improved on internal eval (details kept private). median 8.3s.

## 1.0.3 — 2026-09-23
- Refresh keeps every principal a pointer serves (`--principal` is repeatable), so shared pointers no longer fail with scope-change. `refresh_changed.py` lists pointers with no report as NEEDS MANUAL PREPARE instead of skipping them silently.

## 1.0.2 — 2026-09-23
- Secret scan: the bare word "password" in prose no longer holds a file; real password assignments still do. New `refresh_changed.py` re-prepares only pointers whose files changed.
- Auto-cache: `ask.py --answer "question" "answer"` saves an answer only when the check gate verdict is CLEAN against the last lookup's top file and that file is unchanged since connect; marked `approved_by: auto-check` with evidence file and score. On by default; `--no-auto` / `SUPERJEV_AUTO_CACHE=0` opts out. Cache hits print the approver. `--miss` on a cached question un-saves it (new memory action `forget`). `sources` rows now carry `originalPath`.

## 1.0.1 — 2026-09-22
- Connect no longer fails at random with "Request contains a secret; not sent": the secret scan (Python and Node) skips tool-made fields (sha256, ids, pointer names), and a card-number match must stand alone and pass the Luhn check, so hex hashes can't trip it. A held connect now names the file.
- README quick start and AGENTS.md show the exact `ask.py --approve "question" "answer"` usage.

## 1.0.0 — 2026-09-22
- Rounds 4-9 hardening: every outbound call (Python and Node) runs one shared secret scan before sending and refuses with "not sent"; Python and Node normalize Unicode the same way; uninstall deletes only files Super Jev recorded writing; `ask` content floor 0.85; explicit User-Agent (TypeSafe's edge blocks the default one).

### Earlier rc.1 self-boot fixes
- A fresh clone now sets itself up with no private files: `gate`/`check` and connect use the judge client shipped in `skills/super-jev/lib/jev_client.py` (needs only `TYPESAFE_API_KEY`); `SUPERJEV_GATE_CMD` still overrides it.
- The gate fails closed: a door that exits 0 without a readable, clean claim table is ERROR (exit 1) or READ (exit 3), never CLEAN. The READ line now says "(blocked)".
- `python3 skills/super-jev/setup.py` creates the state folder and memory config (idempotent); `--uninstall` removes state, `prepare-cache/`, `ledger/` and skill links.
- `prepare_bulk.py --writer builtin`: a no-model description writer, used automatically when no `claude` CLI is installed.
- `ask.py` says "nothing connected yet" / "not set up yet" instead of an empty result, and tells the agent not to guess on no-candidates.
- AGENTS.md opens with a numbered self-setup path; README quick start and GETTING-STARTED corrected.
- Round 2: `--uninstall` refuses a state folder without setup's `_memory/config.json` marker; connect holds binary/non-UTF-8 files and exits 1 on an empty folder or when every file failed (naming the cause); `check`, `ask` and connect name the real cause (missing key, HTTP 401, network) instead of a generic error; `ask` refuses questions over 8,000 characters and re-reads its top files so a topic-only match is dropped (one extra Jev call per uncached ask); the built-in writer quotes each file's first 60 words so routing sees the facts.
- Round 3: setup refuses a non-empty folder it did not make, and `--uninstall` deletes only Super Jev's own children (the folder only if then empty); `ask`'s content check secret-scans each file before sending it (HELD, not sent), asks the judge whether each file states the answer (not just the topic) and needs a content score of 0.60, so an absent fact no longer passes on some runs, never keeps a file it did not read, and treats an unfinished check as inconclusive; `--writer builtin` descriptions are checked locally (verdict QUOTED) so plain notes connect; the connect summary names each held/exception file, why, and the command to include it; a stale pointer's line names the `prepare_bulk.py --refresh` command.

## 1.0.0-rc.1 — 2026-09-21
- Clean cut for 1.0: experimental doors moved to `src/experimental/` and `docs/experimental/`; onboarding docs (`docs/GETTING-STARTED.md`, `docs/wire-into-claude-code.md`, `docs/KNOWN-QUIRKS.md`, `docs/AGENT-GUIDE.md`); new `skills/skill-search` for local-only skill discovery; community files (`SECURITY.md`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `LICENSE`, issue/PR templates); new README. Version `1.0.0-rc.1`.
- README: touches table, uninstall, doors, maintainer, platform.

## 0.2.0
- Connect workflow split into its own skill (`skills/super-jev-connect`); bulk prepare gains `--allow-held`; ask supports labeled manual entries and `--replace-entry`.

## 0.1.0
- First working cut: the Jev judge loop, skill search, file navigation, and the skill front door (`ask`).
