# Getting started with Super Jev

The full operating manual: one path, from empty machine to daily loop. Agents: the short version is
[AGENTS.md](../AGENTS.md). Every
step has the exact command and what success looks like. Run from the repo root.

## 1. Prerequisites

- **Node 24** — check: `node --version` → prints `v24.x.y`.
- **Python 3.10+** — check: `python3 --version` → prints `3.10` or newer.
- **A TypeSafe API key** — connect, ask and check all call TypeSafe with it.
  Do not commit it; keep it in a file only you can read.

Check the tests still pass before you touch anything:

```bash
npm test                              # node suite — success: every test passes, 0 fail (under a minute)
python3 -m pytest skills/super-jev/tests -q   # python suite — success: "passed" with 0 failed (4 to 6 minutes; a few skips are normal)
```

## 2. Set the key — do not skip this

Every connect, ask and check needs the key in the environment of the shell you
run them from. **Super Jev never reads a `.env` file.**

```bash
export TYPESAFE_API_KEY="$(cat ~/.typesafe-api-key)"
```

Success: `echo "${#TYPESAFE_API_KEY}"` prints a non-zero length (the length,
never the key). A new shell needs the export again.

For hooks that cannot inherit your shell, copy
`skills/skill-search/deploy/key-provider.example.py` to
`~/.skill-search-key-provider.py` and set `TYPESAFE_API_KEY_FILE` to your key
file; that provider prints the key and nothing else.

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
and `findability: N/N files rank first on their own question`.

Each file gets a one-sentence description, and Jev checks that description
against the file before it is connected. Pick the description writer:

- `--writer builtin` — no model: the description quotes the file's own
  headings. Needs only the TypeSafe key.
- no flag — uses `claude -p --model haiku` when the `claude` CLI is installed
  (it must be logged in), and falls back to builtin when it is not.
- `--writer-command "my-writer"` — any command that reads the prompt on stdin
  and prints a JSON array.

- **Held files:** the secret scan may hold files it does not trust. Read the
  held file list the command printed, review the files, then re-run with
  `--allow-held` if they are safe. The size-ceiling hold is not overridden
  by `--allow-held`.
- **Where state lives:** under `$SUPERJEV_STATE_DIR` or
  `~/.local/state/super-jev/<principal>/` — never in this repo.

## 5. Ask your first question

```bash
python3 skills/super-jev/ask.py --principal ME "your question here"
```

Success: ranked lines `score  /path/to/file.md  [MYPOINTER]`, best first.
These tell you where the answer is; they are not the answer. A cache hit (a
question you approved before) prints `CACHE HIT` and the saved answer.

- **Nothing connected yet:** prints `nothing connected yet for principal
  'ME' -- run connect first` and the command. Go back to step 4.
- **Fact not in the files:** prints `no-candidates across N pointers: no
  connected file answers this.` Say so; do not guess.

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
`--add` quotes are taken verbatim from reviewed text; the same wording twice
refuses unless you pass `--replace-entry`. `--add` is off by default: it needs
`"allowAgentAssist": true` added to the memory config setup wrote
(`~/.local/state/super-jev/_memory/config.json`); without it `--add` says so
and exits 1.

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

Add `--json` for machine-readable output. Set `SUPERJEV_GATE_CMD` to use your
own claim-gate tool instead of the built-in client
(`skills/super-jev/lib/jev_client.py`).

## 9. Refresh when files change

When the connected folder changes on disk, re-run prepare with `--refresh`:

```bash
python3 skills/super-jev/prepare_bulk.py \
  --root /path/to/folder --pointer MYPOINTER --principal ME --writer builtin --refresh
```

Success: the summary shows newly drafted or re-gated files; unchanged files
are skipped.

## 10. Uninstall

```bash
python3 skills/super-jev/setup.py --uninstall
```

Removes the state folder, the memory config, `skills/super-jev/prepare-cache/`,
`skills/super-jev/ledger/`, and any `~/.claude/skills` links into this
checkout. Your own files are never touched.

## Notes

- **Memory config:** `dispatch.py memory` uses the config `setup.py` wrote.
  Pass `--config /path/to/config.json` to use a different one.
- **TLS on Macs without a system CA bundle:** the built-in judge client uses
  `certifi` automatically when it is installed. Other Python doors need
  `SSL_CERT_FILE=$(python3 -m certifi)`, Node doors need
  `NODE_EXTRA_CA_CERTS=$(python3 -m certifi)`. Without them, HTTPS calls fail
  with certificate errors (see KNOWN-QUIRKS).
