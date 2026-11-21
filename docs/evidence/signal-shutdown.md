# Signal shutdown: SIGINT, SIGTERM and SIGHUP run the stdin EOF shutdown in both builds

Date 2026-11-21. Issue #90. This is a repair outside the retirement experiment's five measured changes (user decision of 2026-11-21): it does not touch ADR 0003 (tokens only), issue #79 stays open and the only candidate is #80. The spec text is in `docs/requirements.md` (REQ-27 rule 5), `contract/runtime.md` ("Signals and shutdown limits"), `contract/journal.md` and `contract/CHANGES` (no `tool_schema` bump, stays 4); the scenarios are S-27f to S-27n.

Why: Claude Code stops the server at session end with SIGINT, not stdin EOF (`docs/evidence/live-run-4.md`, second finding; `docs/evidence/perf-comparison.md`, robustness table). Neither build handled any signal, so a normal session end wrote no decision log and left a stale pid file.

## 1. Tooling

`conformance/sigtest/` (stdlib Python; measurement tooling, not run by CI; output below `$SIGTEST_OUT`, default `.scratch/sigtest`, gitignored). Builds default to `impl/rust/target/release/regspike-rs` and `impl/ts/dist/regspike.mjs`; `REGSPIKE_RS_BIN` and `REGSPIKE_TS_BUNDLE` override them.

| Script | What it does |
|---|---|
| `drive.py` | One run through `plugin/bin/register-launch` (the launcher execs, so the child is the server): note_prompt, begin_interview, log_item q1 and q2, resolve q1, optionally check_stop, then EOF or a signal; snapshot of journal, pid files and logs; a fresh server on the same data runs `status`, `resume`, `status`. |
| `matrix.py` | The 16-run matrix (2 builds, flushed and unflushed, EOF and three signals); compares each signal row with its EOF row and the two builds with each other (interview ids masked). |
| `burst.py` | 4000 pipelined `log_item` calls with a signal mid-flight, repeated; checks the journal is whole and gapless, a single `interrupted` log, no pid file, exit code 128 plus the signal number. |
| `edge.py` | `mid` (8 MiB request in flight), `double` (four signals in a row), `pings` (2000 pipelined pings), `ignore` (parent ignored SIGHUP and SIGINT), `race` (EOF and SIGTERM 0 to 3 ms apart, both orders). |

The figures marked `rust-impl-report` and `ts-impl-report` come from the implementation reports of the sessions that ran these scripts (their raw output stays in the gitignored `.scratch/sigfix/`); the matrix and `edge.py` figures marked as re-runs were produced again with the committed scripts.

## 2. Reproduction on the unmodified builds (main at ba36518, 16 runs)

Driver: the first version of `drive.py`. Every signal killed the server by its default action (return codes -1, -2, -15) in both builds: no decision log, pid file left. Unflushed variant: the journal held only the `start` event (1 event; EOF row 4), `status` reported the interview as resumable with no items and an empty tree. Flushed variant (one `check_stop` before the signal): 6 events, as the EOF row, the tree restored by `resume` (q2 open, q1 resolved), still no log and a stale pid file. The EOF rows in both builds exited 0, left no pid file and wrote one log. Rust and TypeScript results were identical.

## 3. After the fix (both builds, branch builds, 16 runs)

Run again on 2026-11-21 with the committed `matrix.py`. Every signal row equals its EOF row in each build (events, last event, pid files, decision-log count, `end_kind: interrupted`, resumable, items, resume tree), and the two builds are equal to each other row by row (interview ids masked). Exit codes: EOF 0, SIGHUP 129, SIGINT 130, SIGTERM 143.

| Variant | Events on disk | Pid file | Decision logs | `end_kind` | `resume` tree |
|---|---|---|---|---|---|
| unflushed (EOF, SIGHUP, SIGINT, SIGTERM) | 4, last `resolve` | none | 1 | interrupted | q2 Open, q1 Resolved |
| flushed (EOF, SIGHUP, SIGINT, SIGTERM) | 6, last `stop` | none | 1 | interrupted | q2 Open, q1 Resolved |

Parent ignores SIGHUP and SIGINT at spawn (`trap "" HUP INT; exec <server>`, active interview): both builds still handle all three (129, 130, 143), one log, no pid file. Before the fix Rust kept ignoring both and TypeScript reset them and was killed (`perf-comparison.md`).

## 4. Burst, mid-request and races

