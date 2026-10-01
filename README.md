# Super Jev

[![Tests](https://github.com/Kevthetech143/super-jev/actions/workflows/test.yml/badge.svg)](https://github.com/Kevthetech143/super-jev/actions/workflows/test.yml)

One judge (Jev) between your agent and your data: the agent asks in its own words, Super Jev finds the file, checks the claim, permits the action, and remembers what you approved. Your agent stays responsible for the answer.

## Vision

- One judge sits between your agent and your data: the agent asks in its own words, Jev finds the file, checks the claim, permits the action, and remembers what you approved. Your agent stays responsible for the answer.
- Agents burn whole LLM turns on lookups, re-hunt the same answers daily, and state things the files never said. Jev is a fast, cheap judge for yes/no and which-one questions; the LLM keeps the writing.
- The daily loop: ask → read the top file → answer → approve / miss / add. Connectors are how your data gets in; the cache fills only from your own approvals — nothing is cached that you did not approve.
- 1.0 promises the proven core: skill search, file navigate, connect + bulk prepare, check gate, permit gate, the harness loop. The core works well when it works; edges still want an agent in the seat — see KNOWN-QUIRKS.md and AGENTS.md.
- 1.0 does not promise unattended answering, semantic cache matching, automatic sync, or live browsing. Next: auto-catch, recipes, a Jev-decided browser driver — each ships only after its own live bench.

## Quick start

People: [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md) — the full operating manual.
Agents: [AGENTS.md](AGENTS.md) — the numbered path and daily loop, plus
[wire-into-claude-code](docs/wire-into-claude-code.md) for the retrieval rule card and Stop-hook claim gate.
No key yet? `npm run demo` runs the loop once offline.

## The loop

`ask` → read the top file yourself → answer → `--miss` a bad saved one, `--add` a fact that has no file. A question you ask again saves itself.

Auto-save: when the same file wins the same question for you N times in a row (`SUPERJEV_SAVE_AFTER`, default 2) on complete searches (a partial search neither counts nor resets) and passes the content check, the ranked list from the winning search is saved (up to 5 files, not an answer; the N wins are the evidence, so no extra claim check, and the secret scan and unchanged-file check still run; marked `approved_by: auto-save`); the next ask returns the whole list at once, in rank order, labelled saved, with no search, and you open the files; if any listed file changed it is withheld as STALE and searched live. Secret-held files are not saved, and a changed file is withheld as STALE; it says why. Opt out with `--no-auto` or `SUPERJEV_AUTO_CACHE=0`. `--approve` meets the threshold at once (`approved_by: principal:NAME`). Every cache hit prints who approved it, and `--miss` on a cached question un-saves it. Matching is the same question after lowercasing, collapsing spaces and dropping trailing punctuation; nothing fuzzier. A saved answer lasts until its source file changes, with no clock expiry. Connectors are how data gets in: onboard a folder once, and it stays answerable.

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
| Leaves the machine | Sent to the TypeSafe provider: file text when `ask` reads a file to confirm an answer, descriptions and questions (to rank files), and the claim plus evidence files you pass to `check`. A connect sends file text only with a model writer (the default when the `claude` CLI is installed): the writer model reads an excerpt of each file and TypeSafe checks each description against its file. `--writer builtin` sends nothing while connecting, unless you add `--findability`: that runs one ranking search per file, which sends the descriptions and that file's sample question. |
| Provider receives | The above; never files the secret scan holds — those stay local. |
| Never leaves | Files the secret scan holds, and files connect skips by default (`profile/` and `documents/` folders, hidden and generated folders, other file types); connect prints a `SKIP` line for each kind. |

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
