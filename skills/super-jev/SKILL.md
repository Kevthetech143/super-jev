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

**Launch acknowledgements are not reports — `verify` skips them before calling the door.** A background `Agent`/`Task` spawn's `tool_response` is sometimes only a launch ack ("Spawned successfully... the agent is now running", "Async agent launched... you will be notified automatically when it completes"), not the sub-agent's actual final report — running `verify` against that text is a category error, and jev's own OVERCLAIMS scoring has been seen to score it as a confident, unsupported claim and block it (the 2026-09-17 false alarm this fixes). Before calling the wrapped verify tool, the REPORT text is classified: if it contains one of a fixed set of ack/launch phrases ("spawned successfully", "async agent launched", "agent is now running", "will be notified", "resumed agent", "idle_notification" — override the whole list with a comma-separated `SUPERJEV_HOOK_ACK_PATTERNS`), or if it is shorter than `SUPERJEV_VERIFY_MIN_CHARS` (default 200 characters) **and** carries none of a real report's own markers (a `COMPLETE`/`INCOMPLETE`/`verdict` word, or a test count like "4 tests passed"), the door is never called: exit 0, nothing on stdout, and a ledger line with `skipped: true`, `reason: "launch-ack"`. A short reply that *does* carry a report marker (e.g. `"COMPLETE: 4 tests passed"`) still runs normally — the length check only fires when there is nothing report-shaped in the text at all.

**A background spawn's `tool_response` is a DICT — `verify` reads it narrowly, never stringifies it.** The live shape (captured 2026-09-17 on a real background `Agent` spawn): `tool_response` is `{"status": "teammate_spawned", "prompt": "<the whole worker brief>", ...}`. Stringifying that dict and judging it as a report is exactly the bug this fixes — the brief itself got read as an OVERCLAIMS-flagged report and blocked. Now a dict `tool_response` is read for only `result` / `report` / `content` / `text` (in that order; each resolved the same best-effort way as a string/list/dict); `"prompt"` is never read as a report. If none of those four keys carry usable text, and either `status` names a known spawn/launch state (`teammate_spawned`, `launched`, `running`, `spawned` — case-insensitive) or none of the four keys are present at all, the door is never called: exit 0, ledger `skipped: true`, `reason: "spawn-dict"`. **The worker's real report is not lost — it just isn't here yet.** It lands later, inside the next USER turn, as a `<teammate-message teammate_id="..." ...>...</teammate-message>` block (Claude Code's own teammate-mailbox rendering), which `PostToolUse` never sees at all — see `hook prompt-verify`, below, for the door that does.

**Fail-open, always, and never silently.** Empty stdin, non-JSON stdin, a payload with no usable text, no derivable evidence, a `tool_name` that is not `Agent`, a launch-ack `tool_response`, a spawn-dict `tool_response`, a subprocess timeout, a bad hook invocation (e.g. a typo'd `--door`), or any unexpected exception during the run — every one of these exits 0 rather than blocking or crashing, and every one writes a ledger line (`skipped: true`, with the reason in `note`, and a short machine-matchable `reason` tag when one applies) — there is no silent skip. `--map` was removed: it was accepted and never read.

**`hook verify --from-file <report.txt>`** runs the identical verify check against a report file that arrived out of band — a worker's final report message the lead is holding, rather than a real `PostToolUse` hook payload on stdin. It bypasses stdin entirely (no JSON payload needed) and the launch-ack pre-check (a caller passing `--from-file` has already decided the file is a report worth checking), prints the exact same one-line allow/advisory/block verdict, and writes the same shape of ledger line except `hook_mode: false` and `source: "manual"`, so a manual check is distinguishable from a real hook firing:

```bash
python3 $S hook verify --from-file /path/to/worker-final-report.txt --worktree /path/to/worktree
```

**`--from-file` gathers its own evidence — `--worktree <dir>`, `--test-cmd "<cmd>"`, `--pr <n>`.** A manual check has no `PostToolUse` payload to derive a worktree or a test command from, so these three flags pass straight through: `--worktree` and `--test-cmd` are worker-verify's own real flags (`FLEET_VERIFY_PY`'s argparse), unchanged. `--pr <n>` has no equivalent worker-verify flag — a bare PR number in report text is not enough for worker-verify's own atom extraction to produce a `gh pr view` evidence block, only a full GitHub pull URL is — so `--pr` is resolved to one via `git -C <worktree> remote get-url origin` and appended to the report text before worker-verify ever sees it (which is why `--pr` needs `--worktree` to do anything). **With none of the three given — and `SUPERJEV_HOOK_WORKTREE` not set either — there is no evidence source at all: the door is never called, one advisory line prints ("no evidence source given; run with --worktree/--test-cmd/--pr"), and the ledger logs `skipped: true`, `reason: "from-file-no-evidence"`.** This never blocks — a missing evidence source is advisory only, the same as everything else on this page.

