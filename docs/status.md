# Status log

Append a dated entry after each completed step. Newest last.

## 2026-11-15

Repo seeded. Next: step 0. Nothing built.

Open items for the user:
- OQ1: control arm.
- Thresholds.
- N=2.
- Licence at public flip.
- Plugin vs frontmatter hooks: decided by step 0.

## 2026-11-15 (direction change)

- [user] Direction change: start from scratch rather than port the Python arm G code; prefer in-memory state; language open (TypeScript is a leaning, other languages to be explored); write thorough requirements before any code. Quotes in docs/requirements-inputs.md section A.
- Repo reset to requirements-first: added docs/requirements-inputs.md, updated CLAUDE.md and the starter prompt, marked ADR 0001 and docs/plan.md as partly superseded.
- Stale open item "Licence at public flip" removed (MIT merged in PR #1).
- Next: requirements interview (/grill-me on the open tensions), then docs/requirements.md.

## 2026-11-15 (requirements interview done; requirements drafted)

- Done: requirements interview (grill-me plus query-walkthrough) with the user; every decision recorded in docs/requirements.md (draft, branch requirements-phase). Verified: spikes 1-4 ran (hooks/MCP/plugin behaviour, five-language comparison, change-request test, skill-start detection); summary and results files under docs/evidence/ (not yet reviewed by the user).
- Decisions that reverse or extend earlier text: base is from scratch (ADR 0001 base superseded); state in memory with hooks calling the live server via mcp_tool; journal for crash resume only; Rust and TypeScript on Node built in parallel as an experiment, one retired later by a rule to be written in the language ADR.
- Not verified: hook error visibility, start-command variable expansion, /clear and /compact in interactive sessions; all spike results are single trials on the cheapest model and Claude Code 2.1.325.
- Blockers: none. Awaiting: /stress-test on docs/requirements.md, then the user's approval, then ADR 0002 (register design, superseding ADR 0001's base), an ADR for the parallel-language experiment (with the retirement rule), and a new build plan. No push or PR yet.
- Questions for the user: retirement-rule values (20% token margin, Rust tie-break); final tool list and schemas; whether wayfinder and write-a-prd are on the skill-name list; product and binary names (regspike-* are working names).
- Coordination note for the research session: the test plan, harness and cost-counter fix remain with it; the requirements add REQ-83 (harness runs the shipped plugin files; zero-call runs are scored).

## 2026-11-16 (stress-test pass 1 done; requirements patched)

- Done: `/stress-test` on docs/requirements.md, one round of three reviewers (51 raw findings, about 27 after hand-dedup, none self-assigned RESTRUCTURE); all P1 findings patched in place; log at the top of the file. New requirements REQ-15, 26, 38, 77 and several clauses changed (finish-attempt classification, session binding, transition table, forgery guard, resume and implicit start, durability).
- Verified: spike 5 (live run) showed `${last_assistant_message}` reaches an `mcp_tool` Stop hook intact (6,869 characters, one trial). Hand-traces for the finish-attempt rule, volunteered decisions, the transition table and journal ownership are recorded in the log entry.
- Not verified: whether compaction changes `session_id`; whether `last_assistant_message` is ever empty; hook error visibility; `/clear` and `/compact` in interactive sessions; start-command variable expansion. Spike results remain single trials.
- Blockers: none. Awaiting: the user's approval of docs/requirements.md, including the [assistant] clauses listed in its section 12. Then ADR 0002, the language ADR with the retirement rule, and the build plan. Nothing committed since 4fa1c2d, nothing pushed, no PR.
- Questions for the user: REQ-15 (where the canonical grill-me skill text lives); confirm REQ-34 (decision kind), REQ-35 (N = 2), REQ-43 (finish-attempt rule); REQ-72 values (20% margin, Rust tie-break); whether wayfinder and write-a-prd are on the skill-name list.

## 2026-11-16 (ADRs and build plan drafted)

