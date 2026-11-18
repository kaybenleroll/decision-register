# Finish-attempt classifier (contract)

Normative for both implementations (REQ-43; build plan 5.1). Ports the prototype
`reference/armG/register_store.py:679-730` in behaviour, plus the decisions of 2026-11-17: only `Q<n>` ids are citable by
label; a model-parked item is not concerned by a question; an empty or whitespace-only message is allowed and journalled as
classification `question`, decision `allow`, `presented` `[]`; word overlap reads the unstripped message; the stop-word list
below is fixed. Wording only: no tool name, parameter, result shape or journal event shape changes, so `tool_schema` stays 4.

## 1. Input and result
Input: the `last_assistant_message` string and the register (item ids, texts, statuses).
Result of one classification: `finish` (no ids), `question` with `presented` = list of item ids
(possibly empty), or `unknown`.

## 2. Pre-processing (in this order)
1. Empty rule: a message that is empty or only whitespace (Unicode `str.strip()`/`trim()` empty)
   is ALLOWED and is not a finish attempt (user decision Q3). Journal: `classification` "question",
   `decision` "allow", `presented` []. Counts no block.
2. `U` = message unchanged (used for labels and for word overlap).
3. `S` (used only for the `?` test) = U with, in order: fenced code ```...``` removed (non-greedy, across lines),
   inline code removed (one backtick, then any run of characters that are neither a backtick nor a newline, possibly empty,
   then one backtick; scanned left to right, so only single-backtick spans are recognised: in a double-backtick span such as
   ``` a ``?`` b ``` the pair ` `` ` is an empty span, and the `?` between the pairs stays in `S`), URLs `https?://\S+` removed, then every line whose
   first non-blank character is `>` removed (quote-line stripping).
4. `?` test: the message "asks" iff `S` contains `?`. (A closing replay, section 4, is nevertheless a finish attempt.)

## 3. Labels
A label cites the item whose id is `Q<n>` (server-assigned form; `n` ASCII digits, compared as written, so `Q01` is not `Q1`).
Caller-supplied ids such as `q1` or `a1` are not citable by label (only by word overlap; decided 2026-11-17)
Case folding: the letter `Q` matches case-insensitively (`q1:` cites `Q1`); the digits are exact.
Labels are read from the unstripped message `U`, so a label inside code, a bold wrapper or a quote line still counts.
Forms:
- anywhere in a line, not preceded by a letter, digit or underscore: `Q1:`, `**Q1:**`, `**Q1**:`, `` `Q1:` ``
  (0-2 of `*` or backtick before; then `:` then 0-2 of `*`/backtick, or 1-2 of `*`/backtick then `:`).
- at line start only, after optional indentation and an optional list marker and optional `*`/backtick wrapper: `Q1:`,
  `Q1.`, `Q1)`, then 0-2 of `*`/backtick (so `**Q1**.` and `` `Q1`) `` are labels, as are the mid-line forms). A list
  marker is `-`, `*` or `+`, or one or more digits then `.` or `)` (`10.`, `3)`), and must be followed by at least one
  space or tab: `- Q1.` and `10. Q1.` are labels, `-Q1.` and `1.Q1.` are not (a bare `*Q1.` reads `*` as a wrapper).
Not labels: `Q1` with no punctuation, `Q1?`, `Q1,`, mid-line `Q1.` or `Q1)`, `Q1-2`, `-Q1.` (no space after the marker),
`Q10` read as `Q1`.

## 4. Finish attempt
Finish attempt = (no `?` in `S` AND not a single labelled request) OR a closing replay.
Single labelled request: after removing fences and URLs only (inline code and quote lines kept), exactly one
non-blank line remains and it starts (line-start form above, so `Q1:`, `Q1.`, `Q1)` with wrapper/list marker) with the
label of an Open or Stale item. That is a question turn about that item (presented = [that id]). This exemption is
evaluated first and is unchanged by the closing-replay rule.

Closing replay: a message whose `S` contains `?` is a finish attempt, not a question turn, iff it is not a single labelled
request, it concerns no Open or Stale item (section 5, read on the unstripped message `U`: no label cited for an Open/Stale
item and fewer than 3 shared content words with every Open/Stale item), and all of (a), (b), (c) hold. "Tokens" are as in
section 5 (lower-case, maximal runs of `[a-z0-9]`).
(a) `S` contains exactly one `?`, and after it only whitespace and the characters `*`, `_`, `)`, `"`, `'`.
(b) The final sentence is the text of `S` from just after the last newline, or after the last `.`, `!` or `:` that is followed
    by a space (whichever is latest), up to and including that `?`. Its first tokens are one of: `shall i`, `should i`,
    `want me to`, `ready to`, `do you want me to`, `would you like me to`.
