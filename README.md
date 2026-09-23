# Super Jev

[![Tests](https://github.com/Kevthetech143/super-jev/actions/workflows/test.yml/badge.svg)](https://github.com/Kevthetech143/super-jev/actions/workflows/test.yml)

One judge (Jev) between your agent and your data: the agent asks in its own words, Super Jev finds the file, checks the claim, permits the action, and remembers what you approved. Your agent stays responsible for the answer.

## Vision

- One judge sits between your agent and your data: the agent asks in its own words, Jev finds the file, checks the claim, permits the action, and remembers what you approved. Your agent stays responsible for the answer.
- Agents burn whole LLM turns on lookups, re-hunt the same answers daily, and state things the files never said. Jev is a fast, cheap judge for yes/no and which-one questions; the LLM keeps the writing.
- The daily loop: ask → read the top file → answer → approve / miss / add. Connectors are how your data gets in; the cache fills only from your own approvals — nothing is cached that you did not approve.
- 1.0 promises the proven core: skill search, file navigate, connect + bulk prepare, check gate, permit gate, the harness loop. The core works well when it works; edges still want an agent in the seat — see KNOWN-QUIRKS.md and AGENT-GUIDE.md.
- 1.0 does not promise unattended answering, semantic cache matching, automatic sync, or live browsing. Next: auto-catch, recipes, a Jev-decided browser driver — each ships only after its own live bench.

## Quick start

**Agents: read [AGENTS.md](AGENTS.md) first.** It is a numbered path from a
fresh clone to a working ask and check, about 10 minutes with a TypeSafe key.

```bash
export TYPESAFE_API_KEY="$(cat /path/to/typesafe-key-file)"   # needed for connect, ask and check
python3 skills/super-jev/setup.py                              # safe to rerun; prints the next step
python3 skills/super-jev/prepare_bulk.py --root /path/to/folder --pointer my-notes --principal me --writer builtin
python3 skills/super-jev/ask.py --principal me "your question"
python3 skills/super-jev/ask.py --principal me --approve "your question" "the answer you gave"   # after a good hit
python3 skills/super-jev/dispatch.py check --claim "a claim" /path/to/file-you-read.md
```

No key yet? `npm run demo` runs the loop once offline. The test suites also
need no key: `npm test` (under a minute) and
`python3 -m pytest skills/super-jev/tests -q` (about 4 to 6 minutes).

Next: [GETTING-STARTED](docs/GETTING-STARTED.md) — the full operating manual
(prerequisites → key → setup → connect → ask → approve/miss/add → check → refresh → uninstall).
For agents: [wire-into-claude-code](docs/wire-into-claude-code.md) (the retrieval rule card
and Stop-hook claim gate), [AGENT-GUIDE](docs/AGENT-GUIDE.md) (the daily loop), and
[KNOWN-QUIRKS](docs/KNOWN-QUIRKS.md) (what breaks and the workaround).

## The loop

`ask` → read the top file yourself → answer → `--answer "question" "your answer"` so a good answer saves itself → `--miss` a bad one, `--add` a fact that has no file.

`--answer` is auto-cache: it runs the check gate on your answer against the top file `ask` returned, and saves it (like `--approve`, marked `approved_by: auto-check` with the evidence file and score) only when the verdict is CLEAN and the file is unchanged since connect. READ, blocked, stale or secret-held answers are not saved, and it says why. On by default; opt out with `--no-auto` or `SUPERJEV_AUTO_CACHE=0`. Human `--approve` still works (`approved_by: human`). Every cache hit prints who approved it, and `--miss` on a cached question un-saves it. Matching is exact wording only. Connectors are how data gets in: onboard a folder once, and it stays answerable.

## Maturity

| Door | Status |
| --- | --- |
| ask (the daily cycle) | LIVE |
| skill search | LIVE |
| file navigate | LIVE |
| connect + bulk prepare | LIVE |
| check gate | LIVE |
| permit gate | LIVE |
| harness loop (demo / replay) | LIVE |
| sweep | MEASURED — has live bench scripts |
| catalog | MEASURED — has live bench scripts |
| fetch | MEASURED — bench scripts exist; admission gate not yet met (see AGENTS.md) |
| chain-cli | EXPERIMENTAL — see src/experimental/ |
| derive-facts | EXPERIMENTAL — see src/experimental/ |
| investigate | EXPERIMENTAL — see src/experimental/ |
| passage-level search | EXPERIMENTAL |
| saved-answer approve reuse | EXPERIMENTAL |
| verify | EXPERIMENTAL |
| auto-catch | EXPERIMENTAL — 1.1 track |

LIVE = proven in daily use. MEASURED = exercised by live bench scripts. EXPERIMENTAL = moved aside; not deleted, not shipped.

## What 1.0 does not do

- Answer unattended — an agent stays in the seat for the final call.
- Match paraphrases from cache — cache hits are exact wording only.
- Sync automatically — data gets in through connectors you run.
- Browse the web — doors work on connected local data only.
- Rewrite history — the scrub covers current files; history is untouched by design.

## Maintainer

Maintainer: Kevthetech143 — issues welcome via the miss template (`.github/ISSUE_TEMPLATE/miss.md`).

## Platform

Tested on macOS and Linux. Windows is untested.

## What this tool touches

| Scope | Detail |
|---|---|
| Reads | Your connected folders and configured skill roots. |
| Writes | State dir under your home — `$SUPERJEV_STATE_DIR` or `~/.local/state/super-jev/` (per-principal logs, the `_memory/` config and pointer store `setup.py` creates) — plus `skills/super-jev/prepare-cache/` and `skills/super-jev/ledger/` in the checkout. |
| Leaves the machine | Sent to the TypeSafe provider: connected file text (to check each description), descriptions and questions (to rank files), and the claim plus evidence files you pass to `check`. |
| Provider receives | The above; never files the secret scan holds — those stay local. |
| Never leaves | Files the secret scan holds, and vault-style folders the inventory skips. |

## Uninstall

```bash
python3 skills/super-jev/setup.py --uninstall
```

Removes the state directory (`$SUPERJEV_STATE_DIR` or `~/.local/state/super-jev`,
including the memory config setup wrote, connected pointers and cached answers),
`skills/super-jev/prepare-cache/` and `skills/super-jev/ledger/` in this checkout,
and any `~/.claude/skills` links that point into this checkout. Your own files are
never touched. Delete the checkout folder to remove the code.

## Doors

- `ask` — `python3 ask.py` in `skills/super-jev/`: the daily ask loop front door over every connected pointer.
- `check` — `python3 dispatch.py check`: the claim gate over a claim or draft.
- `navigation` — `src/navigation-cli.ts`: navigate a file catalog for the best evidence files for one question.
- `skill-search` — `src/skill-search-cli.ts`: suggest which installed skills serve one request; advisory only.
- `permit` — `src/permit-cli.ts`: is this one harness action safe to run automatically?
- `permit` hard rules: destructive/irreversible actions are refused by hard code (before any model call) — documented in `docs/playbook.md`, see the permit row.
- `catalog` — `src/catalog-cli.ts`: maintenance door for fetch v2 catalogs (validate / learn / build).
- `catalog-build` — `src/catalog-build-cli.ts`: build a fetch v2 catalog JSON from a skills directory.
- `fetch` — `src/fetch-cli.ts`: score a catalog against one request and print only the top-k ids.
- `sweep` — `src/sweep-cli.ts`: process a record pile bigger than one call and prove nothing was skipped.
- Experimental (`chain-cli`, `derive-facts-cli`): see `docs/experimental/`.

## Coming next

- auto-catch: approved hits kept without a manual command.
- recipes: cache the how, run it live.
- browser driver: Jev decides, with an action permit before every click.

## Help us: report a miss

This is one team's daily driver made public. We want your misses. Open an issue with these five fields: what you asked; what came back; what you expected; which door; your Node and Python versions. Never paste private data or keys.

## License

MIT — see LICENSE.
