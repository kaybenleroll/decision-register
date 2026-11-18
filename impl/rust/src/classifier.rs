//! Finish-attempt classifier, ported from contract/classifier.md (normative). No regex
//! crate: the fence, inline-code, URL, quote-line and label rules are scanned by hand.

use std::collections::HashSet;

/// Stop words (contract section 6, 88 words).
const STOP_WORDS: [&str; 88] = [
    "about", "all", "also", "an", "and", "any", "are", "as", "at", "be", "been", "but", "by",
    "can", "could", "did", "do", "does", "done", "each", "for", "from", "get", "got", "had", "has",
    "have", "he", "her", "how", "if", "in", "into", "is", "it", "just", "let", "may", "me",
    "might", "more", "most", "must", "my", "no", "not", "of", "on", "or", "other", "our", "out",
    "over", "shall", "she", "should", "so", "some", "such", "than", "that", "the", "their", "them",
    "then", "there", "they", "this", "to", "too", "up", "us", "very", "was", "we", "were", "what",
    "when", "where", "which", "who", "whom", "why", "will", "with", "would", "you", "your",
];

/// Shared word overlap needed to concern an item without a label.
const MIN_SHARED_WORDS: usize = 3;

/// One register item as the classifier sees it, in creation order.
pub struct ClassItem<'a> {
    pub id: &'a str,
    pub text: &'a str,
    /// Open or Stale: the only items a question can concern.
    pub live: bool,
}

#[derive(Clone, PartialEq, Eq, Debug)]
pub enum Class {
    /// Empty or whitespace-only message: allowed, journalled as a question with no ids.
    Empty,
    Finish,
    /// A question turn; an empty list is an unlogged question.
    Question(Vec<String>),
    /// Internal error or unparseable message (REQ-43). No natural message yields it.
    Unknown,
}

fn remove_fences(t: &str) -> String {
    let mut out = String::new();
    let mut rest = t;
    while let Some(start) = rest.find("```") {
        let after = &rest[start + 3..];
        let Some(end) = after.find("```") else { break };
        out.push_str(&rest[..start]);
        rest = &after[end + 3..];
    }
    out.push_str(rest);
    out
}

