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
result actions and limitations. The configured panel also lists dataset
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

Time settings must be positive finite numbers. Cache/review TTL changes apply to new answers/tickets; remove or re-register a pointer to discard existing entries immediately. Unknown options are rejected.
No options disable source verification or auto-approve answers.
Inputs are JSON files; every result has `status` and `nextAction`:

```json
{"action":"register","pointer":"my-brain","dataset":"reviewed-brain","principals":["worker"]}
{"pointer":"my-brain","question":"Where is the policy?","principal":"worker","context":"project/person/as-of scope"}
{"action":"remove","pointer":"my-brain"}
```

`register` and `remove` are administrative actions for the trusted local operator.
The optional retrieval command receives `registry`, `dataset`, and `question`.
Only a successful command with a recognized retrieval status is accepted.
Provider stderr and credentials are not returned through the interface.

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
