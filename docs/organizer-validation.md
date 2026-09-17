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

## Live smoke test: blocked

No `TYPESAFE_API_KEY` was available in the review process environment or checked configuration locations. No live provider request was attempted. Mocked transport checks are not evidence of live API compatibility or classification accuracy.

Merge and release remain gated on a successful live organizer smoke test with synthetic records and passing CI for the final revision. When a credential is securely configured, run the CLI with the synthetic bundled fixture and check the model/usage fields, complete record IDs, expected categories, confidence-based groups, and the `other` review queue. Never include credentials or raw run logs in validation artifacts.

The historical `docs/live-validation.json` covers only the two original v0.1 domains; it does not validate the organizer. Confidence is not a correctness guarantee, and this small fixture is not an accuracy benchmark. Native TypeScript execution does not provide static type checking.
