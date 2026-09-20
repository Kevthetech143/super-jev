---
name: super-jev
description: "Use Super Jev to find skills, retrieve supporting information from reviewed brains/documents, check claims against evidence, or verify agent work. One front door over existing tools; returned evidence still needs caller verification."
---

# Super Jev

The user says “Use Super Jev to…”; choose the matching tool below and run it. Do not require the user to learn backend skill names or command syntax. Use the original request and relevant context; ask only if the intended task or required evidence is genuinely unclear.

Use `python3 <this-skill-directory>/dispatch.py <tool> ...`. With no tool (or `tools`), it lists the four options. The agent interprets natural language; this dispatcher only executes the chosen tool. The legacy `superjev.py ask` keyword router is not the unified natural-language entry point.

| User wants | Tool | How to use it |
|---|---|---|
| Find a skill or capability | `skills` | Read the sibling `skill-search/SKILL.md`. Save the original request plus needed context as its request JSON, then run `skills --request-file REQUEST.json`. Claude roots are selected automatically for a Claude installation. Suggestions are advisory; load the chosen skill before acting. |
| Find information in brains or documents | `find` | Read sibling `fleet-retrieval-experiment/SKILL.md`. Run `find --list-datasets`, choose the relevant reviewed scope, then `find --dataset NAME --request "original question"`. Existing manifest, request-file, neighbor and dedup options pass through unchanged. |
| Check a claim or draft | `check` | Read [checking tools](references/checking.md), specifically the gate instructions. Run `check EVIDENCE... --claim "claim" --json` or use `--draft FILE`. This calls the existing gate. |
| Verify an agent's work | `verify` | Read [checking tools](references/checking.md), specifically verification. Run `verify REPORT --worktree PATH --test-cmd "authorized check" --json` with the evidence required by the existing verifier. |

Read only the selected tool's guidance, not every backend. The siblings live in the same skill root as this skill; checking tools retain the existing deployment configuration. A missing dependency is reported explicitly. Do not silently substitute ordinary search, another model, or a different dataset. If the backend reports fallback, refusal, missing preparation or uncertainty, preserve that outcome and explain it; an explicit user-authorized alternative is a separate action.

`ready` means selected supporting passages, not a complete or correct answer guarantee. Check support before answering. Review view/original provenance when offsets refer to a prepared view. Dataset names describe selected reviewed coverage, not an entire brain. Source changes still require refreshed preparation; this front door grants no new privacy approval or permission to execute actions.

## Record misses

When an expected skill/source is missed, a wrong result is accepted, evidence is incomplete, or coverage/preparation prevents the requested lookup, record the outcome in the existing non-riding feedback card using the supported card tool. Follow this installation's `LOCAL-FEEDBACK.md` for its exact card and evidence location. Keep a brief, non-sensitive dated summary on the card: tool/request intent, actual outcome, expected result if known, and the local evidence pointer. Store private request text and passages only in the local evidence artifact. Distinguish ranking misses, coverage gaps, service failures and partial support; an expected absent-source rejection is not a failure.

Record the initial result before retries or ordinary-search rescue, and append any later resolution without erasing the miss. If the card is unavailable or not writable, preserve the local report and state that card recording is pending; never claim it was saved or create a duplicate card. Do not treat logging as permission to expose private data, change a model gate, or run an unauthorized action.

Existing advanced CLI commands, hooks, thresholds and verdicts remain available through `superjev.py` (or the installed `superjev` shim). Their reference is [checking tools](references/checking.md); do not change hook wiring merely to use this front door.
