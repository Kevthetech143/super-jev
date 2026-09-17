---
name: super-jev
description: "The ONE front door for every check we run — gate a draft against its evidence, verify a worker's report, sweep a pile bigger than one call, and run the live bench. Thin wrappers over a claim-gate tool, a report-verify tool, and the super-jev harness, never a second copy of them. Three doors are named but not built (permit, chain, fetch); each one names its own wishlist item and exits 6, so a missing tool tells you what is missing. `ask \"<one plain sentence>\"` routes a request to the right door with no model call. Triggers: /super-jev, check this before I send it, is this report true, run every question over this pile, super jev"
type: procedure
---

# /super-jev — one simple ask, and the right check runs

**In one sentence: you say what you want checked in plain words, and this picks the door, runs it, and hands you one verdict line.**

*"The skill becomes the thing we steer in between us and the data: one simple ask and super jev handles it."*

Before this, the checks were scattered across two skills and a repo on four branches. A fresh session had to remember four places. This is the one place.

This copy lives in `skills/super-jev/` in the [super-jev](../../README.md) repo and is portable out of the box: `sweep` and `bench` look for their npm scripts in this checkout by default (override with `SUPERJEV_REPO`), and `gate`/`verify` wrap whatever claim-gate and report-verify tools you point them at (see **Install**, below). A clone with no env set at all still runs `status`, `ask`, `sweep` and `bench`; `gate` and `verify` need two tools this repo does not ship — see **Related doors** at the bottom.

    python3 skills/super-jev/superjev.py <door> ...

## Install

```bash
ln -s "$(pwd)/skills/super-jev" ~/.claude/skills/super-jev   # or copy the directory
```

Point `gate` and `verify` at your own tools with two env vars, each a full shell command:

```bash
export SUPERJEV_GATE_CMD="python3 /path/to/your/claim-gate.py"
export SUPERJEV_VERIFY_CMD="python3 /path/to/your/report-verify.py"
```

Without them, `gate` and `verify` fall back to a fixed local path
(`~/.claude/skills/jev-check/lib/jev.py` and `~/.claude/skills/worker-verify/verify.py`)
that is real on the machine this skill was authored on and almost certainly
absent anywhere else — in that case the door names the missing path and the
env var that replaces it, instead of failing inside a subprocess. `status`
reports which of the two is live.

`SUPERJEV_REPO` points `sweep` and `bench` at a super-jev checkout carrying
those npm scripts; it defaults to this repo's own root, so those two doors
need no env at all when the skill is used from inside a clone.

## The doors

| door | what it answers | state | who actually runs it |
|---|---|---|---|
| `gate <evidence...> --draft <file>` or `--claim "..."` | does my draft actually follow from the files I read | **LIVE**, needs a claim-gate tool | `SUPERJEV_GATE_CMD` |
| `verify <report> [--worktree P] [--test-cmd C] [--paths ...]` | is this worker's "done" true | **LIVE**, needs a report-verify tool | `SUPERJEV_VERIFY_CMD` |
| `sweep <records.jsonl> --questions <q.json> --out <dir>` | every question of every record, with proof nothing was skipped | **LIVE** | `npm run sweep` in the harness |
| `bench [--dry-run] [--stub]` | how good is the harness, measured | **LIVE** | `npm run bench:live` |
| `permit <snapshot> --action "..."` | is it safe to click / pay / send / delete automatically | **NOT BUILT** — wishlist item 5 | — |
| `chain <spec.json> <docs...>` | is the ticket-to-order-to-policy chain complete | **NOT BUILT** — wishlist item 4 | — |
| `fetch "<request>"` | which skill or tool should be loaded for this | **NOT BUILT** — wishlist item 6 | — |
| `ask "<one plain sentence>"` | picks the door for you | **LIVE** | a keyword table, no model call |
| `status` | which doors are live here, right now | **LIVE** | — |
| `hook <gate\|verify>` | a Claude Code hook shim: reads the hook payload on stdin, maps the verdict to the hook's own exit convention | **LIVE** | wraps `gate`/`verify` above |
| `ledger [-n N]` | the last N calls this wrapper made, and a per-door count | **LIVE** | reads `ledger/calls.jsonl` under this skill |

**A door that is not built exits 6 and prints its own wishlist line.** That is the point: a missing tool names itself, in the wishlist's own words, instead of a vague failure. Never hand-write "not supported" — run it and read what it says.

