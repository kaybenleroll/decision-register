# Release and CI plan (slice R1, #32)

Status: CI is implemented and runs on every pull request. The release workflow is written and its build half is exercised by
a pull request run; the tag-only half (draft release creation) has **never run** and is untested. No tag has been pushed and
no release exists. Not one of the retirement experiment's five changes (REQ-72). Requirement rules are unchanged; where this
note interprets one, it says so.

## 1. CI (`.github/workflows/ci.yml`)

Runs on `pull_request` and on `push` to `main`. `permissions: contents: read`, no secrets, no `pull_request_target`, every
third-party action pinned by commit SHA with a version comment (`actions/checkout`, `setup-node`, `cache`,
`upload-artifact`, `download-artifact`). Spends nothing: no `claude` session starts; the live smoke suite (`smoke/run.sh`,
paid) stays on demand and is not run here (R1 acceptance).

| Job | Steps |
|---|---|
| `rust` | toolchain from `impl/rust/rust-toolchain.toml` (1.93.0), cargo cache, `cargo fmt --check`, `cargo clippy -D warnings`, `cargo test`, release build, `conformance/run.py` against the binary |
| `typescript` | Node from `impl/ts/.nvmrc` (26), npm cache, `npm ci`, lint, typecheck, `npm test`, build, `conformance/run.py` against `regspike.mjs` |
| `offline-checks` | (needs both builds) `check_contract.py`, `check_lifecycle_events.py`, `run.py --validate`, `selftest.py`, `check_launcher.py` with both builds reached through the launcher, S-73a and S-73b, `smoke/check_fixtures.py` |

Any non-ok step fails the job. Python is the runner's `python3` (the repo pins no Python version; the scripts use the
standard library only). A seeded failure (R1 acceptance: "unit and suite jobs fail on a seeded failure") is a one-line break
in a scenario or unit test on a throwaway branch; not yet done.

## 2. Release (`.github/workflows/release.yml`)

Trigger: a pushed tag `v*`. Procedure (by hand):

1. Make the plugin, crate and package versions equal and bump them together: `plugin/.claude-plugin/plugin.json`,
   `impl/rust/Cargo.toml` (and `Cargo.lock`), `impl/ts/package.json` (and `package-lock.json`). Merge to `main` with CI green.
2. Tag a commit on `main`: `git tag vX.Y.Z <sha> && git push origin vX.Y.Z`.
3. `release.yml` runs `build`, then `publish`, which creates a **draft** release (`gh release create --draft --verify-tag`).
4. Review the draft (three assets, notes), then publish it by hand. Nothing is published automatically.

`build` job, in order: the tag is an ancestor of `origin/main`; `.github/scripts/check_release.py --tag` (below); toolchains;
`cargo build --release --locked`; `npm ci && npm run build`; stage the assets; `sha256sum -c`; record `file`, `ldd` and the
highest GLIBC symbol version (information); the full conformance suite against the **staged** Rust asset and the staged
bundle; `check_release.py` against the same two staged files; upload them as a workflow artefact. `publish` (tag runs only,
`permissions: contents: write`, no checkout) re-verifies the checksums and creates the draft. Release builds use no caches.

Pull requests that touch `release.yml` or `check_release.py` run the `build` job only (same steps, no tag), so everything
except the ancestor check and `publish` is exercised before the first tag.

### Assets (REQ-73, REQ-74)

| Asset | Content | Notes |
|---|---|---|
| `regspike-rs-linux-x86_64` | Rust release binary | Linux x86_64 only (REQ-74). Dynamically linked against glibc; built on `ubuntu-24.04`, so it needs the glibc of that image or newer (measured on the pull request run: GLIBC_2.34, so the practical floor is glibc 2.34; re-read it from each release run log). Install as `regspike-rs` and `chmod 755` (release downloads do not carry the mode). |
| `regspike.mjs` | esbuild single-file bundle, ESM, target `node26`, no runtime dependencies | Needs Node 26 or newer (`engines`); platform independent. |
| `SHA256SUMS` | `sha256sum` lines for the two assets | Verify with `sha256sum -c SHA256SUMS` in the download directory. |

