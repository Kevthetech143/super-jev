# Verified reuse: input, output, next action

The intended agent contract is a registered pointer plus the original question and relevant context. The harness owns freshness checks, exact-cache lookup and bounded retrieval passes. The caller owns checking that the evidence actually supports the answer.

## Installed production path

Use `find --list-datasets`, then `find --dataset NAME --request QUESTION`. Dataset names are existing pointers to reviewed material. Verify whose data and how much of it the scope covers. A raw folder path is not reviewed preparation. Follow the sibling retrieval skill for setup/refresh; do not merely change hashes to silence a stale result. Production `find` has no verified-answer cache API yet.

## Opt-in pointer/cache experiment

Only when explicitly configured from the Super Jev checkout, use the front door: `dispatch.py memory --config PRIVATE_CONFIG.json --input REQUEST.json`. Run `dispatch.py memory --describe` for the experiment's machine-readable control panel. See the [experiment README](../../../experiments/verified-pointer-memory/README.md) for one-time registration and input schema. Set `SUPERJEV_REPO` when the dispatcher is installed outside its checkout. It is an opt-in local experiment, is not installed automatically with this skill, and is not production authentication. Never substitute a guessed command when it is unavailable; preserve its structured missing-dependency error.

For each real queued question:

- Submit pointer, original question, caller identity and relevant project/person/time context once.
- `verified-cache-hit`: use the cited answer within that context.
- `ready`: inspect supporting passages and original-source provenance. Submit one `approve` input containing the complete supported answer and exact evidence quotes only after verification. Partial evidence, model confidence and repeated agreement are insufficient.
- `preparation-required` or `unknown-pointer`: retain the pending question and repair/register the approved scope through the setup workflow. Report incomplete coverage; do not search a different person's data.
- `no-match`, `refused`, `error` or access denial: preserve the outcome and record a miss when appropriate. No-match is not proof of absence. Continue with other queued questions; retry this item only with a justified repair or explicitly designed diagnostic test.

A cheap agent may perform review within its authorized scope; it does not gain authority to self-approve new private source material or relax verification. Keep a durable queue checkpoint and a time/call budget. Stop on queue exhaustion, cancellation or budget exhaustion. The prototype persists cache and tickets, but has no autonomous queue scheduler or production authentication boundary; do not promise unattended hours of work merely because this skill describes the loop. Consult experiment results before claiming a reliability or cost benefit.
