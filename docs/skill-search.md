# Skill search (default skill discovery)

`src/skill-search-cli.ts` — the default skill-discovery entry. Given one or more
skill-catalog roots and a request, it finds the matching skill without
executing anything.

```bash
npm run skill-search -- --roots-file ROOTS.json --request-file REQUEST.json [--local-only]
```

`ROOTS.json` is an array of absolute skill-root paths. `REQUEST.json` is
`{"request": "...", "context": ["..."]}` — context is recent turns, oldest
first, capped at the trailing `DEFAULT_CONTEXT_TURNS`.

## What it prints

Exactly one JSON object on stdout:

```json
{
  "status": "suggestions",
  "candidates": [{ "id": "pdf-editor", "name": "pdf-editor", "path": ".../pdf-editor/SKILL.md", "description": "Edit and merge PDF documents." }],
  "source": "jev",
  "metadataSkipped": 0,
  "duplicateCount": 1,
  "model": "jev-1",
  "usage": { "calls": 1, "inputTokens": 0, "outputTokens": 0, "retries": 0, "estimated": true }
}
```

`status` is one of `exact | suggestions | no_match | fallback | clarify`.
Candidates are at most three, with short descriptions only — no skill bodies,
no catalog dumps, no raw provider payloads, no secrets. Diagnostics
(skills found, skipped, duplicates) go to stderr; the two counts that matter
to a caller, `metadataSkipped` and `duplicateCount`, are also on stdout.

## Advisory only — never execution permission

The result is a recommendation, not an instruction to run anything. The CLI
reads skill files locally only to extract frontmatter metadata — bodies are
never sent to the judge and never appear on stdout. It never executes a skill
and creates no files in the roots it scans. `exact` means the name matched,
not that the skill is safe or appropriate; the caller decides what to do with
a candidate.

## How it decides

1. **Exact name** (`/skill-name` or a bare name): resolves locally, zero
   network. Exact means an actual exact normalized name match — a near-miss
   never resolves as exact; it flows through the judge path as suggestions.
2. **Underspecified** (pronoun-only or empty request with no usable context):
   `clarify`, never a guess.
3. **Judge**: the narrowed candidates go to the shared `runFetch` path — the
   same question builder, batching, and confidence gate (`applyNoneGate`)
   ordinary fetch uses. Recent context reaches the judge as literal data
   inside `relevanceQuestion`, framed exactly like the request itself: never
   as instructions to follow. One attempt per batch, bounded timeout.
4. **Fallback**: missing `TYPESAFE_API_KEY`, timeout, transport error, or an
   unusable judge response returns `fallback` with local-only candidates —
   never `no_match`. Measured call costs are still reported on `usage`.

Scanning covers only the root itself and its first-level directories, for
`SKILL.md` or `skill.md`. Skills with an empty or malformed description are
excluded and counted in `metadataSkipped`; the first root wins on duplicate
names (`duplicateCount` records the rest). An unreadable root fails closed
before any search is claimed.
