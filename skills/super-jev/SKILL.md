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

**A door that is not built exits 6 and prints its own wishlist line.** That is the point: a missing tool names itself, in the wishlist's own words, instead of a vague failure. Never hand-write "not supported" — run it and read what it says.

**`status` is the only honest answer to "does this work here?"** It reads the doors off disk, so a checkout without the sweep script shows `MISSING SCRIPT` rather than a promise.

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
