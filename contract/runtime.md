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

## Decision log (REQ-23, REQ-62, REQ-63)

- Directory: `<project path>/.scratch/` if that directory exists when the log is first written; otherwise `<project path>/decision-logs/`, which the server creates (mode `0700`) together with a `.gitignore` whose entire content is `*` followed by a newline (mode `0600`). `<project path>` is the project path of "Journal location" above. The choice is made when the first log of the interview is written and kept for that interview.
- File name: `decision-<interview_id>.md`; mode `0600` set at creation. It is written by creating `decision-<interview_id>.md.tmp` (mode `0600`) and renaming it over the final name, so a reader never sees a partial file. A later interview has a different id and never overwrites it.
- Written (never depends on the model): at every allowed finish attempt (including one allowed because the caps are spent, and the one that makes the interview `complete`), at `end_interview`, at a session-change end, at a stand-down, and on stdin EOF (an active, not-complete interview is flagged `interrupted`, REQ-27 rule 5). Before writing, the journal queue is flushed, so the position below is durable.
- Header: the file starts with lines of the exact form `key: value`, one per line, in this order, before any heading: `interview_id`, `journal_position` (decimal; the `seq` of the last journal event at the moment of writing, 0 if none; REQ-63 compares it with `status.last_event`; equal means fresh), `state` (`active`, `complete` or `ended`), `mode` (`register`, `plain` or `not engaged`), `end_kind` (`none` while the interview is still running, else one of the four `end_interview` values, `abandoned` or `interrupted`), `implicit_start` (`true` or `false`). The body (tree, answers with source and rationale, parked items and who parked them, reopen and stale history, hook-health counts) is markdown and not machine-compared.
- Which result returns the path: `end_interview.decision_log` and `status.decision_log` (null until a log has been written). A stood-down or session-changed interview has a log too; its path is not returned to anyone (no tool call receives it) but follows the rules above.

## Pid file and claim (REQ-52, REQ-61; build plan F1)

- Content: `<dir>/<interview_id>.pid` holds one line of JSON, UTF-8, ending in a newline: `{"pid":<integer>,"start":<integer or null>,"interview_id":"<id>"}`. `start` is the owner process's start time in clock ticks since boot: field 22 (`starttime`) of `/proc/<pid>/stat`, read by splitting the text after the last `)` on spaces (the 20th token after it). If `/proc/<pid>/stat` cannot be read (not Linux), `start` is `null` and liveness falls back to "the pid exists" (record the difference in the experiment file, build plan I4 drop rule).
- Liveness: the owner is alive iff the pid exists (signal 0 does not fail with "no such process") and, when `start` is not null, the current start time of that pid equals the recorded one exactly. A missing, empty or unparseable pid file means no owner (dead). A pid file whose pid is the reader's own counts as alive only if it is the reader's current claim.
- New interview: the server creates the pid file with `O_CREAT|O_EXCL|O_WRONLY`, mode `0600`, before writing the journal's first event; the id is new, so a clash means the id is drawn again.
- Claim by `resume` (and the stale replace) in this order: (1) read the pid file content C and decide it is stale; (2) rename `<id>.pid` to `<id>.pid.stale-<own pid>-<8 random hex>` (if the rename fails because the file is gone, go on to step 4: another claimer acted); (3) read the renamed file; if its content differs from C it was another claimer's live file: rename it back only if `<id>.pid` does not exist (otherwise leave it) and fail with `journal already claimed`; (4) create `<id>.pid` with `O_CREAT|O_EXCL`; `EEXIST` fails with `journal already claimed`; (5) delete the renamed stale file; (6) replay the journal; on replay failure remove the new pid file and leave the journal unfinished. Residual window: between steps 2 and 3 a third process can observe no pid file and claim; the steps narrow F1 but a rename-aside cannot make it zero, which build-plan slice I4 settles by test.
- Removal: the server removes its pid file at `end_interview`, session-change end, stand-down, and on EOF (after the journal flush and log write). A hard crash leaves it; it is then stale by the liveness rule.
- Orphans: pruning (30 days, "Journal location") also removes any `<id>.pid` and `<id>.pid.stale-*` file in `<dir>` older than 30 days whose journal `<id>.jsonl` is not there, and any `.stale-*` file older than one hour.
