# What live use taught us

Notes from running the doors against real drafts, real worker reports and real hook
payloads, not synthetic fixtures. No internal numbers here — see the provider's own
preview terms for why, and the private knowledge base if you have access to it. This
page is about what changed in our thinking, not what scored what.

## The judge is reliable. The interview is the product.

The single biggest lesson: when the judge gets it wrong, the cause is almost always
what it was shown or how it was asked, not the judge itself. A blocked true report
turned out to be an evidence-gathering bug — the check ran with no real evidence
attached, so everything came back unsupported. That is not the judge being wrong; that
is asking a question with nothing to answer it from.

The corollary: the harness's job is to build a good interview, not to second-guess the
answers. Feed it the right text, ask a hard question per claim, and trust the number it
comes back with. Chase a bad result back to the evidence and the question before you
chase it back to the model.

## Feed rules

- **Evidence is the files actually read this turn**, not a general sense of "what's
  going on." A claim checked against evidence you forgot to include comes back
  unsupported, correctly.
- **A bigger window only helps if it holds the specific fact that refutes the claim.**
  Padding a check with unrelated context does not make it sharper.
- **Ask a hard question per claim type.** A generic "is this supported?" scores worse
  on merged or status-changed claims than a targeted question aimed at the specific
  kind of claim being checked (a number, a status, a completion state).
- **One big context beats many small calls.** Batching questions into a single call is
  both cheaper and faster than the same questions asked one at a time, with no loss of
  accuracy in our runs.

## The six hook lessons

Wiring the judge into a hook (something that runs automatically, not by hand) surfaced
problems that only show up under real hook payloads:

1. **A loop guard is required.** Without one, a blocked reply's own retry can
   re-trigger the same hook on itself.
2. **Machine-generated tags get misread as content.** Internal scaffolding markers
   (board tags, acknowledgement lines) looked like claims to check, and got flagged as
   overclaiming when they were never claims at all. The fix is teaching the hook to
   recognize its own scaffolding, not to weaken the check.
3. **A background agent launch is not a worker report.** The payload a hook sees when
   a background job starts is the brief that was sent, not any result. Judging that
   text as if it were a finished report produces false alarms on spawn notices.
4. **A worker's finished report often arrives on a different channel than the hook
   expects.** If the hook is only watching one path into the conversation, reports that
   arrive by another path go unseen entirely.
5. **Scanning the conversation transcript at the natural end-of-turn point is the more
   reliable path** for catching those reports, compared to trying to catch them at the
   moment a tool runs or a prompt is submitted.
6. **A manual check needs to gather its own evidence**, not assume the caller has
   already assembled it. A check invoked with nothing behind it will fail closed on
   everything, which looks like the check is broken when really it was never given
   anything to check against.

## Why pre-split is off

Splitting a draft into one claim per number and per status word, before handing it to
the judge, sounds like it should help — it targets the exact dilution problem where an
altered detail hides inside an otherwise-true sentence. Tried live, it made results
worse, not better. Mechanically forcing a split can fragment a claim in a way that
loses context the judge needs, trading one failure mode for another. It stays available
behind a flag for further testing, not on by default.

## Why fewer, fuller calls

Early routing work called the judge once per candidate in a list, which is slow and
adds up. Restructuring to ask about the whole candidate set in one call, with an
explicit "none of these" option, cut wall-clock time dramatically with no drop in
accuracy. The general shape: one call that covers everything it can, rather than many
narrow calls, unless there's a real reason (like context size) to split.

## The calibration loop

None of the rules above were designed from first principles — they came from running
a bench of real cases (a mix of true and false drafts, or real routing requests), a
scripted read of the results, and then trying one change at a time and re-measuring on
the same set. A rule that sounds right in the abstract (like claim pre-splitting) can
still lose on measurement. Treat every threshold and every rule as a hypothesis until a
bench has actually run it twice: once to set it, once to confirm the change was really
an improvement and not noise.

## Open questions

- **Sharper per-claim questions**, tailored to the claim's type (a number, a status, a
  completion state) rather than one generic question for every claim, is an untested
  direction with a plausible payoff based on where today's misses cluster.
- **A two-pass check on borderline scores** — running a second, more targeted question
  only on results that land in an ambiguous middle band — is a real candidate for
  closing more of the gap, but it adds a call and needs to prove it earns that cost on
  a held-out set before shipping.

## Related pages

- [`docs/playbook.md`](playbook.md) — which door to use for which job, worked examples.
- [`docs/wire-into-claude-code.md`](wire-into-claude-code.md) — hook wiring in detail.
- [`docs/wishlist.md`](wishlist.md) — what's built, what's still missing, and the
  admission bar for each.
