# Spike 3 results: rust

Counts from LOG.tsv (run.sh invocations, all `build.sh test` via podman rust:1-slim):

| Change | Outcome | Invocations to green | Failing invocations |
|---|---|---|---|
| C1 | DONE | 1 | 0 |
| C2 | DONE | 1 | 0 |
| C3 | DONE | 1 | 0 |

One extra tooling slip occurred before C1's first logged invocation: my first run.sh had an arithmetic bug (grep -c on a missing file) and exited before building; it logged nothing and ran no build.

Notable compiler/test errors: none in any logged invocation. Only the 2 pre-existing dead-code warnings (Item fields).

Files changed (work/src/): 
- C1: core.rs (Item.depends_on, Event::Log depends_on with serde default, Event::Reopen, apply, log_item_with, reopen, 3 tests), main.rs (LogArg.depends_on, ReopenArg, reopen tool).
- C2: core.rs only (Kind::Decision, two guard checks in resolve/park, 2 tests).
- C3: core.rs only (SCHEMA_VERSION, now_rfc3339, commit adds v/ts, resume version check and real line numbers, status journal_schema, 3 tests).
- Added run.sh; patch_c1.py lives in spike3/ (helper used to apply C1 edits).

Unrelated code touched: no. Two small deviations inside the change's own area: log_item kept its signature and delegates to new log_item_with (old tests untouched); resume's line numbers now count raw lines including blanks (needed so v3 line number is the file line).

Compiler catch: none was triggered in the logged runs, because I made all edits in a single patch per change. The design does give one: adding `depends_on` to Event::Log / Item makes every construction and destructuring site (apply) a compile error until updated; and adding Kind::Decision would make any exhaustive match non-compiling. These were avoided up front, not observed as failures, so I claim no measured catch.
Not covered: the separate black-box MCP stdio script was not run by me; tool wiring (reopen, depends_on) is compiled but only unit-tested at State level.
