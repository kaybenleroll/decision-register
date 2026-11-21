# Live run 3, 2026-11-19 (checklist `live-run-2-checklist.md`, issue #78)

Second live pass over the plugin, driven by an agent through `tmux` rather than typed by the user. Every result below is a single trial. Raw pane captures, journals and `claude -p` output are in `.scratch/live-run-3/` (private, gitignored, not committed); this file quotes only what the checks need.

- Claude Code 2.1.328, model `claude-haiku-5-5` (`--model haiku`), `--permission-mode default` (the pane footer read "manual mode on"), plugin via `--plugin-dir $HOME/workspace/decision-register/plugin`, run directory `$HOME/workspace/register-live-run` (outside the plugin). The run used the `grill-me` personal skill as installed.
- Builds: `npm run build` in `impl/ts` (rebuilt, 136.5 kB bundle) and `cargo build --release` in `impl/rust` (already up to date, `regspike-rs` dated 11:48) before any session.
- Driver: `tmux -L regrun` (a private socket, so no existing tmux session was touched), 200x50 pane, `send-keys` for input, `capture-pane -p` after each step. A pane capture holds the visible screen only (about 50 lines), so long answers were also read from the session transcript, read-only.
- Spend: roughly USD 0.10 to 0.15 in total on haiku (estimate; `/cost` in the last Session A segment, after `/clear`, read USD 0.0118; the two `claude -p` calls cost USD 0.0047 and USD 0.0029; the rest was not metered separately and the status line shows no USD figure).
- Pre-existing allow rules for the register tools: `grep -n "decision-register\|mcp__plugin"` over `~/.claude/settings.json`, `~/.claude/settings.local.json` and the run directory's `.claude/settings*.json` found none (the latter two do not exist). So the prompts below are the default behaviour.

## Denials during the run (recorded, not worked around)

1. The first launch command was denied by the auto-mode classifier ("Destructive or Unwanted Actions"). It contained `tmux kill-server`, which would have killed the user's own tmux server (`tmux ls` showed one attached session with 11 windows). I dropped that and used a private socket (`-L regrun`); the second launch was allowed.
2. A helper that answered permission prompts automatically in a loop was denied ("Undermining Oversight"). Not run. From then on every prompt was read first and answered by hand, one at a time, as the checklist's check 7 describes.

## Results

| Check | Issue | Result | Evidence |
|---|---|---|---|
| 1 V3 hook `session_id` across run-1 compaction | #25 | PASS | below |
| 2 tool-count line, both implementations | #22 | PASS | 12 tools each |
| 4 pre-existing allow rules | D3 | PASS (none exist) | grep empty |
| 6 V2 launcher served the TS binary | #24 | PASS | below |
| 7 permission prompts, default mode | D3 | PASS with a note on `end_interview` | below |
| 8 `/mcp` tool count in Claude Code | #22 | PASS (12) | below |
| 9 control: no canary before compaction | #36 | PASS (answer NONE) | below |
| 10a V7 Q1 compaction hook injects | #36 | YES (command hook only) | below |
| 10b V7 Q2 skill text retained | #36 | YES (verbatim; harness re-injection) | below |
| 10c status after `/compact` | #26 | PASS | below |
| V3 trial 2 (journal id equals transcript id) | #25 | PASS | below |
| 11 Stop gate in default mode | - | NOT RUN | below |
| 12 V4 `/clear` | #26 | PASS on the server and id legs; one leg not shown | below |
| 13 `end_interview` abandon prompt | - | NOT RUN (declined) | below |
| 14 V2 Rust binary served | #24 | PASS | below |
| 15 V1 failing server | #23 | VISIBLE, not blocking | below |
| 16 V6 SessionEnd calls `end_interview` | #28 | NO (as configured) | below |

### 1. V3 (#25), run-1 journal (no session)

`jq -r 'select(.kind=="hook")|.session_id' archive/iv-20261119T123740Z-757024ab.jsonl | sort | uniq -c` gave `22 baed6038-c0bc-464c-83a4-4022c2cddfbe`. Last hook row before 12:44:01 was `["2026-11-19T12:43:41.391Z","check_stop"]`; first after was `["2026-11-19T12:45:15.351Z","note_prompt"]`. One id, rows on both sides: matches `docs/evidence/verify-V3.md`.

