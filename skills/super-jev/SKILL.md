---
name: super-jev
description: "The ONE front door for every check we run — gate a draft against its evidence, verify a worker's report, sweep a pile bigger than one call, fetch the top few catalog entries for a request, and run the live bench. Thin wrappers over a claim-gate tool, a report-verify tool, and the super-jev harness, never a second copy of them. A door whose tool is missing names its own wishlist item and exits 6, so a missing tool tells you what is missing. `ask \"<one plain sentence>\"` routes a request to the right door with no model call. Triggers: /super-jev, check this before I send it, is this report true, run every question over this pile, which skill handles this, super jev"
type: procedure
---

# /super-jev — one simple ask, and the right check runs

**In one sentence: you say what you want checked in plain words, and this picks the door, runs it, and hands you one verdict line.**

*"The skill becomes the thing we steer in between us and the data: one simple ask and super jev handles it."*

Before this, the checks were scattered across two skills and a repo on four branches. A fresh session had to remember four places. This is the one place.

This copy lives in `skills/super-jev/` in the [super-jev](../../README.md) repo and is portable out of the box: `sweep`, `fetch` and `bench` look for their npm scripts in this checkout by default (override with `SUPERJEV_REPO`), and `gate`/`verify` wrap whatever claim-gate and report-verify tools you point them at (see **Install**, below). A clone with no env set at all still runs `status`, `ask`, `sweep`, `fetch` and `bench`; `gate` and `verify` need two tools this repo does not ship — see **Related doors** at the bottom.

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

`SUPERJEV_REPO` points `sweep`, `fetch` and `bench` at a super-jev checkout
carrying those npm scripts; it defaults to this repo's own root, so those
three doors need no env at all when the skill is used from inside a clone.

## The doors

| door | what it answers | state | who actually runs it |
|---|---|---|---|
| `gate <evidence...> --draft <file>` or `--claim "..."` | does my draft actually follow from the files I read | **LIVE**, needs a claim-gate tool | `SUPERJEV_GATE_CMD` |
| `verify <report> [--worktree P] [--test-cmd C] [--paths ...]` | is this worker's "done" true | **LIVE**, needs a report-verify tool | `SUPERJEV_VERIFY_CMD` |
| `sweep <records.jsonl> --questions <q.json> --out <dir>` | every question of every record, with proof nothing was skipped | **LIVE** | `npm run sweep` in the harness |
| `fetch "<request>" --catalog <catalog.json> [--prefilter N]` | which few entries in the catalog actually serve this request; `noMatch: true` when none does | **LIVE** (experimental) | `npm run fetch` in the harness |
| `bench [--dry-run] [--stub]` | how good is the harness, measured | **LIVE** | `npm run bench:live` |
| `permit <snapshot> --action "..."` | is it safe to click / pay / send / delete automatically | **NOT BUILT** — wishlist item 5 | — |
| `chain <spec.json> <docs...>` | is the ticket-to-order-to-policy chain complete | **NOT BUILT** — wishlist item 4 | — |
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

It never exits 3, 4 or 5 — those are `gate`'s and `verify`'s own exit codes and mean nothing to a hook runner; they are folded into advisory (0) or block (2) above.

**The real payload fields, verified against the Claude Code hooks docs — not assumed.** A real `Stop` payload never carries a `"draft"` or `"evidence"` key; a real `PostToolUse` payload never carries a `"report"` key. This shim reads the fields that actually exist:

- **gate** (wired to `Stop`): the DRAFT is `payload["last_assistant_message"]` — the field Claude Code hands a `Stop`/`SubagentStop` hook specifically so it does not have to re-parse a possibly-stale transcript for the current turn's text — else `"draft"` / `"text"` / `"prompt"` (a string, for a caller building its own smaller payload), else the last assistant message read out of `payload["transcript_path"]` (every real `Stop` payload carries this too: `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `stop_hook_active`).
  EVIDENCE is `payload["evidence"]` if a caller supplied one (back-compat only — a real `Stop` payload never has this key), else it is **derived from the transcript itself**: the `tool_result` content of the last `N` tool calls found in `transcript_path` (`N` = `SUPERJEV_HOOK_EVIDENCE_N`, default 8; total size capped by `SUPERJEV_HOOK_EVIDENCE_MAX_BYTES`, default 50000 bytes), written to one temp file. If neither an explicit evidence list nor any derivable `tool_result` content exists, there is nothing to check the draft against — fails open (exit 0, silent) rather than running the wrapped tool with zero evidence, which would exit on its own usage error and get misread as a block.
