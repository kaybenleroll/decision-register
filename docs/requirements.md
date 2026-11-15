# Requirements: structured decision register for grill-me

Status: DRAFT 2026-11-15, awaiting `/stress-test` and the user's approval. No source code until the user approves this document.

Source of decisions: the requirements interview of 2026-11-15 (a `/grill-me` and `/query-walkthrough` session). Inputs: `docs/requirements-inputs.md`; history: `docs/adr/0001-structured-decision-register.md`, `docs/plan.md` (partly superseded); evidence: `docs/evidence/spikes.md`.

Tags: [user] decided by the user; [verified] observed by a run, source named; [assistant] my proposal or inference, reversible; [open] undecided. A requirement without a tag is derived from tagged ones and says which. Requirement IDs (REQ-n) are for traceability in the stress-test, the ADRs and the build plan.

## 1. Purpose and scope

- REQ-1 [user] Build a structured decision register for grill-me: structured data captured during an interview gives a plan, documentation and a decision record for free.
- REQ-2 [user] Enforcement moves out of grill-me prose into the tool. A much more complex grill-me than the original is acceptable.
- REQ-3 [user] Causal and statistical purity are waived. There is no budget ceiling; the assistant asks before spending more than about USD 1.
- REQ-4 [user] The register is built from scratch. `reference/armG/` is a behavioural specification and a source of test ideas only; no code is copied from it or from the stock sequential-thinking server.
- REQ-5 [user] Only the grill-me chain uses the register (grill-me, wayfinder, write-a-prd via grill-me). Other skills stay on the stock sequential-thinking tool.
- REQ-6 Out of scope: the paid effectiveness evaluation, the evaluation harness and the harness cost-counter fix (owned by the research session in the research repository); changes to `~/.claude`, skills, hooks and `settings.json` (filed in the dotfiles repository under the user's rule); ADR draft generation by the register [user, deferred]; Windows and macOS binaries [user, "for now"].

## 2. Architecture

- REQ-10 [user] The register state is held in the server process's memory. A server process serves exactly one Claude Code session [verified: spike 1, two concurrent sessions had separate processes and counters].
- REQ-11 [verified: spike 1] Hooks reach the live server with handler type `mcp_tool` (server reference `plugin:<plugin>:<server>`). A Stop-event `mcp_tool` hook reached the same process as the model's tool calls, and returning `{"decision":"block","reason":...}` as the tool's text blocked the Stop. A UserPromptSubmit `mcp_tool` hook also reached the same process. No socket, HTTP port or state file is used between hooks and server.
- REQ-12 The product is a Claude Code plugin containing: the skill text for the grill-me chain, the hook configuration, `.mcp.json`, and nothing compiled. Implementation binaries are separate release assets (section 8).
- REQ-13 [user] The server does nothing and enforces nothing unless an interview is active. Hooks are no-ops in every session without an active interview, so installing the plugin does not affect unrelated sessions. [verified: spike 1, plugin-level hooks fire in every session]
- REQ-14 [user] Enforcement lives in the server and the hooks that call it; the skill text describes the interview but does not carry the enforcement. [from the interview: the enforcement surface is server plus hooks]

## 3. Interview lifecycle

- REQ-20 [user] An interview is started by hooks, not by the model: a UserPromptExpansion hook for a typed `/grill-me`, and a PreToolUse hook matching the Skill tool for a model-invoked skill; each passes the skill name to the server, which strips any plugin prefix and starts an interview only for skills on a configured list. [verified: spike 4, one trial per route on Claude Code 2.1.325; the Skill tool's input field was observed as `skill`, not documented]
- REQ-21 [user] First fallback: a `begin_interview` tool that the skill calls as its first step. Second fallback: if any register tool call arrives while no interview is active, the server starts one and flags the interview as implicitly started; the flag appears in the decision log.
- REQ-22 [user] Starting an interview resets all counters and caps, stamps an interview id, and moves any earlier journal to an archive folder in the data directory. Server state survives `/clear` and `/compact` [verified: spike 1, one trial], so a start is always a reset.
- REQ-23 [user] The interview ends when the Stop hook allows a finish with nothing open: the server writes the decision log, marks the interview ended and removes the journal. An explicit `end_interview` tool also exists to force this. Writing the decision log never depends on the model calling a tool.
- REQ-24 [assistant] The configured skill-name list starts as `grill-me`; whether `wayfinder` and `write-a-prd` are listed is decided when the grill-me integration is built, because those skills call grill-me.

## 4. Data model

- REQ-30 [user] The register is a tree of items. Each item has: id, kind (`question`, `subquestion` or `decision`), text, optional parent, optional `depends_on` ids, status, and event history.
- REQ-31 [user] Status is one of Open, Resolved, Parked, Stale. A Resolved item always carries an answer, an answer source (`user` or `model`) and a one-line rationale; the implementation makes a Resolved item without these unrepresentable where the language allows, and rejects it at the interface where it does not.
- REQ-32 [user] An answer source of `user` is accepted only if a user prompt arrived since the item's last model action, as counted by the prompt hook. Otherwise the call is rejected.
- REQ-33 [user] A Parked item records who parked it (`parked_by`: `user` or `model`) and a reason. A park by the model keeps the item blocking; a park the user asked for does not.
- REQ-34 A `decision` item can be resolved only with source `user`, and parked only with `parked_by` user. [assistant: derived from the change-request spike's specification C2; user to confirm in the stress-test]
- REQ-35 A branch is a parent item with at least N sub-questions; N = 2 and is a declared constant, not measured. [assistant]
- REQ-36 Reopening a Resolved item makes it Open and makes every Resolved item that depends on it, directly or transitively, Stale. A Stale item blocks finishing like an Open item and can be resolved again. [assistant: derived from C1]
- REQ-37 Optional fields beyond those above (options considered, recommendation, tags) are not in the first build. ADR drafting is deferred. [user]

## 5. Tools and hooks

- REQ-40 Tools: `begin_interview`, `log_item`, `resolve`, `park`, `reopen`, `status`, `resume`, `end_interview`, plus the hook-facing tools `note_prompt`, `skill_started` and `check_stop`. Names, schemas and results are identical in every implementation. [assistant: proposal, to be finalised in the build plan]
- REQ-41 Four hook behaviours: the prompt counter (UserPromptSubmit calls `note_prompt`); the finish gate (Stop calls `check_stop` with `last_assistant_message`); and two start hooks (REQ-20). Hooks carry only the fixed input given in their configuration plus substituted fields (for example `${prompt}`, `${session_id}`) [verified: spike 1 and spike 4: hook-originated calls carry no `_meta`].
- REQ-42 [user] The finish gate keeps ADR 0001's behaviour as starting values, counted per interview: at most 2 blocks per user prompt, 8 blocks to finish, 12 for unlogged questions; fail open. Claude Code itself limits consecutive Stop blocks to 8, reset by any tool call; the register's caps sit under that.
- REQ-43 The finish gate blocks when any item is Open, Stale or model-parked, and when the last assistant message asks a question that has not been logged. The question heuristic is ported in behaviour from the prototype, not in code. [assistant]
- REQ-44 The server counts hook calls. A user-sourced answer that arrives when the server has seen no prompt hook is rejected with a message saying hooks are not firing. [user: Q6 option (b)]

## 6. Failure behaviour

- REQ-50 [user] Fail open with visible signals. A register tool failure makes the skill tell the user and continue as a plain interview. The end of the interview states whether the register was complete.
- REQ-51 [user] If the server is absent, grill-me falls back to a plain interview. "Required" becomes "preferred". A notice at the start says no record will be kept, and a notice at the end repeats it. [ADR 0001 Q3]
- REQ-52 After a server crash or restart, the skill offers an explicit resume from the journal or a plain continuation; resume is never automatic. [user]
- REQ-53 [open] Whether a failed `mcp_tool` hook shows the user an error is unverified; it is checked in the build and does not block it.

## 7. Journal and decision log

- REQ-60 [user] A write-behind, append-only JSONL journal records every state change and hook event so that `resume` can rebuild state. Every event carries `"v": 2` and `"ts"`; replay accepts events without `v` as v1; an event with `v` above 2 makes resume fail naming the 1-based line number and leaves state unchanged. [assistant: derived from C3]
- REQ-61 [user] The journal lives in the plugin's per-user data directory (`CLAUDE_PLUGIN_DATA`), keyed by project and interview, with the operating system's temp directory as the fallback if that is unavailable. It is created readable by the user only and removed after a successful finish. [verified: `CLAUDE_PLUGIN_DATA` is in the server environment, spike 1]
- REQ-62 [user] The decision log is a markdown file written by the server at the end of the interview. It goes in the project's `.scratch/` if that directory exists, otherwise in `decision-logs/` in the project root. It shows the tree, each answer with source and rationale, parked items and who parked them, reopened and stale history, the implicit-start flag, and hook-health counts. It records the journal's last event position so replay can detect a stale log.
- REQ-63 The grill-me replay step reads the decision log; if the log is missing or stale it regenerates it from the register, then falls back to the transcript. [assistant: from ADR 0001 R5]

## 8. Implementations and distribution

- REQ-70 [user] Two implementations are built in parallel as an experiment in working in both languages: Rust and TypeScript on Node (an esbuild single-file bundle). After a while one is kept and the other is retired.
- REQ-71 [user] Both implementations pass the same language-neutral black-box conformance suite (REQ-80), expose identical tool names, schemas and journal format, and have equivalent behaviour.
- REQ-72 [user] The retirement rule is written into the language ADR before the real build starts. Starting values [assistant, for the user to edit]: trigger = both pass the suite and the first five real changes have been applied to both; measures = tokens and fix cycles per change, defects found after merge, release friction; if tokens differ by more than 20% the cheaper one is recommended, within 20% Rust is recommended for its type guarantees; the user decides; the loser is tagged, archived, and removed from CI and releases.
- REQ-73 [user] Distribution is by GitHub releases (the repository is private): per-platform Rust binaries and one `regspike.mjs` for TypeScript. They are installed on the PATH as `regspike-rs` and `regspike-ts`. The plugin starts `regspike-${REGSPIKE_IMPL:-rs}`; if variable expansion in the start command does not work, a small launcher script does the selection. [open: expansion unverified] The names are working names. [assistant]
- REQ-74 [user] The Rust binary is built for Linux x86_64 only for now. The TypeScript bundle needs Node on each machine and is platform-independent. Building locally on a machine remains an acceptable fallback.
- REQ-75 The plugin is installed from a private GitHub marketplace in this repository using the user's existing git credentials (SSH key or credential helper); a prompt-requiring credential helper makes background updates fail silently. A local clone loaded with `--plugin-dir` is the development loop. [verified: docs; private install untested]
- REQ-76 Install steps and `settings.json` entries (marketplace, enabled plugin, allow rules for the plugin's tools) are filed as dotfiles-repository issues.

## 9. Testing

- REQ-80 [user] A language-neutral black-box conformance suite is the behaviour specification: a Python script (standard library only), starting from the spike's `acceptance.py`, extended with a scenario for every behaviour in this document. It drives each server over stdio only. It moves to data-file scenarios if it outgrows about 30 scenarios. [assistant]
- REQ-81 [user] Each implementation also has its own offline unit tests for the state machine and tool handlers. Behaviour parity with the earlier Python prototype is not a goal; the prototype's tests are a specification only where behaviour is kept (caps, the user-answer rule, stale marking).
- REQ-82 [user] A small live smoke suite runs `claude -p` on the cheapest model, on demand, with a budget of about USD 0.25 per run of the suite. It checks that hooks fire, the Stop block works, interviews start by both routes, scoping holds (a question in an unrelated session is never blocked), and the plugin loads. `claude` is run from a directory other than the plugin directory, because otherwise `.mcp.json` loads as a project server and the plugin's hooks fail [verified: spikes 2 and 3].
- REQ-83 The evaluation harness must run the shipped plugin files, not a copy, and a register-arm run with no register calls is scored, not discarded. [from docs/requirements-inputs.md, section C; owned by the research session]

## 10. Non-functional requirements

- REQ-90 Server start to initialize reply under 1 second and idle memory under 200 MB; a candidate beyond either counts against it. [assistant; spike 2 candidates measured 2.9 to 587 ms and 5 to 74 MB]
- REQ-91 Privacy: journal and decision log may contain decision content; the journal is user-only readable (REQ-61). No data leaves the machine.
- REQ-92 Baseline platform: Claude Code 2.1.325 on Linux x86_64; other versions are checked by the smoke suite.
- REQ-93 UK English in prose and documents.

## 11. Evidence and its limits

- Every spike is a single trial on the cheapest model and one Claude Code version; details in `docs/evidence/spikes.md`.
- The earlier study found a rule-only checklist matched the prototype at about three real losses each and cost less. Nothing yet shows the register improves interviews; this build is justified by REQ-1, and its effect is measured by the research session.
- The change-request test did not discriminate between the languages: both agents batched the three changes, so fix-cycle counts tie. Tokens favoured TypeScript by about 30%, from one trial each.

## 12. Risks and open items

Risks: (1) hook behaviour may change with Claude Code versions, covered by fallbacks and the smoke suite; (2) two implementations double the cost and can drift, covered by the conformance suite; (3) the required rationale may be padded boilerplate, checked in the first real interviews; (4) the Stop-hook block cap of 8 may interact with the register's caps; (5) a crash loses the in-memory register unless the user resumes from the journal.

Open items: REQ-53 hook error visibility; REQ-73 start-command variable expansion; `/clear` and `/compact` in interactive sessions (spike 1 tested stream-json `-p` only); the final tool list and schemas (build plan); the skill-name list contents (REQ-24); product and binary names (working names only); retirement-rule values (REQ-72); the privacy of the journal on Windows (out of scope for now).

## 13. Traceability

Decisions from the interview, by walk number: state and hooks (Q1, Q9) REQ-10 to REQ-13, REQ-60 to REQ-61; enforcement and scoping (Q2, Q16) REQ-13, REQ-41; base (Q4) REQ-4; data model (Q5) REQ-30 to REQ-37; failure behaviour (Q6) REQ-50 to REQ-53; testing (Q7, Q14) REQ-80 to REQ-83; language and distribution (Q3, Q8, Q11, Q12, Q17) REQ-70 to REQ-76; file locations (Q13) REQ-61, REQ-62; evidence (Q18) `docs/evidence/`; lifecycle (Q15) REQ-20 to REQ-23; process (Q19, Q20) sections 12 and the sequence in `CLAUDE.md`.
