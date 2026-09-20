# Neighbor fallback probe and prepared-artifact cache experiment

Status: bounded development probes on the existing 20-source collection; not deployed, no defaults changed. The neighbor probe reuses earlier questions; the cache probe uses local copies of real source records. Neither is fresh-question validation.

## Neighbor probe: post-hoc, reused questions

Design: eight earlier questions reused unchanged — four source-present, four absent. Two arms: base retrieval versus base plus immediate same-document neighbor sections. 2 repeats, 32 actual HTTP calls, all complete. Reuse makes this a post-hoc analysis; it cannot serve as fresh validation of neighbor behavior.

### Results

| Measure | Base | Base + neighbors |
|---|---|---:|
| Positive cases accepted per repeat | 3/4 | 3/4 |
| Absent cases refused per repeat | 4/4 | 4/4 |
| Wrong source accepted | 0 | 0 |

- Both arms ranked the correct source first for all four positive cases in both repeats.
- Neighbor expansion recovered the previously unresolved positive case (f14) in both repeats, but caused a different positive case (f06) to defer in both repeats; the remaining positive controls stayed accepted in both arms.
- All four absent cases were refused in both arms and both repeats; no absent case was ever accepted from a wrong source.
- Conditional expansion only after a base refusal replayed at 4/4 positive successes per repeat. This is a replay of observed decisions, not a fresh adaptive validation.
- Original source offsets and hashes verified unchanged after both arms. Manual privacy preparation remains required.

Do not revise the earlier fresh-questions result: it stays 15/16 positive with 4/4 missing-source refusals. The neighbor probe's reused questions are a post-hoc analysis, not a replacement heldout.

### Interpretation

Evidence supports a bounded, optional fallback stage — expand to same-document neighbors only after a base refusal — for further testing. It does not support unconditional extra context or fleet-wide readiness. Neighbor retrieval stays OFF by default unless an explicit experimental neighbor preset is enabled.

## Local prepared-artifact cache experiment

Design: 20 sources. Measured cold misses, warm hits, source-byte changes, policy changes, entry corruption, and reload persistence. Timing covers local JSON I/O only.

### Results

| Condition | Hits | Misses |
|---|---|---:|
| Cold (first prepare per source) | 0 | 20 |
| Warm (repeat, nothing changed) | 20 | 0 |
| One actual source-byte change | 19 | 1 |
| Preparation-policy change | 0 | 20 |
| One corrupt entry | 19 | 1 |
| Reload from disk | 20 | 0 |

- Original 20 source hashes reread unchanged.
- No lead/model token savings were measured; cache timing is local JSON I/O only, not end-to-end retrieval.

### Trust boundary

This prototype caches references and integrity metadata only — it is not automatic privacy approval. Production approval must be externally supplied and bound to the source hash, the policy version, and the prepared artifact. A changed source, a changed policy, or a corrupt entry must return a miss, never silently reuse a stale artifact.

### Interpretation

The cache is separate from retrieval accuracy: it is not needed for accuracy and does not substitute privacy review. Optional fallback-stage use is justified for further testing only — this is not deployment proof.

## Verification

Neighbor probe: 32 actual HTTP calls across both arms and both repeats, all complete; no wrong accepted source in any cell. Cache experiment: six measured conditions with stable source hashes. Original source offsets and hashes verified unchanged. No runtime defaults, thresholds, fleet cards, merges, or deployments changed. Private questions, source data, scripts, labels, and raw logs remain outside this public findings branch.
