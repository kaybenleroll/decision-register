# Journal contract (journal_schema 2)

Authority: REQ-60, REQ-61, REQ-27 rule 5, REQ-52. Changing anything here bumps `tool_schema` (see CHANGES).

## File

- One JSON object per line (JSONL), UTF-8, appended; written write-behind. The queue is flushed (written and fsync not required) at every `check_stop` call, at the end of an interview, and on stdin EOF before the server exits (REQ-60 says "at least"; EOF is the user decision of 2026-11-16 so a graceful exit loses nothing). A hard crash can still lose the events since the last flush; `status.last_event` and `resume.last_event` make the loss visible.
- Name: `<interview_id>.jsonl` in the data directory's project folder (runtime.md); archived journals move to its `archive/` folder. The pid file `<interview_id>.pid` sits beside it (REQ-52).

## Event envelope

Every event carries:

| Field | Type | Meaning |
|---|---|---|
| `v` | integer | `2`. Replay treats a missing `v` as 1 and accepts it; `v` above 2 fails `resume`, naming the 1-based line number, with state unchanged. |
| `ts` | string | RFC 3339 UTC. |
| `seq` | integer | 1-based, gapless sequence number; `status.last_event` and `resume.last_event` report the last one. |
| `kind` | string | One of the kinds below. |

Replay applies the REQ-31 checks (non-blank answer and rationale, single-line rationale) and fails `resume` naming the line.

## Event kinds

| kind | Extra fields | Written when |
|---|---|---|
| `start` | `interview_id`, `route` (`hook`, `begin_interview`, `implicit`), `skill` (string; null when route is `implicit`), `session_id` (string, or null if no hook call was seen and `CLAUDE_CODE_SESSION_ID` is unset), `implicit` (boolean, true exactly when route is `implicit`), `prompt_count` (1 if `prompt_seen` was set, else 0) | an interview starts (REQ-20, REQ-21, REQ-22) |
| `engage` | `via` (tool name) | a pending interview becomes active (REQ-27 rule 3) |
| `prompt` | `session_id`, `count` (the interview's prompt count after this prompt) | `note_prompt` while an interview is pending, active or complete (written after that call's `hook` event) |
| `hook` | `tool` (`note_prompt`, `skill_started`, `check_stop`), `session_id` | exactly one per hook call while an interview is pending, active or complete, written first; the `skill_started` call that starts the interview writes `start` instead and no `hook`. Hook calls while no interview exists write nothing (there is no journal). |
| `item` | `id`, `item_kind`, `text`, `parent`, `depends_on` | `log_item` |
| `resolve` | `id`, `answer`, `source`, `rationale` | `resolve`, or `log_item` carrying an answer |
| `park` | `id`, `reason`, `parked_by` | `park` |
| `reopen` | `id`, `reason`, `stale` (ids made Stale) | `reopen` |
| `stop` | `decision` (`allow`, `block`), `classification` (`finish`, `question`, `unknown`), `presented` (ids or null for unknown), `blocks` (object `{prompt, finish, unlogged}`: the three cap counters, REQ-42, after this decision) | `check_stop` in an active interview, written after that call's `hook` event (REQ-42, REQ-43). In pending the call writes `hook` only, and an `end` if it stands the interview down. |
| `complete` | none | allowed finish attempt with nothing open (REQ-23) |
| `mismatch` | `plugin_tool_schema`, `server_tool_schema` | `skill_started` sees a tool_schema mismatch after its interview started or already exists (REQ-77); a `begin_interview` mismatch is an error before any start, so it is journalled only when a pending interview already exists. With no interview a mismatch is kept in memory for `status.version_mismatch` only. |
| `resume` | `session_id`, `from_seq` | `resume` claims the journal (REQ-52). `from_seq` is the `seq` of the last event before this one, equal to the `last_event` in the resume result; the `resume` event itself has `seq` = `from_seq` + 1 and later `status.last_event` includes it. |
| `end` | `end_kind` (`normal`, `ended_after_completion`, `ended_incomplete_by_model`, `abandoned`, `stood_down`), `mode` (`register`, `plain`, `not_engaged`), `reason` | the interview ends (REQ-23, REQ-27). A journal without `end` whose owner is dead is `unfinished`. `interrupted` is not a journal end kind: a crash or a server exit on an active interview writes no `end` event, the journal stays unfinished, and `interrupted` appears only among the decision-log kinds (REQ-62; REQ-27 rule 5). End kinds and triggers are listed below. |

