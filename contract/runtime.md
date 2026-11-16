# Run-time contract (tool_schema 3)

Authority: REQ-61, REQ-77, REQ-80, REQ-27 rule 1, REQ-40. `tool_schema` is **3** (tools.json). Any change bumps it (CHANGES).

## Process start

- argv: no arguments. Rust: `regspike-rs`. TypeScript: `node regspike.mjs` (working names, REQ-73). The suite takes the full command after `--` (`python3 conformance/run.py -- <command...>`), as spike 3 did. The plugin starts the server through the launcher (REQ-77); the suite starts the binary directly.
- Transport: MCP over stdio, newline-delimited JSON-RPC 2.0, protocol version `2024-11-05` in `initialize`, then `notifications/initialized`.
- Error channel: a domain failure is a `tools/call` result with `isError: true` and one plain-text content item (never a JSON-RPC error object); JSON-RPC errors are for malformed protocol only (tools.json `conventions.errors`). The suite treats a JSON-RPC error on a domain failure as a failure.
- The server serves one session at a time and exits on stdin EOF (REQ-10, REQ-27 rule 5). On EOF it first flushes the journal queue, writes the decision log where REQ-27 rule 5 and the table in "Interview ends and server exit" below require, removes its pid file, then exits.

## Environment the suite injects

| Variable | Purpose |
|---|---|
| `CLAUDE_PLUGIN_DATA` | The data directory (REQ-61). The suite sets it to a fresh per-scenario directory under `.scratch/` and reads journals and logs only below it. Layout: see "Journal location" below. |
| `CLAUDE_PROJECT_DIR` | The project key and, for decision logs, the project root (REQ-61, REQ-62). The suite sets it to a per-scenario directory. Fallback when absent: the server uses its working directory as the project; the suite sets it explicitly except in the scenario that tests absence. |
| `CLAUDE_CODE_SESSION_ID` | Start-up session id fallback (REQ-27 rule 1); set only in scenarios testing that fallback. |
| `REGSPIKE_CLOCK_OFFSET_SECONDS` | Test-only integer added to the server's clock, so that pruning (30 days, REQ-61) is testable without waiting. Absent means 0. |

Spike 3's `JOURNAL_DIR` is not part of this contract.

## Journal location (REQ-61)

Both implementations must produce byte-identical paths; the suite computes the same paths (`conformance/run.py`, scenarios S-L1 and S-L2).

- Data root: the value of `CLAUDE_PLUGIN_DATA` if set and non-empty; otherwise `<temp>/decision-register-data`, where `<temp>` is the operating system temp directory as given by `$TMPDIR` if set, else `/tmp` (Rust `std::env::temp_dir()`, Node `os.tmpdir()` both do this on Linux; Windows is out of scope, REQ-61).
- Project path: the value of `CLAUDE_PROJECT_DIR` if set and non-empty, taken verbatim as a string (no realpath, no trailing-separator or case normalisation); otherwise the server's working directory at start, as the absolute path the operating system reports.
- Project key: the first 16 lowercase hexadecimal digits of the SHA-256 digest of the UTF-8 bytes of the project path.
- Layout, with `<dir>` = `<data root>/<project key>`:
  - `<dir>/<interview_id>.jsonl` the journal of an active, complete or unfinished interview;
  - `<dir>/<interview_id>.pid` the pid file, present while a live server owns the interview, removed at end or exit (REQ-27 rule 5);
  - `<dir>/archive/<interview_id>.jsonl` the journal moved here (renamed, not copied) when the interview ends (REQ-23); the `archive` folder is created on first use. An unfinished journal is never moved.
- Permissions (POSIX): every directory the server creates (data root fallback, `<dir>`, `archive`) has mode `0700`; every file it creates (journal, pid file) has mode `0600`, set at creation, not afterwards. A directory that already exists keeps its mode.
- Pruning (REQ-61): at server start, archives and unfinished journals in `<dir>` older than 30 days by file modification time (plus the test clock offset) are deleted.

## `_meta` and hook-style calls

- A model-style call is `tools/call` with `params._meta = {"claudecode/toolUseId": "<id>"}`. A hook-style call is `tools/call` with no `_meta` key at all. The three hook-facing tools reject the former (REQ-40); model-facing tools accept both.
- A hook-style call that carries a session id passes it as the `session_id` argument of the hook-facing tool (`note_prompt`, `check_stop`, `skill_started`). Model-facing tools take no session id.
- Hook-facing results are empty text or exactly `{}`; only `check_stop` may return `{"decision":"block","reason":...}`.

## Compatibility with spike 3 (`docs/evidence/spike3/acceptance.py`)

- Tool names kept: `begin_interview`, `log_item`, `resolve`, `park`, `reopen`, `status`, `resume`, `check_stop`, `note_prompt` (checked by `check_contract.py`).
- Script changes C2 must make, listed here so they are not discovered later: (1) `begin_interview(interview_id=...)` becomes `begin_interview(tool_schema=3, skill="grill-me", mode="register")` (the id is server-assigned and returned); (2) `JOURNAL_DIR` is replaced by `CLAUDE_PLUGIN_DATA` and the journal path by the layout above; (3) `note_prompt` and `check_stop` calls gain `session_id` and are sent without `_meta`; (4) `resume` takes `id`, not `interview_id`; (5) `status` keeps the top-level `items` counts and `journal_schema` the script reads, and gains the other fields in tools.json; the `items` counts must equal the counts derived from `tree` by status, and every C2 scenario that reads `status` asserts it. `log_item`, `resolve`, `park` and `reopen` keep their argument names.