/// Removes single-backtick spans: a backtick, then non-backtick non-newline characters,
/// then a backtick, scanned left to right.
fn remove_inline_code(t: &str) -> String {
    let chars: Vec<char> = t.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '`' {
            let mut j = i + 1;
            while j < chars.len() && chars[j] != '`' && chars[j] != '\n' {
                j += 1;
            }
            if j < chars.len() && chars[j] == '`' {
                i = j + 1;
                continue;
            }
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

/// Removes `https?://` followed by one or more non-whitespace characters.
fn remove_urls(t: &str) -> String {
    let mut out = String::new();
    let mut rest = t;
    loop {
        let found = ["https://", "http://"]
            .iter()
            .filter_map(|p| rest.find(p).map(|i| (i, p.len())))
            .min();
        let Some((i, plen)) = found else { break };
        let tail = &rest[i + plen..];
        let n: usize = tail
            .chars()
            .take_while(|c| !c.is_whitespace())
            .map(char::len_utf8)
            .sum();
        out.push_str(&rest[..i]);
        if n == 0 {
            // `\S+` needs a character: keep the scheme text and move past it
            out.push_str(&rest[i..i + plen]);
            rest = tail;
        } else {
            rest = &tail[n..];
        }
    }
    out.push_str(rest);
    out
}

fn remove_quote_lines(t: &str) -> String {
    t.split('\n')
        .filter(|l| !l.trim_start().starts_with('>'))
        .collect::<Vec<_>>()
        .join("\n")
}

fn is_wrapper(c: char) -> bool {
    c == '*' || c == '`'
}

fn is_word_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// Digits of a label `Q<n>` starting at `chars[q]` (which is `Q`/`q`), and the index after.
fn label_digits(chars: &[char], q: usize) -> Option<(String, usize)> {
    if !matches!(chars.get(q), Some('Q' | 'q')) {
        return None;
    }
    let mut j = q + 1;
    while chars.get(j).is_some_and(char::is_ascii_digit) {
        j += 1;
    }
    (j > q + 1).then(|| (chars[q + 1..j].iter().collect(), j))
}

/// Mid-line form: `[*`]{0,2}Q<n>` then `:` (and 0-2 wrappers) or 1-2 wrappers then `:`.
fn mid_line_labels(chars: &[char], out: &mut Vec<String>) {
    for q in 0..chars.len() {
        let Some((digits, end)) = label_digits(chars, q) else {
            continue;
        };
        let tail_ok = chars.get(end) == Some(&':') || {
            let run = chars[end..].iter().take_while(|c| is_wrapper(**c)).count();
            (1..=2).contains(&run) && chars.get(end + run) == Some(&':')
        };
        if !tail_ok {
            continue;
        }
        let start_ok = |p: usize| p == 0 || !is_word_char(chars[p - 1]);
        let wrappers = chars[..q]
            .iter()
            .rev()
            .take(2)
            .take_while(|c| is_wrapper(**c))
            .count();
        if (0..=wrappers).any(|w| start_ok(q - w)) {
            out.push(digits);
        }
    }
}

/// Line-start form on one line; returns the label digits.
fn line_start_label(line: &str) -> Option<String> {
    let chars: Vec<char> = line.chars().collect();
    let skip = |mut i: usize, set: &[char]| {
        while chars.get(i).is_some_and(|c| set.contains(c)) {
            i += 1;
        }
        i
    };
    let indent = skip(0, &[' ', '\t']);
    let mut starts = vec![indent];
    // an optional list marker, which must be followed by at least one space or tab
    let mut m = indent;
    if matches!(chars.get(m), Some('-' | '*' | '+')) {
        m += 1;
    } else {
        let d = chars[m..].iter().take_while(|c| c.is_ascii_digit()).count();
        if d > 0 && matches!(chars.get(m + d), Some('.' | ')')) {
            m += d + 1;
        } else {
            m = indent;
        }
    }
    if m > indent && matches!(chars.get(m), Some(' ' | '\t')) {
        starts.push(skip(m, &[' ', '\t']));
    }
    for s in starts {
        let mut q = s;
        while q < s + 2 && chars.get(q).is_some_and(|c| is_wrapper(*c)) {
            q += 1;
        }
        // 0-2 wrappers: try each count that lands on the Q
        for w in (s..=q).rev() {
            if let Some((digits, end)) = label_digits(&chars, w) {
                let run = chars[end..]
                    .iter()
                    .take(2)
                    .take_while(|c| is_wrapper(**c))
                    .count();
                if matches!(chars.get(end + run), Some(':' | '.' | ')')) {
                    return Some(digits);
                }
            }
        }
    }
    None
}

/// Digits of every label in the unstripped message.
fn labels(u: &str) -> HashSet<String> {
    let chars: Vec<char> = u.chars().collect();
    let mut found = Vec::new();
    mid_line_labels(&chars, &mut found);
    found.extend(u.split('\n').filter_map(line_start_label));
    found.into_iter().collect()
}

fn server_label(id: &str) -> Option<&str> {
    let digits = id.strip_prefix('Q')?;
    (!digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit())).then_some(digits)
}

fn content_words(t: &str) -> HashSet<String> {
    let lower = t.to_lowercase();
    lower
        .split(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit()))
        .filter(|w| w.len() > 1 && !STOP_WORDS.contains(w))
        .map(str::to_string)
        .collect()
}

/// The id of the live item that a single labelled request names, if the message is one.
fn single_request<'a>(u: &str, items: &'a [ClassItem<'a>]) -> Option<&'a str> {
    let stripped = remove_urls(&remove_fences(u));
    let mut lines = stripped.split('\n').filter(|l| !l.trim().is_empty());
    let line = lines.next()?;
    if lines.next().is_some() {
        return None;
    }
    let digits = line_start_label(line)?;
    items
        .iter()
        .find(|i| i.live && server_label(i.id) == Some(digits.as_str()))
        .map(|i| i.id)
}

/// Section 5 tokens: lower-case maximal runs of `[a-z0-9]`.
fn tokens(t: &str) -> Vec<String> {
    t.to_lowercase()
        .split(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit()))
        .filter(|w| !w.is_empty())
        .map(str::to_string)
        .collect()
}

/// Words that void a closure sentence (contract section 4(c)).
const CLOSURE_GUARDS: [&str; 9] = [
    "except",
    "but",
    "apart",
    "besides",
    "remaining",
    "still",
    "only",
    "though",
    "however",
];

