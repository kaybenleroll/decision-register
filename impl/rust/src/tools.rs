//! Tool handlers. The tool schemas themselves come from `contract/tools.json`.
//!
//! I1 scope: the register tools plus `note_prompt`. There is no journal yet, so
//! `last_event` counts the state changes a journal would record.

use std::collections::hash_map::RandomState;
use std::fmt::Display;
use std::hash::{BuildHasher, Hasher};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value, json};

use crate::register::{
    Actor, Answer, Item, Kind, NewItem, Register, Status, is_nonblank, is_valid_id,
};

/// Tools this build implements, in the order they are listed.
pub const IMPLEMENTED: [&str; 7] = [
    "begin_interview",
    "log_item",
    "resolve",
    "park",
    "reopen",
    "status",
    "note_prompt",
];

const TOOL_SCHEMA: u64 = 4;
const JOURNAL_SCHEMA: u64 = 2;
const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");
/// Harness-injected subagent hand-back prompts start with exactly this (REQ-41).
const AGENT_MESSAGE_PREFIX: &str = "<agent-message ";

type Args = Map<String, Value>;

/// The outcome of a `tools/call`: text plus the `isError` flag.
pub struct ToolResult {
    pub text: String,
    pub is_error: bool,
}

fn bad(msg: impl Display) -> String {
    format!("invalid arguments: {msg}")
}

fn check_known(args: &Args, allowed: &[&str]) -> Result<(), String> {
    match args.keys().find(|k| !allowed.contains(&k.as_str())) {
        Some(k) => Err(bad(format!("unknown argument {k}"))),
        None => Ok(()),
    }
}

fn str_arg<'a>(args: &'a Args, name: &str) -> Result<Option<&'a str>, String> {
    match args.get(name) {
        None => Ok(None),
        Some(Value::String(s)) => Ok(Some(s)),
        Some(_) => Err(bad(format!("{name} must be a string"))),
    }
}

fn required<T>(name: &str, v: Option<T>) -> Result<T, String> {
    v.ok_or_else(|| bad(format!("missing required parameter {name}")))
}

fn text_arg(args: &Args, name: &str) -> Result<String, String> {
    let s = required(name, str_arg(args, name)?)?;
    if is_nonblank(s) {
        Ok(s.to_string())
    } else {
        Err(bad(format!("{name} must not be blank")))
    }
}

fn id_value(name: &str, s: &str) -> Result<String, String> {
    if is_valid_id(s) {
        Ok(s.to_string())
    } else {
        Err(bad(format!("{name} must match ^[A-Za-z0-9_.-]{{1,64}}$")))
    }
}

fn id_arg(args: &Args, name: &str) -> Result<String, String> {
    id_value(name, required(name, str_arg(args, name)?)?)
}

fn opt_id_arg(args: &Args, name: &str) -> Result<Option<String>, String> {
    str_arg(args, name)?.map(|s| id_value(name, s)).transpose()
}

fn actor_arg(args: &Args, name: &str) -> Result<Option<Actor>, String> {
    match str_arg(args, name)? {
        None => Ok(None),
        Some(s) => Actor::parse(s)
            .map(Some)
            .ok_or_else(|| bad(format!("{name} must be one of user, model"))),
    }
}

/// `answer`, `source` and `rationale` go together (REQ-31). Absent entirely gives `None`
/// unless `must_exist`.
fn answer_group(args: &Args, must_exist: bool) -> Result<Option<Answer>, String> {
    let any = ["answer", "source", "rationale"]
        .iter()
        .any(|k| args.contains_key(*k));
    if !any && !must_exist {
        return Ok(None);
    }
    let answer = str_arg(args, "answer")?;
    let source = actor_arg(args, "source")?;
    let rationale = str_arg(args, "rationale")?;
    let (Some(a), Some(s), Some(r)) = (answer, source, rationale) else {
        let missing: Vec<&str> = [
            ("answer", answer.is_none()),
            ("source", source.is_none()),
            ("rationale", rationale.is_none()),
        ]
        .into_iter()
        .filter_map(|(n, m)| m.then_some(n))
        .collect();
        return Err(bad(format!(
            "answer, source and rationale are required together; missing {}",
            missing.join(", ")
        )));
    };
    Answer::new(a, s, r)
        .map(Some)
        .map_err(|(param, why)| bad(format!("{param} {why}")))
}

