# Live run 5, 2026-11-25 (owner_skill against the 0.1.3 server, issue #122)

Fourth live pass. Every result is a single trial, one session per path, haiku (`Haiku5.5`) throughout. Raw `claude -p` streams, the interactive transcript, the five interview journals, the decision logs and the pane captures are in `.scratch/live-run-5/` (private, gitignored, not committed); this file quotes only what the checks need.

- Claude Code 2.1.332. Plugin via `--plugin-dir` from the `issue-121-bump-0.1.3` worktree (plugin 0.1.3, `register-rs` 0.1.3 release build on `PATH`; `REGISTER_IMPL` unset, so the Rust build was driven, the TypeScript build was not). Run directory `~/workspace/register-live-run`, outside the plugin. Installed skills `~/.claude/skills/grill-me` and `query-walkthrough` after dotfiles PR #670 (read, not edited).
- Plugin conflict check: the init event lists exactly one `decision-register` plugin (`decision-register@inline`, version 0.1.3) and one register MCP server (`plugin:decision-register:register`, connected). The installed 0.1.1 marketplace copy is enabled in `settings.json` but did not load alongside it, so no conflict arose.
- First `status`, before any skill: `server_version "0.1.3"`, `tool_schema 5`, `version_mismatch null`, `state "none"`, `owner_skill null`, `hook_health.prompt_seen true`.
- Sessions: `claude -p` for scenario A (two calls, `--max-budget-usd 0.25` each) and one probe; one interactive session in `tmux` (private socket `-L regrun5`, auto mode, no `--max-budget-usd` available) for everything else, because each `claude -p` process starts a new server (see Anomalies) and cannot carry an interview across turns.
- Spend: the three `claude -p` calls reported `total_cost_usd` 0.00586 + 0.00937 + 0.01731 = USD 0.0325. The interactive session is not metered (`/cost` shows no figure on this login). Estimate from its transcript token counts (4.06 M cache-read, 0.14 M cache-write, 16 k output tokens), scaled by the ratio between reported cost and token counts on the `-p` calls: about USD 0.16 (uncertain, roughly within a factor of 2). Total about USD 0.2, under the USD 1.00 cap.
- The "user" answers were typed by the driving agent, in the interactive session as plain text. Test-setup prompts that told the model to call a tool directly (scenario F) are marked as such.

## Results

| Path | Issue item | Result | Evidence |
|---|---|---|---|
| A fresh owner | owner claim | PASS | First `status` after `/grill-me`: `state "pending"`, `owner_skill "grill-me"`; role stated "OWNER (grill-me)"; `begin_interview` returned `state "active"`. Observed twice (`-p` and interactive) |
| B guest | `/query-walkthrough` never ends | PASS | `owner_skill "grill-me"`, `state "active"`; role "GUEST (query-walkthrough)"; no `begin_interview`, no `end_interview`; journal `iv-20261125T120821Z-2956df13` has its only `end` row after the later grill-me call |
| C re-invocation after `/compact` | second `/grill-me` is owner and ends | PASS | After `/compact`, `/grill-me` read `owner_skill "grill-me"`, `state "active"`, made no `begin_interview`, resolved Q2 and called `end_interview` once: `end_kind "normal"` |
| D1 resume sets owner | `resume` with `skill` | PASS | `resume {id, skill: "grill-me"}` then `status`: `owner_skill "grill-me"`, `state "active"`; later `end_interview` `end_kind "normal"`. Observed under `-p` and interactive |
| D2 handover | marker `from query-walkthrough (ownership handed over)` | PASS, marker typed by hand | grill-me stated "OWNER (grill-me, handover)", made no claim attempt, ended the interview; `owner_skill` stayed `"query-walkthrough"` |
| E implicit (null owner) claimed by `begin_interview` | claim | PASS, test setup | `log_item` with no `begin_interview` gave `route "implicit"`, `owner_skill null`, `state "active"`; `/grill-me` said "OWNER (grill-me, claim)", `begin_interview` wrote `engage` with `skill "grill-me"`, then it ended the interview |
| F different skill claims a pending interview | claim of `pending` | NOT EXERCISED | The `UserPromptExpansion` hook gives a pending interview the typed skill as owner, so no different-skill claim of a pending interview arose |
| G real `switch` flow of query-walkthrough | handover via the skill's own `switch` reply | NOT EXERCISED | The marker was typed by hand (D2) |
| H No `version_mismatch` | no mismatch | PASS | `version_mismatch null` in every one of the `status` results saved (18 `status` results parsed by script across the `-p` and interactive transcripts, all `tool_schema 5`) |

## Detail

### A, fresh owner

`-p` trial (`A1`, session `67a9e6cc-46f7-4355-9ec6-8e11d8bf1bfd`): ToolSearch, `status` (`state "pending"`, `interview_id iv-20261125T120434Z-e2420c6d`, `owner_skill "grill-me"`, `hook_health {check_stop 0, note_prompt 1, prompt_seen false, skill_started 1}`), `begin_interview {"tool_schema":5,"skill":"grill-me","mode":"register"}` returning `{"existing":true,...,"state":"active"}`, `log_item`, then the turn ended on the question. Interactive trial: same shape, interview `iv-20261125T120821Z-2956df13`.

### B, guest

