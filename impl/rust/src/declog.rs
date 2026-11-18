//! The decision log (runtime.md "Decision log"): a markdown file the server writes itself,
//! never depending on the model. I3 scope: the file, its header and its location; the
//! interrupted-exit and unwritable-directory handling stay with I4.

use std::fmt::Write as _;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::PathBuf;

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
}

/// Where the logs of one interview go; the directory is chosen at the first write.
#[derive(Default)]
pub struct DecisionLog {
    dir: Option<PathBuf>,
    path: Option<PathBuf>,
}

impl DecisionLog {
    /// The absolute path of the log once one has been written.
    pub fn path(&self) -> Option<&PathBuf> {
        self.path.as_ref()
    }

    /// Writes the log through a temporary file renamed over the final name.
    pub fn write(&mut self, view: &LogView<'_>) -> Result<PathBuf, String> {
        let dir = self.directory().map_err(|e| format!("decision log: {e}"))?;
        let name = format!("decision-{}.md", view.interview_id);
        let final_path = dir.join(&name);
        let tmp = dir.join(format!("{name}.tmp"));
        let _ = fs::remove_file(&tmp);
        let result = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&tmp)
            .and_then(|mut f| f.write_all(render(view).as_bytes()))
            .and_then(|()| fs::rename(&tmp, &final_path));
        match result {
            Ok(()) => {
                self.path = Some(final_path.clone());
                Ok(final_path)
            }
            Err(e) => Err(format!("decision log write failed: {e}")),
        }
    }

    fn directory(&mut self) -> std::io::Result<PathBuf> {
        if let Some(d) = &self.dir {
            return Ok(d.clone());
        }
        let project = PathBuf::from(project_path());
        let scratch = project.join(".scratch");
        let dir = if scratch.is_dir() {
            scratch
        } else {
            let logs = project.join("decision-logs");
            fs::DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(&logs)?;
            let ignore = logs.join(".gitignore");
            if !ignore.exists() {
                OpenOptions::new()
                    .write(true)
                    .create(true)
                    .truncate(true)
                    .mode(0o600)
                    .open(&ignore)?
                    .write_all(b"*\n")?;
            }
            logs
        };
        self.dir = Some(dir.clone());
        Ok(dir)
    }
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
            items: Vec::new(),
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
}
