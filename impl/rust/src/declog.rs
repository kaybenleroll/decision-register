//! The decision log (runtime.md "Decision log"): a markdown file the server writes itself,
//! never depending on the model. I3 scope: the file, its header and its location; the
//! interrupted-exit and unwritable-directory handling stay with I4.

use std::fmt::Write as _;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

use crate::journal::project_path;
use crate::register::{Item, Status};

/// What one write of the log needs to know.
pub struct LogView<'a> {
    pub interview_id: &'a str,
    /// The `seq` of the last journal event when the log is written.
    pub position: u64,
    /// `active`, `complete` or `ended`.
    pub state: &'a str,
    /// `register`, `plain` or `not engaged`.
    pub mode: &'a str,
    /// `none` while the interview runs, else an end kind.
    pub end_kind: &'a str,
    pub implicit: bool,
    pub items: Vec<&'a Item>,
    /// One line saying how the interview ended or stands; empty for none.
    pub note: &'a str,
    /// The reopen history in order: item id, reason, ids made stale.
    pub reopens: &'a [Reopen],
}

/// One `reopen` call, kept for the decision log (REQ-62).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Reopen {
    pub id: String,
    pub reason: String,
    pub stale: Vec<String>,
}

/// Where the logs of one interview go; the directory is chosen at the first write.
#[derive(Default)]
pub struct DecisionLog {
    dir: Option<PathBuf>,
    path: Option<PathBuf>,
    error: Option<String>,
}

impl DecisionLog {
    /// The absolute path of the log once one has been written.
    pub fn path(&self) -> Option<&PathBuf> {
        self.path.as_ref()
    }

    /// The last write failure, cleared by the next success (REQ-50).
    pub fn error(&self) -> Option<&str> {
        self.error.as_deref()
    }

    /// Writes the log through a temporary file renamed over the final name and returns
    /// its path. A failure is kept in [`DecisionLog::error`] and never raised.
    pub fn write(&mut self, view: &LogView<'_>) -> PathBuf {
        let dir = self.dir.clone().unwrap_or_else(choose_directory);
        self.dir = Some(dir.clone());
        let name = format!("decision-{}.md", view.interview_id);
        let final_path = dir.join(&name);
        let result = prepare(&dir).and_then(|()| {
            let tmp = dir.join(format!("{name}.tmp"));
            let _ = fs::remove_file(&tmp);
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&tmp)
                .and_then(|mut f| f.write_all(render(view).as_bytes()))
                .and_then(|()| fs::rename(&tmp, &final_path))
        });
        match result {
            Ok(()) => {
                self.path = Some(final_path.clone());
                self.error = None;
            }
            Err(e) => self.error = Some(format!("decision log write failed: {e}")),
        }
        final_path
    }
}

/// `<project>/.scratch` if it exists now, else `<project>/decision-logs`.
fn choose_directory() -> PathBuf {
    let project = PathBuf::from(project_path());
    let scratch = project.join(".scratch");
    if scratch.is_dir() {
        scratch
    } else {
        project.join("decision-logs")
    }
}

/// Creates the `decision-logs` directory (mode 0700) with its ignore-all `.gitignore`.
fn prepare(dir: &Path) -> std::io::Result<()> {
    if dir.file_name().is_some_and(|n| n == "decision-logs") {
        fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(dir)?;
        let ignore = dir.join(".gitignore");
        if !ignore.exists() {
            OpenOptions::new()
                .write(true)
                .create(true)
                .truncate(true)
                .mode(0o600)
                .open(&ignore)?
                .write_all(b"*\n")?;
        }
    }
    Ok(())
}

fn render(v: &LogView<'_>) -> String {
    let mut s = String::new();
    let _ = writeln!(s, "interview_id: {}", v.interview_id);
    let _ = writeln!(s, "journal_position: {}", v.position);
    let _ = writeln!(s, "state: {}", v.state);
    let _ = writeln!(s, "mode: {}", v.mode);
    let _ = writeln!(s, "end_kind: {}", v.end_kind);
    let _ = writeln!(s, "implicit_start: {}", v.implicit);
    let _ = writeln!(s, "\n# Decision log {}\n", v.interview_id);
    if !v.note.is_empty() {
        let _ = writeln!(s, "{}\n", v.note);
    }
    if v.items.is_empty() {
        s.push_str("No items were logged.\n");
    }
    for i in &v.items {
        let _ = write!(
            s,
            "## {} ({}, {})\n\n{}\n",
            i.id,
            i.kind.as_str(),
            i.status.name(),
            i.text.replace('\n', " ")
        );
        match &i.status {
            Status::Resolved(a) | Status::Stale(a) => {
                let _ = write!(
                    s,
                    "\n- answer: {}\n- source: {}\n- rationale: {}\n",
                    a.text(),
                    a.source().as_str(),
                    a.rationale()
                );
            }
            Status::Parked { by, reason } => {
                let _ = write!(s, "\n- parked by {}: {}\n", by.as_str(), reason);
            }
            Status::Open => {}
        }
        s.push('\n');
    }
    if !v.reopens.is_empty() {
        s.push_str("## Reopen history\n\n");
        for r in v.reopens {
            let stale = if r.stale.is_empty() {
                "none".to_string()
            } else {
                r.stale.join(", ")
            };
            let _ = writeln!(s, "- reopened {}: {} (stale: {})", r.id, r.reason, stale);
        }
        s.push('\n');
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn header_lines_come_first_in_the_pinned_order() {
        let text = render(&LogView {
            interview_id: "iv-1",
            position: 7,
            state: "ended",
            mode: "not engaged",
            end_kind: "stood_down",
            implicit: false,
            note: "",
            items: Vec::new(),
            reopens: &[],
        });
        let head: Vec<&str> = text.lines().take(6).collect();
        assert_eq!(
            head,
            [
                "interview_id: iv-1",
                "journal_position: 7",
                "state: ended",
                "mode: not engaged",
                "end_kind: stood_down",
                "implicit_start: false"
            ]
        );
    }

    #[test]
    fn reopen_history_shows_reason_and_stale_ids() {
        let reopens = [Reopen {
            id: "q3".into(),
            reason: "REO-EEE".into(),
            stale: vec!["q4".into(), "q5".into()],
        }];
        let view = |r: &'static [Reopen]| LogView {
            interview_id: "iv-1",
            position: 1,
            state: "active",
            mode: "register",
            end_kind: "none",
            implicit: false,
            note: "",
            items: Vec::new(),
            reopens: r,
        };
        let text = render(&LogView {
            reopens: &reopens,
            ..view(&[])
        });
        assert!(text.contains("- reopened q3: REO-EEE (stale: q4, q5)"));
        assert!(!render(&view(&[])).contains("Reopen history"));
    }
}