Same session as C. After `/grill-me` (owner, Q1 open), `/query-walkthrough` read `status` (`owner_skill "grill-me"`, `state "active"`, `items.open 1`), said "Register role: GUEST (query-walkthrough)", adopted Q1 under its server id and walked it. After the answer `stderr` it resolved Q1 and logged its own Q2 ("Close the grill-me interview now, or leave it active?"). It never called `begin_interview` or `end_interview`.

### C, re-invocation after `/compact`

`/compact` ran in the interactive session (the pane showed "Skills restored (query-walkthrough, grill-me)"). The next `/grill-me continue ...` call sequence: `status` (`owner_skill "grill-me"`, `state "active"`, `items {open 1, resolved 1}`), `resolve` Q2 (`source user`), `end_interview` with no arguments. `end_interview` returned `end_kind "normal"`. The journal has exactly one `end` row. The reply did not repeat a "Register role" line after the compaction (the role was taken from `status.owner_skill`); the skill asks for the statement.

### D, resume and handover

- D1: a leftover journal from the `-p` trial (`iv-20261125T120434Z-e2420c6d`, last event 12) made `/grill-me` ask resume or discard (R3). On "resume" it called `resume {"id": ..., "skill": "grill-me"}`. The hook-started pending interview `iv-20261125T120602Z-e7c3ab07` ended `stood_down`, reason `replaced by resume`. Its journal shows two `resume` rows with `skill "grill-me"`, one `resolve`, one `end` `normal`.
- D2: `/query-walkthrough` with two decisions engaged late (`begin_interview` with `skill "query-walkthrough"`, two `log_item`); status then read `owner_skill "query-walkthrough"`, `state "active"`. Then `/grill-me from query-walkthrough (ownership handed over) ...` produced the status above and the role line "OWNER (grill-me, handover)"; grill-me adopted Q1 and Q2 and, after `resolve` Q1 and `park` Q2 (`parked_by user`), called `end_interview` once (journal `iv-20261125T121119Z-6cd33a0a`, `end_kind "normal"`).

### E, implicit claim (test setup)

Prompt: call `log_item` directly, no `begin_interview`. The first attempt left an open item; the Stop gate blocked and the model abandoned that interview (`ended_incomplete_by_model`, `iv-20261125T121245Z-efef92f1`, not a result). The second attempt volunteered a resolved decision (`source user`) so nothing was open; `status` read `interview_id iv-20261125T121317Z-cb5bc412`, `owner_skill null`, `state "active"`; the model then logged an open Q2 itself. `/grill-me` followed the claim path (`begin_interview` returned `{"existing":true,...,"state":"active"}`) and the journal gained `{"kind":"engage","skill":"grill-me","via":"begin_interview"}`. The claim was read off the engage row; no `status` between `begin_interview` and `end_interview` showed `owner_skill "grill-me"`.

## end_interview and engage events

- `end_interview` calls: A (D1 flow) once by grill-me; B zero by the guest; C once by grill-me; D2 once by grill-me after handover; E once by grill-me after the claim. The interviews that were abandoned or stood down (`efef92f1` abandoned by the model, `e7c3ab07` stood down by `resume`) are listed above.
- Engage events: three interviews have `{"kind":"engage","skill":null,"via":"begin_interview"}` (`e2420c6d`, `2956df13`, `6cd33a0a`): `begin_interview` on a hook-started pending interview whose owner already matched writes `engage` with `skill` null. Only the claim of an implicit interview (`cb5bc412`) wrote `"skill":"grill-me"`. `begin_interview` reported `existing:true` in every case.

## Anomalies and skill defects

1. `claude -p` plus `--resume` starts a new server process. The second `-p` call (`A2`) opened with `state "none"`, `owner_skill null`, `resumable [{"interview_id":"iv-20261125T120434Z-e2420c6d","last_event":5}]`, and `log_item`/`resolve` failed with `unfinished journal: call resume or discard`. The model then called `resume` with `skill`, which worked (D1), but `resolve` failed with `user-answer budget spent: budget spent: used 0, allowed 0 for this prompt; a new user prompt is needed` and `hook_health.prompt_seen false`, so the interview could not be finished under `-p`. Multi-turn interviews cannot be tested under `-p` in this setup.
2. `hook_health.prompt_seen` read `false` in every interactive `status` after a skill started, although `note_prompt` counted (1 to 4). Not diagnosed.
3. The skill text (grill-me R5 "Never pass `id` to `log_item`") was not followed by haiku under `-p`: `log_item` calls carried `"id":"q-access-pattern"` and `"id":"d-eviction-policy"` and the server accepted the first. Interactive calls did not pass `id`. Single trial; model behaviour, not a server fault.
4. query-walkthrough as GUEST ended its walk by asking the user whether to close the grill-me interview, instead of "continue into the host skill's next step in the same turn" (R6). It did not call `end_interview`, so the guest rule held; the host-continuation clause was not followed.
5. The Stop gate blocked the guest's closing question as unlogged; the guest then logged its own Q2 as a `question` item.
6. In D2 one prompt answered Q1 and asked to park Q2. `resolve` Q1 succeeded; `park` Q2 (`parked_by user`) failed with `user-answer budget spent: budget spent: used 1, allowed 1 for this prompt; a new user prompt is needed`, because only one item had been presented. The interview could end only after a second prompt. This is the per-prompt budget working as designed (H1), noted because it cost a turn.
7. No status field was missing or malformed in any saved response.

## Not run

- `smoke/run.sh --spend`, the paid evaluation, and any TypeScript-build live session.
