# V3: compaction and session_id

Issue #25. Question: does the `session_id` the hooks send to the server stay the same across compaction (REQ-26, REQ-22)?

Source: the live run of 2026-11-19 (`docs/evidence/live-run-2026-11-19.md`), Claude Code 2.1.328, one manual `/compact` at 12:44:01 (`compact_boundary` entry), single trial.

## Verdict: PARTIAL, inferred-stable. NOT directly observed. V3 is not passed.

## What was observed

- The transcript `sessionId` was one value (`baed6038-c0bc-464c-83a4-4022c2cddfbe`) on all 360 entries that carry one, before and after the compaction. That is the harness's own session id for the transcript.
- The interview survived compaction: `interview_id` `iv-20261119T123740Z-757024ab` was unchanged before and after, and `status` after compaction reported state `active`.
- The `status` hook-health counters accumulated on that one interview across the compaction: `note_prompt` 1 to 9, `check_stop` 0 to 11.

## What was not observed

The `session_id` that the hooks substitute (`${session_id}`, REQ-41) and send to the server is not recorded in the transcript. The server's journal or logs that might hold it were not located (journal file unlocated, see the live-run evidence, section 7).

## Why the inference is only an inference

REQ-26 ends an interview when the received `session_id` changes. If the hook `session_id` had changed at compaction, the interview would have ended there. It did not end, and later hook calls kept counting on the same interview. That supports "stable" but depends on the REQ-26 session-change rule being implemented and exercised as the contract says, which this run does not check independently. The transcript `sessionId` equalling the hook `session_id` is an assumption.

## What a direct observation needs

The server (or a throwaway hook) records the `session_id` it receives on each hook call, with timestamps, and the compare is made across a compaction. For example, a debug journal row or log line on `note_prompt` and `check_stop` carrying the received id, then a run with a `/compact` between two prompts. A drop-rule check (an id change at compaction would end the interview under REQ-26) needs the same record.

## Related

Manual `/compact` only. `/clear` (V4) was not run. The run used the TypeScript build (`REGSPIKE_IMPL=ts`) from a branch not stated in the transcript; the I3 branch-build condition in the issue is not established by this run.