Rust (`rust-impl-report`, 30 runs per signal, signal at 15 ms into 4000 pipelined `log_item` calls):
- Reader started after the signal (pipe full, main thread blocked in the reply write): SIGINT, SIGTERM and SIGHUP 30/30 good. Signal to exit median 8 to 9 ms, max 11 ms. The journal exceeds the delivered replies by exactly 1 in 26 to 29 of 30 runs: the reply being written at exit is journalled but not delivered.
- Live reader: 30/30 for each signal; journal items equal replies in all 90 runs; signal to exit median 35 to 57 ms, max 354 to 424 ms (flush of 500 to 2000 queued events plus 1 ms lock polling).
- EOF and SIGTERM 0 to 3 ms apart, 60 runs: EOF first 30/30 exit 0, signal first 30/30 exit 143; always one log, whole journal, no pid file. Re-run for this entry with `edge.py race`: the same.

TypeScript (`ts-impl-report`):
- 4000-item burst, live reader, signal at 50 ms, 10 runs per signal: 30/30 good; all 4000 items committed; signal to exit 161 to 172 ms (the signal is handled after the event-loop turn that processes the whole batch).
- 8 MiB `note_prompt` in flight, SIGTERM: exit 143, no torn journal line, newline-terminated, one log, no pid file. Re-run for both builds with `edge.py mid`: exit 143, no torn line, one log, no pid file (how many events the journal holds depends on how much of the request had been read).
- Four signals in a row (SIGTERM, SIGTERM, SIGINT, SIGHUP): exit 143, exactly one log, no pid file.
- 2000 pipelined pings then SIGTERM: all 2000 replies read, exit 143.
- Start-up window: a signal 5 or 15 ms after spawn killed the process (return code -15, empty journal) in 38 of 40 runs, because Node takes about 35 to 43 ms from spawn to its first reply and installs the handlers only when the bundle runs. Nothing is lost in that window (no interview exists). At 100 ms: 20/20 good. The Rust window is microseconds.
- EOF and SIGTERM 0 to 3 ms apart (`edge.py race ts`), 60 runs per pass. First version of the fix (listeners installed, process leaves by itself after EOF), three passes: signal first 30/30 exit 143 each time; EOF first exit 0 in 16 to 20 of 30 and return code -15 in 10 to 14 of 30 (14 of 30 in a fourth pass for the diagnosis). The files were right in every run. Cause, measured with `strace -e trace=rt_sigaction` on a minimal script: when the event loop empties, Node's tear-down stops the libuv signal watcher and calls `rt_sigaction(SIGTERM, SIG_DFL)` shortly before `exit_group`, so a SIGTERM in that window takes the default action. With `process.exit(0)` the handler is still installed at `exit_group`. Fix: the EOF shutdown ends with `exit(0)` (`process.exit`), the listeners stay installed until the process is gone. After the fix, six passes of 60 (360 runs, 180 per order): EOF first exit 0 in 180/180 (0 non-zero); signal first exit 143 in 179/180, exit 0 in 1/180 (a tie: the process observed EOF first). Signal then EOF at offsets 0 and 0.05 ms, 100 runs each, no interview: exit 0 in 100/100 in both, before and after the fix (the tie); at 0.5 ms 143 in 100/100 in both. The tie is not new. 2000 and 20000 pipelined pings then EOF: all replies read and exit 0, before and after. The parenthetical that stood here, "stdout to a pipe is written synchronously, so `process.exit` loses no reply", was wrong: stdout to a pipe is not written synchronously and `process.exit` drops queued replies (section 9, issue #102). That run did not lose data only because its reader and the default 64 KiB pipe kept up; with the pipe forced to 4096 or 8192 bytes the loss is reproduced (section 9). The scenarios do not exercise the race.
- Stalled stdout reader (pipe full): the original text here said the write is synchronous and blocks the handler until SIGKILL; that was wrong for TypeScript (section 9: replies are queued, a signal handler runs and exits at once, cutting the queued replies). Rust gives up after 5 s with the outcome of SIGKILL on a request in flight, and its reply write is outside the lock so a stalled reader does not use the bound.

Unit tests: Rust 86 (79 and 7 new: shutdown twice from EOF and from a signal, EOF then signal, signal then EOF, lock held past the bound, lock released inside the bound, poisoned lock); TypeScript 111 (103 and 8 new; the fix changed the EOF cases to expect exit 0 and added a failing-EOF-shutdown case). Conformance 144/144 in both builds (135 and the nine S-27f to S-27n).

## 5. Cost

