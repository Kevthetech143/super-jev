# Claim-type → evidence-type coverage

Every door in super-jev, plus the fleet's local area doors that reuse the
same shape, answers a narrow set of claim types with a **free check** (a
local command or file read, zero model calls) before anything reaches a
judge. This page is the map: for each door, which claim types it can check,
what the free check actually does, the shape of the one-sentence derived
fact it produces, and — just as important — what it explicitly does **not**
cover yet.

No figures on this page. Accuracy, hit rates, and pass/fail counts belong in
each door's own `bench` output, never here — see `docs/playbook.md` §4 for
that discussion in words only.

## How to read each row

- **Claim types** — the kind of statement a draft or a worker report makes
  that this door can address at all.
- **Free check** — the deterministic, zero-model-call step that answers it:
  a command, a file read, a git query. If a door has none for a claim type,
  that claim type is judge-only or uncovered (see the NOT COVERED list).
- **Derived-fact sentence shape** — the plain-English sentence the free
  check produces, handed to the judge (or, when it flatly contradicts a
  claim, settled without a judge at all — see `playbook.md`'s "Frame
  evidence as derived facts first").
- **NOT COVERED YET** — claim types the door's own source does not attempt
  to check today. Anything in this list falls through to judge-only
  reasoning over raw evidence, or is entirely unchecked.

## Gate — `skills/super-jev/superjev.py` (`gate` subcommand)

The gate has no hard rules of its own (it never marks a claim
`CONTRADICTED_BY_FACT`); it prepends derived-fact **sentences** to the
evidence window the judge reads, computed from the session transcript.

| Claim types | Free check | Derived-fact sentence shape |
|---|---|---|
| "I removed/deleted X" | scans the transcript window for a later listing row still naming X | "X still present in [turn] after the claimed removal" |
| "the cadence/frequency of card X is now N" | locates the newest matching cadence line for X in the window | "as of [turn], the recorded cadence for X reads: ..." |
| "N of M passed/matched/agreed" | pools `N of M`-shaped matches from result tables in the window | one sentence per table population found |
| "PR #N merged" | scans the window for an actual merge receipt for that PR number | "no merge receipt for PR #N in window" (stated as an absence, not a contradiction) |

**NOT COVERED YET:** anything outside the current transcript window (the
gate only reads what is in the window it is given — it does not fetch a
cited file, run `gh pr view`, or call `git` itself); any claim with zero
tool evidence in the current turn (falls through to an "unchecked claims"
advisory, not a check); non-textual claims (a screenshot, a file's actual
content) — the gate only reasons over transcript text.

## Verify (coding) — `~/.claude/skills/worker-verify/verify.py`

The most mature door; every claim type below is answered by exactly one
kind of local evidence before a judge is ever asked (see `playbook.md`'s
"what answers it" table for the same list in narrative form).

| Claim types | Free check | Derived-fact sentence shape |
|---|---|---|
| test count / pass-fail count | the named test command's own run, executed fresh (`--test-cmd`) | "`<command>` reports N passing, M failing" |
| a path exists / has N lines | directory listing + `wc -l` on the exact path | "PATH exists, N lines" / "PATH MISSING" |
| a file is tracked / checked in / committed | `git ls-files` against the index, never "is on disk" | "PATH is tracked" / "PATH is untracked" |
| a commit exists (by hash) | the hash as a git object in the repo | "commit HASH exists" / "HASH unknown to this worktree" |
| a branch exists | local or remote-tracking ref lookup | "branch NAME exists" / "no such branch" |
| something was pushed | real ahead/behind count against upstream | "N commits ahead of upstream, not pushed" |
| a diff's size (files/insertions/deletions) | `git diff --shortstat` against the stated base | "diffstat: N files, +A/-D lines" |
| a PR's state or CI checks | `gh pr view` / `gh pr checks` (two separate questions) | "PR #N is OPEN/MERGED/CLOSED" / "checks: N passing, M failing" |
| a string/identifier appears in the code (or a private file) | `git grep` / `grep`, filtered through the shared blocklist and redactor (`atoms.py` section 0) before anything is returned | "N matches for TERM, blocklisted paths excluded" |

**NOT COVERED YET:** a live external validation of any kind (deployment
health, a running service, anything outside a local read-only command);
judgment claims about code quality/design (explicitly out of scope, judge
advisory only); claims whose only proof is in a different git worktree or
an unfetched remote (read as "unknown", not proven false — treat as
UNPROVABLE, not as evidence of a lie).

## Research reports — `.../areas-20260918/research/verify_research.py`

| Claim types | Free check | Derived-fact sentence shape |
|---|---|---|
| "report written to PATH" / "N lines" | the path literal near the claim, `wc -l` on that path | "PATH exists, N lines written" |
| a quote is present in a fetched source | a capped, read-only GET of the URL, then a literal search for the quoted text | "quote located in fetched body" / "quote not found in fetched body" |
| "N repos" (read/cloned/total) | counts subdirectories of the given root that are real git repos (`.git` present) | "N real git repos under ROOT" |
| a URL was actually fetched (not just cited) | greps the session transcript for a tool call naming that URL | "URL appears in a fetch/tool call in this transcript" / "no record of a fetch for URL in this transcript" |
| a fact in the report is labelled "unverified" (self-check) | searches the report file itself for the word or a stated synonym | "the word 'unverified' appears in REPORT-PATH" / "does not appear" |

**NOT COVERED YET:** whether the quoted text is used *accurately in
context* (only literal presence is checked, not meaning); a source fetched
outside this session's transcript (a sub-agent's or an earlier session's
fetch is invisible to the transcript grep); anything behind a login wall,
a cookie/consent wall, or a paywall (the fetch is anonymous, read-only,
and capped in size); the accuracy of a claim about a source's *content*
beyond one quoted span.

