//! Minimal append-only JSONL journal (contract/journal.md, runtime.md "Journal location").
//!
//! I2 scope: the writer only. Events are queued and written by [`Journal::flush`]; the
//! callers flush at every `check_stop`. Durability (flush on exit, archive, pid file,
//! pruning, replay) belongs to I4. A failed write is kept in the queue and reported
//! through [`Journal::write_error`]; it never fails a tool call (runtime.md, REQ-50).

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

const JOURNAL_VERSION: u64 = 2;

/// Days since 1970-01-01 to (year, month, day), proleptic Gregorian.
pub fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (yoe + era * 400 + i64::from(m <= 2), m, d)
}

/// Seconds since the epoch, plus the test-only `REGSPIKE_CLOCK_OFFSET_SECONDS`.
pub fn now_secs() -> i64 {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(0));
    let offset = std::env::var("REGSPIKE_CLOCK_OFFSET_SECONDS")
        .ok()
        .and_then(|v| v.trim().parse::<i64>().ok())
        .unwrap_or(0);
    secs + offset
}

/// RFC 3339 UTC, whole seconds.
pub fn rfc3339(secs: i64) -> String {
    let (y, m, d) = civil_from_days(secs.div_euclid(86_400));
    let t = secs.rem_euclid(86_400);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        t / 3600,
        t % 3600 / 60,
        t % 60
    )
}

fn env_nonempty(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.is_empty())
}

/// `<data root>/<project key>`: key is the first 16 hex digits of SHA-256(project path).
pub fn project_dir() -> PathBuf {
    let root = env_nonempty("CLAUDE_PLUGIN_DATA").map_or_else(
        || std::env::temp_dir().join("decision-register-data"),
        PathBuf::from,
    );
    let project = env_nonempty("CLAUDE_PROJECT_DIR").unwrap_or_else(|| {
        std::env::current_dir()
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default()
    });
    let digest = Sha256::digest(project.as_bytes());
    let key: String = digest.iter().take(8).map(|b| format!("{b:02x}")).collect();
    root.join(key)
}

pub struct Journal {
    path: PathBuf,
    queue: Vec<String>,
    seq: u64,
    write_error: Option<String>,
}

impl Journal {
    pub fn new(interview_id: &str) -> Self {
        Self {
            path: project_dir().join(format!("{interview_id}.jsonl")),
            queue: Vec::new(),
            seq: 0,
            write_error: None,
        }
    }

    /// The `seq` of the last event queued (0 if none).
    pub fn seq(&self) -> u64 {
        self.seq
    }

    pub fn write_error(&self) -> Option<&str> {
        self.write_error.as_deref()
    }

    /// Queues one event: the envelope fields first, then `fields` (a JSON object).
    pub fn event(&mut self, kind: &str, fields: Value) {
        self.seq += 1;
        let mut line = Map::new();
        line.insert("v".into(), json!(JOURNAL_VERSION));
        line.insert("ts".into(), json!(rfc3339(now_secs())));
        line.insert("seq".into(), json!(self.seq));
        line.insert("kind".into(), json!(kind));
        if let Value::Object(extra) = fields {
            line.extend(extra);
        }
        self.queue.push(Value::Object(line).to_string());
    }

    /// Writes the queued events in order; on failure keeps them for the next flush.
    pub fn flush(&mut self) {
        if self.queue.is_empty() {
            return;
        }
        match self.try_write() {
            Ok(()) => {
                self.queue.clear();
                self.write_error = None;
            }
            Err(e) => self.write_error = Some(format!("journal write failed: {e}")),
        }
    }

    fn try_write(&self) -> std::io::Result<()> {
        if let Some(dir) = self.path.parent() {
            fs::DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(dir)?;
        }
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .mode(0o600)
            .open(&self.path)?;
        let mut text = self.queue.join("\n");
        text.push('\n');
        file.write_all(text.as_bytes())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rfc3339_known_instants() {
        assert_eq!(rfc3339(0), "1970-01-01T00:00:00Z");
        assert_eq!(rfc3339(1_795_000_000), "2026-11-18T11:06:40Z");
    }

    #[test]
    fn events_are_gapless_and_carry_the_envelope() {
        let mut j = Journal::new("iv-test");
        j.event("hook", json!({"tool": "check_stop", "session_id": "s"}));
        j.event("prompt", json!({"session_id": "s", "count": 1}));
        assert_eq!(j.seq(), 2);
        let first: Value = serde_json::from_str(&j.queue[0]).unwrap();
        assert_eq!(first["v"], 2);
        assert_eq!(first["seq"], 1);
        assert_eq!(first["kind"], "hook");
        assert_eq!(first["tool"], "check_stop");
    }
}
