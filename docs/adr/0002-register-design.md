# ADR 0002: Register design (from scratch, in-memory, hook-backed)

Date: 2026-11-16. Status: Proposed. DRAFT, gate decisions folded 2026-11-16, lifecycle revised 2026-11-16 after three independent reviews (REQ-27), awaiting the user's review of the diff and approval.

Supersedes only the base decision of ADR 0001 (section 2.1: port the Python arm G code; state in files shared by server and hooks). The rest of ADR 0001 stands unless docs/requirements.md changes it. The language choice is in ADR 0003.

Tags as in docs/requirements.md: [user] decided by the user; [verified] observed by a run, source named; [assistant] my proposal, reversible; [open] undecided. Evidence is marked as evidence; judgement is marked as judgement. REQ ids are cited, not restated.

## 1. Context

- Purpose [user: gate walk 2026-11-16]: the register does what the user believed the stock sequential-thinking tool did, which is to manage complex decision trees so that no part of the tree is forgotten, and to be robust to context compaction (REQ-1, REQ-45); enforcement is in the tool, not in prose (REQ-2); the plan, documentation and decision record come for free (REQ-1).
- Requirements are in docs/requirements.md (REQ-1 to REQ-93); it is the source of truth. This ADR records the architectural choice they imply and what is still undecided.
- ADR 0001 chose to port arm G (Python, state in a directory shared with hooks). The user reopened that on 2026-11-15 (docs/requirements-inputs.md, sections A and E): start from scratch, prefer in-memory state.
- The obstacle, stated in docs/requirements-inputs.md E1: hooks run as separate processes and cannot read an MCP server's memory, so in-memory state seemed unable to back the Stop-hook gates. Spike 1 removed that obstacle.

## 2. Decision

1. Build from scratch. `reference/armG/` is a behavioural specification and a source of test ideas only (REQ-4, REQ-81). [user]
2. Register state lives in the server process's memory; one process serves one Claude Code session (REQ-10). [user]
3. Hooks call the live server through the `mcp_tool` handler type; no socket, HTTP port or state file between hooks and server (REQ-11, REQ-41). [user, on verified evidence]
4. The journal is write-behind, append-only, for crash resume only; resume is explicit, never automatic (REQ-52, REQ-60, REQ-61). [user] A server exit on an active, not-complete interview keeps the journal as unfinished and writes the log flagged "interrupted"; a complete interview is archived (REQ-27 rule 5). [assistant]
5. Enforcement is in the server and its hooks, not in skill prose (REQ-2, REQ-14). [user]
6. Interviews are started by hooks, with `begin_interview` and a restricted implicit start as fallbacks (REQ-20 to REQ-24, REQ-26). [user, mechanics assistant] They follow one lifecycle state table: none, pending, active, complete, ended, plus unfinished journals (REQ-27). [assistant]
7. Failure is open and visible (REQ-50, REQ-51, REQ-53, REQ-77).
8. The grill-me skill stays in the dotfiles repository; the plugin ships hooks, `.mcp.json` and the launcher only (REQ-12, REQ-15). [user: gate walk 2026-11-16]
9. After compaction the model recovers the tree from `status` (full tree) and from the open items named in every Stop block (REQ-45). [user: gate walk 2026-11-16]
10. The model cannot know its session id, so the server learns it from every hook call (even when no interview is active) and binds an interview to the last one seen; the invoking prompt counts as prompt 1 (REQ-27 rules 1 and 2). [assistant]
11. A hook-started interview is pending until the skill makes a register call, and is stood down if it makes none by the second Stop check or says it is in plain mode (REQ-27 rule 3). In the complete state the gate allows every turn (rule 4). [assistant]
12. Hooks never put prose in front of the model, and the start hook never blocks the Skill call (REQ-41, REQ-77); a missing binary yields a no-op stub from the launcher, not failing hooks (REQ-77). [assistant]

The detailed behaviour (data model, transition table, finish gate, caps, log and journal format) is in REQ-30 to REQ-63 and is not repeated here.

## 3. Evidence

All in docs/evidence/spikes.md. Single trials unless stated; haiku; Claude Code 2.1.325.

