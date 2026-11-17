# Conformance suite

Black-box suite for REQ-71 and REQ-80. Driver: `run.py` (Python 3 standard library only). Contract: `../contract/`.

    python3 conformance/run.py -- <server command...>      run all scenarios (TAP-like output)
    python3 conformance/run.py --only 'S-A*,S-30' -- <cmd>  select by id (comma list, fnmatch globs)
    python3 conformance/run.py --list                       ids and titles
    python3 conformance/run.py --validate                   check every scenario against contract/tools.json, no server

The suite drives the server over stdio and reads and writes only below the data directory it supplied (journals, pid files,
archives; contract/runtime.md). The command runs in a scenario directory, so relative file paths in it are made absolute first.
Each invocation uses its own scratch directory (`.scratch/conformance/run-<random>/<id>/`: `data/` as CLAUDE_PLUGIN_DATA,
`project/` as CLAUDE_PROJECT_DIR, `cwd/` as the working directory, `server.stderr.log`); it is removed on pass and kept on failure.
The server is killed with its whole process group; one that does not exit within 5 s of stdin EOF fails the scenario
(runtime.md: exits on stdin EOF). A stdout line that is not a JSON-RPC 2.0 message fails the scenario. The variables
CLAUDE_CODE_SESSION_ID and REGSPIKE_CLOCK_OFFSET_SECONDS are unset unless a scenario sets them.

Each scenario is one file, `scenarios/<id>.json`: `id`, `title`, `req` (list), optional `env` and `unset_env`
(an empty string counts as unset for CLAUDE_PLUGIN_DATA and CLAUDE_PROJECT_DIR; `$dir` in an `env` value is the scenario directory) and
`steps`. `--validate` rejects any unknown key in a scenario, step or assert and any assert without exactly one operator, and checks the
arguments of every step that expects success against the tool's input schema (a `$variable` argument is replaced by a placeholder of the right type).

Steps (`op` defaults to `call`):

