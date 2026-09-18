# Changelog

## Unreleased

No version bump.

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
  same catch-ledger shape. In practice this makes the live verify door
  advisory-only for every report until a worktree is supplied: a real
  PostToolUse payload carries no `worktree` key and nothing exports
  `SUPERJEV_HOOK_WORKTREE`, so the gather is thin on every live call
  today. See docs/hooks.md, "verify: spawn acks and gather health".

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
  the same pieces; `bodies_fenced=True` is the flag a migration flips
  once the composer fences its bodies, and the replay prints both numbers
  so the gap is visible.

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