## Config edits + messages — `.../areas-20260918/config-messages/verify_config_msgs.py`

| Claim types | Free check | Derived-fact sentence shape |
|---|---|---|
| "I edited/changed CONFIG-FILE" | the file's existence and current content on disk, through the shared path blocklist | "CONFIG-FILE exists" / "CONFIG-FILE MISSING or blocklisted" |
| "a backup exists at BACKUP-PATH" | the backup file's existence and its relationship to the edited file | "BACKUP-PATH exists" / "MISSING" |
| "N lines added/changed" | a diff between the backup and the current file | "diff shows N added / M removed lines" |
| "key K changed from OLD to NEW" | reads the actual key (dotted-path lookup) out of the JSON/config file, compares both the raw string and a coerced-numeric form | "key K reads: VALUE" |
| the file is valid JSON | a parse attempt | "CONFIG-FILE parses as valid JSON" / "parse error" |
| "the edit happened at/after TIME" | the file's mtime compared to the claimed time | "CONFIG-FILE's mtime is T" |
| "I sent a message to RECIPIENT" | RECIPIENT's inbox/relay directory, matched by sender and rough time window | "a message from SENDER is present in RECIPIENT's inbox" (delivery only — see NOT COVERED) |

**NOT COVERED YET:** a **delivery receipt** for a sent message — the
inbox/relay check can only see what is currently queued; once a recipient
reads or the queue rotates, a genuinely delivered message leaves no trace,
so "not found in the inbox" is evidence of absence only when corroborated,
never proof the message was never sent (this door reports the finding as
advisory for that reason, not a hard block); message *content* accuracy
(only presence/absence of a matching entry is checked, and message bodies
are never forwarded to the judge — only id/from/to/timestamp/size/hash);
payments — no claim type or free check exists for "I paid X" or "the
ledger now shows Y" anywhere in this door or elsewhere in the repo; this
door has **no live-mode bench runner yet** (dry-run/offline only), so its
free-check coverage above is unconfirmed against a real judge call.

## Browser tasks — `.../areas-20260918/browser/verify_browser.py`

Reads a per-session action log (`actions.jsonl`: url, title, click/fill
targets, screenshot paths, timestamps) written by the fleet's `/web`
skill wrapper, when one exists for the claimed session.

| Claim types | Free check | Derived-fact sentence shape |
|---|---|---|
| "I navigated to URL" | the session's action log, matched by session and a time window | "navigation to URL IS recorded in the session log" / "not recorded" (advisory when no log exists for the session) |
| "the page title was TITLE" | the title recorded in the action log against the SAME claim's URL | "recorded title for URL: TITLE" |
| "I filled field F" | the action log's fill entries | "a fill action for F IS recorded" |
| "I took a screenshot at PATH" | the screenshot file's existence on disk (absolute paths only) | "PATH exists" / "PATH MISSING" |
| "I downloaded a file to PATH" | the downloaded file's existence on disk | "PATH exists" / "PATH MISSING" |

**NOT COVERED YET:** login/authenticated state (never pre-ruled — no free
check exists for "I was signed in as X"); anything about page *content*
beyond the recorded title (no DOM/body check); a session with no action
log at all falls back to advisory-only reasoning, not a hard check — this
is the door with the highest remaining share of advisory (non-hard-fact)
outcomes on true reports of any door in this table.

## Skill picking — `.../areas-20260918/skillpick/skillpick_facts.py`

The only door with no `CONTRADICTED_BY_FACT` verdict at all — it never
marks a claim false. Instead it decides, in code, whether a judge call is
needed before serving a skill.

