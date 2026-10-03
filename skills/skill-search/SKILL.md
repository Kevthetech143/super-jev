---
name: skill-search
description: Advisory skill discovery. Takes the user's original request wording plus needed recent context, searches the trusted skill roots, and returns up to three candidate skills as short JSON pointers. Never runs a skill, never grants execution permission.
---

# skill-search

What it does:
Searches the trusted skill roots for the user's request and returns a short list of candidate skills as one compact JSON object. Advisory only. It never runs a skill, never loads a full catalog, and never grants execution permission.

When to use it:
- No skill is named in the request and you need to know whether one already does the job.
- Any agent that needs a skill pointer without reading the whole catalog.

Where it searches (one rule: your own skill folders):
- Inside Claude Code it searches `~/.claude/skills`. `roots.json` is ignored there; to search other folders pass `--config FILE` (a JSON array of folders).
- Any other agent uses `roots.json` next to `search.sh`: copy `roots.example.json` to `roots.json` (gitignored, yours alone) and list your skill folders, absolute or starting with `~/`.
- Extra folders of your own (a project's skills, for example): list them in `~/.local/state/super-jev/skill-roots.json` (a JSON array of absolute or `~/` folders; override the path with `SKILL_SEARCH_EXTRA_ROOTS`). They are added after the defaults, outside any release, so an upgrade keeps them. A folder that does not exist is skipped quietly (`SKILL_SEARCH_DEBUG=1` names it on stderr); a file that is not a JSON array is ignored with one stderr line. Extras need the base roots file (`roots-claude.json` in Claude Code, else `roots.json`) to exist and be a JSON list; otherwise they are not merged and the launcher reports the base file as usual. A folder that cannot be listed is skipped like a missing one. Not used with `--config` or `--roots-file`.
- With neither, the launcher prints one setup line (`status: error`, exit 2) and nothing runs.

How to call it:
1. Write REQUEST.json with the user's original wording and only the recent turns the search needs:
   {"request": "<the user's original wording>", "context": ["<needed recent turn>", "..."]}
   Keep the original wording. Do not paraphrase it away. Write the file inside the caller repo's working directory where feasible (or another caller-owned temp dir) — never in a shared location.
2. Run the launcher:
   bash ~/.claude/skills/skill-search/search.sh --request-file REQUEST.json [--local-only]
   The launcher expands the roots config, forwards everything to the shared runtime, and prints one JSON object on stdout.
3. Read the result. status is one of: exact, suggestions, no_match, fallback, clarify, error. `fallback` and any `source: local` suggestions are unverified local guesses, never a match; `error` (exit 2) carries the real cause in `error`, with no candidates. candidates holds up to three entries, each {id, name, path, description} with a short description only.

Rules:
- A named skill wins. If the request names a skill directly (/name, or an unambiguous exact name), load it. Do not search.
- The result is advisory. You still decide USE, CHAIN, or BUILD. A suggestion is not permission to run.
- You load the chosen skill file. Read the candidate's path and load it with the Skill tool or a file read. The search never runs it for you.
- If the chosen candidate's file is missing when you go to load it, list your skill folders yourself — the same fallback as a fallback result.
- A search is only complete over validated roots. ANY invalid root entry (not a string, empty, or not absolute after ~ expansion) makes the launcher fail explicitly with an error status — it never warns-and-continues, never claims complete over a filtered set. Unreadable-but-valid absolute roots are still forwarded so the runtime can report the search incomplete.
- On clarify: this is an advisory boundary, not a forced question. Ask the user to disambiguate and re-invoke only when intent is actually unclear. If one returned candidate clearly fits the request (read the short descriptors first), the caller may choose it. Do not force a question for near-equivalent duplicates (for example web versus playwright results) — pick or ask based on actual ambiguity. Clarify is never no_match and never a build signal. no_match means no skill fit; it never authorizes building and never grants execution permission.
- On fallback, the candidates are unverified guesses: read each candidate's SKILL.md yourself, or list your skill folders. On error, read the `error` text and fix that cause (for example, shorten an over-long request, or set up `roots.json`); listing the folders is not the answer to an input error. Never read a service failure as no_match.
- Pronoun-only requests with no usable referent get a clarifying question. Do not invent the task.
- Same technique, different purpose (for example rent versus generic browser work) stays advisory. Do not auto-run.
- Node missing, or the runtime entry not installed, is a clear error status, not a silent pass and not a no_match.

## Model calls and credentials

The judge is chosen by `SUPERJEV_JUDGE` (default: Jev); its key variable name comes from `skills/super-jev/judge_profiles.json` (for Jev, `TYPESAFE_API_KEY`).


- The shared runtime makes the model call only in live mode. It reads the API key from the environment only (never argv, never logs). The launcher inherits its environment into the runtime child, so a key exported in the shell reaches it with no key in the repo.
- A hook that cannot inherit your shell supplies the key through a provider command (`deploy/hook-wrapper.sh`, contract in `deploy/ENV-CONTRACT.md`): copy `deploy/key-provider.example.py` to `deploy/local-key-provider.py` (gitignored). Without a key and without a provider, a live search stops with one line that names `TYPESAFE_API_KEY`; `--local-only` still works. The skill ships secret-free, and a fallback is never claimed as a live search.
- --local-only never needs a provider or a key; the launcher never invokes any provider command itself.
- No CA environment is required by this wiring. If your provider endpoint needs a private CA, add your usual CA variables inside your provider wrapper only.

Skill roots follow the agent that asks and are chosen in one place, `search.sh` (see "Where it searches" above). The launcher runs the runtime from the release it ships in, never a separate checkout. A file being readable does not guarantee its tools exist for you: check the selected skill before use.