- Done: drafted docs/adr/0002-register-design.md and docs/adr/0003-parallel-rust-typescript.md (both Proposed) and docs/build-plan.md (23 slices, 6 proposed milestones M0 to M5, 5 dotfiles issue drafts; nothing filed). Requirements amended after drafting: REQ-72 measures (contradiction removed), REQ-52 and REQ-61 (pid and start-time file instead of a file lock, because Node has no portable flock; journal keyed by CLAUDE_PROJECT_DIR), REQ-80 (data-file scenarios from the start).
- Verified: REQ ids and paths cited in the three new documents were checked by script. Not verified: the finish-attempt classifier in the build plan (section 5.1) is unmeasured.
- Blockers: none. Awaiting the user: approval of docs/requirements.md; REQ-15; the [assistant] clauses in its section 12; the ADR 0002 per-prompt budget; REQ-72 values; review of the milestone list before any issue is filed.
- Commits are local on requirements-phase; nothing pushed, no PR.

## 2026-11-16 (issues and milestones filed)

- Done: on the user's instruction, filed 6 milestones (M0 to M5), 31 issues in kaybenleroll/decision-register (#4 to #34: 7 decision gates G0 to G6 labelled needs-user, 23 slices, tracking issue #34) and 5 dotfiles issues D1 to D5 (kaybenleroll/dotfiles #552 to #556, each referring to #34). `ready` on C1 to C5, X1, V1 to V4; `provisional` on the rest. Record: .scratch/issues-created.tsv (untracked).
- Not done: no code; docs/requirements.md is not yet approved by the user; ADRs 0002 and 0003 remain Proposed. PR #3 open, not merged.
- Awaiting the user: requirements approval and the gate decisions (issues labelled needs-user). Gates G4 and G6 carry no recommendation in the plan.

## 2026-11-16 (gate decisions folded)

- Done: folded the user's gate-walk decisions of 2026-11-16 into docs/requirements.md, ADR 0002, ADR 0003 and docs/build-plan.md, tagged `[user: gate walk 2026-11-16]`: purpose (REQ-1, REQ-2); grill-me skill stays in dotfiles with a register mode and plain fallback (REQ-12, REQ-15, REQ-24 list = `grill-me`, REQ-51); per-prompt budget, one `source: user` answer per presented item, minimum 1, flat 4 fallback (REQ-32); N = 2, `decision` kind and finish classifier confirmed as starting values, classifier names presented items (REQ-34, REQ-35, REQ-43); new REQ-45 (full tree from `status`, open items in every Stop reason) and REQ-46 (classifier replay test); REQ-72 values confirmed as starting values; names parked to the release milestone (REQ-73). Build plan: gates G1 to G5 decided, G6 parked, G0 open; new slices P5 (skill register mode, dotfiles side) and V7 (compaction injection, [open]); P2 folded into P5; D4 and D5 rewritten.
- Not done: requirements and ADRs are not approved (DRAFT / Proposed); approval is the user's after reviewing the diff. The issues filed earlier (#4 to #34, dotfiles #552 to #556) predate these changes and are not updated.
- Not verified: compaction injection (V7), plus the earlier open verify-in-build items.

## 2026-11-16 (stress-test pass 2 done; lifecycle revised)