- Rust binary 997,816 to 1,036,056 bytes (+38,240, +3.8%); one new direct dependency, `libc` (already in the lock file, not compiled on x86_64 Linux before); `cargo tree` differs by one line.
- TypeScript bundle 139,787 to 141,300 bytes (+1,513; the explicit `exit(0)` after EOF and its comments add 275 of that).
- Rust request path, advisory (paired and interleaved main against branch, three pairs, `conformance/bench/bench.py` defaults, shared busy machine, load average about 1.5): `log_item` 0.029 to 0.033 ms (+16.4, +16.1, +15.7%), `status` about 1.2 to 1.5 ms (+21.2, +20.6, +22.7%); calls with no interview +2 to +4%; `check_stop` -2.5, -1.9, -2.8%; `resolve` +3.1, +4.1, -6.0%; cold start through the launcher 3.71 to 3.83 ms to 3.77 to 3.89 ms, direct 1.10 to 1.13 ms to 1.12 to 1.16 ms. The plan's investigation trigger (more than 10% in the same direction in all three pairs) fired for `log_item` and `status`. A main-built clone with only an idle parked thread added shows the same cost (`log_item` +19, +14, +9%; `status` +24, +22, +24%), so it comes from the process being multi-threaded (glibc single-thread fast paths lost), not from the mutex, the reply move or `sigwait`. It follows from the decided block-and-`sigwait` design and is microseconds per call. Advisory, not a pass or fail criterion.

## 6. Not verified

- Claude Code's SIGKILL grace period after SIGINT (measured in section 7: about 0.5 s with a server that ignores signals; a server that exits on SIGINT is not killed) and how it treats the 128 plus n exit code (section 7: the exit is reaped and not acted on in `-p` mode; interactive mode not driven).
- A session started under `nohup claude` (whether an inherited SIGHUP or SIGINT ignore reaches the server; in the one normal session read from `/proc` on 2026-11-21 only SIGPIPE was ignored, `SigIgn 0x1000`, for the server and its parent).
- The live check from the branch binary (plan step 6): done on 2026-11-21, section 7 (with `--mcp-config` pointing at the branch builds, not through `register-use` and the installed plugin; headless, not the interactive TUI).
- A Rust shutdown that hits the 5 s bound (unit-tested only). The 5 s is the Rust implementation's own wait for an in-flight request; Claude Code's budget measured in section 7 is about 0.5 s, so it is not a figure Claude Code grants.
- Power loss; write-behind is unchanged (a hard kill still loses events since the last flush; `wb-findings` measured per-event synchronous writes at about +0.005 to +0.011 ms per call without fsync, not adopted, user decision).
- All timings come from a shared desktop and bound relative cost only.

## 7. Live Claude Code check (plan step 6), 2026-11-21

Real `claude` 2.1.330 (`claude -p`, `--model haiku`, `--tools ""`, `--setting-sources project`, `--strict-mcp-config --mcp-config <throwaway json>`, `--allowedTools` for `mcp__register__{begin_interview,log_item,status,resume}`), branch tip 190be6a, both builds rebuilt/checked (Rust 1,036,056 bytes, `cargo build --release --locked` up to date; TypeScript `npm run build`, 141,300 bytes). The MCP config points at a 5-line `exec` wrapper (`wrapper.sh`) that logs its pid, the server's and the parent's `/proc/<pid>/status` `SigIgn`/`SigBlk`/`SigCgt`, then `exec`s the build directly (Rust binary, or `node dist/regspike.mjs`) with `CLAUDE_PLUGIN_DATA` and `CLAUDE_PROJECT_DIR` set to throwaway directories. The whole `claude` process ran under `strace -f -tt -e trace=kill,tgkill,tkill,wait4,waitid` so the signals Claude Code sends, the server's exit status and the timing come from one clock (the wrapper, `/proc` polling every 1 ms and strace agree). The model was told to call `begin_interview` and `log_item` and never `check_stop`, so no log_item was flushed before the close. Not through `register-use` and the installed plugin, which were not touched; the launcher/plugin path adds only an `exec` shell in front of the same binary (as in `drive.py`). Raw logs, driver and per-run directories: `.scratch/livecheck/<rust|ts>/<run>/` (`wrapper.log`, `strace.log`, `claude-out.txt` stream-json with timestamps, `result.json`, the journal and the decision log); driver `.scratch/livecheck/driver.py`, table `.scratch/livecheck/table.py`. Single trial per cell, shared busy machine.

