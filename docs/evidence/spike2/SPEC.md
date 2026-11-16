# Spike 2: language comparison, shared spec

Throwaway. Not the product. Every candidate implements the SAME small MCP server (stdio) so results are comparable. Work only under .scratch/spike2/<candidate>/. Do not edit ~/.claude, settings, tracked repo files; no git commit/push. Temp files in .scratch/, never /tmp/.

Candidates: ts-node (TypeScript on Node), ts-bun (TypeScript compiled with `bun build --compile`), go, rust, python (control).

## Server "regspike" (stdio MCP, use the language's official MCP SDK; if none, the most used one and say so)

In-memory state plus an append-only JSONL journal at $JOURNAL_DIR/<interview_id>.jsonl (default ./journal). Tools:
- begin_interview(interview_id) -> sets active, resets counters (user_prompts=0, chain=0), opens journal.
- log_item(id, kind: "question"|"subquestion", text, parent?) -> item status Open. Error if id exists or parent unknown.
- resolve(id, answer, source: "user"|"model", rationale) -> Open|Stale -> Resolved. Error if id unknown or already Resolved. Error if source=="user" and no user prompt seen since the item's last model action (track via note_prompt counter).
- park(id, reason, parked_by: "user"|"model") -> Open -> Parked; same user-prompt rule as above for parked_by=="user".
- note_prompt() -> user_prompts += 1, chain = 0. No-op when inactive. (Called by a UserPromptSubmit hook.)
- check_stop(last_assistant_message) -> when inactive: return text `{}`. When active and any item is Open (Parked-by-model counts as open; Parked-by-user does not) and chain < 2: chain += 1 and return text `{"decision":"block","reason":"N open: <ids>"}`. Otherwise `{}`. (Called by a Stop hook.)
- status() -> {pid, active, items: counts per status, user_prompts, chain}
- resume(interview_id) -> rebuilds state by replaying the journal file.
Every state change appends one JSON event line to the journal.

Data model requirement: represent item state in the language's idiomatic way so illegal states are as unrepresentable as the language allows (e.g. a Resolved item always has answer, source, rationale). Report honestly how much the type system helped; do NOT contort the code.

## Offline tests (same six in each language; use the language's standard test runner)
1. resolve of an unknown id is rejected.
2. double resolve is rejected.
3. resolve with source=user and no prompt seen is rejected; accepted after note_prompt.
4. check_stop blocks while an item is Open, passes after resolve; with 3 consecutive stops and an item still open, the third passes (chain cap 2).
5. check_stop when inactive returns `{}`.
6. journal replay via resume reproduces the same status() counts.

## Build (container)
Build in a container with a pinned official image (rootless podman; `docker` is a podman shim). Suggested: node:lts-slim (ts-node; bundle to a single JS file with esbuild, or tsc plus production node_modules; say which), oven/bun:1 (ts-bun), golang:1-bookworm (go, CGO off), rust:1-slim (rust, release build), python:3.12-slim (python; no build, report install of deps). Mount the work dir with `-v <dir>:/work:Z --userns=keep-id` so outputs are not root-owned. Run unit tests inside the container. Extract the artefact to .scratch/spike2/<candidate>/out/. Images must be pulled; that is allowed.
Record exact image tags and digests.

## Measurements (all medians over 20 runs unless stated)
Write a small Python harness (host python3) that: spawns the server command with stdio pipes; sends MCP `initialize`; times spawn to first response line (ms); sends `notifications/initialized`; waits 1 s; reads VmRSS from /proc/<pid>/status; kills it. Report median and p95 start ms, median idle RSS MB. Run the artefact on the host (node v26 and python 3.14 are on the host; record that the runtime versions differ from the container's). Also report: artefact size on disk in bytes (what must ship, excluding the runtime itself), whether a separate runtime must be installed, clean build time in seconds (median of 3, dependency download excluded if you can separate it, else say included), source LOC of non-test source (non-blank), direct and transitive dependency counts.
Optional extra: one start-time sample via `podman run -i --rm <image>` to show container-wrapped start.

## Live check (gate), at most 2 runs, cheapest model
Copy the template plugin in .scratch/spike1/plug (read its layout first). Point its .mcp.json at your artefact. Hooks: UserPromptSubmit mcp_tool -> note_prompt; Stop mcp_tool -> check_stop (server ref `plugin:<plugin>:<server>`). Run `claude -p --plugin-dir <dir> --model haiku --allowedTools mcp__plugin_<plugin>_<server>` with a prompt that makes the model call begin_interview, log_item once, then try to finish. PASS = Stop was blocked once via check_stop and the same server PID handled it. Do not use bypassPermissions. Per-candidate live budget USD 0.15.

## Rubric fields to report (facts, not opinions, where possible)
- SDK: package name, version used, official or not, date of its latest release, MCP protocol version it negotiated (from the initialize reply), anything awkward (lines of glue code, missing features, workarounds).
- Type safety: concrete answer to "can a Resolved item without an answer be written in this code? compile error, runtime error, or possible?" with the one-line code reference.
- Test friction: <=3 lines on writing the six tests.
- Pathology: start time > 1000 ms or idle RSS > 200 MB flag.

## Output
Write .scratch/spike2/<candidate>/RESULTS.md and RESULTS.json (keys: image, sdk, protocol, gates{hook_block, container_build_clean, protocol_ok}, start_ms_median, start_ms_p95, rss_mb, artefact_bytes, needs_runtime, build_s, loc, deps_direct, deps_transitive, type_safety, test_friction, notes). Return <=15 lines to the caller: gate PASS/FAIL, the numbers, one line each on SDK and type safety, spend. Do not paste logs.
Stop condition: one pass. If the SDK or build blocks you after 3 distinct attempts, report FAIL with the reason and stop; do not tune performance.
