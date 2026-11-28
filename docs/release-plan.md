# Release and CI plan (slice R1, #32)

Status: CI is implemented and runs on every pull request. The release workflow has run on tags: v0.1.0 (tag 860c610,
released 2026-11-21) and v0.1.1 (tag 4cb5f99, released 2026-11-22) and v0.1.2 (tag bbc3da4, released 2026-11-23) each ran `release.yml` to success and are published
releases (none is a draft or pre-release, per `gh release list`). Not one of the retirement experiment's five changes (REQ-72). v0.1.3 (`tool_schema` 5, owner tracking; tracking issue #128, release issue #121) follows: tag `v0.1.3` (commit 53576fb), published 2026-11-26T12:13:53Z; the sections below describe the v0.1.2 install tests unless they say v0.1.3. Requirement rules are unchanged; where this
note interprets one, it says so.

## 1. CI (`.github/workflows/ci.yml`)

Runs on `pull_request` and on `push` to `main`. `permissions: contents: read`, no secrets, no `pull_request_target`, every
third-party action pinned by commit SHA with a version comment (`actions/checkout`, `setup-node`, `cache`,
`upload-artifact`, `download-artifact`). Spends nothing: no `claude` session starts; the live smoke suite (`smoke/run.sh`,
paid) stays on demand and is not run here (R1 acceptance).

| Job | Steps |
|---|---|
| `rust` | toolchain from `impl/rust/rust-toolchain.toml` (1.93.0), cargo cache, `cargo fmt --check`, `cargo clippy -D warnings`, `cargo test`, release build, `conformance/run.py` against the binary |
| `typescript` | Node from `impl/ts/.nvmrc` (26), npm cache, `npm ci`, lint, typecheck, `npm test`, build, `conformance/run.py` against `register.mjs` |
| `offline-checks` | (needs both builds) `check_contract.py`, `check_lifecycle_events.py`, `run.py --validate`, `selftest.py`, `check_launcher.py` with both builds reached through the launcher, S-73a and S-73b, `smoke/check_fixtures.py` |

