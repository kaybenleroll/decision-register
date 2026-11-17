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
   inline code `...` removed (no backtick or newline inside), URLs `https?://\S+` removed, then every line whose
   first non-blank character is `>` removed (quote-line stripping).
4. `?` test: the message "asks" iff `S` contains `?`.

## 3. Labels
A label cites the item whose id is `Q<n>` (server-assigned form; `n` ASCII digits, compared as written, so `Q01` is not `Q1`).
Caller-supplied ids such as `q1` or `a1` are not citable by label (only by word overlap; decided 2026-11-17)
Case folding: the letter `Q` matches case-insensitively (`q1:` cites `Q1`); the digits are exact.
Labels are read from the unstripped message `U`, so a label inside code, a bold wrapper or a quote line still counts.
Forms:
- anywhere in a line, not preceded by a letter, digit or underscore: `Q1:`, `**Q1:**`, `**Q1**:`, `` `Q1:` ``
  (0-2 of `*` or backtick before; then `:` then 0-2 of `*`/backtick, or 1-2 of `*`/backtick then `:`).
- at line start only, after optional indentation and an optional list marker (`-`, `*`, `+`, `1.`, `1)`) and optional
  `*`/backtick wrapper: `Q1:`, `Q1.`, `Q1)`.
Not labels: `Q1` with no punctuation, `Q1?`, `Q1,`, mid-line `Q1.` or `Q1)`, `Q1-2`, `Q10` read as `Q1`.

## 4. Finish attempt
Finish attempt = no `?` in `S` AND not a single labelled request.
Single labelled request: after removing fences and URLs only (inline code and quote lines kept), exactly one
non-blank line remains and it starts (line-start form above, so `Q1:`, `Q1.`, `Q1)` with wrapper/list marker) with the
label of an Open or Stale item. That is a question turn about that item (presented = [that id]).

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
- finish attempt: block while anything is unresolved (REQ-43), classification `finish`.
- question turn concerning >= 1 item: allow, classification `question`, `presented` = ids.
- question turn concerning none (incl. `?` with only resolved/parked/unknown labels, or no label and < 3 shared words): block as unlogged, `presented` [].

## 8. `unknown`
The classifier returns `unknown` on an internal error, a timeout or an unparseable message (REQ-43). The tool schema types
`last_assistant_message` as a string, so a non-string is a schema error (no state change, never reaches the classifier), and
every string is classifiable: no natural message form yields `unknown`. Decided 2026-11-17: do not invent one. `unknown` (flat budget 4,
journal `presented` null) is covered by each implementation's unit tests (REQ-81) only. S-32d and the `unknown` half of S-43e are
not written. The existing contract text (tools.json, journal.md) stays valid for implementations.