How the close was delivered: mid-turn (`mid`): `claude -p <prompt>` with stream-json output; the signal was sent to the claude pid (a verified child of the strace this script started) as soon as the third `log_item` tool result appeared, while the model was generating its next call (the stream shows `[Request interrupted by user]`). Idle (`idle`): `claude -p --input-format stream-json`, which keeps claude alive waiting on stdin after the turn; five `log_item` calls completed, the `result` event arrived, 3 s later the signal was sent (claude and server both confirmed alive). The interactive TUI was NOT driven: a pty session in the repository tree opened an "Allow external CLAUDE.md file imports?" dialog (the project imports files under `~/.claude`) whose answer is stored in the user's Claude configuration; I did not answer it and used the headless streaming mode as the closest equivalent (aborted run kept as `.scratch/livecheck/rust/aborted-pty-dialog`). The signal went to the claude process only, as asked, not to the whole process group, except the two `pgrp` rows.

### Observed numbers

Times in ms from the moment strace shows the signal arriving at claude. "kill" = when claude's `kill()` on the server pid returned; "SIGCHLD" = when the server's exit reached claude; exit status from `wait4`. "Poll" = my 1 ms `/proc` poll, from my `os.kill` call to the pid vanishing (includes the claude reaction).

| Build | Case | Signal to claude | Claude sent the server | kill (ms) | SIGCHLD (ms) | Server exit | Poll (ms) | Claude rc | Journal events (items; tool results seen) | Decision log, `end_kind` | Pid file | `resume` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Rust | mid | SIGINT | SIGINT (twice) | 4.8 | 5.9 | 130 | 14.9 | 0 | 4 (3; 3) | 1, interrupted | none | 3 open items |
| Rust | mid | SIGHUP | SIGINT | 3.6 | 4.7 | 130 | 15.2 | 129 | 4 (3; 3) | 1, interrupted | none | 3 open items |
| Rust | idle | SIGINT | SIGINT | 4.2 | 5.3 | 130 | 8.5 | 0 | 6 (5; 5) | 1, interrupted | none | 5 open items |
| Rust | idle | SIGHUP | SIGINT | 4.1 | 5.1 | 130 | 8.9 | 129 | 6 (5; 5) | 1, interrupted | none | 5 open items |
| TypeScript | mid | SIGINT | SIGINT (twice) | 13.2 | 19.3 | 130 | 25.0 | 0 | 4 (3; 3) | 1, interrupted | none | 3 open items |
| TypeScript | mid | SIGHUP | SIGINT | 4.5 | 10.5 | 130 | 14.9 | 129 | 4 (3; 3) | 1, interrupted | none | 3 open items |
| TypeScript | idle | SIGINT | SIGINT | 5.2 | 11.2 | 130 | 12.2 | 0 | 6 (5; 5) | 1, interrupted | none | 5 open items |
| TypeScript | idle | SIGHUP | SIGINT | 4.8 | 11.1 | 130 | 12.3 | 129 | 6 (5; 5) | 1, interrupted | none | 5 open items |
| Rust | idle, SIGHUP to the process group (no strace) | SIGHUP to claude, wrapper and server | not observed | - | - | not observed | 7.4 | 129 | 6 (5; 5) | 1, interrupted | none | 5 open items |
| TypeScript | idle, SIGHUP to the process group (no strace) | as above | not observed | - | - | not observed | 6.7 | 129 | 6 (5; 5) | 1, interrupted | none | 5 open items |

Common to every row: journal ends with a newline and has gapless seq numbers; every acknowledged `log_item` is in the journal although no `check_stop` was called (the shutdown flushed them); the decision log lists the open items under "Interrupted: the server exited with the interview unfinished"; a second server on the same data reported the interview resumable (`status`) and `resume` restored the tree (q1..q3 or q1..q5, all Open, same text) with state `active`.

