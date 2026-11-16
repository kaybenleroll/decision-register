# Journal contract (journal_schema 2)

Authority: REQ-60, REQ-61, REQ-27 rule 5, REQ-52. Changing anything here bumps `tool_schema` (see CHANGES).

## File

- One JSON object per line (JSONL), UTF-8, appended; written write-behind and flushed at least at every `check_stop` and at the end of an interview (REQ-60).
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
| `start` | `interview_id`, `route` (`hook`, `begin_interview`, `implicit`), `skill`, `session_id`, `implicit` (boolean), `prompt_count` | an interview starts (REQ-20, REQ-21, REQ-22) |
| `engage` | `via` (tool name) | a pending interview becomes active (REQ-27 rule 3) |
| `prompt` | `session_id`, `count` | `note_prompt` while an interview is active or pending |
| `hook` | `tool` (`note_prompt`, `skill_started`, `check_stop`), `session_id` | hook-health bookkeeping (REQ-44) |
| `item` | `id`, `item_kind`, `text`, `parent`, `depends_on` | `log_item` |
| `resolve` | `id`, `answer`, `source`, `rationale` | `resolve`, or `log_item` carrying an answer |
| `park` | `id`, `reason`, `parked_by` | `park` |
| `reopen` | `id`, `reason`, `stale` (ids made Stale) | `reopen` |
| `stop` | `decision` (`allow`, `block`), `classification` (`finish`, `question`, `unknown`), `presented` (ids or null for unknown), `blocks` | `check_stop` in an active interview (REQ-42, REQ-43) |
| `complete` | none | allowed finish attempt with nothing open (REQ-23) |
| `mismatch` | `plugin_tool_schema`, `server_tool_schema` | `skill_started` or `begin_interview` sees a tool_schema mismatch (REQ-77) |
| `resume` | `session_id`, `from_seq` | `resume` claims the journal (REQ-52) |
| `end` | `end_kind` (`normal`, `ended_after_completion`, `ended_incomplete_by_model`, `abandoned`, `interrupted`, `stood_down`), `mode` (`register`, `plain`, `not_engaged`), `reason` | the interview ends (REQ-23, REQ-27). A journal without `end` whose owner is dead is `unfinished`. `interrupted` is written to the decision log only; the journal stays unfinished (REQ-27 rule 5). |

The kinds `engage`, `prompt`, `hook`, `stop`, `complete` and `mismatch` carry no register data a replay must reproduce except counters, caps and state; replay rebuilds those from them.

## Compatibility

- v1 lines (spike 3 and earlier) lack `v` and `ts`; they use the spike's item-event shapes and are accepted as v1 by replay.
- The suite reads journal files only through the data directory it supplied (runtime.md).
