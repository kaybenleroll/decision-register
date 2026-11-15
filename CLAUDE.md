# decision-register

- Purpose: build the structured decision register (MCP server + hooks plugin) for grill-me. Plan of record: docs/plan.md; decision: docs/adr/0001-structured-decision-register.md; reference code: reference/armG/ (read-only snapshot).
- Work order: step 0 first (live checks of hook firing and hook scoping under `claude -p`, ~USD 0.07 estimate), then plan steps 3 and 4 (port arm G code, plugin packaging) per docs/plan.md requirements R1-R5.
- Do NOT run the paid evaluation (plan steps 6-8) here; that happens in the research repo.
- Do NOT change [user] decisions in the ADR/plan; flag conflicts in docs/status.md instead.
- Thresholds, N=2 and the control arm (OQ1) are open and belong to the user.
- Workflow: feature branches only; no commits to main after the initial seed; one PR per logical unit; tests green per commit; run tests before claiming done; verify claims by running.
- No push or PR without the user's say.
- Skill/hook/settings.json work in the user's dotfiles goes in kaybenleroll/dotfiles under their HARD RULE; do not edit ~/.claude here.
- Register code issues live in this repo; #203 stays the umbrella in kaybenleroll/random_llm_projects (refer to it as `kaybenleroll/random_llm_projects#203`).
- Temp files in .scratch/, not /tmp/.
- Coordination with the research session is file-based through docs/status.md: append a dated entry after each completed step (what was done, what was verified/not, blockers, questions for the user).
- Count/list claims about structured data (JSON, JSONL, YAML) come from scripts, not reading.
- Write tests for each R1-R5 requirement per plan step 3.
- UK English in prose.
