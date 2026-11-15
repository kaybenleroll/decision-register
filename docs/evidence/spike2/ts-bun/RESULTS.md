# ts-bun results
All container commands used `podman` explicitly (never `docker`), per coordinator instruction.
- Image: oven/bun:1 @ sha256:1d22548f177f4ff38e0c15baa3c2c46c6c2060ba2d1bc573bb4720b407548f1d (bun 1.4.4); built with `bun build --compile --minify`; 6 tests pass in container (bun:test).
- SDK: @modelcontextprotocol/sdk 1.29.0 (official; latest 1.32.2 released 2026-10-23), zod 3. Negotiated protocol 2025-06-18. No glue awkwardness; ~15 lines tool registration.
- Gates: hook_block PASS (journal shows chain 1,2 from check_stop; same server instance), container build clean PASS, protocol OK PASS.
- Start median 51.1 ms, p95 54.9 ms; idle RSS 42.5 MB (host run, 20 runs). No pathology.
- Artefact 109,467,824 bytes (embeds runtime); no separate runtime needed.
- Build median of 3: 0.31 s (runs 4.97/0.30/0.31, deps pre-installed, download excluded).
- LOC 124 non-blank non-test; deps direct 2, transitive 95 (bun.lock entries total).
- Type safety: Resolved without answer is a compile error (discriminated union). Journal replay uses untyped JSON.parse so that path is unchecked.
- Test friction: zero config, bun:test built in; logic separated from transport so tests are trivial.
- Live spend USD 0.0073 (1 run, haiku).
