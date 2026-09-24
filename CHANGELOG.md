# Changelog

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
