# Build plan: structured decision register

Status: DRAFT, gate decisions folded 2026-11-16, awaiting the user's review of the diff and approval. (Issues for the earlier draft were filed; slices P5 and V7 and the changes below are not yet reflected in them.) No source code until the user approves `docs/requirements.md` (gate G0).
Sources: `docs/requirements.md` (REQ ids), ADR 0002, ADR 0003, `docs/evidence/spikes.md`, `docs/plan.md` (kept: the cap constants and the step-0 style live checks; dropped: the Python port, the paid evaluation, harness work, all owned elsewhere, REQ-6).
Tags: [user-decision] marks a step that cannot start until the user decides.

## 1. Rules that shape the plan

- One slice = one PR = one nameable capability (user's PR-unit rule). A slice that outgrows about 800 changed lines splits.
- The suite goes first and is red against every server until the slice that makes it green. Each implementation PR states the scenario ids it turns green; no scenario is edited in the same PR that makes it pass, except to fix a suite bug (flagged in the PR).
- Rust (`impl/rust/`) and TypeScript (`impl/ts/`) are separate PRs per slice, same scenario set (REQ-71). Alternation (ADR 0003 section 4, step 2): slice I1 Rust first, I2 TypeScript first, I3 Rust, I4 TypeScript, I5 Rust. The second language starts from the first's merged design. One slice per submission, so conformance failures on first submission are countable (ADR 0003 section 6 recommendation; [user-decision] on that definition, settled at the gate walk).
- Each implementation PR appends its row to `docs/evidence/language-experiment.md` at the time (tokens, first-submission suite failures, date), never reconstructed (REQ-72).
- Spend: the paid evaluation is out (REQ-6). Only the smoke suite spends (slice P4, about USD 0.25 per run); anything beyond about USD 1 needs the user (REQ-3).
- Layout proposal: `plugin/` (REQ-12), `.claude-plugin/marketplace.json` at root, `conformance/`, `contract/`, `impl/rust/`, `impl/ts/`, `smoke/`, `docs/evidence/`.

## 2. Gates needing the user

| Gate | Decision | Outcome | Blocks |
|---|---|---|---|
| G0 | Approve `docs/requirements.md` and ADRs 0002 and 0003 (all remaining [assistant] clauses in requirements section 12) | [open] pending the user's approval after review of the diff | everything |
| G1 | ADR 0002 5.2 per-prompt authorisation budget | Decided [user: gate walk 2026-11-16]: one `source: user` answer per distinct item presented since the previous prompt, minimum 1, flat 4 fallback | was S-32b..d; slice I2 |
| G2 | REQ-15 skill home | Decided [user: gate walk 2026-11-16]: skill stays in dotfiles with a register mode and plain fallback; plugin has no skill text | was P1 skill content, D4; now slice P5 |
| G3 | REQ-72 and ADR 0003 values | Decided [user: gate walk 2026-11-16]: 20% margin, Rust tie-break within 20%, four measures confirmed as starting values; the user decides either way | was start of I1 |
| G4 | REQ-24 list contents | Decided [user: gate walk 2026-11-16]: exactly `grill-me`; revisit in P5 | was S-20c; P1 |
| G5 | N = 2 (REQ-35), `decision` kind (REQ-34), finish classifier (REQ-43) | Decided [user: gate walk 2026-11-16]: confirmed as starting values; classifier also names presented items; replay test REQ-46 before it ships | was C2 batch |
| G6 | Product and binary names (working names `regspike-rs`, `regspike-ts`, REQ-73) | Parked [user: gate walk 2026-11-16]: decided at the release milestone (R1) | R1 release naming |

## 3. Milestones (proposed; not to be filed until reviewed)

| Milestone | Slices | Exit |
|---|---|---|
| M0 Contract and suite | C1 to C5, X1 | Suite runs; every scenario red against the stub; schema file frozen at `tool_schema` 1 |
| M1 Core register | I1R, I1T, I2T, I2R | Data model, transitions, status, hook-facing tools and finish gate green in both languages |
| M2 Lifecycle and durability | I3R, I3T, I4T, I4R, I5R, I5T | Full suite green in both; REQ-90 measured |
| M3 Plugin and verification | P1, P5 (dotfiles side), V1 to V7 (can run once P1 exists, in parallel with M1/M2; V7 is an upgrade, not a dependency) | Plugin loads from a local clone; open items of requirements section 12 each have a recorded outcome |
| M4 Smoke and release | P2, P3, P4, R1 | Smoke suite under USD 0.25; GitHub release by tag with both implementations; private marketplace install tested |
| M5 Experiment close-out | E1 (after five real changes; [user-decision]) | ADR 0003 retirement procedure executed |

Dotfiles issues (section 8) are a separate track, filed only on the user's instruction.

## 4. Conformance suite slices (M0)

Stack: Python 3 stdlib only (REQ-80), starting from `docs/evidence/spike3/acceptance.py` (A1 to A7 and its `Server` stdio driver).

### C1 Tool list, schemas, result shapes
- Goal: freeze the contract all three artefacts (suite, both servers, plugin hooks) share.
- REQ: REQ-40, REQ-77, REQ-31, REQ-60, REQ-33, REQ-45.
- Deliverables: `contract/tools.json` (JSON Schema per tool input and result), `contract/journal.md` (event kinds, `v: 2`), `contract/CHANGES` rule that any change bumps `tool_schema`.
- Tool list (REQ-40, final): model-facing `begin_interview`, `log_item`, `resolve`, `park`, `reopen`, `status`, `resume`, `discard`, `end_interview`; hook-facing `note_prompt`, `skill_started`, `check_stop`. `log_item` args: `kind`, `text`, `parent?`, `depends_on?`, optional volunteered `answer`, `source`, `rationale`. `park`: `id`, `reason`, `parked_by`. `begin_interview`/`skill_started`: `tool_schema`, `session_id`, skill name; `begin_interview` also `mode`. `check_stop`: `session_id`, `last_assistant_message`. `end_interview`: `abandon?`, `reason?`. `status` returns `server_version`, `tool_schema` (integer, initial 1), the full tree (every item with id, text, parent, status, answer, `parked_by`; open items first, REQ-45), item counts, hook-health counts, last event number, `resumable`.
- Acceptance: (a) every schema validates a hand-written good and bad example per tool (stdlib checker, no `jsonschema` dependency); (b) `tool_schema` is 1 and documented; (c) the spike-3 tool names used by `acceptance.py` are either kept or the script change is listed; (d) run-time contract section states process start: argv, how the suite injects the data directory, project directory, clock offset for pruning (REQ-61), and how `_meta` is sent.
- Depends on: G0. Drop rule: none; blocking for everything.

### C2 Suite harness and port of A1 to A7
- Goal: reusable runner with scenario ids, per-scenario temp dirs in `.scratch/`, `--only`, `--list`, TAP-like output and a non-zero exit on failure.
- REQ: REQ-80, REQ-71.
- Acceptance: `acceptance.py` A1 to A7 pass unchanged in meaning against both spike-3 servers (their recorded outputs in `docs/evidence/spike3/acceptance-*.txt` are the oracle); the runner can send a call with and without `_meta`; a scenario count above 30 triggers the data-file form (REQ-80): decision recorded in the PR, recommendation to adopt data files from the start because section 5 lists about 60 scenarios [user-decision].
- Depends on: C1. Drop rule: if data-file format stalls, keep Python functions and defer the format to C5.

### C3, C4, C5 Scenario batches
Split by area so each PR is reviewable; all are written red (no implementation exists), then validated against a scripted fake server (`conformance/fake/`, per-scenario canned replies) to prove the scenario can pass and can fail, which stops a vacuous suite.

| Slice | Scenario ids (section 5) | Acceptance | Depends on | Drop rule |
|---|---|---|---|---|
| C3 Data model, transitions, status | S-30..S-38, S-45a | Each scenario has a failing run on a mutation of the fake and a passing run on the fake | C2, G5 | S-34, S-35 follow G5 outcome |
| C4 Hook-facing tools, finish gate, caps | S-40..S-44, S-32, S-45b, S-45c | Includes the `_meta` forgery pair, the classifier table of 5.1 and the budget scenarios S-32b..d (G1 decided) | C2 | None (G1 decided) |
| C5 Lifecycle, session, journal, resume, log | S-20..S-26, S-50..S-52, S-60..S-63, S-77 | Journal scenarios read files only through the data directory the suite supplied | C2, G4 | S-20c wording per G4 |

### X1 Language-experiment record
- Goal: create `docs/evidence/language-experiment.md` (header, per-change table, alternation order) and the pre-build retirement rule values from G3.
- REQ: REQ-72. Acceptance: file exists with columns from ADR 0003 section 4 step 3; ADR 0003 status moves to Accepted by the user. Depends on: G3. Drop rule: none (REQ-72 requires it before the real build).

## 5. Scenario list (every behavioural REQ)

Ids are stable; one row = one suite scenario. The seven spike-3 scenarios (A1 to A7, see `docs/evidence/spike3/CHANGES.md`) are kept as S-A1..S-A7; their REQ ids are in the S-A row.

| Scenario | REQ ids |
|---|---|
| S-20a typed-start route: `skill_started` with an exact listed name starts an interview | REQ-20, REQ-24 |
| S-20b unlisted or near-miss name (`grill-me-copy`, case change, `wayfinder`, `write-a-prd`) does not start; exactly `grill-me` does | REQ-20, REQ-24 |
| S-20c model-invoked route yields same interview id | REQ-20 |
| S-21a `begin_interview` starts; second call returns existing id | REQ-21, REQ-22 |
| S-21b implicit start on `log_item` with no journal; flagged in log | REQ-21 |
| S-21c hook-facing tools, `status`, `resume`, `end_interview` never start an interview | REQ-13, REQ-21 |
| S-22a start resets counters, caps and stamps a new id | REQ-22 |
| S-22b start while active is a no-op returning the id, state intact | REQ-22 |
| S-22c new start archives only the server's own earlier journal | REQ-22, REQ-61 |
| S-23a allowed finish attempt writes the log; interview stays active, journal kept, complete mark set | REQ-23, REQ-63 |
| S-23b `reopen`/`log_item` after complete clears the mark; no implicit start | REQ-21, REQ-23 |
| S-23c `end_interview` with items open refused; with `abandon` and reason accepted; log says "ended incomplete by the model" | REQ-23, REQ-50 |
| S-23d log file name carries the interview id; second interview does not overwrite | REQ-23 |
| S-23e allowed finish because caps are spent still writes the log | REQ-23, REQ-42 |
| S-26a different `session_id` on a hook call ends the old interview as abandoned, journal archived, gate inactive | REQ-26 |
| S-26b start after session change ends old then starts new (check precedes REQ-22 no-op) | REQ-22, REQ-26 |
| S-26c same-session repeated hook calls do not end it | REQ-26 |
| S-30a item fields round-trip through `status` | REQ-30 |
| S-31a resolve without answer, source or rationale rejected; whitespace-only rejected; multi-line rationale rejected | REQ-31 |
| S-31b replay applies the same check and fails resume naming the line | REQ-31, REQ-60 |
| S-32a `source=user` rejected with no prompt since last model action | REQ-32 |
| S-32b volunteered decision in `log_item` accepted only after a prompt since previous Stop call; it counts against the per-prompt budget | REQ-32 |
| S-32c budget: after a turn presenting k distinct items and one prompt, k `source=user` answers are accepted and the (k+1)th rejected; a turn presenting none still allows 1 | REQ-32 |
| S-32d fallback: when the classifier cannot name the presented items, 4 `source=user` answers are accepted after one prompt and the 5th rejected | REQ-32, REQ-43 |
| S-33a park records `parked_by` and reason; model park keeps blocking; user park does not | REQ-33 |
| S-33b `parked_by=user` without a prompt rejected | REQ-33 |
| S-34a `decision` resolved only with `source=user`; parked only with `parked_by=user` | REQ-34 |
| S-35a `log_item` never rejects on branch size; finish gate flags under-sized parent; `resolve` of parent with fewer than 2 children rejected | REQ-35 |
| S-35b `subquestion` without parent rejected; `question` with parent accepted | REQ-35 |
| S-36a reopen makes dependants (direct and transitive) Stale; Stale blocks finish; resolvable again | REQ-36 |
| S-38a transition table: every allowed and disallowed pair, error names id and current status | REQ-38 |
| S-38b model-parked item resolvable; re-park with `parked_by=user` | REQ-33, REQ-38 |
| S-40a `note_prompt`, `skill_started`, `check_stop` with `_meta.claudecode/toolUseId` rejected; same calls without it accepted | REQ-40 |
| S-40b model-facing tools accept calls with `_meta` | REQ-40 |
| S-40c forged `note_prompt` does not enable `source=user` | REQ-32, REQ-40 |
| S-41a prompt counter advances per `note_prompt` | REQ-41, REQ-44 |
| S-42a caps: 2 blocks per prompt, 8 to finish, 12 unlogged; fail open after; counters per interview | REQ-42 |
| S-43a finish attempt (no `?`) with Open, Stale, model-parked item or under-sized branch: block; none open: allow | REQ-43 |
| S-43b question turn about a logged open item: allow; about an unlogged question: block | REQ-43 |
| S-43c classifier table (6.1), including empty and very long messages | REQ-43 |
| S-43d no interview active: always allow | REQ-13, REQ-43 |
| S-43e classifier returns the logged items a question turn presented (feeds S-32c); empty list triggers the S-32d fallback | REQ-43, REQ-32 |
| S-44a user-sourced answer with zero hook calls seen: error says hooks are not firing | REQ-44 |
| S-45a `status` returns the full tree: every item with id, text, parent, status, answer, `parked_by`; open items first | REQ-45 |
| S-45b Stop block reason names each open item with id and text | REQ-45, REQ-43 |
| S-45c with 12 open items the reason lists the first 10 only and says 2 more; item text over 120 characters is truncated to 120 | REQ-45 |
| S-50a registered tool failure is an error result that does not abort the server (fail open) | REQ-50 |
| S-52a unfinished journal with dead owner: `status` shows `resumable`; mutating call refused with "unfinished journal: call resume or discard" | REQ-52 |
| S-52b live owner (held lock): journal not offered | REQ-52, REQ-61 |
| S-52c `resume` omitted id with one journal; several journals list and require id; `discard` archives | REQ-52 |
| S-52d resume reconstructs state and last event number; loss since last Stop visible | REQ-52, REQ-60 |
| S-60a every event has `v: 2`, `ts`, sequence number; flush at each `check_stop` and end | REQ-60 |
| S-60b v1 events (no `v`) replay; `v: 3` fails resume with 1-based line number, state unchanged | REQ-60 |
| S-61a journal under `CLAUDE_PLUGIN_DATA` (suite-set), fallback to temp dir; mode 0600 | REQ-61, REQ-91 |
| S-61b archive on end; archives over 30 days pruned at start (clock offset) | REQ-61 |
| S-61c two servers, one project: no shared or archived journals | REQ-61 |
| S-62a log location: `.scratch/` if present else `decision-logs/` with `.gitignore` `*`; mode 0600; contents (tree, sources, parked_by, reopen history, implicit flag, skill mode, hook counts, journal position) | REQ-62, REQ-91 |
| S-63a log journal position matches `status`; stale after later event | REQ-63 |
| S-77a `status` returns `server_version`, integer `tool_schema` | REQ-77 |
| S-77b `begin_interview` and `skill_started` with wrong `tool_schema`: error naming both values | REQ-77 |
| S-A1..S-A7 spike-3 scenarios (kept) | REQ-36, REQ-34, REQ-42, REQ-60, REQ-32 |
| S-90a start under 1 s and idle RSS under 200 MB (reported, not a hard CI gate) | REQ-90 |

Non-scenario REQs (design, process, distribution, tooling) are covered by slices, not the suite: REQ-1 to REQ-6, REQ-10 to REQ-14 (S-21c, S-43d cover the observable part), REQ-15 (slice P5), REQ-37, REQ-46 (classifier replay, slice I2), REQ-51, REQ-53, REQ-70 to REQ-76, REQ-81 to REQ-83, REQ-92, REQ-93.

### 5.1 Finish-attempt classifier (REQ-43 spec, [assistant], G5 decided)
The build plan fixes the heuristic as a table in `contract/classifier.md` so both languages implement the same one: finish attempt = no `?` after stripping fenced code and quoted lines; question turn otherwise; question "concerns a logged item" if its text shares at least 3 words, ignoring stop words, with an Open or Stale item (starting values, testable). The same overlap rule names the items a question turn presented (all Open or Stale items it matches; an empty result triggers the flat-4 fallback of REQ-32). Replay test (REQ-46): before the classifier ships, a script runs it over the question turns of stored grill-me transcripts and writes `docs/evidence/classifier-replay.md`, listing each turn with its classification and named items. Reference behaviour: `reference/armG/register_store.py:716-730` (read-only).

## 6. Implementation slices (M1, M2)

Each slice: one PR per language; goal; REQ; green scenarios; offline unit tests per REQ-81; dependency; drop rule. "R" = Rust, "T" = TypeScript on Node with esbuild single-file bundle.

| Slice (first language) | Goal | REQ | Scenarios turned green | Depends on | Drop rule |
|---|---|---|---|---|---|
| I1 (R first) core register: MCP stdio server, `begin_interview`, `log_item`, `resolve`, `park`, `reopen`, `status`, data model, transition table, Stale propagation | The register as a state machine, no hooks | REQ-30..REQ-38, REQ-31 interface check, REQ-45 (`status` tree) | S-30a, S-45a, S-31a, S-33a, S-34a, S-35, S-36a, S-38, S-A1..S-A4 | C1, C3 | If a language cannot make Resolved-without-answer unrepresentable, enforce at interface only (REQ-31) and note it in the experiment file |
| I2 (T first) hook-facing tools and finish gate: `note_prompt`, `skill_started`, `check_stop`, `_meta` guard, caps, classifier, user-answer rule | Enforcement. Acceptance: the classifier replay test (REQ-46) has run over question turns in stored grill-me transcripts and its report is committed before the classifier is accepted; the Stop reason lists open items (REQ-45) | REQ-32, REQ-40, REQ-41, REQ-42, REQ-43, REQ-44, REQ-45, REQ-46 | S-32, S-40, S-41, S-42, S-43, S-44, S-45b, S-45c, S-A5 | I1 (same language), C4 | Classifier word-overlap rule revised if S-43c or the replay report shows it unstable |
| I3 (R first) lifecycle: start routes, session binding, no-op start, implicit start, complete mark, `end_interview`, version handshake | Interview lifecycle | REQ-20..REQ-26, REQ-50, REQ-77 | S-20, S-21, S-22, S-23, S-26, S-50a, S-77 | I2 | Implicit start (REQ-21 second fallback) dropped if the user vetoes at G0 |
| I4 (T first) journal, resume, discard, pid-file liveness check, archive, pruning, decision log | Durability and output | REQ-52, REQ-60..REQ-63, REQ-91 | S-52, S-60, S-61, S-62, S-63, S-A6, S-A7 | I3 | See finding F1 (Node has no flock): if no lock primitive, fall back to pid-file plus liveness probe in TS only and document the difference |
| I5 (R first) non-functional and parity: REQ-90 measurement, both builds reproducible, README for the contract | Close the suite | REQ-90, REQ-71 | S-90a; full suite 100% | I4 both languages | None; REQ-90 breach is recorded against the candidate, not a drop |

Order of PRs: I1R, I1T, I2T, I2R, I3R, I3T, I4T, I4R, I5R, I5T.

## 7. Plugin, verification, smoke, release (M3, M4)

### P1 Plugin skeleton
- Goal: `plugin/` with `.claude-plugin/plugin.json`, `.mcp.json` (server `regspike`), launcher `plugin/bin/regspike-launch` (REQ-73, REQ-77), `hooks/hooks.json` (UserPromptSubmit `note_prompt`; Stop `check_stop` with `${last_assistant_message}`; UserPromptExpansion and PreToolUse `Skill` to `skill_started`, matching exactly `grill-me`), root `.claude-plugin/marketplace.json`. The plugin contains no skill text [user: gate walk 2026-11-16] (REQ-12, REQ-15).
- REQ: REQ-11, REQ-12, REQ-13, REQ-20, REQ-41, REQ-73, REQ-75, REQ-77, REQ-24.
- Acceptance: `claude --plugin-dir plugin/` from a directory outside the plugin lists the three hook-facing and nine model-facing tools; a session at the repository root does not load `.mcp.json` as a project server (REQ-12); launcher with the binary absent exits cleanly with message "register unavailable" once; `REGSPIKE_IMPL=ts` selects the TypeScript bundle; no `SKILL.md` or skill text exists under `plugin/`. Hook inputs carry the `tool_schema` the plugin requires.
- Depends on: C1, one passing implementation (I3 minimum for hooks); G2 and G4 are decided. Drop rule: if `${REGSPIKE_IMPL}` expands in the start command (V2), delete the launcher's selection branch but keep its missing-binary handling.

### V1 to V7 Build-time verification tasks (requirements section 12 open items)
Each is a script under `smoke/verify/` writing `docs/evidence/verify-<id>.md` with command, version, result, USD. Single-trial results are labelled single-trial. Each depends on P1 and a server that passes at least I3 unless stated; none blocks the others. Total expected cost under USD 0.30 on the cheapest model.

| Id | Check | REQ | Acceptance | If it fails |
|---|---|---|---|---|
| V1 | Hook error visibility: a failing `mcp_tool` hook (binary missing, server not connected) shows or hides an error in `claude -p` and interactive | REQ-53, REQ-13, REQ-77 | Outcome recorded (visible, hidden, blocking) | Visible/blocking: tighten launcher and document install order [user-decision] |
| V2 | Start-command variable expansion of `${REGSPIKE_IMPL:-rs}` in `.mcp.json` | REQ-73 | Server starts with the chosen binary in both cases | Keep launcher selection (P1 drop rule) |
| V3 | Compaction and `session_id`: run a long enough session, or `/compact`, and compare `session_id` seen by hooks before and after | REQ-26, REQ-22 | Recorded equal or different | If different: REQ-26 would end the interview at compaction; change rule before I3 merges [user-decision] |
| V4 | Interactive `/clear` and `/compact` (spike 1 was stream-json `-p` only): server state survives, new `session_id` after `/clear` | REQ-10, REQ-22, REQ-26 | Manual script with pause gates (needs a tty; the user runs it once) | Update REQ-10 and S-26 text |
| V5 | Empty `last_assistant_message` (tool-only final turn) and short messages through `${last_assistant_message}` | REQ-41, REQ-43 | Result recorded; S-43c includes the observed form | Gate treats empty as finish attempt or allow, chosen by result [user-decision] |
| V6 | `SessionEnd` hook can call `end_interview` | REQ-26, REQ-23 | Recorded yes or no | If no: rely on REQ-26 session-change detection only |
| V7 [open] | Compaction injection (an upgrade to REQ-45, not a dependency): can a hook on compaction (`PostCompact`, `SessionStart` with `source=compact`, or similar) inject the tree into context; does the model retain the skill text after compaction | REQ-45, REQ-1 | Each of the two questions answered yes, no or not testable, single-trial labelled | If no: REQ-45's `status` and Stop-reason recovery stands alone; nothing else changes |

V3 is needed before I3 merges; V5 before I2 merges. V7 is not needed by any slice and is excluded from the cost figure above. Others any time before P4.

### P2 Skill integration and fallbacks (superseded by P5; id kept)
- [user: gate walk 2026-11-16] The skill stays in the dotfiles repository, so this slice has no repository-side PR; its content moved to P5 and dotfiles drafts D4 and D5. Resume prompts per REQ-52 are part of P5.

### P5 Skill register mode (dotfiles side)
- Goal: add the register mode with a plain fallback to the existing grill-me skill in the dotfiles repository (drafts D4, D5): first step is a call to `status`; if it answers and `tool_schema` matches, run in register mode (log items, resolve, walk open branches, `begin_interview` carrying `mode`, resume prompts per REQ-52); if absent, error or mismatch, run the existing plain interview and tell the user once that no record is being kept (REQ-51). Also check, in the first integration, whether `wayfinder` or `write-a-prd` run question rounds without calling grill-me (REQ-24).
- REQ: REQ-14, REQ-15, REQ-21, REQ-24, REQ-50, REQ-51, REQ-52, REQ-62, REQ-63.
- Acceptance (live runs, cost declared and under USD 1, REQ-3): (1) one live run with the plugin loaded: register mode, and the decision log shows the mode as `register`; (2) one live run without the plugin: plain fallback, the transcript shows the notice that no record is being kept, and the interview completes; with a resumable journal the skill asks resume or discard and never proceeds silently.
- Depends on: P1, one passing implementation (I3 minimum); the work is filed in the dotfiles repository on the user's instruction only. Drop rule: none; REQ-15 is decided.
### P3 Smoke fixtures
- Goal: scripted prompts and a minimal skill stub used by P4 (the real skill lives in the dotfiles repository, REQ-15). REQ-82. Acceptance: fixtures run offline against the fake server. Depends on: P1. Drop rule: merge into P4 if under 100 lines.

### P4 Live smoke suite
- Goal: `smoke/run.sh` runs `claude -p` on the cheapest model, from a directory other than the plugin directory (REQ-82; spike 2 invalidation).
- REQ: REQ-82, REQ-92, REQ-13, REQ-20, REQ-41, REQ-42, REQ-51, REQ-12.
- Acceptance (each a check printed PASS/FAIL, with a `--max-budget-usd` guard per call and a running total): (1) plugin loads and tools appear; (2) hooks fire (`status` hook counts non-zero); (3) Stop block works on an unlogged finish; (4) interview starts by typed `/skill` route; (5) starts by model-invoked Skill route; (6) scoping: a question in an unrelated session is never blocked; (7) server-absent run shows the notices; (8) whole run under USD 0.25, total printed. Declared cost in the PR; a run over USD 0.25 fails the suite.
- Depends on: P1, fixtures P3 (a stub skill; P5 is exercised by its own two live runs), one passing implementation. Drop rule: cases (4) or (5) dropped if V1 or V4 show the route is unavailable in the pinned Claude Code version, recorded in REQ-92.

### R1 Release and CI
- Goal: GitHub Actions: on pull request run the suite and unit tests for both languages; on tag build `regspike-rs` (Linux x86_64 only, REQ-74) and `regspike.mjs` and attach to a GitHub release; the plugin version and `tool_schema` are asserted equal across plugin and binary before release (REQ-77).
- REQ: REQ-73, REQ-74, REQ-75, REQ-77, REQ-12, REQ-71.
- Acceptance: a draft release contains both assets and checksums; installing from the private marketplace on a second clone (`/plugin marketplace add` with the user's git credentials) lists the plugin; unit and suite jobs fail on a seeded failure. Smoke suite is on demand only, not in CI (spend). Depends on: I5 both, P1. Checklist item [user: gate walk 2026-11-16]: rename the working names (`regspike-rs`, `regspike-ts`, REQ-73) or confirm them before the first release tag; G6 is decided here. Drop rule: macOS and Windows never (REQ-6); Rust cross-build dropped (REQ-74). Needs a decision if the private install fails under background credentials [user-decision].

### E1 Experiment close-out
- Goal: after five real changes with both rows filled, run the ADR 0003 retirement procedure: tag, archive, remove from CI, release and launcher.
- REQ: REQ-70, REQ-72. Acceptance: ADR 0003 records the user's decision and date; loser removed from `.github/` and the release assets; suite stays. Depends on: R1, five rows in `docs/evidence/language-experiment.md`. Drop rule: none; the user may extend the experiment.

## 8. Dotfiles issue drafts (not filed; target repository `kaybenleroll/dotfiles`)

Filed 2026-11-16 as kaybenleroll/dotfiles #552 to #556, one per issue, under the user's HARD RULE (dotfiles repository owns `~/.claude`).

| Draft | Title | REQ | Content | Order |
|---|---|---|---|---|
| D1 | Install the decision-register plugin and binaries | REQ-73, REQ-75, REQ-76 | Put `regspike-rs` and `regspike-ts` on the PATH from the GitHub release (chezmoi external or run script); document the credential helper requirement; set `REGSPIKE_IMPL` default | After R1 |
| D2 | Marketplace and enabled plugin in `settings.json` | REQ-76, REQ-75 | `extraKnownMarketplaces` entry for the private repository; `enabledPlugins` entry; applied by chezmoi template `dot_claude/settings.json.tmpl` | After P1 |
| D3 | Allow rules for the plugin's tools | REQ-76 | `mcp__plugin_<plugin>_<server>__*` model-facing allow rules (not the hook-facing ones, which hooks call without permission); keep `mcp__sequential-thinking` for other skills (REQ-5) | After P1 |
| D4 | Add the register mode with plain fallback to the existing grill-me skill (REQ-15) | REQ-15, REQ-24, REQ-51, REQ-6 | [user: gate walk 2026-11-16] Replaces the earlier 'retire the copy / move to plugin' draft. The skill stays in dotfiles; first step calls `status`; register mode if it answers with the expected `tool_schema`, otherwise the existing plain interview with a once-only notice that no record is being kept; `begin_interview` carries `mode`; resume prompt per REQ-52. Acceptance as slice P5 (one live run with the plugin, one without) | After P1 and one passing implementation |
| D5 | Plain-fallback wording in the grill-me text | REQ-51 | "required" to "preferred", the once-only no-record notice, resume prompt. Always applies now (the skill is no longer in doubt); may be one PR with D4 | With D4 |

(The user's gate walk referred to this change as D2; the retire-or-move draft was D4 and D2 is the marketplace entry, left unchanged.)

## 9. Slice and milestone counts

Slices: C1 to C5 (5), X1, I1 to I5 (5, each in two languages = 10 PRs), P1 to P5 (5; P2 is folded into P5, so four have PRs and P5 is dotfiles-side), V1 to V7 (7, may share one PR), R1, E1: 25 named slices, about 30 PRs. Milestones: M0 to M5 (6).

## 10. Findings in the requirements (for the user)

- F1 Resolved in the requirements (2026-11-16): REQ-52 and REQ-61 now use a pid-and-start-time file in both languages, because Node has no portable `flock` and a native addon would break the single-file bundle (REQ-70). The user may overrule at approval; the conformance scenario is S-52b.
- F2 REQ-80 says move to data-file scenarios beyond about 30; section 5 lists about 60, so the threshold is already exceeded.
- F3 REQ-43 delegates the classifier to this plan; section 5.1 specifies a starting version, which is [assistant] and unmeasured until the REQ-46 replay test has run.
- F4 REQ-72 versus ADR 0003 section 6: "failed acceptance runs" and "fix-cycle counts are not a measure" overlapped; resolved at the gate walk: the four measures use the narrow definition (suite failures on first submission of one slice).
- F5 REQ-61 says "the project" keys the journal, but the server learns the project only from its working directory or the hook payload; C1 must fix which. Not stated in the requirements.
