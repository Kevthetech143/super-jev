---
name: super-jev
description: "Find skills and reviewed brain/doc evidence through Super Jev source connectors. Check claims, verify agent work, and reuse explicitly approved answers with bounded reviewed-source recovery. Evidence still needs caller review."
---

# Super Jev

One front door: `python3 <this-skill-directory>/dispatch.py <tool> ...`. Use the user's original request and relevant context; keep backend names and setup mechanics out of ordinary replies.

**First use:** confirm the selected tool runs and the intended person's/project's records are prepared and connected. If setup is missing, follow the returned hint and [connector setup](references/connectors.md); never treat a setup failure as “nothing found.” Existing working connections skip this step.

From this skill directory (replace dataset/pointer and principal with your authorized configured values):

```sh
python3 dispatch.py help --topic overview
python3 dispatch.py find --list-datasets
python3 dispatch.py find --dataset DATASET_ID --request "Your original question"
python3 dispatch.py memory --describe
```

For configured memory, `python3 dispatch.py memory --principal YOUR_AGENT_NAME` lists your pointers. Save a search request in your repo's private working area, then run `python3 /path/to/skill/dispatch.py memory --input /path/to/request.json`:

```json
{"action":"search","pointer":"POINTER_ID","principal":"YOUR_AGENT_NAME","question":"Your original question"}
```

Installed `memory.sh` supplies the runtime/config automatically when no explicit repo/config is supplied. Without it, set `SUPERJEV_REPO` to the checkout and pass `memory --config /path/to/config.json ...`; consult `LOCAL-MEMORY.md` if present. `--describe` reports capabilities, not whether records are onboarded. On `ready`, inspect evidence before answering or approving reuse; `verified-cache-hit` returns an already approved scoped answer. Missing preparation requires setup/refresh, not repeated searching.

**Already connected?** Use the known roots, dataset or pointer directly. Do not reload manuals, reopen the panel, enumerate sources or repeat onboarding on every request. Revisit setup for new/changed scope, missing configuration or a reported preparation/freshness problem. Runtime integrity checks stay enabled.

**New agent or need help?** Use `help --topic overview` for Quick Start, `help --question "your how-to question"` for suggested help topics, or `help --topic TOPIC` for an exact topic. Built-in answers need no data, key or Jev call; they cover maintained setup topics, not arbitrary facts. If instructions are already known, skip help. Read longer guides only when needed.

| Tool | Purpose |
|---|---|
| `help` | Quick Start and focused setup answers |
| `skills` | Skills connector: discover candidate skills; suggestions never execute them |
| `find` | Brain/Documents/Repo connectors: retrieve from reviewed local datasets |
| `memory` | Use registered pointers, reviewed recovery and explicitly approved exact-answer reuse |
| `check` | Check a claim or draft against evidence |
| `verify` | Verify an agent report against evidence |

`tools` lists executable tools and separate agent-guided setup workflows. `memory --describe` exposes settings, actions and connector limits without setup. Existing configured pointers use `memory`; `find` alone does not save approved answers. Follow `LOCAL-MEMORY.md` and its `memory.sh` when installed; otherwise configure the documented experiment. The legacy `superjev.py ask` router is not this front door.

Load only the selected workflow's guidance if it is not already known: sibling `skill-search/SKILL.md` for discovery; sibling `fleet-retrieval-experiment/SKILL.md` for reviewed retrieval/preparation; [verified reuse](references/verified-reuse.md) for memory; [checking](references/checking.md) for check/verify. Skill roots follow the caller; check that a suggested skill's tools are available before using it. Existing advanced commands and hooks remain unchanged.

Quick boundaries: `ready` is evidence, not an answer guarantee. Review full support and original provenance before approval; exact verified hits are reusable only within their returned scope. Preserve actual errors, uncertainty and missing preparation. Do not hide ordinary-search, alternate-model or different-dataset recovery; authorized assistance follows the bounded memory guide. No new access, privacy approval, execution permission, automatic syncing or autonomous scheduler is granted here.

For connecting existing data, starting blank or defining a user-named connector, ask help first if needed, then use [connector setup](references/connectors.md) for the procedure. Existing originals can keep their structure; the starter layout is optional. Connector labels describe workflows, not extra executable commands or proof of readiness. Hints are optional advice, never mandatory user prompts. For current-state questions or freshness problems, explain the returned freshness limit briefly; do not repeat it on every ordinary hit. Connected means registered, not automatically synced. Never invent a checked date or treat cache approval time as source freshness.

On misses, incomplete evidence or review failures, preserve the initial result and follow [feedback recording](references/feedback.md). Use the existing non-riding feedback card when available; detailed/private evidence stays local. Report assistance separately from an automatic hit. Normally tell the human the answer or concrete next step, not the internal ceremony.