(c) The text of `S` before the final sentence contains a closure sentence. Split that text into sentences at each newline and
    after each `.`, `!` or `:` followed by whitespace. A sentence is a closure sentence iff its tokens contain none of `except`,
    `but`, `apart`, `besides`, `remaining`, `still`, `only`, `though`, `however`, and contain one of: `all` or `everything`
    followed within the next four tokens by `resolved`; `nothing` or `none` followed within the next two tokens by
    `unresolved`; `no` followed by `unresolved`; `no` followed by `open` and then one of `items`, `threads`, `branches`,
    `questions`, `decisions`.
Order of evaluation: empty rule; single labelled request (question); no `?` in `S` (finish); section 5 concern; if the message
concerns no Open/Stale item and is a closing replay, finish; otherwise a question turn as in section 7. A reclassified turn is
`finish` with `presented` [].

## 5. Which items a question concerns
Only Open and Stale items can be concerned or presented. Resolved items and Parked items (either `parked_by`) are never
concerned: a question citing only a Resolved/Parked item's label, with no other overlap, concerns nothing, so it is an
unlogged question (blocked, counts toward the unlogged cap, presented = []). If it also cites or overlaps an Open/Stale
item, that item is concerned and the resolved/parked label is ignored. (Matches build plan 5.1 and REQ-43; the prototype
likewise blocked a question citing only resolved ids. A model-parked item counts as unresolved for the finish gate but is not
concerned by a question; decided 2026-11-17.)
An Open/Stale item is concerned iff (a) its label is cited, or (b) failing that, the content words of the message `U`
and of the item's `text` share at least 3 distinct words.
Content words: lower-case `U` and the text; tokens are maximal runs of `[a-z0-9]` (ASCII; other characters,
including non-ASCII letters, are separators); drop tokens of length 1; drop stop words; compare as sets (a repeated word counts once).
`presented` = every concerned item, once each, in creation order.

## 6. Stop words (full list, 88)
about, all, also, an, and, any, are, as, at, be, been, but, by, can, could, did, do, does, done, each, for, from, get,
got, had, has, have, he, her, how, if, in, into, is, it, just, let, may, me, might, more, most, must, my, no, not, of,
on, or, other, our, out, over, shall, she, should, so, some, such, than, that, the, their, them, then, there, they,
this, to, too, up, us, very, was, we, were, what, when, where, which, who, whom, why, will, with, would, you, your
(`a` and `i` are length-1 and dropped anyway). Deliberately NOT stop words: first, second, last, after, before, only,
now, while, another, under, its, key, choice, use (scenario item texts use them).

## 7. Decision
- finish attempt (including a closing replay): block while anything is unresolved (REQ-43), classification `finish`, `presented` [].
- question turn concerning >= 1 item: allow, classification `question`, `presented` = ids.
- question turn concerning none (incl. `?` with only resolved/parked/unknown labels, or no label and < 3 shared words): block as unlogged, `presented` [].
- The `reason` text of a block is not specified for an unlogged block: the journal row (`classification`, `decision`,
  `presented`) is the contract, and a conformance scenario tells finish from unlogged by that row, never by the `reason`.
  A finish block's `reason` names the open items (REQ-45), so it contains the id of an open item.

## 8. `unknown`
The classifier returns `unknown` on an internal error, a timeout or an unparseable message (REQ-43). The tool schema types
`last_assistant_message` as a string, so a non-string is a schema error (no state change, never reaches the classifier), and
every string is classifiable: no natural message form yields `unknown`. Decided 2026-11-17: do not invent one. `unknown` (flat budget 4,
journal `presented` null) is covered by each implementation's unit tests (REQ-81) only. S-32d and the `unknown` half of S-43e are
not written. The existing contract text (tools.json, journal.md) stays valid for implementations.

## 9. Change log
- 2026-11-18: closing-replay rule (candidate e5) added to sections 2, 4 and 7, user decision of the same date, on the replay
  evidence in `docs/evidence/classifier-replay.md` (REQ-46: finish attempts classified as question turns by the `?` test).
  Wording only: no tool name, parameter, result shape or journal event shape changes, so `tool_schema` stays 4.
