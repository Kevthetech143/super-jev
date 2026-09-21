# Super Jev

[![Tests](https://github.com/Kevthetech143/super-jev/actions/workflows/test.yml/badge.svg)](https://github.com/Kevthetech143/super-jev/actions/workflows/test.yml)

One judge (Jev) between your agent and your data: the agent asks in its own words, Super Jev finds the file, checks the claim, permits the action, and remembers what you approved. Your agent stays responsible for the answer.

## Vision

- One judge sits between your agent and your data: the agent asks in its own words, Jev finds the file, checks the claim, permits the action, and remembers what you approved. Your agent stays responsible for the answer.
- Agents burn whole LLM turns on lookups, re-hunt the same answers daily, and state things the files never said. Jev is a fast, cheap judge for yes/no and which-one questions; the LLM keeps the writing.
- The daily loop: ask → read the top file → answer → approve / miss / add. Connectors are how your data gets in; the cache fills only from your own approvals — nothing is cached that you did not approve.
- 1.0 promises the proven core: skill search, file navigate, connect + bulk prepare, check gate, permit gate, the harness loop. The core works well when it works; edges still want an agent in the seat — see KNOWN-QUIRKS.md and AGENT-GUIDE.md.
- 1.0 does not promise unattended answering, semantic cache matching, automatic sync, or live browsing. Next: auto-catch, recipes, a Jev-decided browser driver — each ships only after its own live bench.

## 60-second quick start

Fresh clone, Node 24:

```bash
npm run demo                                        # watch the loop run once
npm test                                            # node suite, all green
printf '["%s/skills"]' "$PWD" > /tmp/ROOTS.json
printf '{"request":"look up a saved fact","context":[]}' > /tmp/REQUEST.json
npm run skill-search -- --roots-file /tmp/ROOTS.json \
  --request-file /tmp/REQUEST.json --local-only     # skill search, no network
python3 -m pytest skills/super-jev/tests -q          # python suite
```

Next: [GETTING-STARTED](docs/GETTING-STARTED.md) — the full operating manual
(one path: prerequisites → install → key → connect → ask → approve/miss/add → check → refresh).
For agents: [wire-into-claude-code](docs/wire-into-claude-code.md) (the retrieval rule card
and Stop-hook claim gate), [AGENT-GUIDE](docs/AGENT-GUIDE.md) (the daily loop), and
[KNOWN-QUIRKS](docs/KNOWN-QUIRKS.md) (what breaks and the workaround).

## The loop

`ask` → read the top file yourself → answer → `--approve` a good hit, `--miss` a bad one, `--add` a fact that has no file. Connectors are how data gets in: onboard a folder once, and it stays answerable.

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
| fetch | MEASURED — has live bench scripts |
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
| Writes | State dir under your home — `$SUPERJEV_STATE_DIR` or `~/.local/state/super-jev/<principal>/` (`skills/super-jev/ask.py:90-92`) — plus the pointer memory config and local caches. |
| Leaves the machine | Prepared file descriptions and sample questions sent to the TypeSafe provider. |
| Provider receives | Those descriptions; never raw secret files — files the secret scan holds stay local. |
| Never leaves | Files the secret scan holds, and vault-style folders the inventory skips. |

## Uninstall

```bash
rm ~/.claude/skills/super-jev ~/.claude/skills/super-jev-connect   # skill symlinks (AGENTS.md:33-34)
rm -rf "${SUPERJEV_STATE_DIR:-~/.local/state/super-jev}"            # state dir (skills/super-jev/ask.py:90-92)
# remove the pointer memory config.json you passed via the installed memory.sh wrapper / --config
# (skills/super-jev/ask.py:233-234; its location is deployment-local)
```

## Doors

- `ask` — `python3 ask.py` in `skills/super-jev/`: the daily ask loop front door over every connected pointer.
- `check` — `python3 dispatch.py check`: the claim gate over a claim or draft.
- `navigation` — `src/navigation-cli.ts`: navigate a file catalog for the best evidence files for one question.
- `skill-search` — `src/skill-search-cli.ts`: suggest which installed skills serve one request; advisory only.
- `permit` — `src/permit-cli.ts`: is this one harness action safe to run automatically?
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