Inherited dispositions (wrapper, read before `exec`): server `SigIgn 0000000000000000` (Claude Code's spawn does not pass its own SIGPIPE ignore on), parent `claude` `SigIgn 0000000000001000` (SIGPIPE only; SIGINT, SIGHUP, SIGTERM not ignored) in all 10 runs. After `exec` and start-up: Rust `SigIgn 0x1000` (its runtime's SIGPIPE), `SigBlk 0x4003` (SIGHUP, SIGINT, SIGTERM blocked in the main thread for `sigwait`); TypeScript `SigIgn 0x1001000` (Node's own SIGPIPE and SIGXFSZ), `SigBlk 0`.

Cost: client-side `total_cost_usd` USD 0.0018 to 0.0033 per run where Claude Code wrote a result event (the SIGHUP-killed headless runs wrote none); the 10 rows, the aborted pty attempt and the grace probe together are well under USD 0.05 (estimate; about 37k cached prompt tokens and 300 to 730 output tokens per run).

### Which signal Claude Code sends, and the SIGKILL grace period

- Claude Code translates its own SIGINT and SIGHUP into a SIGINT to each MCP server child, sent within 3.6 to 13.2 ms; never SIGHUP or SIGTERM in these runs. With a SIGINT to claude mid-turn it sent the server a second SIGINT about 8 to 10 ms later (after the server had already exited, before the zombie was reaped); harmless. Claude's own exit code was 0 for SIGINT and 129 for SIGHUP.
- The server's own exit code 130 (128 plus SIGINT) was reaped by claude with no complaint; nothing in stdout or stderr mentioned the exit code.
- No SIGKILL in any of the 8 strace rows: both builds exit 5 to 20 ms after the SIGINT. Grace period, measured with a stub MCP server that ignores SIGINT, SIGTERM and SIGHUP and keeps running (`.scratch/livecheck/grace/`): claude sends SIGINT at 0 ms, polls the pid with `kill(pid, 0)` every 50 ms, sends SIGTERM at 100 ms and SIGKILL at 501 ms (stub gone at 509 ms, status `WIFSIGNALED SIGKILL`); it did not close the server's stdin first. So the shutdown budget in this Claude Code version is about 0.5 s from the first SIGINT. Single trial; Claude Code 2.1.330 (a documented interactive exit was not driven).
- Consequence for the earlier figures (section 4): the Rust 4000-event burst took up to 424 ms and the TypeScript 4000-item burst about 160 ms from signal to exit on this machine; a session with a very large unflushed queue could approach the 0.5 s budget and then lose the unflushed events as in a SIGKILL. The 5 s Rust bound in `runtime.md` therefore exceeds what Claude Code gives it; typical sessions (tens of events) took 5 to 20 ms.

### Verdict against the criteria

Criteria: decision log with `end_kind: interrupted`, no stale pid file, `resume` works.

| Build | Mid-turn close | Idle close |
|---|---|---|
| Rust | PASS (SIGINT and SIGHUP) | PASS (SIGINT, SIGHUP, SIGHUP to the process group) |
| TypeScript | PASS (SIGINT and SIGHUP) | PASS (SIGINT, SIGHUP, SIGHUP to the process group) |

Not covered: the interactive TUI (`/exit`, Ctrl-C, terminal close) and a session via `register-use` and the installed plugin; a very large unflushed queue against the 0.5 s budget; whether Claude Code 2.1.330's grace period is configurable or differs between `-p` and interactive mode; the SIGHUP-to-process-group rows did not capture the server's exit status or the order of the two signals (strace was not used there).

## 8. Open items (not verified)

- Interactive TUI close (`/exit`, Ctrl-C, terminal close): not driven; blocked by the stored-answer 'Allow external CLAUDE.md file imports?' dialog.
- A stalled stdout reader: measured in section 9 (both builds); Rust's 5 s wait does not cover the reply write and Rust has no ceiling at EOF; TypeScript ends the drain after 5 s.
- A very large unflushed queue against the 0.5 s budget: not run under Claude Code. Earlier bursts (4000 queued events: Rust up to 424 ms, TypeScript about 160 ms from signal to exit) are below it on this machine, but a larger queue could exceed it and then lose its events as in a SIGKILL. The 5 s Rust bound is the implementation's own wait and does not extend the budget.
- `nohup claude` (inherited ignored SIGHUP or SIGINT): not run; only one normal session was read from `/proc`.
- The grace-period figure (SIGINT at 0 ms, SIGTERM about 100 ms, SIGKILL about 501 ms) is a single trial, claude 2.1.330, with a stub server that ignores signals; whether it is configurable or differs between `-p` and interactive mode is not known.

## 9. Reply delivery at shutdown (issue #102, 2026-11-21)

Correction to sections 4 and 6: Node's stdout pipe is non-blocking here (strace of the 200 KB reply, 8 KiB pipe: `write(1) = 4096`, `write(1) = EAGAIN`, `exit_group(0)`), so replies are queued in libuv and `process.exit` (added by the #90 fix, commit cbfbd96) discarded them. The pre-#91 bundle (2b10e5e) lost nothing.
- TypeScript before the fix, EOF immediately after the last request, late or slow reader: replies of 16383 to 16385 bytes lost in 136 to 186 of 300 runs, of 64 KB and 200 KB in 300 of 300, 2000 pipelined small replies in 16 to 20 of 20; 52 KB `tools/list` in 41 to 53 of 100. Whether a run loses data is decided by the pipe capacity against the unread output at exit (the kernel gave 8192 or 65536 bytes, depending on `fs.pipe-user-pages-soft` use), not by CPU load or reader speed. Forced 4096 or 8192 byte pipes: every cell above 8 KiB of output lost data.
- Rust: stdin EOF clean, 0 of 14,420 runs (the main thread's blocking write completes before it returns). Signal path: lost in the same cells as TypeScript (3,618 of 10,980 at a fixed 150 or 500 ms; partial lines), by design: the signal thread exits while the main thread is blocked in the reply write. Exit codes 143, 130, 129 throughout.
- Fix (TypeScript only, EOF path): `process.stdout.write("", () => process.exit(0))`, the listeners stay installed until the exit, so the -15 race fix of section 4 is kept (`edge.py race ts`: EOF first exit 0 in 120 of 120 runs after the change). A signal during the drain exits 0 at once (the EOF decided the status) and drops replies still queued; it is not a no-op. Stalled reader with EOF: the process waits, in both builds, and a signal ends it, but the journal state differs. TypeScript: the EOF shutdown has run, so the journal is flushed before the wait, and the signal exits 0. Rust: the main thread is blocked in the reply write before it sees the EOF, so a signal is the first trigger it observes, the exit status is 128 plus the signal number (143 for SIGTERM) and, while it waits, the journal is NOT yet flushed (the signal's own shutdown flushes it); a SIGKILL during the wait loses the events since the last write-behind flush. Rust has no timer and no ceiling on a stalled-reader write at EOF (the 5 s `SIGNAL_BOUND` covers only the signal thread's wait for a request in flight); TypeScript bounds the wait by `DRAIN_BOUND_MS` (5 s, the Rust figure reused as a declared constant, an `unref()`'d timer), after which it exits 0 with the reply tail dropped, the journal already flushed, so a SIGKILL during the wait loses no events either. Measured in this unit: `check_stdout_drain.py --expect-bounded-drain` exits 0 after about 5 s with stdout unread (pipe 4096 and 8192) and leaves no process in its group; the bundle without the bound fails that cell (no exit within 12 s). The reader closing its end ends the wait with exit 0 in both. A 3,770-run trial on a copy (0 lost) drained on every exit, signals included; it shows only that `write("")` completes after the earlier queued writes and is not evidence for the shipped EOF-only design. The shipped design's evidence is the 16 of 16 runs of the guard, the 24 invocations run in parallel, and the re-measurements in the issue #102 unit. Decision (user, 2026-11-21): the signal path is unchanged in both builds; it may cut a reply the client has not read, the journal stays whole, and it arises in practice when the client is closing (a signal from elsewhere, for example SIGHUP under `nohup claude`, can also reach a live client). Stated in `contract/runtime.md`.
- Guard: `conformance/check_stdout_drain.py` (CI, both builds): stdout pipe forced to 4096 and 8192 bytes, reply sizes 16385, 65536 and 200000 and 2000 pipelined pings, EOF at once, reader starting after it; all replies whole and exit 0. With `--expect-bounded-drain` (TypeScript job only) it adds the stalled-reader cell: stdin closed, stdout never read, pipe 4096 or 8192, 65536-byte reply; exit 0 within 4 to 12 s (the bound is 5 s), no process left in the server's group. Re-measured at implementation: a bundle built from main (before the change) fails 16 of 16 runs (3.7 s); the fixed bundle (141,285 to 141,846 bytes) and the Rust release binary pass 16 of 16 (4.0 s and 7.3 s). The check enforces its own read and exit deadlines (a server that never closes stdout or never exits is killed and reported as `not ok`; proved against a stub server that writes replies then never exits, with the ceilings shortened to 2 s for the test).
- Readline lines between a signal and the exit (decision, issue #102): none is processed. In TypeScript the signal listener runs between event-loop callbacks and calls the synchronous `process.exit`, so no later `line` event runs; at EOF the `close` event follows the last `line` event. In Rust the signal thread holds the shutdown lock from `finish()` through `exit`, so the main thread cannot process a request after `finish()`. Evidence: `edge.py` `mid` (8 MiB request in flight: journal has no torn line), `double` and `pings` (2000 pipelined pings then SIGTERM), section 4.
