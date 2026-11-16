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
