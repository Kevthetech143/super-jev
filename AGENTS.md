# Super Jev for agents: start here

You are an agent. Your human wants you to find facts in their own files and to
check what you say before you say it. Follow the steps below in order, from the
repo root. You need no private files and no help. Budget about 10 minutes. An
`ask` or `check` makes live TypeSafe calls (an answer Super Jev already saved is
served without one). A connect makes them only with a model writer (the default
when the `claude` CLI is installed); a `--writer builtin` connect makes none
unless you add `--findability`.

## Set yourself up (numbered path)

1. **Check the tools.** `node --version` must print `v24` or newer, and
   `python3 --version` must print `3.10` or newer. If either is missing or too
   old, install it into your home folder (no admin rights needed). Python first:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   source "$HOME/.local/bin/env"
   uv python install 3.12 --default
   ```

   Then Node (the `touch` line is for zsh, the Mac default; skip it on bash):

   ```bash
   touch ~/.zshrc
   curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash
   \. "$HOME/.nvm/nvm.sh"
   nvm install 24
   ```

   Already use Homebrew? Run `brew install node python` instead of the lines above.
   If your tool starts every command in a fresh shell and `node` or `python3`
   still shows the old version, start each command with
   `source "$HOME/.local/bin/env"; \. "$HOME/.nvm/nvm.sh";` until the session
   restarts. Setup (step 3) prints these same lines for whatever is missing.

2. **Load the TypeSafe key into this shell. Do not skip this.** Every ask and
   every check needs it (a `--writer builtin` connect does not unless you add
   `--findability`, but your next steps do). The key lives in one file, `~/.typesafe-api-key`. If
   that file does not exist yet, ask your human to make it in their own terminal
   (the key never goes through chat). They run this, paste the key, press Enter,
   then Ctrl-D:

   ```bash
   (umask 077; cat > ~/.typesafe-api-key)
   ```

   Then load it:

   ```bash
   export TYPESAFE_API_KEY="$(cat ~/.typesafe-api-key)"
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

   Success: `connect: registered pointer=my-notes`, then a last line
   `CONNECTED n, HELD 0, FAILED 0`, and exit code 0 (add `--findability` for a
   `findability:` report; it costs one paid search per file, with any writer).
   `--writer builtin` writes each file's description from its own headings and
   checks it locally: no model call and no TypeSafe call while connecting. If the
   `claude` CLI is installed and logged in, you can drop that flag to get
   model-written descriptions instead (those are checked by TypeSafe).

   Read the last line and the exit code, never just the word "registered":

   - Exit 3: connected, but some files were held (a secret-like value, or a note
     over 250,000 bytes). Read the `HELD` lines; those files are not searchable.
   - Exit 1: a file failed its check or the run could not finish. Read the
     `EXCEPTION` and `ERROR` lines. Other files may still have connected: read
     `CONNECTED n` on the last line.
   - Exit 2: refused. A first connect refuses more than 250 files, even with
     `--writer builtin` (`--max-files` raises it). It is a cost guard for model
     writers, so narrow `--root` or `--exclude` first.
   - `SKIP` lines: what a default rule left out, counted by reason, with folder
     or extension names and never file names. Every `.md` file left out is
     counted, and so is every file of another type except hidden ones (a hidden
     file, or anything inside a hidden folder such as `.git/`, is not counted).
     `.md` files in a skipped folder (`documents/`, `profile/`, `node_modules/`, a
     hidden folder) are counted per folder. To connect one, connect that folder
     as its own set: `--root FOLDER --pointer NEW-NAME`. Re-running with a
     pointer that already exists replaces that set's files, so use a new name. A
     folder under `documents/` or `profile/` (`~/Documents` included) does not
     pick up new files on its own: run it again with `--refresh` after adding
     files. Only `.md` files connect; other types are never read.

5. **Ask a question you know the answer to.**

   ```bash
   python3 skills/super-jev/ask.py --principal me "your question in plain words"
   ```

   Success: ranked lines like `0.99  /path/to/file.md  [my-notes]`. This is
   where the answer is, not the answer itself: open the top file, read it, and
   answer from what it says, naming that file. `ask` only ranks files; it
   never reads the value out for you. Read every relevant returned file yourself: a component may need another source
   or calculation. Topic overlap alone is insufficient. See
   [source retrieval](skills/super-jev/references/source-retrieval.md) for scope and limits.

6. **Ask for a fact that is not in the files.** Success: the output starts with
   `OUTCOME: not-found - searched 1 set, no matching file (it may still exist)` (exit 1). Tell your
   human it may still exist, and offer to search by hand. Do not guess.

