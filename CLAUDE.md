# decision-register

**Phase: build and release.** docs/requirements.md and ADR 0002 exist; the Rust and TypeScript builds, the plugin and the conformance suite are in the repository. v0.1.0 to v0.1.3 are released (v0.1.3: `tool_schema` 5, owner-aware register, tagged 2026-11-26; unit #128). Requirements changes need the user's approval; see docs/status.md and docs/release-plan.md.

- Purpose: build the structured decision register for grill-me. Inputs: docs/requirements-inputs.md; history: docs/adr/0001-structured-decision-register.md, docs/plan.md (Python-port plan, partly superseded); reference code: reference/armG/ (read-only, reference only).
- Work order (step 0 first, then steps 3-4 of docs/plan.md): SUPERSEDED by the requirements and build work (docs/requirements.md, docs/build-plan.md).
- Do NOT run the paid evaluation (plan steps 6-8) here; that happens in the research repo.
- Do NOT change [user] decisions in the ADR/plan; flag conflicts in docs/status.md.
- Workflow: feature branches only; no commits to main; one PR per logical unit; no push or PR without the user's say; verify claims by running.
- Merge method (project rule; overrides the global issue-count rule): `--merge` when the branch has two or more commits that each stand alone (a logical step that is green and could be bisected to or reverted separately), whatever the number of closing issues; `--squash` for a single commit, a docs-only change, or uncurated work-in-progress, fixup or review-noise commits. Fold fixups before merging so the history stays curated.
- Skill/hook/settings.json work goes in kaybenleroll/dotfiles under their HARD RULE; do not edit ~/.claude here. Umbrella: `kaybenleroll/random_llm_projects#203`.
- Coordination with the research session is file-based: append a dated entry to docs/status.md after each completed step.
- Temp files in .scratch/, not /tmp/. Count/list claims about structured data come from scripts. UK English in prose.