- `call`: `{"call": tool, "args": {...}, "meta": true|false (default: model-facing tools send _meta, hook-facing tools send none), "expect": "ok"|"error"|"protocol_error", ...}`.
  - `expect: ok`: `"save": {var: "dotted.path"}` and `"assert": [...]`. Every result is validated against the tool's result schema; every `status` result must have `items` equal to the counts derived from `tree`.
  - `expect: error` requires a tool result with `isError: true` (a JSON-RPC error object fails); `expect: protocol_error` requires a JSON-RPC error object. Both take `"mention"` (substring or list), `"not_mention"` and `"matches"` (regular expression or list) on the message text.
  - An assert entry is an optional `path` (dotted, list indices as numbers; omitted means the whole result) plus exactly one operator: `eq`, `ne`, `contains`, `not_contains`, `matches` (regular expression, `re.search`), `len`, `ge`, `absent: true` (the path does not exist), `decision_block: true|false` (whole result: a block decision, or no decision at all).
  - A `"$var"` string is replaced by the saved value (any type); `${var}` inside a longer string by its text. Predefined: `$iid` after a step saves it, `$key` the project key, `$dir`, `$data`, `$proj`, `$cwd`, `$pid` (the server's process id), `$n`, `$n1`, `$np1` after `journal load`, `$v1_n` after a v1 `journal write`.
- `stop` / `start` / `restart`: close and start the server process (same data directory); `stop` and `restart` require the graceful exit above. Each takes an optional `"server": name` (lower case, default `main`, the one the driver starts); a second server such as `b` runs against the same data directory, project and stderr log, `$pid_b` is its process id, and `call` and `tools_list` take `"server"` too. `--validate` follows which servers run: `start` needs a stopped name, the other steps a running one, and a `repeat` must leave the same servers running. `{"op": "kill", "server": name}` sends SIGKILL to the server's process group (default `main`): no graceful exit is required or waited for, and its pid file, journal and files stay as they were; the name can be started again. At the end of the scenario every running server must exit on stdin EOF.
- `tools_list`: REQ-71, checks the `initialize` reply and that `tools/list` returns exactly the tool names of `contract/tools.json` with equal input schemas (`$ref` resolved, `description`/`title`/`$defs` ignored, `required`/`enum`/`oneOf` compared as sets).
- `journal`: `load` (`where`: `active` or `archive`; `into`: a name, default `main`; sets `$n_<name>` lines and `$np1_<name>`, and for `main` also `$n`, `$n1` as a string, `$np1`), `check_events` (`from`, `ended`: every line valid against `contract/journal.schema.json`, `seq` 1..n gapless, first event `start`; `ended: true` requires a final `end`, `false` forbids any), `assert_events` (`from`; `events`: partial objects matching distinct events in order; `last`; `count`: {kind: n}; `absent`: kinds; `total`; `prefix`: another loaded journal; `at`: {position: partial object}, positions from 1 or negative from the end; `each`: {kind or `*`: partial object} that every event of that kind, at least one, matches), `write` with `form` `orig`, `v1_all` (only item, resolve, park and reopen lines, without v, ts, seq), `v1_but_last`, `orig_plus_v3`.
- `fs`: `{"op": "fs", "assert": [{"path": "rel/path", "exists": true|false (default true), "mode": "600", "nonempty": true, "matches": regex, "age_ge": seconds, "age_le": seconds}]}` (`age_ge`, `age_le`: seconds since the last modification); paths are relative to the data root (`CLAUDE_PLUGIN_DATA`, or `$TMPDIR/decision-register-data` when it is unset or empty), or with `"root": "project"` to the project path (`CLAUDE_PROJECT_DIR`, or the working directory when it is unset or empty; where the decision log goes), and may not be absolute or contain `..`; `$key` is the project key and `$iid` the saved interview id (contract/runtime.md, Journal location).

- `repeat`: `{"op": "repeat", "count": n, "var": "i", "steps": [...]}` runs the steps n times (1 to 200) with `$i` / `${i}` set to 1..n; `{"op": "repeat", "rows": [{...}, ...], "steps": [...]}` runs them once per row, each key a variable (a table). Repeats do not nest. `--validate` expands the first and last pass (count) or every row and validates the concrete steps.

`python3 -I conformance/selftest.py` tests the driver itself: every construct above must be parsed and the malformed forms rejected (it runs a tiny fake server for the execution tests; no real server needed).

- `seed`: `{"op": "seed", "path": "rel/path", "root": "data"|"project" (as for fs), "mode": "600", and exactly one of "content": text, "lines": [object or text, ...] (objects as JSON lines), "json": object (one JSON line, e.g. a pid file), "mkdir": true}` creates or replaces a file (or directory) as a crashed or earlier server might have left it; parent directories get mode 0700, files 0600 unless `mode`. `path` expands `$key`, `$iid` and `${variables}`. `$start` (and `$start_b`, ...) is the start time of the server process in clock ticks (field 22 of `/proc/<pid>/stat`, null where unreadable) for a pid file of a live owner; `$dead_pid`, defined once a seed mentions it, is the pid of a process that has exited, for a pid file of a dead one.

- `utime`: `{"op": "utime", "path": "rel/path", "root": "data"|"project", "age_seconds": n}` sets the access and modification times of an existing file or directory to n seconds ago, to exercise the age rules (pruning after 30 days, stale files after an hour); an `fs` assertion with `age_ge` then shows a file left alone, `age_le` one written just now.
- `stderr`: `{"op": "stderr", "assert": [{"empty": true|false} | {"contains": text} | {"not_contains": text} | {"matches": regex} | {"not_matches": regex}]}` checks what the server processes have written to standard error so far (the one `server.stderr.log`, shared by every named server); one operator per entry, `${variables}` expand.

The TAP detail of a passing scenario lists the values the assertions observed.
