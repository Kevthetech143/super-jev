# Super Jev for agents: start here

You are an agent. Your human wants you to find facts in their own files and to
check what you say before you say it. Follow the steps below in order, from the
repo root. You need no private files and no help. Budget about 10 minutes; the
connect step makes live TypeSafe calls (well under a cent for a small folder at the published input rate).

## Set yourself up (numbered path)

1. **Check the tools.** `node --version` must print `v24` or newer, and
   `python3 --version` must print `3.10` or newer.

2. **Load the TypeSafe key into this shell. Do not skip this.** Every check and
   every connect needs it. Ask your human where the key file is, then:

   ```bash
   export TYPESAFE_API_KEY="$(cat /path/to/typesafe-key-file)"
   ```

   Never print the key, paste it into a command line, or write it into this repo.
   Each new shell needs the export again.

3. **Run setup.** It is safe to run again at any time.

   ```bash
   python3 skills/super-jev/setup.py
   ```

   Success: the last block starts with `READY.` If it says `NOT READY`, fix each
   listed item and run it again.

4. **Connect a folder of `.md` files** (only files your human said you may send
   to TypeSafe):

   ```bash
   python3 skills/super-jev/prepare_bulk.py --root /path/to/folder \
     --pointer my-notes --principal me --writer builtin
   ```

   Success: `connect: registered pointer=my-notes` and a `findability:` line.
   `--writer builtin` writes each file's description from its own headings, so
   the TypeSafe key is all you need. If the `claude` CLI is installed and logged
   in, you can drop that flag to get model-written descriptions instead.

5. **Ask a question you know the answer to.**

   ```bash
   python3 skills/super-jev/ask.py --principal me "your question in plain words"
   ```

   Success: ranked lines like `0.99  /path/to/file.md  [my-notes]`. This is
   where the answer is, not the answer itself: open the top file, read it, and
   answer from what it says, naming that file. `ask` only ranks files; it
   never reads the value out for you. Find the exact value in the file
   yourself; if the file only mentions the topic, the answer is not there.

6. **Ask for a fact that is not in the files.** Success: `no-candidates across
   1 pointers: no connected file answers this.` Tell your human it is not in
   their files. Do not guess.

7. **Check a claim before you send it.** Give it a claim you know is false:

   ```bash
   python3 skills/super-jev/dispatch.py check --claim "the claim" /path/to/file-you-read.md
   ```

   Exit 0 and `VERDICT: CLEAN` means send it. Exit 3 and `VERDICT: READ (blocked)`
   means do not send it: the file does not support the claim (`NOT_SUPPORTED`)
   or disproves it (`CONTRADICTED`). Any other exit means the check itself
   failed; treat it as blocked too.

8. **Uninstall when your human asks** (removes everything setup and connect made;
   their files are never touched):

   ```bash
   python3 skills/super-jev/setup.py --uninstall
   ```

## How to drive it for your human

- Before you state a fact from their files: run `ask`, open the top file, read it.
- Before you send an answer: run `check` with each claim and the file you read.
  Only exit 0 is a pass.
- Nothing found means say "not in your files", never a guess.
- When their files change, run step 4 again with `--refresh`.
- `ask.py --approve`, `--miss` and `--add` save good answers and log misses; see
  [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md) step 7. Approve takes the
  question and your answer: `python3 skills/super-jev/ask.py --principal me --approve "question" "answer"`.
- If something breaks: [docs/KNOWN-QUIRKS.md](docs/KNOWN-QUIRKS.md).

---

# Reference: using super-jev from an LLM agent