- Spike 1 (docs/evidence/spike1/RESULTS.md): Stop and UserPromptSubmit `mcp_tool` hooks reached the same server PID as the model's calls; a `{"decision":"block"}` tool text blocked Stop. One process per session. State survived `/compact` and `/clear` (stream-json `-p` only; interactive untested). Hook-originated calls carry no `_meta`; model calls do. Plugin-level hooks fire in every session.
- Spike 4 (docs/evidence/spike4/RESULTS.md): `${prompt}`, `${session_id}`, `${tool_input.skill}` substitute into `mcp_tool` input; typed `/skill` is caught by UserPromptExpansion, not PreToolUse. One trial per route.
- Spike 5 (docs/evidence/spike5/RESULTS.md): `${last_assistant_message}` delivered a 6,869-character message byte-identical (length and sha256). Short and empty messages not tested.
- Spike 6 (docs/evidence/spike6/RESULTS.md): `${session_id}` substitutes into UserPromptSubmit and Stop `mcp_tool` hooks and equals the session's id (two runs); a UserPromptSubmit hook's return text was not visible to the model (two runs); a Stop hook returning `{}` allowed the turn. UserPromptExpansion and PreToolUse output visibility is untested (REQ-41, slice V8).
- Spike 2 (docs/evidence/spike2/): all five candidate servers passed the live Stop-hook gate. Language comparison belongs to ADR 0003.
- Not evidence: that the register improves interviews. Nothing yet shows it; the build is justified by REQ-1 and measured by the research session (REQ-6, requirements section 11).

Judgement, not evidence: that `_meta` discrimination is a safe forgery guard (one trial each, spikes 1 and 4; a Claude Code change could remove it); that the hook route stays supported across versions (REQ-92, smoke suite REQ-82).

## 4. Consequences

- No second channel to secure or clean up; state vanishes with the process, so a crash loses everything since the last Stop flush unless the user resumes from the journal (REQ-60; requirements risk 5). A graceful exit mid-interview keeps the journal as unfinished and loses nothing (REQ-27 rule 5).
- Correctness depends on undocumented or recently documented hook behaviour (`mcp_tool` handler, Skill `tool_input.skill`). The smoke suite (REQ-82) and the version handshake (REQ-77) are the mitigations.
- The plugin's hooks fire in every session, so a missing binary or disconnected server affects unrelated sessions (REQ-13, REQ-53, REQ-77). For a missing binary the launcher serves a no-op stub, so the hooks succeed silently; for a server that is otherwise not connected, whether a failed hook shows an error is unverified (REQ-53).
- An interview that the skill never engages is stood down after two Stop checks, which bounds the harm of a plain-mode skill or a same-named copy but also costs enforcement if a register-mode skill is slow to make its first call (requirements risk 6).
- The register cannot prove a user prompt answered a specific item; the per-prompt budget bounds the damage (REQ-32, section 5.2).
- The skill's mode depends on a runtime `status` probe (REQ-15): with the plugin absent or mismatched the skill runs a plain interview and says so (REQ-51). There is one skill text, edited in the dotfiles repository.

## 5. Decisions from the gate walk, and what remains open

### 5.1 REQ-15: grill-me skill home [decided: user, gate walk 2026-11-16]

The grill-me skill stays in the dotfiles repository (not retired, not moved into the plugin). The plugin ships hooks, `.mcp.json` and the launcher only (REQ-12). The skill gains a register mode with a plain fallback: its first step is a call to the register's `status`; if it answers and its `tool_schema` matches what the skill expects, the skill runs in register mode (logs items, resolves, walks open branches); if the tool is absent, errors, or the schema mismatches, it runs the existing plain interview and tells the user once that no record is being kept (REQ-51). The decision log records which mode ran (REQ-62). In register mode the skill calls `begin_interview` with `mode: register` and no session id, and `end_interview` after its replay step; on a schema mismatch it calls `end_interview` with `mode: plain` [assistant] (REQ-27). Enforcement stays in the server and hooks (REQ-14). The reason is not version drift between plugin and skill; REQ-77 remains a robustness check. The start list is exactly `grill-me` (REQ-24). This supersedes the earlier assistant recommendation (ship the skill in the plugin and retire the dotfiles copy), which is withdrawn. The dotfiles work is slice P5 and drafts D4 and D5 in the build plan.

