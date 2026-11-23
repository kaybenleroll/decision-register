//! Journal replay for `resume` (contract/journal.md "Replay failure modes", REQ-60).
//!
//! Rebuilds the register, the hook counts and the finish-gate caps from the events. A
//! torn final line is ignored; every other defect fails with `replay failed at line N`.

use crate::declog::Reopen;
use serde_json::{Map, Value};

use crate::register::{Actor, Answer, Kind, NewItem, Register};

/// Highest journal version this build reads.
const MAX_VERSION: u64 = 2;

const KINDS: [&str; 13] = [
    "start", "engage", "prompt", "hook", "item", "resolve", "park", "reopen", "stop", "complete",
    "mismatch", "resume", "end",
];
/// The only kinds a v1 line (spike 3 and earlier) can carry.
const V1_KINDS: [&str; 4] = ["item", "resolve", "park", "reopen"];

/// What a replay recovers.
#[derive(Default, Debug)]
pub struct Replayed {
    pub register: Register,
    /// `seq` of the last valid event.
    pub last_seq: u64,
    pub implicit: bool,
    /// The `start` event's skill (null for an implicit start).
    pub skill: Option<String>,
    pub hook_note_prompt: u64,
    pub hook_skill_started: u64,
    pub hook_check_stop: u64,
    /// Cap counters of the last `stop` event: prompt, finish, unlogged.
    pub blocks: (u64, u64, u64),
    /// The reopen history, in journal order.
    pub reopens: Vec<Reopen>,
}

fn fail(line: usize, why: &str) -> String {
    format!("replay failed at line {line}: {why}")
}

fn str_field<'a>(o: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    o.get(key).and_then(Value::as_str)
}

/// Replays journal `text`.
pub fn replay(text: &str) -> Result<Replayed, String> {
    let lines: Vec<&str> = text.split('\n').collect();
    // `split` leaves an empty last element when the text ends with a newline.
    let complete_end = text.ends_with('\n');
    let count = if complete_end {
        lines.len() - 1
    } else {
        lines.len()
    };
    let mut out = Replayed::default();
    for (idx, raw) in lines.iter().take(count).enumerate() {
        let n = idx + 1;
        let o = match serde_json::from_str::<Value>(raw) {
            Ok(Value::Object(o)) => o,
            Ok(_) => return Err(fail(n, "not a JSON object")),
            Err(_) if n == count && !complete_end => break, // torn final line
            Err(_) => return Err(fail(n, "invalid JSON")),
        };
        let v = o.get("v").map_or(Some(1), Value::as_u64);
        let Some(v) = v else {
            return Err(fail(n, "v must be an integer"));
        };
        if v > MAX_VERSION {
            return Err(fail(n, &format!("unsupported journal version {v}")));
        }
        let seq = if v == 1 {
            n as u64
        } else {
            o.get("seq")
                .and_then(Value::as_u64)
                .ok_or_else(|| fail(n, "seq missing"))?
        };
        if seq != out.last_seq + 1 {
            return Err(fail(n, "seq is not the previous plus one"));
        }
        let Some(kind) = str_field(&o, "kind") else {
            return Err(fail(n, "kind missing"));
        };
        let allowed = if v == 1 { &V1_KINDS[..] } else { &KINDS[..] };
        if !allowed.contains(&kind) {
            return Err(fail(n, &format!("unknown kind {kind}")));
        }
        apply(&mut out, kind, &o).map_err(|why| fail(n, &why))?;
        out.last_seq = seq;
    }
    Ok(out)
}