fn item_json(i: &Item) -> Value {
    let (answer, source, rationale) = match &i.status {
        Status::Resolved(a) | Status::Stale(a) => (
            json!(a.text()),
            json!(a.source().as_str()),
            json!(a.rationale()),
        ),
        _ => (Value::Null, Value::Null, Value::Null),
    };
    let (parked_by, park_reason) = match &i.status {
        Status::Parked { by, reason } => (json!(by.as_str()), json!(reason)),
        _ => (Value::Null, Value::Null),
    };
    json!({
        "id": i.id,
        "kind": i.kind.as_str(),
        "text": i.text,
        "parent": i.parent,
        "depends_on": i.depends_on,
        "status": i.status.name(),
        "answer": answer,
        "source": source,
        "rationale": rationale,
        "parked_by": parked_by,
        "park_reason": park_reason,
    })
}

/// Days since 1970-01-01 to (year, month, day), proleptic Gregorian.
fn civil_from_days(days: i64) -> (i64, i64, i64) {
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

/// `iv-YYYYMMDDTHHMMSSZ-<8 hex>`; the suffix comes from the std randomly seeded hasher.
fn new_interview_id() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = i64::try_from(now.as_secs()).unwrap_or(0);
    let (y, m, d) = civil_from_days(secs.div_euclid(86_400));
    let t = secs.rem_euclid(86_400);
    let mut h = RandomState::new().build_hasher();
    h.write_u128(now.as_nanos());
    h.write_u32(std::process::id());
    format!(
        "iv-{y:04}{m:02}{d:02}T{:02}{:02}{:02}Z-{:08x}",
        t / 3600,
        t % 3600 / 60,
        t % 60,
        h.finish() & 0xffff_ffff
    )
}

#[derive(Default)]
pub struct App {
    register: Register,
    interview_id: Option<String>,
    events: u64,
    prompt_seen: bool,
    hook_note_prompt: u64,
    version_mismatch: Option<(u64, u64)>,
}

impl App {
    pub fn new() -> Self {
        Self::default()
    }

    /// Runs a tool. `None` means the tool name is unknown (a protocol error).
    pub fn call(&mut self, name: &str, args: &Value, model_facing: bool) -> Option<ToolResult> {
        if !IMPLEMENTED.contains(&name) {
            return None;
        }
        Some(match self.dispatch(name, args, model_facing) {
            Ok(v) => ToolResult {
                text: v.to_string(),
                is_error: false,
            },
            Err(text) => ToolResult {
                text,
                is_error: true,
            },
        })
    }

    fn dispatch(&mut self, name: &str, args: &Value, model_facing: bool) -> Result<Value, String> {
        if name == "note_prompt" && model_facing {
            return Err(
                "note_prompt is hook-facing: a call carrying _meta claudecode/toolUseId is rejected"
                    .to_string(),
            );
        }
        let Some(args) = args.as_object() else {
            return Err(bad("arguments must be an object"));
        };
        match name {
            "begin_interview" => self.begin_interview(args),
            "log_item" => self.log_item(args),
            "resolve" => self.resolve(args),
            "park" => self.park(args),
            "reopen" => self.reopen(args),
            "status" => self.status(args),
            "note_prompt" => self.note_prompt(args),
            _ => Err(format!("tool not implemented: {name}")),
        }
    }

    /// Starts the interview if none is active; returns its id.
    fn ensure_started(&mut self) -> String {
        if let Some(id) = &self.interview_id {
            return id.clone();
        }
        let id = new_interview_id();
        self.interview_id = Some(id.clone());
        self.events += 1;
        // The prompt before the start counts once; earlier inactive ones are not counted.
        self.hook_note_prompt = u64::from(self.prompt_seen);
        id
    }

