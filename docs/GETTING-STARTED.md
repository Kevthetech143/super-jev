# Getting started with Super Jev

The full operating manual: one path, from empty machine to daily loop. Agents: the short version is
[AGENTS.md](../AGENTS.md). Every
step has the exact command and what success looks like. Run from the repo root.

## 1. Prerequisites

- **Node 24 or newer** — check: `node --version` → prints `v24` or newer.
- **Python 3.10 or newer** — check: `python3 --version` → prints `3.10` or newer.
- **A TypeSafe API key** — ask and check call TypeSafe with it (a `--writer builtin` connect does not,
  unless you add `--findability`).
  Do not commit it; keep it in a file only you can read (step 2 makes it).

Missing or too old? Install it into your home folder (no admin rights needed).
Python first:

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
Then open a new terminal (or run the `source` and `\.` lines again) so the new
versions are on your PATH. Setup (step 3) prints these same lines for whatever
is missing.

Check the tests still pass before you touch anything. The Python suite needs
pytest, so give it its own environment first. If `venv` says `ensurepip is not
available` (Debian and Ubuntu system Python), run the uv lines above first; no
sudo needed.

```bash
python3 -m venv .venv && . .venv/bin/activate && python -m pip install pytest
npm test                              # node suite — success: every test passes, 0 fail (under a minute)
python3 -m pytest skills/super-jev/tests -q   # python suite — success: "passed" with 0 failed (4 to 6 minutes; a few skips are normal)
```

Both suites start from a clean environment: they clear every `SUPERJEV_*`
setting (except `SUPERJEV_TEST_*`), the judge key and url variables and
`SWEEP_BATCH` first, so what your shell exports cannot change the result.

## 2. Set the key — do not skip this

Every ask and check needs the key in the environment of the shell you
run them from. **Super Jev never reads a `.env` file.**

The key lives in one file only you can read, `~/.typesafe-api-key`. If it does
not exist yet, make it in your own terminal (never paste a key into chat): run
this, paste the key, press Enter, then Ctrl-D:

```bash
(umask 077; cat > ~/.typesafe-api-key)
```

Then load it into the shell:

```bash
export TYPESAFE_API_KEY="$(cat ~/.typesafe-api-key)"
```

Success: `echo "${#TYPESAFE_API_KEY}"` prints a non-zero length (the length,
never the key). A new shell needs the export again.

For hooks that cannot inherit your shell (the skill finder is the one that
needs it), copy `skills/skill-search/deploy/key-provider.example.py` to
`skills/skill-search/deploy/local-key-provider.py` (gitignored). It prints the
key from `$TYPESAFE_API_KEY_FILE`, or from `~/.typesafe-api-key` when that is
not set, and nothing else.

The skill finder also needs to know which skill folders to search. Inside
Claude Code it uses `~/.claude/skills` and needs nothing. Any other agent copies
`skills/skill-search/roots.example.json` to `skills/skill-search/roots.json`
(gitignored) and lists its own skill folders there.

## 3. Run setup

```bash
python3 skills/super-jev/setup.py
```

Creates the state folder (`$SUPERJEV_STATE_DIR` or `~/.local/state/super-jev`,
owner-only) and the memory config inside it (`_memory/config.json`), then
checks Node, Python and the key. Success: it ends with `READY.` and the next
command. Safe to rerun: it never overwrites an existing config.

**Optional — install as Claude Code skills.** Link them into your agent's skill
directory; `setup.py --uninstall` removes these links again:

```bash
mkdir -p ~/.claude/skills
ln -s "$PWD/skills/super-jev" "$PWD/skills/super-jev-connect" "$PWD/skills/skill-search" ~/.claude/skills/
```

## 4. Connect your first folder

Onboard a folder of documents so its files become answerable. Replace the
caps words:

```bash
python3 skills/super-jev/prepare_bulk.py \
  --root /path/to/folder --pointer MYPOINTER --principal ME --writer builtin
```

Success: one `PASS` line per file, then `connect: registered pointer=MYPOINTER`
and a last line `CONNECTED n, HELD 0, FAILED 0` (exit 0).
Add `--findability` to also search each file's own sample question and report
`findability: N/N files rank first on their own question` (one paid search per file).