Names carry no version, so `releases/latest/download/<name>` is stable for D1 (the repository is private: downloads need
credentials, for example `gh release download vX.Y.Z -R kaybenleroll/decision-register`). The names `regspike-rs`,
`regspike-ts` and `regspike.mjs` are still working names (G6, #10): rename or confirm them before the first tag; the asset
and workflow strings change with them. Out of scope (REQ-6, REQ-74, drop rule): macOS, Windows, Rust cross-builds, musl or
other static targets, build provenance attestations (private-repository availability not checked).

### Version and `tool_schema` assertion (REQ-77)

`.github/scripts/check_release.py` asserts: tag (without `v`) = `plugin.json` version = `Cargo.toml` version =
`package.json` version; the `tool_schema` the plugin hooks send = `contract/tools.json`; and, for the staged files, that each
reports that `server_version` and `tool_schema` from a real `status` call. Interpretation to confirm: REQ-77 says plugin and
binary are "versioned separately"; the R1 acceptance asks for the versions asserted equal before release. The script
enforces equality at release time only (one tag is one repository state); CI does not call it with `--tag`. Relax it if
independent versions are wanted.

## 3. Where the launcher finds the server, and choosing an implementation

The plugin always starts `plugin/bin/register-launch` (REQ-73, REQ-77). `REGSPIKE_IMPL` is read by the launcher from the
environment of the Claude Code process (the start command has no variable expansion, V2 #24; the live run in
`docs/evidence/live-run-3.md` set it in the shell before `claude`):

| `REGSPIKE_IMPL` | The launcher runs | Where it looks |
|---|---|---|
| unset or `rs` | `regspike-rs` | `PATH` (`command -v regspike-rs`) |
| `ts` | `node <bundle>` | `$REGSPIKE_TS_BUNDLE`, else `<plugin>/../../impl/ts/dist/regspike.mjs` (a repository checkout) |
| `off` or anything else | the no-op stub | `plugin/bin/register-stub.py` |

A missing implementation serves the stub with `register unavailable: binary not found` on stderr; it is never success.

Trying both from a release: download the assets, `install -m 755 regspike-rs-linux-x86_64 ~/.local/bin/regspike-rs`, keep
`regspike.mjs` somewhere stable, then `claude` (Rust) or `REGSPIKE_IMPL=ts REGSPIKE_TS_BUNDLE=/path/to/regspike.mjs claude`
(TypeScript). The choice applies to sessions started afterwards.

**Gap found while writing this (not changed here):** REQ-73 says the TypeScript bundle is installed on the `PATH` as
`regspike-ts`, but the launcher does not look on `PATH` for it. Its default is repository-relative, which only exists for
`--plugin-dir` runs from a checkout; a plugin installed from the marketplace is copied elsewhere by Claude Code (assumed from
the documented marketplace behaviour, not tested: private install is untested, REQ-75), so TypeScript there needs
`REGSPIKE_TS_BUNDLE`. Decision for the user (D1, build-plan section 8, or a launcher change under P1 with an S-73 scenario): add a `PATH` lookup for the
bundle, or have D1 export `REGSPIKE_TS_BUNDLE`. Whether an environment variable set in `settings.json` `env` reaches the MCP
server process is also unverified.

## 4. Not verified (needs a tag or a decision)

- `publish`: `gh release create --draft --verify-tag` with these three files, asset names, the generated notes, and the
  `contents: write` token, all untested until a tag is pushed.
- The tag-on-`main` ancestor check (runs only for tags).
- Installing from the private marketplace on a second clone (R1 acceptance, REQ-75), and any background-credential failure.
- The seeded-failure demonstration; G6 naming.
