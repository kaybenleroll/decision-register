# decision-register plugin

Hooks, an MCP server entry (`register`) and a launcher for the structured decision register. The plugin
contains no skill text and nothing compiled (REQ-12, REQ-15): the `grill-me` skill stays in the user's
dotfiles, and the server is a separate build.

Status: skeleton (slice P1). The server currently serves seven of the twelve tools (slice I1); the live
hook behaviour (`check_stop` finish gate, hook-started interviews) is **pending** until slices I2 to I4
land in both implementations. Until then the hooks are wired but a Stop check does nothing useful.

## Build the server

The launcher starts one of two implementations, chosen by `REGISTER_IMPL`:

| `REGISTER_IMPL` | Starts |
|---|---|
| unset or `rs` | `register-rs` found on `PATH` |
| `ts` | `node <bundle>`, the first bundle of this order: 1. `$REGISTER_TS_BUNDLE` (non-empty), 2. `impl/ts/dist/register.mjs` relative to the repository, 3. the first `register.mjs` on `PATH` |
| `off` (or any other value) | the no-op stub (`plugin/bin/register-stub.py`, Python standard library only) |

Rust: `cd impl/rust && cargo build --release`, then put `impl/rust/target/release` (or a copy of
`register-rs`) on `PATH`. TypeScript: `cd impl/ts && npm ci && npm run build` (Node 26). When the chosen
implementation is missing the launcher prints `register unavailable: binary not found` to stderr and serves
the stub, so hooks never block; this is a degraded state, not success. `status` then answers
`{"unavailable":"binary not found"}`.

TypeScript bundle order (`REGISTER_IMPL=ts`), first match wins; `node` must be on `PATH` too, else the stub:

1. `REGISTER_TS_BUNDLE`, when set and non-empty. A path that does not exist serves the stub; the launcher does not
   fall through to the other sources, so an explicit override never silently runs something else.
2. `impl/ts/dist/register.mjs` relative to the plugin (`<plugin>/../../impl/ts/dist/register.mjs`), when it exists:
   a repository checkout (`--plugin-dir`) under development.
3. The first `register.mjs` on `PATH` (an installed release, for example from a GitHub release placed on `PATH`).

A plugin installed from a marketplace is copied away from the repository, so it has no repository-relative bundle and
step 3 (or step 1) serves it. Consequence for development runs: while a built `impl/ts/dist/register.mjs` exists in
the checkout, step 2 wins over any installed `register.mjs` on `PATH`; to run the installed bundle from a checkout, set
`REGISTER_TS_BUNDLE` to it. The Rust build has no equivalent order: `register-rs` is found on `PATH` only. Only a
`PATH` that the Claude Code process itself carries counts (a shell without `mise activate`, or a GUI launch, may not
have the install directory on it, which also affects `register-rs`; untested, see `docs/release-plan.md` section 5).

## Choose a build without an environment variable

`bin/register-use rs|ts|status` (repository root, not in the plugin) points `register-rs` in `~/.local/bin` at
the Rust build or at a small `exec node .../impl/ts/dist/register.mjs` wrapper for the TypeScript build, so the
launcher's default (`register-rs` on `PATH`) picks the build. Build the one you want first (the script prints the
command if it is missing). `status` shows the active build, the resolved target, whether `~/.local/bin` is on
`PATH`, and warns if `REGISTER_IMPL` is set (it overrides the link). Overrides: `--prefix DIR` or
`REGISTER_BIN_DIR` for the link directory, `REGISTER_SHARE_DIR` (default `~/.local/share/register`) for the
choices. It refuses to replace a real file or a foreign symlink named `register-rs`. A switch takes effect at the
next session start (the server is started once per session). Offline check: `smoke/check_register_use.sh`.

Ordering note: `register-use` writes `register-rs` (a link, or the TypeScript wrapper) into `~/.local/bin`. The
launcher takes the first `register-rs` on `PATH`, so a mise-installed `register-rs` earlier on `PATH` than
`~/.local/bin` wins over the link; `register-use status` shows what the link resolves to, not what the launcher
picks. The wrapper is only reached through `REGISTER_IMPL` unset or `rs`; with `REGISTER_IMPL=ts` the launcher uses the
bundle order above and ignores the link.

Migration from the old working names (renamed 2026-11-21, issue #96): a `~/.local/bin/regspike-rs` link (or a
TypeScript wrapper pointing at `dist/regspike.mjs`) goes dead after the rename. Rebuild, run `bin/register-use rs`
(or `ts`) again to create `register-rs`, and remove the old link yourself with `rm ~/.local/bin/regspike-rs`
(`register-use` never touches it). `REGSPIKE_IMPL` and `REGSPIKE_TS_BUNDLE` are ignored now: set `REGISTER_IMPL`
and `REGISTER_TS_BUNDLE` instead.

Installing a release with mise (the `github:` backend, a `mise.toml` entry for `register-rs` and a fallback entry for
`register.mjs`, with `asset_pattern`, `bin`, the `SHA256SUMS` checksum, the tag style and the private-repository token) is
described in `docs/release-plan.md` section 4. That route is untested: no release exists yet.

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

- Switch the register off without removing the plugin: start Claude Code with `REGISTER_IMPL=off`. The
  launcher serves the stub (hook tools return `{}`, `status` returns `{"unavailable":"disabled by REGISTER_IMPL=off"}`),
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

`REGISTER_RS_DIR` names the directory holding `register-rs` for the first command when it is not on `PATH`.