### 2. Tool count, direct from the launcher (#22)

`tools/list` through `plugin/bin/register-launch`, once with `REGSPIKE_IMPL=ts` and once with `rs` (Rust directory on `PATH`): both printed `12 tools: begin_interview log_item resolve park reopen status resume discard end_interview note_prompt skill_started check_stop` (nine model-facing, three hook-facing).

### 6. V2 (#24), TypeScript

Session A (`REGSPIKE_IMPL=ts`), `pgrep -af regspike`: `3613676 node $HOME/workspace/decision-register/plugin/bin/../../impl/ts/dist/regspike.mjs`. No `register-stub.py`. As the checklist notes, `.mcp.json` has no `${REGSPIKE_IMPL:-rs}` expansion; selection is in the launcher, so this checks the outcome, not the variable expansion.

### 14. V2 (#24), Rust

Session B (`REGSPIKE_IMPL` unset, `impl/rust/target/release` first on `PATH`): `pgrep -af 'regspike|register-stub'` gave `3727698 regspike-rs`; `/proc/3727698/exe` resolves to `impl/rust/target/release/regspike-rs`. The model's `status` call answered `server_version: 0.1.0`, `tool_schema: 4`. Both halves of V2 pass.

### 7. Permission prompts (D3), default mode

Prompt order and choice, Session A: `status` (prompt, answered 1 "Yes"), `begin_interview` (prompt, "Yes"), `log_item` four times (prompt each time, "Yes"), `resolve` (prompt, "Yes"), `status` again after `/compact` and again after `/clear` (prompt each time, "Yes"). Session B: `status` prompted. Session D: `status`, `begin_interview` and `log_item` prompted. Hook-facing tools (`note_prompt`, `skill_started`, `check_stop`) never produced a prompt, and the hooks never stalled; the model's `ToolSearch` loading of the deferred register tools produced no prompt either. The interview was not blocked.

The prompt names the tool and server. Verbatim, for `status`:

```
 Tool use
 plugin:decision-register:register — Status Tool: (MCP)
 About the plugin:decision-register:register — Status Tool:
 │ Probe and recovery. Reports server_version, tool_schema, lifecycle state (none, pending, active or complete; ...
 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for plugin:decision-register:register — Status commands in $HOME/workspace/register-live-run
   3. No
```

`status`, `log_item` and `resolve` offered the "don't ask again" option 2 (scoped to the tool and the project); `begin_interview` and `end_interview` offered only "1. Yes / 2. No" (the capture shows no "don't ask again" line for them). I always chose option 1 and never option 2, so no permission rule was written. Matches the README: model-facing tools prompt on first use, hook-facing never. One caveat: in default mode a "this time" answer means each call prompts again, which for a model that logs several items is one prompt per call.

In Session D the model also ran unrelated read-only `Bash` commands (`ls`, `find`, `grep` over the run directory); those prompts offered "switch to auto mode"; I declined both ("No"), which is outside the register question.

### 8. `/mcp` in Claude Code (#22)

`/mcp` listed `plugin:decision-register:register   12 tools`; its detail view showed `Status: connected`, `Config location: Dynamically configured`, `Capabilities: 12 tools`; "View tools" listed `begin_interview log_item resolve park reopen status resume discard end_interview note_prompt skill_started check_stop`. So Claude Code shows all 12, matching check 2. The #22 tool-count acceptance criterion (three hook-facing and nine model-facing tools listed from a directory outside the plugin) is met on both views.

### 9 and 10. V7 (#36), `/compact` (#26), V3 trial 2 (#25)

Setup: a throwaway `--settings` file with a `SessionStart` hook, matcher `compact`, a command hook returning `hookSpecificOutput.additionalContext` "V7 canary: the code word is HELIOTROPE-7731". Not in the plugin; it is a command hook, not an `mcp_tool` hook.

