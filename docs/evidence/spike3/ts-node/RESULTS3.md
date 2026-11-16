# Spike 3 results: ts-node

Counts from ts-node/LOG.tsv. Caveat: all three changes were implemented in one edit pass before the first run.sh invocation, so C1's single run covered the C1+C2+C3 code and tests; the C2 and C3 invocations are re-runs of the same green suite recorded under their ids, not independent iterations.

| Change | run.sh invocations to green | failing invocations | notable errors |
|---|---|---|---|
| C1 | 1 | 0 | none |
| C2 | 1 | 0 | none |
| C3 | 1 | 0 | none |

All DONE (14 tests pass: 6 old + 8 new covering A1-A7 plus a mixed v1/v2 journal).

Files changed: src/core.ts (Kind, Item.dependsOn, Event reopen, append adds v/ts, apply rules, reopen, resume via scratch Registry, status journal_schema), src/server.ts (depends_on, decision kind, reopen tool), test/core.test.ts (new tests), run.sh and LOG.tsv (new).

Unrelated code touched: none required. resume was rewritten (replay into a scratch Registry then swap) because it is where C3's all-or-nothing requirement lives.

Type checker catches: no compile error occurred in this run (tsc passed first time), so no example to give. The `dependsOn` field being required on Item and the Event union would have flagged missed constructions, but nothing was actually flagged. Black-box MCP script not run by me.
