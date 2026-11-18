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
        .collect();
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
}
