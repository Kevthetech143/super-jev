# Changelog

## 0.2.0

- Rename package metadata to super-jev to match the repository.
- Add a configurable data-organizer domain pack that runs through the existing loop.
- Add a JSON CLI with explicit live/demo modes, output protection and a review queue.
- Document input/output contracts for terminal-equipped LLM agents.
- Add tests for grouping, review routing, validation, CLI output and overwrite protection.

Validation: 22 automated tests, both original offline demos, and the organizer offline demo. Regression coverage includes private error handling, review-policy enforcement, output permissions, failure cleanup, and preventing inference when output exists. Live CLI failure paths use a mocked transport; no live API calls were made for this release. Original two domains previously smoke-tested against live Jev. Organizer live API validation is pending. No npm publication, MCP server, or exchange integration is included.

## 0.1.0

Initial harness, Jev adapter, two domain demos, JSONL logs and 13 tests.
