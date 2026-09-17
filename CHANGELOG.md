# Changelog

## 0.2.0

- Rename package metadata to super-jev to match the repository.
- Add a configurable data-organizer domain pack that runs through the existing loop.
- Add a JSON CLI with explicit live/demo modes, output protection and a review queue.
- Document input/output contracts for terminal-equipped LLM agents.
- Add tests for grouping, review routing, validation, CLI output and overwrite protection.

Validation: 26 automated tests, both original offline demos, and the organizer offline demo. Regression coverage includes private error handling, review-policy enforcement, output permissions, failure cleanup, preventing inference when output exists, excluding incidental record metadata, bounded input reads, and the successful mocked live-response contract. One live organizer smoke test on `jev-1.13.0` classified all four synthetic records as expected, grouped three records, and routed the `other` record to review. Low-confidence routing is covered by mocked tests. See [organizer validation](docs/organizer-validation.md) for results and limits. This release remains experimental; no npm publication, MCP server, or exchange integration is included.

## 0.1.0

Initial harness, Jev adapter, two domain demos, JSONL logs and 13 tests.
