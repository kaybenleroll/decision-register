# Conformance suite

Black-box suite for REQ-71 and REQ-80. Driver: `run.py` (Python 3 standard library only). Contract: `../contract/`.

    python3 conformance/run.py -- <server command...>      run all scenarios (TAP-like output)
    python3 conformance/run.py --only 'S-A*,S-30' -- <cmd>  select by id (comma list, fnmatch globs)
    python3 conformance/run.py --list                       ids and titles
    python3 conformance/run.py --validate                   check every scenario against contract/tools.json, no server

Each scenario is one file, `scenarios/<id>.json`: `id`, `title`, `req` (list), optional `pass_detail`, `env`, `unset_env`, and `steps`.
Per-scenario directories (`data/` as CLAUDE_PLUGIN_DATA, `project/` as CLAUDE_PROJECT_DIR) live under `.scratch/conformance/<id>/`, removed on pass and kept on failure with `server.stderr.log`.

Steps (`op` defaults to `call`):

- `call`: `{"call": tool, "args": {...}, "meta": true|false (default: model-facing tools send _meta, hook-facing tools send none), "expect": "ok"|"error", "mention": str|[str], "save": {var: "dotted.path"}, "assert": [{"path": "dotted.path" (omit for the whole result), "eq": v | "contains": v | "decision_block": true}]}`. `"$var"` strings are replaced by saved values. Every ok result is validated against the tool's result schema; every `status` result must have `items` equal to the counts derived from `tree`.
- `stop` / `start` / `restart`: close and start the server process (same data directory).
- `journal`: `load` (reads the journal of `$iid` below the data directory; sets `$n` lines and `$n1` = n+1 as a string), `check_events` (v=2, RFC 3339 UTC ts), `write` with `form` `orig`, `v1_all`, `v1_but_last`, `orig_plus_v3`.
- `fs`: `{"op": "fs", "assert": [{"path": "rel/path", "exists": true|false (default true), "mode": "600"}]}`; paths are relative to the data root (`CLAUDE_PLUGIN_DATA`, or `$TMPDIR/decision-register-data` when it is unset); `$key` is the project key and `$iid` the saved interview id (contract/runtime.md, Journal location). In a scenario `env` value, `$dir` is the scenario directory.