7. **Check a claim before you send it.** Give it a claim you know is false:

   ```bash
   python3 skills/super-jev/dispatch.py check --claim "the claim" /path/to/file-you-read.md
   ```

   Exit 0 and `VERDICT: CLEAN` means send it. Exit 3 and `VERDICT: READ (blocked)`
   means do not send it: the file does not support the claim (`NOT_SUPPORTED`)
   or disproves it (`CONTRADICTED`). Any other exit means the check itself
   failed; treat it as blocked too.

   Want the proof file and line, or don't know which file holds the fact? Use
   `ask.py --claim` instead (see "Check a claim: which door" below).

8. **Uninstall when your human asks** (removes everything Super Jev wrote, including logs,
   auto-heal state and an older chat CLI's config. It keeps the key file
   `~/.typesafe-api-key`: tell them to delete it if they want the key gone. Their files
   are never touched, even one that sits in a folder Super Jev writes to):

   ```bash
   python3 skills/super-jev/setup.py --uninstall
   ```

## How to drive it for your human

- Before you state a fact from their files: run `ask`, open the top file, read it.
- Before you send an answer: run `check FILE --claim "..."` with each claim and the file you read,
  or `ask.py --claim "..."` when you do not know which file holds the fact. Only exit 0 is a pass.
- Nothing found means say "Super Jev couldn't find it; it may still exist" and offer to search by hand, never a guess.
- When their files change, run `python3 skills/super-jev/prepare_bulk.py --pointer NAME --principal me --refresh`:
  it replays how the folder was connected, writer included. An ask also starts that refresh in the
  background; if it says `auto-heal: last refresh FAILED: <reason>`, run the refresh command printed on that line.
- A question you ask again saves itself: when the same file wins it N times
  (`SUPERJEV_SAVE_AFTER`, default 2) and passes the check, the next ask returns it at once,
  labelled saved (auto-save on by default; `--no-auto` or `SUPERJEV_AUTO_CACHE=0` to opt out).
  `--miss` on a saved question un-saves it; hits print `approved_by: auto-save|principal:NAME`.
- `ask.py --approve`, `--miss` and `--add` save a good answer at once and log misses; see
  [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md) step 7. Approve takes the
  question and your answer: `python3 skills/super-jev/ask.py --principal me --approve "question" "answer"`.
- If something breaks: [docs/KNOWN-QUIRKS.md](docs/KNOWN-QUIRKS.md).

## Before real work: preflight, remember vs. one-off, claims, and the build cycle

- **Check readiness first.** `ask.py --principal me --preflight` reports connections ready and
  per-folder coverage with no paid call. Add `--about "the work"` for what's already known (4 paid
  asks: tried before, rules, traps, files) or `--skill "what a new skill would do"` (one paid ask)
  to find an existing skill before building one — both cost a call, the base preflight does not.
  `--skill` needs only skill folders, so it also runs while connections are NOT READY.
- **Remember vs. one-off.** Connect a folder (step 4) only if you will ask it more than once. For
  a one-off file, a diff, or a worker's report, skip connecting and check it directly:
  `dispatch.py check FILE... --claim "..."` or `dispatch.py verify REPORT --worktree DIR`.
- **Check a claim: which door.** Use `ask.py --principal me --claim "statement"` when you do not
  know which file holds the fact and you want the proof file and line; it answers
  `TRUE`/`FALSE` (with the proof file and line), `CONFLICT`, `PARTIAL`, `UNSURE`, or `NOT FOUND`, and
  `--claims-file FILE` checks one statement per line. Use `dispatch.py check FILE --claim "..."`
  when you already have the file, diff or report and want a send/no-send gate; it gives no proof
  line. Both doors: only exit 0 passes. `ask --claim` exits TRUE 0, FALSE 5; every other result is
  not a pass, and its code says how the search went, whatever the verdict word and whether or not
  files are listed: 1 searched fully and nothing settles it (NOT FOUND, UNSURE, PARTIAL, CONFLICT),
  3 a set or the content check failed (or `UNSURE: the true/false check did not run`), 4 setup needed
  (a set is stale or unprepared, or files were skipped or held), 2 refused input; several statements
  exit with the highest code.
  `check` exits CLEAN 0, READ 3, REJECT 2; any other code (1 error, 5 refused) means the check
  failed (step 7). The two doors use different numbers: ask's 5 is FALSE, check's 5 is a refusal.
- **Building something bigger than one answer:** use the `super-jev-build-cycle` skill. Six steps —
  preflight, start, check-report, review, reply-check, learn — and the review step must come from a
  fresh, independent agent, never the builder; it refuses to close while the latest review says FIX.

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

`skills/super-jev-build-cycle/` is an optional third skill: a plain-CLI build cycle (`build_cycle.py`) that uses Super Jev in every step and leaves a receipt per step. Link it into any agent's skills folder next to `super-jev` (it finds `../super-jev/`).

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

Tests: `python3 -m pytest skills/super-jev/tests -q`, or `npm run test:skill` (pytest goes in a venv; see [GETTING-STARTED](docs/GETTING-STARTED.md) step 1). Fully offline; every wrapped door is a fake in the test suite.
