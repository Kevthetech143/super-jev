# Changelog

## Unreleased

No version bump.

- **Ask: hits print before pointer errors.** Fixed `ask.py` so a miss that
  finds real candidates on some pointers, while another pointer errors, now
  always shows those candidates first, followed by the per-pointer error
  lines, then the unresolved summary. Previously an error on any pointer
  printed the unresolved summary and returned before the merged candidates
  from healthy pointers were ever shown, hiding a real hit behind an
  unrelated pointer's failure. The exit code is unchanged: 1 whenever any
  pointer errored, 0 otherwise, with the no-candidates hint reserved for the
  case where every pointer answered cleanly and none had anything.
- **Bulk prepare: gated labels and a local list filter.** The writer now
  drafts four labels per file alongside the description — `kind`, `status`,
  `as_of`, `subject` — validated against a fixed enum locally (an
  out-of-enum value is coerced to `unknown`, never crashes the run) and
  folded into one claim gated per file, rather than a second, separate gate
  call. The connect description carries the labels in brackets so ranking
  sees them. `prepare_bulk.py --list --pointer NAME` filters the already-
  gated labels back out of the cache by status, kind, subject, or recency,
  with no writer, gate, or memory call — a label is only as true as the file
  it was drafted from, and `as_of` shows staleness, not current truth. Also
  fixed `connect_checked.gate()` to parse every verdict the gate's claim
  question can return, including `CONTRADICTED`, which previously fell
  through the verdict regex and was reported as a generic error instead of
  a real fail.
- **Ask: cache-first lookup loop.** Added `skills/super-jev/ask.py`, a front
  door that checks a new harness `cached` action before doing anything else,
  so a repeat question is answered with zero provider calls and without any
  local approved-answer map. On a miss it navigates every pointer the caller
  can see in parallel; a pointer that errors prints its own status line and
  the run exits with an unresolved count instead of being folded silently
  into "nothing found." `--approve` promotes a good search hit into a cached
  answer. `--add` records a fact with no file as its own small one-file
  manual pointer, so it never replaces or invalidates any other pointer's
  approved answers, and a later hit on a manual pointer warns if its
  recorded source file changed underneath it. `--miss` logs that the answer
  was found somewhere the loop didn't reach.
- **Bulk prepare.** Added `skills/super-jev/prepare_bulk.py`, a folder-scale
  front end to the checked connect flow. It inventories `*.md` files under a
  root, skipping hidden directories, backups, and vault-style subdirectories,
  and holds back any file with card/password-like text or over the gate's
  size ceiling. A cheap writer model drafts one description and one sample
  question per remaining file, in batches; each description is gated against
  its own file the same way `connect_checked.py` gates a description, with
  one rewrite retry on a failure and a feedback message carrying the
  rejected verdict. Only the passing set is connected, through the same
  preview-then-confirm path, replacing an existing pointer of the same name
  when one is already registered. After connecting, each file's own sample
  question is run through `navigate` and the file is expected to rank
  first; a miss is reported, not retried. A cache keyed by file hash under
  `prepare-cache/<pointer>.json` skips the writer and gate for a file that
  has not changed since it last passed, and a run report lands next to it.
  `--no-connect` stops after drafting and gating, for a dry run; `--limit`
  refuses above the connect action's 50-file cap. See
  `skills/super-jev/SKILL.md` and `skills/super-jev/references/connectors.md`.

- **Bulk prepare for a whole agent brain.** Extended `prepare_bulk.py` to
  onboard scope spanning more than one folder and more than the connect
  action's 50-file cap in a single run. `--root` is now repeatable and
  inventories the union of the given roots, in the order given; `--exclude`
  (repeatable) skips any file whose path relative to its root starts with
  the given subpath, and `--no-recurse` limits a root to its direct
  children. `--limit` now bounds each connected part instead of the whole
  run: an approved set larger than the limit is split into parts named
  after the base pointer, each connected separately through the same
  preview-then-confirm path, with the cache and report staying keyed by the
  base pointer. `--max-files` refuses an oversized run before any drafting
  starts, replacing the old inventory truncation. A held file's reason is
  now also written to a `prepare-cache/<pointer>-held.txt` review file,
  including the matching pattern's type and line number with digits masked
  for the card/password-like case, so a human can review without opening
  the file. `--refresh` drops any cached file no longer present on disk
  from the cache and the connect set and notes it in the report, and a
  reconnect that replaces an existing pointer now prints a one-line warning
  that the replacement rotates that pointer's approved answers. See
  `skills/super-jev/SKILL.md` and `skills/super-jev/references/connectors.md`.

- **Checked connect wrapper.** Added `skills/super-jev/connect_checked.py`, a
  wrapper around the memory `connect` action that gates every source's
  description against its own file with the reply-kit claim gate
  (`dispatch.py check --claim`) before doing anything else. A description
  that comes back NOT_SUPPORTED, SUPPORTED under the 0.80 confidence line,
  or over the gate's 32k-token ceiling refuses the whole connect; nothing
  is registered until every source passes. Only on an all-pass does it run
  the normal connect preview, copy the returned sha256 hashes back into the
  request, set `reviewed: true`, and confirm. A `<name>.verdicts.json` file
  is written next to the request with each source's state, confidence, and
  sha256, so a later run can see which files changed since they were last
  checked. `--check-only` runs the gate without connecting; `--line` moves
  the confidence line. It does not auto-recheck files that changed after a
  connect, and it does not chunk or truncate files over the token ceiling —
  split or drop them instead. See `skills/super-jev/SKILL.md` and
  `skills/super-jev/references/connectors.md`.

