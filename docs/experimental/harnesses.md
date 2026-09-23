# Where super-jev's doors land on other harnesses

super-jev's three doors are: the **reply gate** (checks a draft reply against
evidence before it goes out), the **worker-report verify** (checks a
sub-agent's finished-work report against evidence the machine collected), and
the **action permit** (would check a tool call before it runs; today this is a
CLI, `npm run permit`, not wired to any hook — see `docs/wishlist.md`). This
page asks one question per harness: does the harness expose an interception
point these three doors could attach to, and if so, which one, blocking or
advisory?

All claims below are dated September 2026 and labeled verified (read from the
harness's own docs) or unverified (inferred from secondary sources, forum
threads, or GitHub issues, and not cross-checked against the harness's own
reference page). Nothing here reports performance numbers.

## Summary table

| Harness | Interception point | Events | Can it block? | Payload | Reply-gate fit | Worker-verify fit | Action-permit fit | Docs |
|---|---|---|---|---|---|---|---|---|
| Claude Code | Native hooks | PreToolUse, PostToolUse, Stop, UserPromptSubmit, Notification, SessionStart/End, + ~30 events | Yes, exit 2 on blockable events (not all events block) | stdin JSON, stdout JSON or stderr text | Native: Stop | Native: PostToolUse on Agent tool | Native: PreToolUse | [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks) |
| OpenAI Codex CLI | Native hooks (experimental, opt-in) | PreToolUse-style matcher hooks in `hooks.json`; separate `notify` for turn-complete | Unverified whether hook denial blocks the tool call the way Claude Code's exit 2 does; docs describe matcher + command handler, not a confirmed exit-code contract | stdin JSON (payload not fully documented in sources found) | Wrapper: no turn-level "final reply" hook found; would need a CLI wrapper around `codex exec` output | Wrapper: pipe `codex exec` transcript through a wrapper script | Native (experimental): hooks.json matcher on tool calls, feature-flagged off by default | [github.com/openai/codex/blob/main/docs/config.md](https://github.com/openai/codex/blob/main/docs/config.md), [developers.openai.com/codex/cli/reference](https://developers.openai.com/codex/cli/reference) |
| Nous Research Hermes Agent | Native hooks (27 lifecycle events) + separate middleware registry | `pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`, `on_session_end`, `pre_approval_request`, etc. | Yes for `pre_tool_call` (fail-closed on timeout); most observer hooks are read-only/advisory; middleware can rewrite requests | In-process Python callback (not stdin/JSON — plugin API, not a subprocess contract) | Native: `on_session_end` or `post_llm_call` as a Python plugin | Native: `post_tool_call` as a Python plugin | Native: `pre_tool_call` (blocking, fail-closed) | [hermes-agent.nousresearch.com/docs/user-guide/features/hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks), [.../developer-guide/middleware](https://hermes-agent.nousresearch.com/docs/developer-guide/middleware) |
| OpenClaw | Plugin/agent-harness SDK (built on the Pi harness) | Documented as "agent harness plugins" in `docs.openclaw.ai/plugins/sdk-agent-harness`; exact event list not confirmed in sources found | Unverified | Unverified | Unverified — likely plugin-level, not a subprocess hook | Unverified | Unverified | [docs.openclaw.ai/plugins/sdk-agent-harness](https://docs.openclaw.ai/plugins/sdk-agent-harness) |
| Cursor CLI / agent | Native hooks (`hooks.json`, since v1.7) | beforeShellExecution, afterShellExecution, beforeMCPExecution, beforeReadFile, afterFileEdit, stop | Yes for the "before*" events; CLI reportedly emits only beforeShellExecution/afterShellExecution, dropping other events (forum-reported bug, unverified against current release) | stdin JSON, stdout controls proceed/deny | Wrapper: `stop` hook is closest, but no confirmed full-reply payload | Wrapper: no equivalent to Claude's Agent-subagent report; would need external tracking | Native: beforeShellExecution / beforeMCPExecution | [cursor.com/docs/hooks](https://cursor.com/docs/hooks) |
| Gemini CLI | Native hooks (`settings.json`, default-on since v0.26.0) | BeforeAgent, AfterAgent, BeforeTool, AfterTool, BeforeModel, AfterModel, BeforeToolSelection, Notification, SessionStart/End, PreCompress | Yes; hooks run synchronously and can deny via JSON output (`allow`/`deny`-style decision, same shape as Claude Code) | stdin JSON, stdout must be pure JSON | Native: AfterAgent (closest to a finished-reply gate) | Native: AfterTool | Native: BeforeTool | [geminicli.com/docs/hooks/reference](https://geminicli.com/docs/hooks/reference/) |
| Aider | None native; git hooks only | N/A (feature request #5712 open, unimplemented as of the sources found) | Only via standard git `pre-commit` hook, and only if `--git-commit-verify` is passed (Aider skips git hooks by default) | N/A | Wrapper: none built-in; would need to wrap the `aider` CLI process and read its output/diff | Wrapper: same — wrap the process, diff the working tree | Manual: git pre-commit hook, commit-scoped only, not tool-call scoped | [github.com/Aider-AI/aider/issues/5712](https://github.com/Aider-AI/aider/issues/5712), [aider.chat/docs/git.html](https://aider.chat/docs/git.html) |
| OpenHands (SDK) | Native hooks (shell scripts under `scripts/`) | PreToolUse, PostToolUse, UserPromptSubmit, Stop | Yes; matches the Claude Code exit-code contract (0 allow, 2 block, other non-zero = non-blocking error) | stdin JSON, stdout JSON (decision/reason/additionalContext/continue) | Native: Stop | Native: PostToolUse | Native: PreToolUse | [docs.openhands.dev/sdk/guides/hooks](https://docs.openhands.dev/sdk/guides/hooks) |
| Cline | Native hooks (since v3.36, macOS/Linux only) | TaskStart, PreToolUse, TaskResume, TaskCancel (naming per Cline's own hook-type docs) | Yes; `cancel` field in the hook's JSON response blocks the operation | stdin JSON, stdout JSON (`cancel`, `contextModification`) | Wrapper: no documented "final reply" hook found; TaskCancel/TaskResume are lifecycle, not reply-scoped | Native: PreToolUse can inspect prior tool results, but there is no dedicated post-report hook documented | Native: PreToolUse | [cline.bot/blog/cline-v3-36-hooks](https://cline.bot/blog/cline-v3-36-hooks) |
| Roo Code | `.clinerules`-style rules only (forked from Cline) | Unverified — sources found describe Roo Code's philosophy (autonomy over institutional control) but not a confirmed native hook system distinct from Cline's | Unverified | Unverified | Unverified | Unverified | Unverified | No primary doc found in this pass; treat as "not found" per the worker card, not as "no hooks" |
| Goose (Block / AAIF) | MCP extension system, not a hook system | Extension enable/disable at config or runtime (`extensionmanager__manage_extensions`); no lifecycle hook API found | No native block/allow hook found; enforcement today is "install only trusted MCPs," an allow-list policy, not an interception point | N/A (MCP tool calls, not a hook payload) | Wrapper: none native; would need to wrap the Goose process or write a policy-enforcing MCP proxy in front of Goose's tool calls | Wrapper: same | Wrapper: an MCP-proxy extension could deny tool calls, but this is a build-it-yourself pattern, not a shipped hook | [github.com/block/goose/blob/main/AGENTS.md](https://github.com/block/goose/blob/main/AGENTS.md), [block.github.io/goose/blog/2025/03/31/securing-mcp](https://block.github.io/goose/blog/2025/03/31/securing-mcp/) |
| Sourcegraph Amp | Plugin API (lifecycle event handlers) | "lifecycle event handlers" named in Amp's Plugin API description; exact event names not confirmed in sources found | Unverified whether plugin handlers can deny an action or only observe/classify | Unverified | Unverified — plugin-level, in-process, not a subprocess/stdin contract as far as found | Unverified | Unverified — Plugin API replaced "the earlier static permission system with composable, code-driven rules," implying some blocking capability, but not confirmed | No primary Amp docs page found in this pass (ampcode.com not fetched); summary drawn from secondary coverage, treat as unverified |

## Per-harness notes

### Claude Code — verified
Hooks live in `settings.json` at three merging scopes (user, project,
project-local). Exit code 2 is the universal block signal on events that
support blocking; not every event can block (e.g. `PermissionRequest` uses a
decision object instead, and several notification-style events only surface
stderr to the user, never to the model). JSON output on stdout
(`hookSpecificOutput.permissionDecision`) is the structured alternative to a
bare exit code. This is the harness super-jev is built for; see
`docs/wire-into-claude-code.md` for the exact wiring.
Source: [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks) (verified).

### OpenAI Codex CLI — mostly unverified
Codex's MCP configuration (`~/.codex/config.toml`, `[mcp_servers.<name>]`) is
solid and verified. Its hook system is separate, experimental, and opt-in:
disabled by default, first shipped v0.114, enabled via
`[features] codex_hooks = true`, and not available on Windows. Docs found
describe a matcher + command-handler shape similar to Claude Code's, but none
of the sources in this pass confirmed the exit-code semantics (whether a
non-zero exit actually blocks the tool call) or the full stdin payload shape.
Treat the block/advisory question as **not found** rather than answered.
`notify` is a separate, non-blocking, machine-local mechanism for firing a
command on `agent-turn-complete` — advisory only, not an interception point.
Source: [github.com/openai/codex/blob/main/docs/config.md](https://github.com/openai/codex/blob/main/docs/config.md) (verified for MCP/notify; hook exit-code semantics unverified).

### Nous Research Hermes Agent — verified
Hermes has the richest documented lifecycle-hook surface of anything in this
list: 27 named events (`hermes_cli.plugins.VALID_HOOKS`), a documented
timeout contract (default 30s, max 600s), and an explicit fail-open/fail-closed
split — `pre_tool_call` fails closed (blocks) on timeout or crash, while other
bounded hooks fail open. Hooks are Python plugin callbacks, not subprocess
commands with a JSON stdin/stdout contract, so wiring super-jev in would mean
writing a Hermes plugin that shells out to `superjev.py hook`, not a
`settings.json` snippet. Middleware is a separate, more powerful surface that
can rewrite requests before hooks or guardrails see them.
Source: [hermes-agent.nousresearch.com/docs/user-guide/features/hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks) (verified).

### OpenClaw — identified, hook details unverified
OpenClaw is a real, distinct, fast-growing open-source project (Peter
Steinberger, first released late 2025, renamed twice before settling on
"OpenClaw" in January 2026; now stewarded by the OpenClaw Foundation). It is
**not** an OpenAI project despite Steinberger later joining OpenAI. It is
built on top of **Pi**, a separate minimal terminal-coding harness, and adds
a multi-channel messaging gateway (WhatsApp, Discord, Slack, etc.) plus
voice. A docs page exists at `docs.openclaw.ai/plugins/sdk-agent-harness`
describing "agent harness plugins," but this pass did not fetch that page
directly, so the exact event list, block/advisory semantics, and payload
shape are **not found** — flagged for a follow-up fetch rather than guessed.
Source: [en.wikipedia.org/wiki/OpenClaw](https://en.wikipedia.org/wiki/OpenClaw) (verified for identity/history); [docs.openclaw.ai/plugins/sdk-agent-harness](https://docs.openclaw.ai/plugins/sdk-agent-harness) (not fetched, hook mechanics unverified).

### Cursor CLI / agent — verified with a known gap
Cursor's hooks (`hooks.json`, introduced v1.7) cover
`beforeShellExecution`/`afterShellExecution`,
`beforeMCPExecution`/`afterMCPExecution`, `beforeReadFile`, `afterFileEdit`,
and `stop`. There is no `beforeFileEdit` — `afterFileEdit` fires only once the
write has already happened, so it cannot deny an edit, only audit or react to
one. A Cursor forum thread (unverified against a specific dated release)
reports the CLI variant only emits `beforeShellExecution`/
`afterShellExecution` and drops other configured events that the IDE variant
sends — worth checking against your installed version before relying on any
event besides the shell ones.
Source: [cursor.com/docs/hooks](https://cursor.com/docs/hooks) (verified); CLI event-drop report (unverified, forum-sourced).

### Gemini CLI — verified
Hooks are enabled by default since v0.26.0, configured in `settings.json` at
project/user/system scopes plus extension manifests, and documented with a
full event list (`BeforeTool`, `AfterTool`, `BeforeAgent`, `AfterAgent`,
`BeforeModel`, `AfterModel`, `BeforeToolSelection`, `Notification`,
`SessionStart`/`SessionEnd`, `PreCompress`). The JSON contract is close to
Claude Code's: stdin JSON in, stdout must be pure JSON out (any stray text
breaks parsing and defaults to Allow), stderr for logs. `CLAUDE_PROJECT_DIR`
is provided as a compatibility alias, which suggests Google built this
hook system with Claude Code's contract explicitly in mind — the easiest
port target in this whole list.
Source: [geminicli.com/docs/hooks/reference](https://geminicli.com/docs/hooks/reference/) (verified).

### Aider — no native hook system
Aider has no lifecycle hook API. The closest thing is standard git
`pre-commit`, which Aider skips by default (`--no-verify`) unless you pass
`--git-commit-verify`. A general hook system (`post_edit`, `pre_commit`, with
a `blocking: true` option) is an **open, unimplemented feature request**
(issue #5712) as of the sources found in this pass. Any super-jev
integration today would have to wrap the `aider` process externally (watch
its output stream, diff the working tree after each turn) rather than hook
into it.
Source: [github.com/Aider-AI/aider/issues/5712](https://github.com/Aider-AI/aider/issues/5712) (verified — confirms the feature is requested, not shipped).

### OpenHands SDK — verified, closest analog to Claude Code
OpenHands' SDK hooks (`PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`)
explicitly match the Claude Code contract: exit 0 allows, exit 2 blocks
(rejects the action on PreToolUse/UserPromptSubmit, keeps the conversation
open on Stop), any other non-zero exit is a non-blocking error that's
logged but does not stop the operation. Hooks can also be agent-delegated
(`type="agent"`) instead of shell scripts. This is the most direct structural
match for porting super-jev's three doors as-is.
Source: [docs.openhands.dev/sdk/guides/hooks](https://docs.openhands.dev/sdk/guides/hooks) (verified).

### Cline — verified
Hooks (since v3.36, macOS/Linux only) live in
`~/Documents/Cline/Rules/Hooks/` (global) or `.clinerules/hooks/` (project),
named for their event with no file extension, made executable. JSON in via
stdin, JSON out via stdout with a `cancel` field (block) and
`contextModification` field (inject context). Documented event types found:
`PreToolUse`, `TaskStart`, `TaskResume`, `TaskCancel`. No documented
reply-final-message hook was found in this pass, so the reply-gate mapping is
a wrapper, not native.
Source: [cline.bot/blog/cline-v3-36-hooks](https://cline.bot/blog/cline-v3-36-hooks) (verified for the events found; full event list not confirmed complete).

### Roo Code — not found
Sources in this pass describe Roo Code's general philosophy (favors engineer
autonomy over Cline's compliance/audit focus) but none confirmed a native
hook system distinct from `.clinerules`-style rule files, which the docs
themselves note are context, not an enforcement gate. Marked **not found**,
not "no hooks" — a targeted look at Roo Code's own repo/docs is needed before
concluding either way.

### Goose (Block, now AAIF/Linux Foundation) — verified as MCP client, no hook API found
Goose is an MCP client and extension host (built-in extensions in the
`goose-mcp` crate, external extensions as separate MCP server processes), not
a hook/middleware framework. Its own security guidance treats "only install
trusted MCPs" and allow-listing as the mitigation, which implies there is no
native pre-tool-call interception point today — enforcement would mean
writing an MCP proxy in front of Goose rather than registering a hook inside
it. Ownership moved from Block to the Agentic AI Foundation (Linux Foundation)
in November 2025; `block/goose` now redirects to `aaif-goose/goose`.
Source: [github.com/block/goose/blob/main/AGENTS.md](https://github.com/block/goose/blob/main/AGENTS.md), [block.github.io/goose/blog/2025/03/31/securing-mcp](https://block.github.io/goose/blog/2025/03/31/securing-mcp/) (verified for architecture and security guidance; absence of a hook API is an absence-of-evidence finding, not a docs page that says "no hooks exist").

### Sourcegraph Amp — unverified
Secondary sources describe an "Amp Plugin API" with "lifecycle event
handlers" that replaced an "earlier static permission system with
composable, code-driven rules" — language that implies some blocking
capability exists, but this pass did not reach Amp's own docs site
(ampcode.com) to confirm event names, payload shape, or exit/return-value
semantics. Marked unverified; needs a direct fetch of ampcode.com's docs
before super-jev wiring guidance can be written for it.
