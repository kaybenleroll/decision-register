//! Data model, transition table and Stale propagation (REQ-30, 31, 36, 38).
//!
//! A Resolved or Stale item carries its [`Answer`] inside the status variant, and an
//! [`Answer`] can only be built through [`Answer::new`], so a Resolved item without an
//! answer, source or one-line rationale cannot be represented (REQ-31).

use std::collections::HashSet;
use std::fmt;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Actor {
    User,
    Model,
}

impl Actor {
    pub fn as_str(self) -> &'static str {
        match self {
            Actor::User => "user",
            Actor::Model => "model",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "user" => Some(Actor::User),
            "model" => Some(Actor::Model),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Kind {
    Question,
    Subquestion,
    Decision,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Question => "question",
            Kind::Subquestion => "subquestion",
            Kind::Decision => "decision",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "question" => Some(Kind::Question),
            "subquestion" => Some(Kind::Subquestion),
            "decision" => Some(Kind::Decision),
            _ => None,
        }
    }
}

pub fn is_nonblank(s: &str) -> bool {
    s.chars().any(|c| !c.is_whitespace())
}

/// A single line with at least one non-whitespace character (contract `oneline`).
pub fn is_oneline(s: &str) -> bool {
    is_nonblank(s)
        && !s.chars().any(|c| {
            matches!(
                c,
                '\r' | '\n' | '\x0b' | '\x0c' | '\u{85}' | '\u{2028}' | '\u{2029}'
            )
        })
}

/// Ids match `^[A-Za-z0-9_.-]{1,64}$`.
pub fn is_valid_id(s: &str) -> bool {
    (1..=64).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'-'))
}

/// An answer with its source and one-line rationale; only constructible valid.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Answer {
    text: String,
    source: Actor,
    rationale: String,
}

impl Answer {
    /// On failure returns the offending parameter name and the reason.
    pub fn new(
        text: &str,
        source: Actor,
        rationale: &str,
    ) -> Result<Self, (&'static str, &'static str)> {
        if !is_nonblank(text) {
            return Err(("answer", "must not be blank"));
        }
        if !is_oneline(rationale) {
            return Err(("rationale", "must be a single non-blank line"));
        }
        Ok(Self {
            text: text.to_string(),
            source,
            rationale: rationale.to_string(),
        })
    }

    pub fn text(&self) -> &str {
        &self.text
    }

    pub fn source(&self) -> Actor {
        self.source
    }

    pub fn rationale(&self) -> &str {
        &self.rationale
    }
}

#[derive(Clone, PartialEq, Eq, Debug)]
pub enum Status {
    Open,
    Resolved(Answer),
    Parked {
        by: Actor,
        reason: String,
    },
    /// Was Resolved; a dependency was reopened. Keeps its old answer.
    Stale(Answer),
}

impl Status {
    pub fn name(&self) -> &'static str {
        match self {
            Status::Open => "Open",
            Status::Resolved(_) => "Resolved",
            Status::Parked { .. } => "Parked",
            Status::Stale(_) => "Stale",
        }
    }

    /// Position in the `status` tree order: Open, Stale, Parked, Resolved.
    fn rank(&self) -> u8 {
        match self {
            Status::Open => 0,
            Status::Stale(_) => 1,
            Status::Parked { .. } => 2,
            Status::Resolved(_) => 3,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Item {
    pub id: String,
    pub kind: Kind,
    pub text: String,
    pub parent: Option<String>,
    pub depends_on: Vec<String>,
    pub status: Status,
}

#[derive(Clone)]
pub struct NewItem {
    pub id: Option<String>,
    pub kind: Kind,
    pub text: String,
    pub parent: Option<String>,
    pub depends_on: Vec<String>,
    pub answer: Option<Answer>,
}

#[derive(Clone, PartialEq, Eq, Debug)]
pub enum RegisterError {
    DuplicateId(String),
    SubquestionNeedsParent,
    MissingItem(String),
    NoSuchItem(String),
    Transition {
        action: &'static str,
        id: String,
        status: &'static str,
    },
}

impl fmt::Display for RegisterError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RegisterError::DuplicateId(id) => write!(f, "duplicate id: {id}"),
            RegisterError::SubquestionNeedsParent => write!(f, "subquestion needs a parent"),
            RegisterError::MissingItem(id) => write!(f, "missing item: {id}"),
            RegisterError::NoSuchItem(id) => write!(f, "no such item: {id}"),
            RegisterError::Transition { action, id, status } => {
                write!(f, "cannot {action} {id}: item is {status}")
            }
        }
    }
}