**`status` is the only honest answer to "does this work here?"** It reads the doors off disk, so a checkout without the sweep script shows `MISSING SCRIPT` rather than a promise. It also checks that `npm` itself is actually runnable — a checkout that carries the `sweep`/`bench:live` script but has no reachable `npm` shows `script present, npm not runnable`, never a false `LIVE`.

## Machine-readable output: `--json`

Every door above except `ask` and `hook` (which have contracts of their own) takes `--json`. It prints **exactly one JSON object on stdout and nothing else** — no echoed command, no npm banner, nothing from the wrapped tool leaks onto stdout unparsed. Anything the wrapped tool printed is captured and folded into `details`.

```bash
python3 $S gate evidence.md --draft draft.md --json
python3 $S status --json
```

```json
{
  "door": "gate",
  "verdict": "CLEAN",
  "exit_code": 0,
  "summary": "CLEAN — every claim is carried by the evidence at or above 0.80. Send it.",
  "details": {"stdout": "...", "stderr": ""},
  "would_run": ["python3", "/path/to/jev.py", "evidence.md", "--kit", "reply", "--draft", "draft.md"]
}
```

`verdict` is a short keyword (`CLEAN` / `READ` / `REJECT` / `RAN` / `ERROR` / `REFUSED` / `NOT_BUILT` / `OK`, depending on the door); `summary` is the same human sentence the non-JSON mode prints; `would_run` is the argv this wrapper built, present on every response including refusals.

## Hook shim: `hook <gate|verify>`

`hook` is a *different* contract from the rest of this file — built for Claude Code's own hook exit convention, not this wrapper's usual exit codes:

| this shim returns | what it means to Claude Code |
|---|---|
| exit 0, nothing on stdout | **allow** — CLEAN, or the wrapped tool's verdict maps to "not a problem" |
| exit 0, one line on stdout | **advisory** — READ (or any other non-blocking verdict); never blocks |
| exit 2, one line on stderr | **block** — REJECT: the evidence disproves a claim, or a quote is fabricated |

It never exits 3, 4 or 5 — those are `gate`'s and `verify`'s own exit codes and mean nothing to a hook runner; they are folded into advisory (0) or block (2) above. It reads the Claude Code hook payload as JSON on stdin:

- **gate** looks for `"draft"` / `"text"` / `"prompt"` (a string), else falls back to the transcript (below). `"evidence"` — a list of file paths — is **required** for gate to actually run; without it there is nothing to check the draft against, so it fails open (exit 0, silent) rather than running the wrapped tool with zero evidence, which would exit on its own usage error and get misread as a block.
- **verify** looks for `"report"` / `"text"` / `"message"`, else the same transcript fallback, plus `"worktree"` if present.
- `"transcript_path"` (real Claude Code Stop/PostToolUse payloads carry this, not a direct text field) is read as a Claude Code transcript JSONL and the most recent assistant message becomes the text.

**Fail-open, always.** Empty stdin, non-JSON stdin, a payload with no usable text field, a gate payload with no evidence, or any unexpected exception during the run — every one of these exits 0 with nothing printed rather than blocking or crashing. Every one of them is still written to the call ledger, so a fail-open run is invisible to the session but not to an audit. `--map` accepts a mapping-profile name for forward compatibility; only `"default"` (the table above) exists today.

`hooks/` in this skill ships two example scripts, not installed into `~/.claude/settings.json` by this skill:

- **`hooks/stop-gate.sh`** — wires a Stop hook to `hook gate`, so nothing has to remember to gate a draft before the turn ends.
- **`hooks/posttooluse-verify.sh`** — wires a PostToolUse hook (matcher `Agent`) to `hook verify`, so a sub-agent's final report gets checked as soon as it lands.

Both are plain, executable bash; open either for the exact `settings.json` snippet that wires it in. Wiring them in is a deliberate, separate step — this skill ships the scripts, it does not touch your settings.

## The call ledger

Every invocation of `gate`, `verify`, `sweep` or `bench` — from the CLI, from `ask`, or from `hook` — appends one JSON line to `ledger/calls.jsonl` under this skill's own directory: `{ts, door, argv, exit_code, ms, json_mode, hook_mode}`. `hook`'s own fail-open decisions are logged too, with `door: "hook"` and a `note`.

```bash
python3 $S ledger          # last 20 calls, plus a per-door count
python3 $S ledger -n 100   # last 100
```

`status` shows the ledger path and today's call count. The ledger is local, plaintext, never contains a secret (the key itself is never in argv or output), and is gitignored — `ledger/` fills up with real use and is not meant to be committed.

## Bare-environment safety

