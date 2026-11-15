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
