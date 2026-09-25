# Changelog

## Unreleased
- Word search scores each file by its best 3500-character passage instead of the whole file, so a long note (the 36 KB medical timeline) is no longer sunk by its unrelated visits; a written phone number (212-305-6390) counts as the words "phone" and "number"; a file under 0.55x the best not-yet-routed word score takes no read slot. Full eval (corrected key, 49 scored, same-day data): top-1 40 -> 41, right-confirmed 33 -> 35, wrong-confirmed 3 -> 2. Fixes "NYP ENT phone number" (hand test 6).
- Stale pointers: when every file of a stale pointer is already reviewed at its current bytes (auto-heal used to skip it as "no-change" forever, e.g. primary-reference), the ask reconnects it inline with `--no-findability` (no writer or Jev calls; one 45 s budget for all reconnects in a lookup, then left running in the background; no cooldown after a failed or timed-out reconnect) and reads it in the same lookup. Split parts (`<pointer>-N`) now heal through the parent's report instead of skipping as "no-report". Real changes keep the background refresh.
- Content check, spread answers: among files in the same tier, a file read as several passages ranks on its best passage plus half the on-topic probability the other passages took (`file_score`, SPREAD_CREDIT 0.5), so a note whose answer spans passages outranks a sibling with one slightly stronger passage. Tiers still come from the best single passage (possible >= 0.60, confirmed >= 0.85; blend capped at 0.84); live-value asks get no blend. No extra Jev calls.
- Routing cost: a pointer is not routed when a question of 2+ words has none of them (or a listed synonym: doctor/physician, dad/father, car/vehicle...) in any of its files. Never skipped: a pointer holding a resolved person's folder, or one whose files cannot be listed. Words are read locally via `sources` beside routing, once per pointer generation (`$STATE/pointer-words.json`); word search still searches every pointer. Trace stage `prefilter` lists skipped pointers.
- Full 60-question eval on top of #161 (corrected key, 49 scored): top-1 40 -> 42, right-confirmed 35 -> 35, wrong-confirmed 2 -> 2, absent handled 8/9 -> 8/9; Jev calls per ask 28.9 -> 26.0 (routing 21.3 -> 18.4).
- Source over copy: a README/INDEX/PROFILE hub yields to a note under its own folder that is confirmed or scored higher than the hub, a `_staging/` or `_TEMPLATE` copy yields to any note that passed the content check (both then list as possible), and a PR-history write-up yields to a confirmed doc. With nothing to yield to they keep their place (clov/README stays the dashboard). No extra Jev calls.
- Person resolution: "my dad"/"my mom"/a person's name/"I, me, my" resolves to `~/agents/global/documents/<name>/` folders ("my wife and I" = both; group words like parents/kids/family/our, or a relation no folder claims, filter nothing) (relation read from that folder's PROFILE `Relation:` line); other people's files are not read, not confirmed, and pointers holding only their files are not asked. No name resolved, nothing filtered.
- Full 60-question eval (corrected key, 49 scored): top-1 38 -> 41, right-confirmed 34 -> 34, wrong-confirmed 5 -> 2. `--trace-show` lists the person filter and every source-first move.
- Pick trail: `--used last --rank N --answer "..."` logs which listed file an agent used; every 5 picks the claim checker auto-saves CLEAN ones as `agent-pick+check`, unsure ones wait in `--pending-picks`. (#155)
- `--approve --rank N` / `--file PATH` saves evidence only from the picked file (matched by full path); a same-name file in the same tree (clov/analysis/README.md vs clov/README.md) can no longer supply the quote. If the search has no passage from the picked file, approve cites the file's own reviewed lines that best match the answer (assisted review, needs `allowAgentAssist`); the pick trail uses the same evidence path. The saved quote is the picked file's passage that best matches the answer, not its first section.
- Bench datasets under `.local/retrieval-datasets/` (e.g. health-selected-passages) are never connected by prepare_bulk and never listed in ask results (routing or word search).
- Stale pointer hint prints a command that runs as-is: absolute prepare_bulk.py path plus the pointer's recorded roots and every recorded principal (v1.0.12 printed a bare `--refresh` that was refused for want of `--root`). A refresh that names only some of a pointer's registered principals now reconnects with all of them instead of failing with scope-change and leaving the pointer STALE.
- No-candidates wording no longer claims absence: "Super Jev couldn't find it in the connected files. It may still exist" plus an offer to search by hand.
- Possible tier: a README with a real content score ranks above route-only files (was printed last below 0.60 route-only files), still below any note with a content score (#149). Other hubs still rank last; confirmed-tier rules unchanged. Replay of 558 fleet traces: 1 reorder, 0 top-file flips.

## 1.0.11 — 2026-09-24
- Picker noise: test/scratch output (`ops/sj*/` except `ops/sj-manual/`, `*superjev-test*`, `*-hand-test-*`) is skipped by connect and word search; word search drops already-routed files before taking its top 3, so no slot is wasted; the secret scan's password/api-key `:`/`=` value is held unless it starts with a pointer/placeholder word (in, see, none, vault, `<...>`, `${...}`...) (`PASSWORD: in other-file.md` is prose). (#153)
- Big notes: a file too long to read whole, routed >= 0.85 and judged on topic (>= 0.5), stays possible with a "start at section" pointer (health-fitness pending.md, 80 KB, was dropped at 0.57). It never outranks a file with a real content score; live-value asks unchanged. (#152)
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
- Retrieval recall toward 80%: businessfi bench top-3 50/60 (83%, was 46/60), top-1 36/60, false hits 1/15.
- Review found and fixed 3 false-hit holes.

## 1.0.4 — 2026-09-23
- Better recall for casual questions: word search over each pointer's own cache (this principal's pointers only), long files are content-checked instead of kept on routing score, and near-misses print as "possible" (never for value questions, never approvable).
- Navigate runs at most 6 pointers at once (SUPERJEV_NAV_CONCURRENCY) with a 15s provider timeout (SUPERJEV_NAV_TIMEOUT_MS). Fixes most "Navigation provider timed out" misses.
- Bench (businessfi, 60 casual + 15 no-answer): right file first 35/60 (was 25/60), top 3 45/60, false hits 2/15 (was 3/15), median 8.3s.

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
