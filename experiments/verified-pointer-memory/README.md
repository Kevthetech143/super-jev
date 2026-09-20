# Verified pointer memory — local experiment

Register a reviewed dataset once. Send its pointer and a question. The harness
checks freshness, returns a verified exact-repeat answer when available, or runs
Super Jev retrieval. The output says what to do next. A reviewing agent approves
fresh answers with citations before they enter memory.

This is an opt-in experiment, not an installed production feature. It includes
its driving instructions in `skills/super-jev/references/verified-reuse.md` and a
machine-readable control panel. It has no private account, card, or Mac-path
dependency. The default live bridge calls the existing repository retrieval
engine; it does not reimplement ranking or silently use another model.

## Who can use it

| Situation | Support |
|---|---|
| A developer or agent on a trusted local machine | Yes, with Python 3.10+, Node 24+, reviewed local data and writable private storage |
| Inspect options, register pointers, run synthetic tests | No provider account or API key needed |
| Make live Jev retrieval calls | Supply your own `TYPESAFE_API_KEY`; your provider account's terms and charges apply |
| Supply another trusted retrieval adapter | Optional administrator-set `retrievalCommand` argument array |
| Host untrusted users or agents behind a shared API | No: caller names are scope labels, not authentication |
| Point at arbitrary raw files and obtain automatic complete coverage | No: descriptions and passage preparation require review |
| Leave it autonomously reviewing a queue for hours | Not yet: no scheduler or autonomous reviewer is bundled |

## Control panel and quickstart

From the repository root:

```sh
python3 skills/super-jev/dispatch.py memory --describe
python3 experiments/verified-pointer-memory/make_example.py .local/pointer-demo
python3 skills/super-jev/dispatch.py memory --config .local/pointer-demo/config.json --input .local/pointer-demo/register.json
python3 skills/super-jev/dispatch.py memory --config .local/pointer-demo/config.json
```

`--describe` returns actions, input fields, supported settings, prerequisites,
result actions and limitations, including the freshness policy and its limits. The configured panel also lists dataset
coverage descriptions and the caller's pointers with their readiness.
`--principal NAME` selects the panel's local scope; it is not login/authentication.
The example generator creates only a fixed public synthetic record, refuses an
existing output directory, and never auto-approves your private documents.

After setting `TYPESAFE_API_KEY` in your environment through your normal secret
management, submit the sample question:

```sh
python3 skills/super-jev/dispatch.py memory --config .local/pointer-demo/config.json --input .local/pointer-demo/question.json
```

A `ready` result contains passages and an `approvalTicket`. Verify the answer,
then save this JSON as an approval input (replace the ticket with the actual one):

```json
{"action":"approve","ticket":"returned approvalTicket","principal":"local","approved":true,"answer":"The example project launch color is blue.","evidence":[{"sourceId":"launch","quote":"The example project launch color is blue."}]}
```

Submit it with the same command's `--input` option. Submit the question again:
a successful verified repeat returns `verified-cache-hit` and its citations,
without Jev calls. An exact quote's presence establishes provenance; the reviewing
agent must still judge whether it supports the complete answer.

For an installed skill outside the checkout, set `SUPERJEV_REPO` to your checkout.
The direct equivalent entrypoint is `python3 experiments/verified-pointer-memory/cli.py`.

## Configuration and actions

Private config requires `db` and `registry` paths. Relative config paths resolve
against the config file. The existing reviewed-dataset registry binds a manifest,
originals, descriptions, and approved passage offsets/hashes. This experiment
requires absolute paths inside the registry/manifest. Source URLs are unsupported.

| Setting | Default | Meaning |
|---|---:|---|
| `cacheTtlSeconds` | 86400 | Maximum answer lifetime; choose shorter for time-sensitive information |
| `reviewTtlSeconds` | 600 | Time allowed to approve a fresh retrieval ticket |
| `providerTimeoutSeconds` | 120 | Whole retrieval subprocess timeout |
| `retrievalCommand` | Bundled Node bridge | Optional trusted executable argument array; receives JSON on stdin and returns JSON |
| `allowAgentAssist` | false | Operator-enabled recovery from existing reviewed sources; never automatic approval |

