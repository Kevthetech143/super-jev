# Getting started with Super Jev

The full operating manual: one path, from empty machine to daily loop. Every
step has the exact command and what success looks like. Run from the repo root.

## 1. Prerequisites

- **Node 24** — check: `node --version` → prints `v24.x.y`.
- **Python 3.10+** — check: `python3 --version` → prints `3.10` or newer.
- **A TypeSafe API key** — the writer and the skill-search model lane both
  need it. Do not commit it; keep it in a file only you can read.

Check the tests still pass before you touch anything:

```bash
npm test                              # node suite — success: 647 passing
python3 -m pytest skills/super-jev/tests -q   # python suite — worktree: 1224 passed, 8 skipped; a fresh stranger copy gives 1222 passed, 10 skipped (two more skips with no local runtime)
```

## 2. Install the skills

Super Jev ships two skills: `super-jev` (the daily doors) and
`skill-search` (find the right skill). Link them into your agent's skill
directory:

```bash
ln -s "$PWD/skills/super-jev" "$PWD/skills/super-jev-connect" "$PWD/skills/skill-search" ~/.claude/skills/
```

Success: `ls ~/.claude/skills` shows `super-jev`, `super-jev-connect`, and `skill-search` entries.
Remove the links the same way (`unlink`) if you uninstall.

## 3. Set the key

**No `.env` file:** Super Jev never reads one. The key comes from the
environment (`TYPESAFE_API_KEY`) or the key-provider example below.

You have two options. Pick one.

**Option A — env var (simplest):**

```bash
export TYPESAFE_API_KEY="$(cat ~/.typesafe-api-key)"
```

Success: `echo "${#TYPESAFE_API_KEY}"` prints a non-zero length.

**Option B — key provider (for hooks):**
copy `skills/skill-search/deploy/key-provider.example.py`, point it at your
key file via the env var it reads:

```bash
cp skills/skill-search/deploy/key-provider.example.py ~/.skill-search-key-provider.py
export TYPESAFE_API_KEY_FILE="$HOME/.typesafe-api-key"
```

Success: `~/.skill-search-key-provider.py` prints the key and nothing else.
Never use `deploy/local-key-provider.py` from a dev checkout — it reads a
machine-specific credential adapter and is intentionally not shipped.

## 4. Connect your first folder

Onboard a folder of documents so its files become answerable. Replace the
caps words:

```bash
python3 skills/super-jev/prepare_bulk.py \
  --root /path/to/folder --pointer MYPOINTER --principal ME \
  --writer-command "my-writer"
```

Success: the command prints a summary of drafted, held, and failed files,
and your folder appears in later `ask` results. `--writer-command` receives
the prompt on stdin and must return a JSON array on stdout; omit it to use
the default writer (`--writer-model` picks the model, default is Haiku).

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

Success: prints the answer plus the evidence file path, or a no-candidates
hint naming `references/connectors.md` and `--add`. A cache hit prints the
answer and stops without any provider call.

- **First run:** if you see `preparation-required`, that is normal on a fresh
  clone — nothing is connected yet. Go back to step 4 and run the connect command.

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
refuses unless you pass `--replace-entry`.

## 8. Check a draft

Before you send an answer to someone else, run the check gate on it:

```bash
python3 skills/super-jev/dispatch.py check \
  --draft /tmp/draft.txt --claim "claim one" --claim "claim two" \
  /path/to/evidence-you-actually-read.md
```

Success: the gate passes when every claim is supported by a verbatim quote
from the evidence files you list. It matches exact quotes, not paraphrases.
Add `--json` for machine-readable output.

## 9. Refresh when files change

When the connected folder changes on disk, re-run prepare with `--refresh`:

```bash
python3 skills/super-jev/prepare_bulk.py \
  --root /path/to/folder --pointer MYPOINTER --principal ME --refresh
```

Success: the summary shows newly drafted or re-gated files; unchanged files
are skipped.

## Notes

- **Memory config:** `dispatch.py memory` takes your own config file via
  `--config /path/to/config.json` (for example
  `.local/pointer-demo/config.json`). The `memory.sh` wrapper that supplies
  deployment-local repo/config is generated locally at install time — it is
  not shipped in this repo and is not tracked by git. If a second checkout
  sees 0 pointers, pass `--config` explicitly (see KNOWN-QUIRKS).
- **TLS on Macs without a system CA bundle:** Python doors need
  `SSL_CERT_FILE=$(python3 -m certifi)` in the environment, Node doors need
  `NODE_EXTRA_CA_CERTS=$(python3 -m certifi)`. Without them, HTTPS calls fail
  with certificate errors (see KNOWN-QUIRKS).
