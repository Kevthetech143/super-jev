# Organizer v0.2.0 validation

PR #1 follow-up review on 2026-09-17, Node v24.11.1.

## Offline and mocked checks

- `npm test`: 26 passed, 0 failed.
- `npm run demo`: service recovery succeeds in two steps.
- `npm run demo -- --documents`: document review succeeds in one step.
- `npm run organize -- examples/organizer.json --demo`: invoice, receipt, and support records are grouped; `misc-1` is in review and excluded from automatic groups.
- A successful mocked live CLI call verifies JSON parsing, resolved model, token usage, all four output rows, grouping, and both low-confidence and `other` review routing.
- Missing answers, mismatched answer types, unknown categories, invalid confidence, and invalid probability distributions cannot reach the grouping tool.

Review fixes exclude incidental record metadata from provider evidence and enforce the 80,000-byte input limit while reading (at most 80,001 bytes retained). Tests cover snapshot isolation and multibyte input at the exact byte boundary.

## Live smoke test: passed

On 2026-09-17, one live evaluation used only the four synthetic records and categories in `examples/organizer.json`, with the default confidence threshold of 0.75. The command was `node --env-file=.env src/cli.ts organize examples/organizer.json --live`. Credentials were loaded from an ignored, owner-only local file and are not included in this report. No full run trace was saved.

The CLI exited successfully and emitted JSON with `version: 0.2.0`, `mode: live`, resolved model `jev-1.13.0`, and usage of 899 input tokens and 179 output tokens. The harness accepted the provider's typed answers and probability distributions. All four unique input IDs appeared exactly once in the output rows.

| Synthetic record | Category | Confidence | Routing |
| --- | --- | --- | --- |
| bill-1 | invoice | 0.98 | invoice group |
| receipt-1 | receipt | 0.98 | receipt group |
| help-1 | support | 0.98 | support group |
| misc-1 | other | 1.00 | review only |

The three automatic groups contained the expected IDs. The `other` group was empty and the review queue contained only `misc-1`. All categories matched the fixture's intended meanings, including the request for an invoice copy being classified as support. No live failure required a code change or retry.

This call exercised `other` routing with live data. Low-confidence routing is covered by deterministic mocked tests; the live fixture did not produce a low-confidence answer. Merge and release also require passing CI on the final revision.

The historical `docs/live-validation.json` covers only the two original v0.1 domains; it does not validate the organizer. Confidence is not a correctness guarantee, and this small fixture is not an accuracy benchmark. Native TypeScript execution does not provide static type checking.
