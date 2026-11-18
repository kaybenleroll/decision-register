# I5 parity check, Rust versus TypeScript (REQ-71, AC3 of #67)

One-off check, run 2026-11-18. Not a permanent harness (a permanent one belongs with CI issue #32 if the differences below matter). Contract text and both implementations were left unchanged; each difference is an input to a user-owned decision.

## Builds compared

- Rust: branch `issue-67-i5-rs` (the I4 merge plus the S-90a harness commit), `cargo build --release`.
- TypeScript: commit `17e5c6ce43dbb6a333048831235fa463f2f4ee45` (branch `issue-68-i5-ts`), `npm ci && npm run build`, `node impl/ts/dist/regspike.mjs`.

## Method

The session driver is the suite's own `Server` class from `conformance/run.py` (same environment, `_meta` and process handling as the scenarios). A throwaway script (kept in the worktree `.scratch/`, not committed) sent the same 21 calls to each build, each on a fresh data, project and working directory under the same path, so the project key is identical:

1. `note_prompt`, `begin_interview` (grill-me, register), `log_item` q1, q2 (depends on q1), d1.
2. `note_prompt`; a volunteered `log_item` v1 with `source: user`; `resolve` q1 (model); `park` q2; `status`.
3. `check_stop` with a question ("Which region should we use?"), then `note_prompt`.
4. `reopen` q2 while Parked (an error case); `resolve` q2 (user) and d1 (model); `reopen` and `resolve` v1; `status`.
5. `check_stop` with a completion message (allowed, interview complete); `end_interview`; `check_stop` after the end.

Compared: every reply text (parsed JSON and raw text), the archived journal (22 events, parsed and raw lines), the decision log, the set of files written, file mode of the journal (600 on both), and stderr (empty on both).

## Normalised (only these)

- Timestamps `YYYY-MM-DDTHH:MM:SSZ` replaced by `<TS>` (22 journal lines on each build; also in the decision log).
- The interview id (`iv-<timestamp>-<8 hex>`, differs per run) replaced by `<IID>`.
- The scratch working-directory prefix in the `decision_log` path replaced by `<WORK>`.
- Nothing else: no pids, temp paths or other fields appear in the compared outputs.

## Result

Same: tool replies for 20 of the 21 calls on parsed JSON, the journal's event kinds, order, `seq`, every field value except `end.reason`, the set of files written (one archived journal, one decision log), the journal file mode, the decision-log header (key order and values), and the empty stderr.

Differences found (4):

1. Journal `end` event, `reason` field: Rust writes `null`, TypeScript writes `"end_interview"` for an `end_interview` call that ends a complete interview (`end_kind: ended_after_completion`). `contract/journal.md` lists `reason` for `end` without saying what a plain `end_interview` writes.
2. `check_stop` block reason for a question that is not in the register: Rust returns "Your question is not in the register: log it with log_item, then ask it. 2 open:" followed by the open items; TypeScript returns "That question is not in the decision register. Log it with log_item, then ask it again." (no item list). The suite checks a block decision, not this text.
3. Decision log body (`decision-<IID>.md`): the header lines match, the body does not. Rust writes one `## <id> (<kind>, <status>)` section per item and a line "reopened v1: check it (stale: none)"; TypeScript writes an `## Items` bullet list, a "v1 reopened: check it" line, and a `## Hook health` section that Rust omits. `contract/runtime.md` fixes only the header; whether the body needs to match is for the contract owner (I did not search the rest of the contract for body rules).
4. JSON key order, in every journal line and every reply: Rust sorts keys alphabetically (for example `{"existing":false,"interview_id":...}`), TypeScript uses schema order (`{"v":2,"ts":...,"seq":...,"kind":...}`). The parsed values are equal. Anything that compares journal lines as text, rather than as JSON, would see every line differ.

The rest of the session (status trees and counts, hook health, item statuses, the error text for reopening a Parked item, the finish gate allowing the complete interview, the end result) was identical after normalisation.