/// A closure sentence (contract section 4(c)): no guard word, and one of the closure phrases.
fn closure_sentence(sentence: &str) -> bool {
    let t = tokens(sentence);
    if t.iter().any(|w| CLOSURE_GUARDS.contains(&w.as_str())) {
        return false;
    }
    let within =
        |i: usize, n: usize, target: &str| t.iter().skip(i + 1).take(n).any(|w| w == target);
    (0..t.len()).any(|i| match t[i].as_str() {
        "all" | "everything" => within(i, 4, "resolved"),
        "nothing" | "none" => within(i, 2, "unresolved"),
        "no" => {
            let next = t.get(i + 1).map(String::as_str);
            next == Some("unresolved")
                || (next == Some("open")
                    && matches!(
                        t.get(i + 2).map(String::as_str),
                        Some("items" | "threads" | "branches" | "questions" | "decisions")
                    ))
        }
        _ => false,
    })
}

/// Openers of the final sentence (contract section 4(b)).
const REPLAY_OPENERS: [&[&str]; 6] = [
    &["shall", "i"],
    &["should", "i"],
    &["want", "me", "to"],
    &["ready", "to"],
    &["do", "you", "want", "me", "to"],
    &["would", "you", "like", "me", "to"],
];

/// Closure pieces: split at each newline and after each `.`, `!` or `:` followed by whitespace.
fn closure_pieces(t: &str) -> Vec<&str> {
    let mut pieces = Vec::new();
    let mut start = 0;
    let mut chars = t.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        let end = if c == '\n' {
            Some(i)
        } else if matches!(c, '.' | '!' | ':')
            && chars.peek().is_some_and(|(_, n)| n.is_whitespace())
        {
            Some(i + 1)
        } else {
            None
        };
        if let Some(e) = end {
            pieces.push(&t[start..e]);
            start = i + c.len_utf8();
        }
    }
    pieces.push(&t[start..]);
    pieces
}

/// The tail rules of a closing replay (contract section 4(a)-(c)) on the stripped text `S`.
/// The caller has already established that the message concerns no Open or Stale item.
fn closing_replay(s: &str) -> bool {
    let Some(q) = s.find('?') else { return false };
    let after = &s[q + 1..];
    if after.contains('?')
        || !after
            .chars()
            .all(|c| c.is_whitespace() || "*_)\"'".contains(c))
    {
        return false;
    }
    let before = &s[..q];
    // the final sentence starts after the last newline or after the last `.`, `!` or `:`
    // that is followed by a space, whichever is later
    let nl = before.rfind('\n').map_or(0, |i| i + 1);
    let punct = before
        .match_indices(['.', '!', ':'])
        .filter(|(i, _)| before[i + 1..].starts_with(' '))
        .map(|(i, _)| i + 1)
        .max()
        .unwrap_or(0);
    let start = nl.max(punct);
    let opener = tokens(&before[start..]);
    if !REPLAY_OPENERS
        .iter()
        .any(|o| opener.len() >= o.len() && opener.iter().zip(o.iter()).all(|(a, b)| a == b))
    {
        return false;
    }
    closure_pieces(&before[..start])
        .into_iter()
        .any(closure_sentence)
}