Time settings must be positive finite numbers. Cache/review TTL changes apply to new answers/tickets; remove or re-register a pointer to discard existing entries immediately. Unknown options are rejected.
No options disable source verification or auto-approve answers.
Inputs are JSON files; every result has `status` and `nextAction`:

```json
{"action":"register","pointer":"my-brain","dataset":"reviewed-brain","principals":["worker"]}
{"pointer":"my-brain","question":"Where is the policy?","principal":"worker","context":"project/person/as-of scope"}
{"pointer":"my-brain","question":"What is Alice's current medication list after reconciliation?","principal":"worker","freshness":{"mode":"current","maxAgeSeconds":3600}}
{"action":"remove","pointer":"my-brain"}
```

`register` and `remove` are administrative actions for the trusted local operator.
Search defaults to `{"mode":"snapshot"}`. Snapshot means the reviewed
manifest/original hashes still match the registered snapshot; it must not be
described as current information. A current request requires exactly
`{"mode":"current","maxAgeSeconds":positive finite seconds}`. Its dataset
registry entry must contain `checkedAt`, finite Unix seconds no later than the
request, recorded by a trusted upstream check of the *entire* dataset scope.
Missing, future, or expired `checkedAt` returns `refresh-required` with
`nextAction: refresh-source-and-preparation` before a cache lookup or provider
call. Current ready and cache-hit responses include the mode, `checkedAt`, and
deadline. Current cache entries cannot outlive that deadline.

`checkedAt` is neither an event/effective date nor proof that a fact remains
true. It is not inferred from file modification times. This experiment does
not fetch sources, watch for changes, or infer supersession. The reviewer must
still resolve contradictions and verify the relevant person, date, version and
record status before approval; contradictory evidence must not be approved
until resolved. Include those facts in the original question. `context` scopes
the exact cache key, so a new context cannot reuse an old-context answer, but
the retrieval provider receives only `registry`, `dataset`, and `question`.

| Question type | Evidence the reviewer must establish |
|---|---|
| Old completed test vs scheduled test | A scheduled test is planned, not a completed result. |
| Medication list | A list recorded as of a date is not a verified current reconciliation; a newer note does not automatically supersede it. |
| Software fix | The fix applies to the named version; verify the version in question. |
| Internet fact | Published or updated date is not the last trusted upstream whole-scope check. |

The optional retrieval command receives `registry`, `dataset`, and `question`.
Only a successful command with a recognized retrieval status is accepted.
Provider stderr and credentials are not returned through the interface.
Read [the data lifecycle contract](DATA-LIFECYCLE.md) before onboarding or
refreshing a dataset; it documents the supported controls and the responsibilities
that this local harness cannot automate.

## Storage and lifecycle

Originals stay in place. The existing registry/manifests own dataset preparation.
One SQLite database owns pointers, pending review tickets and approved answers.
Pointers bind a dataset generation and fingerprint; answer and ticket rows refer
to that generation rather than duplicate the entire dataset. Answer evidence
keeps source IDs, hashes, reviewed quotes and line locations. Exact cache keys
include question, principal and relevant context.

Changed files invalidate reuse. Re-register only after review/refresh; doing so
rotates the generation and removes prior associated cache/tickets. Removing a
pointer also deletes its associated rows; this is logical deletion, not secure
erasure of SQLite pages/backups. CLI-created files use a restrictive umask;
protect the containing directory and use your normal disk protection/backups.
Existing legacy experiment databases are refused rather than destructively
migrated. Source files and registries are not changed by cache operations.

## Agent loop and limits

The bundled skill describes submit → verify → approve → reuse. A cheap agent may
review questions within its authorized scope. Queue checkpoints, budgets and
scheduling remain the caller's responsibility in this version. Keep unresolved
items distinct from successful cached answers. Do not repeatedly retry a failure
until it becomes ready; repair coverage/preparation or obtain new evidence first.

