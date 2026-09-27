# Reviewed view recipes

Use this when a connector may process a redacted view of an original file, but not its raw text. A policy is explicit local data, never an executable command or caller-supplied regular expression. It does not discover sensitive information or grant privacy approval: review the policy and its output within the source's authorized scope.

Add `viewTransform` to each affected source in the usual connect request:

```json
{
  "path": "/authorized/notes.md",
  "description": "Reviewed project notes",
  "viewTransform": {
    "version": 1,
    "operations": [
      {"op": "drop-lines-containing", "values": ["PRIVATE:"]},
      {"op": "redact-literals", "values": ["Example Person"], "replacement": "[PERSON_REDACTED]"},
      {"op": "redact-local-paths"},
      {"op": "redact-emails"}
    ]
  }
}
```

Operations run in order. Literal matching is case-sensitive and does not normalize Unicode: list NFC/NFD or nonbreaking-space variants explicitly when needed; longer values run first. Line dropping uses LF only, matching the chunker; Unicode separators remain within that line. The built-in path operation replaces whitespace-delimited paths beginning `/Users/`, `/home/`, or `~/`; it is not a general detector for every operating system's path syntax. The email operation handles ordinary email shapes. Literal replacement markers are limited to `[REDACTED]`, `[PERSON_REDACTED]`, `[LOCAL_PATH_REDACTED]`, and `[EMAIL_REDACTED]`. Unknown fields, versions and operations are refused. Policies allow up to 32 operations, 256 values per literal operation, and 64 KiB of canonical JSON. Originals and derived views each have a 5 MiB total bound per connect.

Save the policy object alone as `policy.json`, then derive a private local copy for review:

```sh
python3 reviewed_view.py --source /authorized/notes.md --policy policy.json --output /private/reviewed-notes.txt
```

The output path must be new. Read that copy before authorizing it. This command is local and does not connect anything. `connect_checked.py CONNECT.json` gates each description against that same derived text in a private temporary file, never against the original. Its verdict file records the original, view and policy hashes; a change between the gate and connect preview refuses publication. Existing description-gate costs apply, one invocation per source.

For the low-level memory `connect` action, preview returns `sha256` (original), `viewSHA` (derived bytes), and `transformSHA` (canonical policy). Keep the original request's `viewTransform` and merge all three hashes into its source before confirming with `reviewed:true` and the returned `navigationSHA`. Preview intentionally does not echo literal redaction values. Hashes without their policy are refused.

The registry pins original bytes for freshness and records the policy in its refresh recipe. The immutable manifest stores only derived document bytes, their hashes and line offsets, plus local provenance/policy metadata. Source and navigation responses use their readable `originalPath` field for the **prepared view**; `upstreamPath` names the original for provenance only. Do not read `upstreamPath` into a provider call. This keeps existing ask/content-check readers on the view. The policy's literal values remain private local configuration.

A recipe refresh uses the same paths, policy and principals, derives new views, and retains the existing secret scan. It is not a new review of every future edit: a policy only hides the patterns it declares. An existing transformed source cannot be dropped, renamed to another path, or switched to a different policy through replacement; use a separately authorized connector for a policy/scope change. A hand-prepared legacy dataset requires an explicit transform on every replacement source. Before migrating it, reproduce and compare its old reviewed output, preserve its access scope, and review any newly included text. There is no automatic inference of historical redactions and no raw fallback.

Routing metadata must already be safe under the policy: ids, descriptions and explicit navigation labels that would be transformed are refused. Transformed files use opaque catalog labels instead of original filenames or implicit folder names. A surviving bulk cache/report blocks conversion; quarantine those artifacts before choosing this workflow. Ask also ignores old bulk caches for transformed pointers, only heals through their view recipe, and withholds saved claim verdicts whose proof points to a transformed original. Historical traces/lookups are audit records, not retrieval evidence, and are not automatically erased.

Before conversion, registered manual-answer pointers in the same principal scope are checked on decoded strings for the policy’s literal values (including quotes and backslashes); matches or unreadable records are listed and block conversion until reviewed and disconnected or redacted. On successful view connection, raw-original claim proofs are removed from those principals’ local claim caches. Only the directly replaced connector-owned `.prepared-*` generation is deleted, and only if unreferenced and containing its expected files. Legacy/custom folders, shared generations and cleanup failures are reported in `cleanupWarnings`; older untracked artifacts need an explicit retention review. No broad artifact deletion is performed.
