# python candidate (control)

- **image**: docker.io/library/python:3.12-slim @ sha256:05a994d2344eb364cc186910f4fc8786a38215f39f00d0acc25bff33a08f4177 (image id 78976a69c067)
- **sdk**: mcp (modelcontextprotocol/python-sdk) 2.3.0, official, latest release 2026-10-02; MCPServer API (FastMCP renamed in 2.x)
- **protocol**: 2025-06-18
- **gates**: {"hook_block": true, "container_build_clean": true, "protocol_ok": true}
- **start_ms_median**: 586.7
- **start_ms_p95**: 636.0
- **rss_mb**: 73.5
- **artefact_bytes**: 40734605
- **needs_runtime**: true
- **build_s**: 10.0
- **loc**: 139
- **deps_direct**: 1
- **deps_transitive**: 27
- **type_safety**: Resolved without answer: not constructible without type-checker error (Resolved dataclass requires answer/source/rationale; core.py _apply 'Resolved(...)'); but no type checker was run and dataclasses do not validate at runtime beyond arity, so empty strings are possible; MCP tool args are plain str (kind/source not enforced by MCP schema, checked by hand).
- **test_friction**: Six pytest tests, ~35 lines, tmp_path fixture, no friction; core separated from MCP glue so tests need no SDK.
- **notes**: All container commands used podman explicitly (coordinator instruction); no docker invocation. Build: pip install --target into out/site (deps vendored, not bundled), compiled wheels are cp312-specific; host measurements used a separate host-built site/ (python 3.14.7) via run.sh, container test/build used python 3.12. build_s median of 3 = 10.0 (9.53,10.05,10.07), pip download INCLUDED, image already pulled. Idle RSS and start measured on host py3.14. Dependency counts: 28 dist-info in site (mcp + 27). Live gate FAIL: 2 runs used (~USD 0.011 total); begin_interview/log_item worked via server PID 1051162, but plugin hooks reported "MCP server 'plugin:spike:regspike' not connected" for both UserPromptSubmit and Stop, so check_stop never ran. Likely cause: cwd was the plugin dir so .mcp.json was also loaded as a project server named 'regspike' (tools were mcp__regspike__*), shadowing/deduping the plugin one; unverified, third run would exceed the 2-run cap. Server logic itself is verified offline only. Optional container-wrapped start: initialize reply obtained via podman run -i, not timed (time output lost). Python-3.14-vs-3.12 runtime differs between host measurements and container. CORRECTION: the first two live runs (cwd inside plug dir) were INVALID (project .mcp.json loaded, plugin server not connected). Corrected run 3 with cwd=.scratch/spike2/python/cwd and --plugin-dir ../plug: PASS. Stop blocked twice via check_stop ('1 open: q1', chain cap 2 then {}), all on server PID 1064058 (same as begin_interview/log_item). Run 3 cost USD 0.0068; total live spend about USD 0.018.