    fn begin_interview(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["tool_schema", "skill", "mode"])?;
        let ts = required(
            "tool_schema",
            args.get("tool_schema")
                .map(|v| v.as_u64().filter(|n| *n >= 1)),
        )?
        .ok_or_else(|| bad("tool_schema must be an integer >= 1"))?;
        text_arg(args, "skill")?;
        if required("mode", str_arg(args, "mode")?)? != "register" {
            return Err(bad("mode must be register"));
        }
        if ts != TOOL_SCHEMA {
            self.version_mismatch = Some((ts, TOOL_SCHEMA));
            return Err(format!(
                "tool_schema mismatch: plugin {ts}, server {TOOL_SCHEMA}"
            ));
        }
        self.version_mismatch = None;
        let existing = self.interview_id.is_some();
        let id = self.ensure_started();
        Ok(json!({"interview_id": id, "state": "active", "existing": existing}))
    }

    fn log_item(&mut self, args: &Args) -> Result<Value, String> {
        check_known(
            args,
            &[
                "id",
                "kind",
                "text",
                "parent",
                "depends_on",
                "answer",
                "source",
                "rationale",
            ],
        )?;
        let id = opt_id_arg(args, "id")?;
        let kind = Kind::parse(required("kind", str_arg(args, "kind")?)?)
            .ok_or_else(|| bad("kind must be one of question, subquestion, decision"))?;
        let text = text_arg(args, "text")?;
        let parent = opt_id_arg(args, "parent")?;
        let depends_on = match args.get("depends_on") {
            None => Vec::new(),
            Some(Value::Array(a)) => a
                .iter()
                .map(|v| {
                    v.as_str()
                        .ok_or_else(|| bad("depends_on must be an array of ids"))
                        .and_then(|s| id_value("depends_on", s))
                })
                .collect::<Result<_, _>>()?,
            Some(_) => return Err(bad("depends_on must be an array of ids")),
        };
        let answer = answer_group(args, false)?;
        let new = NewItem {
            id,
            kind,
            text,
            parent,
            depends_on,
            answer,
        };
        let (id, status) = self.register.log(new).map_err(|e| e.to_string())?;
        // A rule error above leaves the state untouched; success starts an interview.
        self.ensure_started();
        self.events += 1;
        Ok(json!({"id": id, "status": status}))
    }

    fn resolve(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["id", "answer", "source", "rationale"])?;
        let id = id_arg(args, "id")?;
        let answer = answer_group(args, true)?
            .ok_or_else(|| bad("answer, source and rationale are required"))?;
        self.register
            .resolve(&id, answer)
            .map_err(|e| e.to_string())?;
        self.events += 1;
        Ok(json!({"id": id, "status": "Resolved"}))
    }

    fn park(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["id", "reason", "parked_by"])?;
        let id = id_arg(args, "id")?;
        let reason = text_arg(args, "reason")?;
        let by = required("parked_by", actor_arg(args, "parked_by")?)?;
        self.register
            .park(&id, by, &reason)
            .map_err(|e| e.to_string())?;
        self.events += 1;
        Ok(json!({"id": id, "status": "Parked"}))
    }

    fn reopen(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["id", "reason"])?;
        let id = id_arg(args, "id")?;
        text_arg(args, "reason")?; // required now; kept once the journal exists
        let stale = self.register.reopen(&id).map_err(|e| e.to_string())?;
        self.events += 1;
        Ok(json!({"id": id, "status": "Open", "stale": stale}))
    }

    fn status(&self, args: &Args) -> Result<Value, String> {
        check_known(args, &[])?;
        let c = self.register.counts();
        Ok(json!({
            "server_version": SERVER_VERSION,
            "tool_schema": TOOL_SCHEMA,
            "journal_schema": JOURNAL_SCHEMA,
            "state": if self.interview_id.is_some() { "active" } else { "none" },
            "interview_id": self.interview_id,
            "items": {"open": c.open, "resolved": c.resolved, "parked": c.parked, "stale": c.stale},
            "tree": self.register.ordered().into_iter().map(item_json).collect::<Vec<_>>(),
            "hook_health": {
                "note_prompt": self.hook_note_prompt,
                "skill_started": 0,
                "check_stop": 0,
                "prompt_seen": self.prompt_seen,
            },
            "last_event": self.events,
            "resumable": [],
            "version_mismatch": self.version_mismatch.map(|(p, s)| {
                json!({"plugin_tool_schema": p, "server_tool_schema": s})
            }),
            "decision_log": null,
            "write_error": null,
        }))
    }

    /// Hook-facing. In I1 this only keeps the prompt counters; nothing consumes them yet.
    fn note_prompt(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["session_id", "prompt"])?;
        text_arg(args, "session_id")?;
        let prompt = required("prompt", str_arg(args, "prompt")?)?;
        if !prompt.starts_with(AGENT_MESSAGE_PREFIX) {
            self.prompt_seen = true;
            if self.interview_id.is_some() {
                self.hook_note_prompt += 1;
            }
        }
        Ok(json!({}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn call(app: &mut App, name: &str, args: Value) -> Result<Value, String> {
        let r = app
            .call(name, &args, !matches!(name, "note_prompt"))
            .unwrap();
        if r.is_error {
            Err(r.text)
        } else {
            Ok(serde_json::from_str(&r.text).unwrap())
        }
    }

    fn started() -> App {
        let mut app = App::new();
        let args = json!({"tool_schema": 4, "skill": "grill-me", "mode": "register"});
        call(&mut app, "begin_interview", args).unwrap();
        app
    }

    fn q(app: &mut App, id: &str) {
        let args = json!({"id": id, "kind": "question", "text": format!("t {id}")});
        call(app, "log_item", args).unwrap();
    }

    #[test]
    fn begin_is_idempotent_and_checks_schema() {
        let mut app = App::new();
        let args = json!({"tool_schema": 4, "skill": "x", "mode": "register"});
        let a = call(&mut app, "begin_interview", args.clone()).unwrap();
        let b = call(&mut app, "begin_interview", args).unwrap();
        assert_eq!(a["existing"], false);
        assert_eq!(b["existing"], true);
        assert_eq!(a["interview_id"], b["interview_id"]);
        let id = a["interview_id"].as_str().unwrap();
        assert!(id.starts_with("iv-") && id.len() == "iv-20261116T093015Z-a3f9c2d1".len());
        let old = json!({"tool_schema": 3, "skill": "x", "mode": "register"});
        let e = call(&mut app, "begin_interview", old).unwrap_err();
        assert!(e.contains('3') && e.contains('4'));
        let s = call(&mut app, "status", json!({})).unwrap();
        assert_eq!(s["version_mismatch"]["plugin_tool_schema"], 3);
    }

    #[test]
    fn log_item_starts_implicitly_and_errors_do_not() {
        let mut app = App::new();
        let orphan = json!({"kind": "subquestion", "text": "t"});
        let e = call(&mut app, "log_item", orphan).unwrap_err();
        assert!(e.contains("subquestion needs a parent"));
        assert_eq!(
            call(&mut app, "status", json!({})).unwrap()["state"],
            "none"
        );
        q(&mut app, "q1");
        let s = call(&mut app, "status", json!({})).unwrap();
        assert_eq!(s["state"], "active");
        assert_eq!(s["last_event"], 2); // start, item
    }

    #[test]
    fn log_item_argument_validation_names_the_parameter() {
        let mut app = started();
        let cases = [
            (
                json!({"kind": "question", "text": "t", "answer": "a"}),
                "source",
            ),
            (json!({"kind": "question", "text": "  "}), "text"),
            (json!({"kind": "topic", "text": "t"}), "kind"),
            (
                json!({"kind": "question", "text": "t", "id": "bad id"}),
                "id",
            ),
            (
                json!({"kind": "question", "text": "t", "depends_on": "q1"}),
                "depends_on",
            ),
            (
                json!({"kind": "question", "text": "t", "extra": 1}),
                "extra",
            ),
            (
                json!({"kind": "question", "text": "t", "answer": "a", "source": "model", "rationale": "x\ny"}),
                "rationale",
            ),
        ];
        for (args, param) in cases {
            let e = call(&mut app, "log_item", args).unwrap_err();
            assert!(
                e.starts_with("invalid arguments:") && e.contains(param),
                "{e}"
            );
        }
        assert_eq!(
            call(&mut app, "status", json!({})).unwrap()["last_event"],
            1
        );
    }

    #[test]
    fn volunteered_answer_is_resolved() {
        let mut app = started();
        let args = json!({"kind": "question", "text": "t", "answer": "a", "source": "user", "rationale": "r"});
        assert_eq!(
            call(&mut app, "log_item", args).unwrap()["status"],
            "Resolved"
        );
    }

    #[test]
    fn note_prompt_rules() {
        let mut app = App::new();
        let p = |s: &str| json!({"session_id": "s", "prompt": s});
        call(&mut app, "note_prompt", p("hello")).unwrap();
        call(&mut app, "note_prompt", p("<agent-message from=\"a\">\nx")).unwrap();
        let s = call(&mut app, "status", json!({})).unwrap();
        assert_eq!(s["hook_health"]["prompt_seen"], true);
        assert_eq!(s["hook_health"]["note_prompt"], 0);
        call(
            &mut app,
            "begin_interview",
            json!({"tool_schema": 4, "skill": "x", "mode": "register"}),
        )
        .unwrap();
        call(&mut app, "note_prompt", p("again")).unwrap();
        call(&mut app, "note_prompt", p(" <agent-message from=\"a\">")).unwrap(); // not at char 0
        call(&mut app, "note_prompt", p("<agent-message x")).unwrap(); // no space after the word
        let s = call(&mut app, "status", json!({})).unwrap();
        assert_eq!(s["hook_health"]["note_prompt"], 3);
        let model = app.call("note_prompt", &p("x"), true).unwrap();
        assert!(model.is_error);
        assert!(call(&mut app, "note_prompt", json!({"prompt": "x"})).is_err());
    }

    fn resolve_args(id: &str) -> Value {
        json!({"id": id, "answer": "an", "source": "model", "rationale": "why"})
    }

    #[test]
    fn resolve_park_reopen_flow() {
        let mut app = started();
        q(&mut app, "a");
        let dep = json!({"id": "b", "kind": "question", "text": "t", "depends_on": ["a"]});
        call(&mut app, "log_item", dep).unwrap();
        call(&mut app, "resolve", resolve_args("a")).unwrap();
        call(&mut app, "resolve", resolve_args("b")).unwrap();
        let r = call(&mut app, "reopen", json!({"id": "a", "reason": "new fact"})).unwrap();
        assert_eq!(r, json!({"id": "a", "status": "Open", "stale": ["b"]}));
        let p = json!({"id": "b", "reason": "later", "parked_by": "user"});
        assert_eq!(call(&mut app, "park", p).unwrap()["status"], "Parked");
        let s = call(&mut app, "status", json!({})).unwrap();
        assert_eq!(
            s["items"],
            json!({"open": 1, "resolved": 0, "parked": 1, "stale": 0})
        );
        assert_eq!(s["tree"][1]["parked_by"], "user");
        assert_eq!(s["tree"][1]["answer"], Value::Null);
    }

    #[test]
    fn rejections_name_id_and_status_and_change_nothing() {
        let mut app = started();
        q(&mut app, "a");
        call(&mut app, "resolve", resolve_args("a")).unwrap();
        let before = call(&mut app, "status", json!({})).unwrap();
        let e = call(&mut app, "resolve", resolve_args("a")).unwrap_err();
        assert!(e.contains('a') && e.contains("Resolved"));
        let p = json!({"id": "a", "reason": "r", "parked_by": "model"});
        assert!(call(&mut app, "park", p).unwrap_err().contains("Resolved"));
        let e = call(&mut app, "resolve", resolve_args("zz")).unwrap_err();
        assert!(e.contains("no such item: zz"));
        let bad_source = json!({"id": "a", "answer": "x", "source": "robot", "rationale": "r"});
        assert!(
            call(&mut app, "resolve", bad_source)
                .unwrap_err()
                .contains("source")
        );
        assert_eq!(call(&mut app, "status", json!({})).unwrap(), before);
    }

    #[test]
    fn tools_without_an_interview_find_no_item() {
        let mut app = App::new();
        let e = call(&mut app, "resolve", resolve_args("q1")).unwrap_err();
        assert!(e.contains("no such item: q1"));
        let e = call(&mut app, "reopen", json!({"id": "q1", "reason": "r"})).unwrap_err();
        assert!(e.contains("no such item: q1"));
        assert_eq!(
            call(&mut app, "status", json!({})).unwrap()["state"],
            "none"
        );
    }
}