Each file gets a one-sentence description, and that description is checked
against the file before it is connected (by Jev for a model-written one, locally
for `--writer builtin`). Pick the description writer:

- `--writer builtin` — no model and no TypeSafe call (unless you add `--findability`):
  the description quotes the file's own headings and is checked locally.
- no flag — uses `claude -p --model haiku` when the `claude` CLI is installed
  (it must be logged in), and falls back to builtin when it is not.
- `--writer-command "my-writer"` — any command that reads the prompt on stdin
  and prints a JSON array.

- **Skipped by default:** connect says what a default rule left out, in `SKIP` lines with
  counts and folder or extension names (never file names). Every `.md` file left out is counted:
  those in `documents/`, `profile/`, `node_modules/` or a hidden folder (counted per folder; to
  connect one, connect that folder as its own set, `--root FOLDER --pointer NEW-NAME`, since
  re-running with an existing pointer replaces that set's files; a folder under `documents/` or
  `profile/`, `~/Documents` included, then does not pick up new files on its own, so run it again
  with `--refresh` after adding files), plus hidden, backup-named and empty files and links that
  point outside the roots (`--allow-target` admits them). Files of other types (only `.md`
  connects) are counted too, except hidden ones: a hidden file, or anything inside a hidden folder
  such as `.git/`, is not counted. Your own `--exclude` and `--name` choices are not counted.
  Skips never change the exit code.
- **Size guard:** a first connect refuses more than 250 files (`--max-files` raises it)
  and exits 2. It is a cost guard for model writers (a writer call and a TypeSafe check
  per file) but applies with `--writer builtin` too. A `--refresh` guards only the files
  that changed.
- **Held files:** Connect ends with `CONNECTED n, HELD m, FAILED k` and exits 3 if any file was held. A held
  file (a secret-like value, or a note over 250,000 bytes) has no override: read the held list the command
  printed, then remove or move the value, or split the note, and re-run. A password or key keyword holds a
  file only when a literal value follows it (a digit or symbol in it, not a placeholder or a call).
- **Where state lives:** per-principal logs and the memory config under `$SUPERJEV_STATE_DIR` or
  `~/.local/state/super-jev/<principal>/`. Preparation records land in the checkout:
  `skills/super-jev/prepare-cache/` (descriptions, reports and the recipe a refresh replays),
  `skills/super-jev/ledger/` and `skills/super-jev/autoheal-state/` (see step 9).

## 5. Ask your first question

```bash
python3 skills/super-jev/ask.py --principal ME "your question here"
```

Success: ranked lines `score  /path/to/file.md  [MYPOINTER]`, best first.
These tell you where the answer is; they are not the answer. A cache hit (a
question you approved before) prints `CACHE HIT` and the saved answer.

Every answer starts with one `OUTCOME:` line (`found`, `not-found`,
`not-supported`, `needs-setup` or `error`, with the one `next:` command when it
is not `found`). Exit codes: found 0, not-found 1, not-supported 2, error 3,
needs-setup 4.

- **`needs-setup`** (nothing connected, or a set is stale or unprepared): run the
  `next:` command (for nothing connected, go back to step 4), then ask again.
- **`not-found`** (a complete search): `What was searched:` (sets searched,
  files read, closest named) and `Next step (pick one):` list the exact connect
  and `--add` commands. Say so; do not guess, and take one of the listed next
  steps.

## 6. Open the top file yourself

When `ask` returns candidates, open the top evidence file in your editor and
read it. This step is yours — the agent does not trust a reply it did not
read. Success: you can point at the exact line that answers the question.

## 7. Approve, miss, or add

```bash
python3 skills/super-jev/ask.py --principal ME --approve "the question" "the answer"   # good hit: caches it
python3 skills/super-jev/ask.py --principal ME --miss "the question" "where it actually was"   # bad hit: log-only
python3 skills/super-jev/ask.py --principal ME --add "the question" "the answer"               # no file: writes a manual record
```

Success for `--approve`: asking the same question again is a cache hit.