#[derive(Default, Clone)]
pub struct Register {
    items: Vec<Item>,
    next_auto: u64,
}

/// Item counts by status.
#[derive(Default, PartialEq, Eq, Debug)]
pub struct Counts {
    pub open: usize,
    pub resolved: usize,
    pub parked: usize,
    pub stale: usize,
}

impl Register {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    /// Items in creation order.
    pub fn iter(&self) -> impl Iterator<Item = &Item> {
        self.items.iter()
    }

    pub fn get(&self, id: &str) -> Option<&Item> {
        self.items.iter().find(|i| i.id == id)
    }

    fn index(&self, id: &str) -> Option<usize> {
        self.items.iter().position(|i| i.id == id)
    }

    fn fresh_id(&mut self) -> String {
        loop {
            self.next_auto += 1;
            let id = format!("Q{}", self.next_auto);
            if self.index(&id).is_none() {
                return id;
            }
        }
    }

    /// Logs an item; returns its id and the status name it starts in.
    pub fn log(&mut self, new: NewItem) -> Result<(String, &'static str), RegisterError> {
        if let Some(id) = &new.id
            && self.index(id).is_some()
        {
            return Err(RegisterError::DuplicateId(id.clone()));
        }
        if new.kind == Kind::Subquestion && new.parent.is_none() {
            return Err(RegisterError::SubquestionNeedsParent);
        }
        for dep in new.parent.iter().chain(new.depends_on.iter()) {
            if self.index(dep).is_none() {
                return Err(RegisterError::MissingItem(dep.clone()));
            }
        }
        let id = match new.id {
            Some(id) => id,
            None => self.fresh_id(),
        };
        let status = match new.answer {
            Some(a) => Status::Resolved(a),
            None => Status::Open,
        };
        let name = status.name();
        self.items.push(Item {
            id: id.clone(),
            kind: new.kind,
            text: new.text,
            parent: new.parent,
            depends_on: new.depends_on,
            status,
        });
        Ok((id, name))
    }

    fn lookup(&self, id: &str) -> Result<usize, RegisterError> {
        self.index(id)
            .ok_or_else(|| RegisterError::NoSuchItem(id.to_string()))
    }

    /// Number of children (items naming `id` as parent), of any kind and status (REQ-35).
    pub fn child_count(&self, id: &str) -> usize {
        self.items
            .iter()
            .filter(|i| i.parent.as_deref() == Some(id))
            .count()
    }

    /// Whether `resolve` is allowed from the item's current status (REQ-38).
    pub fn check_resolve(&self, id: &str) -> Result<(), RegisterError> {
        let item = &self.items[self.lookup(id)?];
        if matches!(item.status, Status::Resolved(_)) {
            return Err(RegisterError::Transition {
                action: "resolve",
                id: id.to_string(),
                status: item.status.name(),
            });
        }
        Ok(())
    }

    /// Whether `park` is allowed from the item's current status (REQ-38).
    pub fn check_park(&self, id: &str) -> Result<(), RegisterError> {
        let item = &self.items[self.lookup(id)?];
        let allowed = match &item.status {
            Status::Open | Status::Stale(_) => true,
            Status::Parked { by, .. } => *by == Actor::Model,
            Status::Resolved(_) => false,
        };
        if allowed {
            Ok(())
        } else {
            Err(RegisterError::Transition {
                action: "park",
                id: id.to_string(),
                status: item.status.name(),
            })
        }
    }

    /// Resolve accepts Open, Stale and Parked (either parker); rejects Resolved.
    pub fn resolve(&mut self, id: &str, answer: Answer) -> Result<(), RegisterError> {
        self.check_resolve(id)?;
        let i = self.lookup(id)?;
        self.items[i].status = Status::Resolved(answer);
        Ok(())
    }

    /// Park accepts Open, Stale and model-Parked; rejects Resolved and user-Parked.
    pub fn park(&mut self, id: &str, by: Actor, reason: &str) -> Result<(), RegisterError> {
        self.check_park(id)?;
        let i = self.lookup(id)?;
        self.items[i].status = Status::Parked {
            by,
            reason: reason.to_string(),
        };
        Ok(())
    }