pub fn classify(message: &str, items: &[ClassItem<'_>]) -> Class {
    if message.trim().is_empty() {
        return Class::Empty;
    }
    let s = remove_quote_lines(&remove_urls(&remove_inline_code(&remove_fences(message))));
    let asks = s.contains('?');
    let single = single_request(message, items);
    if !asks {
        return match single {
            Some(id) => Class::Question(vec![id.to_string()]),
            None => Class::Finish,
        };
    }
    let cited = labels(message);
    let words = content_words(message);
    let presented = items
        .iter()
        .filter(|i| i.live)
        .filter(|i| {
            server_label(i.id).is_some_and(|d| cited.contains(d))
                || content_words(i.text).intersection(&words).count() >= MIN_SHARED_WORDS
        })
        .map(|i| i.id.to_string())
        .collect::<Vec<_>>();
    if presented.is_empty() && closing_replay(&s) {
        return Class::Finish;
    }
    Class::Question(presented)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn items() -> Vec<ClassItem<'static>> {
        vec![
            ClassItem {
                id: "Q1",
                text: "storage engine choice",
                live: true,
            },
            ClassItem {
                id: "Q2",
                text: "retention period policy",
                live: true,
            },
            ClassItem {
                id: "Q3",
                text: "closed question here",
                live: false,
            },
            ClassItem {
                id: "q4",
                text: "caller supplied identifier",
                live: true,
            },
        ]
    }

    fn q(ids: &[&str]) -> Class {
        Class::Question(ids.iter().map(|s| s.to_string()).collect())
    }

    #[test]
    fn stop_word_list_has_88_distinct_words() {
        let set: HashSet<_> = STOP_WORDS.iter().collect();
        assert_eq!(set.len(), 88);
    }

    #[test]
    fn empty_and_finish() {
        let it = items();
        assert_eq!(classify("  \n\t", &it), Class::Empty);
        assert_eq!(classify("All done.", &it), Class::Finish);
        assert_eq!(
            classify("see `why?` and https://x.test/a?b", &it),
            Class::Finish
        );
        assert_eq!(classify("```\nwhy?\n```\nfine", &it), Class::Finish);
        assert_eq!(classify("> quoted?\nplain", &it), Class::Finish);
    }

    #[test]
    fn labels_cite_exact_server_ids_only() {
        let it = items();
        assert_eq!(classify("Q1: which one?", &it), q(&["Q1"]));
        assert_eq!(classify("**q2:** which one?", &it), q(&["Q2"]));
        assert_eq!(classify("- Q1. which one?", &it), q(&["Q1"]));
        assert_eq!(classify("Q01: which one?", &it), q(&[]));
        assert_eq!(classify("q4: which one?", &it), q(&[]));
        assert_eq!(classify("Q3: which one?", &it), q(&[]));
        assert_eq!(classify("xQ1: which one?", &it), q(&[]));
        assert_eq!(classify("Q1? something", &it), q(&[]));
        assert_eq!(classify("see Q1. then ask?", &it), q(&[]));
    }

    #[test]
    fn single_labelled_request_is_a_question_turn() {
        let it = items();
        assert_eq!(classify("Q1: pick a store", &it), q(&["Q1"]));
        assert_eq!(classify("1.Q1. pick", &it), Class::Finish);
        assert_eq!(classify("-Q1. pick", &it), Class::Finish);
        assert_eq!(classify("Q3: pick a store", &it), Class::Finish);
        assert_eq!(classify("Q1: pick\nQ2: pick", &it), Class::Finish);
    }

    #[test]
    fn word_overlap_needs_three_content_words() {
        let it = items();
        assert_eq!(classify("which storage engine choice?", &it), q(&["Q1"]));
        assert_eq!(classify("storage engine only?", &it), q(&[]));
        assert_eq!(
            classify("storage engine choice and retention period policy?", &it),
            q(&["Q1", "Q2"])
        );
    }

    #[test]
    fn inline_code_scan_is_single_backtick_only() {
        let it = items();
        assert_eq!(classify("a ``?`` b", &it), q(&[]));
        assert_eq!(classify("a `x?` b", &it), Class::Finish);
    }

    const CLOSED: &str = "All threads are resolved. ";

    fn replay(s: &str) -> bool {
        closing_replay(s)
    }

    #[test]
    fn closure_sentence_phrases() {
        assert!(closure_sentence("Everything is now resolved"));
        assert!(closure_sentence("all the items are resolved"));
        assert!(!closure_sentence(
            "all of the five items are fully resolved"
        ));
        assert!(closure_sentence("Nothing is unresolved"));
        assert!(closure_sentence("none unresolved"));
        assert!(!closure_sentence("nothing at all is unresolved"));
        assert!(closure_sentence("No unresolved items"));
        assert!(closure_sentence("There are no open decisions"));
        assert!(closure_sentence("no open threads left"));
        assert!(!closure_sentence("no open issues"));
        assert!(!closure_sentence("no open"));
        assert!(!closure_sentence("resolved all"));
    }

    #[test]
    fn closure_sentence_guard_words_void_it() {
        for g in [
            "except",
            "but",
            "apart",
            "besides",
            "remaining",
            "still",
            "only",
            "though",
            "however",
        ] {
            let text = format!("all resolved {g} one");
            assert!(!closure_sentence(&text), "{g}");
        }
    }

    #[test]
    fn closing_replay_accepts_each_opener() {
        for o in [
            "Shall I",
            "should i",
            "Want me to",
            "Ready to",
            "Do you want me to",
            "Would you like me to",
        ] {
            assert!(replay(&format!("{CLOSED}{o} finish?")), "{o}");
        }
        assert!(!replay(&format!("{CLOSED}Can I finish?")));
        assert!(!replay(&format!("{CLOSED}Shall we finish?")));
        assert!(!replay(&format!("{CLOSED}Do you want to finish?")));
    }

    #[test]
    fn closing_replay_question_mark_must_be_single_and_last() {
        assert!(replay(&format!("{CLOSED}Shall I stop?")));
        assert!(replay(&format!("{CLOSED}Shall I stop?  \n")));
        assert!(replay(&format!("{CLOSED}**Shall I stop?**")));
        assert!(replay(&format!("{CLOSED}(Shall I stop?)")));
        assert!(replay(&format!("{CLOSED}\"Shall I stop?\"")));
        assert!(replay(&format!("{CLOSED}'Shall I stop?'")));
        assert!(!replay(&format!("{CLOSED}Shall I stop?!")));
        assert!(!replay(&format!("{CLOSED}Shall I stop? Thanks")));
        assert!(!replay(&format!("{CLOSED}Shall I stop??")));
        assert!(!replay(&format!("Is it done? {CLOSED}Shall I stop?")));
        assert!(!replay(&format!("{CLOSED}Shall I stop")));
    }

    #[test]
    fn closing_replay_final_sentence_boundaries() {
        // after a newline, after `.`/`!`/`:` followed by a space; the later boundary wins
        assert!(replay("All resolved\nShall I stop?"));
        assert!(replay("All resolved! Shall I stop?"));
        assert!(replay("All resolved: shall I stop?"));
        assert!(replay("All resolved. Ok\nShall I stop?"));
        // a `.` without a following space does not end a sentence
        assert!(!replay("All resolved.Shall I stop?"));
        // an opener buried mid-sentence is not the opener
        assert!(!replay("All resolved, so shall I stop?"));
        // the opener sentence itself cannot supply the closure
        assert!(!replay("Shall I stop now that all is resolved?"));
        assert!(!replay("Shall I stop?"));
    }

    #[test]
    fn closing_replay_closure_pieces_split_and_guard_independently() {
        assert!(replay("Done.\nAll resolved\nShall I stop?"));
        assert!(replay(
            "Everything resolved! Nothing is unresolved. Shall I stop?"
        ));
        // a guard word voids only its own sentence
        assert!(replay("Still working. All resolved. Shall I stop?"));
        assert!(!replay("All resolved but one. Shall I stop?"));
        assert!(!replay("All resolved, however. Shall I stop?"));
        assert!(!replay("Done. Shall I stop?"));
    }

    #[test]
    fn classify_closing_replay_is_a_finish_attempt() {
        let it = items();
        assert_eq!(classify("All resolved. Shall I stop?", &it), Class::Finish);
        assert_eq!(classify("All resolved. Shall I stop", &it), Class::Finish);
        // closure sentence inside code or a quote line is invisible
        assert_eq!(classify("> all resolved\nShall I stop?", &it), q(&[]));
        assert_eq!(classify("`all resolved` Shall I stop?", &it), q(&[]));
        // ordinary questions are unchanged
        assert_eq!(classify("Done. Shall I stop?", &it), q(&[]));
    }

    #[test]
    fn classify_closing_replay_concerning_an_item_stays_a_question() {
        let it = items();
        // a label for an Open item
        assert_eq!(
            classify("All resolved. Shall I stop? Q1: unsure", &it),
            q(&["Q1"])
        );
        assert_eq!(classify("Q1: all resolved. Shall I stop?", &it), q(&["Q1"]));
        // three shared content words with an Open item
        assert_eq!(
            classify(
                "All resolved, storage engine choice included. Shall I stop?",
                &it
            ),
            q(&["Q1"])
        );
        // a label for a Resolved item concerns nothing, so the replay still finishes
        assert_eq!(
            classify("Q3: all resolved. Shall I stop?", &it),
            Class::Finish
        );
        // two shared words are not enough
        assert_eq!(
            classify("All resolved, storage engine settled. Shall I stop?", &it),
            Class::Finish
        );
    }

    #[test]
    fn classify_empty_and_single_request_precede_the_replay_rule() {
        let it = items();
        assert_eq!(classify("   ", &it), Class::Empty);
        assert_eq!(classify("Q1: all resolved, shall I stop?", &it), q(&["Q1"]));
    }
}
