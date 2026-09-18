# The hooks: what they do, what they cost, and when not to install them

`skills/super-jev/hooks/` holds three shell scripts. Each one wires a Claude
Code hook event to `superjev.py hook`, which checks what an agent is about to
say, or what a sub-agent just reported, against evidence a machine collected.
Nothing here is installed for you. You copy a snippet into your own
`settings.json`, and you can remove it the same way.

This page is written for someone who has not used them. It is honest about the
downsides, because a checker that blocks true statements costs more than it
saves.

## The three hooks

**Stop → `hook gate`.** When the agent finishes a turn, Claude Code hands the
hook the final assistant message. The gate treats that message as a draft,
derives evidence from the transcript itself (the tool results of the last
several tool calls in the turn), strips fleet machine tags out of the draft so
bookkeeping lines are not judged as content, and asks the claim gate whether
each claim in the reply is carried by that evidence. A fabricated quote is
caught for free by a string match before any model call. If the turn ran no
tools at all there is no evidence to check against, so the gate prints one
advisory line and always allows.

**PostToolUse on the Agent tool → `hook verify`.** When a sub-agent's tool call
returns, this checks the returned text as a worker report against machine
evidence: git state, the diff, a listing of every path the report names, the
pull-request state, and a comment-stripped grep. It skips outright when the
returned text is only a launch acknowledgement, or a spawn dictionary carrying
the brief rather than a report, because judging a brief as if it were a report
is a category error that produced false blocks.

**UserPromptSubmit → `hook prompt-verify`.** A background sub-agent's real
final report never arrives through PostToolUse. It lands later, inside the next
user turn, as a teammate-message block. This hook reads those blocks, derives
the worktree, pull request and test command from the report's own text, and
verifies each one. It is advisory only and never blocks, because blocking a
user's prompt is not a recoverable state.

## Wiring it in

