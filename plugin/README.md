# decision-register plugin

Hooks, an MCP server entry (`register`) and a launcher for the structured decision register. The plugin
contains no skill text and nothing compiled (REQ-12, REQ-15): the `grill-me` skill stays in the user's
dotfiles, and the server is a separate build.

Status: skeleton (slice P1). The server currently serves seven of the twelve tools (slice I1); the live
hook behaviour (`check_stop` finish gate, hook-started interviews) is **pending** until slices I2 to I4
land in both implementations. Until then the hooks are wired but a Stop check does nothing useful.

## Build the server

The launcher starts one of two implementations, chosen by `REGSPIKE_IMPL`:

| `REGSPIKE_IMPL` | Starts |
|---|---|
| unset or `rs` | `regspike-rs` found on `PATH` |
| `ts` | `node impl/ts/dist/regspike.mjs`, resolved relative to the repository (override with `REGSPIKE_TS_BUNDLE`) |
| `off` (or any other value) | the no-op stub (`plugin/bin/register-stub.py`, Python standard library only) |

Rust: `cd impl/rust && cargo build --release`, then put `impl/rust/target/release` (or a copy of
`regspike-rs`) on `PATH`. TypeScript: `cd impl/ts && npm ci && npm run build` (Node 26). When the chosen
implementation is missing the launcher prints `register unavailable: binary not found` to stderr and serves
the stub, so hooks never block; this is a degraded state, not success. `status` then answers
`{"unavailable":"binary not found"}`.

## INSTALL

Local, from a clone (no marketplace needed):

    claude --plugin-dir /path/to/decision-register/plugin

Run it from a directory outside the plugin. Sessions opened at the repository root do not load
`plugin/.mcp.json` as a project server (REQ-12).

Through the marketplace in this repository (root `.claude-plugin/marketplace.json`):

    /plugin marketplace add /path/to/decision-register
    /plugin install decision-register@decision-register

The enabled-plugin key is `decision-register@decision-register`. Model-facing tools appear as
`mcp__plugin_decision-register_register__<tool>`; the hook-facing tools (`note_prompt`, `skill_started`,
`check_stop`) are called by hooks and need no permission rule.

## DISABLE

- Switch the register off without removing the plugin: start Claude Code with `REGSPIKE_IMPL=off`. The
  launcher serves the stub (hook tools return `{}`, `status` returns `{"unavailable":"disabled by REGSPIKE_IMPL=off"}`),
  prints one line to stderr, and the skill falls back to its plain interview (REQ-15).
- Remove it: `/plugin disable decision-register@decision-register` (or uninstall). Restart the session.

## RECOVER

An interview that did not end cleanly leaves an unfinished journal (REQ-52). Resume or discard it:

- Resume: call the `resume` tool (optionally with `id`), or answer the skill's resume prompt.
- Discard: call the `discard` tool (optionally with `id`). **Pending:** `resume` and `discard` are not served
  until slice I4.
- Where files live: the data root is `$CLAUDE_PLUGIN_DATA` when Claude Code sets it, otherwise
  `$TMPDIR/decision-register-data` (`/tmp/decision-register-data` when `TMPDIR` is unset). Journals are
  `<data root>/<project key>/<interview_id>.jsonl` (project key: first 16 hex digits of the SHA-256 of the
  project path); a live server also holds `<interview_id>.pid`; ended interviews move to `archive/`. Files
  older than 30 days are pruned at server start. Decision logs go to `<project>/.scratch/` if it exists,
  else `<project>/decision-logs/` (REQ-62).
- Clear by hand: stop Claude Code, then delete the `.jsonl` (and any `.pid`) for the interview, or the
  whole `<data root>/<project key>` folder to clear every interview of that project.

## Checks

    python3 -I conformance/check_launcher.py                   # static plugin checks, rs/ts/stub positive checks
    python3 -I conformance/run.py --only 'S-73*' -- true       # launcher scenarios S-73a and S-73b

`REGSPIKE_RS_DIR` names the directory holding `regspike-rs` for the first command when it is not on `PATH`.
