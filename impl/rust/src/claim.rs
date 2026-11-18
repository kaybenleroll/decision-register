//! Pid file, liveness and claim (runtime.md "Pid file and claim", REQ-52, build plan F1).
//!
//! No lock primitive: ownership is a `<id>.pid` file holding the owner's pid and process
//! start time, created with `O_EXCL`. Every function takes the project directory so the
//! unit tests can run against a scratch directory.

use std::collections::hash_map::RandomState;
use std::fs::{self, OpenOptions};
use std::hash::{BuildHasher, Hasher};
use std::io::{self, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use serde_json::Value;

/// What a pid file records.
#[derive(Debug, PartialEq, Eq)]
pub struct PidInfo {
    pub pid: u64,
    pub start: Option<u64>,
}

/// Field 22 (`starttime`) of `/proc/<pid>/stat`: the 20th token after the last `)`.
/// `None` when the file cannot be read (not Linux, or no such process).
pub fn proc_start(pid: u64) -> Option<u64> {
    let text = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    start_from_stat(&text)
}

fn start_from_stat(text: &str) -> Option<u64> {
    let rest = &text[text.rfind(')')? + 1..];
    rest.split_whitespace().nth(19)?.parse().ok()
}

fn pid_exists(pid: u64) -> bool {
    Path::new(&format!("/proc/{pid}")).exists()
}

pub fn pid_path(dir: &Path, id: &str) -> PathBuf {
    dir.join(format!("{id}.pid"))
}

/// The one-line JSON content of this process's pid file for `id`.
pub fn own_content(id: &str) -> String {
    let pid = u64::from(std::process::id());
    // Written by hand: the key order pid, start, interview_id is part of the contract.
    let start = proc_start(pid).map_or_else(|| "null".to_string(), |s| s.to_string());
    format!(
        "{{\"pid\":{pid},\"start\":{start},\"interview_id\":{}}}\n",
        Value::from(id)
    )
}

/// Parses a pid file; `None` for empty or unparseable content (that means no owner).
pub fn parse(text: &str) -> Option<PidInfo> {
    let v: Value = serde_json::from_str(text.trim()).ok()?;
    Some(PidInfo {
        pid: v.get("pid")?.as_u64()?,
        start: v.get("start").and_then(Value::as_u64),
    })
}

/// Liveness: the pid exists and, when a start time was recorded, it still matches.
/// A pid file naming this very process counts only when `id` is its current claim.
pub fn is_alive(info: &PidInfo, id: &str, own_claim: Option<&str>) -> bool {
    if info.pid == u64::from(std::process::id()) {
        return own_claim == Some(id);
    }
    if !pid_exists(info.pid) {
        return false;
    }
    match (info.start, proc_start(info.pid)) {
        (Some(recorded), Some(now)) => recorded == now,
        (Some(_), None) => false,
        (None, _) => true,
    }
}

/// Whether the journal `id` in `dir` has a live owner.
pub fn owner_alive(dir: &Path, id: &str, own_claim: Option<&str>) -> bool {
    fs::read_to_string(pid_path(dir, id))
        .ok()
        .and_then(|t| parse(&t))
        .is_some_and(|info| is_alive(&info, id, own_claim))
}

fn create_dir(dir: &Path) -> io::Result<()> {
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(dir)
}

/// Creates `<id>.pid` exclusively (mode 0600) holding this process; `AlreadyExists`
/// means the id clashed.
pub fn create_new(dir: &Path, id: &str) -> io::Result<()> {
    create_dir(dir)?;
    create_with(dir, id, &own_content(id))
}

fn create_with(dir: &Path, id: &str, content: &str) -> io::Result<()> {
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(pid_path(dir, id))?
        .write_all(content.as_bytes())
}

pub fn remove(dir: &Path, id: &str) {
    let _ = fs::remove_file(pid_path(dir, id));
}

fn random_hex() -> String {
    let mut h = RandomState::new().build_hasher();
    h.write_u32(std::process::id());
    format!("{:08x}", h.finish() & 0xffff_ffff)
}

/// Claims a journal that is not live-owned: the stale-replace sequence of runtime.md
/// (rename aside, verify, create exclusively, delete the aside). Errors name the failure
/// as `journal already claimed`.
pub fn claim(dir: &Path, id: &str) -> Result<(), String> {
    let lost = || format!("journal already claimed: {id}");
    let pid_file = pid_path(dir, id);
    if let Ok(seen) = fs::read_to_string(&pid_file) {
        let aside = dir.join(format!(
            "{id}.pid.stale-{}-{}",
            std::process::id(),
            random_hex()
        ));
        if fs::rename(&pid_file, &aside).is_ok() {
            if fs::read_to_string(&aside).ok().as_deref() != Some(seen.as_str()) {
                // Another claimer's live file: put it back unless a newer one exists.
                if !pid_file.exists() {
                    let _ = fs::rename(&aside, &pid_file);
                }
                return Err(lost());
            }
            create_new_for_claim(dir, id).map_err(|_| lost())?;
            let _ = fs::remove_file(&aside);
            return Ok(());
        }
    }
    create_new_for_claim(dir, id).map_err(|_| lost())
}

fn create_new_for_claim(dir: &Path, id: &str) -> io::Result<()> {
    create_with(dir, id, &own_content(id))
}

/// One journal in the project directory that no live process owns.
#[derive(Debug, PartialEq, Eq)]
pub struct Unfinished {
    pub id: String,
    pub last_event: u64,
}

/// The last valid event number of a journal text (a torn final line is ignored; v1 lines
/// take their line number).
pub fn last_event(text: &str) -> u64 {
    scan_text(text).0
}

/// The last valid event number and whether an `end` event is present: a journal that
/// holds one is finished and is never offered, whatever its file name says.
fn scan_text(text: &str) -> (u64, bool) {
    let (mut last, mut ended) = (0, false);
    for (n, line) in text.lines().enumerate() {
        if let Ok(Value::Object(o)) = serde_json::from_str::<Value>(line) {
            last = o.get("seq").and_then(Value::as_u64).unwrap_or(n as u64 + 1);
            ended |= o.get("kind").and_then(Value::as_str) == Some("end");
        }
    }
    (last, ended)
}

/// Unfinished journals with no live owner (`own_claim` is this server's current
/// interview, never offered), most recently modified first, ties by id ascending.
pub fn unfinished(dir: &Path, own_claim: Option<&str>) -> Vec<Unfinished> {
    let Ok(entries) = fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut found = Vec::new();
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let Some(id) = name.strip_suffix(".jsonl") else {
            continue;
        };
        if own_claim == Some(id) || owner_alive(dir, id, own_claim) {
            continue;
        }
        let mtime = entry
            .metadata()
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map_or(0, |d| d.as_nanos());
        let text = fs::read_to_string(entry.path()).unwrap_or_default();
        let (last, ended) = scan_text(&text);
        if !ended {
            found.push((mtime, id.to_string(), last));
        }
    }
    found.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(&b.1)));
    found
        .into_iter()
        .map(|(_, id, last_event)| Unfinished { id, last_event })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("regspike-claim-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn start_time_is_the_twentieth_token_after_the_last_paren() {
        let stat = "42 (a b) c) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 777 20";
        assert_eq!(start_from_stat(stat), Some(777));
        assert_eq!(start_from_stat("no paren"), None);
    }

    #[test]
    fn own_process_has_a_start_time() {
        assert!(proc_start(u64::from(std::process::id())).is_some());
    }

    #[test]
    fn parse_rejects_garbage_and_accepts_null_start() {
        assert_eq!(parse("not json\n"), None);
        assert_eq!(parse(""), None);
        assert_eq!(
            parse("{\"pid\":5,\"start\":null,\"interview_id\":\"x\"}\n"),
            Some(PidInfo {
                pid: 5,
                start: None
            })
        );
    }

    #[test]
    fn liveness_needs_pid_and_matching_start() {
        let me = u64::from(std::process::id());
        let parent = u64::from(std::os::unix::process::parent_id());
        let start = proc_start(parent);
        assert!(start.is_some());
        assert!(is_alive(&PidInfo { pid: parent, start }, "x", None));
        assert!(!is_alive(
            &PidInfo {
                pid: parent,
                start: Some(1)
            },
            "x",
            None
        ));
        assert!(is_alive(
            &PidInfo {
                pid: parent,
                start: None
            },
            "x",
            None
        ));
        assert!(!is_alive(
            &PidInfo {
                pid: u64::MAX / 2,
                start: None
            },
            "x",
            None
        ));
        // Own pid counts only for the current claim.
        assert!(!is_alive(
            &PidInfo {
                pid: me,
                start: None
            },
            "x",
            None
        ));
        assert!(is_alive(
            &PidInfo {
                pid: me,
                start: None
            },
            "x",
            Some("x")
        ));
    }

    #[test]
    fn create_new_is_exclusive_and_private() {
        let dir = scratch("excl");
        create_new(&dir, "iv-a").unwrap();
        let err = create_new(&dir, "iv-a").unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::AlreadyExists);
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(pid_path(&dir, "iv-a"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
        let info = parse(&fs::read_to_string(pid_path(&dir, "iv-a")).unwrap()).unwrap();
        assert_eq!(info.pid, u64::from(std::process::id()));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn claim_replaces_a_stale_file_and_leaves_no_aside() {
        let dir = scratch("stale");
        fs::write(
            pid_path(&dir, "iv-b"),
            "{\"pid\":999999999,\"start\":1,\"interview_id\":\"iv-b\"}\n",
        )
        .unwrap();
        claim(&dir, "iv-b").unwrap();
        let info = parse(&fs::read_to_string(pid_path(&dir, "iv-b")).unwrap()).unwrap();
        assert_eq!(info.pid, u64::from(std::process::id()));
        let leftovers = fs::read_dir(&dir).unwrap().flatten().count();
        assert_eq!(leftovers, 1);
        // A second claim while the file is ours is a lost race only if it changed; here
        // the same content is replaced again, which is the stale path for a dead owner.
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn claim_without_a_pid_file_creates_one_and_exclusive_create_loses_to_a_racer() {
        let dir = scratch("fresh");
        claim(&dir, "iv-c").unwrap();
        assert!(pid_path(&dir, "iv-c").exists());
        let err = create_new_for_claim(&dir, "iv-c").unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::AlreadyExists);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn own_content_has_the_contract_key_order() {
        let text = own_content("iv-k");
        assert!(text.starts_with("{\"pid\":") && text.ends_with("\"interview_id\":\"iv-k\"}\n"));
        assert!(text.find("\"start\"").unwrap() < text.find("\"interview_id\"").unwrap());
        assert!(parse(&text).is_some());
    }

    #[test]
    fn a_journal_with_an_end_event_is_not_unfinished() {
        let dir = scratch("ended");
        fs::write(
            dir.join("iv-e.jsonl"),
            "{\"v\":2,\"seq\":1,\"kind\":\"start\"}\n{\"v\":2,\"seq\":2,\"kind\":\"end\"}\n",
        )
        .unwrap();
        assert!(unfinished(&dir, None).is_empty());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn unfinished_lists_dead_owners_newest_first_and_skips_live_and_own() {
        let dir = scratch("scan");
        let line =
            "{\"v\":2,\"ts\":\"t\",\"seq\":1,\"kind\":\"hook\"}\n{\"v\":2,\"seq\":2}\n{\"torn";
        for id in ["iv-1", "iv-2", "iv-3", "iv-own"] {
            fs::write(dir.join(format!("{id}.jsonl")), line).unwrap();
        }
        let parent = u64::from(std::os::unix::process::parent_id());
        let live = json!({"pid": parent, "start": proc_start(parent), "interview_id": "iv-3"});
        fs::write(pid_path(&dir, "iv-3"), format!("{live}\n")).unwrap();
        fs::write(pid_path(&dir, "iv-2"), "garbage").unwrap();
        let found = unfinished(&dir, Some("iv-own"));
        let ids: Vec<&str> = found.iter().map(|u| u.id.as_str()).collect();
        assert_eq!(ids.len(), 2);
        assert!(ids.contains(&"iv-1") && ids.contains(&"iv-2"));
        assert!(found.iter().all(|u| u.last_event == 2));
        let _ = fs::remove_dir_all(&dir);
    }
}