**`hook prompt-verify`** is the door that actually sees a background spawn's real, final report. `PostToolUse` never does — see the spawn-dict paragraph above — because the report lands later, inside the next USER turn, rendered by Claude Code's own teammate mailbox as one or more `<teammate-message teammate_id="..." ...>...</teammate-message>` blocks. Wired to `UserPromptSubmit`, it reads `payload["prompt"]`, finds every teammate-message block in it, and for each one carrying a report marker (a `COMPLETE`/`INCOMPLETE` word, a `PR #N`/`pull/N` mention, or a test count) runs `verify` against it — with evidence **auto-derived from the report's own text**: the first absolute path mentioned becomes `--worktree`; a `PR #N` / `pull/N` mention becomes `--pr` (resolved to a `gh pr view` URL the same way `--from-file --pr` does it); an `npm test` / `pytest` phrase becomes `--test-cmd`, but *only* when a worktree was also found (a bare test command with no repo to run it in is not evidence). Prints one line per report checked, to stdout:

```
super-jev verify <teammate_id>: CLEAN|READ|REJECT — <flags> (<derived flags used>)
```

and writes one ledger line per report, `source: "teammate-message"`. **`prompt-verify` never blocks, under any circumstance, including an unexpected exception — always exit 0.** A `UserPromptSubmit` hook that blocks eats Kelvin's own next message along with the check result, so this is advisory-only by construction, not just by convention. A block with no report marker, a prompt with no teammate-message blocks at all, empty stdin, or non-JSON stdin all print nothing and log one skipped ledger line.

```bash
echo '{"prompt": "... <teammate-message teammate_id=\"X\">COMPLETE: ...</teammate-message> ..."}' \
  | python3 $S hook prompt-verify
```

An example hook script, `hooks/userpromptsubmit-verify.sh`, wires this to `UserPromptSubmit` — see **Wiring** below.

**Subprocess timeouts.** `gate` and `verify` bound the wrapped tool's runtime — default 90s for gate (`SUPERJEV_GATE_TIMEOUT`), 300s for verify (`SUPERJEV_VERIFY_TIMEOUT`). A timeout never raises: it returns exit code 124, which is not in the hook's allow/block table, so it folds into the same fail-open advisory (exit 0) as everything else above.

**No fd-level leak.** In `hook` mode the wrapped door's subprocess is always run with `capture_output=True` — never with the child's stdout/stderr inherited straight onto this process's own fd 1/2 — so a chatty wrapped tool's own table never escapes onto the hook's real stdout; only this shim's own single advisory/block line (or nothing, on a silent allow or a silent fail-open) is ever printed.

