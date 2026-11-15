# ts-node results
Containers: podman used explicitly for every container command (no docker shim), per coordinator.
Image: node:lts-slim @ sha256:a5a5adfee3013de54504e0e6aff49993acf7daed072e86be75b8818c0f6ac258 (node v26.13.1). Host node v26.10.0.
SDK: @modelcontextprotocol/sdk 1.32.2, official, latest release 2026-10-23, negotiated protocol 2025-06-18. zod 3 for schemas.
Gates: hook_block PASS (run 2), container build clean PASS (typecheck + 6/6 tests + esbuild), protocol OK.
Start median 82.3 ms, p95 85.2 ms (20 runs), idle RSS 67.0 MB. No pathology.
Artefact: out/regspike.mjs 752,447 B (esbuild single-file bundle, deps inlined); needs Node installed.
Build: 0.125 s median of 3 (esbuild only; npm install excluded).
LOC non-blank non-test: 118 (core 99, server 19); tests 32. Deps: 2 direct, 95 transitive (prod tree).
Type safety: Resolved without answer = compile error (discriminated union, src/core.ts:14); tsc-only, erased at runtime.
Test friction: node:test runs .ts natively; only .ts import extensions and no parameter properties needed.
Live: run 1 invalid (cwd=plug loaded .mcp.json as project server, hook did not fire); run 2 PASS, 2 blocks (cap), same pid 1053978 via status chain=1. Spend USD 0.0123.
