# Source retrieval

Ask one focused English source-finding question against explicitly connected,
reviewed files under the intended principal. Use a meaningful title, one topic
per note, and explicit names and dates. The bounded validation scope is UTF-8
Markdown notes. Notes over 12,000 characters are supported, up to 250 KB (250,000 bytes)
(contract v2, 2026-09-28): they are read in sections, and a passage past character
12,000 can be found. In code: a note up to 12,000 characters is checked whole; notes of about 12,000-14,000 characters get every passage read; longer ones only the best-matching passages, up to about 14,000 characters. A note over 250 KB is outside the contract. Reading selected sections has additional recall limits.

The retrieval stage returns up to five source files. Each completed content check
uses the same rule: the text must supply a fact requested about the specified
subject. A cost component or list item can be useful without containing the full
answer. A related topic, different event, or pointer saying the answer exists
elsewhere does not itself supply the requested fact.

Ordinary source discovery asks which catalog child may supply any requested fact
or necessary input; it does not require a complete answer in one child. It uses
one relative-choice question per branch and retains the existing none-win rule.
Default catalog and claim navigation keep their original routing prompt.
Source discovery ranks candidates within a five-candidate beam and result bound
per pointer. Ordinary lookup does not reject a candidate merely because its
relative routing probability is small. Routing scores compare only inside one pointer and
see names and descriptions, not text, so unrelated files can fill every routed slot; word
search therefore adds up to five files whose text matches the question, and the content
check decides what is kept.
A routing no-match or error retains its distinct meaning. At most five routed files
plus up to five word-matched files are content-checked across pointers, so dense
collections can still hide a useful note behind that bound. Meaningful titles and focused notes matter for discovery.
The content stage asks of each passage one question, "does this passage state the answer
to the question?": it states a requested fact or supplies a necessary input to
determining that fact. Being about the same subject or topic, without the requested
property or a necessary input to it, does not qualify. An arithmetic
operand or list member can qualify while other required inputs are missing.
Subject, property, event and modality must match; a related measurement or a
pointer alone does not qualify. The same source floor applies to every query.
It was chosen during development; the score is not a calibrated probability that
an answer is true. The judge remains sensitive to wording.

Passages from one file share an evaluation; different files receive independent
checks through the batch CLI. There is no second answer-picking pass.
Routing retains its catalog navigation task. Reading more candidates can
increase provider calls and tokens; batching does not make file checks free.
Accepted files rank by content, then routing
on exact ties. File names, query keywords and a second single-answer picker do not
rescue or demote ordinary retrieval results.

Read the sources before answering. Combine evidence, calculate totals, resolve
dates and state uncertainty yourself. A returned file is a lead, not a guarantee
that the final answer is true or complete. No match means not found in this bounded
search, not proof of absence. Preparation requirements, secret holds, unfinished
checks and operational errors remain distinct from an ordinary no-match result.

Every ordinary ask starts with exactly one `OUTCOME:` line, computed from the whole
search state, and its exit code matches:

| Outcome | Meaning | Exit |
|---|---|---|
| `found` | at least one file or skill suggestion; the file count is ranked files only (skill suggestions have their own label, e.g. `5 files; 2 skill suggestions`); `partial: N sets not searched` if a set failed or is unprepared (a stale set served from its last refresh was searched: a separate `served from older catalog: N sets` line prints, not part of the OUTCOME line); `(unconfirmed: content check failed)` if the check failed and the files are routed but unread (still 0: read them) | 0 |
| `not-found` | every set searched, no file. A file no searched set checked (label failed, held for a secret, too big, edited and not yet re-admitted, not UTF-8) never changes the outcome: it is named in `left_out` and in a `partial: N files not checked` note with `--status` | 1 |
| `not-supported` | input outside the contract (empty or over-long question) | 2 |
| `error` | no file, and execution failed | 3 |
| `needs-setup` | no file, and a set was unprepared (its refresh command will run) | 4 |

A partial search is never a complete `not-found`. Every outcome except `found` names the one next command.

Claim checking is a separate task: `--claim` checks support and contradiction and
retains its claim judge. This change does not redefine claim verdicts, source
admission, principal authorization or approved-answer reuse.

Keep scope fixed during a test round. Change the contract explicitly between
rounds when needed; do not relabel observed failures as out of scope.