- Done: `/stress-test` pass 2 on docs/requirements.md after the gate decisions were folded in (three reviewers, 48 raw findings, about 24 after hand-dedup; none self-assigned RESTRUCTURE). All P1 findings patched in place through one lifecycle state table (REQ-27): hook-started interviews that the skill never engages are stood down; the gate allows every turn once an interview is complete; a graceful server exit leaves the journal resumable; the session id comes from the last hook call seen, not from the model. Log entry at the top of the requirements file. Build plan, ADR 0002 and ADR 0003 updated to match; new verification slices V8 to V10.
- Verified: spike 6 (live): `${session_id}` substitutes into UserPromptSubmit and Stop hooks and equals claude's session id (two runs); text returned by a prompt hook is not shown to the model; a failing `mcp_tool` hook shows no error in `--output-format json` (stream-json and interactive untested). Notes: docs/evidence/spike6/. Hand-traces for the new lifecycle rules are in the log entry.
- Not verified: V8 to V10 (hook output for expansion and PreToolUse hooks; skill-name form for a user-level skill; UserPromptSubmit-before-start ordering); REQ-53 interactive error visibility; compaction and session id; the engagement rule's 2-Stop threshold.
- For the user, at approval: REQ-13 ([user]) now has an [assistant] bookkeeping exception; `begin_interview` is refused while an unfinished journal exists; REQ-35 counts children of any status (no 'withdrawn' status exists); the 34 requirements still tagged [assistant] are listed in section 12.
- Issues: #4 to #34 and dotfiles #552 to #556 predate this revision and still need updating; not done yet.
- Nothing merged. PR #3 open.

## 2026-11-16 (requirements approved)

- Done: the user approved docs/requirements.md in their own words after a /query-walkthrough of four items. Decisions: Q1 = hooks keep `last_seen_session_id` and `prompt_seen` while no interview is active (REQ-13 bookkeeping exception accepted); Q2 = mutating calls and `begin_interview` are refused while an unfinished journal exists, until resume or discard (REQ-21, REQ-52); Q3 = a pending interview with no register call is stood down at its second Stop check, threshold of 2 to be revisited after the live smoke runs (REQ-27 rule 3); Q4 = REQ-35 counts all children of any status, no 'withdrawn' status.
- Gates: #4 (approval) closed; #10 (names) still parked.
- Not done: issues #4 to #34 and dotfiles #552 to #556 still predate the revisions.

## 2026-11-16 (slice C1: contract frozen)