Exact matching only; no semantic cache reuse or guaranteed answer correctness.
An incorrect reviewing-agent decision can still cache an incorrect interpretation.
Trusted local storage is not tamper-proof or encrypted by this code. Source checks
are point-in-time reads, not a transactional filesystem snapshot. No untrusted
multi-tenant access control, global suppression of simultaneous cold requests,
automatic arbitrary-file ingestion, or background scheduler is provided.

## Tests and evidence

```sh
python3 experiments/verified-pointer-memory/stress.py
python3 -m unittest discover -s experiments/verified-pointer-memory -p 'test_*.py'
```

Synthetic tests verify storage/failure mechanics; they do not measure Jev accuracy.
`RESULTS.md` separates real-provider development questions from synthetic load. Run `python3 experiments/verified-pointer-memory/scale_probe.py` for optional synthetic dataset-size measurements.
Private live traces, sources, keys, databases and preparation artifacts must not
be committed. These small tests do not prove arbitrary-data reliability, hours-long
worker endurance, or end-to-end token savings.

Freshness lifecycle test scope and live project-memory observations: [FRESHNESS-RESULTS.md](FRESHNESS-RESULTS.md).

## Agent-assisted recovery and persistent audit

Configure persistent `db` and `registry` paths for the agent/project; a pointer binds a reviewed dataset, not an arbitrary directory. The panel shows resolved storage destinations, source coverage, assistance limits, and optional advisory hints. Hints recommend a next step; they never change permissions or force a user prompt. Supported schema-2 databases migrate atomically to schema 3, preserving existing caches and pending tickets while adding an attempt ledger. Back up the database before upgrade; old runtimes cannot read schema 3.

Fresh retrievals return `attemptId`. The database preserves original request/context, status, trace, source binding and any reason; it does **not** archive every raw passage response. The caller retains complete raw responses in its configured private work artifacts. Removing/replacing a pointer clears cache/tickets but retains attempt metadata for trusted local audit. The public `attempt` action only exposes records authorized by the matching current pointer generation. Approved answers carry `resolution` (`retrieval` or `agent-assisted`) and `originatingAttemptId`; that resolution metadata lives with the cache entry and is not a permanent answer-history archive.

When the operator has enabled `allowAgentAssist`, an agent may recover a partial, `no-match`, or `refused` result within the authorized dataset:

1. Preserve the original result. Inspect `attempt` and paginate `sources` to locate registered source IDs, descriptions, paths and line counts.
2. Independently investigate those sources, then submit `assist` with the original attempt ID, reason and concrete source-line references. New sources require reviewed onboarding, registry refresh and a new original attempt.
3. Review the returned passages against the **entire** question. `assist` never approves or calls Jev. Unresolved contradictions or absent evidence remain unresolved.
4. Approve using the returned `evidenceId` values, then repeat the original question/context to check reuse. IDs avoid copied-quote errors; they do not establish semantic correctness.

Example action files:

```json
{"action":"sources","pointer":"my-brain","principal":"worker","offset":0,"limit":25}
{"action":"attempt","attemptId":"returned-attempt-id","principal":"worker"}
{"action":"assist","attemptId":"returned-attempt-id","principal":"worker","reason":"Found the missing policy section","references":[{"sourceId":"policy","startLine":20,"endLine":24}]}
{"action":"approve","ticket":"returned-ticket","principal":"worker","approved":true,"answer":"Complete supported answer","evidence":[{"evidenceId":"returned-evidence-id"}]}
```

Use at most one assistance attempt per question unless a concrete input error can be corrected. Sources page at 25 by default, at most 100; assisted evidence is limited to 20 preparations and 60,000 reviewed characters. Freshness, source version, principal and pointer binding are checked again before approval. This feature neither edits originals nor trains Jev, and does not guarantee instant answers to new or changed questions.
