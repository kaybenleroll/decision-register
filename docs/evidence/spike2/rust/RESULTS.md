# rust (rmcp 3.10.0) results

All container commands used `podman` explicitly (not the `docker` shim), per coordinator instruction.

- Image: rust:1-slim, digest sha256:4b68057dc8c103af4fa66b5cd29f21b18afec79fadfcd8fb91e295e939f0690c, rustc 1.100.0. Build: `build.sh` (rootless podman, -v :Z --userns=keep-id, CARGO_HOME in work dir).
- Gates: hook_block PASS (live run 1/2, USD 0.0079, journal shows stop_block events; status pid 1113137 same process), container_build_clean PASS (3 builds, 0 errors, 2 dead-code warnings), protocol_ok PASS (2025-06-18).
- Start median 2.85 ms, p95 3.21 ms (n=20, host, native binary); idle RSS median 5.04 MB. No pathology.
- Artefact: out/regspike 1,869,344 bytes (release, lto, opt-level s, strip); no runtime needed (dynamic glibc only).
- Build: median 35.8 s (35.1/35.8/36.8), fresh target dir, dependency download excluded, container start included.
- LOC non-test source: 242 (core 179 + main 63). Deps: 5 direct, 79 transitive (cargo tree, unique name+version).
- SDK: rmcp 3.10.0, official, released 2026-11-12. Glue: ~45 lines of #[tool_router]/#[tool] macro code; needed explicit `schemars` dep for derive; stdio via `transport-io` feature. Tools returning Result<String,String> work directly.
- Type safety: Resolved without answer is a compile error (enum variant has mandatory fields). Transition legality is runtime.
- Test friction: six tests on pure State, no friction; cargo test in container: 6 passed.
