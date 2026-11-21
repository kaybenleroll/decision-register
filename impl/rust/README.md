# register-rs

Rust build of the decision register MCP server. Toolchain pinned by `rust-toolchain.toml` (1.93.0, with rustfmt and clippy).

`cargo` is not on `PATH` on the development machine: call `~/.cargo/bin/cargo`.

Build (from `impl/rust`): `~/.cargo/bin/cargo build --release` (writes `target/release/register-rs`). Rebuild before every suite run; a stale binary gives false failures.
Run: `./target/release/register-rs` speaks MCP over stdio and exits on stdin EOF.
Unit tests (offline): `~/.cargo/bin/cargo test`.
Suite (from the repository root): `python3 conformance/run.py -- ./impl/rust/target/release/register-rs`, or with `python3 -I`.
