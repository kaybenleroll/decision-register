# ADR 0003: Build Rust and TypeScript in parallel, then retire one

Date: 2026-11-16. Status: Accepted, 2026-11-18, accepted by the user. Gate decisions folded 2026-11-16. On 2026-11-18 the user also chose to build the remaining slices in both languages in lockstep (build order only; the retirement experiment's change order in section 4 is unchanged).

Tags as in docs/requirements.md: [user], [verified], [assistant], [open]. Evidence and judgement are separated. Depends on ADR 0002 (register design).

## 1. Context

The user would not reuse Python merely because it exists, leaned towards TypeScript, and asked for the choice to be explored (docs/requirements-inputs.md, section A). Spikes could not separate the two leaders, so the user chose to build both and retire one (REQ-70).

## 2. Decision

1. Build Rust and TypeScript on Node (esbuild single-file bundle) in parallel as a time-boxed experiment (REQ-70). [user]
2. Both sit behind one language-neutral black-box conformance suite (a standard-library Python script driving each server over stdio) with identical tool names, schemas and journal format (REQ-71, REQ-80). Each also has its own offline unit tests (REQ-81). [user]
3. After the experiment, one is retired under the procedure in section 4. [user: the rule exists] [user: gate walk 2026-11-16] The values below are confirmed by the user 2026-11-16 as starting values; the user decides either way.
4. Distribution and platform: REQ-73, REQ-74. Names `regspike-rs` and `regspike-ts` are working names [user: gate walk 2026-11-16] parked; decided at the release milestone (slice R1).

## 3. Evidence

Source: docs/evidence/spikes.md. Judgement is labelled.

Spike 2 (docs/evidence/spike2/; five candidates, same spec, six offline tests each, live Stop-hook gate):
- All five passed the live gate. Start median/p95 ms: Rust 2.85/3.21, Go 3.6/4.1, ts-bun 51.1/54.9, ts-node 82.3/85.2, Python 586.7/636.0. Idle RSS MB: Rust 5.04, Go 8.8, ts-bun 42.5, ts-node 67.0, Python 73.5. Start and RSS are 20-run medians but from one build per candidate on one machine. All five meet REQ-90 (start under 1 s, idle memory under 200 MB), so it does not discriminate between them.
- Not like-for-like and must not be used to rank: dependency counts (transitive counts use five different methods), build seconds (different inclusion of downloads), artefact bytes (ts-bun embeds the runtime at 109 MB; ts-node needs Node).
- Type safety (spike 2 table): Rust makes a Resolved item without an answer a compile error, transition legality is runtime; TypeScript makes it a compile error via a discriminated union, but only in tsc, erased at runtime, so journal replay is untyped `JSON.parse`; Go can build empty-string values. Judgement: this is why REQ-31 also requires the check at the interface where the language cannot enforce it.

Spike 3 (docs/evidence/spike3/; Rust vs TypeScript on Node, three changes each):
- Both reached green first time on each change (1/1/1), 0 failing invocations, 7/7 black-box scenarios. The test did not discriminate: both agents batched all three changes into one patch, so fix-cycle counts tie and are not independent.
- Tokens, tool calls and time: Rust 54.9k / 14 / 134 s, TypeScript 38.7k / 7 / 60 s. These came from harness task notifications and are not in the evidence files [recalled]. n = 1 per language. Judgement: they suggest TypeScript was cheaper for this task; they do not establish it.
- No compiler or type-checker catch was observed in either.

What the evidence does not say: nothing here measures long-run maintenance, defect rate after merge, or release friction. That is what the experiment is for.

## 4. Retirement rule (REQ-72), as a checkable procedure

Starting values, confirmed by the user 2026-11-16 as starting values [user: gate walk 2026-11-16] (REQ-72 requires the rule to be fixed before the real build starts). The user decides either way.

1. Trigger. Both implementations pass the conformance suite, and the first five real changes have been applied to both. The five real changes are the first five change requests made after milestone M2 (full suite green in both), not slices I1 to I5. Check: `docs/evidence/language-experiment.md` has five completed change rows, each with both languages filled. [assistant]
2. Order. The language that goes first alternates between changes, Rust, TypeScript, Rust, TypeScript, Rust, because the second agent works from a change already designed once. The file records which went first for each change. [assistant, from REQ-72]
3. Recording. At the time of each change, not reconstructed afterwards from session notifications, the person or agent making the change appends a row to `docs/evidence/language-experiment.md` with: change id, language that went first, tokens used, number of conformance-suite failures on first submission, and date. Defects found after merge and release friction (short free-text note per release) are assessed per language at close-out (slice E1), after the release milestone R1, not at the fifth change. [REQ-72]
4. Measures. Four: tokens per change; conformance-suite failures on first submission; defects after merge; release friction. [REQ-72] [user: gate walk 2026-11-16]
5. Margin rule. The test uses the paired per-change token differences (both languages made the same change), and the report also gives mean tokens by role, first versus second implementer, so a first-mover effect is visible. If mean tokens per change differ by more than 20%, the cheaper language is recommended. Within 20%, Rust is recommended for its type guarantees. The 20% margin and the Rust tie-break are confirmed by the user 2026-11-16 as starting values [user: gate walk 2026-11-16]; no evidence fixes them (spike 3's 30% gap is a single trial).
6. Decision. The user decides; the recommendation is advisory (REQ-72). Record the decision and date in the same file.
7. Retirement action. Tag the loser's last commit (for example `retired-rust` or `retired-ts`), archive its directory (or branch), remove it from CI and from the release assets and from the plugin's selection (REQ-73), and drop its unit tests from CI. The conformance suite stays. Consequence for the winner: `REGSPIKE_IMPL` selection and the launcher may be simplified. [assistant: tag naming]

Known defect in the starting rule: see section 6 (failed acceptance runs vs fix cycles).

## 5. Alternatives set aside

- Go. Passed the live gate and had the best balance of small binary (8.1 MB) and low RSS (8.8 MB), start 3.6 ms. Set aside because the user's choice was between Rust and TypeScript after spike 2 [user]; the evidence offers no technical disqualifier. Weaker on type safety: `Resolved` can be built with empty strings, and `Stale` existed in the type but no transition produced it (spike 2 notes). Judgement: a legitimate candidate dropped for scope, not on evidence.
- Python as control. 587 ms median start and the largest runtime footprint (73.5 MB, needs Python). Meets REQ-90 but is the slowest by two orders of magnitude, and the user did not want to reuse it merely because the prototype was Python.
- Bun compile (ts-bun). 109 MB artefact embeds the runtime; its start (51 ms) and RSS (42.5 MB) were better than ts-node, but it gives up the 752 KB bundle that needs only Node, which REQ-74 treats as the platform-independent option. Journal replay is untyped JSON.parse, as with Node. Judgement: no advantage worth a third implementation.

## 6. Contradiction found in the requirements (for the user)

REQ-72 lists "failed acceptance runs per change" as a measure but also says "fix-cycle counts are not a measure: the change-request test showed they tie when agents batch changes". These are close to the same quantity. Either drop failed acceptance runs from the measures, or define it narrowly (failures of the conformance suite on first submission of a change, counted by the harness, not by the agent). Resolved at the gate walk [user: gate walk 2026-11-16]: the four confirmed measures use the narrow definition (conformance-suite failures on first submission), which the build plan's one-slice-per-submission rule makes countable. The spike-3 tie came from agents batching, which that rule prevents.

## 7. Consequences

- Double the implementation and review cost for the experiment's duration; drift risk is bounded by the conformance suite (requirements section 12, risk 2).
- The experiment's own data is thin (five changes). The decision is the user's, not the arithmetic's.
- If neither language is clearly better, the Rust tie-break is a judgement favouring type guarantees, not an empirical result.