| Decision types | Free check | What it settles |
|---|---|---|
| serve without a judge (R1) | the request contains one catalog skill's own unique trigger phrase and none of its negative phrases | serves that skill id, zero judge calls |
| demote a judge pick (R2) | the judge's own top-1 pick hits one of its own negative phrases | demotes to review, names the negative phrase that fired |
| declare no match (R3) | no catalog skill's trigger phrase appears anywhere in the request, and the judge's top-1 confidence (if any) is below the floor | reports noMatch instead of guessing |

**NOT COVERED YET:** any usage-prior or feedback signal (no record exists
today of which served pick was actually the right one, so R1/R2/R3 have
nothing to learn from over time); anything beyond trigger-phrase matching
— there is no fact-gatherer here for "the skill actually ran" or "the
skill produced the right output", only for "the right skill was served".

## Permits — `.../areas-20260918/permits/permit_facts.py`

Pre-judge, code-only rules that can settle `refuse` or escalate to
`needs_approval` with zero model calls; they can never settle a verdict to
`safe_to_auto` on their own.

| Claim/action types | Free check | What it settles |
|---|---|---|
| a destructive verb + target (`rm -rf`, force-push, `drop table`, `format <disk\|volume\|partition>`) | keyword/verb matching plus, with `--cwd`/`--worktree`, whether the target is a tracked, committed file recoverable from git's own index | refuses outright unless the target is git-recoverable, in which case that fact is attached and passed to the judge instead of a blind refuse |
| a money amount over the threshold, or a new payee | a fixed default threshold ($200) and a `--new-payee` flag | always escalates to `needs_approval`, zero model calls either way |
| whether a target is inside the task's own worktree | `--worktree` scope comparison | feeds the destructive-verb rule above; not a standalone verdict |
| a branch's existence, for a branch-targeting action | local/remote-tracking ref lookup | feeds the reversibility read for that action |

**NOT COVERED YET:** any claim-type checking of what actually happened
*after* an action ran — this door only judges "is this safe to do", not
"was this payment/deletion/send real and correct after the fact" (that is
the report-verification job, and no payment-specific fact-gatherer exists
anywhere in this repo yet); over-cautious false escalations on ambiguous
keyword hits are a known open risk, not yet resolved by a fix.

## Shared atoms library — `.../areas-20260918/atoms/atoms.py`

A generic `PreRule` engine (identity check, then a pure fact comparison,
deferring rather than guessing) that the area doors above build on. Four
rules are wired today:

| Claim types | Free check |
|---|---|
| a file's line count | exact path-string match, then `wc -l` |
| a path exists | resolved path lookup (absolute paths only, by design) |
| a commit hash is known | git object lookup |
| a branch exists | local/remote-tracking ref lookup |

Also carries the **shared secrets guard** (`is_blocked_path` / `redact`,
section 0) that every door above now calls before a path is opened or text
enters an evidence block sent to the judge — this is infrastructure, not a
claim-type check, but it gates every row in this page.

Also carries **declined-check-facts** (`PreRule.check`, `check_rules`,
`render_declined_fact`): when a pre-rule's identity matches but the
comparator finds no conflict — a tolerance clears a gap, a claimed count
matches the counted one, a labelling check confirms what was claimed — the
outcome now renders as its own DERIVED FACT sentence ("A declined check
must still speak," see `playbook.md` §4) instead of being indistinguishable
from a check that never ran at all. All four rules above carry it; door-level
wiring (calling `check_rules` instead of `apply_rules` and threading the
result through to the judge's evidence) is separate follow-up work, not
done in this pass.

**NOT COVERED YET:** anything beyond these four atom types — quotes,
counts-with-units pairing, and message/config-specific checks all live in
each area door's own file, not in the shared library yet, so a new area
door has to re-derive them rather than reuse a shared implementation. None
of the area doors above (verify, research, config+messages, browser,
skillpick, permits) call `check_rules` yet — their own tolerance/policy
checks (an mtime gap, a repo count, an unverified label) are not yet wired
to speak when declined.

## Cross-door NOT COVERED, at a glance

These claim types have **no free check anywhere in the repo today**:

- Messages sent, with a real delivery receipt (only a queue-snapshot check
  exists, and it is advisory-only by design — see the config+messages
  section above).
- Payments of any kind — no fact-gatherer, no bench, no door.
- Live external validation of any action's real-world effect (a deployed
  service's health, a portal's post-submit state, anything outside a local
  read-only command or a recorded session log).
- Judgment/quality claims (code quality, design taste, writing quality) —
  deliberately out of scope everywhere; these stay judge-only advisory, by
  design, not by gap.
- A usage-prior or outcome-feedback loop for skill picking (was the served
  skill actually correct in hindsight).