    /// Reopen accepts Resolved only. The item becomes Open and every Resolved item that
    /// depends on it, directly or transitively, becomes Stale. Returns the stale ids in
    /// creation order.
    pub fn reopen(&mut self, id: &str) -> Result<Vec<String>, RegisterError> {
        let i = self.lookup(id)?;
        if !matches!(self.items[i].status, Status::Resolved(_)) {
            return Err(RegisterError::Transition {
                action: "reopen",
                id: id.to_string(),
                status: self.items[i].status.name(),
            });
        }
        self.items[i].status = Status::Open;
        let mut seen: HashSet<usize> = HashSet::from([i]);
        let mut queue = vec![i];
        let mut stale = Vec::new();
        while let Some(cur) = queue.pop() {
            let cur_id = self.items[cur].id.clone();
            for j in 0..self.items.len() {
                if seen.contains(&j) || !self.items[j].depends_on.contains(&cur_id) {
                    continue;
                }
                seen.insert(j);
                queue.push(j);
                if let Status::Resolved(a) = &self.items[j].status {
                    self.items[j].status = Status::Stale(a.clone());
                    stale.push(j);
                }
            }
        }
        stale.sort_unstable();
        Ok(stale
            .into_iter()
            .map(|j| self.items[j].id.clone())
            .collect())
    }

    /// Items in `status` order: Open, Stale, Parked, Resolved; creation order within.
    pub fn ordered(&self) -> Vec<&Item> {
        let mut v: Vec<&Item> = self.items.iter().collect();
        v.sort_by_key(|i| i.status.rank()); // stable sort keeps creation order
        v
    }

