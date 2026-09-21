# Known quirks

Each quirk: what you see, why it happens (if known), and the workaround.

## 1. "Preparation required" is setup, not a no-match

- **Symptom:** a lookup returns "preparation required".
- **Cause:** the connector for that source has not been onboarded yet — a setup state, not a failed search.
- **Workaround:** run the connect flow for the source, then ask again.

## 2. Cache hits are exact wording only

- **Symptom:** you approved an answer, but asking again in different words misses.
- **Cause:** the cache matches exact wording; paraphrases go through the search lane.
- **Workaround:** approve the new wording once with `--approve`, or let it search.

## 3. The check gate needs verbatim quotes

- **Symptom:** a claim you know is true fails the check gate.
- **Cause:** the gate matches verbatim quotes from the source, not paraphrases.
- **Workaround:** quote the source text exactly.

## 4. A second checkout sees 0 pointers

- **Symptom:** a fresh worktree or second checkout shows no pointers.
- **Cause:** registry resolution does not follow the second checkout; root cause still under investigation. The `memory.sh` wrapper is also untracked — it is generated locally at install time, so a fresh checkout may not have one at all.
- **Workaround:** work from the original checkout, or re-onboard the folder there. For `dispatch.py memory`, pass `--config /path/to/your-config.json` explicitly so it does not depend on the untracked wrapper.

## 5. Hidden form inputs fool naive "already filled" checks

- **Symptom:** a form reports as already filled when it is not.
- **Cause:** hidden inputs carry values that a naive check reads as filled.
- **Workaround:** check visible inputs only, or clear and re-enter.

## 6. Held files on the literal string TYPESAFE_API_KEY

- **Symptom:** bulk prepare holds a file that contains no real secret.
- **Cause:** the literal placeholder string trips the secret scan — a false positive.
- **Workaround:** review the file, then admit it with `prepare_bulk.py --allow-held`.

## 7. hooks.md withheld from rc.1

- **Symptom:** references to `docs/hooks.md` (the long hook research notes) now 404.
- **Cause:** hooks.md withheld from rc.1: the long hook research notes carried internal names and are being scrubbed.
- **Workaround:** the hook wiring you need is in `docs/wire-into-claude-code.md`.

## 8. Helper first fire is a dry-fire

- **Symptom:** the first run of a helper appears to do nothing.
- **Cause:** the first fire is a dry-fire by design.
- **Workaround:** run it again; the second fire is live.

## 9. Certificate errors on Macs without a system CA bundle

- **Symptom:** Python or Node doors fail with TLS/certificate errors while the rest of the machine works fine.
- **Cause:** the process cannot find a CA bundle. `search.sh` auto-sets `NODE_EXTRA_CA_CERTS` from certifi when it can, but bare Python and Node invocations do not.
- **Workaround:** export `SSL_CERT_FILE=$(python3 -m certifi)` for Python doors and `NODE_EXTRA_CA_CERTS=$(python3 -m certifi)` for Node doors before running commands.

## 10. Large files

- **Symptom:** `skills/super-jev/superjev.py` is one 13,355-line file, far over the size ceiling.
- **Cause:** the superjev skill is still a monolith; the split into one module per door is the first 1.1 job.
- **Workaround:** none needed — behavior is unaffected; the file is exempt in gate.json allow_paths.

## 11. Env var names from the author's harness
**Symptom:** `superjev.py` reads `CLAW4MAC_SESSION_ID`, `CLAW4MAC_BOT_ID` and `CLAUDE_BOT_ID` to derive a principal name.
**Cause:** compatibility with the harness Super Jev was built in. They are optional; unset, the principal comes from `--principal`.
**Workaround:** pass `--principal` explicitly. 1.1 renames them to `SUPERJEV_*` with a fallback.
