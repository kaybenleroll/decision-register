# Spike evidence summary

Four throwaway spikes, run 2026-11-15 on Claude Code 2.1.325 with the cheapest model (haiku) for live runs. All figures below are copied from the RESULTS/LOG files in this directory; nothing is re-derived. Every live result is a single trial unless stated. Spike code, binaries and logs are not committed (they stay in `.scratch/`).

## Spike 1: hooks, MCP and plugin behaviour
Files: `spike1/RESULTS.md`.
- Question: can plugin hooks call tools on a plugin MCP server (same process as the model's calls), and what session identity does the server get?
- Method: python3 stdlib stub MCP server in a plugin dir, loaded with `claude -p --plugin-dir` (no install); logs inspected for PIDs and counters.
- Result: (1) Stop and UserPromptSubmit `mcp_tool` hooks reach the same server PID as model calls; a `{"decision":"block",...}` tool text blocks Stop, and the second Stop payload has `stop_hook_active=true`. (2) Skill-frontmatter hooks (Stop, PreToolUse) fire only after the skill is invoked; skill UserPromptSubmit did not fire in the invoking turn (not tested on a later turn). (3) Plugin-level hooks are session-global. (4) Hook stdin has `session_id`, `transcript_path`, `cwd`, `prompt_id`, etc.; MCP `initialize` params and tool-call `_meta` carry NO session_id; hook-originated calls carry no `_meta`. The server sees the session id only via env `CLAUDE_CODE_SESSION_ID` at spawn, which goes stale after `/clear`. (5) One server process per session (two concurrent `claude -p` gave two PIDs); `/compact` keeps PID and state; `/clear` changes session_id but keeps PID and counter (no respawn). (6) Plugin loads from a directory with no build; qualified server ref `plugin:spike:stub` works; `-p` needed `--allowedTools mcp__plugin_spike_stub`.
- Caveats: haiku only, one trial each; item 5 is PASS "with caveat" per the file. Cost about USD 0.07 total (the file says r4 results may be cumulative, so r4 <= 0.04).
- Producing command: `claude -p --plugin-dir <plug> --model haiku --allowedTools mcp__plugin_spike_stub ...` (stub logs: server*.log, hooks.log).

## Spike 2: five-language comparison
Files: `spike2/SPEC.md` (shared spec), `spike2/<candidate>/RESULTS.{md,json}`.
- Question: which language suits a stdio MCP server (the "regspike" register: begin_interview, log_item, resolve, park, note_prompt, check_stop, status, resume; JSONL journal)?
- Method: same spec and six offline tests per language, built in pinned podman containers (`podman` explicitly), artefact run on the host. Harness (host python3): spawn, MCP `initialize`, time to first response line, wait 1 s after `notifications/initialized`, read VmRSS, kill; 20 runs, median and p95. Build time is the median of 3 builds. Live gate: Stop `mcp_tool` hook -> `check_stop` in a plugin copy, `claude -p --plugin-dir <dir> --model haiku --allowedTools mcp__plugin_<plugin>_<server>`, at most 2 runs, PASS = Stop blocked via check_stop on the same server PID.

| | ts-node | ts-bun | go | rust | python (control) |
|---|---|---|---|---|---|
| Live Stop-hook gate | PASS (run 2) | PASS (1 run) | PASS (run 2) | PASS (live run 1/2) | PASS (run 3) |
| Start ms median / p95 | 82.3 / 85.2 | 51.1 / 54.9 | 3.6 / 4.1 | 2.85 / 3.21 | 586.7 / 636.0 |
| Idle RSS MB (median) | 67.0 | 42.5 | 8.8 | 5.04 | 73.5 |
| Artefact bytes | 752,447 (needs Node) | 109,467,824 (embeds runtime) | 8,126,624 | 1,869,344 | 40,734,605 (needs Python) |
| Build seconds | 0.125 (esbuild only; npm install excluded) | 0.31 (deps pre-installed) | 6.9 (modules excluded) | 35.8 (download excluded) | 10.0 (pip download included) |
| Source LOC (non-blank, non-test) | 118 | 124 | 334 | 242 | 139 |
| Deps direct / transitive | 2 / 95 | 2 / 95 | 1 / 8 | 5 / 79 | 1 / 27 |
| SDK, version, official | @modelcontextprotocol/sdk 1.32.2, yes | @modelcontextprotocol/sdk 1.29.0, yes | modelcontextprotocol/go-sdk v1.8.0, yes | rmcp 3.10.0, yes | mcp 2.3.0, yes |
| Type safety: Resolved without answer | compile error (discriminated union; tsc only, erased at runtime) | compile error; journal replay uses untyped JSON.parse | constructible with empty strings; cannot structurally lack fields | compile error (mandatory enum fields); transition legality is runtime | not constructible without a type-checker error, but no checker was run; empty strings possible |

Other facts from the files:
- Protocol negotiated 2025-06-18 in all five. Offline tests: six per language, all passed in container.
- Images: node:lts-slim (node v26.13.1; host v26.10.0), oven/bun:1 (bun 1.4.4), golang:1-bookworm (go1.27.4), rust:1-slim (rustc 1.100.0), python:3.12-slim. Digests are in each RESULTS file. Runtime versions differ between host and container for node and python (python host 3.14.7 vs container 3.12).
- Transitive counts are not one method: go = indirect entries in go.mod; ts-bun = bun.lock entries; rust = unique name+version from `cargo tree`; python = 28 dist-info minus mcp; ts-node = prod tree.
- Rust glue about 45 lines of macros; Go about 100 lines; ts-bun about 15 lines of tool registration. Go: `Stale` exists in the type but no transition produces it (spec silent).
- Live spend per candidate: ts-node USD 0.0123, ts-bun 0.0073, go about 0.013, rust 0.0079 (run 1 of 2), python about 0.018.

Caveats:
- Single measurement set per language, haiku, host-run binaries; start/RSS are 20-run medians.
- Invalid first live runs: with cwd inside the plugin dir, `claude` also loads the plugin's `.mcp.json` as a project server, shadowing the plugin server so hooks report "not connected". This invalidated ts-node run 1, go run 1 and python runs 1-2 (the python file says the cause was "unverified" until the corrected run 3 from a neutral cwd passed). Valid runs use a neutral cwd with `--plugin-dir ../plug`.
- Build seconds are not comparable (different inclusion of dependency downloads, shown in the table). Artefact bytes are not like for like (runtime embedded or not).
- Container-wrapped start was not measured (go: not measured; python: reply obtained, not timed).
- Producing commands: per-candidate `build.sh` (rootless podman, `-v <dir>:/work:Z --userns=keep-id`), host `harness.py` for start/RSS, `claude -p ...` as above. The RESULTS.json files hold the same numbers.

## Spike 3: change-request test (Rust vs TypeScript on Node)
Files: `spike3/CHANGES.md`, `spike3/acceptance.py`, `spike3/acceptance-{rust,ts-node}.txt`, `spike3/{rust,ts-node}/RESULTS3.md` and `LOG.tsv`.
- Question: how well does each language hold up when an agent changes a working server?
- Method: each agent copied its spike 2 implementation and made three changes in order (C1 stale propagation with `depends_on`/`reopen`; C2 `decision` kind with user-only resolve; C3 journal schema versioning, `v: 2` and `ts`), building and testing only through a wrapper `run.sh` that logs to `LOG.tsv` (cap 12 invocations per change). A separate black-box script (`acceptance.py`) checks scenarios A1-A7 over MCP stdio.
- Result:

| | Rust | TypeScript on Node |
|---|---|---|
| run.sh invocations to green (C1 / C2 / C3) | 1 / 1 / 1 | 1 / 1 / 1 |
| Failing invocations | 0 | 0 |
| Compiler/type-checker catch observed | none | none (tsc passed first time) |
| Unrelated code touched | none required | none required |
| Black-box acceptance (A1-A7) | 7/7 | 7/7 |
| Subagent tokens / tool calls / time (harness task notifications, NOT in the files) | 54.9k / 14 / 134 s | 38.7k / 7 / 60 s |

- Notes: both agents batched all three changes in one patch, so the cycle counts are not independent; ts-node's own file says the C2 and C3 invocations are re-runs of the same green suite. Rust had one tooling slip (arithmetic bug in the first `run.sh`, exited before building, nothing logged). Tests: ts-node 14 (6 old + 8 new); Rust added 3 + 2 + 3 tests. Both builds passed the black-box script 7/7 after the Rust binary was rebuilt (as stated by the coordinator; the txt files record only the 7/7 outcome). Neither agent ran the black-box script itself.
- Caveats: n=1 per language; the tokens/tool calls/time come from harness task notifications, so they are not reproducible from the files; both RESULTS3 files claim no measured compiler catch (Rust names design-level catches that were avoided up front, not observed). Minor output differences: A3 reason is `2 open: q2,q3` (Rust) vs `2 open: q2, q3` (ts-node); A6 v=3 failure names line 5 in both.
- Producing commands: `run.sh` (Rust: `build.sh test` via podman rust:1-slim; ts-node: podman node:lts-slim `build.sh`), `python3 acceptance.py` against each binary (output in `acceptance-*.txt`).

## Spike 4: skill-start detection
Files: `spike4/RESULTS.md`.
- Question: can a hook detect that a skill has started (to start an interview), including typed `/skill`?
- Method: documentation check (hooks.md) plus two live `claude -p` runs (haiku, plugin spike4, neutral cwd, each capped at `--max-budget-usd 0.05`); the stub server log records the substituted values.
- Result: `mcp_tool` `input` supports `${path}` substitution from hook JSON. UserPromptSubmit `${prompt}` works (both a prose request and a raw `/probe hello world`). PreToolUse with matcher `Skill` gives `${tool_input.skill}` = `spike4:probe` (plugin-qualified; `name`/`args` are empty). A typed `/probe` does not trigger PreToolUse; UserPromptExpansion fires with `command_name="spike4:probe"`, `command_args="hello world"`, `prompt="/spike4:probe hello world"` (UserPromptSubmit also fires first). `session_id` substitution works; hook-called tools get no `_meta`.
- Caveats: the per-event variable list and the Skill `tool_input` schema are not documented (the latter was found empirically); single trials; exact spend not captured, estimated under USD 0.10.
- Producing command: `claude -p --plugin-dir <spike4 plugin> --model haiku --max-budget-usd 0.05 ...`.

## Omitted by design
Stub-server and candidate source, binaries, container build logs, node_modules, journals and plugin dirs are not copied. The spike 3 `RESULTS.md/.json` copies are identical to the spike 2 files and are not duplicated.

## Spike 5: Stop-hook message substitution (2026-11-16)

One trial, haiku, Claude Code 2.1.325, USD 0.0074. A Stop-event `mcp_tool` hook with input `{"last_assistant_message":"${last_assistant_message}"}` delivered a 6,869-character final message (quotes, backslashes, newlines, `?`, unicode, JSON fragment) to the server with matching length and sha256; no `_meta` on the call. Short and empty messages were not tested. Notes: `spike5/RESULTS.md`.
