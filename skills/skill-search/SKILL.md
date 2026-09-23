---
name: skill-search
description: Advisory skill discovery. Takes the user's original request wording plus needed recent context, searches the trusted skill roots, and returns up to three candidate skills as short JSON pointers. Never runs a skill, never grants execution permission.
---

# skill-search

What it does:
Searches the trusted skill roots for the user's request and returns a short list of candidate skills as one compact JSON object. Advisory only. It never runs a skill, never loads a full catalog, and never grants execution permission.

When to use it:
- The /go tier A, tier B, or tier C discovery step, when no skill is named and the skills map card misses.
- The Astra and Fable operations cards, when discovery is needed outside /go. Astra and Fable never route through /go; they call this skill directly, bypassing /go at entry through their model-gated cards.
- Any agent that needs a skill pointer without reading the whole catalog.

How to call it:
1. Write REQUEST.json with the user's original wording and only the recent turns the search needs:
   {"request": "<the user's original wording>", "context": ["<needed recent turn>", "..."]}
   Keep the original wording. Do not paraphrase it away. Write the file inside the caller repo's working directory where feasible (or another caller-owned temp dir) — never in a shared location.
2. Run the launcher:
   bash ~/.claude/skills/skill-search/search.sh --request-file REQUEST.json [--local-only]
   The launcher expands the roots config, forwards everything to the shared runtime, and prints one JSON object on stdout.
3. Read the result. status is one of: exact, suggestions, no_match, fallback, clarify, local-unavailable. candidates holds up to three entries, each {id, name, path, description} with a short description only.

Rules:
- A named skill wins. If the request names a skill directly (/name, or an unambiguous exact name), load it. Do not search.
- The result is advisory. You still decide USE, CHAIN, or BUILD. A suggestion is not permission to run.
- You load the chosen skill file. Read the candidate's path and load it with the Skill tool or a file read. The search never runs it for you.
- If the chosen candidate's file is missing when you go to load it, fall back to the existing catalog (the skills map card, ~/agents/global/skills/INDEX.md) — the same fallback as a fallback/local-unavailable result.
- A search is only complete over validated roots. ANY invalid root entry (not a string, empty, or not absolute after ~ expansion) makes the launcher fail explicitly with a fallback diagnostic — it never warns-and-continues, never claims complete over a filtered set. Unreadable-but-valid absolute roots are still forwarded so the runtime can report the search incomplete.
- On clarify: this is an advisory boundary, not a forced question. Ask the user to disambiguate and re-invoke only when intent is actually unclear. If one returned candidate clearly fits the request (read the short descriptors first), the caller may choose it. Do not force a question for near-equivalent duplicates (for example web versus playwright results) — pick or ask based on actual ambiguity. Clarify is never no_match and never a build signal. no_match means no skill fit; it never authorizes building and never grants execution permission.
- On fallback or local-unavailable, use the existing catalog (the skills map card, ~/agents/global/skills/INDEX.md). Never read a service failure as no_match.
- Pronoun-only requests with no usable referent get a clarifying question. Do not invent the task.
- Same technique, different purpose (for example rent versus generic browser work) stays advisory. Do not auto-run.
- Node missing, or the runtime entry not installed, is a clear local-unavailable diagnostic, not a silent pass and not a no_match.

## Model calls and credentials

- The shared runtime makes the model call only in live mode. It reads the API key from the environment only (never argv, never logs). The launcher inherits its environment into the runtime child, so a deployment-provided key reaches it without any key in the repo.
- The deployment wrapper that supplies the key is a LOCAL deployment artifact only (see deploy/hook-wrapper.sh, reference implementation of deploy/ENV-CONTRACT.md); the skill ships secret-free. Without a key in the environment the runtime reports fallback/local-unavailable honestly — a fallback is never claimed as a live search.
- --local-only never needs a provider or a key; the launcher never invokes any provider command itself.
- No CA environment is required by this wiring. If a deployments provider endpoint needs a private CA, add the sites usual CA variables inside the deployment wrapper only.

Skill roots follow the caller: Claude/Fable/Opus seats append `--config ~/.claude/skills/skill-search/roots-claude.json`; Codex seats use the default roots. A file being readable does not guarantee its tools exist on this seat: check the selected skill before use.