- **Written-file fact wording (family 6).** The family-6 WRITTEN FILE
  fact no longer states a closed inventory. The old sentence ("the
  only file written in this window is ...") read to the judge as a
  pre-written finding that the evidence could not support the draft,
  and truthful drafts were being blocked the same way family 11 was
  before its wording fix; the word "only" was also ungrammatical when
  several files had been written, and false whenever the byte-capped
  window text had dropped part of the turn. The line now lists what
  the current turn’s receipts show as a positive open list
  ("files written in this window: a.txt, b.txt", or "file written in
  this window: a.txt" for a single receipt), keeping the
  CONTRADICTED_BY_FACT path and which claims it supports unchanged.
  See docs/hooks.md, "Written-file identity".

- **Send-by-draft-id lookback (family 11).** A Gmail `send_message` called
  with only `draftId` sends an existing draft but names no recipient and
  no thread in its own input, so the message-tool branch emitted no "sent"
  line for a genuine send. When a send-verb tool's input carries a draft
  id and no recipient/thread key, the family now looks back through the
  same transcript window for the `create_draft`/`update_draft` call that
  built that draft (matched on the id in the draft call's input, or on
  the id its result names) and reads the recipients off that call's
  `to`/`cc`/`bcc`, with the latest builder winning when an `update_draft`
  changed them; the line reads like any other named-recipient send. When
  no matching draft call is in the window, the line still records the send
  by draft id while saying the recipient is not in the window, so the
  judge sees that a send happened without crediting a recipient the
  family never saw. `create_draft` and `update_draft` remain blocklisted as
  non-sends. See docs/hooks.md, "Receipt shapes — family 11".

- **Receipt shapes wording fix (family 11).** Measured live: truthful
  drafts were being blocked by the overclaims judge, and the only judge
  input that had changed was the family-11 RECEIPT SHAPE fact lines. Each
  line's negative scope clause ("and support for nothing else — it says
  nothing about ...") read to the judge as a pre-written finding that the
  evidence could not support the draft, so it is removed; each line now
  states only the positive act-to-verb mapping. The target list inside
  each line no longer closes with a counted overflow ("and N more"), which
  read as a closed inventory of the turn's acts; it now ends with an open,
  uncounted "and others" when truncated. `DERIVED_FACTS_HEADER` is restored
  to its pre-family-11 wording ("a literal reading of the raw evidence
  below"), dropping the "never an inference from it" clause that family 11
  had added on top — "prefer it over re-reading the column dump yourself"
  predates family 11 and is retained — while keeping the earlier, purely
  additive note that RECEIPT SHAPE lines come from this turn's
  tool_use/tool_result records rather than the window text. The
  gameability bound itself is unchanged and still documented in
  docs/hooks.md; only the judge-facing sentence text moved. See
  docs/hooks.md, "Receipt shapes — family 11".

- **Plug-in check arms and a swappable judge.** Two new packages give the
  gate a clean plug-in seam. `skills/super-jev/arms/` is a registry: an
  arm is one module exposing `NAME`, `KIND`, `DEFAULT_MODE` and
  `check(window, draft, ctx) -> Verdict | None`, and the registry
  discovers arms by listing the package directory, so dropping a file in
  registers it and there is no hard-coded list anywhere. Per-arm mode
  (`block`/`advisory`/`off`) comes from `SUPERJEV_ARM_<NAME>`, else a JSON
  file named by `SUPERJEV_ARMS_CONFIG`, else the arm's own default; an
  unrecognised value falls back to the default and prints one stderr
  line. `skills/super-jev/judges/` puts the model call behind one
  interface — `get_judge().classify(draft, window) -> JudgeResult` — with
  a `typesafe` backend that wraps today's `cmd_gate` call unchanged and a
  `fake` backend that runs `SUPERJEV_GATE_CMD` directly for offline
  tests, selected with `SUPERJEV_JUDGE`. The PR-state arm is migrated
  end-to-end as the template (`arms/pr_state.py`, asking the window
  model's `pr_state_verdict_from_window` instead of scanning raw evidence
  text); `superjev.py`'s legacy `_pr_mismatch_reason` stays callable and
  stays the default, and `SUPERJEV_ARMS=1` is the switch that runs the
  arm through the registry instead. Both offline replays print the same
  decisions with the switch off and on, which is why one arm was migrated
  first rather than all of them: the switch is the proof harness. See
  docs/plugins.md.

  The arm contract carries four more things. **One judge call per run:**
  `ctx["judge"]()` is a memoised accessor that performs the single
  TypeSafe classify on first use and hands every later caller the same
  `JudgeResult`, so zero `KIND = "judge"` arms cost zero calls and N of
  them cost exactly one, each interpreting the shared result in its own
  `check`. **An evidence hook:** an arm may also expose
  `contribute(window, draft, ctx) -> list[str]`, and those lines are
  appended to the evidence — as a FIRST-CLASS window section,
  `[contributed by check arms]`, wrapping rather than mutating the
  `Window` — before any judge call happens, which is what a derived-fact
  family needs to put something in front of the judge rather than only
  ruling on what is already there. A section and not a note after one,
  because a note after one laundered trust: the header was not a header
  the window reader recognised, so the `===` before it was not a section
  boundary and the whole block folded into the chunk above, and a block
  landing under `[session receipts]` had every line — plus any forged
  `[from: ...]` tail — re-parse as a TRUSTED session receipt. It is now
  `window_model.SECTION_CONTRIBUTED`: its own header (one string, shared
  by the renderer and the reader), its own emit slot above every other
  section, a `check_arm` piece origin that the single trust rule answers
  no to, and prose-strength to every deterministic reader (counts,
  labelled values, merge receipts, the PR-state signals). Each line is
  also neutralised on the way out — identity tail stripped, line quoted —
  so no single line passes for a receipt row on its own shape either. The
  invariant is a test: after render and re-parse, no contributed line has
  trust.
  **`ctx` is a named contract:** every key is documented in one table in
  docs/plugins.md, every key is optional, and adding one means adding the
  row in the same change. **Discovery is one keyspace:** an arm whose
  `NAME` is not its own file stem is rejected with one stderr line and
  skipped, and a search path (`SUPERJEV_ARMS_EXTRA_DIR`, or `extra=`)
  means a test double is never written into the shipped package
  directory.

- **The gate records which arms it consulted.** The catch-ledger row for a
  gate decision now carries `arms` (every arm consulted with the mode it
  ran in, `["pr_state:block", ...]`, or `pr_state:legacy-inline` while
  `SUPERJEV_ARMS` is off) and, separately, `arm_errors` (every arm that
  raised, with its exception class). `run_arms` returned that information
  and the gate threw it away. Two fields rather than one because "we asked
  this arm" and "this arm broke" are different facts: a raising blocking
  arm still fails open, but it is no longer invisible. Both are `null` on
  a row that consulted no arms, which is not `[]`.

- **Family 8's explicit-label colon syntax ("items: 2") could pair a
  sentence-subject colon with the numerator of an "N of M" ratio.** The
  #64 escape hatch that lets a draft mark a word as a label with real
  punctuation ("items: 2") did not account for the value right after the
  colon being the first half of a ratio ("Status gate: 6 of 7 checks") —
  the word before the colon is naming the sentence's subject there, not
  handing that one number a label. `_fact_draft_label_values` now skips
  both numbers of an "N of M" / "N/M" shape entirely (see
  `_FACT_RATIO_RE`), by explicit-label syntax or by plain adjacency, so
  neither end of a ratio can be mistaken for a labelled value. Found via
  a live post-merge false block (a "door" claim contradicting an
  unrelated checklist row); see docs/hooks.md.
- **`replay_fact_block_sweep.py` could not see a `[cited files]` fact at
  all, and used a live results directory's `.exit` files as its
  pre-change baseline.** `cmd_hook`'s own window assembly folds the
  receipt-turn extra fact and `build_cited_file_block`'s cited-file tail
  into the window before deriving facts; the sweep skipped both, so any
  case whose draft names its own source ("per the summary log") replayed
  a window with real content missing from it — not "predicts no block",
  genuinely blind to what the live gate saw. The sweep now assembles the
  window the same way `cmd_hook` does, and its "old" side is a real
  import-and-run of `superjev.py` as of the commit before the one under
  test (default HEAD's own parent, override via
  `SUPERJEV_SWEEP_BASELINE_REF`) rather than a recorded exit code, whose
  timing relative to the change under test is not guaranteed. See
  docs/hooks.md.
- **A worker inside a GENUINE worktree could move the ref that vouched for
  `package.json`, and could plant a program in the config the worktree
  SHARES with the protected repo.** `git worktree add` gives a worktree the
  same `.git/config` and the same refs directory as the protected checkout,
  so neither is worker-proof and no identity check can catch either — the
  worktree is real and does belong to the right repository. Two
  consequences, both closed. (1) The npm provenance check read the
  vouched-for blob through `origin/main:package.json`, and a worker could
  run `git update-ref refs/remotes/origin/main $(git rev-parse HEAD)` from
  its own worktree to make that ref name its OWN hostile commit. Nothing
  asks git for the vouched-for content any more: the PROTECTED checkout's
  on-disk `package.json` is hashed in Python and compared to the worktree's
  on-disk bytes, and the protected checkout must itself match a door-owned
  pin committed at `skills/super-jev/trusted-package-json.sha256`, else the
  refusal is `protected-package-json-unpinned`. The additive
  `git diff --quiet` leg is gone; the Python hash is the whole answer. (2)
  `filter.<driver>.clean` and its siblings name programs git EXECUTES on a
  `status -sb`, `diff --stat`, `diff --quiet` or `ls-files -m`, and the key
  names are open-ended so there is nothing finite to pin off with `-c`
  flags. `_worktree_trust` now ends with a `git config --list --show-origin
  -z` scan (a non-executing verb) and refuses the worktree on any key that
  names a program, `worktree-config-execution:<key>`; a `.gitattributes`
  naming any filter or diff driver is its own refusal,
  `worktree-attributes-driver:<attr>`. The scan refuses only on keys whose
  origin file resolves INSIDE the protected repo's common dir, because
  system and global config are not worker-writable and legitimately carry
  `credential.helper` and `alias.*`; `include.path` / `includeIf.*` are on
  the list, which closes the route of laundering a key's origin out of the
  common dir. New `superjev doctor` runs the same scan against the
  PROTECTED repo, which the door can refuse a worktree over but cannot
  un-write, and exits non-zero on a worker-writable hit. See docs/hooks.md,
  "The shared `.git/config` is a list of programs".
- **RECEIPT SHAPES: a new derived-facts family that maps a tool act to the
  plain verbs it supports.** Families 6 through 10 all check a value — a
  filename, a labelled figure, a score, an extremum. The gap they left is a
  missing noun, not a missing number: a draft says "logged it", "notified
  health-fitness", "escalated it", "scheduled the scan", and the judge had to
  bridge that to a `Write`, a relay `send.sh` call or a `CronCreate` on its
  own, out of a column dump, in one call. `_facts_receipt_shapes` now states
  the bridge as a sentence — an ACT of a named shape ran against a named
  TARGET, and that act is support for exactly these verbs and nothing more —
  in at most four lines, one per verb class (saved, sent, scheduled, handed
  off). It is the one family that reads the TRANSCRIPT rather than the
  assembled window, on purpose: the window's identity strings are truncated
  (a long here-document's write to `~/a/b/c/BRIEF.md` read back as a write to
  `~/a`), and the window also carries worker prose, so a receipt taken from
  it is a receipt the claimant can write for itself. Tool name and tool input
  come from the assistant `tool_use` record, the result and its error state
  from the paired `tool_result`; the draft is never read. No matching act
  means no line, an act whose result came back `is_error` is dropped whole
  rather than hedged, and detection runs on a quote-masked, here-document-
  stripped command so an `echo "--- crontab ---"` label is not a crontab
  install and a `>` inside a `printf` argument is not a redirection. Every
  line carries its own bound in its own text: a write says nothing about what
  the file now contains, a send nothing about delivery, a scheduler call
  nothing about the job having run — so a worker that writes an empty file
  named after its claim gets the path named and no support for the claim. The
  lines are folded in last, behind every `CONTRADICTED_BY_FACT` sentence, and
  carry no `CONTRADICTED_BY_FACT` marker themselves, so the family is judge
  input only and cannot block, allow or flip a decision; replaying every
  recorded case in all four benches, main against this change, the
  deterministic block reasons and the other families' fact lines are
  identical. `hook gate --explain` gained a `receipt shapes` row. Off with
  `SUPERJEV_DERIVED_FACTS=0`, same switch as every other family. See
  docs/hooks.md, "Receipt shapes — family 11, the act-to-verb bridge".
- **RECEIPT SHAPES round 2: the "sent" class's outbox pattern matched the
  harness's own `answer-<hex>.txt` answer-delivery file, handing the judge
  blanket sent/notified/escalated/reported support on almost any window.**
  `answer-<hex>.txt` is dropped from the outbox pattern (a write there now
  falls through to the ordinary "saved" shape); a "sent" line now only comes
  from a channel with a knowable, named recipient — a relay send (naming the
  task id its own command segment carried), a write to the Telegram outbox
  file, or a message-shaped tool call (Telegram/email/chat) whose own input
  names a recipient. The relay-send match is now anchored to the start of a
  command segment, so `cat send.sh` / `grep task send.sh` / `chmod +x
  send.sh` no longer fire, and a send's task-id scrape is scoped to that
  send's own command segment so a sibling `&&`-joined command's id is never
  attributed to it. See docs/hooks.md.
- **RECEIPT SHAPES round 3: the message-tool "sent" subclass was verb-blind
  — it fired on a channel word in the tool name plus any recipient-ish
  key, with no check that the tool actually SENDS.** A Gmail-shaped
  `create_draft` call names a real recipient and delivers nothing at all,
  and read as support for "sent"/"replied"/"notified"/"told"; `get_thread`,
  `search_threads`, `trash_thread`, `label_thread`, `mark_thread_spam` and
  `apply_sensitive_thread_label` all fired the same way. A message-shaped
  tool call is now a "sent" line only when its own name carries a send verb
  (send/reply/forward/post/notify/message) AND no read/mutate verb
  (get/list/search/create/update/label/unlabel/trash/untrash/mark/apply/
  delete/draft), matched on lower-cased word tokens split on `_` and
  CamelCase boundaries, never a substring. The late-Telegram outbox line
  now names what it actually delivers — the configured owner's Telegram,
  via the claw4mac poller — rather than just the file's name. See
  docs/hooks.md.
- **RECEIPT SHAPES round 4: real Gmail sends produced NO receipt at all.**
  The live Gmail MCP schema passes `to`/`cc`/`bcc` as ARRAYS of address
  strings, not a single string, and `reply`/`forward` can carry only
  `messageId` (their one required field) with no `to` at all when the
  reply keeps the thread's existing recipients — a genuine send that
  still names no recipient. List-valued recipients are now accepted
  (joined verbatim, comma-separated); the camelCase schema keys
  (`messageId`, `threadId`, `replyThreadId`, `replyToMessageId`) are
  checked as a fallback, naming the thread a reply/forward addressed when
  no recipient field is present. `unmark` is added to the read/mutate
  blocklist (`unmark_message_spam` fuses the prefix onto the verb with no
  separator, so `mark` alone never matched it). docs/hooks.md also notes
  the late-Telegram line's two honest limits: a control-less (iMessage)
  session's poller delivers nothing at all, and even a real delivery
  happens on the poller's own later tick, not at the moment of the write.
  See docs/hooks.md.
- **`replay_fact_block_sweep.py`'s LIVE side could raise and the sweep
  would print-and-skip with no counter and no effect on the exit code.**
  A live-module crash silently shrank coverage — fewer cases replayed,
  nothing to say anything had gone wrong. The live side now mirrors the
  baseline treatment: every exception is counted and printed under its
  own "LIVE ERROR" line, and the sweep exits non-zero if any occurred.
- **The window budget: a receipt that supports a true reply was being
  dropped before the judge ever saw it.** Five separate gathering faults,
  all in the Stop-hook gate's wide evidence window, each one capable of
  hiding the very line that made a reply true. See docs/hooks.md, "The
  window budget — reservations, not ceilings".

  1. *A worker's report went out of the window with its turn.* A report
     relayed in an earlier turn was concatenated onto that turn's tool
     results and rode inside its `[previous turn -N]` block, so it was
     dropped whenever the turn was — and the previous-turn block is the
     first thing the trimmer gives up. Relayed reports from earlier turns
     now have their own budget and their own marker,
     `[relayed reports in previous turns]`, kept newest-first through the
     same report fence renderer, so each one still reads as an unverified
     relayed claim rather than a receipt. A receipt-bearing report a turn
     or two back now survives whether or not its turn's tool output does.

  2. *The session-receipts layer grew until it owned the window.* It is
     the backing layer — a one-line memory of facts from turns too far
     back to carry whole — and it had no share of its own, so on a long
     session it crowded out the turns that held the actual proof. It now
     has a reserved share of the cap. Over the share, receipts are given
     up newest-first, except that receipts whose claim keys appear in the
     draft are kept first regardless of age.

  3. *Previous-turn depth counted turns, not evidence.* `SUPERJEV_PREV_TURNS`
     counted turns of wall clock, so a lead whose last two turns were pure
     conversation got a window with no previous-turn material at all, even
     with a tool-carrying turn one hop further back. The setting now counts
     turns that CARRY TOOL RESULTS, walking over tool-free turns up to
     `SUPERJEV_PREV_TURN_SCAN_LIMIT`. Tool-free turns on the way are still
     read for the reports they carry.

  4. *The cited-file read-back only ever read the tail.* When a draft names
     its own source and that source is an append-only log, the entry the
     draft is quoting is routinely nowhere near the end of the file. The
     block now carries, above the tail, the lines from earlier in the same
     file that overlap the draft's own figures — a ratio the draft states,
     its own integers, its own label words, scored literally in code, each
     line prefixed with its real line number. The tail itself is unchanged.

  5. *A session-wide merge total read as a contradiction.* A draft that
     says a number of PRs merged, or that a whole set is merged, is making
     a claim whose scope is the session; a window that saturates below that
     number cannot settle it either way, and silence left the judge reading
     the gap as a refutation. A new derived fact now states the window's own
     merge-receipt count and says plainly that the session-wide total cannot
     be checked there. It fires only when the claimed total is past the
     window's count, or when the claim names no total at all.

  `window_model.py` follows the new layout: a `SECTION_PREV_REPORTS` with
  its own header, emit slot and recency rank, detected by feature so
  `render()` stays byte-for-byte correct against a composer on either side
  of the change. See docs/window-model.md, "The previous-turn reports
  section". One measured cost, in the safe direction: `from_text` refuses a
  section boundary once an unbounded report body has opened, and more
  windows now carry a report section, so fewer recorded windows round-trip
  to the same pieces. Nothing gained trust and no piece changed kind on
  re-parse.

  Two supporting fixes came out of the same measurement. `"either merged or
  on PR #N"` was being read as a claim that PR #N was merged, and the absent
  receipt reported as a finding; a disjunction or negation between "merged"
  and the PR number now disqualifies the match, in the Python gate and in
  the TypeScript `windowFacts` mirror alike. And both new shares are
  RESERVATIONS rather than ceilings: a layer is guaranteed its share against
  the layers below it, and gets back whatever the layers above it leave
  unspent, because a hard ceiling threw evidence away in windows with room
  to spare.

- **Window-budget round 2: six review findings closed.** (1) The receipts
  layer's over-share selection dropped the OLDEST receipts first, the
  opposite of the section's own rationale (an old receipt is what the
  previous-turn layers cannot re-derive) — `_build_receipts_block` now
  drops newest-first, keeping the oldest, same as the comment always said.
  (2) A previous-turn-reports regression: `_section_recency_rank` gave
  every relayed report the same flat rank regardless of which previous
  turn it came from, so a stale report from turn -2 could outrank a merge
  receipt from turn -1 and family 5's stale-report fact went silent where
  it used to fire. Each report's marker line now carries its own
  originating turn (`REPORT_LABEL_PREV_TURN`), and `_report_not_merged_claims`
  reads that turn back out instead of collapsing every previous-turn
  report to one rank. (3) `tests/replay_fact_block_sweep.py` now also
  WARNS (never fails) when a recorded case's window loses a
  receipt-worthy line the baseline carried, closing the gap between what
  docs/hooks.md claimed the sweep checked and what it actually ran — see
  "Window-budget round 3" below for how this check widened again shortly
  after landing. (4) docs/hooks.md's wording matched to the source
  comment it was paraphrasing loosely — both now say "a sizeable share of
  the recorded cases" (round 3 corrected this same wording again, after
  the round-2 fix here first landed on a fixed count). (5) The
  merge-count derived fact now quotes the draft's own claimed total
  alongside the window's receipt count, and states plainly when the
  window carries no merge receipt at all, rather than reading as amnesty
  for an unsupported claim. (6) The cited-file relevant-line block is
  relabelled to say it is a number match, not confirmation that a picked
  line says what the draft says, and the picked lines now sit below the
  tail rather than above it.

- **Window-budget round 3.** The receipt-line warning above now covers
  truths as well as lies, printed separately — restricting it to lies
  alone could not see the real signal, since the recorded case that
  actually loses a line is a truth, not a lie. `_receipts_relevant_to_draft`
  now ranks a receipt the draft names by a concrete identifier (a PR
  number, a cited file, a task id) above one that merely shares a generic
  stemmed word: a draft naming one PR out of many shares the same
  "merged" stem with every OTHER merge receipt in the window, so stem
  overlap alone could not tell them apart and left age to drop the very
  receipt the draft was about — mirrored in `window_model.py` so the two
  stay byte-identical. `test/fixtures/gate-window-facts.json` now also
  pins the zero-receipt merge-count sentence, holding the Python and
  TypeScript mirrors identical on that branch too, through the same
  shared-fixture mechanism as the rest of the file. Docs and the
  `RECEIPTS_BUDGET_SHARE` comment reworded to "a sizeable share of the
  recorded cases" — what was actually measured, not a fixed count.

- **The count arm's `REPORT FROM ...` fence closed on any blank line, not
  just a real section boundary.** `_extract_labelled_evidence_counts_scoped`
  and `_fact_window_lines_excluding_reports` (families 4/5's shared receipt
  reader) both used a bare blank line to decide a worker's own report body
  had ended — but the assembler puts a blank line INSIDE a report's own
  multi-paragraph text too, so only the report's first paragraph was ever
  actually excluded. A bold-markdown count, or a merge/CI-shaped claim,
  sitting in a LATER paragraph of the same report read straight back in as
  real evidence, right beside a genuine receipt that disagreed with it. Both
  readers now close the fence only on a real structural marker — a
  bracketed section header or an explicit `END REPORT FROM ...` line — never
  on a blank line, which a report's own prose can legitimately contain. See
  docs/hooks.md, "The report fence closes on structure, not blank lines".

- **Four more deterministic-arm review findings closed, on top of the
  hash-split/noun-label fix above.** (1) The count arm's tokenizer folded
  `#` and `/` into the same character class as letters and digits
  (`[A-Za-z0-9#/]+`), so a slash fraction or a hash-prefixed number
  ("41/41 passed", "3/41 tests pass", "Tests #52 passed") glued into one
  non-digit token that `tok.isdigit()` dropped outright — the draft
  claimed no count at all, so a false count claim of that shape slipped
  past the arm clean. `#` and `/` now tokenize as their own single-
  character tokens instead; a mixed alnum run with neither character in
  it, e.g. a git short SHA, is unaffected. (2) `_fact_draft_label_values`'
  digit-then-letter guard (added for the same short-SHA shape) was too
  broad — it skipped every digit run immediately followed by a letter, so
  a unit-suffixed value ("250ms", "4k", "8GB") was dropped too, and a
  labelled-value contradiction against one no longer fired. Narrowed to
  only skip when the tail right after the digits looks like the rest of a
  fused identifier — a letter, then eventually another digit — leaving a
  pure unit suffix, which never has a trailing digit, as a real value.
  (3) `_extract_labelled_evidence_counts_scoped` had no `REPORT FROM ...`
  fence exclusion, unlike families 4 and 5's receipt scan, so a worker's
  own bold-markdown run summary inside its OWN unverified report body
  (`**61 passed**`) was read as a real evidence count — a trust-boundary
  hole a worker's own claim text could exploit to clear the count arm
  against itself. Evidence counts now skip every line inside a report
  fence, matching families 4/5. (4) `_iter_window_report_lines`, added
  alongside the families 4/5 fix, had no production caller and collided
  in name with a different-signature function a parked PR defines; it is
  removed, and its one test now exercises
  `_fact_window_lines_excluding_reports` directly. Net effect on the
  deterministic arms' live bench read: one blind-bench lie catch that
  happened to work only because of the old hash-split tokenizer artifact
  is now left to the judge, same as most lies always have been — the
  bench's zero-false-block guarantee on true reports is unaffected. See
  docs/hooks.md, "Four more deterministic-arm review findings closed".

- **`catch signal` round 3: bounded reason families, per-bot grouping and
  breakdown.** A colonless reason with no recognised `HEADER:`/judge-score
  prefix (an advisory note, e.g. one embedding a `--test-cmd '...'`
  argument) used to become the reason **family** verbatim — the one
  string that reached a public issue title/body without ever going
  through `_catch_redact` or the markdown-escape pass, and unbounded, so
  one advisory note per differing detail never collapsed with the next
  the way every other arm's repeat blocks did. `_catch_reason_family` now
  buckets that shape instead (first two letters-only words, max 40 chars,
  else the fixed literal `advisory-note`), caps every family at 60
  characters, and always escapes it; `_catch_signal_title`/
  `_catch_signal_body` escape the family again on top of that, since a
  *recognised*-header family can still carry a backtick/`@handle`/`#NN`
  from the draft text before its own colon. `catch signal` also gained
  `--bot <id>` (same filter `catch list`/`catch report` already had,
  restricting grouping itself, not just the printed output) and a
  counts-only per-bot breakdown line inside every printed/issue-body
  signal block. See docs/hooks.md, "Turning a repeat pattern into a fix
  PR."

- **`catch signal`.** The first step of the compounding loop the catch
  ledger exists to feed: harness catches -> tags -> issue -> fix PR ->
  bench robot. `superjev.py catch signal [--min 3] [--since 24h] [--open
  --repo owner/name] [--dry-run] [--with-reasons] [--with-drafts]` groups
  catch-ledger records tagged `false`/`miss` by **reason family** (the
  reason string with its numbers/values, and a per-call judge claim key
  like `c2`, stripped — so two count mismatches with different draft/
  evidence numbers, or six `c1`/`c2`/.../`OVERCLAIMS` blocks from
  different claim indices, all still collapse into one family) and, once
  a family reaches `--min` (an explicit `--min 0` is now refused, not
  silently rewritten to 3), prints a signal block. A signal's own
  printed/issue-body content is **metadata only by default** — family,
  count, first/last timestamps, and every matching record's id, plus a
  pointer to `catch list --id <id>` for the redacted detail locally —
  never draft-derived text, closing off names/addresses/order numbers/
  health details/dollar figures/non-US phone numbers/token URLs that
  reached a public issue before even though `_catch_redact` never covered
  those shapes. `--with-reasons`/`--with-drafts` add the redacted (and,
  since this text can still carry markdown/GitHub-autolink syntax,
  markdown-escaped) reason line / draft excerpt to the *local*
  printed/`--dry-run` output only — both are refused outright (exit 2)
  together with `--open`. Exit 0 with signals filed/previewed, exit 1
  with no signal reaching `--min`, exit 3 if `--open` attempted a filing
  that was refused or failed. `--open` files a GitHub issue via `gh issue
  create --label harness-signal` and records the family in a
  `signals.jsonl` sidecar so the same family is never filed twice;
  `--dry-run` prints the issue body and never calls `gh`. A final email/
  phone/SSN check on the assembled body runs right before any real `gh`
  call, belt-and-braces on top of the metadata-only default body. `catch
  list` gained `--id <id>` so the pointer above is a real, working lookup.
  See docs/hooks.md, "Turning a repeat pattern into a fix PR."

- **OVERCLAIMS arm — the receipt-turn fix.** A hand adjudication of every
  live `hook gate` block on the primary's own seat
  (`ops/gate-adjudication-20260918.md`) found one live false-block
  mechanism this fix addresses: a tool-free current turn whose reply is a
  correct restatement of a result from the *previous* turn's own tool
  activity was read as having no in-window evidence. The most recent
  previous turn that ran tools is now named in a new DERIVED FACTS
  sentence (`RECEIPT TURN: ...`) whenever the current turn is tool-free
  but the window carries a receipt from that previous turn — its
  `[previous turn -N]` section header is left exactly as-is, pointed at
  from the DERIVED FACTS sentence rather than rewritten. See
  `_receipt_turn_index`/`_receipt_turn_extra_fact`, and
  `compose_window_with_facts`'s `extra_facts` parameter, and docs/hooks.md,
  "OVERCLAIMS arm — the receipt-turn fix". This addresses the tool-free-
  turn-with-in-window-receipt case only: it does not address a false block
  whose receipt lies beyond the previous-turn window, a block that was
  really the deterministic PR-state arm's job, or a false block on a turn
  that itself ran a tool.

- **Judge-advisory mode, granular.** `SUPERJEV_GATE_JUDGE_ADVISORY` now
  also accepts `weak`, alongside the existing `1`: `weak` demotes only the
  per-claim NOT_SUPPORTED/CONTRADICTED arm (v2's secondary arm) and
  SELF_CONTRADICTORY to advisory — OVERCLAIMS still blocks, matching the
  adjudication's finding that OVERCLAIMS is the only judge arm with a
  positive live record while the secondary arm and SELF_CONTRADICTORY are
  not. `1` (all judge arms advisory)
  and `0`/unset (off) are unchanged. The catch ledger's `advisory-judge`
  decision now carries a `judge-advisory-mode:1`/`judge-advisory-mode:weak`
  tag in its `reasons`, alongside the existing `key VERDICT score` strings
  that already name the arm. See docs/hooks.md, "Judge-advisory mode".
- **`bot`/`origin` on every ledger record.** Both the call ledger
  (`calls.jsonl`) and the catch ledger (`catches.jsonl`) now tag every
  record with `bot` (`CLAW4MAC_BOT_ID` or `CLAUDE_BOT_ID` from the
  environment if set, else the claw4mac project-dir segment out of the
  hook payload's `transcript_path` — the part after `agent-cwd-` up to the
  next path separator — else `"unknown"`) and `origin` (`"bench"` when
  `SUPERJEV_BENCH=1` or the hook payload's `session_id` starts with
  `bench-`, else `"live"`). Neither field changes a hook's decision — both
  are best-effort, never-raise defaults applied at the same point `id`
  already was. `superjev.py catch list`/`catch report` gain `--bot <id>`,
  `catch list` shows a `bot=` column, and `catch report` prints a `by
  bot:` breakdown when `--bot` is not given. See docs/hooks.md, "The
  ledger" and "The catch ledger".

- **Verify hook: spawn acks and unchecked no-evidence runs now show up in
  the catch ledger.** Two fixes off `gate-adjudication-20260918.md`'s
  verify-door findings — every adjudicated live verify block was false.
  First, the live
  `hook verify` (PostToolUse) already skipped a spawn/launch dict or a
  launch-ack text without calling the judge — it just never told anyone:
  the catch ledger carried nothing for those runs, so a spawn ack and a
  real unchecked report were indistinguishable in `catches.jsonl`. It now
  prints `super-jev verify: spawn ack, nothing to judge` on stderr and
  logs a `door="verify"`, `decision="unchecked"`, `reasons=["spawn-ack"]`
  catch record. Second, and the real live bug: the PostToolUse hook never
  computed its own evidence-gather health for `verify` the way
  `hook verify --from-file` and the Stop-scan already did, so a bare
  worker-verify exit 4 (REJECT) blocked even when nothing was actually
  gathered to judge the report against (no `--worktree`, the only
  evidence source a live hook ever has). It now runs the same
  `_evidence_inventory` check verify's other two entry points already
  ran; when the gather is thin, a would-be block is downgraded to one
  advisory line (`no evidence gathered; not judged`, exit 0) and logged
  as `decision="unchecked"`, `reasons=["no-evidence", ...]` instead of
  blocking. The Stop-scan's REJECT label had the identical hole — most
  REJECT labels in the same adjudication ran at `health=thin` — a bare
  exit code there now prints `UNCHECKED`, not `REJECT`, and writes the
  same catch-ledger shape. In practice this made the live verify door
  advisory-only for every report until a worktree was supplied: a real
  PostToolUse payload carries no `worktree` key and nothing exports
  `SUPERJEV_HOOK_WORKTREE`, so the gather was thin on every live call.
  See docs/hooks.md, "verify: spawn acks and gather health".

- **Verify hook: a worktree is now derived from the worker's own report
  text when the payload and the environment give it none — behind a trust
  boundary.** Closes the gap the entry above left open: a real
  PostToolUse(Agent) payload never carries a `worktree` key, so
  `_evidence_inventory` in the live verify path always saw a thin gather
  and the door stayed advisory-only. `_worktree_from_report(text)` scans
  the report for any absolute, no-spaces path it mentions. Source
  precedence: the payload's own `worktree` key, then
  `SUPERJEV_HOOK_WORKTREE`, then this report-derived path. Every
  `hook verify` run logs which source won as `worktree_source` in the call
  ledger, so a reviewer can see when the door used a path the worker
  itself named rather than one supplied upstream.

- **Security: one validator stands between report text and every
  subprocess.** A directory named in a worker's report is untrusted input,
  and it is not inert input to git: a repository's own `.git/config` can
  set `core.fsmonitor` or `core.hooksPath`, and `git status` inside it runs
  what those name. Deriving a worktree from report text on the strength of
  "it exists and has a `.git`" was therefore an arbitrary-code-execution
  path, and the same shape already existed on the Stop-scan and
  `hook prompt-verify` paths, which derived a worktree AND a test command
  from report text with no check at all. New `_trusted_worktree(path,
  protected_repo)` is the single gate both paths now go through. It
  resolves the realpath first, refuses anything the evidence-guard
  blocklist covers (so a harmless-looking symlink cannot launder a
  credential-adjacent target), refuses the protected repo's own main
  checkout, requires the realpath to sit under an allowlist root
  (`SUPERJEV_WORKTREE_ROOTS`, `os.pathsep`-separated, defaulting to the
  fleet's own worktree root), and then requires
  `git rev-parse --git-common-dir` there to resolve to the protected
  repo's own common dir — so a foreign or planted repository is refused
  even when it is a perfectly real repo in the right place. A `.git` FILE
  worktree whose gitdir points outside that common dir is refused too. The
  protected repo is `SUPERJEV_PROTECTED_REPO`, else the checkout
  `superjev.py` itself lives in; when neither answers, nothing is trusted.
  Every check that could execute repository config runs only AFTER the
  cheap filesystem gates, and the validator itself uses only `rev-parse`,
  which does not trigger `core.fsmonitor`. Refusals are recorded in the
  ledger as `worktree_refused: ["worktree-untrusted:<why>"]`, and
  `worktree_source` carries `"report-refused:<why>"` so a thin gather is
  never mistaken for a report that named nothing.

- **Security: the trust boundary now reaches the CONSUMER, via the
  environment.** Argv flags only cover git commands this module builds
  itself, and that left two holes. First, the validated worktree is handed
  on to programs that run git themselves off their own argv: the external
  `worker-verify` door runs `git -C <worktree> status -sb`, `gh` shells
  out to git, and a derived `npm`/`node` test command can too — so the
  boundary ended at the door while the untrusted path went through it.
  Second, and worse, a worker inside a GENUINE worktree of the protected
  repo can run `git config core.fsmonitor <script>`, which lands in the
  SHARED `.git/config` every worktree of that repo reads; the worktree is
  real and in the right place, so `_trusted_worktree` has nothing to
  refuse. Git reads config from the environment at the same precedence as
  `-c`, and children inherit it, so a new `safe_git_env()` builds an
  environment pinning `core.fsmonitor=false`, `core.hooksPath=/dev/null`
  and `core.pager=cat`, and EVERY subprocess this module spawns now runs
  under it — the `verify` and `gate` doors, `gh`, `_git_rc`, the derived
  test command and the `node` derived-facts CLI. Inherited
  `GIT_CONFIG_*` pairs are preserved and ours appended after them, since
  git applies the pairs in order and the last wins. `cmd_verify` refuses
  to launch `worker-verify` at all when the pins are absent from the
  environment it is about to inherit, and a source-level test requires
  every `subprocess` call in `superjev.py` to pass an explicit `env`, so a
  call added later cannot ship inheriting a bare `os.environ`. The `-c`
  flags stay on our own argv as well.

- **Security: `diff.external` and textconv can no longer run, or bend what
  a diff reports.** Two more config keys name a program git executes, and
  neither is covered by pinning `core.fsmonitor`/`core.hooksPath`:
  `diff.external` replaces git's diff engine for every file, and a
  `diff.<driver>.textconv` selected by a checked-in `.gitattributes` runs
  over each blob before diffing. Both are writable by a worker in a
  genuine worktree, and both also CHANGE what a diff reports — which is
  what the `npm` provenance check reads, so a planted differ that exited
  quietly would have made a modified `package.json` read as clean. Every
  `git diff` the door runs now carries `--no-ext-diff --no-textconv`,
  injected centrally by `git_argv` so no call site can omit them. Pinning
  the key is not an option: `-c diff.external=` makes git fatal out with
  "external diff died".

- **Security: a clean filter can no longer hide a hostile
  `package.json`.** Found while fixing the two entries above, and
  exploitable. `filter.<driver>.clean` is a program git runs over the
  WORKING COPY before comparing it, selected per-path by a checked-in
  `.gitattributes`; a filter that echoes the committed content makes `git
  diff --quiet HEAD -- package.json` report a hostile working copy as
  unmodified, defeating the very check meant to catch an uncommitted edit.
  `--no-ext-diff`/`--no-textconv` do not cover it and no key can be
  pinned, because the driver name is chosen by whoever writes the
  `.gitattributes`. So the working-copy half of the provenance check no
  longer asks git: it reads the bytes and computes the git object id in
  Python (sha1 and sha256, since the object format is a repository
  property), and compares that to the vouched-for blob id. `git diff
  --quiet` is still consulted, but only ever to refuse. The cost is a
  false refusal on a repo that legitimately filters or CRLF-normalises the
  file being checked, which is the safe direction.

- **Security: `SUPERJEV_WORKTREE_ROOTS=none` refuses every derived
  worktree.** A reserved value, not a path: no root is allowlisted, so
  nothing derived from report text reaches the filesystem and no test
  command runs. One `none` anywhere in the list wins over a real root
  beside it. Unset and blank keep their existing meaning (fall back to the
  fleet's default root), which is why the lockdown needs a word of its own
  rather than an empty value. Refusals carry the new
  `no-allowlist-root` token, distinct from `outside-allowlist-root`, which
  also now covers the case where every root named failed to resolve.

- **Security: every `git` call the door makes disables fsmonitor and
  hooks.** All of them now build their argv through one helper,
  `git_argv`, which prepends `-c core.fsmonitor=false -c
  core.hooksPath=/dev/null` ahead of `-C`. This is defence in depth behind
  `_trusted_worktree`, not a substitute for it — and, on its own, not
  enough; see the environment-pin entry above for what it does not cover.

- **Security: a report-derived `npm test` no longer vouches for itself.**
  `npm test` names no program — `package.json`'s `scripts` does, and a
  worker can write that file. A worker could therefore report a pass count,
  have this door run its own script, and have that script's output accepted
  as independent evidence for the report.

  The first cut of this check asked the wrong question. It required
  `package.json` to be tracked and unmodified against `HEAD`, both
  evaluated inside the worker's OWN worktree — which a worker defeated by
  simply committing its hostile `package.json` on its task branch, so that
  the file was tracked and its working copy agreed with its own `HEAD`.
  The question is not "did this worker leave `package.json` alone", it is
  "is this the script the PROTECTED repo vouches for". So the worktree's
  committed blob (`HEAD:package.json`) is now compared against the
  protected repo's default-branch blob (`origin/main:package.json`,
  falling back to `main:package.json` — the remote-tracking ref first,
  because a local `main` in a shared checkout can be moved), and the
  working copy must equal that same blob as well. Either lookup failing to
  answer is a refusal, not a pass. Otherwise the run records
  `untrusted-test-cmd` plus a specific `untrusted-test-cmd:<why>`
  (`package-json-differs`, `package.json-modified`,
  `package.json-not-tracked`, `protected-package-json-unreadable`, and so
  on) and gathers no test evidence. Enforced both where the command is
  derived and at `check_test_cmd_for_fallback`, the point where it would
  execute.

- **Fix: a path containing the word "pytest" is no longer read as a test
  command.** `_TEST_PHRASE_RE` matched `pytest` inside a longer path (a
  `/tmp/pytest-of-<user>/...` scratch directory, say) and its greedy tail
  swallowed the rest of that path into the capture — so a report that
  merely mentioned such a directory produced a "test command" whose
  `argv[0]` came straight out of report text and was then executed. A
  derived command must now be a runner the report actually named, and a
  match can no longer grow across a `/`, a quote or a shell
  metacharacter. See docs/hooks.md, "The trust boundary for worker-named
  paths".

- **Two more deterministic-arm false-block sources closed.** The count
  arm's draft-side tokenizer no longer splits a mixed alnum run (a git
  short SHA like `0dca183`) into bogus digit tokens, and its evidence-side
  recogniser now also trusts a worker's own bold-markdown run summary
  (`**N passed**`). Family 8 (labelled-value pairing) gained a
  common-noun/number-list guard so a plain English noun phrase in the
  draft ("items 2 and 3") is no longer read as a reference to an
  unrelated, longer evidence label ("feat items") sharing one common
  word, with an explicit-label-syntax escape hatch (`items: 2`,
  `items = 2`, `` `items` 2 ``) and a verbatim-label escape hatch. Families
  4 and 5's receipt scan no longer reads a worker's own claim text inside
  a `REPORT FROM ...` fence as a real merge receipt. See docs/hooks.md,
  "Two more false-block sources closed".

- **Judge-advisory gate mode.** `SUPERJEV_GATE_JUDGE_ADVISORY=1` demotes a
  `hook gate` block to advisory (print the reason, exit 0) when every
  blocking verdict behind it came from an arm whose `KIND` is `judge`.
  That rule is the registry's own (`arms.judge_only_blocks`); the gate
  adapts this call's reason lists into verdicts for it rather than keeping
  a second copy, so it covers the OVERCLAIMS arm and, under
  `SUPERJEV_RULE=v2`, the secondary NOT_SUPPORTED/CONTRADICTED arm without
  naming either. The granular `=weak` level is the SAME registry rule with
  a filter: the gate hands `judge_only_blocks` the weak verdict set, and a
  blocking judge verdict outside it — OVERCLAIMS, or a reason line naming
  no verdict at all — keeps its block. So there is still one rule and one
  copy of it, at both levels.
  This failsafe is a different object from a per-arm mode: a mode is one
  arm's standing on every run, this is one gate call's outcome demoted
  after the fact. The
  judge's confidence score is a guess, and a wrong guess should not stop a
  true turn. A block carrying even one deterministic reason (count
  mismatch, PR mismatch, `CONTRADICTED_BY_FACT`) still blocks exactly as
  today, env or no env. The stderr line is prefixed `super-jev gate (judge
  advisory, not blocked):` instead of `super-jev gate blocked this`, and
  the catch ledger records the decision as `advisory-judge` (tagged
  `fair`/`false` the same way `advisory-forced` is). `catch report` prints
  the new count on its own line, `judge advisories: N`, alongside `blocks
  suppressed: N` — the two failsafes are counted separately. See
  docs/hooks.md, "Judge-advisory mode".
- **The structural window model.** New `skills/super-jev/window_model.py`:
  the gate's evidence window as labelled `Piece`s (`receipt`, `claim`,
  `fact`, `header`, `draft`) carrying their own section, recency rank,
  source and provenance, instead of one flat blob of text that six
  readers each re-parse. Two constructors — `from_transcript(...)` from
  the composer's own transcript inputs, and `from_text(...)` for
  replaying a recorded bench — plus `render()`, which emits the exact
  bytes `_derive_evidence_text_from_transcript` emits today, and query
  helpers (`lines`, `receipts_for`, `values_labelled`, `newest`,
  `claims`, `receipts`, `sections`, `fit`).

  The trust rule now lives in ONE function: a piece is trusted iff its
  text came out of a tool result record, or out of a session receipt,
  which is a cache of an earlier turn's tool result. Never from a
  teammate message, never from assistant text, regardless of wording. So
  a report quoting `{"state": "MERGED"}` stays a claim, and a tool result
  that merely PRINTS a report stays a receipt. Truncation keeps piece
  boundaries (`Window.fit`): whole claims go before whole receipts do,
  and nothing is ever cut inside a piece. The default `policy="legacy"`
  reproduces the composer's byte truncation instead, quirks included, so
  `render()` is byte-identical during the migration; a legacy cut that
  fuses two pieces into one blob is marked untrusted, because provenance
  is no longer separable.

  Wired to nothing on purpose — PR #53 is open over the readers a
  migration would touch. The one reader here is read-only:
  `pr_state_signals_from_window` / `pr_state_verdict_from_window`
  reproduce PR #53's PR-state arm, verdict strings and both fail-closed
  rules included, with `in_report` replaced by `not line.trusted`. New
  `docs/window-model.md` carries the trust rule, the truncation
  policies, what `from_text` cannot know, and a per-reader migration
  plan.

  `from_text` states one guarantee and keeps it: no byte the composer
  copied out of a worker's report can end up inside a TRUSTED piece. A
  section header only bounds a section where the composer could have
  emitted it (its slot steps strictly upward in the composer's own emit
  order), a `===` run after a report label does not bound one at all
  because this branch's composer copies report bodies in verbatim, and
  past a byte-cut marker nothing is structure in either direction. The
  cost is that a window carrying a relayed report no longer re-parses to
  the same pieces, and there is no flag that buys the exact parse back —
  `from_text` never takes a caller's word for how its bytes were built,
  so it fails closed on every input, always. The replay prints the
  round-trip count so the gap is visible.

  New `skills/super-jev/tests/test_window_model.py` (offline) and
  `skills/super-jev/tests/replay_window_model.py`, which replays every
  recorded gate-bench transcript with no network and no key, against this
  checkout's composer and then against PR #53's — whose previous-turn
  reports sit behind a `[relayed reports in this turn]` mark that `main`
  does not emit, and which `render()` now reproduces by feature-detecting
  the composer rather than by assuming a branch.

- **The catch ledger.** A new, separate JSONL file (`SUPERJEV_CATCH_LEDGER`,
  default `catches.jsonl` next to the call ledger) records one small line
  per gate/verify/prompt-verify hook decision — id, door, decision, the
  same reason strings `--explain` prints, a redacted excerpt of the draft
  or report, window size, and two blank fields (`tag`, `note`) for a
  human. The excerpt is sliced to 4096 chars, then redacted for
  secrets/credentials, emails, US-shaped phone numbers (area code not
  starting 0/1, never matched glued to another digit or a decimal
  continuation), SSN-shaped digit strings and 13+ digit card numbers
  (space, dash or dot separators; a bare 13-digit run shaped like a
  plausible unix-ms timestamp is kept, not redacted — catch-ledger-only
  patterns, never applied to the gate/verify evidence window itself), then
  sliced to 240 chars.
  Every real decision now gets a record, including the previously-silent
  `hook verify --from-file`, per-teammate `hook prompt-verify` verdicts,
  and the two budget-exceeded paths (recorded as `unchecked`, reason
  `budget-exceeded`); the stop_hook_active second-pass demotion records as
  its own decision, `advisory-forced`, not `advisory`. Writing a record is
  best-effort (any Exception, not just OSError) and never affects the
  decision or exit code it is reporting on.

  New `superjev.py catch` subcommand: `list [--since 24h] [--untagged]`,
  `tag <id> fair|false|miss "note"`, and `report [--since 7d]`, which
  prints fair catches / false stops / misses / untagged plus a separate
  **blocks suppressed** count (`advisory-forced` records — a real block
  demoted to advisory). An `advisory-forced` record later tagged fair or
  false counts on both lines — being suppressed and being judged right or
  wrong are different questions — and `report` prints a one-line footnote
  naming how many blocks-suppressed records are also tagged, whenever
  that's nonzero. `tag` refuses, **exit 3**, a tag that contradicts its
  record's own decision (`false`/`fair` only fit `block`/`advisory-forced`;
  `miss` only fits `allow`/`advisory`/`unchecked`). An unparseable
  `--since` value also refuses with **exit 3** — the same code as the tag
  contradiction, since neither is an argparse usage error (argparse
  already accepted the flags fine in both cases) and neither is the
  generic "missing input/door" refusal (exit 5); usage errors (a bad
  flag argparse itself rejects) stay on argparse's own exit 2, so a
  caller can still tell "bad flag" apart from "refused for a domain
  reason" by exit code alone. A record with no parseable timestamp is
  excluded from a real `--since` window and counted on its own
  `undated: N` line. Re-tagging the same id
  is an upsert, not a second tag: at most one catch case exists per record
  id, a re-tag between `false`/`miss` replaces it, and a later `fair`
  withdraws it. The whole `catch tag` read-modify-write (ledger rewrite
  and catch-cases upsert together) runs under one `flock`-held lock file
  so parallel `catch tag` commands on different ids never lose a write.

  Tagging a record `false` or `miss` writes (or replaces) one **catch
  case** — `{id, ts, door, kind, draft, payload_path, reasons, note}` — in
  a single JSON array file (`SUPERJEV_CATCH_CASES`, default
  `catch-cases.json` next to the catch ledger). This is a new,
  catch-ledger-specific shape, not the existing `gate-bench-*` case shape
  those replay scripts read — a catch case has no transcript anchor to
  replay against. New `skills/super-jev/tests/replay_catch_cases.py` reads
  it directly and, for any case with a `payload_path` (opt-in
  `SUPERJEV_CATCH_KEEP_PAYLOAD=1` saved a redacted hook-payload copy at
  decision time), re-runs the original gate/verify decision offline
  through `SUPERJEV_GATE_CMD`/`SUPERJEV_VERIFY_CMD`. The script refuses to
  run at all, exit 2, unless every door a case needs has its env var set,
  or `--live` is passed — it never falls back to the real fleet door on
  its own, and never sets or reads `TYPESAFE_API_KEY`. A case that cannot
  be replayed is reported with its own reason (no payload / payload
  unreadable / payload shape unsupported for this door / door failed)
  instead of one generic message. See `docs/hooks.md`, "The catch
  ledger".

