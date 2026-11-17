# Language experiment record (X1, REQ-72)

Status: header and empty tables, written in slice X1 (Refs #16). Rule source: ADR 0003 section 4 (Proposed; moves to Accepted by the user), REQ-72, docs/build-plan.md section 4 (X1, E1). Tags as in docs/requirements.md. Anything marked OPEN is not decided; it is listed in the final section.

## 1. Experiment

Rust (`impl/rust/`) and TypeScript on Node with an esbuild single-file bundle (`impl/ts/`) are built in parallel (REQ-70). Both pass the same language-neutral black-box conformance suite (`conformance/`, REQ-71, REQ-80), expose identical tool names, schemas and journal format, and have their own offline unit tests (REQ-81). After the experiment one is retired under the rule in section 3. Product and binary names (`regspike-rs`, `regspike-ts`) are working names; gate G6 stays parked until R1 and nothing here depends on them.

## 2. What is recorded where

- Build table (section 4): one row per slice I1 to I5 and language. Build order only; these slices are not the experiment's changes.
- Change table (section 5): the experiment proper. The first five change requests made after milestone M2 (full suite green in both languages), not slices I1 to I5 (REQ-72, build plan section 4).
- Rows are appended at the time of each change by the person or agent making it, never reconstructed from session notifications (REQ-72). One slice or change per submission, so conformance failures on first submission are countable (ADR 0003 section 6).
- Defects after merge and release friction are assessed per language at close-out (slice E1), after release milestone R1, not at the fifth change (REQ-72, ADR 0003 section 4 step 3). They are recorded in section 6.

## 3. Pre-build retirement rule values (G3, confirmed 2026-11-16 as starting values; the user decides either way)

| Item | Value |
|---|---|
| Trigger | Both pass the conformance suite, and the first five change requests made after M2 have been applied to both; this file has five completed change rows, each with both languages filled |
| Order of first implementer | Change 1 Rust, 2 TypeScript, 3 Rust, 4 TypeScript, 5 Rust (the second agent works from a change already designed once) |
| Measures (four) | Tokens per change; conformance-suite failures on first submission per change; defects found after merge; release friction |
| Margin test | Paired per-change token differences (both languages made the same change); the report also gives mean tokens by role (first versus second implementer) |
| Margin | Mean tokens differ by more than 20%: the cheaper language is recommended. Within 20%: Rust is recommended for its type guarantees |
| Decision | The user decides; the recommendation is advisory. Decision and date go in section 7 |
| Retirement action | Tag the loser's last commit (`retired-rust` or `retired-ts`, [assistant] naming), archive its directory or branch, remove it from CI, release assets and the plugin's selection (REQ-73), drop its unit tests from CI; the suite stays |
| Not a measure | Fix-cycle counts (they tie when agents batch changes) |

Build alternation (build order only, build plan section 1): I1 Rust first, I2 TypeScript first, I3 Rust, I4 TypeScript, I5 Rust; the second language starts from the first's merged design. Separate PRs per slice and language.

## 4. Build table (slices I1 to I5)

Columns for these rows are not specified by ADR 0003 section 4; see OPEN 1.

| Slice | Language | First or second | Date merged | Tokens | Suite failures on first submission | Note |
|---|---|---|---|---|---|---|

## 5. Change table (retirement experiment)

Columns from ADR 0003 section 4 step 3 (change id, language that went first, tokens used, conformance-suite failures on first submission, date). Each change has one row per language so that the paired differences can be computed; the first-language column repeats on both rows. See OPEN 2 for the row layout.

| Change id | Order (1 to 5) | Language | Went first | Tokens used | Suite failures on first submission | Date |
|---|---|---|---|---|---|---|
| (change 1) | 1 | Rust | Rust |  |  |  |
| (change 1) | 1 | TypeScript | Rust |  |  |  |
| (change 2) | 2 | Rust | TypeScript |  |  |  |
| (change 2) | 2 | TypeScript | TypeScript |  |  |  |
| (change 3) | 3 | Rust | Rust |  |  |  |
| (change 3) | 3 | TypeScript | Rust |  |  |  |
| (change 4) | 4 | Rust | TypeScript |  |  |  |
| (change 4) | 4 | TypeScript | TypeScript |  |  |  |
| (change 5) | 5 | Rust | Rust |  |  |  |
| (change 5) | 5 | TypeScript | Rust |  |  |  |

### By-role summary (filled after change 5)

| Role | Mean tokens | Mean suite failures on first submission |
|---|---|---|
| First implementer |  |  |
| Second implementer |  |  |

| Language | Mean tokens per change | Paired difference (Rust minus TypeScript), mean and per change | Within 20%? |
|---|---|---|---|
| Rust |  |  |  |
| TypeScript |  |  |  |

## 6. Close-out assessment (slice E1, after R1)

| Language | Defects found after merge | Release friction (short note per release) |
|---|---|---|
| Rust |  |  |
| TypeScript |  |  |

## 7. Recommendation and decision

Recommendation by the rule: (blank until the table is complete). Decision by the user and date: (blank). ADR 0003 status moves to Accepted by the user; this file does not change it.

## 8. Out of scope

- Behaviour parity with the Python prototype (REQ-81).
- Go, Python and Bun compile as candidates (ADR 0003 section 5).
- The paid evaluation (research repo).
- MacOS and Windows builds, Rust cross-build (REQ-74).
- Naming (gate G6, parked to R1).
- Changing the 20% margin, the Rust tie-break or the measures: they are the user's to change; this file only applies them.

## 9. OPEN questions (not decided here)

1. OPEN: Build-table columns for slices I1 to I5, and whether tokens and suite failures are recorded for them at all (recommendation: record tokens, date and first-submission failures with the same definitions as section 5, as context only; they do not enter the margin test).
2. OPEN: Change table layout, one row per language per change (as above) versus one row per change with both languages in columns (recommendation: one row per language, as above; the pairing is by change id).
3. OPEN: How tokens are counted and from where (input plus output, cached tokens, subagent tokens, session versus change boundary), recorded at the time of the change (recommendation: total input plus output tokens for the agent session that made the change, including subagents, from the harness usage report at the end of that session, with the source named in the row).
4. OPEN: What counts as a change request and who picks the five (recommendation: the first five requests after M2 that alter behaviour covered by the suite, listed by issue number in the order raised; the user confirms the list at M2).
5. OPEN: Treatment of a change that fails the suite on first submission and is resubmitted (recommendation: count only the first submission's failures; tokens cover the whole change to green).
6. OPEN: Definition of a defect after merge and of release friction, and how each is scored at E1 (recommendation: defect is a confirmed bug in merged code of one language that the suite and unit tests missed, counted per language; friction is a free-text note per release, compared qualitatively).
7. OPEN: How the four measures combine when they disagree with the token rule (recommendation: tokens alone fix the recommendation as ADR 0003 section 4 step 5 says; the other measures are reported and the user weighs them).
8. OPEN: Sequencing of E1 relative to the fifth change if R1 is later (recommendation: E1 waits for both, as the build plan states; no interim decision).
9. OPEN: Cost and time budget for the experiment (the plan and issue set none; recommendation: no cap beyond the five changes, with the user able to stop early; a token ceiling per change only if the user wants one).
10. OPEN: Early-stop or abandonment conditions, for example one implementation failing to pass the suite by M2 (recommendation: user decision at M2; no automatic rule).
11. OPEN: Whether differing change sizes are normalised (changes may differ in size; recommendation: no normalisation, the paired design and the five-change limit are accepted as thin data per ADR 0003 section 7).
