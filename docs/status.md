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

- Done: on the user's instruction, filed 6 milestones (M0 to M5), 31 issues in kaybenleroll/decision-register (#2 to #34: 7 decision gates G0 to G6 labelled needs-user, 23 slices, tracking issue #34) and 5 dotfiles issues D1 to D5 (kaybenleroll/dotfiles #552 to #556, each referring to #34). `ready` on C1 to C5, X1, V1 to V4; `provisional` on the rest. Record: .scratch/issues-created.tsv (untracked).
- Not done: no code; docs/requirements.md is not yet approved by the user; ADRs 0002 and 0003 remain Proposed. PR #3 open, not merged.
- Awaiting the user: requirements approval and the gate decisions (issues labelled needs-user). Gates G4 and G6 carry no recommendation in the plan.

## 2026-11-16 (gate decisions folded)

- Done: folded the user's gate-walk decisions of 2026-11-16 into docs/requirements.md, ADR 0002, ADR 0003 and docs/build-plan.md, tagged `[user: gate walk 2026-11-16]`: purpose (REQ-1, REQ-2); grill-me skill stays in dotfiles with a register mode and plain fallback (REQ-12, REQ-15, REQ-24 list = `grill-me`, REQ-51); per-prompt budget, one `source: user` answer per presented item, minimum 1, flat 4 fallback (REQ-32); N = 2, `decision` kind and finish classifier confirmed as starting values, classifier names presented items (REQ-34, REQ-35, REQ-43); new REQ-45 (full tree from `status`, open items in every Stop reason) and REQ-46 (classifier replay test); REQ-72 values confirmed as starting values; names parked to the release milestone (REQ-73). Build plan: gates G1 to G5 decided, G6 parked, G0 open; new slices P5 (skill register mode, dotfiles side) and V7 (compaction injection, [open]); P2 folded into P5; D4 and D5 rewritten.
- Not done: requirements and ADRs are not approved (DRAFT / Proposed); approval is the user's after reviewing the diff. The issues filed earlier (#2 to #34, dotfiles #552 to #556) predate these changes and are not updated.
- Not verified: compaction injection (V7), plus the earlier open verify-in-build items.

## 2026-11-16 (stress-test pass 2 done; lifecycle revised)

- Done: `/stress-test` pass 2 on docs/requirements.md after the gate decisions were folded in (three reviewers, 48 raw findings, about 24 after hand-dedup; none self-assigned RESTRUCTURE). All P1 findings patched in place through one lifecycle state table (REQ-27): hook-started interviews that the skill never engages are stood down; the gate allows every turn once an interview is complete; a graceful server exit leaves the journal resumable; the session id comes from the last hook call seen, not from the model. Log entry at the top of the requirements file. Build plan, ADR 0002 and ADR 0003 updated to match; new verification slices V8 to V10.
- Verified: spike 6 (live): `${session_id}` substitutes into UserPromptSubmit and Stop hooks and equals claude's session id (two runs); text returned by a prompt hook is not shown to the model; a failing `mcp_tool` hook shows no error in `--output-format json` (stream-json and interactive untested). Notes: docs/evidence/spike6/. Hand-traces for the new lifecycle rules are in the log entry.
- Not verified: V8 to V10 (hook output for expansion and PreToolUse hooks; skill-name form for a user-level skill; UserPromptSubmit-before-start ordering); REQ-53 interactive error visibility; compaction and session id; the engagement rule's 2-Stop threshold.
- For the user, at approval: REQ-13 ([user]) now has an [assistant] bookkeeping exception; `begin_interview` is refused while an unfinished journal exists; REQ-35 counts children of any status (no 'withdrawn' status exists); the 34 requirements still tagged [assistant] are listed in section 12.
- Issues: #2 to #34 and dotfiles #552 to #556 predate this revision and still need updating; not done yet.
- Nothing merged. PR #3 open.
