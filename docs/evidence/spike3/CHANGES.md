# Spike 3: change-request test (Rust vs TypeScript on Node)

Question: how well does each language hold up when an agent changes an existing working server? Same three changes, same acceptance scenarios, both languages. Throwaway.

Base: the spike 2 implementation of the server described in .scratch/spike2/SPEC.md. Work on a COPY: `cp -r .scratch/spike2/<candidate> .scratch/spike3/<candidate>` (candidates: rust, ts-node), and change only the copy. Never touch .scratch/spike2/. Do not look at the other language's directory. Temp files in .scratch/, never /tmp/. No git commit or push; do not edit ~/.claude.

## Protocol (mandatory, this is what is measured)
- All builds and test runs go through ONE wrapper script `run.sh` in your copy (create it first; it may call the existing build.sh / container commands from spike 2, using `podman` explicitly). It appends one line to LOG.tsv per invocation: `<UTC timestamp>\t<change id>\t<exit code>\t<command>`. Do not build or test any other way.
- Before starting each change write `START <change id>` and when green `DONE <change id>` to LOG.tsv (plain lines, same file).
- Do the changes in order C1, C2, C3. A change is DONE only when the full test suite is green (old tests plus your new tests for that change).
- Cap: at most 12 run.sh invocations per change. If still not green, write `FAIL <change id>`, keep the code as is, and go on to the next change.
- Stop condition: one pass. No performance work, no refactors beyond what a change needs.
- At the end write RESULTS3.md: per change, invocations to green, failing invocations, notable compiler or test errors (one line each), and which files changed. Also: did any change require touching code unrelated to it, and did a compiler or type checker catch a mistake that would otherwise have been a runtime error (give the example). Do not invent numbers: LOG.tsv is the source for counts.

## Changes

### C1: Stale propagation
- log_item gains an optional `depends_on` (list of item ids, each must already exist; error otherwise).
- New tool `reopen(id, reason)`: only for a Resolved item; it becomes Open; every Resolved item that depends (directly or transitively) on it becomes Stale. Error if the item is not Resolved.
- Resolving a Stale item is allowed and returns it to Resolved (same rules as any resolve).
- check_stop treats Stale like Open (counts towards "N open").
- Old journals (events without depends_on) must still replay.

### C2: Decision kind with user-only resolve
- log_item accepts a new kind `decision`.
- A `decision` item can only be resolved with source=user; resolve with source=model on a decision is rejected with an error mentioning the id.
- A `decision` item can be parked only with parked_by=user; model park is rejected.
- Old journals still replay.

### C3: Journal schema versioning
- Every newly written journal event carries `"v": 2` and `"ts"` (RFC 3339 UTC).
- Replay (`resume`) accepts old events with no `v` (treated as v1) and v2 events. An event with `v` greater than 2 makes resume return an error that names the 1-based line number; state is not partially applied.
- status() reports `journal_schema: 2`.
- Journals mixing v1 and v2 lines replay correctly.

## Acceptance scenarios (the implementation's own tests must cover these; a separate black-box script also checks them over MCP stdio)
A1 (C1): log q1, log q2 depends_on [q1], log q3 depends_on [q2]; resolve all three; reopen q1 -> q1 Open, q2 Stale, q3 Stale; status counts open=1, stale=2; resolve q1 then resolve q2 -> q2 Resolved, q3 still Stale.
A2 (C1): reopen on an Open item errors; log_item with unknown depends_on errors.
A3 (C1): check_stop with only Stale items active returns a block whose reason lists the stale ids.
A4 (C2): log d1 kind=decision; resolve d1 source=model -> error mentioning d1; after note_prompt, resolve d1 source=user -> ok.
A5 (C2): park d1 parked_by=model -> error; q-kind item can still be parked by model.
A6 (C3): events written after the change contain v=2 and ts; a hand-written journal of three v1 lines resumes with the correct counts; appending a line with v=3 makes a fresh resume fail naming that line number (e.g. line 4) and the previous state is unchanged.
A7 (C3): status reports journal_schema 2.