- Control (check 9), asked before compaction with one question resolved and one open: the model answered `NONE`.
- `/compact` ran to completion ("Compacted"; "Skills restored (grill-me)"); `compact_boundary` at 15:13:10Z.
- 10a, asked afterwards: the model reported the line "V7 canary: the code word is HELIOTROPE-7731" in its context (and corrected its earlier `NONE`), adding that it could not tell from inside the context whether it arrived at the compaction or at session start. The word appeared nowhere in the conversation before, and the control said NONE before the compaction, so it arrived at the compaction. V7 Q1: yes for a `SessionStart` `compact` command hook. Not tested: an `mcp_tool` hook, which is what the register would use.
- 10b: the model quoted the heading `## Register mode (decision register)` and the section's first paragraph (two sentences, "A server records the interview ..." and "Run this section BEFORE Process step 1 ..."); both are identical to lines 14 and 16 of `~/.claude/skills/grill-me/SKILL.md`. It also noted that the skill's first heading overall is "Setup - Load Sequential Thinking". V7 Q2: yes, retained. Caveat: Claude Code itself re-injects invoked skills after compaction ("Skills restored (grill-me)" in the pane), so this is harness behaviour, single trial.
- 10c: `status` after the compaction returned `last_event: 34`, `decision_log` for `iv-20261119T150828Z-1d8cf935` (the same interview id as before the compaction), `hook_health` `{note_prompt: 7, skill_started: 1, check_stop: 7}`; the summary line read "items: open 3, resolved 1". Q1 was `Resolved` with the user's answer; Q2 to Q4 open. The `state` field itself was above the visible capture and was not read; the interview was evidently still active (the Stop hook then listed Q2 to Q4 as unresolved). Pass: same interview id, item 1 resolved, open items kept.
- V3 trial 2 (journal `iv-20261119T150828Z-1d8cf935.jsonl`): `jq ... | uniq -c` gave `15 f247273e-b229-4cb3-9b51-0719dcbab8e1`; the newest transcript file under `~/.claude/projects/<run-directory-key>/` is `f247273e-b229-4cb3-9b51-0719dcbab8e1.jsonl`. Hook rows after the compaction at 15:13:10Z: first `["2026-11-19T15:13:24.875Z","note_prompt"]`. One id, equal to the transcript file name: pass. This closes "the transcript id equals the hook id" for this run, and is the second trial after run 1.

### 11. Stop gate in default mode: NOT RUN

On "Stop now, we are done, no more questions." the model chose to call `end_interview` with `abandon: true` itself. The prompt for that call is the one quoted for check 13. I answered "No" so the interview stayed open for the `/clear` check; the turn was interrupted ("Interrupted · What should Claude do instead?"), so no Stop hook fired on that prompt and the gate was not exercised. The checklist's own stop-gate evidence stays run 1. Separately, earlier in the session, the Stop hook did block while Q2 to Q4 were open: the pane showed "Stop hook reported a problem. Items are still unresolved in the decision register. Resolve, park or ask about each before finishing:" followed by `- Q2: ...`, `- Q3: ...`, `- Q4: ...` (twice, after two separate prompts), with the model continuing by asking Q2. That is incidental evidence, not the planned check.

### 12. V4 (#26), `/clear`

- Server process before and after: `3613676 node .../impl/ts/dist/regspike.mjs` in both listings (PID1 == PID2): the server survived `/clear`.
- Old session id (`OLD`): `f247273e-b229-4cb3-9b51-0719dcbab8e1`. After `/clear` and the first prompt (`hello`) the old interview's journal (`iv-20261119T150828Z-1d8cf935.jsonl`, moved to `archive/`) has `{"kind":"end","ts":"2026-11-19T15:15:54.498Z","end_kind":"abandoned","mode":"register","reason":"session change"}`. Q2 to Q4 were open, so `abandoned` with `session change` is the expected row (REQ-26).
- New transcript `ab5d1b2e-a119-476c-b050-2f5c3147dc20.jsonl` is the newest in the project directory; the next interview's `start` row (`iv-20261119T151611Z-10adc7ad`, route `hook`, `skill: grill-me`, 15:16:11Z) carries `session_id` `ab5d1b2e-...`, different from `OLD` and equal to that file name. So `/clear` gives a new `session_id`: REQ-10 and S-26 text need no change on this evidence.
- The leg not shown: the new session's `status` reported `state: none`, `interview_id: null`, not a new interview id. Cause, from the journal: haiku did not take the skill's register-mode steps in the second `/grill-me` (it asked directly instead), so the hook-started interview ended at 15:16:33Z with `end_kind: stood_down`, `reason: "skill did not engage"` (`mode: not_engaged`). The stand-down is the designed behaviour for a skill that does not engage; but the checklist's "status in the new session reports a different interview_id" could not be shown. That is a model-behaviour limit on haiku, not a finding about the server.