Copy this into `~/.claude/settings.json`, with absolute paths, and set
`SUPERJEV_GATE_CMD` and `SUPERJEV_VERIFY_CMD` to your own claim-gate and
report-verify tools (see `skills/super-jev/SKILL.md`). With those unset the
hooks fail open and do nothing, rather than blocking on a missing dependency.

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command",
          "command": "/abs/path/skills/super-jev/hooks/stop-gate.sh" } ] }
    ],
    "PostToolUse": [
      { "matcher": "Agent",
        "hooks": [ { "type": "command",
          "command": "/abs/path/skills/super-jev/hooks/posttooluse-verify.sh" } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command",
          "command": "/abs/path/skills/super-jev/hooks/userpromptsubmit-verify.sh" } ] }
    ]
  }
}
```

Start with PostToolUse only. It is the hook with the clearest payoff and the
one that cannot interrupt you mid-sentence.

## Block versus advisory

**Advisory** means the hook exits 0 and prints one line. The agent sees the
line in its own context and can act on it. Nothing is prevented. Most findings
are advisories, and that is the intended resting state.

**Block** means the hook exits 2 with a reason on stderr. On Stop, the turn
does not end and the agent is asked to try again. On PostToolUse, the lead
agent is told the report did not check out.

A block needs one of three things: a fabricated quote, or a claim the judge
flagged not-supported or contradicted with high confidence, or a confident
overclaim sitting next to a claim the judge is at least fairly confident is
not supported or contradicted. Self-contradiction never blocks, alone or in
company. None of these fire on evidence too thin to judge against — no
evidence source, a directory-level test command worker-verify refuses
outright, a `--pr` block that carries no check-run data, or a gather under the
char floor all turn every one of them into an advisory instead, because a
confident-but-ungrounded verdict and a real problem print the identical red
table. `hook verify --from-file --explain` prints the gather size, every claim
row and which rule decided.

## The loop guard

A Stop hook that can block can also refuse to let a turn ever end. That
happened: one short reply blocked three times running while the reply was
rewritten more carefully each pass, because a calmer rewrite still reads as
mildly self-contradictory. Two guards exist now. Self-contradiction alone is
not a block reason. And when Claude Code sets `stop_hook_active`, meaning this
Stop event is already a re-run after a block, the gate never blocks again on
that pass. It prints what it would have blocked on and allows. The ledger line
records this plainly as a fail-open, not as an approval.

## The ledger

Every call appends one JSON line: timestamp, which door, the exit code, how
long it took, and the flags behind any block or advisory. `superjev.py ledger`
prints recent calls and per-door counts. Read it before you trust any claim on
this page, including ours. It is the only record that distinguishes "the gate
approved this" from "the gate could not look".

## What it costs

Evidence collection is local and free: git, a directory listing, a grep, and
optionally your test command and a `gh` call. The judging step is one paid
model call per check. The gate adds well under a second to the end of a turn,
fast enough that you will not notice it. A report verify is slower, a few
seconds, because it gathers evidence first and sends much more of it. On a
session with many sub-agent spawns those calls add up, and the verify door is
the expensive one. Both doors have timeouts and both fail open on timeout.

## Failure modes, honestly

**False alarms on true reports are the main cost.** On the last live bench, the
judge caught every single fabricated report. On true reports it was clean only
half the time, flagged four more as needing a human, and hard-rejected one that
was entirely true. Almost all of that noise comes from one place: test-count
claims. The evidence for a test count is either missing or ambiguous more often
than any other kind.

**The gate cannot tell "you lied" from "I could not look".** This is the
deepest problem. A missing evidence block and a false claim produce nearly the
same verdict table. Two specific gaps caused real false blocks. A
directory-level pytest is refused outright by the evidence collector, for a
good reason, so a test count checked that way has nothing behind it. And the
pull-request evidence is state only. It carries no check-run data at all, so
"CI is green" is unprovable no matter how green it is. The precision fix makes
those cases advisory instead of blocking, but it cannot invent the evidence.

**Decided: the block rule follows confidence upward, not downward.** The number
beside a verdict is the judge's confidence in that verdict, not a measure of
support — a claim marked unsupported at very high confidence is the strongest
signal the judge can give, and the block rule now follows it upward instead of
firing on the judge's least confident findings and letting its most confident
ones through. The trade-off that comes with fixing the direction: the same
number that used to block on a low-confidence finding no longer does, so a
claim the judge only barely suspects is unsupported now reads as an advisory,
not a block, until a human reads it. That softening is deliberate and lives
next to the fix, not hidden by it — the fixture from the earliest false-alarm
writeup on this page (a claim marked unsupported at low confidence, alongside
a confident overclaim) is kept in the test suite specifically to show it now
passes as advisory rather than blocking, so the trade is visible, not silent.
The same confidence gate that decides a block also turns off entirely when the
evidence gather itself was too thin to judge against, for exactly the same
reason a low-confidence claim does not block: a confident-sounding verdict
built on no evidence is not a confident finding.

**Other things a lie can still do.** A reply that makes claims with no tool
calls in the turn is never checked, only noted. Every hook fails open on a
timeout, a crash, an unreadable payload or a missing door, so anything that
makes the checker unavailable makes the session unchecked. The report-verify
path driven by a hook never passes a test command, so on that path a test count
can never be proved. And the evidence collector's refusal of directory-level
test runs is bypassed by wrapping the same command in an npm script.

**One vendor, today.** The judge is a single external provider. The doors are
swappable by design through two environment variables, and swapping them is
the intended escape hatch, but on this machine today there is one dependency
and no fallback judge.

**Offline, it does nothing.** No key or no network means the judging step
fails, and every hook fails open. You get a session with no checking and no
loud warning that checking stopped. The ledger is where you find out.

**Claim splitting is approximate.** When you do not pass claims explicitly, the
draft is split automatically. Fragments under a few words are dropped, machine
tags are dropped, and a long sentence carrying two assertions becomes one
claim that can only get one verdict. A claim that splits badly gets judged
badly.

**Ordering and timeouts.** Hooks on the same event run together and the
strictest exit code wins, so another hook can block for reasons that have
nothing to do with this one. The gate and verify timeouts are separate, and a
slow test command inside a verify eats the verify budget first.

**Privacy.** This is the one to think about before installing. The draft reply,
or the worker report, plus the assembled evidence, leaves the machine on every
judged call. That evidence is machine-collected and can include file contents,
diffs, test output and repository paths you did not choose by hand. A string
match catches fabricated quotes before any call, and no API key is ever
printed, but nothing redacts the evidence itself.

## When not to install this

- Any repository whose contents must not leave the machine. The evidence goes
  to an external judge.
- Work with no checkable claims. Design conversations, brainstorming and
  writing produce advisories about nothing.
- A session where an interrupted turn is worse than an unverified sentence.
  Use PostToolUse alone, or advisory mode only.
- Offline or air-gapped work, where every hook fails open and you gain nothing
  but latency.
- Test-count-heavy reporting, until the test-output evidence path is fixed.
  That single evidence kind is behind most of the false alarms measured so far.
- As a substitute for reading the work. The judge advises. It never decides,
  and on true reports it is wrong often enough that a block is a reason to
  look, not a verdict.
