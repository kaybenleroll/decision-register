# V3: compaction and session_id

Issue #25. Question: does the `session_id` the hooks send to the server stay the same across compaction (REQ-26, REQ-22)?

Source: the live run of 2026-11-19 (`docs/evidence/live-run-2026-11-19.md`), Claude Code 2.1.328, one manual `/compact` at 12:44:01 (`compact_boundary` entry), single trial.

Implementation: run 1 used `REGSPIKE_IMPL=ts` (the TypeScript build) as instructed and reported by the user, started from `$HOME/workspace/register-live-run` with `--plugin-dir`; the transcript and journal do not record the implementation themselves.

## Verdict: observed equal across one compaction in one session (one trial). Not claimed closed.

The server journal `~/.claude/plugins/data/decision-register-inline/<hash>/archive/iv-20261119T123740Z-757024ab.jsonl` records the received `session_id` on every hook row. All 22 hook rows (`note_prompt`, `check_stop`) carry `baed6038-c0bc-464c-83a4-4022c2cddfbe`, equal to the transcript `sessionId`: 18 rows before the manual `/compact` at 12:44:01 and 4 after (first 12:45:15). Journal timestamps are UTC (`Z`) and the transcript `compact_boundary` timestamp is also UTC, so no conversion was applied. Counted by script over the 58-row file (matches `journal_position` 58). The 9 `prompt` rows and the `start` row carry the same id; no other value appears. The journal does not record which implementation served (no server_version, binary path or impl field); the TypeScript build is from `REGSPIKE_IMPL=ts` in the run command only.

Issue #25 acceptance criteria: "Recorded equal or different. Runs against the I3 branch build before merge (a server passing the suite on the branch, not a merged one) and gates the I3 merge." The criterion does not ask for more than one trial; the open point is the I3 branch-build condition, which this run does not establish.

## What was observed

- The transcript `sessionId` was one value (`baed6038-c0bc-464c-83a4-4022c2cddfbe`) on all 360 entries that carry one, before and after the compaction. That is the harness's own session id for the transcript.
- The interview survived compaction: `interview_id` `iv-20261119T123740Z-757024ab` was unchanged before and after, and `status` after compaction reported state `active`.
- The `status` hook-health counters accumulated on that one interview across the compaction: `note_prompt` 1 to 9, `check_stop` 0 to 11.

## What was not observed

The transcript does not record the `session_id` the hooks send. The journal (`~/.claude/plugins/data/decision-register-inline/<hash>/archive/iv-20261119T123740Z-757024ab.jsonl`) does, as above. Not observed: which implementation served, the I3 branch-build condition, `/clear` (V4), and any second trial.

## Earlier inference (superseded by the journal observation)

REQ-26 ends an interview when the received `session_id` changes. If the hook `session_id` had changed at compaction, the interview would have ended there. It did not end, and later hook calls kept counting on the same interview. That supports "stable" but depends on the REQ-26 session-change rule being implemented and exercised as the contract says, which this run does not check independently. The transcript `sessionId` equalling the hook `session_id` is an assumption.

## What a further observation would add

The server (or a throwaway hook) records the `session_id` it receives on each hook call, with timestamps, and the compare is made across a compaction. For example, a debug journal row or log line on `note_prompt` and `check_stop` carrying the received id, then a run with a `/compact` between two prompts. A drop-rule check (an id change at compaction would end the interview under REQ-26) needs the same record.

## Related

Manual `/compact` only. `/clear` (V4) was not run. The run used the TypeScript build (`REGSPIKE_IMPL=ts`) from a branch not stated in the transcript; the I3 branch-build condition in the issue is not established by this run.