## End kinds (journal `end` event) and their triggers

| `end_kind` | Trigger | `mode` |
|---|---|---|
| `normal` | `end_interview` on an active interview with nothing open; also a session change (REQ-26) on an active interview with nothing open | `register` |
| `ended_incomplete_by_model` | `end_interview` with `abandon: true` and a non-blank reason while items are open (REQ-23); the log says "ended incomplete by the model" | `register` |
| `abandoned` | session change (REQ-26) on an active interview with items open; the log is flagged incomplete | `register` |
| `ended_after_completion` | a complete interview ends by `end_interview`, a session change or server exit (REQ-23, REQ-26); never downgraded | `register` |
| `stood_down` | a pending interview ends by `end_interview` with `mode: plain` (mode `plain`); or, with mode `not_engaged`: by its second Stop check with no model-facing register call, by a session change (REQ-27 rule 3, REQ-26), by being replaced when a `resume` succeeds (REQ-52; `reason` "replaced by resume"), or by server exit (stdin EOF) while pending (`reason` "server exit while pending") | `plain` or `not_engaged` (`plain` only for `end_interview` with `mode: plain`) |

A pending interview holds no items, so none of its ends is unfinished: every end of a pending interview writes an `end` event with `end_kind` `stood_down` and the journal is archived, including server exit and replacement by `resume`; a pending journal never becomes an unfinished ghost.

`interrupted` (server exit or crash on an active, not-complete interview) is a decision-log kind only: no `end` event is written and the journal stays `unfinished` (REQ-27 rule 5, REQ-62). The `end_interview` tool result never returns `interrupted` or `abandoned` (tools.json keeps its own four-value enum; the journal enum has five). After an interview ends, `status.state` is `none`.

Every row and rule of the REQ-27 table maps to at least one of the 13 event names above; the frozen names are checked by `contract/check_lifecycle_events.py`.

The kinds `engage`, `prompt`, `hook`, `stop`, `complete` and `mismatch` carry no register data a replay must reproduce except counters, caps and state; replay rebuilds those from them:

- `status.hook_health.note_prompt` = `start.prompt_count` + the number of `hook` events with tool `note_prompt`; `skill_started` = 1 if the route was `hook` else 0, + the `hook` events with tool `skill_started`; `check_stop` = the `hook` events with tool `check_stop`. `prompt_seen` is not journalled (it only matters while no interview exists) and is false after a resume.
- The cap counters are the last `stop.blocks`; the prompt count is the last `prompt.count` (or `start.prompt_count`).

## Replay failure modes (`resume`)

- Final line torn (no trailing newline and invalid JSON): ignored; replay continues from the lines before it. The result's `last_event` is the last valid event.
- Invalid JSON on any other line, an unknown `kind`, a missing or repeated `seq`, a `seq` not equal to the previous plus one (a gap), or an REQ-31 violation: `resume` fails with `replay failed at line N` (1-based line number, digits) and state unchanged. `ts` is not checked for monotonicity.
- `v` above 2: fails as in the envelope table.

## Compatibility

- v1 lines (spike 3 and earlier) lack `v`, `ts` and `seq`; their `seq` is taken as the 1-based line number. Only the kinds `item`, `resolve`, `park` and `reopen` can occur in v1, with the field names of the v2 kind of the same name; a v1 `kind` outside those four fails replay naming the line.
- The suite reads journal files only through the data directory it supplied (runtime.md).