`sweep` and `bench` need `npm`. A hook's shell is usually non-interactive and does not carry the PATH edit an interactive shell profile adds — on a machine where Node is nvm-managed, that means `npm` is often simply absent. Before shelling out, both doors resolve `npm` themselves: `shutil.which("npm")` first, then the newest version under `~/.nvm/versions/node/*/bin`, prepending its `bin/` to `PATH` if found. If neither works, the door refuses with one line and exits 1 — no Python traceback. Every path in this file is resolved from the skill file's own location or an explicit flag, never from the process's current directory; if `HOME` is not set at all in the environment, the wrapper refuses with one line and exits 1 rather than guessing paths.

## The one simple ask

```bash
S=~/.claude/skills/super-jev/superjev.py

python3 $S ask "does my draft notes.md log.txt draft.md actually hold up"
python3 $S ask "the worker says it is done, check report.md"
python3 $S ask "run every question over this pile"
python3 $S ask "is it safe to pay this automatically"
python3 $S ask "which skill should handle this"
```

**It always prints the command it would run before it runs anything.** Name real file paths in the sentence and it runs the door; leave them out and it prints exactly what is missing and stops. An unroutable sentence lists the doors instead of guessing.

**Routing is a keyword table, not a judgement.** Most keywords win, ties go to the door listed first, so *"send this draft reply"* is a `gate` and not a `permit`.

| say | you get |
|---|---|
| draft · reply · say · claim | `gate` |
| report · worker · done · pushed | `verify` |
| pile · records · every line · all of these | `sweep` |
| click · pay · send · delete · safe | `permit` |
| ticket · order · policy · chain | `chain` |
| which skill · tool · route | `fetch` |

## The 0.80 rule — it is the same rule everywhere

**Under 0.80, a person reads the source. That is the whole rule.**

Every door here inherits it from the claim-gate tool it wraps, unchanged. This skill sets no threshold of its own and never softens one.

| verdict | exit | what it means |
|---|---|---|
| CLEAN | 0 | every claim is carried by the evidence at or above 0.80 |
| READ | 3 | something is red or under the line — a human reads the source |
| REJECT | 4 (`verify`) / 2 (`gate`, fabricated quote) | the evidence disproves a claim, or the cited span is not in the file |

**Exit codes come from the wrapped door, untouched.** `5` is this wrapper refusing (missing input, missing door). `6` is NOT BUILT. Gate on the number, not on the prose.

**Report the real output.** The wrapped door's table is the result. "It passed" is not.

## What it refuses

**A `gate` with no draft and no claim.** There is nothing to check, and a silent empty run would look like a pass.

**A door that is not installed here.** If the claim-gate or report-verify tool behind `gate`/`verify` is missing and no `SUPERJEV_GATE_CMD`/`SUPERJEV_VERIFY_CMD` is set, it says so by path and by env var name rather than failing inside a subprocess.

**A checkout that does not carry the script.** `sweep` and `bench` need a real super-jev checkout, from `SUPERJEV_REPO` or this repo's own root. No directory, no `package.json`, or no such script and it refuses by name and tells you to point `SUPERJEV_REPO` at one. The sweep and bench scripts can live on their own branches, so a given checkout may well not have them — `status` tells you.

**A live `bench` with no `TYPESAFE_API_KEY` in the environment.** It prints the dry-run plan instead — the call count and the token estimate, no network, no cost — and exits 5 so nothing downstream reads it as a completed bench.

**Nothing here reads a secret file.** No `logins.md`, no `*-secret.md`, no credential file. The key is passed through from the environment to the child process and never printed; `status` says `yes` or `no` and nothing more. `SSL_CERT_FILE` passes through the same way.

**It re-implements no check.** Every judgement belongs to the door behind it. Fix a threshold, a question or a collector in that door, never here.

## Tests

    python3 -m pytest skills/super-jev/tests -q

Also runnable as `npm run test:skill` from the repo root. Fully offline: every door is replaced by a fake, `subprocess.run` is monkeypatched, so what is asserted is the exact argv this wrapper builds and the exit code it propagates. No test reaches TypeSafe, npm or git, and none needs a key.

## Related doors

- The claim-gate tool behind `SUPERJEV_GATE_CMD` — the check itself, the question batteries, and the owner of the 0.80 line. Not shipped in this repo; see **Install** above.
- The report-verify tool behind `SUPERJEV_VERIFY_CMD` — the evidence collectors and the CLEAN/READ/REJECT rule for a report. Not shipped in this repo; see **Install** above.
- [`docs/wishlist.md`](../../docs/wishlist.md) — the seven items. Items 4, 5 and 6 are the three doors above that print instead of running.
