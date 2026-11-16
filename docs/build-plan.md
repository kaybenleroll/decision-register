# Build plan: structured decision register

Status: `docs/requirements.md` APPROVED by the user 2026-11-16 (gate G0, issue #4, closed); this plan is still a draft for the user's review. (Issues for the earlier draft were filed; slices P5 and V7 to V10, the lifecycle revision of 2026-11-16 (REQ-27) and the changes below are not yet reflected in them.) Gate G0 no longer blocks source code; the slice order below does (the suite goes first, section 1).
Sources: `docs/requirements.md` (REQ ids), ADR 0002, ADR 0003, `docs/evidence/spikes.md`, `docs/plan.md` (kept: the cap constants and the step-0 style live checks; dropped: the Python port, the paid evaluation, harness work, all owned elsewhere, REQ-6).
Tags: [user-decision] marks a step that cannot start until the user decides.

## 1. Rules that shape the plan

- One slice = one PR = one nameable capability (user's PR-unit rule). A slice that outgrows about 800 changed lines splits.
- The suite goes first and is red against every server until the slice that makes it green. Each implementation PR states the scenario ids it turns green; no scenario is edited in the same PR that makes it pass, except to fix a suite bug (flagged in the PR).
- Rust (`impl/rust/`) and TypeScript (`impl/ts/`) are separate PRs per slice, same scenario set (REQ-71). Build alternation: slice I1 Rust first, I2 TypeScript first, I3 Rust, I4 TypeScript, I5 Rust; the second language starts from the first's merged design. This is build order only. The retirement experiment (ADR 0003 section 4, REQ-72) counts the first five change requests made after M2, with its own order Rust, TypeScript, Rust, TypeScript, Rust; slices I1 to I5 are not those changes. One slice per submission, so conformance failures on first submission are countable (ADR 0003 section 6 recommendation; [user-decision] on that definition, settled at the gate walk).
- Each implementation PR appends its build row to `docs/evidence/language-experiment.md` at the time (tokens, first-submission suite failures, date), never reconstructed (REQ-72). Build rows are kept in their own table and are not the retirement experiment's change rows.
- Spend: the paid evaluation is out (REQ-6). Only the smoke suite spends (slice P4, about USD 0.25 per run); anything beyond about USD 1 needs the user (REQ-3).
- Layout proposal: `plugin/` (REQ-12), `.claude-plugin/marketplace.json` at root, `conformance/`, `contract/`, `impl/rust/`, `impl/ts/`, `smoke/`, `docs/evidence/`.

## 2. Gates needing the user

| Gate | Decision | Outcome | Blocks |
|---|---|---|---|
| G0 | Approve `docs/requirements.md` and ADRs 0002 and 0003 (all remaining [assistant] clauses in requirements section 12) | Decided: requirements approved by the user 2026-11-16 (issue #4 closed); ADRs 0002 and 0003 stay Proposed until their own review | everything |
| G1 | ADR 0002 5.2 per-prompt authorisation budget | Decided [user: gate walk 2026-11-16]: one `source: user` answer per distinct item presented since the previous prompt, minimum 1; mechanics [assistant]: budget max(1, p), flat 4 only when the classifier returns `unknown`, volunteered `log_item` answers outside the budget (4 per prompt) | was S-32b..d; slice I2 |
| G2 | REQ-15 skill home | Decided [user: gate walk 2026-11-16]: skill stays in dotfiles with a register mode and plain fallback; plugin has no skill text | was P1 skill content, D4; now slice P5 |
| G3 | REQ-72 and ADR 0003 values | Decided [user: gate walk 2026-11-16]: 20% margin, Rust tie-break within 20%, four measures confirmed as starting values; the user decides either way | was start of I1 |
| G4 | REQ-24 list contents | Decided [user: gate walk 2026-11-16]: exactly `grill-me`; revisit in P5 | was S-20c; P1 |
| G5 | N = 2 (REQ-35), `decision` kind (REQ-34), finish classifier (REQ-43) | Decided [user: gate walk 2026-11-16]: confirmed as starting values; classifier also names presented items; replay test REQ-46 before it ships | was C2 batch |
| G6 | Product and binary names (working names `regspike-rs`, `regspike-ts`, REQ-73) | Parked [user: gate walk 2026-11-16]: decided at the release milestone (R1) | R1 release naming |

## 3. Milestones (proposed; not to be filed until reviewed)

| Milestone | Slices | Exit |
|---|---|---|
| M0 Contract and suite | C1 to C5, X1 | Suite runs; every scenario red against the stub; schema file frozen at the current `tool_schema` (3 after the C1 review fixes; see `contract/CHANGES`) |
| M1 Core register | I1R, I1T, I2T, I2R | Data model, transitions, status, hook-facing tools and finish gate green in both languages |
| M2 Lifecycle and durability | I3R, I3T, I4T, I4R, I5R, I5T | Full suite green in both; REQ-90 measured |
| M3 Plugin and verification | P1, P5 (dotfiles side), V1 to V10 (can run once P1 exists, in parallel with M1/M2; V7 is an upgrade, not a dependency) | Plugin loads from a local clone; open items of requirements section 12 each have a recorded outcome |
| M4 Smoke and release | P2, P3, P4, R1 | Smoke suite under USD 0.25; GitHub release by tag with both implementations; private marketplace install tested |
| M5 Experiment close-out | E1 (after the first five change requests made after M2, and after R1; [user-decision]) | ADR 0003 retirement procedure executed |

Dotfiles issues (section 8) are a separate track, filed on the user's instruction (D1 to D5 filed 2026-11-16).

## 4. Conformance suite slices (M0)

Stack: Python 3 stdlib only (REQ-80), starting from `docs/evidence/spike3/acceptance.py` (A1 to A7 and its `Server` stdio driver).

### C1 Tool list, schemas, result shapes
- Goal: freeze the contract all three artefacts (suite, both servers, plugin hooks) share.
- REQ: REQ-40, REQ-77, REQ-31, REQ-60, REQ-33, REQ-45, REQ-27, REQ-41.
- Deliverables: `contract/tools.json` (JSON Schema per tool input and result), `contract/journal.md` (event kinds, `v: 2`), `contract/CHANGES` rule that any change bumps `tool_schema`.
- Tool list (REQ-40, final): model-facing `begin_interview`, `log_item`, `resolve`, `park`, `reopen`, `status`, `resume`, `discard`, `end_interview`; hook-facing `note_prompt`, `skill_started`, `check_stop`. `log_item` args: `kind`, `text`, `parent?`, `depends_on?`, optional volunteered `answer`, `source`, `rationale`. `park`: `id`, `reason`, `parked_by`. `begin_interview`: `tool_schema`, skill name, `mode` (value `register`); it takes no `session_id` (the interview binds to `last_seen_session_id`, REQ-27). `skill_started`: `tool_schema`, `session_id`, skill name. `note_prompt`: `session_id`, `prompt`. `check_stop`: `session_id`, `last_assistant_message`. `end_interview`: `abandon?`, `reason?`, `mode?` (value `plain` stands a pending interview down). `resume` and `discard`: `id?`. Hook-facing results: `note_prompt` and `skill_started` return empty text or exactly `{}` and never block or deny (REQ-41); `check_stop` returns `{}` or a block decision. `status` returns `server_version`, `tool_schema` (integer; frozen at 1, now 3, see `contract/CHANGES`), lifecycle `state`, the full tree (every item with id, text, parent, status, answer, `parked_by`; open items first, REQ-45), item counts, hook-health counts, last event number, `resumable` (an array), `decision_log`, `write_error` and any recorded version mismatch (REQ-77). The review fixes of 2026-11-16 also pinned in `contract/`: the state-by-tool matrix and error precedence (`tools.json`), the decision log, pid file and stale-claim sequence, id formats and pending-interview ends (`runtime.md`), replay rules (`journal.md`).
- Acceptance: (a) every schema validates a hand-written good and bad example per tool (stdlib checker, no `jsonschema` dependency); (b) `tool_schema` is documented in `contract/CHANGES` and `contract/runtime.md` and equals the value in `tools.json` (checked by `check_contract.py`; 3 after the review fixes); (c) the spike-3 tool names used by `acceptance.py` are either kept or the script change is listed; (d) run-time contract section states process start: argv, how the suite injects the data directory, project directory, clock offset for pruning (REQ-61), how `_meta` is sent, and how a hook-style call that carries a session id is sent.
- Depends on: G0. Drop rule: none; blocking for everything.

### C2 Suite harness and port of A1 to A7
- Goal: reusable runner with scenario ids, per-scenario temp dirs in `.scratch/`, `--only`, `--list`, TAP-like output and a non-zero exit on failure.
- REQ: REQ-80, REQ-71.
- Acceptance: `acceptance.py` A1 to A7 pass unchanged in meaning against both spike-3 servers (their recorded outputs in `docs/evidence/spike3/acceptance-*.txt` are the oracle); the runner can send a call with and without `_meta`; a scenario count above 30 triggers the data-file form (REQ-80): decision recorded in the PR, recommendation to adopt data files from the start because section 5 lists about 90 scenarios [user-decision].
- Depends on: C1. Drop rule: if data-file format stalls, keep Python functions and defer the format to C5.

### C3, C4, C5 Scenario batches
Split by area so each PR is reviewable; all are written red (no implementation exists), then validated against a scripted fake server (`conformance/fake/`, per-scenario canned replies) to prove the scenario can pass and can fail, which stops a vacuous suite.

| Slice | Scenario ids (section 5) | Acceptance | Depends on | Drop rule |
|---|---|---|---|---|
| C3 Data model, transitions, status | S-30..S-31, S-33..S-38 (incl. S-30b, S-35c, S-38c; not S-32, which is C4), S-45a | Each scenario has a failing run on a mutation of the fake and a passing run on the fake | C2, G5 | S-34, S-35 follow G5 outcome |
| C4 Hook-facing tools, finish gate, caps | S-40..S-44 (incl. S-40d, S-41b, S-43f), S-32 (S-32a to S-32e; authored here only), S-45b, S-45c | Includes the `_meta` forgery pair, the classifier table of 5.1 and the budget scenarios S-32b..e (G1 decided) | C2 | None (G1 decided) |
| C5 Lifecycle, session, journal, resume, log, launcher | S-13a, S-13b, S-20..S-27 (incl. S-20d, S-22d, S-23g, S-23h), S-50..S-52 (incl. S-52h, S-52i, S-52j), S-60..S-63 (incl. S-60c, S-61e, S-62c), S-73, S-77, plus the existing S-L1 and S-L2 | Journal scenarios read files only through the data directory the suite supplied; S-73 drives the launcher with the binary absent from the PATH | C2, G4 | S-20c wording per G4 |

### X1 Language-experiment record
- Goal: create `docs/evidence/language-experiment.md` (header, a build table for slice rows, a per-change table for the retirement experiment's first five change requests after M2, with the alternation order Rust, TypeScript, Rust, TypeScript, Rust, and a by-role summary) and the pre-build retirement rule values from G3.
- REQ: REQ-72. Acceptance: file exists with columns from ADR 0003 section 4 step 3; ADR 0003 status moves to Accepted by the user. Depends on: G3. Drop rule: none (REQ-72 requires it before the real build).

## 5. Scenario list (every behavioural REQ)

Ids are stable; one row = one suite scenario. The seven spike-3 scenarios (A1 to A7, see `docs/evidence/spike3/CHANGES.md`) are kept as S-A1..S-A7, each with its own row; S-L1 and S-L2 were added in slice C2.

| Scenario | REQ ids |
|---|---|
| S-13a while no interview is active, `note_prompt`, `check_stop` and `skill_started` (unlisted name) record `last_seen_session_id` and set `prompt_seen` only: no journal, no counters, empty text or `{}` returned | REQ-13, REQ-27, REQ-41 |
| S-13b `check_stop` in state `none` allows and clears `prompt_seen`: a `note_prompt` followed by a `check_stop` and then `skill_started` starts with prompt count 0 (only a prompt since the previous Stop counts as the invoking prompt) | REQ-27 (none row, rule 2), REQ-13 |
| S-20a typed-start route: `skill_started` with an exact listed name starts an interview | REQ-20, REQ-24 |
| S-20b unlisted or near-miss name (`grill-me-copy`, case change, `wayfinder`, `write-a-prd`) does not start; exactly `grill-me` does | REQ-20, REQ-24 |
| S-20d `skill_started` outcomes: a listed name leaves the server `pending`; an unlisted name leaves it `none`; a `tool_schema` mismatch still returns `{}` or empty text, records `status.version_mismatch` (set by the mismatch, cleared by a later match) and writes no `mismatch` event when no interview exists; a second listed `skill_started` while pending, active or complete is a no-op | REQ-20, REQ-24, REQ-41, REQ-77 |
| S-20c model-invoked route yields same interview id | REQ-20 |
| S-21a `begin_interview` with `mode: register` and no `session_id` starts and binds to `last_seen_session_id`; second call returns existing id | REQ-21, REQ-22, REQ-27 |
| S-21b implicit start on `log_item` with no journal; flagged in log | REQ-21 |
| S-21c hook-facing tools, `status`, `resume`, `end_interview` never start an interview | REQ-13, REQ-21 |
| S-22a start resets counters, caps and stamps a new id; keeps one prompt when `prompt_seen` was set | REQ-22, REQ-27 |
| S-22b start while pending, active or complete is a no-op returning the id, state intact | REQ-22 |
| S-22c new start archives only the server's own earlier journal | REQ-22, REQ-61 |
| S-22d interview ids match `iv-<UTC timestamp>-<8 hex>`; two servers run in turn on one project never reuse an id; server-assigned item ids are `Q<n>` (next unused) and caller-supplied ids are kept | REQ-22, REQ-23, REQ-30 |
| S-23a allowed finish attempt writes the log; with nothing open the interview becomes `complete`, journal kept | REQ-23, REQ-27, REQ-63 |
| S-23b `reopen`, `log_item`, `park` or `resolve` in `complete` returns it to `active`; no implicit start | REQ-21, REQ-23, REQ-27 |
| S-23c `end_interview` with items open refused; with `abandon` and reason accepted; log says "ended incomplete by the model" | REQ-23, REQ-50 |
| S-23d log file name carries the interview id; second interview does not overwrite | REQ-23 |
| S-23e allowed finish because caps are spent still writes the log | REQ-23, REQ-42 |
| S-23f `end_interview` after the replay step ends a complete interview as normal; journal archived | REQ-23, REQ-27 |
| S-23g `status` and `end_interview` do not return a `complete` interview to `active`; `status` after `end_interview` reports state `none` | REQ-23, REQ-27 |
| S-23h `end_interview` per state: `none` errors "no interview to end"; `pending` without `mode: plain` errors; `mode: plain` outside pending errors; `abandon: true` with nothing open errors; `abandon` without `reason` (and `reason` without `abandon`) rejected as invalid arguments; the result carries `decision_log` | REQ-23, REQ-27 |
| S-26a different `session_id` on a hook call while items are open ends the old interview as abandoned, log flagged incomplete, journal archived, gate inactive; with nothing open it ends without the flag | REQ-26 |
| S-26b start after session change ends old then starts new (check precedes REQ-22 no-op) | REQ-22, REQ-26 |
| S-26c same-session repeated hook calls do not end it | REQ-26 |
| S-26d `begin_interview` or implicit start binds to `last_seen_session_id` recorded by an earlier hook call; with no hook call seen it falls back to `CLAUDE_CODE_SESSION_ID` | REQ-26, REQ-27, REQ-21 |
| S-26e `resume` rebinds the resumed interview to `last_seen_session_id`; the next hook call with that id does not abandon it | REQ-26, REQ-27, REQ-52 |
| S-26f session change on a `complete` interview ends it as "ended after completion" and the log is not downgraded; on a pending interview it stands it down | REQ-26, REQ-27, REQ-23 |
| S-27a invoking prompt counted: `note_prompt` while inactive sets `prompt_seen`; the hook start keeps that one prompt, so a `source=user` answer volunteered in the kickoff prompt is accepted and REQ-44 does not report hooks as not firing | REQ-27, REQ-22, REQ-44, REQ-32 |
| S-27b hook-started interview is `pending`; a second Stop check with no model-facing register call stands it down: gate off, log note "skill did not engage", mode `not engaged` | REQ-27, REQ-62 |
| S-27c engagement: `begin_interview` (register), `log_item`, `resolve`, `park` or `reopen` makes a pending interview `active`; `status` alone does not | REQ-27, REQ-21 |
| S-27d `end_interview` with `mode: plain` stands a pending interview down at once, not flagged incomplete, log mode `plain` | REQ-27, REQ-23, REQ-62 |
| S-27e `complete` state: Stop gate allows every turn including an unlogged question and a further finish attempt; only a mutating call returns it to `active` | REQ-27, REQ-23, REQ-43 |
| S-30a item fields round-trip through `status` | REQ-30 |
| S-30b a `log_item` carrying a caller-supplied `id` already used errors `duplicate id: <id>`, logs nothing and writes no journal event | REQ-30 |
| S-31a resolve without answer, source or rationale rejected; whitespace-only rejected; multi-line rationale rejected | REQ-31 |
| S-31b replay applies the same check and fails resume naming the line | REQ-31, REQ-60 |
| S-32a `source=user` rejected with no prompt since last model action | REQ-32 |
| S-32b volunteered decision in `log_item` accepted only after a prompt since previous Stop call | REQ-32 |
| S-32c budget max(1, p): after a turn presenting p distinct items and one prompt, p `source=user` answers are accepted and the (p+1)th rejected; an empty list (p = 0) allows 1 | REQ-32, REQ-43 |
| S-32d fallback: when the classifier returns `unknown` (error, timeout, unparseable), 4 `source=user` answers are accepted after one prompt and the 5th rejected; an empty list is not `unknown` (S-32c) | REQ-32, REQ-43 |
| S-32e volunteered `log_item` answers do not draw on the budget of S-32c; 4 per prompt are accepted and the 5th rejected | REQ-32 |
| S-33a park records `parked_by` and reason; model park keeps blocking; user park does not | REQ-33 |
| S-33b `parked_by=user` without a prompt rejected | REQ-33 |
| S-34a `decision` resolved only with `source=user`; parked only with `parked_by=user` | REQ-34 |
| S-35a `log_item` never rejects on branch size; finish gate flags under-sized parent; `resolve` of a parent with fewer than 2 children is rejected for source `model` | REQ-35 |
| S-35b `subquestion` without parent rejected ("subquestion needs a parent"); `question` with parent accepted | REQ-35 |
| S-35c a parent parked by the user is not flagged by the gate; `resolve` of an under-sized parent with source `user` accepted; children of any kind and any status count toward N | REQ-35, REQ-33 |
| S-36a reopen makes dependants (direct and transitive) Stale; Stale blocks finish; resolvable again | REQ-36 |
| S-38a transition table: every allowed and disallowed pair, error names id and current status | REQ-38 |
| S-38c `resolve`, `park` or `reopen` on an unknown id errors `no such item: <id>`; from `none` or `pending` such a call starts and engages nothing and writes nothing | REQ-27, REQ-38, REQ-21 |
| S-38b model-parked item resolvable; re-park with `parked_by=user` | REQ-33, REQ-38 |
| S-40a `note_prompt`, `skill_started`, `check_stop` with `_meta.claudecode/toolUseId` rejected; same calls without it accepted | REQ-40 |
| S-40b model-facing tools accept calls with `_meta` | REQ-40 |
| S-40d the `_meta` test is the presence of `claudecode/toolUseId`: a `_meta` holding only `progressToken` is hook-style (accepted by hook tools), an empty-valued `claudecode/toolUseId` is model-style (rejected); every domain failure is a result with `isError: true`, never a JSON-RPC error | REQ-40 |
| S-40c forged `note_prompt` does not enable `source=user` | REQ-32, REQ-40 |
| S-41a prompt counter advances per `note_prompt` | REQ-41, REQ-44 |
| S-41b hook output contract: `note_prompt` and `skill_started` return empty text or exactly `{}` (also for an unlisted name and for a version mismatch) and never block or deny; only `check_stop` returns a decision | REQ-41, REQ-77 |
| S-42a caps: 2 blocks per prompt, 8 to finish, 12 unlogged; fail open after; counters per interview | REQ-42 |
| S-43a finish attempt (no `?`) with Open, Stale, model-parked item or under-sized branch: block; none open: allow | REQ-43 |
| S-43b question turn about a logged open item: allow; about an unlogged question: block | REQ-43 |
| S-43c classifier table (5.1), including empty and very long messages | REQ-43 |
| S-43d no interview active, or pending, or complete: the gate always allows without classifying | REQ-13, REQ-27, REQ-43 |
| S-43e classifier returns either a list of presented item ids (possibly empty) or `unknown`; these are distinct and feed S-32c and S-32d | REQ-43, REQ-32 |
| S-43f classifier parity: a question citing an open item's id label concerns it; otherwise at least 3 shared content words are required; a single labelled one-line request for an open item is not a finish attempt, while the same text on two lines is | REQ-43 |
| S-44a user-sourced answer with zero hook calls seen (counting calls made while inactive): error says hooks are not firing | REQ-44, REQ-27 |
| S-45a `status` returns the full tree: every item with id, text, parent, status, answer, `parked_by`; order Open, Stale, Parked, Resolved, creation order within a group; Resolved items carry non-null answer, source and rationale, Parked items a reason | REQ-45, REQ-31 |
| S-45b Stop block reason names each open item with id and text | REQ-45, REQ-43 |
| S-45c with 12 open items the reason lists the first 10 only and says 2 more; item text over 120 characters is truncated to 120 | REQ-45 |
| S-50a registered tool failure is an `isError: true` result, not a JSON-RPC error, and does not abort the server (fail open) | REQ-50, REQ-40 |
| S-52a unfinished journal with dead owner: `status` shows `resumable`; mutating calls and `begin_interview` refused with "unfinished journal: call resume or discard" | REQ-52 |
| S-52b live owner (pid file with a live process and the same start time): journal not offered; a reused pid with a different start time counts as dead | REQ-52, REQ-61 |
| S-52c `resume` omitted id with one journal; several journals list and require id; `discard` archives | REQ-52 |
| S-52d resume reconstructs state and last event number; loss since last Stop visible | REQ-52, REQ-60 |
| S-52e graceful exit (stdin EOF) on an active, not-complete interview: log flagged "interrupted", journal kept unfinished (no `end` event), pid file removed; on a complete interview: journal archived | REQ-27, REQ-23, REQ-52, REQ-60 |
| S-52f resume exclusivity: `resume` claims the journal with an exclusive pid-file create; a second resumer fails with "journal already claimed"; `resume` is refused in `active` or `complete` | REQ-52, REQ-27 |
| S-52g a hook start while an unfinished journal exists proceeds; `status` reports `resumable` alongside; mutating calls refused until `resume` or `discard` | REQ-52, REQ-27 |
| S-52h `discard`: archives the named journal, or the only one, without starting an interview; state stays `none` (a pending interview stays pending); with no unfinished journal, an unknown id, a live-owned journal, several journals and no id, or in `active`/`complete` it errors as the contract lists; the same no-journal, unknown-id and live-owned cases for `resume` | REQ-52 |
| S-52i pending fates: a pending interview ended by session change, by server exit, or by a successful `resume` writes an `end` event (`stood_down`, mode `not_engaged`) and its journal is archived, leaving no unfinished ghost; the failed `resume` leaves the pending interview untouched | REQ-27, REQ-26, REQ-52, REQ-60 |
| S-52j stale pid file: a crashed owner's pid file is replaced by `resume` (rename aside, exclusive create), a pid file holding a live pid with a different start time is stale, `status.resumable` is an array listing every unfinished journal with no live owner | REQ-52 |
| S-60a every event has `v: 2`, `ts`, sequence number; flush at each `check_stop` and end | REQ-60 |
| S-60b v1 events (no `v`) replay; `v: 3` fails resume with 1-based line number, state unchanged | REQ-60 |
| S-60c flush on stdin EOF: events queued after the last `check_stop` are in the journal after a graceful exit; replay ignores a torn final line and fails on invalid JSON, a seq gap or an unknown kind on any other line naming the line | REQ-60, REQ-52 |
| S-61a journal under `CLAUDE_PLUGIN_DATA` (suite-set), fallback to temp dir; mode 0600; the exact path layout and modes are pinned in `contract/runtime.md` and tested by S-L1 and S-L2 | REQ-61, REQ-91 |
| S-61b archive on end; archives over 30 days pruned at start (clock offset) | REQ-61 |
| S-61c two servers, one project: no shared or archived journals | REQ-61 |
| S-61d unfinished journals older than 30 days are pruned at start (clock offset); younger ones are kept | REQ-61, REQ-27 |
| S-61e unwritable data directory: start-up and every tool call still succeed, `status.write_error` is set and clears after a successful write | REQ-61, REQ-50 |
| S-62a log location: `.scratch/` if present else `decision-logs/` with `.gitignore` `*`; mode 0600; contents (tree, sources, parked_by, reopen history, end kind, implicit flag, skill mode, hook counts, journal position) | REQ-62, REQ-91 |
| S-62b log written at every allowed finish attempt and at end, interrupted exit and stand-down; mode recorded as `register`, `plain` or `not engaged` | REQ-62, REQ-23, REQ-63 |
| S-62c `end_interview.decision_log` and `status.decision_log` return the log path; file name `decision-<interview_id>.md`; the header lines `journal_position`, `mode`, `end_kind`, `implicit_start` are present with the pinned strings | REQ-62, REQ-63 |
| S-63a log journal position matches `status`; stale after later event | REQ-63 |
| S-71a `initialize` reply (protocol version, server info, tools capability) and `tools/list`: exactly the twelve tool names of `contract/tools.json` and input schemas equal to it (refs resolved, annotations ignored); an unknown tool name is a JSON-RPC error, after which the server still answers | REQ-71, REQ-80, REQ-40 |
| S-77a `status` returns `server_version`, integer `tool_schema` | REQ-77 |
| S-77b `begin_interview` with wrong `tool_schema`: error naming both values | REQ-77 |
| S-77c `skill_started` with wrong `tool_schema` returns `{}` and does not block; `status` reports the recorded mismatch | REQ-77, REQ-41 |
| S-73a launcher with the binary missing serves a no-op stub MCP server: hook-facing tools return `{}`, `status` returns `{"unavailable":"binary not found"}`, one-line message on stderr | REQ-73, REQ-77 |
| S-73b the launcher's binary-missing message differs from the version-mismatch error text | REQ-73, REQ-77 |
| S-A1 dependent items go Stale on reopen and resolve back one at a time; the tree lists Open, Stale, Parked, Resolved in that order and keeps each answer, source and rationale (kept) | REQ-36, REQ-38, REQ-31, REQ-45 |
| S-A2 reopen on an Open item errors naming the id and its status; unknown `depends_on` or `resolve` ids error naming the id; a duplicate id errors; a failing call leaves the tree and `last_event` unchanged (kept) | REQ-36, REQ-38, REQ-30, REQ-40 |
| S-A3 finish gate blocks with Stale items open and names them (kept) | REQ-42, REQ-43, REQ-45 |
| S-A4 model resolve of a decision rejected; user resolve after a user prompt succeeds (kept) | REQ-32, REQ-34 |
| S-A5 model park of a decision rejected; model park of a question succeeds (kept) | REQ-33, REQ-34 |
| S-A6 journal carries `v: 2` and `ts`; v1 and mixed journals replay; `v: 3` fails resume naming the line (kept) | REQ-60, REQ-61 |
| S-A7 `status` reports `journal_schema` 2 (kept) | REQ-60 |
| S-L1 journal layout, user-only permissions and archive move (existing) | REQ-61, REQ-23, REQ-27 |
| S-L2 without `CLAUDE_PLUGIN_DATA` the journal uses the temp directory with the same layout (existing) | REQ-61 |
| S-90a start under 1 s and idle RSS under 200 MB (reported, not a hard CI gate) | REQ-90 |

Non-scenario REQs (design, process, distribution, tooling) are covered by slices, not the suite: REQ-1 to REQ-6, REQ-10 to REQ-14 (S-21c, S-43d cover the observable part), REQ-15 (slice P5), REQ-24 start-route name (slices V9, P4), REQ-37, REQ-46 (classifier replay, slice I2), REQ-51, REQ-53, REQ-70 to REQ-76, REQ-81 to REQ-83, REQ-92, REQ-93.

### 5.1 Finish-attempt classifier (REQ-43 spec, [assistant], G5 decided)
The build plan fixes the heuristic as a table in `contract/classifier.md` so both languages implement the same one, ported in behaviour from the prototype (`reference/armG/register_store.py:679-730`: `extract_ids`, `single_label_request`, `is_finish_attempt`, `classify_message`; read-only). Finish attempt = no `?` after stripping fenced code, inline code, URLs (the prototype's `strip_code_urls`) and quoted lines, except a single labelled one-line request: one non-blank line (fences and URLs removed) that starts with the id label of an Open or Stale item, which is allowed as a question turn about that item (the prototype's exemption). Question turn otherwise. A question concerns an open item if it cites the item's id label (labels are extracted from the unstripped message, as `extract_ids` does) or, failing that, shares at least 3 content words, ignoring stop words, with an Open or Stale item's text (starting values, testable). The same rule names the items a turn presented (every Open or Stale item it cites or overlaps). The classifier's result is a list of ids, which may be empty, or `unknown` when it errors, times out or cannot parse; these are different results: an empty list gives the budget of 1, only `unknown` gives the flat 4 (REQ-32; S-32c, S-32d, S-43e). Replay test (REQ-46): before the classifier ships, a script runs it over question turns taken from stored grill-me transcripts, compares the result with a hand-labelled register state per turn for a sample of 30 turns (labels by the assistant, reviewed by the user), and writes `docs/evidence/classifier-replay.md` listing each turn with its classification and named items. Pass: at least 90% of sampled turns agree and zero finish attempts are classified as question turns [assistant: declared constants].

## 6. Implementation slices (M1, M2)

Each slice: one PR per language; goal; REQ; green scenarios; offline unit tests per REQ-81; dependency; drop rule. "R" = Rust, "T" = TypeScript on Node with esbuild single-file bundle.

| Slice (first language) | Goal | REQ | Scenarios turned green | Depends on | Drop rule |
|---|---|---|---|---|---|
| I1 (R first) core register: MCP stdio server, `begin_interview`, `log_item`, `resolve`, `park`, `reopen`, `status`, data model, transition table, Stale propagation | The register as a state machine, no hooks: nothing in I1 needs `note_prompt`, `check_stop` or the user-answer rule, so every scenario below uses model-sourced calls only | REQ-30, REQ-31, REQ-35 (rejections only), REQ-36, REQ-38, REQ-45 (`status` tree), REQ-31 interface check | S-30a, S-30b, S-31a, S-35b, S-38a (model-sourced pairs), S-45a | C1, C3 | If a language cannot make Resolved-without-answer unrepresentable, enforce at interface only (REQ-31) and note it in the experiment file |
| I2 (T first) hook-facing tools and finish gate: `note_prompt`, `skill_started`, `check_stop`, `_meta` guard, caps, classifier, user-answer rule, hook output contract | Enforcement. Acceptance: the classifier replay test (REQ-46) has run on its 30-turn labelled sample and its report is committed before the classifier is accepted; the Stop reason lists open items (REQ-45) | REQ-32, REQ-33, REQ-34, REQ-35, REQ-36, REQ-38, REQ-40, REQ-41, REQ-42, REQ-43, REQ-44, REQ-45, REQ-46 | S-32, S-33a, S-33b, S-34a, S-35a, S-35c, S-36a, S-38b, S-40, S-41, S-42, S-43, S-44, S-45b, S-45c, S-71a (the first slice in which all twelve tools exist), S-A1..S-A5 (every S-A scenario opens with `note_prompt`; S-A3 also calls `check_stop`; S-A4 needs the user-answer rule; S-A5 needs the prompt rule for its decision park) | I1 (same language), C4, V5, V8 | If the replay report misses the REQ-46 criterion (below 90% agreement, or any finish attempt classified as a question turn), the classifier rule is revised and replayed; if still failing, the 3-word overlap rule is dropped for id-label matching only |
| I3 (R first) lifecycle: start routes, session binding (`last_seen_session_id`), pending, engagement and stand-down, no-op start, implicit start, complete state, `end_interview` (including `mode: plain`), version handshake | Interview lifecycle (REQ-27 state table) | REQ-13, REQ-20..REQ-27, REQ-50, REQ-77 | S-13a, S-13b, S-20, S-21, S-22, S-23, S-26, S-27, S-38c, S-50a, S-77 | I2, V3, V10 | Implicit start (REQ-21 second fallback) dropped if the user vetoes at G0 |
| I4 (T first) journal, `end` event, graceful exit, resume with exclusive claim, discard, pid-file liveness check, archive, pruning (archives and unfinished journals), decision log | Durability and output | REQ-52, REQ-60..REQ-63, REQ-91 | S-31b, S-52, S-60, S-61, S-62, S-63, S-A6, S-A7, S-L1, S-L2 | I3 | No lock primitive in either language (REQ-52, F1): both use the pid file with an `O_EXCL` claim and a process start-time liveness check; if a platform cannot report process start time, fall back to pid-alive only and record the difference in the experiment file |
| I5 (R first) non-functional and parity: REQ-90 measurement, both builds reproducible, README for the contract | Close the suite | REQ-90, REQ-71 | S-90a; full suite 100% | I4 both languages | None; REQ-90 breach is recorded against the candidate, not a drop |

Order of PRs: I1R, I1T, I2T, I2R, I3R, I3T, I4T, I4R, I5R, I5T.

Scenario ownership rule: a scenario is green in the first I-slice whose tools it needs; the I1 list uses no hook-facing tool and no `source: user` or `parked_by: user` call. Every planned scenario id appears in exactly one I-slice list, except S-73a and S-73b, which turn green in P1 (checked by script) and is authored in exactly one of C3 to C5 (existing S-A1..S-A7, S-L1 and S-L2 were authored in C2).

## 7. Plugin, verification, smoke, release (M3, M4)

### P1 Plugin skeleton
- Goal: `plugin/` with `.claude-plugin/plugin.json`, `.mcp.json` (server `regspike`, always started through the launcher), launcher `plugin/bin/regspike-launch` (REQ-73, REQ-77), `hooks/hooks.json` (UserPromptSubmit `note_prompt`; Stop `check_stop` with `${last_assistant_message}`; UserPromptExpansion and PreToolUse `Skill` to `skill_started`, matching exactly `grill-me`), root `.claude-plugin/marketplace.json`. The plugin contains no skill text [user: gate walk 2026-11-16] (REQ-12, REQ-15).
- REQ: REQ-11, REQ-12, REQ-13, REQ-20, REQ-41, REQ-73, REQ-75, REQ-77, REQ-24.
- Acceptance: `claude --plugin-dir plugin/` from a directory outside the plugin lists the three hook-facing and nine model-facing tools; a session at the repository root does not load `.mcp.json` as a project server (REQ-12); with the binary absent the launcher serves the no-op stub (hook-facing tools return `{}`, `status` returns `{"unavailable":"binary not found"}`) and prints the one-line message "register unavailable: binary not found" to stderr, distinct from the version-mismatch text (S-73a, S-73b turn green); `REGSPIKE_IMPL=ts` selects the TypeScript bundle; no `SKILL.md` or skill text exists under `plugin/`. Hook inputs carry the `tool_schema` the plugin requires.
- Depends on: C1, one passing implementation (I3 minimum for hooks); G2 and G4 are decided. Drop rule: if `${REGSPIKE_IMPL}` expands in the start command (V2), delete the launcher's selection branch but keep its missing-binary stub.

### V1 to V10 Build-time verification tasks (requirements section 12 open items)
Each is a script under `smoke/verify/` writing `docs/evidence/verify-<id>.md` with command, version, result, USD. Single-trial results are labelled single-trial. Each depends on P1 and a server that passes at least I3 unless stated; none blocks the others. Total expected cost under USD 0.40 on the cheapest model (V1 to V6, V8 to V10).

| Id | Check | REQ | Acceptance | If it fails |
|---|---|---|---|---|
| V1 | Hook error visibility: a failing `mcp_tool` hook (binary missing, server not connected) shows or hides an error in `claude -p` and interactive | REQ-53, REQ-13, REQ-77 | Outcome recorded (visible, hidden, blocking) | Visible/blocking: tighten launcher and document install order [user-decision] |
| V2 | Start-command variable expansion of `${REGSPIKE_IMPL:-rs}` in `.mcp.json` | REQ-73 | Server starts with the chosen binary in both cases | Keep launcher selection (P1 drop rule) |
| V3 | Compaction and `session_id`: run a long enough session, or `/compact`, and compare `session_id` seen by hooks before and after | REQ-26, REQ-22 | Recorded equal or different | If different: REQ-26 would end the interview at compaction; change rule before I3 merges [user-decision] |
| V4 | Interactive `/clear` and `/compact` (spike 1 was stream-json `-p` only): server state survives, new `session_id` after `/clear` | REQ-10, REQ-22, REQ-26 | Manual script with pause gates (needs a tty; the user runs it once) | Update REQ-10 and S-26 text |
| V5 | Empty `last_assistant_message` (tool-only final turn) and short messages through `${last_assistant_message}` | REQ-41, REQ-43 | Result recorded; S-43c includes the observed form | Gate treats empty as finish attempt or allow, chosen by result [user-decision] |
| V6 | `SessionEnd` hook can call `end_interview` | REQ-26, REQ-23 | Recorded yes or no | If no: rely on REQ-26 session-change detection only |
| V7 [open] | Compaction injection (an upgrade to REQ-45, not a dependency): can a hook on compaction (`PostCompact`, `SessionStart` with `source=compact`, or similar) inject the tree into context; does the model retain the skill text after compaction | REQ-45, REQ-1 | Each of the two questions answered yes, no or not testable, single-trial labelled | If no: REQ-45's `status` and Stop-reason recovery stands alone; nothing else changes |
| V8 [open] | Hook output visibility. Spike 6 (`docs/evidence/spike6/RESULTS.md`, two runs, haiku, 2.1.325) found a UserPromptSubmit `mcp_tool` hook's return text not visible to the model. Still to record: for UserPromptExpansion and PreToolUse (Skill) hooks, whether return text is visible to the model, whether non-empty or non-`{}` text blocks or alters the call, and whether `{}` and empty text are both inert | REQ-41, REQ-77 | Outcome recorded per event (visible, hidden, blocking, inert); the hook output contract of REQ-41 is confirmed or revised before I3 merges | If any event treats `{}` or empty text as a decision: change the contract (C1) and S-41b before I3 [user-decision] |
| V9 [open] | Start-route name: record what `command_name` (typed route) and `tool_input.skill` (model-invoked route) carry for a user-level (dotfiles) skill named `grill-me` and for a project-level skill with the same name, including any prefix or scope | REQ-24, REQ-20 | Exact received strings recorded for both skill locations and both routes, single-trial labelled | If the name is scoped or prefixed: change the configured list entry (REQ-24) and S-20a, S-20b before P4 [user-decision] |
| V10 [open] | Typed-start hook order: for a typed `/grill-me`, does UserPromptSubmit (`note_prompt`) reach the server before UserPromptExpansion (`skill_started`)? Also whether `prompt_seen` is set for a model-invoked start | REQ-27 (rule 2), REQ-44 | Order recorded, single-trial labelled | If the expansion hook arrives first: a typed-route start counts its own prompt as prompt 1 regardless of `prompt_seen` (change REQ-27 rule 2 and S-27a before I3 merges) [user-decision] |

V3, V8 and V10 are needed before I3 merges (V8 also before I2); V5 before I2 merges; V9 before P4. V7 is not needed by any slice and is excluded from the cost figure above. Others any time before P4.

### P2 Skill integration and fallbacks (superseded by P5; id kept)
- [user: gate walk 2026-11-16] The skill stays in the dotfiles repository, so this slice has no repository-side PR; its content moved to P5 and dotfiles drafts D4 and D5. Resume prompts per REQ-52 are part of P5.

### P5 Skill register mode (dotfiles side)
- Goal: add the register mode with a plain fallback to the existing grill-me skill in the dotfiles repository (drafts D4, D5): first step is a call to `status`; if it answers and `tool_schema` matches, run in register mode (log items, resolve, walk open branches, `begin_interview` carrying `mode: register` and no session id, `end_interview` after the replay step, `end_interview` with `mode: plain` when it has detected a schema mismatch or another reason for plain mode, resume prompts per REQ-52); if absent, error or mismatch, run the existing plain interview and tell the user once that no record is being kept (REQ-51). Also check, in the first integration, whether `wayfinder` or `write-a-prd` run question rounds without calling grill-me (REQ-24).
- REQ: REQ-14, REQ-15, REQ-21, REQ-24, REQ-27, REQ-50, REQ-51, REQ-52, REQ-62, REQ-63.
- Acceptance (live runs, cost declared and under USD 1, REQ-3): (1) one live run with the plugin loaded: register mode, and the decision log shows the mode as `register`; (2) one live run without the plugin: plain fallback, the transcript shows the notice that no record is being kept, and the interview completes; with a resumable journal the skill asks resume or discard and never proceeds silently; (3) after the replay step the skill has called `end_interview`, and a forced plain stand-down is logged with mode `plain`.
- Depends on: P1, one passing implementation (I3 minimum); the work is filed in the dotfiles repository on the user's instruction only. Drop rule: none; REQ-15 is decided.
### P3 Smoke fixtures
- Goal: scripted prompts and a minimal user-level skill stub named exactly `grill-me` (in a throwaway user skills directory, not under the plugin) used by P4 (the real skill lives in the dotfiles repository, REQ-15). REQ-82. Acceptance: fixtures run offline against the fake server. Depends on: P1. Drop rule: merge into P4 if under 100 lines.

### P4 Live smoke suite
- Goal: `smoke/run.sh` runs `claude -p` on the cheapest model, from a directory other than the plugin directory (REQ-82; spike 2 invalidation).
- REQ: REQ-82, REQ-92, REQ-13, REQ-20, REQ-41, REQ-42, REQ-51, REQ-12.
- Acceptance (each a check printed PASS/FAIL, with a `--max-budget-usd` guard per call and a running total): (1) plugin loads and tools appear; (2) hooks fire (`status` hook counts non-zero); (3) Stop block works on an unlogged finish; (4) interview starts by typed `/skill` route; (5) starts by model-invoked Skill route (both with the user-level skill named `grill-me`, not the plugin stub name; the names the hooks receive are those recorded by V9); (6) scoping: a question in an unrelated session is never blocked; (7) server-absent run shows the notices; (8) whole run under USD 0.25, total printed. Declared cost in the PR; a run over USD 0.25 fails the suite.
- Depends on: P1, V9, fixtures P3 (a stub skill; P5 is exercised by its own two live runs), one passing implementation. Drop rule: cases (4) or (5) dropped if V1 or V4 show the route is unavailable in the pinned Claude Code version, recorded in REQ-92.

### R1 Release and CI
- Goal: GitHub Actions: on pull request run the suite and unit tests for both languages; on tag build `regspike-rs` (Linux x86_64 only, REQ-74) and `regspike.mjs` and attach to a GitHub release; the plugin version and `tool_schema` are asserted equal across plugin and binary before release (REQ-77).
- REQ: REQ-73, REQ-74, REQ-75, REQ-77, REQ-12, REQ-71.
- Acceptance: a draft release contains both assets and checksums; installing from the private marketplace on a second clone (`/plugin marketplace add` with the user's git credentials) lists the plugin; unit and suite jobs fail on a seeded failure. Smoke suite is on demand only, not in CI (spend). Depends on: I5 both, P1. Checklist item [user: gate walk 2026-11-16]: rename the working names (`regspike-rs`, `regspike-ts`, REQ-73) or confirm them before the first release tag; G6 is decided here. Drop rule: macOS and Windows never (REQ-6); Rust cross-build dropped (REQ-74). Needs a decision if the private install fails under background credentials [user-decision].

### E1 Experiment close-out
- Goal: after the first five change requests made after M2 (full suite green in both) have been applied to both languages, and after the release milestone R1, run the ADR 0003 retirement procedure: assess defects after merge and release friction, then tag, archive, remove from CI, release and launcher.
- REQ: REQ-70, REQ-72. Acceptance: ADR 0003 records the user's decision and date; loser removed from `.github/` and the release assets; suite stays. Depends on: R1, five change rows (both languages filled) in `docs/evidence/language-experiment.md`. Drop rule: none; the user may extend the experiment.

## 8. Dotfiles issue drafts (filed 2026-11-16; target repository `kaybenleroll/dotfiles`)

Filed 2026-11-16 as kaybenleroll/dotfiles #552 to #556, one per issue, under the user's HARD RULE (dotfiles repository owns `~/.claude`).

| Draft | Title | REQ | Content | Order |
|---|---|---|---|---|
| D1 | Install the decision-register plugin and binaries | REQ-73, REQ-75, REQ-76 | Put `regspike-rs` and `regspike-ts` on the PATH from the GitHub release (chezmoi external or run script); document the credential helper requirement; set `REGSPIKE_IMPL` default | After R1 |
| D2 | Marketplace and enabled plugin in `settings.json` | REQ-76, REQ-75 | `extraKnownMarketplaces` entry for the private repository; `enabledPlugins` entry; applied by chezmoi template `dot_claude/settings.json.tmpl` | After P1 |
| D3 | Allow rules for the plugin's tools | REQ-76 | `mcp__plugin_<plugin>_<server>__*` model-facing allow rules (not the hook-facing ones, which hooks call without permission); keep `mcp__sequential-thinking` for other skills (REQ-5) | After P1 |
| D4 | Add the register mode with plain fallback to the existing grill-me skill (REQ-15) | REQ-15, REQ-24, REQ-51, REQ-6 | [user: gate walk 2026-11-16] Replaces the earlier 'retire the copy / move to plugin' draft. The skill stays in dotfiles; first step calls `status`; register mode if it answers with the expected `tool_schema`, otherwise the existing plain interview with a once-only notice that no record is being kept; `begin_interview` carrying `mode: register` and no session id; `end_interview` after the replay step, and `end_interview` with `mode: plain` on a schema mismatch; resume prompt per REQ-52 (the filed issue text predates these additions). Acceptance as slice P5 (one live run with the plugin, one without) | After P1 and one passing implementation |
| D5 | Plain-fallback wording in the grill-me text | REQ-51 | "required" to "preferred", the once-only no-record notice, resume prompt. Always applies now (the skill is no longer in doubt); may be one PR with D4 | With D4 |

(The user's gate walk referred to this change as D2; the retire-or-move draft was D4 and D2 is the marketplace entry, left unchanged.)

## 9. Slice and milestone counts

Slices: C1 to C5 (5), X1, I1 to I5 (5, each in two languages = 10 PRs), P1 to P5 (5; P2 is folded into P5, so four have PRs and P5 is dotfiles-side), V1 to V10 (10, may share one PR), R1, E1: 28 named slices, about 30 PRs. Milestones: M0 to M5 (6).

## 10. Findings in the requirements (for the user)

- F1 Resolved in the requirements (2026-11-16): REQ-52 and REQ-61 use a pid-and-start-time file in both languages, because Node has no portable `flock` and a native addon would break the single-file bundle (REQ-70). `resume` claims a journal with an exclusive (`O_EXCL`) pid-file create. The stale-replace sequence (rename aside, verify, create exclusively) is now pinned in `contract/runtime.md` "Pid file and claim"; a residual window remains and is for I4 to settle by test (S-52j). The user may overrule at approval; the conformance scenarios are S-52b and S-52f.
- F2 REQ-80 says move to data-file scenarios beyond about 30; section 5 lists about 90, so the threshold is already exceeded.
- F3 REQ-43 delegates the classifier to this plan; section 5.1 specifies a starting version, which is [assistant] and unmeasured until the REQ-46 replay test has run.
- F4 REQ-72 versus ADR 0003 section 6: "failed acceptance runs" and "fix-cycle counts are not a measure" overlapped; resolved at the gate walk: the four measures use the narrow definition (suite failures on first submission of one slice).
- F5 Resolved by REQ-61: the project key is the server's `CLAUDE_PROJECT_DIR`, which is in the server environment [verified: spike 1]; C1 records how the suite injects it and the fallback when it is absent.
