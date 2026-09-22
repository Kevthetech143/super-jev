# Changelog

## Unreleased (rc.1 self-boot fixes)
- A fresh clone now sets itself up with no private files: `gate`/`check` and connect use the judge client shipped in `skills/super-jev/lib/jev_client.py` (needs only `TYPESAFE_API_KEY`); `SUPERJEV_GATE_CMD` still overrides it.
- The gate fails closed: a door that exits 0 without a readable, clean claim table is ERROR (exit 1) or READ (exit 3), never CLEAN. The READ line now says "(blocked)".
- `python3 skills/super-jev/setup.py` creates the state folder and memory config (idempotent); `--uninstall` removes state, `prepare-cache/`, `ledger/` and skill links.
- `prepare_bulk.py --writer builtin`: a no-model description writer, used automatically when no `claude` CLI is installed.
- `ask.py` says "nothing connected yet" / "not set up yet" instead of an empty result, and tells the agent not to guess on no-candidates.
- AGENTS.md opens with a numbered self-setup path; README quick start and GETTING-STARTED corrected.
- Round 2: `--uninstall` refuses a state folder without setup's `_memory/config.json` marker; connect holds binary/non-UTF-8 files and exits 1 on an empty folder or when every file failed (naming the cause); `check`, `ask` and connect name the real cause (missing key, HTTP 401, network) instead of a generic error; `ask` refuses questions over 8,000 characters and re-reads its top files so a topic-only match is dropped (one extra Jev call per uncached ask); the built-in writer quotes each file's first 60 words so routing sees the facts.
- Round 3: setup refuses a non-empty folder it did not make, and `--uninstall` deletes only Super Jev's own children (the folder only if then empty); `ask`'s content check secret-scans each file before sending it (HELD, not sent), needs a content score of 0.80 (an absent fact's near-tie with "none" no longer passes), never keeps a file it did not read, and treats an unfinished check as inconclusive; `--writer builtin` descriptions are checked locally (verdict QUOTED) so plain notes connect; the connect summary names each held/exception file, why, and the command to include it; a stale pointer's line names the `prepare_bulk.py --refresh` command.

## 1.0.0-rc.1 — 2026-09-21
- Clean cut for 1.0: experimental doors moved to `src/experimental/` and `docs/experimental/`; onboarding docs (`docs/GETTING-STARTED.md`, `docs/wire-into-claude-code.md`, `docs/KNOWN-QUIRKS.md`, `docs/AGENT-GUIDE.md`); new `skills/skill-search` for local-only skill discovery; community files (`SECURITY.md`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `LICENSE`, issue/PR templates); new README. Version `1.0.0-rc.1`.
- README: touches table, uninstall, doors, maintainer, platform.

## 0.2.0
- Connect workflow split into its own skill (`skills/super-jev-connect`); bulk prepare gains `--allow-held`; ask supports labeled manual entries and `--replace-entry`.

## 0.1.0
- First working cut: the Jev judge loop, skill search, file navigation, and the skill front door (`ask`).