### 5.2 Per-prompt authorisation budget (REQ-32) [decided: user, gate walk 2026-11-16]

Option chosen: a user prompt authorises at most one `source: user` answer per distinct item presented since the previous user prompt, with a minimum of 1. [user: gate walk 2026-11-16] Mechanics: the budget is max(1, p), where p is the number of distinct items the classifier names as presented; an empty list gives 1; the flat 4 applies only when the classifier returns `unknown` (errors, times out or cannot parse), a result distinct from an empty list; volunteered `log_item` answers do not draw on the budget and are limited to 4 per prompt [assistant: 4 is a declared constant]. Options set aside: none (audit-only; simplest but does not stop a model that wants to mislabel) and a flat 1 (strong, but rejects honest batches of up to four questions). The classifier of REQ-43 therefore names the items each turn presented, and its replay test is REQ-46. The budget does not prove an answer matched an item; the residual risk is requirements section 12, risk 0.

### 5.3 Clauses marked [open] or [assistant] in requirements (section 12)

For the user to confirm or change at approval of docs/requirements.md:

- [open] REQ-53 (hook error visibility, verified in the build); REQ-73 (variable expansion in the start command, V2; the launcher is the fallback); compaction injection of the tree (REQ-45, V7: an upgrade, not a dependency); hook output visibility for UserPromptExpansion and PreToolUse (V8); the names the start hooks receive for user-level and project-level skills (V9, REQ-24); typed-start hook order (V10, REQ-27 rule 2). Parked: product and binary names (REQ-73), decided at the release milestone.
- Decided at the gate walk of 2026-11-16: REQ-15 (5.1), REQ-32 budget (5.2), REQ-24 list, REQ-34, REQ-35 (N = 2) and REQ-43 as starting values, REQ-72 values (ADR 0003). Approval of the whole remains the user's, after review of the diff.
- Every requirement that still carries an [assistant] tag, computed by script, identical to requirements section 12 (34): REQ-10, REQ-12, REQ-13, REQ-15, REQ-20, REQ-21, REQ-22, REQ-23, REQ-26, REQ-27, REQ-31, REQ-32, REQ-33, REQ-34, REQ-35, REQ-36, REQ-38, REQ-40, REQ-41, REQ-43, REQ-44, REQ-45, REQ-46, REQ-52, REQ-60, REQ-61, REQ-62, REQ-63, REQ-72, REQ-73, REQ-77, REQ-80, REQ-82, REQ-90. For a requirement that also carries a [user] clause, only its [assistant] clauses are in question. The lifecycle revision (REQ-27 and the clauses that agree with it) is wholly [assistant].
- Unverified premises to check in the build, not decisions: whether compaction changes `session_id`; whether a `SessionEnd` hook can call a tool; whether `last_assistant_message` is ever empty; interactive `/clear` and `/compact`.
- REQ-72's values are in ADR 0003 (confirmed as starting values 2026-11-16).

## 6. Alternatives considered

- State file or socket channel between hooks and server (requirements-inputs E1 options a and b). Rejected: unnecessary once spike 1 showed `mcp_tool` hooks reach the live process; it adds a file or port to secure, race over and clean up, and a mirrored file is exactly the on-disk state the user wanted to avoid. Judgement; the cost of adding a channel later is low if `mcp_tool` is withdrawn, which is the main risk of the chosen design.
- Port arm G (ADR 0001's base). Rejected by the user (REQ-4); additionally the arm G state design assumed a shared directory and cap counters that never reset (ADR 0001 section 2.8 R2). Its behaviour is kept as a specification only where requirements say so (REQ-81).
- Enforcement in skill prose only (option c in E1, tool-response-only). Rejected: ADR 0001 evidence shows prose enforcement is the status quo (grill-me has no enforcement section), and tool responses cannot catch the model not calling the tool. A rule-only checklist matched the prototype in the earlier study [recalled, requirements section 11, not re-checked], so this rejection rests on REQ-2 (the user's decision), not on measured superiority.
- Fork of the stock sequential-thinking server. Not chosen (REQ-4); it would also carry its licence notice (requirements-inputs E2).
