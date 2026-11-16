# Run-time contract (tool_schema 1)

Authority: REQ-61, REQ-77, REQ-80, REQ-27 rule 1, REQ-40. `tool_schema` is **1** (tools.json). Any change bumps it (CHANGES).

## Process start

- argv: no arguments. Rust: `regspike-rs`. TypeScript: `node regspike.mjs` (working names, REQ-73). The suite takes the full command after `--` (`python3 conformance/run.py -- <command...>`), as spike 3 did. The plugin starts the server through the launcher (REQ-77); the suite starts the binary directly.
- Transport: MCP over stdio, newline-delimited JSON-RPC 2.0, protocol version `2024-11-05` in `initialize`, then `notifications/initialized`.
- The server serves one session at a time and exits on stdin EOF (REQ-10, REQ-27 rule 5).

## Environment the suite injects

| Variable | Purpose |
|---|---|
| `CLAUDE_PLUGIN_DATA` | The data directory (REQ-61). The suite sets it to a fresh per-scenario directory under `.scratch/` and reads journals and logs only below it. Layout: `<CLAUDE_PLUGIN_DATA>/<project key>/<interview_id>.jsonl`, `.pid`, and `archive/`. |
| `CLAUDE_PROJECT_DIR` | The project key and, for decision logs, the project root (REQ-61, REQ-62). The suite sets it to a per-scenario directory. Fallback when absent: the server uses its working directory as the project; the suite sets it explicitly except in the scenario that tests absence. |
| `CLAUDE_CODE_SESSION_ID` | Start-up session id fallback (REQ-27 rule 1); set only in scenarios testing that fallback. |
| `REGSPIKE_CLOCK_OFFSET_SECONDS` | Test-only integer added to the server's clock, so that pruning (30 days, REQ-61) is testable without waiting. Absent means 0. |

When `CLAUDE_PLUGIN_DATA` is absent the server falls back to the operating system temp directory (REQ-61). Spike 3's `JOURNAL_DIR` is not part of this contract.

## `_meta` and hook-style calls

- A model-style call is `tools/call` with `params._meta = {"claudecode/toolUseId": "<id>"}`. A hook-style call is `tools/call` with no `_meta` key at all. The three hook-facing tools reject the former (REQ-40); model-facing tools accept both.
- A hook-style call that carries a session id passes it as the `session_id` argument of the hook-facing tool (`note_prompt`, `check_stop`, `skill_started`). Model-facing tools take no session id.
- Hook-facing results are empty text or exactly `{}`; only `check_stop` may return `{"decision":"block","reason":...}`.

## Compatibility with spike 3 (`docs/evidence/spike3/acceptance.py`)

- Tool names kept: `begin_interview`, `log_item`, `resolve`, `park`, `reopen`, `status`, `resume`, `check_stop`, `note_prompt` (checked by `check_contract.py`).
- Script changes C2 must make, listed here so they are not discovered later: (1) `begin_interview(interview_id=...)` becomes `begin_interview(tool_schema=1, skill="grill-me", mode="register")` (the id is server-assigned and returned); (2) `JOURNAL_DIR` is replaced by `CLAUDE_PLUGIN_DATA` and the journal path by the layout above; (3) `note_prompt` and `check_stop` calls gain `session_id` and are sent without `_meta`; (4) `resume` takes `id`, not `interview_id`; (5) `status` keeps the top-level `items` counts and `journal_schema` the script reads, and gains the other fields in tools.json. `log_item`, `resolve`, `park` and `reopen` keep their argument names.
