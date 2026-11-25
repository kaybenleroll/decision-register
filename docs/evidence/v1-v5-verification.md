# V1 and V5 verification, 2026-11-25 (issues #23 and #27; Refs #128)

Every result is a single trial (one `claude -p` call per case). Claude Code 2.1.332, model haiku, subscription login (no API key), run directory `~/workspace/register-live-run`, plugin 0.1.3 via `--plugin-dir` from the `issue-121-bump-0.1.3` worktree, `register-rs` 0.1.3 release build on `PATH` unless stated. Costs are the `total_cost_usd` reported by each call. Raw streams, stderr, scripts and the Stop-hook stdin log are in `.scratch/v1-v5/` (private, gitignored, not committed). Neither issue carries a comment; the criteria below come from the issue bodies and `docs/build-plan.md` section 7.

## V1 (#23): hook error visibility. Result: PASS for the `claude -p` half, interactive half NOT RUN

Checks: whether a failing `mcp_tool` hook (binary missing, server not connected) shows or hides an error, in `claude -p` and interactive.
Acceptance criteria: outcome recorded (visible, hidden, blocking); cost under about USD 0.05 on the cheapest model. Drop rule: if visible or blocking, tighten the launcher and document install order [user decision, scoped only after this outcome (status 2026-11-24)].

Method: `claude -p --model haiku --max-budget-usd 0.02 --output-format stream-json --verbose --include-hook-events`, prompt "Reply with the single word: ok". Three cases:

| Case | Setup | MCP server status (init event) | Hook events | Reply, exit | Cost USD |
|---|---|---|---|---|---|
| baseline | `register-rs` on `PATH` | connected | `UserPromptSubmit` and `Stop` register hooks `success`, output `{}` | "ok", exit 0 | 0.00449 |
| binary missing | `register-rs` not on `PATH`: the launcher serves the stub | connected (the stub) | register hooks `success`, output `{}` | "ok", exit 0 | 0.00291 |
| server not connected | plugin copy whose `.mcp.json` command is `/nonexistent/register-launch` | failed | `UserPromptSubmit` and `Stop` register hooks `error`, exit_code 1, message `MCP server 'plugin:decision-register:register' not connected` | "ok", exit 0 | 0.00293 |

Outcome:
- Binary missing (launcher stub): hidden. The hooks succeed with `{}`; the only trace is the launcher's one stderr line from the server process, which the `-p` stream does not show.
- Server not connected: visible only as a structured `hook_response` event with `outcome error`, so it appears in `--output-format stream-json --verbose --include-hook-events`. The plain `claude -p` stderr carried only the unrelated "no stdin data" warning. Not blocking: the turn completed, the answer was delivered and the exit code was 0, for both hooks including `Stop`.
- Interactive: NOT RUN. Not metered on this login and not reproducible within the `-p` budget; the interactive display of the failed hook is unknown. REQ-53 interactive error visibility therefore stays unverified.

Spend, V1: USD 0.0103. Single trial per case.

## V5 (#27): empty and short `last_assistant_message`. Result: NOT RUN to a conclusion (empty form not produced)

Checks: what the harness sends in `last_assistant_message` on a tool-only final turn (empty) and for short messages, through `${last_assistant_message}`.
Acceptance criteria: result recorded; S-43c includes the observed form. Drop rule: the gate treats empty as finish attempt or allow, chosen by the result; by the 2026-11-17 decision `contract/classifier.md` already allows empty and the user revisits it only if the harness behaves differently.

Staleness: not stale. The issue is open; the empty observation was parked on 2026-11-18 as settled only by a deterministic harness or a model forced to emit an empty final turn, and nothing in the contract has changed that.

Method: `claude -p --model haiku --max-budget-usd 0.05 --permission-mode bypassPermissions`, the plugin's `Stop` `mcp_tool` hook, plus a `--settings` `Stop` command hook logging the hook's stdin JSON (`last_assistant_message`). Four attempts to force a final turn that is a tool call with no text:

| Attempt | Prompt idea | Final text | Stop stdin `last_assistant_message` | Cost USD |
|---|---|---|---|---|
| 1 | one Bash call, then no text | "hello" | "hello" | 0.00574 |
| 2 | three Bash calls, "zero characters of text" | summary sentence | the summary sentence | 0.00580 |
| 3 | `true`, "respond with an empty message" | sentence | the sentence | 0.00565 |
| 4 | as 3 with `--max-turns 1` | result `""` (turn limit) | no Stop hook fired, so nothing logged | 0.00402 |

Findings: haiku wrote a closing sentence in attempts 1 to 3, so no empty `last_assistant_message` reached any Stop hook. In attempt 4 the result was empty but the turn limit stopped the run without a Stop event. Short messages ("hello", 5 characters) were delivered intact; the register `check_stop` replied `{}` (allow) in every attempt. The `${last_assistant_message}` substitution of an empty message was not observed, so S-43c is not updated.

Recommendation: do not close as stale. Keep #27 parked (no run can finish it with a text-eager model on this login); either leave it open with the 2026-11-18 reason, or, if the user accepts the standing allow rule without an observation, close it as "not testable with haiku; allow rule stands". A deterministic harness (a scripted stub model or hook replay) is the only route to the observation.

Spend, V5: USD 0.0212 (four calls). Total V1 plus V5: USD 0.0315, under the USD 0.30 cap.

## Sonnet retry, 2026-11-25 (#27; Refs #128). Result: NOT OBSERVED (blocked by the per-call budget)

Method as above with `--model sonnet` (claude-sonnet-5-5), `--max-budget-usd 0.05`, subscription login, same plugin dir and `PATH` prefix, run from `~/workspace/register-live-run`; the Stop-command hook logged stdin to a fresh file. Two attempts, then stopped: the per-call cap cannot be met on Sonnet, and a third attempt could not fit the USD 0.25 total.

| Attempt | Prompt | Stop hook event | `last_assistant_message` | `check_stop` | Cost USD |
|---|---|---|---|---|---|
| 1 | "Run the Bash command `echo hi`. After the tool result arrives, end your turn immediately without writing any text at all." | none | none logged (no Stop event) | not invoked | 0.0861 |
| 2 | "Run the Bash command `echo hi`. Then end your turn without writing any text." | none | none logged (no Stop event) | not invoked | 0.0788 |

Both runs ended after turn 1 with `result` subtype `error_max_budget_usd`, `stop_reason` `tool_use`, empty result text and `num_turns` 1: the first assistant turn (the Bash tool call, 74 output tokens, 21054 cache-creation and 11811 cache-read input tokens) cost more than USD 0.05, so the budget check ended the run before the tool executed, a tool result existed or any Stop event fired. The PreToolUse hooks had started but their responses arrived after the result. Cache reuse across the two calls was small (cost fell by about 8 per cent).

Findings: Sonnet on this login costs about USD 0.08 to 0.09 for one turn with this plugin and the user's hooks and settings loaded, so a run that reaches a final turn needs about USD 0.16 or more, above the stated per-call cap (0.05) and, with two attempts spent, above the remaining total (0.25 less 0.165). The empty `last_assistant_message` form was not observed; S-43c is not updated and the haiku-era conclusion stands (a deterministic harness is the only route). Whether Sonnet writes closing text was not tested. Options if the user wants a retry: raise the per-call cap to about 0.25 with a total of about 0.5, or run with a slimmer context (`--bare` style, no user hooks) if the login allows.

Spend, Sonnet retry: USD 0.1649 (0.0861 plus 0.0788). Raw streams in `.scratch/v5b/` (private, not committed).
