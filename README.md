# decision-register

Structured decision register for the grill-me interview skill: an MCP server with typed calls, a JSONL store, hooks, and plugin packaging.

Status: built and released. Two implementations (Rust, `impl/rust`; TypeScript, `impl/ts`) pass one conformance suite; the plugin is in `plugin/`. Releases v0.1.0 to v0.1.3 are published (v0.1.3, `tool_schema` 5, owner-aware register: `owner_skill` on `status`, tagged 2026-11-26; release unit #128). Install and release steps: docs/release-plan.md and plugin/README.md.

- Requirements: docs/requirements.md (inputs: docs/requirements-inputs.md)
- Release and install plan: docs/release-plan.md
- Decision record: docs/adr/0001-structured-decision-register.md
- Build plan of record: docs/plan.md
- Progress log: docs/status.md
- Arm G reference code (read-only snapshot): reference/armG/

MIT (see LICENSE).