- **Fetch none gate: floor + margin.** The none gate's confidence floor drops from 0.80 to a new default of **0.60**, and a new **margin** check joins it (`--margin N`, default **0.10**): the gate now also asks a clarifying question when the gap between the top pick's confidence and the runner-up's is below the margin, even when the top pick alone clears the floor. Rationale, in words: a confident-looking top-1 sitting in a crowded field — a runner-up almost as confident — is still a guess, just a confident-sounding one; the floor alone can't tell "clearly the best answer" apart from "the least-bad of two nearly-tied answers," and the margin is what catches the second case. An offline replay of saved live fetch rankings found the floor+margin pair served more correct top picks with fewer wrong serves than the old floor-only rule, described here in words rather than as benchmarked numbers. Both are overridable via `--floor`/`--margin` and the `SUPERJEV_FETCH_FLOOR`/`SUPERJEV_FETCH_MARGIN` env vars (a CLI flag always wins over its env var); the old floor-only behaviour is still reachable with `--floor 0.80 --margin 0`. `applyNoneGate` takes a third `margin` parameter; new `topMargin` export computes the gap (a lone top pick with no runner-up counts its own confidence as its margin). The `noMatch` decision (nothing beat "none of these") is unchanged and still takes precedence over both checks.

- **Fetch v2**: a catalog schema (`src/enhance/catalog.ts`) — a v1 record stays `{id,text}`; a v2 record adds optional `utterances` (realistic user phrasings), `negatives` (near-miss phrasings that should NOT route here) and `tags`, drawn from the tool/skill-retrieval literature (ToolRet, SkillRet, aurelio-labs/semantic-router's `Route(utterances=[...])`). `loadCatalog`/`parseCatalogText` accept v1, v2, or a mix in one array. New `npm run catalog -- validate <file>` reports records with fewer than three utterances, the same phrasing claimed by two records, and a negative that is word-for-word another record's utterance.
  Local narrowing (`prefilterCatalog`) now scores `text` and `utterances` (utterances weighted 2x by default), subtracts `negatives`, and folds `--context` (a JSON array of recent user turns, `--context-turns` keeps the trailing N, default 3) into the query at a lower weight, so a referent like "restart it" inherits its subject from a prior turn; a v1-only catalog with no context scores identically to the old text-only pass. `--prefilter`'s default drops from 40 to **8**, now that local narrowing is trusted to do more of the work before the one Jev call (`DEFAULT_PREFILTER`).
  A **none gate** (`applyNoneGate`, `--floor N`, default 0.80) sits after the judge: when nothing beat "none of these", or the top pick's confidence is below the floor, the result is `{noMatch: true, candidates: [top 3 by score], ask: "<one clarifying question built from their ids>"}` instead of a best guess; otherwise `{noMatch: false, ranked}`. `runFetch` now also reports `allScored` (every judged record, whether or not it beat none) so the gate always has something to offer even when nothing ranked.
  A **feedback loop**: `npm run fetch -- ... --record <chosen-id> [--ledger FILE]` (default `.superjev/fetch-ledger.jsonl`) appends `{request, context, ranked, chosen, ts}` after scoring; `npm run catalog -- learn <ledger> <catalog> [--out proposals.json]` mines lines where the human's `chosen` id differed from the run's own top pick into proposed new utterances, written to a separate file — the catalog is never edited silently.
  `bench/fetch-bench.ts` is now an **admission bench**: cases move to `{request, context?, expected_id}`, a seeded `--holdout` split (default 0.3, `--seed` 42) simulates one round of `catalog -- learn` on the train split only (so a holdout request's own text is never fed to its own narrowing pass), and it reports hit@1, hit@k, no-match precision/recall and calls per request on the holdout split, exiting non-zero when hit@1 falls below `--min-hit1` (default 0.8). No live API calls, no embeddings, no new dependencies — the local narrowing pass is still plain BM25-lite; only Jev is ever called, once.

- Stress-test follow-ups. `fetch`: every batch question carries a "none of these" option; a record ranks only if it beats it, and a run where `none` wins everywhere returns `ranked: []` with `noMatch: true` and the confidence of `none`. New `--prefilter N` (default 40, `0` disables): a local token-overlap (BM25-lite) pass keeps the top N records before the one provider call; `calls` is reported and is 1 when the prefilter is at or under the per-call size. Fetch's own token window default is 16000 so 40 short records fit one call. Five no-match cases added to `bench/fetch-cases.json`. Hook gate: a Stop turn with no tool_result evidence no longer passes silently; the gate runs against the user's last prompt, prints one advisory line only if it reports a checkable claim, never blocks, and the ledger line carries `unchecked: true`. Permit: `format` is destructive only with a storage target (disk, drive, volume, partition, SD card, USB, `mkfs`, `diskutil erase`); "format the code" and "preview the changes a formatter would make" no longer trip. Wishlist items 4, 5, 6 marked built (6 experimental).

- Add an installable Claude Code agent skill at `skills/super-jev/` (`superjev.py`, `SKILL.md`, tests): one front door — `gate`, `verify`, `sweep`, `bench`, `ask`, `status`, plus `permit`/`chain`/`fetch` stubbed to name their own wishlist item instead of failing silently. `gate` and `verify` wrap external claim-gate and report-verify tools via `SUPERJEV_GATE_CMD`/`SUPERJEV_VERIFY_CMD` (no default tool ships in this repo); `sweep` and `bench` default `SUPERJEV_REPO` to this checkout's own root, so they need no env at all. Add `docs/wishlist.md` (the roadmap behind the stubbed doors, with every measured figure and internal path stripped) and a "Agent front door" section in the README and in `docs/agents.md`. Tests run with `python3 -m pytest skills/super-jev/tests -q` or `npm run test:skill`; CI runs them on `ubuntu-latest` via `actions/setup-python`.

Fixes:

- **Stop-hook gate: `budget-exceeded` now means an unjudged reply, and nothing else.** After the Stop-event budget landed, the ledger filled with `budget-exceeded` records for events that had in fact completed and been judged, and `status` reported lost checks that never happened. The records were not the gate's. They came from the advisory teammate-report scan, which defers its worker-report checks when the event has no live call left for it or too little wall clock to finish one honestly — and logged that deferral under the *gate's* reason string, `budget-exceeded`, which the health monitor maps to its **LOST** bucket. The two events are not the same thing at all: the scan deferring costs nothing, because the reply itself is still judged by the gate on that same event, and the scan's state file only advances past reports it actually attempted, so the deferred ones come back on the next Stop event. It was also not occasional. At the shipped defaults the scan can never take a call, since `SUPERJEV_GATE_MAX_CALLS` is 1 and the gate's verdict reserves it, so *every* Stop event that saw a new worker report wrote one of these — a permanent false warning on any session that spawned workers. The scan's deferral now logs its own reason, `stop-scan-deferred`, bucketed **DEFERRED**, and says "deferred, not dropped ... retried on the next Stop event" rather than "not judged". `stop-scan-timeout`, the scan's other deferral, moves from LOST to DEFERRED for exactly the same reason. `budget-exceeded` is left to the two gate paths that genuinely ship an unjudged reply, and stays LOST there, warning on the first occurrence. The deferral message also names the call allowance before the clock, because at the defaults that is the real reason it defers — the old message blamed a 15-second wall clock that had not yet spent a second. Tests pin all of it: a completed, judged Stop event writes no `budget-exceeded` record and raises no LOST count even when a new worker report makes the scan defer, and the bucket table pins both scan reasons as DEFERRED. Genuinely lost checks, `bad-stdin` among them, still warn — and since the health window is a rolling last-N of hook runs, an old test artifact ages out of it rather than warning forever.

- **Stop-hook gate: one live call, one wall-clock budget, one window cap.** A Stop event had no bound on how long it could take, and on a heavy turn it kept the user waiting long enough to notice. Almost none of that wait was the gate's own verdict — it was the advisory teammate-report scan, which ran a live `verify` check for every new worker report it found, one after another, before the gate check ever started; its own budget was checked only *before* each report and never during one, so slow checks went straight past it. Three changes. `SUPERJEV_GATE_MAX_CALLS` (default 1) is now the whole event's live-call allowance and the gate's verdict has first claim on it, so the advisory scan spends only what is left — nothing, at the default — and defers its reports to the next Stop event exactly as it already did on a timeout, advancing its state file only past reports it actually attempted. `SUPERJEV_GATE_BUDGET_S` (default 15 s) bounds the event end to end and clamps every child check's timeout to what remains of it; when it runs out before the gate can judge, the gate prints one line saying the budget ran out and the reply was **not checked**, exits 3 (advisory — this path never blocks), and logs reason `budget-exceeded`, which sits in the ledger health monitor's **LOST** bucket so an unjudged reply is never quiet. `SUPERJEV_STOP_SCAN_MIN_S` (default 20 s) stops the scan starting a check it can already see it would have to kill, and `SUPERJEV_STOP_SCAN_MAX_BYTES` (default 2 MB) reads only the transcript's recent tail instead of a multi-megabyte whole. Finally, the window was capped in three places that did not compose — the builder capped what it assembled, the caller appended a cited-file block *after* that cap, and the facts step re-applied the cap only when it had a fact to put at the head, returning over-cap text untouched otherwise. `SUPERJEV_GATE_WINDOW_TOK` (default 8000) is now the single cap, applied to the finished text immediately before the call, giving sections up in priority order: previous turns oldest first, then the session-receipts backing layer, then the cited-file tail, then this turn's worker reports. Derived facts and the current turn are never given up; a section that can cover the overflow out of its own head does that instead of being dropped whole, and if facts plus the current turn alone exceed the budget the facts stay whole and the current turn keeps its tail. `hook gate --explain` prints the budget, the size before and after, and what was dropped or shrunk. One honest limit is documented rather than hidden: a pre-call size estimate cannot see a `verify` check's real input at all, because worker-verify gathers its own git, diff and pull-request evidence inside the check, so wall-clock is the only latency control that works there. A follow-up timing pass over a much longer live hang confirmed where the waiting is: the gate path shells out to nothing but the judge — no lock, no network, no `git` or `gh` — and finishes its local work in well under a second even on the largest session transcripts here, which a new test now pins. Every long-timeout wait in the skill belongs to a verify path, much the largest being a *test command derived from a worker's report*, which the scan hands to the verify door to run and which the dry-run evidence probe then gathers and runs a second time. That probe took no timeout at all, so the budget never reached it; it, and the `git remote` lookup before it, are now each clamped to the remaining event budget. `SUPERJEV_STOP_SCAN_MAX_BYTES` (default 2 MB) bounds the scan's own transcript read to the tail, while the gate window builder still reads the whole file on purpose — it walks back from the end to find the turn boundary and must not see half a turn. See `docs/hooks.md`, "The latency budget".

- **Stop-hook gate: DERIVED FACTS at the head of the evidence window.** The gate shipped raw command output into the window and asked a language judge to infer state from a column dump. The 2026-09-17 gate analysis found the remaining misses were all that one defect, with the refuting string already present in the window: a draft claiming it had deleted a card while a post-write listing row still showed that card and the only `REMOVED:` receipt named a different one; a draft claiming a card "rides every turn" against the window's own `untagged -> role-default (... ~ every 152t)` line; a draft making a universal claim over a mismatch table whose rows were every one of them *stricter* than expected, which is what made the claim true rather than false. A judge catches "evidence says X, draft says not-X"; it is weak on reading a column dump as authoritative state and on noticing that a receipt is absent. The gate now states the answer as a sentence, in code, before the judge reads anything: a `DERIVED FACTS` block at the HEAD of the window, the raw window below it unchanged as `BACKING`. Four families, all literal string and integer work over text already in the window plus the draft — delete/remove claims (every removal receipt named; an identifier claimed removed but still on a listing row stated as still present), cadence claims (the window's own cadence line quoted, `~ every Nt` turned into the comparison), result tables (an `N of M` line or `(case, expected, actual)` rows counted whenever the draft makes a universal claim sharing a label with the table, including how many mismatch rows were stricter than expected versus more permissive), and merge/CI claims (every merge receipt named with its PR number, every PR the draft says was merged with no receipt named as missing one). No model call, no extra evidence bytes, no threshold or rule change. The cap applies AFTER the facts: a fact is never dropped, and the raw window's head is trimmed instead, since its tail — the current turn — is already the highest-priority segment. With nothing derivable the door receives the window byte-for-byte unchanged. `hook gate --explain` prints the facts and what the cap trimmed; `SUPERJEV_DERIVED_FACTS=0` restores the old window. The logic lives twice on purpose: `windowFacts` in `src/enhance/derive-facts.ts` (canonical, reachable through `node src/derive-facts-cli.ts` as `evidence.window`) and a pure-Python mirror in `skills/super-jev/superjev.py` that the hook itself uses, because the hook runs every turn, the bridge pays a whole node process's startup per call, and on a fleet install this repo is not on disk next to the skill at all. One shared fixture, `test/fixtures/gate-window-facts.json`, pins both sides to the same fact sentences. See `docs/hooks.md`, "Gate v3 — derived facts at the head of the window".

- **Stop-hook gate: a worker's report is now evidence, and a count carries the command it came from.** Two window defects, both found by reading blocked cases back against their transcripts. (a) A worker or teammate report never arrives as a `tool_result` — Claude Code delivers it as a `role: "user"` text record, either a `<teammate-message teammate_id="...">` block or a harness `<task-notification>` whose `<summary>`/`<result>` holds the report. The evidence window was built from `tool_result` blocks only, so the judge could not see any of it, and several drafts that were faithfully relaying a worker were blocked as if they had invented the numbers. Those blocks are now carried, from the current turn and the previous turns the window already reaches over, each labelled `REPORT FROM <who> (unverified worker claim)` so a relayed claim is still told apart from a receipt. They ride at current-turn priority inside the same window cap, capped at `REPORTS_BUDGET_SHARE` of the room left so a long report cannot starve the previous-turn block, dropping the oldest report first. (b) The deterministic count arm paired counts across suites: the node:test recogniser was `\s*#?\s*pass\s+(\d+)` and node:test prints `ℹ pass 158`, where the glyph is not whitespace, so the true count went unmatched while a stale count from another repository paired against the draft instead. Both `ℹ pass N` and `ℹ tests N` are now recognised, and every receipt and carried tool result is labelled `[from: <command> @ <cwd>]`, read off the `tool_use` input the transcript already holds. A draft count pairs only with evidence counts whose runner family or repository/package path the draft or the current turn actually names; the shell cwd is not identity, since every run in a session shares it. When nothing pairs the arm stays silent — an unpairable claim is an evidence gap, not a lie. `hook gate --explain` prints the reports it carried and, per unit label, the draft counts, the evidence counts paired with them, what matched, and which counts were scoped out by command identity and from which run. See `docs/hooks.md`, "Gate v3 — worker reports and count identity".

- Bound journaling by the run deadline. A journal whose append never resolved held a run open past its `timeoutMs`; every append, including the opening and closing records, now goes through the same abort signal. A failed or timed-out intent record stops the step before the effect and reports "action not attempted"; if the effect ran and only its completion record failed, the run reports "action outcome unknown". The run result gains a `journaled` flag.
- Reject special files in the organizer CLI without blocking. Input is opened with `O_RDONLY | O_NONBLOCK` and its type is taken from `fstat` on the descriptor already held, so a named pipe with no writer is refused immediately instead of parking the process inside `open()`. This also closes the check-then-reopen window. Byte limits, no-echo error messages and descriptor cleanup are unchanged.
- Check a `score` against its own distribution. The provider documents `score` as the probability-weighted mean of the level indices, which may land between levels, so `validateEvaluation` now compares it to that expectation. It previously accepted `score: 0` alongside probabilities `{0: 0, 1: 1}`.
- Accept a rounding-sized shortfall in a probability total. The previous 0.001 tolerance discarded all 32 answers of a recorded batch because two distributions totalled 0.99. The band is now one rounding step of 0.01; inside it the values are renormalized and the provider's untouched values are retained on the answer as `rawProbabilities`; outside it the answer is rejected. A total of 0, a total below 0.99 or above 1.01, a negative value, a `NaN`, a missing level and an extra level all still fail, nothing is substituted, and a validation failure still triggers no retry of any kind.
- **Do not** reject a `confidence` above its distribution peak. An earlier draft of this change added that rule as an inferred one, flagging it as the single rule a live probe could overturn. One live call on 2026-09-17 overturned it: `jev-1.13.0` returned a `score` answer with `confidence` 0.71 over a peak probability of 0.66. The rule is gone rather than narrowed to `choice` answers, because the docs give no formula for `confidence` and the score counterexample leaves no basis for asserting the relationship anywhere. `confidence` is now checked only for what the provider documents, a finite number in `[0, 1]`. The live response is checked in as `test/fixtures/live-score-2026-09-17.json`.
- `validateEvaluation` no longer writes to the response it is given. It returns the evaluation to use downstream, the provider's parsed reply is left as it arrived, and a frozen response validates. The loop journals and decides on the returned object. Validating an already-validated evaluation returns an equal object.
- Never trust a provider-supplied `rawProbabilities`. That field is this harness's audit record of what was actually returned. On the renormalizing path an incoming value is overwritten with the provider's real probabilities; otherwise it is accepted only if it renormalizes to them, and rejected as `Untrusted rawProbabilities` if not.
- Add `docs/provider-contract.md`, which labels every claim about the response contract DOCUMENTED, OBSERVED or INFERRED, and `scripts/reproduce-findings.py`, an offline reproducer for the three findings.

Validation: 50 automated tests (26 before, 24 added), all three offline demos, and the three offline reproducers now passing.

Limitations:

- **One live call only.** A single call to `jev-1.13.0` on 2026-09-17 returned one `score`, one `choice` and one `noul` answer; it is checked in as a fixture. Every other measured number cited here and in `docs/provider-contract.md` comes from 30 previously recorded calls to the same model version.
- The 0.01 rounding band is INFERRED, not promised by the provider, which documents no rounding contract at all. The live response totalled exactly 1.00 and so neither strengthens nor weakens it.
- The score expectation rule has now met real provider output exactly once, and matched it exactly: score 1.85 is the exact probability-weighted mean of the live distribution. One answer from one model version is a confirmation, not a guarantee.
- **Mitigations are still pending.** The classification order-sensitivity, the context and evidence-completeness work, named record references, bounded batching and source-completeness checks are not in this change. The robustness report's outstanding-benchmarks list stands in full.

## 0.2.0

- Rename package metadata to super-jev to match the repository.
- Add a configurable data-organizer domain pack that runs through the existing loop.
- Add a JSON CLI with explicit live/demo modes, output protection and a review queue.
- Document input/output contracts for terminal-equipped LLM agents.
- Add tests for grouping, review routing, validation, CLI output and overwrite protection.

Validation: 26 automated tests, both original offline demos, and the organizer offline demo. Regression coverage includes private error handling, review-policy enforcement, output permissions, failure cleanup, preventing inference when output exists, excluding incidental record metadata, bounded input reads, and the successful mocked live-response contract. One live organizer smoke test on `jev-1.13.0` classified all four synthetic records as expected, grouped three records, and routed the `other` record to review. Low-confidence routing is covered by mocked tests. See [organizer validation](docs/organizer-validation.md) for results and limits. This release remains experimental; no npm publication, MCP server, or exchange integration is included.

## 0.1.0

Initial harness, Jev adapter, two domain demos, JSONL logs and 13 tests.