Any non-ok step fails the job. Python is the runner's `python3` (the repo pins no Python version; the scripts use the
standard library only). A seeded failure (R1 acceptance: "unit and suite jobs fail on a seeded failure", #146) is shown two ways, neither
touching `ci.yml` or its required checks:

- `.github/workflows/seeded-failure-demo.yml` (`workflow_dispatch` only, so it never runs on a pull request or push):
  `rust-unit`, `ts-unit` and `conformance` jobs call `.github/scripts/seeded_failure_demo.py`, which runs the `ci.yml` command
  (`cargo test --locked`, `npm test`, `conformance/run.py` against each build) on a temporary copy of the tree as a control
  (must exit 0), injects one failure into the copy (a failing Rust unit test, a failing node test, a failing scenario
  `S-zz-seeded`), and runs it again (must exit non-zero). A green job means the failure turned the command red. Run it from
  the Actions tab or `gh workflow run seeded-failure-demo.yml --ref <branch>`; the workflow file must be on the branch.
  The script runs locally too (`python3 -I .github/scripts/seeded_failure_demo.py rust|ts`, or `conformance -- <server>`).
- The literal demonstration (the #146 acceptance criteria): on a throwaway branch break one Rust unit test, one TypeScript
  test and one scenario, open a draft PR, confirm the `rust` and `typescript` jobs of `ci.yml` go red, record the run URLs in
  section 5, close the PR unmerged and delete the branch. Not yet done.

## 2. Release (`.github/workflows/release.yml`)

Trigger: a pushed tag `v*`. Tag style: `vMAJOR.MINOR.PATCH`, with an optional pre-release suffix (for example `v0.1.0-rc.1`;
`check_release.py` accepts it, so such a tag also triggers a draft, and the three versions in the files must then carry the same
suffix). Any other `v*` tag fails the `check_release.py --tag` step. The first tag intended is `v0.1.0`. Procedure (by hand):

1. Make the plugin, crate and package versions equal and bump them together: `plugin/.claude-plugin/plugin.json`,
   `impl/rust/Cargo.toml` (and `Cargo.lock`), `impl/ts/package.json` (and `package-lock.json`). Merge to `main` with CI green.
2. Tag a commit on `main`: `git tag vX.Y.Z <sha> && git push origin vX.Y.Z`.
3. `release.yml` runs `build`, then `publish`, which creates a **draft** release (`gh release create --draft --verify-tag`).
4. Review the draft (three assets, notes), then publish it by hand. Nothing is published automatically.

`build` job, in order: the tag is an ancestor of `origin/main`; `.github/scripts/check_release.py --tag` (below); toolchains;
`cargo build --release --locked`; `npm ci && npm run build`; stage the assets; `sha256sum -c`; assert that the staged set is
exactly `SHA256SUMS register-rs-linux-x86_64 register.mjs`; the glibc guard (below); the full conformance suite against the **staged** Rust asset and the staged
bundle; `check_release.py` against the same two staged files; upload them as a workflow artefact. `publish` (tag runs only,
`permissions: contents: write`, no checkout) re-verifies the checksums and creates the draft. Release builds use no caches.

Pull requests that touch `release.yml` or `check_release.py` run the `build` job only (same steps, no tag), so everything
except the ancestor check and `publish` is exercised before the first tag.

### glibc guard

The `build` job runs `file` and `ldd` on the staged Rust binary, then takes the highest `GLIBC_` version from
`objdump -T` and fails when it is above 2.34 (compared with `sort -V`) and when no `GLIBC_` version is found at all (a changed
`objdump` output or a static build would otherwise pass unnoticed). The runner stays `ubuntu-24.04`: the guard, not the runner,
enforces the floor, so a future dependency bump that raises it turns the build red instead of shipping a binary that needs a
newer glibc than the documented 2.34. The 2.34 figure is a measurement of the current binary (local `objdump -T`, 2026-11-21),
not a requirement from REQ-74, which fixes only the platform (Linux x86_64). The same job asserts that the staged directory
holds exactly `SHA256SUMS`, `register-rs-linux-x86_64` and `register.mjs`. Neither check has run on GitHub yet; both were run as
shell, locally, against the real files and against a faked `GLIBC_2.35`, an empty `objdump` output and a missing file (all three
fail as intended).

### Assets (REQ-73, REQ-74)

| Asset | Content | Notes |
|---|---|---|
| `register-rs-linux-x86_64` | Rust release binary | Linux x86_64 only (REQ-74). Dynamically linked against glibc. The floor is set by the `GLIBC_` symbol versions the binary references, not by the runner image (an older runner cannot raise it): measured highest is GLIBC_2.34, so the floor is glibc 2.34. A `build` step enforces it (see "glibc guard"). Install as `register-rs` and `chmod 755` (release downloads do not carry the mode). |
| `register.mjs` | esbuild single-file bundle, ESM, target `node26`, no runtime dependencies | Needs Node 26 or newer (`engines`); platform independent. |
| `SHA256SUMS` | `sha256sum` lines for the two assets | Verify with `sha256sum -c SHA256SUMS` in the download directory. |

Names carry no version, so `releases/latest/download/<name>` is stable for D1 (the repository is public: downloads need no
credentials; for example `gh release download vX.Y.Z -R kaybenleroll/decision-register`). The names `register-rs`,
`register-ts` and `register.mjs` are final (set A, decided 2026-11-21, G6, #10, #96); they are the public interface, so
renaming after the first release breaks pinned installs. Out of scope (REQ-6, REQ-74, drop rule): macOS, Windows, Rust cross-builds, musl or
other static targets, build provenance attestations (not checked; the repository is public).

### Version and `tool_schema` assertion (REQ-77)

`.github/scripts/check_release.py` asserts: tag (without `v`) = `plugin.json` version = `Cargo.toml` version =
`package.json` version; the `tool_schema` the plugin hooks send = `contract/tools.json`; and, for the staged files, that each
reports that `server_version` and `tool_schema` from a real `status` call. Interpretation to confirm: REQ-77 says plugin and
binary are "versioned separately"; the R1 acceptance asks for the versions asserted equal before release. The script
enforces equality at release time only (one tag is one repository state); CI does not call it with `--tag`. Relax it if
independent versions are wanted.

## 3. Where the launcher finds the server, and choosing an implementation

The plugin always starts `plugin/bin/register-launch` (REQ-73, REQ-77). `REGISTER_IMPL` is read by the launcher from the
environment of the Claude Code process (the start command has no variable expansion, V2 #24; the live run in
`docs/evidence/live-run-3.md` set it in the shell before `claude`):

| `REGISTER_IMPL` | The launcher runs | Where it looks |
|---|---|---|
| unset or `rs` | `register-rs` | `PATH` (`command -v register-rs`) |
| `ts` | `node <bundle>` | the first of: `$REGISTER_TS_BUNDLE` (non-empty), `<plugin>/../../impl/ts/dist/register.mjs` (a repository checkout), the first `register.mjs` on `PATH` |
| `off` or anything else | the no-op stub | `plugin/bin/register-stub.py` |

A missing implementation serves the stub with `register unavailable: binary not found` on stderr; it is never success.

Trying both from a release: download the assets, `install -m 755 register-rs-linux-x86_64 ~/.local/bin/register-rs`, keep
`register.mjs` somewhere stable, then `claude` (Rust) or `REGISTER_IMPL=ts REGISTER_TS_BUNDLE=/path/to/register.mjs claude`
(TypeScript; with `register.mjs` in a `PATH` directory and no checkout bundle, `REGISTER_TS_BUNDLE` can be left out). The choice applies to sessions started afterwards.
The binary has no `--version` (it starts the server and blocks on stdin), so verify with the `initialize` handshake.

Order for `REGISTER_IMPL=ts` (REQ-73, decided in #94; implemented in `plugin/bin/register-launch`, checked by
`conformance/check_launcher.py` with a decoy `register.mjs` on `PATH` so a wrong order fails):

1. `$REGISTER_TS_BUNDLE`, if set and non-empty. A missing file serves the stub and does not fall through, so an explicit
   override never silently runs another bundle.
2. `<plugin>/../../impl/ts/dist/register.mjs`, if it exists. A checkout under development beats an installed release.
3. The first `register.mjs` on `PATH` (an installed release). A plugin installed from the marketplace is copied away from
   the repository (assumed from the documented marketplace behaviour; a marketplace install on a second clone is tracked in #32, REQ-75), so
   it has no repository-relative bundle and this step serves it.

Then `node` must be on `PATH`, else the stub. The launcher runs the file as `node <file>`, so it need not be executable.
Whether an environment variable set in `settings.json` `env` reaches the MCP server process is unverified, and so is
whether the Claude Code process carries a `PATH` that includes the directory a release manager (for example `mise`)
installs `register.mjs` into; both are settled by the install test listed in section 5.

## 4. Installing with mise

An alternative to downloading the assets by hand (`mise` github backend). Tested on 2026-11-23 against the published v0.1.2 release,
with an isolated `MISE_DATA_DIR` and no token (the repository is public). Observed: `sha256sum -c SHA256SUMS` passes for
`register-rs-linux-x86_64` and `register.mjs`; the Rust binary and `node register.mjs` both complete `initialize` and
`tools/list` (`serverInfo` `register-rs` 0.1.2 and `register-ts` 0.1.2; 12 tools; no `$defs`); the Rust stanza below, with
version `0.1.2` and the real hash, installs `register-rs` with mode 755 (`bin` keeps the file name), and the `v`-prefixed tag is
accepted when `version` is `0.1.2`; the bundle stanza (`format = "raw"`, `bin = "register.mjs"`) installs, runs under `node`,
and the handshake gives 12 tools; an explicit `checksum` is verified (a wrong hash fails with Expected and Actual sha256
lines); `bin-paths` lists the install directory for the Rust install. Still untested: marketplace or plugin install, `PATH` reach into the Claude Code process, `mise activate`, `GITHUB_TOKEN` use, the clean-container run, and #95's "clean machine" criterion. Tested on v0.1.0 (see the comments on #95): the launcher finds `register-rs` (default) and `register.mjs` (`REGISTER_IMPL=ts`) on `PATH` from a plugin copy with no `impl/` sibling; mise does not read `SHA256SUMS` by name (it uses an explicit `checksum`, else the GitHub API digest).

Rust (`register-rs`), in a `mise.toml`:

```toml
[tools."github:kaybenleroll/decision-register"]
version = "0.1.3"          # pin; checksum needs a pinned version (tested with 0.1.2; v0.1.3 install not yet run)

[tools."github:kaybenleroll/decision-register".platforms]
linux-x64 = { asset_pattern = "register-rs-linux-x86_64", bin = "register-rs", checksum = "sha256:dc771e0c75c38dc5aec829926236c12380a775d9bacc32873f153f5ec2d99c71" }   # v0.1.3 SHA256SUMS
```

- `asset_pattern` picks the one asset of the release by name; `bin = "register-rs"` pins the installed file name (otherwise mise
  derives the name itself and may strip the `-linux-x86_64` suffix).
- `<hash>` is the first column of the `register-rs-linux-x86_64` line of the release's `SHA256SUMS`. Whether mise also reads
  `SHA256SUMS` by name, or GitHub's reported asset digest, is not established, so keep the explicit `checksum`.
- Tag style: tags are `vX.Y.Z`; the entry pins `0.1.3`. mise accepts the leading `v` on the tag when `version` is `0.1.2` (tested on v0.1.2; not re-run for 0.1.3).
- `mise activate` (or shims for executables) must put the install directory on the `PATH` of the Claude Code process;
  untested (section 5).

TypeScript bundle fallback, an alternative entry (a tool key cannot be used twice in one file, so use a separate `mise.toml`,
or a different config file, for it):

```toml
[tools."github:kaybenleroll/decision-register"]
version = "0.1.3"
format = "raw"
bin = "register.mjs"

[tools."github:kaybenleroll/decision-register".platforms]
linux-x64 = { asset_pattern = "register.mjs", checksum = "sha256:8700d0ec948acdb2198a6aee647f0fcad1af8e9fb44b136ab1f51a3a6bc95249" }   # v0.1.3 SHA256SUMS
```

The launcher then finds `register.mjs` on `PATH` (section 3, step 3), with `REGISTER_IMPL=ts`, Node 26 or newer on `PATH`, and no
built `impl/ts/dist/register.mjs` in a checkout (a checkout bundle beats `PATH`). The bundle stanza installs and runs under `node` (tested, v0.1.2). Untested: whether
the install directory reaches `PATH` of the Claude Code process. The launcher finding the file on `PATH` was tested on v0.1.0 (see the comments on #95).

Repository visibility: the repository is public, and no token was needed for the test. Whether `GITHUB_TOKEN` or
`MISE_GITHUB_TOKEN` is used was not tested. Do not put a token in a committed `mise.toml`.

Untested, in short: the checksum source (`SHA256SUMS`), `PATH` placement and `mise activate`, token use, the launcher and
`REGISTER_IMPL` handling, and the clean-container run.

## 5. Not verified (needs a tag or a decision)

- (Resolved: `publish` and the tag-on-`main` ancestor check ran for v0.1.0 and v0.1.1, `release.yml` success, and both
  releases are published.)
- Installing from the marketplace on a second clone (R1 acceptance, REQ-75; "private" no longer applies, the repository is public; #32 and the 2026-11-24 ruling in `docs/status.md` say what evidence closes it), and any background-credential failure.
- The seeded-failure demonstration on a real CI run (#146): local control and seeded runs pass (script above); the
  `seeded-failure-demo.yml` run and the draft-PR run (run URLs and ids) are still to be recorded here.
- That an installed `register.mjs` is on the `PATH` of the Claude Code process (launcher step 3, section 3) and that
  `REGISTER_TS_BUNDLE` in `settings.json` `env` reaches the server; the launcher logic itself is checked offline.
- The mise install route, section 4: run for v0.1.2, not yet for v0.1.3 (isolated `MISE_DATA_DIR`, no token; results in section 4). Remaining:
  the items listed there as untested, and #95's acceptance criterion "verified by installing on a clean machine" (no clean-container run yet). #95 is closed; the clean-container run remains unrun.
  Exact commands, to run once, in a clean container; the entries below are the documented `mise.toml` stanzas themselves, not an
  ad hoc `mise use`:

  ```
  podman run --rm -it docker.io/library/ubuntu:22.04 bash
  apt-get update && apt-get install -y curl ca-certificates      # the base image has neither
  curl https://mise.run | sh && export PATH="$HOME/.local/bin:$PATH"
  mkdir /w && cd /w
  # Rust: write the section 4 Rust stanza to /w/mise.toml (real hash from SHA256SUMS), then
  mise trust && mise install && eval "$(mise activate bash)" && command -v register-rs
  printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}\n' | register-rs
  # Bundle: replace /w/mise.toml with the section 4 bundle stanza (Node 26 on PATH, for example `mise use node@26`), then
  mise install && eval "$(mise activate bash)" && command -v register.mjs
  ```

  Then run the same probe through the launcher, with `REGISTER_IMPL=ts plugin/bin/register-launch`. State how the launcher was
  supplied: a clone of the repository at the tag (note the clone's own `impl/ts/dist/register.mjs` must be absent, or step 2 of
  the order wins), or the marketplace install. Pass criteria: `register-rs` and `register.mjs` land on `PATH`, the probe answers `serverInfo` (`register-rs` or `register-ts`)
  with version `0.1.3` (the version the run pins), and the checksum is verified. Afterwards the live checks of unit #92 on the skikk-thor machine.
- The glibc guard and the staged-asset-set assertion on a GitHub runner (the pull request run of `release.yml` exercises both;
  they were run only as local shell until then).