fn apply(out: &mut Replayed, kind: &str, o: &Map<String, Value>) -> Result<(), String> {
    let need = |key: &str| str_field(o, key).ok_or_else(|| format!("{key} missing"));
    match kind {
        "start" => {
            out.skill = str_field(o, "skill").map(str::to_owned);
            out.implicit = o.get("implicit").and_then(Value::as_bool).unwrap_or(false);
            out.hook_note_prompt = o.get("prompt_count").and_then(Value::as_u64).unwrap_or(0);
            out.hook_skill_started = u64::from(str_field(o, "route") == Some("hook"));
        }
        "hook" => match str_field(o, "tool") {
            Some("note_prompt") => out.hook_note_prompt += 1,
            Some("skill_started") => out.hook_skill_started += 1,
            Some("check_stop") => out.hook_check_stop += 1,
            _ => {}
        },
        "item" => {
            let kind = Kind::parse(need("item_kind")?).ok_or("item_kind invalid")?;
            let ids = |key: &str| -> Vec<String> {
                o.get(key)
                    .and_then(Value::as_array)
                    .map(|a| {
                        a.iter()
                            .filter_map(|v| v.as_str().map(String::from))
                            .collect()
                    })
                    .unwrap_or_default()
            };
            out.register
                .log(NewItem {
                    id: Some(need("id")?.to_string()),
                    kind,
                    text: need("text")?.to_string(),
                    parent: str_field(o, "parent").map(String::from),
                    depends_on: ids("depends_on"),
                    answer: None,
                })
                .map_err(|e| e.to_string())?;
        }
        "resolve" => {
            let source = Actor::parse(need("source")?).ok_or("source invalid")?;
            let answer = Answer::new(need("answer")?, source, need("rationale")?)
                .map_err(|(field, why)| format!("{field} {why}"))?;
            out.register
                .resolve(need("id")?, answer)
                .map_err(|e| e.to_string())?;
        }
        "park" => {
            let by = Actor::parse(need("parked_by")?).ok_or("parked_by invalid")?;
            let reason = need("reason")?;
            if reason.trim().is_empty() {
                return Err("reason must not be blank".to_string());
            }
            out.register
                .park(need("id")?, by, reason)
                .map_err(|e| e.to_string())?;
        }
        "reopen" => {
            let id = need("id")?;
            let stale = out.register.reopen(id).map_err(|e| e.to_string())?;
            out.reopens.push(Reopen {
                id: id.to_string(),
                reason: str_field(o, "reason").unwrap_or_default().to_string(),
                stale,
            });
        }
        "stop" => {
            let b = o.get("blocks");
            let get = |k: &str| {
                b.and_then(|b| b.get(k))
                    .and_then(Value::as_u64)
                    .unwrap_or(0)
            };
            out.blocks = (get("prompt"), get("finish"), get("unlogged"));
        }
        _ => {}
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const START: &str = r#"{"v":2,"ts":"t","seq":1,"kind":"start","interview_id":"iv","route":"hook","skill":"grill-me","session_id":"s","implicit":false,"prompt_count":1}"#;
    const ITEM: &str = r#"{"v":2,"ts":"t","seq":2,"kind":"item","id":"q1","item_kind":"question","text":"t","parent":null,"depends_on":[]}"#;

    fn text(lines: &[&str]) -> String {
        lines.join("\n") + "\n"
    }

    #[test]
    fn rebuilds_register_counts_and_caps() {
        let hook = r#"{"v":2,"ts":"t","seq":3,"kind":"hook","tool":"check_stop","session_id":"s"}"#;
        let stop = r#"{"v":2,"ts":"t","seq":4,"kind":"stop","decision":"block","classification":"finish","presented":null,"blocks":{"prompt":1,"finish":2,"unlogged":3}}"#;
        let res = r#"{"v":2,"ts":"t","seq":5,"kind":"resolve","id":"q1","answer":"a","source":"model","rationale":"r"}"#;
        let r = replay(&text(&[START, ITEM, hook, stop, res])).unwrap();
        assert_eq!(r.last_seq, 5);
        assert_eq!(r.register.counts().resolved, 1);
        assert_eq!(
            (r.hook_note_prompt, r.hook_skill_started, r.hook_check_stop),
            (1, 1, 1)
        );
        assert_eq!(r.blocks, (1, 2, 3));
    }

    #[test]
    fn torn_final_line_is_ignored_but_a_bad_middle_line_fails_by_number() {
        let torn = format!("{START}\n{ITEM}\n{{\"v\":2,\"se");
        assert_eq!(replay(&torn).unwrap().last_seq, 2);
        let bad = format!("{START}\nnot json\n{ITEM}\n");
        assert!(
            replay(&bad)
                .unwrap_err()
                .starts_with("replay failed at line 2:")
        );
    }

    #[test]
    fn seq_gap_repeat_unknown_kind_and_future_version_fail() {
        let gap = ITEM.replace("\"seq\":2", "\"seq\":3");
        assert!(
            replay(&text(&[START, &gap]))
                .unwrap_err()
                .contains("line 2")
        );
        assert!(
            replay(&text(&[START, START]))
                .unwrap_err()
                .contains("line 2")
        );
        let unknown = ITEM.replace("\"item\"", "\"bogus\"");
        assert!(
            replay(&text(&[START, &unknown]))
                .unwrap_err()
                .contains("line 2")
        );
        let future = ITEM.replace("\"v\":2", "\"v\":3");
        assert!(
            replay(&text(&[START, &future]))
                .unwrap_err()
                .contains("line 2")
        );
    }

    #[test]
    fn reopen_history_is_rebuilt_with_reason_and_stale_ids() {
        let res = r#"{"v":2,"ts":"t","seq":3,"kind":"resolve","id":"q1","answer":"a","source":"model","rationale":"r"}"#;
        let reo =
            r#"{"v":2,"ts":"t","seq":4,"kind":"reopen","id":"q1","reason":"REO-EEE","stale":[]}"#;
        let r = replay(&text(&[START, ITEM, res, reo])).unwrap();
        assert_eq!(
            r.reopens,
            [Reopen {
                id: "q1".into(),
                reason: "REO-EEE".into(),
                stale: Vec::new()
            }]
        );
    }

    #[test]
    fn v1_lines_replay_but_other_v1_kinds_fail() {
        let item = r#"{"kind":"item","id":"q1","item_kind":"question","text":"t","parent":null,"depends_on":[]}"#;
        let res = r#"{"kind":"resolve","id":"q1","answer":"a","source":"model","rationale":"r"}"#;
        let r = replay(&text(&[item, res])).unwrap();
        assert_eq!((r.last_seq, r.register.counts().resolved), (2, 1));
        let stop = r#"{"kind":"stop","decision":"allow"}"#;
        assert!(replay(&text(&[item, stop])).unwrap_err().contains("line 2"));
    }

    #[test]
    fn req_31_checks_apply_to_resolve_and_park() {
        let blank = r#"{"v":2,"ts":"t","seq":3,"kind":"resolve","id":"q1","answer":"a","source":"model","rationale":"  "}"#;
        assert!(
            replay(&text(&[START, ITEM, blank]))
                .unwrap_err()
                .contains("line 3")
        );
        let park =
            r#"{"v":2,"ts":"t","seq":3,"kind":"park","id":"q1","reason":" ","parked_by":"model"}"#;
        assert!(
            replay(&text(&[START, ITEM, park]))
                .unwrap_err()
                .contains("line 3")
        );
    }
}