- Done: C1 (issue #11) on branch issue-11-c1-tool-schema: `contract/tools.json` (REQ-40 tool list, JSON Schema per input and result, good and bad examples, error-message table), `contract/journal.md` (event kinds, `v: 2`), `contract/runtime.md` (process start, injected environment, `_meta`, hook-style calls, spike-3 script changes for C2), `contract/CHANGES` (bump rule; `tool_schema` 1), `contract/check_contract.py` (stdlib checker).
- Verified: `python3 -I contract/check_contract.py` passes: 12 tools, 34 good and 53 bad examples all behave (35 good after the tool_schema 2 bump); tool_schema was 1 and documented (2 after the walkthrough, 3 after the review fixes); the 9 tool names used by the spike-3 `acceptance.py` are all kept.
- Choices the plan left open (not user decisions; revisit before M0 closes): `log_item` keeps an optional caller-supplied `id` (spike 3 used it; the plan's argument list omits it); `reopen` requires `reason` (spike 3 SPEC); `status` keeps `journal_schema` and the `items` counts object (spike 3 A7); the skill-name argument is called `skill`; clock offset variable is `REGSPIKE_CLOCK_OFFSET_SECONDS`; `end_kind` and journal event names are the assistant's proposal.
- Not done: no suite harness, scenarios or fake server (C2 to C5); the C1 data-file scenario format is C2's.

## 2026-11-16 (slice C1: walkthrough decisions Q1 to Q6)

- Decided by the user after a walkthrough of the C1 contract. Q1 end kinds: `normal` = `end_interview` on an active interview with nothing open; `ended_incomplete_by_model` = `end_interview` with `abandon: true` and a reason while items are open; `interrupted` is decision-log-only (a crash or server exit writes no `end` event), so it is removed from the journal `end` enum and from the `end_interview` result enum; the other five kinds keep the triggers of REQ-23, REQ-26 and the REQ-27 table (listed in contract/journal.md). Q2 journal event names: the 13 names stay frozen; the table check (`contract/check_lifecycle_events.py`) passed, no gap (state-none bookkeeping, server exit, `discard` and pruning are file-level and need no replayable event). Q3 optional caller-supplied `log_item` id kept; a duplicate id is an error row in tools.json. Q4 `reopen` requires `reason`. Q5 `journal_schema` and `status` `items` counts kept; the counts must equal those derived from `tree` (checked in check_contract.py). Q6 the argument name `skill` and `REGSPIKE_CLOCK_OFFSET_SECONDS` kept.
- Contract change: `tool_schema` bumped 1 to 2 (journal end-enum change and result-enum narrowing, per the CHANGES rule); recorded in contract/CHANGES. `python3 -I contract/check_contract.py` exits 0.
- Conflict flagged, not edited (requirements are approved): the REQ-27 active row reads "`end_interview` into `ended` (abandoned if `abandon: true`)", while REQ-23 and the Q1 decision call that end kind `ended_incomplete_by_model`; `abandoned` is the session-change kind. The contract follows REQ-23.

## 2026-11-16 (slice C1: REQ-27 wording fix, lifecycle-event script tracked)

- Post-approval wording fix in docs/requirements.md (the Stress-Test Log holds stress-test passes only, so recorded here): REQ-27 table, `active` row, `end_interview` now reads "ended incomplete by the model" (if `abandon: true` with items open) instead of "abandoned"; `abandoned` stays the session-change-with-items-open kind. Resolves the conflict flagged in the previous entry; matches REQ-23, REQ-26 and REQ-62. No [user] clause edited.
- The lifecycle-event table check moved from `.scratch/` to `contract/check_lifecycle_events.py` (tracked); references in contract/journal.md updated. `python3 -I contract/check_lifecycle_events.py` and `python3 -I contract/check_contract.py` both exit 0.

## 2026-11-16 (slice C2: suite harness and A1 to A7 port)

- Done: C2 (issue #12) on branch issue-11-c1-tool-schema (bundled into PR #40): `conformance/run.py` (stdlib driver: `--` command, `--only` with comma list and globs, `--list`, `--validate`, TAP-like output, non-zero exit on failure or empty selection, per-scenario `data/` and `project/` directories under `.scratch/conformance/`, kept on failure), `conformance/README.md` (scenario file format), `conformance/scenarios/S-A1.json` to `S-A7.json` (the spike-3 scenarios as data files, ported with the five script changes listed in contract/runtime.md). The runner sends a call with or without `_meta` (default: model-facing tools with, hook-facing tools without; per-step `meta` override), validates every ok result against the tool's result schema in `contract/tools.json`, and asserts on every `status` that `items` equals the counts derived from `tree`.
- Data-file decision (build plan C2 asks for it to be recorded in the PR): scenarios are data files from the start, as REQ-80 already says and as the plan recommends (section 5 lists about 90 scenarios). One JSON file per scenario, step language in `conformance/README.md`. No contract change; tool_schema stays 2.
- Verified: `python3 -I conformance/run.py --validate` passes (7 scenarios, every call's arguments checked against `contract/tools.json` input schemas). The driver was also exercised end to end against a throwaway reference stub kept in `.scratch/` (not committed): 7/7 pass, and a mutated stub (reopen marks no dependents Stale) fails S-A1. `contract/check_contract.py` still exits 0.
- Not verified, and why: the build plan's oracle check (A1 to A7 pass against both spike-3 servers, recorded outputs in `docs/evidence/spike3/acceptance-*.txt`) cannot be run: the spike-3 server sources are not in the repository (only `LOG.tsv` and `RESULTS3.md`), and they speak the pre-contract protocol (`JOURNAL_DIR`, `interview_id` argument). The A1 to A7 names, assertions and detail strings match the recorded outputs; the real check is the first conformant server.
- Ambiguities decided here (not user decisions): S-A1 to S-A7 open with a `note_prompt` (session S1) before `begin_interview`, because `begin_interview` binds to `last_seen_session_id` (REQ-27 rule 1); the journal is located by glob of `<id>.jsonl` below the data directory, since the project-key derivation is not in the contract; `resume` is accepted only in state none or pending (tools.json), so the spike's "failed resume on a live server leaves state unchanged" is ported as: a failed resume on a fresh server names the line, leaves state none with no items, and the same server then resumes the repaired journal to the original counts.

## 2026-11-16 (slice C2: journal layout pinned in the contract)

- Done: `contract/runtime.md` gains "Journal location" (REQ-61): data root `CLAUDE_PLUGIN_DATA`, else `<TMPDIR or /tmp>/decision-register-data`; project key = first 16 hex digits of SHA-256 of the UTF-8 bytes of `CLAUDE_PROJECT_DIR` taken verbatim (working directory if unset), so Rust and TypeScript agree; `<dir>/<id>.jsonl`, `<dir>/<id>.pid`, `<dir>/archive/<id>.jsonl` (renamed at end, unfinished journals never moved); directories 0700, files 0600 set at creation; 30-day pruning by modification time. `conformance/run.py` computes the same path instead of globbing `<id>.jsonl`, and gains an `fs` step (existence and mode assertions) and `$dir` in scenario `env` values; scenarios `S-L1` (layout, permissions, archive move) and `S-L2` (temp fallback) added; `conformance/README.md` updated.
- tool_schema: not bumped, stays 2. The rule is run-time layout only: no tool name, parameter, result shape or journal event shape changes (the CHANGES rule); a no-bump entry is recorded in `contract/CHANGES`.
- Choices that are the assistant's (REQ-61 fixes only "keyed by project and interview", user-only, archive, temp fallback): the hash key (not a readable name, to avoid sanitising and collisions), the 16-digit length, verbatim path without normalisation, the folder names `decision-register-data` and `archive`, modes 0700/0600. No conflict with a requirement.
- Verified: `python3 -I contract/check_contract.py` and `python3 -I contract/check_lifecycle_events.py` exit 0; `python3 -I conformance/run.py --validate` passes (9 scenarios); the throwaway stub passes 9/9, and a stub with wrong modes fails S-L1 and S-L2.

## 2026-11-16 (C1 review fixes, phase 1: contract and plan; atomic commits on PR #40)

- Done, after three independent reviews (contract, tests, coverage; detail in `.scratch/review-*.md`) and the user's approval of the fix set. `tool_schema` bumped 2 to 3 (entries in `contract/CHANGES`); `journal_schema` stays 2. Contract: begin_interview engages a pending interview (existence required, state active|complete); domain failures are `isError` results, never JSON-RPC errors; journal flushed on stdin EOF too (user decision); state-by-tool matrix and numbered error precedence in `tools.json` (every cell of the REQ-27 table, a failing call changes nothing); error rows added for end_interview (none, pending, mode plain, empty abandon), unknown id, resume and discard (no journal, unknown id, live owner, several, wrong state), invalid arguments, subquestion parent, REQ-34 via log_item, REQ-32 budgets; decision log, interview and item id formats, pid file and stale-claim sequence, pending-interview ends, status.resumable array, status.state none after end, resume.state const active, item oneOf and tree order, journal hook/prompt/stop counting and replay failure modes, `_meta` test, unwritable data directory, single-line patterns rejecting a trailing newline. Checkers: `check_contract.py` now also reads the errors table and state matrix and expects tool_schema 3; `check_lifecycle_events.py` covers the new pending ends and requires that only `abandoned` differs between the journal and tool end enums. Build plan: stale text corrected, scenario ownership fixed (I1 hook-free), 14 scenarios added (S-13b, S-20d, S-22d, S-23g, S-23h, S-30b, S-38c, S-40d, S-52h, S-52i, S-52j, S-60c, S-61e, S-62c; plus per-file S-A rows and S-L1/S-L2 rows), S-31b and S-33b assigned. Counts corrected above (#4 to #34; 35 good examples at tool_schema 2).
- Not done (phase 2, another agent): `conformance/run.py`, the scenario files (S-A1..S-A7 and S-L1/S-L2 still pass `tool_schema: 2` to `begin_interview` and need updating to 3 and the new result shapes), checker strengthening beyond the errors table.
- Proposed, for the user to confirm (the requirements are silent; each is the simplest consistent option):
  1. begin_interview result `state` is `active` or `complete` (never `pending`); an unlisted skill name is accepted and recorded.
  2. Argument-schema violations are `isError` results ("invalid arguments: <parameter>"); JSON-RPC errors only for unparseable JSON, unknown method or unknown tool.
  3. end_interview: `abandon` is const true and requires `reason` (and the reverse); `mode: plain` excludes both; `abandon: true` with nothing open is an error; end_interview in `pending` without `mode: plain` is an error; in `none` an error.
  4. resolve, park and reopen from `none` or `pending` always error "no such item" and never start or engage (REQ-27 lists them as engaging calls; only a successful call can engage).
  5. discard is accepted in `none` and `pending` only (a pending interview stays pending); resume and discard on a live-owned journal error "journal is live-owned", distinct from "journal already claimed".
  6. Error precedence (tools.json `error_precedence`): protocol, hook `_meta`, argument validation, schema mismatch, unfinished journal, wrong state, item lookup, transition, kind rules, user-prompt rules, open items.
  7. Interview id `iv-<UTC yyyymmddThhmmssZ>-<8 hex>`; server item ids `Q<n>`, next unused.
  8. Pid file is one-line JSON {pid, start, interview_id}; start time from /proc/<pid>/stat field 22, exact equality; stale replace is rename aside, verify, O_EXCL create; orphan and `.stale-*` files pruned.
  9. A pending interview's every end (session change, server exit, replaced by a successful resume, second Stop check) writes `end` stood_down mode `not_engaged` and archives the journal; mode `plain` only from end_interview mode plain.
  10. status gains `decision_log` and `write_error`; end_interview gains `decision_log`; status.state is `none` after an end; `ended` stays only as the end_interview result state.
  11. Decision log: `<project>/.scratch/` else `decision-logs/` (0700, `.gitignore` with `*`), file `decision-<interview_id>.md` mode 0600 via temp file and rename, header lines `journal_position`, `mode`, `end_kind` and others.
  12. Item shape: Open all-null; Stale keeps answer, source and rationale; Parked clears them; Resolved clears a park; tree order Open, Stale, Parked, Resolved, creation order within.
  13. Journal: one `hook` event per hook call while an interview exists; `stop.blocks` is {prompt, finish, unlogged}; `resume.from_seq` equals the result `last_event` and the resume event follows it; a torn final line is ignored, other bad lines fail replay naming the line; v1 `seq` is the line number.
  14. skill_started on a tool_schema mismatch still starts the pending interview; version_mismatch is set by a mismatch and cleared by a later match; a `mismatch` event is journalled only when an interview exists.
  15. A `_meta` without the key `claudecode/toolUseId` is hook-style; check_stop always returns JSON text.
  16. An unwritable data directory fails open: calls succeed, `status.write_error` is set, writes retry at the next flush.
  17. Single-line patterns also reject VT, FF, NEL, U+2028 and U+2029.
  18. resume always reports state `active`; a journal whose last event was `complete` is not restored as complete.
- Conflicts with requirements: none found. REQ-60 says "at least" for flush points, so the EOF flush is compatible. REQ-27's engaging-call list is narrowed in effect by choice 4 (flagged here, REQ text not edited).
- Verified: `python3 -I contract/check_contract.py` and `python3 -I contract/check_lifecycle_events.py` exit 0; `tools.json` parses and round-trips; 27 errors rows (11 before), 39 good and 83 bad examples (35 and 53 before) by script.

## 2026-11-16 (C1/C2 review fixes, phase 2: conformance runner, scenarios and checkers; atomic commits on PR #40)

- Done (detail in `.scratch/review-tests.md` and `.scratch/review-coverage.md`; `tool_schema` stays 3, `journal_schema` stays 2, no contract change except the no-bump `contract/journal.schema.json`):
  - `conformance/run.py`: `--validate` whitelists keys per step and per assert, requires exactly one operator per assert (`eq`, `ne`, `contains`, `not_contains`, `matches`, `len`, `ge`, `absent`, `decision_block`), checks the non-variable arguments of steps that use `$variables`; `decision_block: false` now asserts no decision; `expect: error` requires an `isError` result (`protocol_error` is a separate expectation); a failed initialize or start cleans up and the server's process group is killed; a server that does not exit within 5 s of stdin EOF fails the scenario; an empty `CLAUDE_PLUGIN_DATA` counts as unset; explicit cwd apart from the project dir; UTF-8; a unique scratch directory per invocation; non-JSON stdout fails; the TAP detail lists observed values; `CLAUDE_CODE_SESSION_ID` and the clock offset are unset unless a scenario sets them; journal ops (`load` active or archive, `assert_events`, `prefix`, `ended`), fs content checks, `tools_list`. The README documents all of it.
  - `contract/journal.schema.json` (machine-readable journal.md: envelope and 13 kinds, 17 good and 27 bad examples); `check_events` validates every journal line, gapless `seq` and a `start` first event; `check_contract.py` ties the file to the journal.md event table (field names, enum values, envelope rows) and to tools.json.
  - Scenarios (14, was 9): S-A1..S-A7, S-L1, S-L2 updated to tool_schema 3 and the new shapes and strengthened (item text in the stop reason, parked fields, tree order, stored answers, user-resolve rejection without a prompt and after the budget, exact line number for `v: 3`, check_stop before stop plus the stdin EOF flush, v1/mixed/v2 replay to an identical tree and `last_event`, archive content as a prefix of the pre-end journal plus `end`, pid file content, verbatim project path); new S-71a (initialize and `tools/list` equal tools.json), S-A8 (hook start then `begin_interview` engages), S-A9 (implicit start), S-L2b (empty `CLAUDE_PLUGIN_DATA`), S-L3 (empty `CLAUDE_PROJECT_DIR`). S-A4, S-A7, S-L1, S-L2, S-L2b, S-L3 and S-A9 start without `note_prompt`; sessions differ across scenarios. Ownership in the build plan: S-71a in I2; S-A8 and S-A9 in I3; S-L2b and S-L3 in I4.
  - `check_lifecycle_events.py` derives the REQ-27 rows and clauses, rules, the journal.md "Written when" cells and End kinds table and the runtime.md ends table from the documents; the event mapping is pinned by text key, so any change to a reviewed text, a new row or clause, or an unmapped event fails. It also derives that the engaging calls equal tools.json `engages_pending` and the state rows equal the state enum. `check_contract.py` now pins the 27 error rows and their matched text, `mutating`/`engages_pending`/audience, enums, example floors, `status` required keys, `journal_schema`, and tool_schema across tools.json, runtime.md and CHANGES.
- Verified, counts by script: `check_contract.py`, `check_lifecycle_events.py`, `run.py --validate` (14 scenarios, 0 errors) exit 0; the throwaway stub (`.scratch/stub.py`, updated to tool_schema 3, not committed) passes 14/14. The reviewer's corruption harness (`.scratch/mutate.py`): 49 of 49 detected (29 of 49 went undetected before). Mutation regression (`.scratch/mutants3.py`, 128 mutants of the stub, ports of the earlier ~70 plus new): 124 killed, 4 survive, all with a planned scenario: `A3_text_not_truncated` and `A3_ten_item_cap_missing` (S-45c needs 12 items and a 130-character text), `U_note_prompt_not_clearing` (S-13b), `U_complete_left_by_status` (S-23g). The mutants and the stub are not committed.
- Not changed: no contract conflict found; no requirement or [user] decision touched. Choice recorded: the `resume` event's `session_id` is nullable in `journal.schema.json` (journal.md does not say; the start event allows null for the same fallback).