**Strong-flag block override.** A `gate`/`verify` run that comes back READ (exit 3, "advisory") can still carry a claim- or draft-level flag red enough that letting it through as a silent advisory is a bug: `NOT_SUPPORTED` at or below `SUPERJEV_BLOCK_NOT_SUPPORTED` (default 0.20) and `OVERCLAIMS` at or above `SUPERJEV_BLOCK_OVERCLAIM` (default 0.80) each block on their own. `SELF_CONTRADICTORY` at or below 0.30 does **not** block on its own — it only counts as a block reason when the same run also carries a blocking `NOT_SUPPORTED` or `OVERCLAIMS` flag. This was a fix in itself: the 2026-09-17 live finding was the same short Stop reply blocking three times running while the lead rewrote it more carefully each pass — the actual overclaim problem was fixed by the second rewrite (0.87 → 0.25), but a mild self-contradiction score alone (0.10 → 0.15 → 0.05) kept blocking every pass, because self-contradiction used to block by itself.

**Machine-tag stripping.** Before a `gate` draft is checked, fleet bookkeeping is stripped out of it: a trailing `[BOARD: ...]` line, `Rung:`/`Rung line:` lines, `[Add] [Skip]` buttons, and any `<...>` system tag. These are not part of the reply's own content, but the gate's `leaked_internal`/`self_contradictory` scoring has read them as internal leakage or a contradiction. Override the pattern list with `SUPERJEV_STRIP_PATTERNS` (a JSON list of regex strings) if your fleet's tag vocabulary differs.

**`stop_hook_active` loop guard.** Claude Code sets `stop_hook_active: true` on a Stop payload when this is a re-run — a prior Stop hook already blocked this turn once. `hook gate` never blocks on that re-run, regardless of flags: it prints one advisory line naming what it would have blocked on and exits 0. Without this, a gate and a rewritten reply that keep disagreeing can loop the session forever — this is exactly what happened in the 2026-09-17 finding above before the strong-flag fix, and it is possible again any time the gate and a hand-edited reply disagree twice in a row.

`hooks/` in this skill ships three example scripts, not installed into `~/.claude/settings.json` by this skill:

- **`hooks/stop-gate.sh`** — wires a Stop hook to `hook gate`, so nothing has to remember to gate a draft before the turn ends.
- **`hooks/posttooluse-verify.sh`** — wires a PostToolUse hook (matcher `Agent`) to `hook verify`, so a sub-agent's final report gets checked as soon as it lands. For a *foreground* Agent call this is the whole story; for a *background* spawn, `tool_response` is only a launch ack or a spawn dict (see above) and this hook always skips — pair it with the next one.
- **`hooks/userpromptsubmit-verify.sh`** — wires a `UserPromptSubmit` hook to `hook prompt-verify`, so a background spawn's real report — the `<teammate-message>` block that lands in the next user turn, which `PostToolUse` never sees — gets checked too.

All three are plain, executable bash; open any of them for the exact `settings.json` snippet that wires it in. Wiring them in is a deliberate, separate step — this skill ships the scripts, it does not touch your settings.

## The call ledger

Every invocation of `gate`, `verify`, `sweep` or `bench` — from the CLI, from `ask`, or from `hook` — appends one JSON line to `ledger/calls.jsonl` under this skill's own directory (override the path with `SUPERJEV_LEDGER`): `{ts, door, argv, exit_code, ms, json_mode, hook_mode}`. `hook`'s own decisions are logged too, with `door: "hook"`, a `note`, and `skipped` (`true` when the wrapped tool never ran at all — a fail-open path, including a launch-ack skip — `false` when it actually ran and this is its allow/advisory/block outcome); `exit_code` on a `hook` line is the real code that invocation returned (0 or 2), never hard-coded. A skip carries a short `reason` tag (e.g. `"launch-ack"`) alongside the free-text `note`. A `hook verify --from-file` run logs `hook_mode: false` and `source: "manual"` instead of the usual `hook_mode: true`, so a manual check is distinguishable from a real hook firing. If the ledger path itself is unwritable, one line goes to stderr rather than dropping the record in total silence.

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
