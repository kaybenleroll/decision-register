# Requirements inputs

Inputs for a requirements interview. These are NOT requirements. Tags: [user] the user's words or decision; [assistant] a draft for the user to edit; [assistant: stress-test] found by the two stress-test passes in docs/plan.md; [verified] / [assistant: subagent] as tagged in docs/plan.md. No [assistant] item is the user's choice.

## A. What the user wants

End product (carried from ADR 0001 context):
- [user] A structured decision register for grill-me. Structured data captured during the interview gives a plan, documentation and a decision record "for free".
- [user] Enforcement moves out of grill-me prose into the tool.
- [user] Accepts a much more complex grill-me than the original.
- [user] Causal and statistical purity are waived. No budget ceiling.

New direction, 2026-11-15:
- [user] "I'm not sure we should rely on the Python code or the Python approach because my gut feeling is we should just start from scratch. We can certainly build off sequential thinking if you think that's worthwhile. Or we just start with a brand new functionality that's based on everything... I don't really want to rely too much on the past if we end up a slave to the past... let's think hard and deep about what the requirements are..."
- [user] "I prefer the idea of keeping everything in memory for speed rather than storing on disk, because that also means we have to run stuff on disk and save the files somewhere. But I think we need to do a thorough explanation of what it is we want. And then once we set that up, we'll start committing stuff to the repo."
- [user] Language, latest words: "Assuming we were completely greenfielded, my gut feeling is we would write this in TypeScript because we get type safety, we probably get better performance... maybe think about that. Or some other language completely. If you think we should write this in Go or Rust or something else, you let me know. What I don't want to do is reuse the language just because we've already done the work... I was probably too prescriptive by saying TypeScript. If we want to use something else, I'm actually very happy to. And I'd actually like to explore that quite thoroughly." This is a leaning (type safety, performance), not a decision. An earlier remark preferred TypeScript; it is superseded by this.

## B. Earlier user decisions that stand unless the interview reopens them

From the research repo's grill-me walk (Q1-Q8), wording from docs/plan.md section (a). Q1 (base = arm G Python code, hook-backed) is reopened by section A and is not listed.
- Q2 [user] Finish-rejection behaviour is kept (caps, fail-open). A new field records who parked; "a park counts as done only if the user asked for it" (today any park is non-blocking).
- Q3 [user] Server absent: grill-me falls back to a plain interview; "required" becomes "preferred"; visible notice at start. A second notice at the end saying no record was kept: [assistant] (user did not object).
- Q4 [user] The register generates a markdown decision log from its store at the end of the interview into the project's .scratch/; grill-me step 8 replay reads it. ADR draft generation is deferred.
- Q6 [user] Only the grill-me chain moves (grill-me, wayfinder, write-a-prd via grill-me). Other skills stay on stock sequential-thinking.
- Q7/Q8 [user] Test plan: 8 scenarios x 2 arms, 5 repeats per cell then batches of 5 up to 15, closed-form credible intervals (no Stan).
- Repo [user]: decision-register, private, MIT (MIT merged in PR #1).
- Tracking [user]: skill, hook and settings work goes in kaybenleroll/dotfiles under their HARD RULE; kaybenleroll/random_llm_projects#203 stays the umbrella.

## C. Candidate requirements from the stress-test passes

All [assistant: stress-test]; each needs the user's decision.
- R1 Hook scope: hooks must do nothing outside an active interview, or they block questions in every session once the plugin is enabled.
- R2 State lifecycle: one state location resolved in one place, reset per interview, safe for concurrent sessions; otherwise stale entries and lifetime caps leak between interviews.
- R3 Who-parked: record who parked and accept "user" only when a user prompt arrived since the model's last action.
- R4 Hook liveness: detect and report when hooks are not firing; a hook crash must not fail open silently.
- R5 Decision log freshness: the log is written without depending on the model calling a tool, records the store position, and replay regenerates it if stale.
- Hook scope in the harness: the evaluation harness must run the shipped artefact, not a copy.
- Intent-to-treat scoring: a register-arm run with no register calls is scored, not discarded; only an infrastructure fault voids a run.

## D. Evidence the design should know

- Stock sequential-thinking server is about 480 lines of TypeScript, no enforcement, in-process state per connection, returns counts only. [verified: seqthink_upstream/ in the research repo]
- 263 grill-me sessions never recorded a branch or revision. [verified: closeout notes]
- The AP rule-only checklist matched arm G at about 3 real losses and cost less. [assistant: subagent, closeout notes/seq-study-closeout-20261114.md]
- The arm G gate barely fired. [assistant: subagent]
- N=2 sub-questions would have rejected none of the 8 archived branch calls. [assistant: subagent, plan section c]
- Corrected cost is USD 1.59-2.52 per f4 run; the recorded cost_usd is inflated 15-27x by a harness bug. [verified: harness/run_arm.py:136, per plan]
- Remaining losses were unasked branch sub-questions and owner-only specifics. [assistant: subagent]

## E. Open design tensions (settle in this order)

1. In-memory state vs hooks. Claude Code hooks run as separate processes and cannot read an MCP server's memory. In-memory-only state therefore cannot back the Stop-hook gates (unlogged-question block, finish block) or the user-turn counter. Options: (a) the server exposes a local channel (socket or HTTP) that hooks query; (b) state is mirrored to a small file; (c) enforcement is limited to tool responses, which cannot catch "the model did not call the tool". Upside of in-memory: per-connection state removes the shared-directory, lifecycle and concurrent-session problems (R2). Costs to settle: what is lost on server crash, restart, /resume or compaction; the decision log still needs a file written at the end.
2. Base: from scratch, fork of the stock TypeScript server, or port of arm G. The reference/armG snapshot stays as reference only. The stock server's licence is mixed MIT/Apache-2.0 (mid-transition; arm G has no copied code per a provenance check); a fork carries its notice.
3. Language: open. Candidates: TypeScript, Go, Rust, others (Bun or Deno compiled TypeScript; Python as a control). The user leans towards TypeScript for type safety and performance but wants the choice explored thoroughly [user]. The criteria depend on tensions 1, 4 and 7 (state channel, enforcement surface, packaging), so decide AFTER those. Evaluate with a measured spike, not by reading: per candidate, a hello-world MCP server plus a Stop-style hook, measuring cold start per hook call, the install path under a plugin without an install or build step, binary or bundle size, and MCP SDK availability and maturity. A single compiled binary acting as both MCP server and hook command is a candidate worth listing.
4. Enforcement surface: server only, or server plus hooks. If hooks: scoping to grill-me sessions (skill-frontmatter hooks vs plugin hooks with an activation marker) and how a hook identifies the session.
5. Data model: what the register captures (questions, sub-questions, branches, answers with source, parked_by, decisions, dependencies), what the decision log needs, what replay needs.
6. Failure behaviour: server absent, hooks not firing, caps, fail-open.
7. Packaging and install across machines: plugin, marketplace, private-repo authentication (untested).
8. Testing: what must be testable offline; parity with arm G behaviour is now optional.

## F. Superseded

- ADR 0001's base decision (Python port of arm G) and its language and state-location assumptions (all reopened).
- docs/plan.md steps 3-4 as written.
- The plan's evaluation steps 0, 5a/5b, 6, 6a, 7, 8 still stand as a test plan, but their harness assumptions depend on the interview.
