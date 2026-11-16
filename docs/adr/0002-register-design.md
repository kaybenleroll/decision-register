# ADR 0002: Register design (from scratch, in-memory, hook-backed)

Date: 2026-11-16. Status: Proposed (awaiting the user's approval of docs/requirements.md).

Supersedes only the base decision of ADR 0001 (section 2.1: port the Python arm G code; state in files shared by server and hooks). The rest of ADR 0001 stands unless docs/requirements.md changes it. The language choice is in ADR 0003.

Tags as in docs/requirements.md: [user] decided by the user; [verified] observed by a run, source named; [assistant] my proposal, reversible; [open] undecided. Evidence is marked as evidence; judgement is marked as judgement. REQ ids are cited, not restated.

## 1. Context

- Requirements are in docs/requirements.md (REQ-1 to REQ-93); it is the source of truth. This ADR records the architectural choice they imply and what is still undecided.
- ADR 0001 chose to port arm G (Python, state in a directory shared with hooks). The user reopened that on 2026-11-15 (docs/requirements-inputs.md, sections A and E): start from scratch, prefer in-memory state.
- The obstacle, stated in docs/requirements-inputs.md E1: hooks run as separate processes and cannot read an MCP server's memory, so in-memory state seemed unable to back the Stop-hook gates. Spike 1 removed that obstacle.

## 2. Decision

1. Build from scratch. `reference/armG/` is a behavioural specification and a source of test ideas only (REQ-4, REQ-81). [user]
2. Register state lives in the server process's memory; one process serves one Claude Code session (REQ-10). [user]
3. Hooks call the live server through the `mcp_tool` handler type; no socket, HTTP port or state file between hooks and server (REQ-11, REQ-41). [user, on verified evidence]
4. The journal is write-behind, append-only, for crash resume only; resume is explicit, never automatic (REQ-52, REQ-60, REQ-61). [user]
5. Enforcement is in the server and its hooks, not in skill prose (REQ-2, REQ-14). [user]
6. Interviews are started by hooks, with `begin_interview` and a restricted implicit start as fallbacks (REQ-20 to REQ-24, REQ-26). [user, mechanics assistant]
7. Failure is open and visible (REQ-50, REQ-51, REQ-53, REQ-77).

The detailed behaviour (data model, transition table, finish gate, caps, log and journal format) is in REQ-30 to REQ-63 and is not repeated here.

## 3. Evidence

All in docs/evidence/spikes.md. Single trials unless stated; haiku; Claude Code 2.1.325.

- Spike 1 (docs/evidence/spike1/RESULTS.md): Stop and UserPromptSubmit `mcp_tool` hooks reached the same server PID as the model's calls; a `{"decision":"block"}` tool text blocked Stop. One process per session. State survived `/compact` and `/clear` (stream-json `-p` only; interactive untested). Hook-originated calls carry no `_meta`; model calls do. Plugin-level hooks fire in every session.
- Spike 4 (docs/evidence/spike4/RESULTS.md): `${prompt}`, `${session_id}`, `${tool_input.skill}` substitute into `mcp_tool` input; typed `/skill` is caught by UserPromptExpansion, not PreToolUse. One trial per route.
- Spike 5 (docs/evidence/spike5/RESULTS.md): `${last_assistant_message}` delivered a 6,869-character message byte-identical (length and sha256). Short and empty messages not tested.
- Spike 2 (docs/evidence/spike2/): all five candidate servers passed the live Stop-hook gate. Language comparison belongs to ADR 0003.
- Not evidence: that the register improves interviews. Nothing yet shows it; the build is justified by REQ-1 and measured by the research session (REQ-6, requirements section 11).

Judgement, not evidence: that `_meta` discrimination is a safe forgery guard (one trial each, spikes 1 and 4; a Claude Code change could remove it); that the hook route stays supported across versions (REQ-92, smoke suite REQ-82).

## 4. Consequences

- No second channel to secure or clean up; state vanishes with the process, so a crash loses everything since the last Stop flush unless the user resumes from the journal (REQ-60; requirements risk 5).
- Correctness depends on undocumented or recently documented hook behaviour (`mcp_tool` handler, Skill `tool_input.skill`). The smoke suite (REQ-82) and the version handshake (REQ-77) are the mitigations.
- The plugin's hooks fire in every session, so a missing binary or disconnected server affects unrelated sessions (REQ-13, REQ-53, REQ-77). The launcher mitigates; whether a failed hook shows an error is unverified.
- The register cannot prove a user prompt answered a specific item (REQ-32 known limit, section 5 below).
- Two skill-text copies are possible until REQ-15 is decided; REQ-15's interim rule limits the damage.

## 5. Open decisions for the user

### 5.1 REQ-15: canonical grill-me skill home [open]

Recommendation (assistant, awaiting the user): ship the grill-me skill text in the plugin and retire the dotfiles copy. Reason: the skill text and the tool schema must version together (REQ-77); with the text in dotfiles, a plugin update can change tool names or `tool_schema` while the installed skill still calls the old ones, and the user would have two places to edit. Cost: it crosses the repository boundary (REQ-6, REQ-76), so the retirement and its ordering are a dotfiles-repository issue under the user's rule; the plugin-qualified skill name (for example `<plugin>:grill-me`) becomes the single entry on the start list (REQ-24). Until decided, REQ-15's interim rule applies (one full name, matched exactly).

### 5.2 Per-prompt authorisation budget (REQ-32 known limit) [open: decided in this ADR]

The limit: REQ-32 checks that a user prompt arrived, not that it answered this item, so after one prompt the model can resolve any number of items as `user`. Options:

- None (current REQ-32): simplest, no false rejections; the decision log shows sources and hook-health counts, so a reader can audit afterwards. Does not stop a model that wants to mislabel.
- Flat budget of 1 user-sourced answer per prompt: strong, but rejects honest cases where the user answers a batch (grill-me may batch up to four independent low-stakes questions).
- Budget of one user-sourced answer per distinct item presented since the previous prompt, counting items the preceding question turn presented. Bounds the damage to the number of questions actually asked, and permits honest batches.

Recommendation (assistant, judgement): the third, with a minimum of 1 per prompt and, if REQ-43's classifier cannot say which items a turn presented, a flat 4 as the fallback. Reason: it is the only option that is both bounded and compatible with batched questions. It still does not prove the answer matched the item; the residual risk is in requirements section 12, risk 0. If the user prefers the audit-only position, choose "none" and keep the decision log as the control. Needs: confirm whether REQ-43's classifier can attribute a question turn to specific items (not established); the budget is a declared constant like REQ-35's N.

### 5.3 Clauses marked [open] or newly [assistant] in requirements (section 12)

For the user to confirm or change at approval of docs/requirements.md:

- [open] REQ-15 (5.1 above); REQ-53 (hook error visibility, verified in the build); REQ-73 (variable expansion in the start command; the launcher is the fallback).
- [assistant] added after the first stress-test round: REQ-21 (implicit-start restriction), REQ-22 (no-op start, archive), REQ-23 (log at every allowed finish; interview stays active), REQ-26 (session binding), REQ-32 (volunteered decisions, known limit), REQ-33 and REQ-38 (transitions), REQ-35 (enforcement points), REQ-40 (forgery guard), REQ-43 (finish-attempt classification), REQ-52 (resume mechanics), REQ-60 (durability), REQ-77 (handshake and launcher).
- [assistant] earlier, still unconfirmed: REQ-34 (decision kind), REQ-35 (N = 2), REQ-43, REQ-12 (plugin subdirectory), REQ-24 (list contents), REQ-61 (30-day pruning), REQ-90.
- Unverified premises to check in the build, not decisions: whether compaction changes `session_id`; whether a `SessionEnd` hook can call a tool; whether `last_assistant_message` is ever empty; interactive `/clear` and `/compact`.
- REQ-72's values are decided in ADR 0003.

## 6. Alternatives considered

- State file or socket channel between hooks and server (requirements-inputs E1 options a and b). Rejected: unnecessary once spike 1 showed `mcp_tool` hooks reach the live process; it adds a file or port to secure, race over and clean up, and a mirrored file is exactly the on-disk state the user wanted to avoid. Judgement; the cost of adding a channel later is low if `mcp_tool` is withdrawn, which is the main risk of the chosen design.
- Port arm G (ADR 0001's base). Rejected by the user (REQ-4); additionally the arm G state design assumed a shared directory and cap counters that never reset (ADR 0001 section 2.8 R2). Its behaviour is kept as a specification only where requirements say so (REQ-81).
- Enforcement in skill prose only (option c in E1, tool-response-only). Rejected: ADR 0001 evidence shows prose enforcement is the status quo (grill-me has no enforcement section), and tool responses cannot catch the model not calling the tool. A rule-only checklist matched the prototype in the earlier study [recalled, requirements section 11, not re-checked], so this rejection rests on REQ-2 (the user's decision), not on measured superiority.
- Fork of the stock sequential-thinking server. Not chosen (REQ-4); it would also carry its licence notice (requirements-inputs E2).
