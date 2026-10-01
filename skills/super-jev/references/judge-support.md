# Judge support: which judge on which build

A judge (a profile in `judge_profiles.json`, or the test judge `fake`) is "supported" on a build only when a measured run on that same build says so. This page covers the first piece: the fingerprint that ties a result to a build and a judge. Grading a judge against your own bar comes in a later change; this one only names what a result belongs to.

## The fingerprint

```sh
python3 judge_support.py --fingerprint --ask BUILD/ask.py --judge NAME [--env NAME=VALUE ...]
```

Free: no ask, no judge call, no key. It prints one JSON line: the five parts below and `fingerprint`, a SHA-256 over them. Exit 0 prints it; exit 2 is bad input (an unknown judge, a malformed or repeated `--env`, a build whose `judge_profiles.json` cannot be read).

| part | what it is |
|---|---|
| `judge` | the profile key after aliases (`typesafe` and `typesafe-jev` give the same part) |
| `implementation` | `profile`, or `fake` for the test judge. `fake` uses the default profile's numbers, so this part keeps a fake result from ever standing in for the default judge |
| `profile_sha256` | that profile's entry in the build's own `judge_profiles.json`, as canonical JSON, without keys that start with `_` (comments and sources) |
| `judge_path_sha256` | the build's code files (`.py`, `.ts`, `.mjs`, `.js`, `.sh`) under `skills/super-jev/` (not its `tests/`), `src/` and `experiments/verified-pointer-memory/`, plus `secret_patterns.json`, which decides what may reach the judge |
| `env_sha256` | the sorted `--env NAME=VALUE` pairs. Values can hold a token, so they are hashed and never printed |

It changes exactly when the judge, its profile, an explicit setting or the engine code that talks to the judge changes. It does not change when you edit another profile, a `_source` or `_comment`, a test, a doc, the changelog or a prepared set (`prepare-cache`), or when the same build is copied to another folder.

The judge path is broad on purpose: the questions put to the judge are worded in `ask.py`, `superjev.py` and `src/`, and thresholds sit outside the profile table. A change to any of them is a new build as far as a result is concerned. A comment-only edit to one of those files counts too; that costs one re-run, never a wrong "supported".

## What the fingerprint cannot see

- A hosted model alias can change behind the same profile (the provider swaps the model under the same name). The fingerprint cannot see that; re-run on request when you learn of it.
- A setting you did not pass with `--env` is not part of it. Settings a run needs must be passed explicitly.
- Prepared sets (`prepare-cache`) are not part of it: they are the data a run reads, not the judge.
