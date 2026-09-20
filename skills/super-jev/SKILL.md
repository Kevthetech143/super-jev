---
name: super-jev
description: "Find skills and reviewed brain/doc evidence through Super Jev source connectors. Check claims, verify agent work, and reuse explicitly approved answers with bounded reviewed-source recovery. Evidence still needs caller review."
---

# Super Jev

The user says “Use Super Jev to…”; choose the matching tool below and run it. Do not require the user to learn backend skill names or command syntax. Use the original request and relevant context; ask only if the intended task or required evidence is genuinely unclear.

For “connect my skills/brain/repo,” starting a new collection from scratch, setup questions, or explaining available connections, read [source connectors](references/connectors.md). Use connector names with humans; keep backend skill names and commands inside execution. A connector name describes the source workflow, not automatic syncing or a new command.

Use `python3 <this-skill-directory>/dispatch.py <tool> ...`. With no tool (or `tools`), it lists executable tools plus agent-guided setup workflows. `memory --describe` exposes the connector availability, setup guidance, actions, settings and limits without configuration or a key. Setup menu entries are instructions for the agent, not extra CLI commands. The agent interprets natural language; this dispatcher only executes the chosen tool. The legacy `superjev.py ask` keyword router is not the unified natural-language entry point.

| User wants | Tool | How to use it |
|---|---|---|
| Find a skill — Skills connector | `skills` | Read the sibling `skill-search/SKILL.md`. Save the original request plus needed context as its request JSON, then run `skills --request-file REQUEST.json`. Claude roots are selected automatically for a Claude installation. Suggestions are advisory; load the chosen skill before acting. |
| Find brain/document information — Brain or Documents connector | `find` | Read sibling `fleet-retrieval-experiment/SKILL.md`. Run `find --list-datasets`, choose the relevant reviewed scope, then `find --dataset NAME --request "original question"`. Existing manifest, request-file, neighbor and dedup options pass through unchanged. |
| Check a claim or draft | `check` | Read [checking tools](references/checking.md), specifically the gate instructions. Run `check EVIDENCE... --claim "claim" --json` or use `--draft FILE`. This calls the existing gate. |
| Verify an agent's work | `verify` | Read [checking tools](references/checking.md), specifically verification. Run `verify REPORT --worktree PATH --test-cmd "authorized check" --json` with the evidence required by the existing verifier. |
| Reuse an explicitly approved answer | `memory` | Opt-in local experiment only. Run `memory --describe` for its control panel, then pass the experiment's `--config` and `--input` arguments. Set `SUPERJEV_REPO` when dispatch is installed outside its checkout. |

Read only the selected tool's guidance, not every backend. The siblings live in the same skill root as this skill; checking tools retain the existing deployment configuration. A missing dependency is reported explicitly. Do not silently substitute ordinary search, another model, or a different dataset. If the backend reports fallback, refusal, missing preparation or uncertainty, preserve that outcome and explain it; an explicit user-authorized alternative is a separate action.

`ready` means selected supporting passages, not a complete or correct answer guarantee. Check support before answering. Review view/original provenance when offsets refer to a prepared view. Dataset names describe selected reviewed coverage, not an entire brain. Source changes still require refreshed preparation; this front door grants no new privacy approval or permission to execute actions.

## Verified reuse loop

For repeated questions or a background worker warming answer memory, read [verified reuse](references/verified-reuse.md). Production `find` currently uses saved reviewed datasets but does not automatically save verified answers. The pointer/cache experiment is explicitly opt-in; confirm that runtime is configured before using it. Keep setup mechanics inside the configured tool and act on its returned status; never imply an unprepared dataset is ready. Agent-assisted recovery is available only when the trusted operator enables it for authorized normal-search recovery; it selects already reviewed registered preparation, then requires explicit review and approval. Treat returned `hints` as optional recommendations, never authority or a required user prompt; `nextAction` remains the control. It does not provide production authentication, raw-file ingestion, automatic approval/training, or an autonomous scheduler.

## Record misses

When an expected skill/source is missed, a wrong result is accepted, evidence is incomplete, or coverage/preparation prevents the requested lookup, record the outcome in the existing non-riding feedback card using the supported card tool. When this installation has `LOCAL-FEEDBACK.md`, follow it for the exact card and evidence location. On other installations, keep a durable private feedback artifact in the project and report its location; a fleet card system is not required. Keep a brief, non-sensitive dated summary on the card: tool/request intent, actual outcome, expected result if known, and the local evidence pointer. Store private request text and passages only in the local evidence artifact. Distinguish ranking misses, coverage gaps, service failures and partial support; an expected absent-source rejection is not a failure.

Record the initial result before retries or ordinary-search rescue, and append any later resolution without erasing the miss. If the card is unavailable or not writable, preserve the local report and state that card recording is pending; never claim it was saved or create a duplicate card. Do not treat logging as permission to expose private data, change a model gate, or run an unauthorized action.

Existing advanced CLI commands, hooks, thresholds and verdicts remain available through `superjev.py` (or the installed `superjev` shim). Their reference is [checking tools](references/checking.md); do not change hook wiring merely to use this front door.