    pub fn counts(&self) -> Counts {
        let mut c = Counts::default();
        for i in &self.items {
            match i.status {
                Status::Open => c.open += 1,
                Status::Resolved(_) => c.resolved += 1,
                Status::Parked { .. } => c.parked += 1,
                Status::Stale(_) => c.stale += 1,
            }
        }
        c
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ans() -> Answer {
        Answer::new("a", Actor::Model, "because").unwrap()
    }

    fn item(id: &str, deps: &[&str]) -> NewItem {
        NewItem {
            id: Some(id.to_string()),
            kind: Kind::Question,
            text: format!("text {id}"),
            parent: None,
            depends_on: deps.iter().map(|s| s.to_string()).collect(),
            answer: None,
        }
    }

    fn reg(specs: &[(&str, &[&str])]) -> Register {
        let mut r = Register::new();
        for (id, deps) in specs {
            r.log(item(id, deps)).unwrap();
        }
        r
    }

    #[test]
    fn answer_rejects_blank_and_multiline() {
        assert_eq!(
            Answer::new(" \t", Actor::User, "r").unwrap_err().0,
            "answer"
        );
        for bad in ["", "  ", "a\nb", "a\r\nb", "a\n", "a\u{2028}b"] {
            assert_eq!(
                Answer::new("x", Actor::User, bad).unwrap_err().0,
                "rationale",
                "{bad:?}"
            );
        }
        assert!(Answer::new("x", Actor::User, "one line; fine.").is_ok());
    }

    #[test]
    fn id_pattern() {
        assert!(is_valid_id("n-resolve.Open_1"));
        assert!(!is_valid_id(""));
        assert!(!is_valid_id("bad id"));
        assert!(!is_valid_id("q1\n"));
        assert!(!is_valid_id(&"a".repeat(65)));
    }

    #[test]
    fn log_rules() {
        let mut r = reg(&[("q1", &[])]);
        assert_eq!(
            r.log(item("q1", &[])),
            Err(RegisterError::DuplicateId("q1".into()))
        );
        let mut orphan = item("s", &[]);
        orphan.kind = Kind::Subquestion;
        assert_eq!(r.log(orphan), Err(RegisterError::SubquestionNeedsParent));
        assert_eq!(
            r.log(item("d", &["nope"])),
            Err(RegisterError::MissingItem("nope".into()))
        );
        let mut auto = item("x", &[]);
        auto.id = None;
        assert_eq!(r.log(auto).unwrap(), ("Q1".to_string(), "Open"));
        assert_eq!(r.len(), 2);
    }

    #[test]
    fn transition_table_is_exhaustive() {
        // (status, resolve allowed, park allowed, reopen allowed)
        let cases: [(&str, bool, bool, bool); 5] = [
            ("Open", true, true, false),
            ("Stale", true, true, false),
            ("ParkedModel", true, true, false),
            ("ParkedUser", true, false, false),
            ("Resolved", false, false, true),
        ];
        for (name, can_resolve, can_park, can_reopen) in cases {
            let build = || {
                let mut r = reg(&[("b", &[]), ("x", &["b"])]);
                match name {
                    "Stale" => {
                        r.resolve("b", ans()).unwrap();
                        r.resolve("x", ans()).unwrap();
                        r.reopen("b").unwrap();
                    }
                    "ParkedModel" => r.park("x", Actor::Model, "r").unwrap(),
                    "ParkedUser" => r.park("x", Actor::User, "r").unwrap(),
                    "Resolved" => r.resolve("x", ans()).unwrap(),
                    _ => {}
                }
                r
            };
            assert_eq!(
                build().resolve("x", ans()).is_ok(),
                can_resolve,
                "resolve {name}"
            );
            assert_eq!(
                build().park("x", Actor::Model, "r").is_ok(),
                can_park,
                "park {name}"
            );
            assert_eq!(build().reopen("x").is_ok(), can_reopen, "reopen {name}");
        }
    }

    #[test]
    fn rejection_changes_nothing_and_names_id_and_status() {
        let mut r = reg(&[("q", &[])]);
        r.resolve("q", ans()).unwrap();
        let err = r.park("q", Actor::Model, "r").unwrap_err();
        assert_eq!(err.to_string(), "cannot park q: item is Resolved");
        assert_eq!(r.get("q").unwrap().status.name(), "Resolved");
        assert_eq!(
            r.resolve("zz", ans()),
            Err(RegisterError::NoSuchItem("zz".into()))
        );
    }

    #[test]
    fn reopen_marks_transitive_resolved_dependents_stale() {
        let mut r = reg(&[
            ("a", &[]),
            ("b", &["a"]),
            ("c", &["b"]),
            ("d", &["a"]),
            ("e", &["a"]),
        ]);
        for id in ["a", "b", "c", "d"] {
            r.resolve(id, ans()).unwrap();
        } // e stays Open
        assert_eq!(r.reopen("a").unwrap(), vec!["b", "c", "d"]);
        assert_eq!(r.get("a").unwrap().status, Status::Open);
        assert_eq!(r.get("c").unwrap().status.name(), "Stale");
        assert_eq!(r.get("e").unwrap().status, Status::Open);
        // a Stale item keeps its answer and can be resolved again
        assert!(matches!(&r.get("b").unwrap().status, Status::Stale(a) if a.text() == "a"));
        r.resolve("b", ans()).unwrap();
    }

    #[test]
    fn park_clears_answer_and_resolve_clears_park() {
        let mut r = reg(&[("a", &[]), ("b", &["a"])]);
        r.resolve("a", ans()).unwrap();
        r.resolve("b", ans()).unwrap();
        r.reopen("a").unwrap();
        r.park("b", Actor::Model, "later").unwrap();
        assert!(matches!(
            &r.get("b").unwrap().status,
            Status::Parked {
                by: Actor::Model,
                ..
            }
        ));
        r.resolve("b", ans()).unwrap();
        assert_eq!(r.get("b").unwrap().status.name(), "Resolved");
    }

    #[test]
    fn ordering_and_counts() {
        let mut r = reg(&[("a", &[]), ("b", &["a"]), ("c", &[]), ("d", &[])]);
        r.resolve("a", ans()).unwrap();
        r.resolve("b", ans()).unwrap();
        r.reopen("a").unwrap();
        r.park("c", Actor::User, "x").unwrap();
        r.resolve("d", ans()).unwrap();
        let ids: Vec<&str> = r.ordered().iter().map(|i| i.id.as_str()).collect();
        assert_eq!(ids, ["a", "b", "c", "d"]);
        assert_eq!(
            r.counts(),
            Counts {
                open: 1,
                resolved: 1,
                parked: 1,
                stale: 1
            }
        );
    }
}
