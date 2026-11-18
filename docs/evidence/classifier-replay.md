# Classifier replay (REQ-46), draft labels

**Status: PROVISIONAL.** The 30 labels were drafted by the assistant. The user's review is OUTSTANDING. Until the user has
reviewed the labels, both the sample and the verdict below are provisional; a changed label can change a result.

No turn text appears here or anywhere in the repository. The corpus and the labels are private and stay outside the
repository; this report holds aggregate counts, turn ids and contract section numbers only.

## Method

- Corpus: 30 assistant turns (ids F01 to F30) from stored grill-me transcripts, drawn with seed 20261118. Project groups that
  contributed (number of turns): ai-assisted-research (3), claude-transcript-sanitizer (3), construct-synthetic-reinsurance-data
  (4), decision-register (3), describe-data-poc-planning-tool (4), experimental-llamacpp (3), gitnexus-r-fork (3),
  random-llm-projects-claude-code-research (4), random-llm-projects-system-queries (3).
- Labels: a class per turn (finish attempt or question turn) and, for question turns, whether the turn concerns a logged item
  (`presented`). Where a turn's intent differs from what `contract/classifier.md` computes, the AMBIGUOUS turns take the draft
  label, which follows intent. Two turns (F01, F18) have no fixed `presented` value and are compared on class only.
- Driver: `conformance/replay_classifier.py`, black box over MCP stdio, no internals. For each turn it starts a fresh server
  (own `CLAUDE_PLUGIN_DATA` and project directory under `.scratch/`), then calls `note_prompt`, `begin_interview`, `log_item`
  and `check_stop` without `_meta`, as the hooks do. `check_stop` receives the turn as `last_assistant_message`.
- Register state: one Open item per turn. For `presented` true its text is built from content words of the turn (classifier.md
  section 5(b), 3 or more shared words); otherwise it is unrelated filler text. This is a proxy for the hand-labelled state:
  it tests the classifier on what each label says the turn concerns, not on a reconstructed historical register.
- Observed classification: read from the journal `stop` event (`classification`, `decision`, `presented`), because a block reply
  alone does not tell a finish block from an unlogged-question block (classifier.md section 7). `CLAUDE_PLUGIN_DATA` is a
  temporary directory under `.scratch/`. Nothing beyond the contract's journal was needed.
- Agreement: the observed class equals the label class and, for a question turn with a fixed `presented`, whether `presented` is
  non-empty matches. Pass (REQ-46): agreement of at least 90% and zero finish-labelled turns classified as question turns.
- Builds: TypeScript bundle `impl/ts/dist/regspike.mjs` from branch `issue-51-i2-ts`; Rust release binary built from the tip of
  branch `issue-18-i2-rs` (`ee54139`, "Rust: list all twelve contract tools").

## Results

Both builds gave identical per-turn results.

| Build | Agreement | finish to finish | finish to question | question to finish | question to question | Agreement >= 90% | Zero finish classified as question |
|---|---|---|---|---|---|---|---|
| TypeScript | 27/30 = 90.0% | 10 | 3 | 0 | 17 | pass | FAIL |
| Rust | 27/30 = 90.0% | 10 | 3 | 0 | 17 | pass | FAIL |

Overall verdict: **FAIL on the zero-finish-as-question criterion for both builds** (provisional on label review).

Per-turn (label, result; both builds):

- Agree, label finish attempt: F02, F03, F06, F08, F09, F10, F11, F26, F28, F30.
- Agree, label question turn: F01, F05, F07, F12, F13, F14, F15, F16, F17, F18, F19, F21, F23, F24, F25, F27, F29.
- Disagree, label finish attempt, classified question turn: F04, F20, F22.

## Disagreements

F04, F20 and F22 are all AMBIGUOUS in the draft labels: each is a closing replay by intent, labelled finish, but each contains
a question mark in the stripped message. The rule involved is classifier.md section 2 (step 4: the message asks iff the stripped
message `S` contains `?`) with section 4 (a finish attempt is a message with no `?` in `S` and not a single labelled request;
none of the three is a single labelled request). Section 7 then makes it a question turn that concerns no logged item, hence a
block as unlogged. Both builds implement the rule as written, so these are label-versus-rule differences, not implementation
defects. No classifier was changed.

Consequence: under REQ-46 as written, a closing replay that ends with an offer or a confirmation question is a "finish attempt
classified as a question turn", which fails the zero criterion. Whether to revise the rule (section 2 or 4) or to relabel these
turns by the rule is a separate decision, to be taken after the user reviews the labels.
