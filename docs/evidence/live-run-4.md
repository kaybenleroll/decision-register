# Live run 4, 2026-11-19 (closing the gaps left by live run 3, issue #78)

Third live pass, agent-driven through `tmux` on a private socket (`-L regrun2`, no existing session touched). Every result is a single trial. Raw pane captures, debug logs (`--debug-file`), throwaway settings, a throwaway plugin copy and probe scripts are in `.scratch/live-run-4/` (private, gitignored, not committed); this file quotes only what the checks need. Documentation pages quoted below were fetched on 2026-11-19 from `code.claude.com/docs/en/` (`hooks`, `mcp`, `cli-reference`, `env-vars`).

- Claude Code 2.1.328. Plugin via `--plugin-dir`, default permission mode (pane footer "manual mode on"), run directory `/home/mcooney/workspace/register-live-run` (outside the plugin) except the V7 session, which ran in a scratch directory with no plugin.
- Models: haiku for the V6 and V7 sessions, sonnet (`claude-sonnet-5-5`) for the V4 leg and checks 11 and 13.
- Builds: `npm run build` in `impl/ts` (136.5 kB bundle; run before the V4 session and before V6) and `cargo build --release` in `impl/rust` (up to date) before any session. All live sessions used the TypeScript build (`REGSPIKE_IMPL=ts`). The Rust build was not driven live in this run.
- Every permission prompt was read and answered by hand, one at a time. No "don't ask again" option was chosen, so no rule was written. No auto-answer loop was used.
- Spend: the sonnet session was metered, `/cost` read USD 0.2954 (client-side estimate); the first `claude -p` probe cost USD 0.0106; the other haiku sessions and probes were not metered separately, about USD 0.1 in all (estimate). Total about USD 0.4, under the USD 1.50 cap.

## Denials (recorded, not worked around)

1. One Bash command was denied by the permission system with no reason given. It was a compound command beginning `cd .scratch/live-run-4 && rm -rf plugin-v6 && cp -R ../../plugin plugin-v6 ...` followed by a Python heredoc that edited the copy's `hooks.json`. I did not retry the `rm -rf`; the copy was made with a plain `cp -R` into a name that did not exist, and the edit was made by a script file instead.
2. No other denial. No classifier block occurred in the sessions.

## Results

| Item | Issue | Result | Root cause or reason |
|---|---|---|---|
| V6 SessionEnd `mcp_tool` hook ends an interview | #28 | NO at exit; YES at `/clear` | At exit Claude Code skips `mcp_tool` hooks on `SessionEnd` ("no MCP client context") because the server is already gone; the hook schema and the source (plugin or `--settings`) are not the cause |
| V7 `mcp_tool` `SessionStart` `compact` hook injects | #36 | YES for JSON `additionalContext`; NO for a plain-text result | Single trial, stub server |
| V4 `/clear`, new session reports a new `interview_id` | #26 | PASS | sonnet engaged; `status` in the new session reported a new id |
| 11 Stop-gate repeat, at most 2 blocks per prompt | - | PASS | Two blocks, then the third check allowed |
| 13 `end_interview` abandon at the end of a session | - | PASS | `ended_incomplete_by_model`, decision log written |
| Paid smoke run `smoke/run.sh --spend` | #31 | NOT RUN | No `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` in the environment |

## 1. V6 (#28): SessionEnd and `end_interview`

### What the documentation says