- **verify** (wired to `PostToolUse`, matcher `Agent`): if `payload["tool_name"]` is present and is not `"Agent"`, this event is not a sub-agent report — fails open (this hook is only meaningful on an Agent/Task tool call). The REPORT is `payload["tool_response"]` (the real field a `PostToolUse` payload carries — the tool's own output; text is pulled out best-effort from a string, a list of `{"type":"text"}` blocks, or a dict), else `"report"` / `"text"` / `"message"` (back-compat), else the transcript fallback. The worktree is `payload["worktree"]` if a caller supplied one, else `SUPERJEV_HOOK_WORKTREE` from the environment, else none — **never** derived from `payload["cwd"]`, which describes the lead session's directory, not necessarily the worker's tree.

**Fail-open, always, and never silently.** Empty stdin, non-JSON stdin, a payload with no usable text, no derivable evidence, a `tool_name` that is not `Agent`, a subprocess timeout, a bad hook invocation (e.g. a typo'd `--door`), or any unexpected exception during the run — every one of these exits 0 rather than blocking or crashing, and every one writes a ledger line (`skipped: true`, with the reason in `note`) — there is no silent skip. `--map` was removed: it was accepted and never read.

**Subprocess timeouts.** `gate` and `verify` bound the wrapped tool's runtime — default 90s for gate (`SUPERJEV_GATE_TIMEOUT`), 300s for verify (`SUPERJEV_VERIFY_TIMEOUT`). A timeout never raises: it returns exit code 124, which is not in the hook's allow/block table, so it folds into the same fail-open advisory (exit 0) as everything else above.

**No fd-level leak.** In `hook` mode the wrapped door's subprocess is always run with `capture_output=True` — never with the child's stdout/stderr inherited straight onto this process's own fd 1/2 — so a chatty wrapped tool's own table never escapes onto the hook's real stdout; only this shim's own single advisory/block line (or nothing, on a silent allow or a silent fail-open) is ever printed.

`hooks/` in this skill ships two example scripts, not installed into `~/.claude/settings.json` by this skill:

- **`hooks/stop-gate.sh`** — wires a Stop hook to `hook gate`, so nothing has to remember to gate a draft before the turn ends.
- **`hooks/posttooluse-verify.sh`** — wires a PostToolUse hook (matcher `Agent`) to `hook verify`, so a sub-agent's final report gets checked as soon as it lands.

Both are plain, executable bash; open either for the exact `settings.json` snippet that wires it in. Wiring them in is a deliberate, separate step — this skill ships the scripts, it does not touch your settings.

## The call ledger

Every invocation of `gate`, `verify`, `sweep` or `bench` — from the CLI, from `ask`, or from `hook` — appends one JSON line to `ledger/calls.jsonl` under this skill's own directory (override the path with `SUPERJEV_LEDGER`): `{ts, door, argv, exit_code, ms, json_mode, hook_mode}`. `hook`'s own decisions are logged too, with `door: "hook"`, a `note`, and `skipped` (`true` when the wrapped tool never ran at all — a fail-open path — `false` when it actually ran and this is its allow/advisory/block outcome); `exit_code` on a `hook` line is the real code that invocation returned (0 or 2), never hard-coded. If the ledger path itself is unwritable, one line goes to stderr rather than dropping the record in total silence.

```bash
python3 $S ledger          # last 20 calls, plus a per-door count
python3 $S ledger -n 100   # last 100
```

`status` shows the ledger path and today's call count. The ledger is local, plaintext, never contains a secret (the key itself is never in argv or output), and is gitignored — `ledger/` fills up with real use and is not meant to be committed.

## Bare-environment safety

`sweep` and `bench` need `npm`. A hook's shell is usually non-interactive and does not carry the PATH edit an interactive shell profile adds — on a machine where Node is nvm-managed, that means `npm` is often simply absent. Before shelling out, both doors resolve `npm` themselves: `shutil.which("npm")` first, then the newest version under `~/.nvm/versions/node/*/bin` — sorted by actual semantic version, not by directory name as a string, so a stale `v9.x` never shadows a real `v24.x` — prepending its `bin/` to `PATH` if found. If neither works, the door refuses with one line and exits 1 — no Python traceback. Every path in this file is resolved from the skill file's own location or an explicit flag, never from the process's current directory; if `HOME` is not set at all in the environment, the wrapper refuses with one line and exits 1 rather than guessing paths.

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

**A checkout that does not carry the script.** `sweep`, `fetch` and `bench` need a real super-jev checkout, from `SUPERJEV_REPO` or this repo's own root. No directory, no `package.json`, or no such script and it refuses by name and tells you to point `SUPERJEV_REPO` at one. These scripts can live on their own branches, so a given checkout may well not have them — `status` tells you.

**A live `bench` with no `TYPESAFE_API_KEY` in the environment.** It prints the dry-run plan instead — the call count and the token estimate, no network, no cost — and exits 5 so nothing downstream reads it as a completed bench.

**Nothing here reads a secret file.** No `logins.md`, no `*-secret.md`, no credential file. The key is passed through from the environment to the child process and never printed; `status` says `yes` or `no` and nothing more. `SSL_CERT_FILE` passes through the same way.

**It re-implements no check.** Every judgement belongs to the door behind it. Fix a threshold, a question or a collector in that door, never here.

## Tests

    python3 -m pytest skills/super-jev/tests -q

Also runnable as `npm run test:skill` from the repo root. Fully offline: every door is replaced by a fake, `subprocess.run` is monkeypatched, so what is asserted is the exact argv this wrapper builds and the exit code it propagates. No test reaches TypeSafe, npm or git, and none needs a key.

## Related doors

- The claim-gate tool behind `SUPERJEV_GATE_CMD` — the check itself, the question batteries, and the owner of the 0.80 line. Not shipped in this repo; see **Install** above.
- The report-verify tool behind `SUPERJEV_VERIFY_CMD` — the evidence collectors and the CLEAN/READ/REJECT rule for a report. Not shipped in this repo; see **Install** above.
- [`docs/wishlist.md`](../../docs/wishlist.md) — the seven items. Items 4, 5 and 6 (`chain`, `permit`, `fetch`) are built; `fetch` is experimental until its own admission gate is met.