This is primarily a terminal interface, not an MCP server. Any agent with authorized file/terminal access and Node 24+ can follow these instructions. Reading them does not grant permission to transmit private records. One installable Claude Code skill does exist, covering the checks below (`gate`, `verify`, `sweep`, `bench`) — see [Agent front door (Claude Code skill)](#agent-front-door-claude-code-skill) at the end of this file. `organize`, below, has no skill wrapper yet; call it directly.

## Tool: organize

Purpose: classify text records into caller-defined categories, then group record IDs. Use it when categories have clear descriptions and the task needs semantic classification. Ordinary numeric/alphabetic sorting should use regular code.

Input: a JSON object with `records` (1–100 objects containing unique nonempty `id` and `text` strings), `categories` (2–30 category ID/description pairs, including `other`), and optional `minConfidence` (0–1; default 0.75). Category IDs use lowercase letters, numbers, hyphens or underscores and begin with a letter. See `examples/organizer.json`.

Only each record's `id` and `text` are included in provider evidence; extra record metadata is discarded. IDs and category descriptions are also transmitted, so use synthetic or authorized values for those fields too.

1. Confirm the user authorizes sending these records to TypeSafe. Keep credentials in `TYPESAFE_API_KEY`, never in JSON, commands, or committed files.
2. Prepare a small input JSON file containing only the records needed. Keep private inputs and outputs outside the checkout. The CLI limits the input to 80 KB and the engine independently limits the request to 100 KB. Category descriptions repeat for every record's question, so a valid input under 80 KB can still exceed the request budget. These are byte limits, not token guarantees.
3. Run `node src/cli.ts organize /path/to/input.json --live --out /path/to/new-output.json` from the repository. The output path must not exist. Omit `--out` for JSON on stdout. An API request may incur charges.
4. Check exit code. A nonzero code means failure, not an empty classification. Do not silently retry billable calls. New output files have owner-only permissions; failed runs attempt to remove incomplete output. Diagnostics omit raw parser/provider errors to protect private contents. Use the direct Node command for JSON stdout; npm script banners are not JSON.
5. Parse `rows`, `groups`, and `review`. Low-confidence and `other` results are kept in `review` and excluded from automatic groups. Confidence is not a correctness guarantee. Never silently discard review items.
6. Report the output path and unresolved items to the user. No original record is modified or moved. This command verifies output completeness, not category truth.

For a free offline demonstration run `npm run organize -- examples/organizer.json --demo`. Scripted mode deliberately refuses modified input; it is not an alternate classifier.

Live output includes `mode`, the resolved `model`, and token `usage` when returned. No full trace is saved by this CLI. Use `organizer()` with `run()` and your own Journal to record/replay a workflow, taking care with sensitive data.

This release does not implement priority scoring, file moves, database writes, recursive directory ingestion, arbitrary format extraction, automatic batching, or an MCP/API service. Use an adapter or another domain pack for those behaviors.

## Agent front door (Claude Code skill)

`skills/super-jev/` is an installable [Claude Code](https://docs.claude.com/en/docs/claude-code) skill: one Python file, `superjev.py`, giving an agent a single command for the checks this repo already runs elsewhere, instead of four separate tools to remember. It never re-implements a check — every judgement belongs to the tool it wraps.

Install by symlink or copy. `skills/super-jev-connect/` is a second skill for onboarding and refresh — registering a connector, drafting and gating its labels with a confirmed cheap writer model, and refreshing a pointer after its files change; install both:

```bash
ln -s "$(pwd)/skills/super-jev" ~/.claude/skills/super-jev
ln -s "$(pwd)/skills/super-jev-connect" ~/.claude/skills/super-jev-connect
```

| subcommand | wraps | what it needs |
| --- | --- | --- |
| `gate <evidence...> --draft <file>` / `--claim "..."` | the built-in judge client (`skills/super-jev/lib/jev_client.py`) | `TYPESAFE_API_KEY`; `SUPERJEV_GATE_CMD` (env) replaces the client with your own tool |
| `verify <report> [--worktree P] [--test-cmd C] [--paths ...]` | a report-verify tool | `SUPERJEV_VERIFY_CMD` (env), your own tool |
| `sweep <records.jsonl> --questions <q.json> --out <dir>` | `npm run sweep` | nothing — `SUPERJEV_REPO` defaults to this checkout |
| `bench [--dry-run] [--stub]` | `npm run bench:live` | nothing to plan; `TYPESAFE_API_KEY` for a live run |
| `permit`, `chain`, `fetch` | `npm run permit` / `npm run chain` / `npm run fetch` in this repo | built (fetch experimental: its admission gate in [`docs/wishlist.md`](docs/wishlist.md) is not yet met) |
| `ask "<one plain sentence>"` | the table above | a keyword router, no model call, no env |
| `status` | — | reports which subcommands are live in this checkout |

`gate` uses the judge client shipped in this repo and needs only `TYPESAFE_API_KEY`; set `SUPERJEV_GATE_CMD` to use your own claim-gate tool instead. `verify` is the one door this repo does not ship a tool for: point `SUPERJEV_VERIFY_CMD` at your report-verify tool; without it, `verify` falls back to a best-effort local check that can never say CLEAN.

Full reference, the exit-code table, and the `ask` routing keywords: [`skills/super-jev/SKILL.md`](skills/super-jev/SKILL.md).

Tests: `python3 -m pytest skills/super-jev/tests -q`, or `npm run test:skill`. Fully offline; every wrapped door is a fake in the test suite.