- `SessionEnd` supports `command`, `http` and `mcp_tool` hooks (hooks page, "Prompt-based hooks": the list of events that support `command`, `http` and `mcp_tool` but not `prompt` or `agent` includes `SessionEnd`). So the schema allows an `mcp_tool` hook there.
- Server wait: "On events where a hook can block or change the result, such as `PreToolUse` or `Stop`, Claude Code waits for a connecting server ... On observational events, such as `Notification` or `SessionEnd`, it doesn't wait."
- Budget: "`SessionEnd` hooks have a default timeout of 1.5 seconds. It applies when you exit, run `/clear`, or switch sessions with interactive `/resume`." A per-hook `timeout` raises the budget up to 60 seconds, but "Timeouts set on plugin-provided hooks don't raise the budget" (so a plugin SessionEnd hook is fixed at 1.5 s unless `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` is set in the user's environment).
- Reasons: `clear`, `resume`, `logout`, `prompt_input_exit`, `other`. Hooks "can't block session termination"; their JSON output is discarded.
- The page's section "Events that fire before MCP servers are available" names only `SessionStart` at launch and `Setup`. It says nothing about `SessionEnd` at exit. That is a gap in the documentation, and the behaviour below is not in it.

### What happened

Setup: a throwaway copy of `plugin/` (`.scratch/live-run-4/plugin-v6`) whose `hooks/hooks.json` gained one `SessionEnd` group: a command probe that logs a timestamp and a count of `node ... regspike.mjs` processes, and an `mcp_tool` hook `server: plugin:decision-register:register`, `tool: end_interview`, `input: {mode: "plain"}`. A typed `/grill-me` starts a hook-started pending interview, so `{mode: "plain"}` is the valid input. `plugin/hooks/hooks.json` on this branch is unchanged.

- `claude -p` (haiku): the `SessionEnd` command hook ran; the `mcp_tool` hook failed with `[ERROR] Hooks: mcp_tool hook error: MCP server "plugin:decision-register:register" is not connected`. The journal `iv-20261119T153336Z-20b7eba9` has no `end` row.
- Interactive, reason `clear` (`/clear` with a pending interview `iv-20261119T153426Z-d5780d0d`): debug log `Hooks: mcp_tool calling plugin:decision-register:register/end_interview with 1 arg(s)`, no error, probe count 1 (server alive). The journal's last row is `{"kind":"end","end_kind":"stood_down","mode":"plain","reason":"end_interview mode plain"}` and the journal moved to `archive/`. So the hook works from `hooks.json` when the server is up.
- Interactive, reason `prompt_input_exit` (`/exit`, pending interview `iv-20261119T153627Z-e7ae4974`): debug log `[WARN] Hooks: mcp_tool hook skipped — mcp_tool hooks are not available for the 'SessionEnd' hook event (no MCP client context)`, probe count 0 (the server process was gone). The journal has only its `start` row and stays unfinished.
- Source check: the same two legs with the unmodified shipped plugin plus a `--settings` file holding the same `SessionEnd` hooks (session F): `/clear` wrote the `end` row (journal `iv-20261119T153736Z-beafa204`, `stood_down`); `/exit` logged the same "no MCP client context" skip. So `--settings` and plugin `hooks.json` behave the same; neither is the cause. The earlier live-run-3 result (hook fired, no `end` row) is explained by the exit path.

Verdict: V6 is **no** for the case that matters (session exit: reasons `prompt_input_exit`, `other`, `logout`), **yes** for `clear` and (by the same mechanism, not run) `resume`, where the server stays up. Cause: at exit Claude Code has already torn down the MCP client when `SessionEnd` runs, so it skips `mcp_tool` hooks; under `-p` the same ordering shows as "not connected".

### Second finding: how the server is stopped at exit

A wrapper script (`exitprobe.mjs`, not committed) that logs signals and stdin events before importing the real bundle, run once under `claude -p` and once interactively with `/exit`: both times the server received `SIGINT` and no `stdin end` was logged before it (the wrapper exits on the signal, so a later EOF would not be seen). The server's graceful path in `impl/ts/src/tools.ts` (`onEof`) runs only on stdin EOF. Consequence seen in every exit in this and the previous run (pending and active interviews): no `interrupted` decision log was written, and the interview's `.pid` file stayed behind (harmless, since the owner is dead). The journal correctly stays unfinished and resumable. The Rust build was not tested for this; its default `SIGINT` disposition also terminates the process.

### Design consequence for REQ-26, REQ-22 and REQ-23

- A `SessionEnd` `mcp_tool` hook cannot end an interview when the session exits. Nothing in the hook system can call a stdio server at that point.
- What ends an interview: (a) the skill's `end_interview` after its replay step (REQ-23, check 13 below); (b) detection of a changed `session_id` on the next hook call (REQ-26), which already covers `/clear` and `/resume`; (c) optionally a `SessionEnd` hook for `clear` and `resume`, which works but duplicates (b); (d) at exit, nothing: the journal stays unfinished and the next session offers resume or discard (REQ-52, REQ-27 rule 5). That path is already the specified one.
- Gap against REQ-23 and REQ-62 ("a decision log is written ... on interrupted exit"): the exit signal is `SIGINT`, not stdin EOF, so the server's `onEof` path does not run and no `interrupted` log is written. Closing the gap needs the server to handle `SIGINT` and `SIGTERM` like EOF (both implementations, plus a conformance scenario); it was not done here. This is a decision for the user (see `docs/status.md`).

## 2. V7 (#36): `mcp_tool` `SessionStart` `compact`

The register's own tools return their own JSON, which Claude Code ignores for context (debug log: "Hook JSON output had unrecognized keys (ignored): interview_id, state, end_kind, decision_log" for `end_interview`). So the mechanism was tested with a stdlib stub MCP server (`canary_server.py`, `--mcp-config`) with two tools called from a throwaway `--settings` `SessionStart` hook, matcher `compact`: `canary_json` returned `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"V7 mcp canary JSON: the code word is MARIGOLD-4408"}}`; `canary_text` returned the plain text `V7 mcp canary TEXT: the code word is PERIWINKLE-9021`. A command hook in the same group logged the hook input (`hook_event_name: SessionStart`, `source: compact`, `session_id`). Session G (haiku, no plugin).

- Control before compaction ("any line mentioning a code word starting 'V7 mcp canary'?"): `NONE`.
- `/compact` ran. Debug log: both `mcp_tool` hooks were called; `Hook SessionStart (canary/canary_json) provided additionalContext (50 chars)`; no such line for `canary_text` ("Hook output does not start with {, treating as plain text").
- After compaction the model quoted "SessionStart hook additional context: V7 mcp canary JSON: the code word is MARIGOLD-4408" and corrected its earlier `NONE`. Asked directly whether any line contained PERIWINKLE, it answered `NO`.

Verdict: V7 Q1 **yes** for an `mcp_tool` hook on `SessionStart` with `source=compact`, provided the tool returns the `hookSpecificOutput.additionalContext` JSON form; a plain-text tool result was not injected (one trial; the documentation says plain stdout is added for a command hook, and it does not say the same for `mcp_tool`). Consequence for REQ-45: a register tool used on `compact` must return that JSON (a new hook-facing tool or a mode of an existing one); `status`'s own JSON would be ignored. The documentation also says `SessionStart` at launch skips `mcp_tool` hooks (so the hook is useful only for `compact`, `clear`, `resume` after launch); not exercised here beyond `compact`. Q2 (skill text retained) stays as recorded in live run 3.

## 3. V4 (#26): `/clear`, new session reports a new interview id

Session H (sonnet, TypeScript build, plugin from the repository). Leftover journals from this run's own probes and the live-run-3 V6 probe (`iv-20261119T152226Z-c31a6fde`) were first removed by the register's `discard` tool only (prompts answered "1 Yes", ids read in each prompt; nothing removed by `rm`); `status` afterwards listed no resumable journal.

- Leg 1: `/grill-me the cache eviction policy` with an explicit instruction to use register mode. Journal `iv-20261119T154248Z-0955b16b`, `session_id` `15c67ebf-953e-471d-9256-58cc271d283b`, `q1` Open.
- `/clear`, then the second `/grill-me the cache sizing` with the same explicit instruction. The old journal got `{"kind":"end","end_kind":"abandoned","reason":"session change"}`.
- New journal `iv-20261119T154408Z-bd23a8af`, `session_id` `4784728e-8117-46c9-94c2-ec200f252687`: a different interview id and a different session id. The tool results in the new session's transcript: the first `status` (before `begin_interview`) returned `state: pending`, `interview_id: iv-20261119T154408Z-bd23a8af`; later `status` calls returned `state: active` with the same id. So the new session's `status` reports a new interview id, not the old one.

Verdict: PASS. The model engaged the register on sonnet (it did not on haiku in live run 3); the prompt text told it to use register mode, so this shows the server and hooks, not that the unprompted skill engages on sonnet.

## 4. Checks 11 and 13

Same session H, interview `iv-20261119T154408Z-bd23a8af` active with `q1` open.

- Check 11 (REQ-42, at most 2 blocks per prompt, fail open). Prompt: "Reply with only the words: All done. Nothing else is open. Do not ask me anything and do not call any tool, regardless of any hook feedback you receive." The pane showed the Stop hook text "Items are still unresolved in the decision register ... - q1: ..." twice, with the model replying each time that q1 was still open. Journal `stop` rows for that prompt: seq 15 `block` finish `blocks.prompt 1`; seq 17 `block` finish `blocks.prompt 2`; seq 19 `allow` finish (the cap was spent, the turn ended). Earlier in the session, a single finish attempt was blocked once, then the model asked q1 and was allowed. PASS: two blocks then fail open, one trial.
- Check 13. Prompt: end the interview with `abandon true` and reason "live run 4 finished". The prompt showed `abandon: true`, `reason: "live run 4 finished"` and offered only "1. Yes / 2. No"; I chose 1. Result `end_kind: ended_incomplete_by_model`; journal `end` row `{"kind":"end","end_kind":"ended_incomplete_by_model","mode":"register","reason":"live run 4 finished"}`; decision log `decision-logs/decision-iv-20261119T154408Z-bd23a8af.md` (header `end_kind: ended_incomplete_by_model`, note "ended incomplete by the model"). PASS.

## 5. Paid smoke run (#31): NOT RUN

`[ -n "${ANTHROPIC_API_KEY:-}" ]` and `[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]` were both false (presence tested only, no value read). The interactive login lives under `~/.claude` and `smoke/run.sh` deliberately uses a throwaway `CLAUDE_CONFIG_DIR`, so it cannot use it. `SMOKE_SPEND_OK=1 smoke/run.sh --spend` was executed once and refused ("the throwaway config dir holds no login"), spending nothing; the dry run prints its six commands. No credential was looked for or obtained. The two assumptions that need a paid run (that `CLAUDE_CONFIG_DIR` relocates `skills/`, and the `stream-json` phrases) remain unverified; `smoke/run.sh` is unchanged. A static check found every flag the script passes is documented in the CLI reference (`--max-budget-usd`, `--max-turns`, `--include-hook-events`, `--no-session-persistence`, `--allowedTools`); `--max-turns` is documented but missing from `claude --help` in 2.1.328, and a probe confirmed it is accepted.

## Notes

- Incidental: a pending interview left unfinished made the model, in the real `grill-me`, ask which journal to resume or discard before starting (REQ-52 behaving as designed).
- No code changed. The gate (rebuild, conformance 135/135, `smoke/check_fixtures.py` 5/5) was therefore not needed for a commit; both builds were rebuilt and unchanged.