A saved answer is the answer to one question, saved through one door: whether it saves itself on a repeat question, or you use `--approve` or `--add`, the same secret scan runs, and `--approve` and `--add` also run the claim check (CLEAN, 0.80 or higher, against the cited file); a repeat question saves the file itself, not an answer, and its N wins stand in for the claim check; a fact with no file gets the secret scan only and is shown as "no source file", and a `--source` that does not exist is refused. It lasts until its source file changes (there is no clock expiry), or until `--miss` removes it; `--miss` exits 1 when there was nothing saved to remove. The same question means the same words after lowercasing, collapsing spaces and dropping trailing punctuation; nothing fuzzier matches. A question saves itself when the same file wins it N times in a row (`SUPERJEV_SAVE_AFTER`, default 2); `--approve` meets that threshold at once. A hit says `saved answer, from FILE, saved DATE` (an auto-saved one prints the file path, not an answer) (or `no source file`); if the file changed, it says so and searches live instead.
`--add` quotes are taken verbatim from reviewed text; the same wording twice
refuses unless you pass `--replace-entry`.

## 8. Check a draft

Before you send an answer to someone else, run the check gate on it:

```bash
python3 skills/super-jev/dispatch.py check \
  --claim "claim one" --claim "claim two" \
  /path/to/evidence-you-actually-read.md
```

Or pass `--draft /tmp/draft.txt` instead of `--claim` to check every sentence
of a draft.

Jev reads the evidence files and answers, per claim, `SUPPORTED`,
`NOT_SUPPORTED` or `CONTRADICTED` with a confidence.

- Exit 0, `VERDICT: CLEAN` — every claim is supported at 0.80 or above. Send it.
- Exit 3, `VERDICT: READ (blocked)` — a claim is unsupported, contradicted or
  under 0.80. Do not send it.
- Any other exit — the check itself failed (no key, network, unreadable
  output). Treat it as blocked.

Don't know which file holds the fact, or want the proof file and line? Use
`python3 skills/super-jev/ask.py --principal me --claim "claim one"` instead: it exits
0 only for TRUE (FALSE is 5, every other result is non-zero), so only exit 0 is a pass there too.

Add `--json` for machine-readable output. Set `SUPERJEV_GATE_CMD` to use your
own claim-gate tool instead of the built-in client
(`skills/super-jev/lib/jev_client.py`).

## 9. Refresh when files change

When the connected folder changes on disk, re-run prepare with `--refresh`:

```bash
python3 skills/super-jev/prepare_bulk.py \
  --pointer MYPOINTER --principal ME --refresh
```

This replays how you connected it, writer included. Give a writer flag to
change it, and later refreshes keep it.

Success: the summary shows newly drafted or re-gated files; unchanged files
are skipped.

**Auto-heal, best effort.** Nothing watches your folders. An `ask` that meets a
set whose files changed starts this same refresh in the background and does not
wait for it; the next ask finds the result. Only asks trigger it. It runs one
refresh per set at a time, waits out a cooldown before touching a set again, and
caps how many refreshes it starts per hour. If a background refresh fails, the
ask says `auto-heal: last refresh FAILED: <reason>`; run the refresh command
printed on that line to see the error and refresh by hand. Auto-heal writes its locks,
cooldowns and logs, which name your sets and files, to
`skills/super-jev/autoheal-state/`. `SUPERJEV_NEW_FILE_SCAN=0` turns off the
scan that looks for new notes in connected folders; refreshing a changed set has
no off switch.

## 10. Uninstall

```bash
python3 skills/super-jev/setup.py --uninstall
```

Removes everything Super Jev wrote: the state folder (memory config, logs, pointers),
`skills/super-jev/prepare-cache/`, `ledger/` and `autoheal-state/`, the chat CLI's
config and launcher if you installed it, and any `~/.claude/skills` links into this
checkout. A file of yours that sits in one of those folders stays, and the output lists it.
Your own files are never touched.

## Notes

- **Memory config:** `dispatch.py memory` uses the config `setup.py` wrote.
  Pass `--config /path/to/config.json` to use a different one.
- **TLS on Macs without a system CA bundle:** the built-in judge client uses
  `certifi` automatically when it is installed. Other Python doors need
  `SSL_CERT_FILE=$(python3 -m certifi)`, Node doors need
  `NODE_EXTRA_CA_CERTS=$(python3 -m certifi)`. Without them, HTTPS calls fail
  with certificate errors (see KNOWN-QUIRKS).