### 13. End of Session A: NOT RUN

No interview was open after `/clear` and the stand-down, so the `end_interview abandon, reason "run 2 finished"` step was skipped and `/exit` used. The `end_interview` permission prompt was seen once (at check 11) and declined; it offers "1. Yes / 2. No" only. `status.write_error` was `null` in both statuses shown (10c and after `/clear`).

### 15. V1 (#23), failing server

Two `claude -p --output-format stream-json --verbose "say hi"` runs, haiku, from the run directory:

- (a) `REGSPIKE_TS_BUNDLE=/nonexistent` (binary missing, launcher serves the stub): `mcp_servers` shows `plugin:decision-register:register` `connected`, 12 tools; no hook error anywhere in the output; stderr empty; `is_error: false`; USD 0.00470. Class: hidden (silent), as the checklist expected.
- (b) `REGSPIKE_TS_BUNDLE=.../bad.mjs` (`process.exit(1)`): the server `failed` (0 tools). A `system/notification` row appeared with `key: "stop-hook-error"`, `text: "stop hook reported a problem · ctrl+o to see"`; the turn completed normally (`is_error: false`); USD 0.00286. Class: visible, non-blocking.
- (b) interactive, same server: after `say hi`, the pane showed under the prompt "UserPromptSubmit hook failed / MCP server 'plugin:decision-register:register' not connected", the model answered normally, and after the turn "Stop hook failed / MCP server 'plugin:decision-register:register' not connected" under `plugin:decision-register:register/check_stop`. `/mcp` listed `✘ plugin:decision-register:register`. Class: visible, not blocking (the turn completed and the model replied). Per the issue's drop rule (visible or blocking: tighten the launcher and document install order) this is a user decision.
- Side finding: the next session launched with the good bundle within about two minutes showed the same "not connected" errors. `/mcp` for the server said "Skipping connection (recent failure cached; retries automatically in 15 min, or edit the plugin config to retry now)". Reconnecting from `/mcp` fixed it. So a failed start is cached per project for 15 minutes, which affects install-order advice.

### 16. V6 (#28), SessionEnd hook

Throwaway `--settings` with a `SessionEnd` hook: a command hook appending a timestamp to a marker file, then an `mcp_tool` hook (`server: plugin:decision-register:register`, `tool: end_interview`, `input: {abandon: true, reason: "v6 sessionend probe"}`). Session D (TS, haiku): `/grill-me`, the interview became active with Q1 open and unanswered, then `/exit`.

- Marker file: `2026-11-19T15:23:44Z`. The SessionEnd hook fired.
- The interview journal `iv-20261119T152226Z-c31a6fde.jsonl` has 5 rows (`start`, `engage`, `item`, `hook`, `stop`) and no `end` row; it stayed live in the data directory with its `.pid` file (pid 3768636) and the server process was gone after the exit. No `v6 sessionend probe` reason anywhere.
- Result: V6 = no, as configured. I cannot tell why: the `mcp_tool` hook may not be accepted from `--settings`, or the server may already have been torn down when SessionEnd ran, or the hook did not reach it. Same caveat as the checklist: not testable against the shipped plugin, which has no SessionEnd hook. The leftover journal was not removed (the run does not edit `~/.claude`); it shows up as resumable in the next session's `status`.

## Which issues this can support closing (user's decision)

- #23 (V1): outcome recorded (visible, not blocking); cost per run under USD 0.005. Drop-rule decision needed from the user.
- #24 (V2): server starts with the chosen binary in both cases (ts and rs); variable expansion itself not exercised (no such expansion in `.mcp.json`).
- #25 (V3): second trial passes; run 1 plus run 3 are two trials.
- #26 (V4): `/compact` state survived (same interview id), `/clear` gives a new `session_id`, server process survived, old interview ended `abandoned` with `session change`; the status-in-new-session leg is not shown.
- #28 (V6): recorded "no" for a throwaway `--settings` hook; the cause is not separated.
- #36 (V7): Q1 yes (command hook, not `mcp_tool`), Q2 yes (harness re-injection); both single trial.
- #22: the tool-count line passes (12 = 9 + 3, from the launcher and in `/mcp`).
