//! Tool handlers. The tool schemas themselves come from `contract/tools.json`.
//!
//! The register tools, the hook-facing tools and the minimal journal writer (I2).

use std::collections::hash_map::RandomState;
use std::fmt::Display;
use std::hash::{BuildHasher, Hasher};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value, json};

use crate::classifier::{Class, ClassItem, classify};
use crate::declog::{DecisionLog, LogView};
use crate::journal::{Journal, civil_from_days};
use crate::register::{
    Actor, Answer, Item, Kind, NewItem, Register, Status, is_nonblank, is_valid_id,
};

/// Tools this build implements, in the order they are listed.
pub const IMPLEMENTED: [&str; 12] = [
    "begin_interview",
    "log_item",
    "resolve",
    "park",
    "reopen",
    "status",
    "resume",
    "discard",
    "end_interview",
    "note_prompt",
    "skill_started",
    "check_stop",
];

/// Every tool in `contract/tools.json`, in contract order; the unbuilt ones are listed but
/// answer with a not-implemented error result.
pub const CONTRACT_TOOLS: [&str; 12] = [
    "begin_interview",
    "log_item",
    "resolve",
    "park",
    "reopen",
    "status",
    "resume",
    "discard",
    "end_interview",
    "note_prompt",
    "skill_started",
    "check_stop",
];

/// Hook-facing tools reject model-sourced calls (REQ-40); `check_stop` joins in phase B.
const HOOK_FACING: [&str; 3] = ["note_prompt", "skill_started", "check_stop"];

/// The configured skill-name list (REQ-24), matched exactly as received.
const SKILL_LIST: [&str; 1] = ["grill-me"];

/// Budget when the classifier returned `unknown` (REQ-32), and the volunteered-answer cap.
const FLAT_BUDGET: usize = 4;
const VOLUNTEERED_CAP: usize = 4;

/// A branch is a parent with at least this many children (REQ-35).
const BRANCH_MIN: usize = 2;

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

/// The three block counters of REQ-42, after the latest decision.
#[derive(Default, Clone, Copy, PartialEq, Eq, Debug)]
struct Blocks {
    prompt: u64,
    finish: u64,
    unlogged: u64,
}

/// Caps per interview (REQ-42): blocks per user prompt, finish blocks, unlogged-question blocks.
const CAP_PROMPT: u64 = 2;
const CAP_FINISH: u64 = 8;
const CAP_UNLOGGED: u64 = 12;
/// The Stop reason lists this many open items, each text cut to this many characters (REQ-45).
const LIST_ITEMS: usize = 10;
const LIST_TEXT_CHARS: usize = 120;

/// What the finish gate decided for one Stop check.
#[derive(PartialEq, Eq, Debug)]
struct Verdict {
    reason: Option<String>,
    classification: &'static str,
    presented: Option<Vec<String>>,
    completes: bool,
}

/// How an interview came to start (journal `start.route`).
enum Route {
    Hook(String),
    Begin(String),
    Implicit,
}

#[derive(Default)]
pub struct App {
    register: Register,
    interview_id: Option<String>,
    journal: Option<Journal>,
    last_seen_session: Option<String>,
    /// Per-prompt budget of `source: user` answers and parks (REQ-32).
    allowance: usize,
    spent: usize,
    /// Volunteered `log_item` answers accepted for the current prompt.
    volunteered: usize,
    /// A user prompt arrived since the previous Stop-hook call.
    prompt_since_stop: bool,
    /// Distinct items the classifier presented since the previous user prompt.
    presented: Vec<String>,
    /// The classifier returned `unknown` since the previous user prompt.
    unknown_seen: bool,
    hook_check_stop: u64,
    /// The interview holds a clean finish; the gate allows every turn (REQ-23).
    complete: bool,
    /// Block counters of the finish gate (REQ-42): per prompt, finish, unlogged question.
    blocks: Blocks,
    prompt_seen: bool,
    hook_note_prompt: u64,
    hook_skill_started: u64,
    /// A hook start not yet engaged by a model-facing call (REQ-27).
    pending: bool,
    version_mismatch: Option<(u64, u64)>,
    /// The interview began with an implicit start (log header `implicit_start`).
    implicit: bool,
    declog: DecisionLog,
    /// The session the interview is bound to (REQ-26); None until a hook call names one.
    bound_session: Option<String>,
}

impl App {
    pub fn new() -> Self {
        Self::default()
    }

    /// Runs a tool. `None` means the tool name is unknown (a protocol error).
    pub fn call(&mut self, name: &str, args: &Value, model_facing: bool) -> Option<ToolResult> {
        if !IMPLEMENTED.contains(&name) {
            return CONTRACT_TOOLS.contains(&name).then(|| ToolResult {
                text: format!("not implemented in this build: {name}"),
                is_error: true,
            });
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
        if model_facing && HOOK_FACING.contains(&name) {
            return Err(format!(
                "{name} is hook-facing: a call carrying _meta claudecode/toolUseId is rejected"
            ));
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
            "end_interview" => self.end_interview(args),
            "resume" | "discard" => self.resume_or_discard(args),
            "note_prompt" => self.note_prompt(args),
            "skill_started" => self.skill_started(args),
            "check_stop" => self.check_stop(args),
            _ => Err(format!("tool not implemented: {name}")),
        }
    }

    /// Writes any queued journal events (end of input; other flush points are I4).
    pub fn flush_journal(&mut self) {
        if let Some(j) = &mut self.journal {
            j.flush();
        }
    }

    /// Queues a journal event; nothing is written while no interview exists.
    fn emit(&mut self, kind: &str, fields: Value) {
        if let Some(j) = &mut self.journal {
            j.event(kind, fields);
        }
    }

    /// Starts the interview if none is active; returns its id. A model-facing call
    /// (`via`) engages a pending interview and writes the `engage` event.
    fn ensure_started(&mut self, via: &str, route: Route) -> String {
        if let Some(id) = self.interview_id.clone() {
            if self.pending {
                self.pending = false;
                self.emit("engage", json!({"via": via}));
            }
            return id;
        }
        let id = new_interview_id();
        self.interview_id = Some(id.clone());
        self.journal = Some(Journal::new(&id));
        // The prompt before the start counts once; earlier inactive ones are not counted.
        let prompt_count = u64::from(self.prompt_seen);
        self.hook_note_prompt = prompt_count;
        self.hook_skill_started = 0;
        self.hook_check_stop = 0;
        self.complete = false;
        self.implicit = matches!(route, Route::Implicit);
        self.declog = DecisionLog::default();
        self.blocks = Blocks::default();
        if !self.prompt_seen {
            self.allowance = 0;
            self.spent = 0;
            self.volunteered = 0;
        }
        self.prompt_seen = false;
        let (route, skill) = match route {
            Route::Hook(s) => ("hook", Some(s)),
            Route::Begin(s) => ("begin_interview", Some(s)),
            Route::Implicit => ("implicit", None),
        };
        let session = self.last_seen_session.clone().or_else(|| {
            std::env::var("CLAUDE_CODE_SESSION_ID")
                .ok()
                .filter(|v| !v.is_empty())
        });
        self.bound_session = session.clone();
        self.emit(
            "start",
            json!({
                "interview_id": id,
                "route": route,
                "implicit": skill.is_none(),
                "skill": skill,
                "session_id": session,
                "prompt_count": prompt_count,
            }),
        );
        // The file is created with its first event (runtime.md); later events are write-behind.
        if let Some(j) = &mut self.journal {
            j.flush();
        }
        id
    }

    /// Writes the `item` event for a freshly logged item, and `resolve` if it came
    /// with an answer.
    fn journal_logged(&mut self, id: &str) {
        let Some(item) = self.register.get(id).cloned() else {
            return;
        };
        self.emit(
            "item",
            json!({
                "id": item.id,
                "item_kind": item.kind.as_str(),
                "text": item.text,
                "parent": item.parent,
                "depends_on": item.depends_on,
            }),
        );
        if let Status::Resolved(a) = &item.status {
            self.journal_resolved(id, a);
        }
    }

    fn journal_resolved(&mut self, id: &str, a: &Answer) {
        self.emit(
            "resolve",
            json!({
                "id": id,
                "answer": a.text(),
                "source": a.source().as_str(),
                "rationale": a.rationale(),
            }),
        );
    }

    fn begin_interview(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["tool_schema", "skill", "mode"])?;
        let ts = required(
            "tool_schema",
            args.get("tool_schema")
                .map(|v| v.as_u64().filter(|n| *n >= 1)),
        )?
        .ok_or_else(|| bad("tool_schema must be an integer >= 1"))?;
        let skill = text_arg(args, "skill")?;
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
        let id = self.ensure_started("begin_interview", Route::Begin(skill));
        // A start while complete is a no-op that stays complete (tools.json state_matrix).
        let state = if self.complete { "complete" } else { "active" };
        Ok(json!({"interview_id": id, "state": state, "existing": existing}))
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
        // Rules run in the contract's order (lookups, kind rules, user-prompt rules) on a
        // trial copy, so a rejection changes nothing.
        let source = new.answer.as_ref().map(Answer::source);
        let named = new.id.clone().unwrap_or_else(|| "(new item)".to_string());
        let is_decision = new.kind == Kind::Decision;
        let mut trial = self.register.clone();
        let (id, status) = trial.log(new).map_err(|e| e.to_string())?;
        if is_decision && source == Some(Actor::Model) {
            return Err(format!(
                "decision {named}: a decision can be answered only with source user"
            ));
        }
        if source == Some(Actor::User) {
            self.user_gate(true)?;
            self.volunteered += 1;
        }
        self.register = trial;
        self.complete = false;
        // A rule error above leaves the state untouched; success starts an interview.
        self.ensure_started("log_item", Route::Implicit);
        self.journal_logged(&id);
        Ok(json!({"id": id, "status": status}))
    }

    fn resolve(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["id", "answer", "source", "rationale"])?;
        let id = id_arg(args, "id")?;
        let answer = answer_group(args, true)?
            .ok_or_else(|| bad("answer, source and rationale are required"))?;
        self.register
            .check_resolve(&id)
            .map_err(|e| e.to_string())?;
        if answer.source() == Actor::Model {
            let item = self.register.get(&id).expect("checked by check_resolve");
            if item.kind == Kind::Decision {
                return Err(format!(
                    "decision {id}: a decision can be resolved only with source user"
                ));
            }
            let children = self.register.child_count(&id);
            if (1..BRANCH_MIN).contains(&children) {
                return Err(format!(
                    "{id} is a branch with {children} sub-question(s), fewer than {BRANCH_MIN}: only the user can resolve it"
                ));
            }
        } else {
            self.user_gate(false)?;
            self.spent += 1;
        }
        self.register
            .resolve(&id, answer.clone())
            .map_err(|e| e.to_string())?;
        self.complete = false;
        self.journal_resolved(&id, &answer);
        Ok(json!({"id": id, "status": "Resolved"}))
    }

    fn park(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["id", "reason", "parked_by"])?;
        let id = id_arg(args, "id")?;
        let reason = text_arg(args, "reason")?;
        let by = required("parked_by", actor_arg(args, "parked_by")?)?;
        self.register.check_park(&id).map_err(|e| e.to_string())?;
        if by == Actor::Model {
            if self
                .register
                .get(&id)
                .is_some_and(|i| i.kind == Kind::Decision)
            {
                return Err(format!(
                    "decision {id}: a decision can be parked only with parked_by user"
                ));
            }
        } else {
            self.user_gate(false)?;
            self.spent += 1;
        }
        self.register
            .park(&id, by, &reason)
            .map_err(|e| e.to_string())?;
        self.complete = false;
        self.emit(
            "park",
            json!({"id": id, "reason": reason, "parked_by": by.as_str()}),
        );
        Ok(json!({"id": id, "status": "Parked"}))
    }

    fn reopen(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["id", "reason"])?;
        let id = id_arg(args, "id")?;
        let reason = text_arg(args, "reason")?;
        let stale = self.register.reopen(&id).map_err(|e| e.to_string())?;
        self.complete = false;
        self.emit(
            "reopen",
            json!({"id": id, "reason": reason, "stale": stale}),
        );
        Ok(json!({"id": id, "status": "Open", "stale": stale}))
    }

    /// Writes the decision log now (REQ-23): the queue is flushed first so the position
    /// is durable. Returns the log path; a write failure only sets `write_error`.
    fn write_log(&mut self, state: &str, mode: &str, end_kind: &str) -> Option<String> {
        let id = self.interview_id.clone()?;
        self.flush_journal();
        let position = self.journal.as_ref().map_or(0, Journal::seq);
        let note = match (end_kind, mode) {
            ("stood_down", "plain") => "Stood down by the model (mode plain): nothing was logged.",
            ("stood_down", _) => {
                "The skill did not engage the register: the interview was stood down."
            }
            ("ended_incomplete_by_model", _) => "Ended incomplete by the model with items open.",
            ("abandoned", _) => "Abandoned: the session changed with items open (incomplete).",
            ("interrupted", _) => "Interrupted: the server exited with the interview unfinished.",
            _ => "",
        };
        let view = LogView {
            interview_id: &id,
            position,
            state,
            mode,
            end_kind,
            note,
            implicit: self.implicit,
            items: self.register.ordered(),
        };
        Some(self.declog.write(&view).to_string_lossy().into_owned())
    }

    /// Ends the interview (REQ-23): the `end` event, the flush, the log, the archive
    /// rename, then the state back to none. Returns the interview id and the log path.
    fn end_current(
        &mut self,
        end_kind: &str,
        journal_mode: &str,
        reason: Option<&str>,
    ) -> (String, String) {
        let id = self.interview_id.clone().unwrap_or_default();
        self.emit(
            "end",
            json!({"end_kind": end_kind, "mode": journal_mode, "reason": reason}),
        );
        let log_mode = journal_mode.replace('_', " ");
        let log = self.write_log("ended", &log_mode, end_kind);
        if let Some(j) = &mut self.journal {
            j.archive();
        }
        self.register = Register::default();
        self.interview_id = None;
        self.journal = None;
        self.complete = false;
        self.pending = false;
        self.implicit = false;
        self.declog = DecisionLog::default();
        (id, log.unwrap_or_default())
    }

    /// `resume` and `discard`: only the state check (state_matrix) in I3. There is no
    /// unfinished-journal scan yet, so from none or pending both report that nothing
    /// can be resumed; the scan, claim and replay are I4.
    fn resume_or_discard(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["id"])?;
        opt_id_arg(args, "id")?;
        if self.interview_id.is_some() && !self.pending {
            let state = if self.complete { "complete" } else { "active" };
            return Err(format!("wrong state: {state}"));
        }
        Err("no unfinished journal".to_string())
    }

    /// REQ-26: a hook call from another session ends the interview bound to the old one
    /// before the call is handled, as if no interview existed. A pending one is stood
    /// down, a complete one ends after completion, an active one ends `normal` with
    /// nothing open and `abandoned` otherwise.
    fn session_guard(&mut self, session: &str) {
        if self.interview_id.is_none() {
            return;
        }
        match self.bound_session.as_deref() {
            None => self.bound_session = Some(session.to_string()),
            Some(bound) if bound == session => {}
            Some(_) => {
                if self.pending {
                    self.end_current("stood_down", "not_engaged", Some("session change"));
                } else if self.complete {
                    self.end_current("ended_after_completion", "register", None);
                } else if self.unresolved().is_empty() {
                    self.end_current("normal", "register", None);
                } else {
                    self.end_current("abandoned", "register", Some("session change"));
                }
            }
        }
    }

    fn end_interview(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["mode", "abandon", "reason"])?;
        let plain = match str_arg(args, "mode")? {
            Some("plain") => true,
            Some(_) => return Err(bad("mode must be plain")),
            None => false,
        };
        if plain && args.len() > 1 {
            return Err(bad("mode plain takes no other argument"));
        }
        let abandon = match args.get("abandon") {
            None => false,
            Some(Value::Bool(true)) => true,
            Some(_) => return Err(bad("abandon must be true")),
        };
        let reason = if args.contains_key("reason") {
            Some(text_arg(args, "reason")?)
        } else {
            None
        };
        if abandon != reason.is_some() {
            return Err(bad("abandon and reason must be given together"));
        }
        if self.interview_id.is_none() {
            return Err("no interview to end".to_string());
        }
        if self.pending {
            if !plain {
                return Err("pending interview: use mode plain".to_string());
            }
            return Ok(self.ended("stood_down", "plain", None));
        }
        if plain {
            return Err("mode plain applies only to a pending interview".to_string());
        }
        let open = self.unresolved();
        if self.complete {
            if abandon {
                return Err("nothing is open: abandon does not apply".to_string());
            }
            return Ok(self.ended("ended_after_completion", "register", None));
        }
        match (open.is_empty(), abandon) {
            (true, true) => Err("nothing is open: abandon does not apply".to_string()),
            (true, false) => Ok(self.ended("normal", "register", None)),
            (false, false) => {
                let ids: Vec<&str> = open.iter().filter_map(|l| l.split(' ').next()).collect();
                Err(format!(
                    "items are open: {}; resolve or park them, or end with abandon true and a reason",
                    ids.join(", ")
                ))
            }
            (false, true) => {
                Ok(self.ended("ended_incomplete_by_model", "register", reason.as_deref()))
            }
        }
    }

    fn ended(&mut self, end_kind: &str, mode: &str, reason: Option<&str>) -> Value {
        let (id, log) = self.end_current(end_kind, mode, reason);
        json!({"interview_id": id, "state": "ended", "end_kind": end_kind, "decision_log": log})
    }

    fn status(&self, args: &Args) -> Result<Value, String> {
        check_known(args, &[])?;
        let c = self.register.counts();
        Ok(json!({
            "server_version": SERVER_VERSION,
            "tool_schema": TOOL_SCHEMA,
            "journal_schema": JOURNAL_SCHEMA,
            "state": match (&self.interview_id, self.pending) {
                (None, _) => "none",
                (Some(_), true) => "pending",
                (Some(_), false) if self.complete => "complete",
                (Some(_), false) => "active",
            },
            "interview_id": self.interview_id,
            "items": {"open": c.open, "resolved": c.resolved, "parked": c.parked, "stale": c.stale},
            "tree": self.register.ordered().into_iter().map(item_json).collect::<Vec<_>>(),
            "hook_health": {
                "note_prompt": self.hook_note_prompt,
                "skill_started": self.hook_skill_started,
                "check_stop": self.hook_check_stop,
                "prompt_seen": self.prompt_seen,
            },
            "last_event": self.journal.as_ref().map_or(0, Journal::seq),
            "resumable": [],
            "version_mismatch": self.version_mismatch.map(|(p, s)| {
                json!({"plugin_tool_schema": p, "server_tool_schema": s})
            }),
            "decision_log": self.declog.path().map(|p| p.to_string_lossy().into_owned()),
            "write_error": self
                .journal
                .as_ref()
                .and_then(Journal::write_error)
                .or_else(|| self.declog.error()),
        }))
    }

    /// Hook-facing. In I1 this only keeps the prompt counters; nothing consumes them yet.
    fn note_prompt(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["session_id", "prompt"])?;
        let session = text_arg(args, "session_id")?;
        let prompt = required("prompt", str_arg(args, "prompt")?)?;
        if !prompt.starts_with(AGENT_MESSAGE_PREFIX) {
            self.session_guard(&session);
            self.last_seen_session = Some(session.clone());
            self.prompt_since_stop = true;
            self.allowance = if self.unknown_seen {
                FLAT_BUDGET
            } else {
                self.presented.len().max(1)
            };
            self.spent = 0;
            self.volunteered = 0;
            self.blocks.prompt = 0;
            self.presented.clear();
            self.unknown_seen = false;
            if self.interview_id.is_some() {
                self.hook_note_prompt += 1;
                self.emit(
                    "hook",
                    json!({"tool": "note_prompt", "session_id": session}),
                );
                let count = self.hook_note_prompt;
                self.emit("prompt", json!({"session_id": session, "count": count}));
            } else {
                self.prompt_seen = true;
            }
        }
        Ok(json!({}))
    }

    /// Hook-facing. A listed skill name starts a pending interview (REQ-20); anything
    /// else, and any call while an interview exists, only bookkeeps. Never blocks.
    fn skill_started(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["tool_schema", "session_id", "skill"])?;
        let ts = required(
            "tool_schema",
            args.get("tool_schema")
                .map(|v| v.as_u64().filter(|n| *n >= 1)),
        )?
        .ok_or_else(|| bad("tool_schema must be an integer >= 1"))?;
        let session = text_arg(args, "session_id")?;
        let skill = text_arg(args, "skill")?;
        self.session_guard(&session);
        self.last_seen_session = Some(session.clone());
        // A listed name starts the interview even on a schema mismatch, which is then
        // only recorded for `status` (REQ-77). The starting call writes `start`, not `hook`.
        if self.interview_id.is_some() {
            self.emit(
                "hook",
                json!({"tool": "skill_started", "session_id": session}),
            );
        } else if SKILL_LIST.contains(&skill.as_str()) {
            self.ensure_started("skill_started", Route::Hook(skill));
            self.pending = true;
        }
        self.version_mismatch = (ts != TOOL_SCHEMA).then_some((ts, TOOL_SCHEMA));
        if self.interview_id.is_some() {
            self.hook_skill_started += 1;
            if ts != TOOL_SCHEMA {
                self.emit(
                    "mismatch",
                    json!({"plugin_tool_schema": ts, "server_tool_schema": TOOL_SCHEMA}),
                );
            }
        }
        Ok(json!({}))
    }

    /// Hook-facing Stop check (REQ-42, REQ-43): always returns `{}` or a block decision.
    fn check_stop(&mut self, args: &Args) -> Result<Value, String> {
        check_known(args, &["session_id", "last_assistant_message"])?;
        let session = text_arg(args, "session_id")?;
        let message = required(
            "last_assistant_message",
            str_arg(args, "last_assistant_message")?,
        )?;
        self.session_guard(&session);
        self.last_seen_session = Some(session.clone());
        self.prompt_since_stop = false;
        if self.interview_id.is_none() {
            self.prompt_seen = false; // a Stop check ends the "prompt before the start" window
            return Ok(json!({}));
        }
        self.hook_check_stop += 1;
        self.emit("hook", json!({"tool": "check_stop", "session_id": session}));
        // A pending interview survives one Stop check; the second, with no model-facing
        // register call in between, stands it down (REQ-27 rule 3).
        if self.pending && self.hook_check_stop >= 2 {
            self.end_current(
                "stood_down",
                "not_engaged",
                Some("second Stop check without a register call"),
            );
            return Ok(json!({}));
        }
        let result = if self.pending || self.complete {
            json!({})
        } else {
            let items: Vec<ClassItem<'_>> = self
                .register
                .iter()
                .map(|i| ClassItem {
                    id: &i.id,
                    text: &i.text,
                    live: matches!(i.status, Status::Open | Status::Stale(_)),
                })
                .collect();
            let class = classify(message, &items);
            let verdict = self.gate(class);
            self.emit(
                "stop",
                json!({
                    "decision": if verdict.reason.is_some() { "block" } else { "allow" },
                    "classification": verdict.classification,
                    "presented": verdict.presented,
                    "blocks": {
                        "prompt": self.blocks.prompt,
                        "finish": self.blocks.finish,
                        "unlogged": self.blocks.unlogged,
                    },
                }),
            );
            if verdict.completes {
                self.complete = true;
                self.emit("complete", json!({}));
            }
            // Every allowed finish attempt writes the log (REQ-23), also one allowed
            // because the caps are spent.
            if verdict.reason.is_none() && verdict.classification == "finish" {
                let state = if self.complete { "complete" } else { "active" };
                self.write_log(state, "register", "none");
            }
            match verdict.reason {
                Some(reason) => json!({"decision": "block", "reason": reason}),
                None => json!({}),
            }
        };
        if let Some(j) = &mut self.journal {
            j.flush();
        }
        Ok(result)
    }

    /// Items that stop a finish: Open, Stale or model-Parked, and parents with children
    /// but fewer than N unless the user parked or resolved them (REQ-35). Creation order.
    fn unresolved(&self) -> Vec<String> {
        self.register
            .iter()
            .filter_map(|i| {
                let undersized = (1..BRANCH_MIN).contains(&self.register.child_count(&i.id));
                let note = match &i.status {
                    Status::Open | Status::Stale(_) => "",
                    Status::Parked { by, .. } if *by == Actor::Model => "",
                    Status::Parked { .. } => return None,
                    Status::Resolved(a) if undersized && a.source() == Actor::Model => {
                        " [branch with fewer than 2 sub-questions]"
                    }
                    Status::Resolved(_) => return None,
                };
                let text: String = i.text.chars().take(LIST_TEXT_CHARS).collect();
                Some(format!("{} {}{}", i.id, text, note))
            })
            .collect()
    }

    /// The Stop block reason: the unresolved items, the first 10 then `N more`.
    fn block_reason(&self, lead: &str) -> String {
        let open = self.unresolved();
        let mut reason = format!("{lead} {} open:", open.len());
        for line in open.iter().take(LIST_ITEMS) {
            reason.push_str("\n- ");
            reason.push_str(line);
        }
        if open.len() > LIST_ITEMS {
            reason.push_str(&format!("\n{} more", open.len() - LIST_ITEMS));
        }
        reason
    }

    /// Applies the gate rules to one classification, updating the counters (REQ-42).
    fn gate(&mut self, class: Class) -> Verdict {
        let mut v = Verdict {
            reason: None,
            classification: "question",
            presented: Some(Vec::new()),
            completes: false,
        };
        match class {
            Class::Empty => {}
            Class::Unknown => {
                self.unknown_seen = true;
                v.classification = "unknown";
                v.presented = None; // fails open
            }
            Class::Question(ids) if !ids.is_empty() => {
                for id in &ids {
                    if !self.presented.contains(id) {
                        self.presented.push(id.clone());
                    }
                }
                v.presented = Some(ids);
            }
            Class::Question(_) => {
                if self.blocks.prompt < CAP_PROMPT && self.blocks.unlogged < CAP_UNLOGGED {
                    self.blocks.prompt += 1;
                    self.blocks.unlogged += 1;
                    v.reason = Some(self.block_reason(
                        "Your question is not in the register: log it with log_item, then ask it.",
                    ));
                }
            }
            Class::Finish => {
                v.classification = "finish";
                v.presented = Some(Vec::new());
                if self.unresolved().is_empty() {
                    v.completes = true;
                } else if self.blocks.prompt < CAP_PROMPT && self.blocks.finish < CAP_FINISH {
                    self.blocks.prompt += 1;
                    self.blocks.finish += 1;
                    v.reason = Some(self.block_reason("Not finished:"));
                }
            }
        }
        v
    }

    /// REQ-44 then REQ-32: a user-sourced answer needs a prompt hook call seen at all;
    /// a volunteered one also a prompt since the previous Stop check and room under the
    /// cap of 4 per prompt; any other the per-prompt budget max(1, p). Callers count the
    /// point only once the call is certain to succeed.
    fn user_gate(&self, volunteered: bool) -> Result<(), String> {
        if !(self.prompt_seen || self.hook_note_prompt > 0) {
            return Err(
                "user-sourced answer rejected: hooks are not firing (no prompt hook call seen)"
                    .to_string(),
            );
        }
        if volunteered {
            if !self.prompt_since_stop {
                return Err(
                    "volunteered user answer rejected: no user prompt since the previous Stop check"
                        .to_string(),
                );
            }
            if self.volunteered >= VOLUNTEERED_CAP {
                return Err(format!(
                    "volunteered-answer cap reached: {VOLUNTEERED_CAP} per user prompt"
                ));
            }
        } else if self.spent >= self.allowance {
            return Err(format!(
                "user-answer budget spent: budget spent: used {}, allowed {} for this prompt; a new user prompt is needed",
                self.spent, self.allowance
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A new app that does not depend on the session id of the shell running the tests.
    fn fresh() -> App {
        // SAFETY: tests only ever clear this variable, none sets it.
        unsafe { std::env::remove_var("CLAUDE_CODE_SESSION_ID") };
        App::new()
    }

    fn call(app: &mut App, name: &str, args: Value) -> Result<Value, String> {
        let r = app.call(name, &args, !HOOK_FACING.contains(&name)).unwrap();
        if r.is_error {
            Err(r.text)
        } else {
            Ok(serde_json::from_str(&r.text).unwrap())
        }
    }

    fn started() -> App {
        let mut app = fresh();
        let args = json!({"tool_schema": 4, "skill": "grill-me", "mode": "register"});
        call(&mut app, "begin_interview", args).unwrap();
        app
    }

    fn q(app: &mut App, id: &str) {
        let args = json!({"id": id, "kind": "question", "text": format!("t {id}")});
        call(app, "log_item", args).unwrap();
    }

    #[test]
    fn flush_journal_writes_events_queued_after_the_last_flush() {
        let dir = std::env::temp_dir().join(format!("regspike-eof-{}", std::process::id()));
        // SAFETY: only this test sets CLAUDE_PLUGIN_DATA in this crate's unit tests.
        unsafe { std::env::set_var("CLAUDE_PLUGIN_DATA", &dir) };
        let mut app = started();
        q(&mut app, "Q1");
        let pending = |a: &App| a.journal.as_ref().unwrap().pending();
        assert!(pending(&app) > 0);
        app.flush_journal();
        assert_eq!(pending(&app), 0);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn begin_is_idempotent_and_checks_schema() {
        let mut app = fresh();
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
        let mut app = fresh();
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
        let p = json!({"session_id": "s", "prompt": "answer"});
        call(&mut app, "note_prompt", p).unwrap();
        let args = json!({"kind": "question", "text": "t", "answer": "a", "source": "user", "rationale": "r"});
        assert_eq!(
            call(&mut app, "log_item", args).unwrap()["status"],
            "Resolved"
        );
    }

    #[test]
    fn note_prompt_rules() {
        let mut app = fresh();
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

    fn hook_call(app: &mut App, name: &str, args: Value, model: bool) -> ToolResult {
        app.call(name, &args, model).unwrap()
    }

    #[test]
    fn hook_facing_tools_reject_model_calls_without_effect() {
        let mut app = fresh();
        let sk = json!({"tool_schema": 4, "session_id": "s", "skill": "grill-me"});
        let r = hook_call(&mut app, "skill_started", sk.clone(), true);
        assert!(r.is_error && r.text.contains("skill_started") && r.text.contains("hook-facing"));
        let r = hook_call(
            &mut app,
            "note_prompt",
            json!({"session_id": "s", "prompt": "p"}),
            true,
        );
        assert!(r.is_error && r.text.contains("hook-facing"));
        let s = call(&mut app, "status", json!({})).unwrap();
        assert_eq!(s["state"], "none");
        assert_eq!(s["last_event"], 0);
        assert_eq!(s["hook_health"]["prompt_seen"], false);
        // The same call without the marker is accepted and returns exactly {}.
        let r = hook_call(&mut app, "skill_started", sk, false);
        assert!(!r.is_error && r.text == "{}");
    }

    #[test]
    fn skill_started_starts_pending_and_begin_engages() {
        let mut app = fresh();
        let sk =
            |skill: &str, ts: u64| json!({"tool_schema": ts, "session_id": "s", "skill": skill});
        call(&mut app, "skill_started", sk("other-skill", 4)).unwrap();
        call(&mut app, "skill_started", sk("Grill-Me", 4)).unwrap();
        assert_eq!(
            call(&mut app, "status", json!({})).unwrap()["state"],
            "none"
        );
        call(&mut app, "skill_started", sk("other-skill", 3)).unwrap();
        let s = call(&mut app, "status", json!({})).unwrap();
        assert_eq!(s["state"], "none");
        assert_eq!(s["version_mismatch"]["plugin_tool_schema"], 3);
        // A listed name starts even on a mismatch; a later matching call clears the flag.
        call(&mut app, "skill_started", sk("grill-me", 3)).unwrap();
        assert_eq!(
            call(&mut app, "status", json!({})).unwrap()["state"],
            "pending"
        );
        call(&mut app, "skill_started", sk("grill-me", 4)).unwrap();
        let s = call(&mut app, "status", json!({})).unwrap();
        assert_eq!(s["version_mismatch"], Value::Null);
        assert_eq!(s["hook_health"]["skill_started"], 2);
        let id = s["interview_id"].clone();
        let begin = json!({"tool_schema": 4, "skill": "grill-me", "mode": "register"});
        let b = call(&mut app, "begin_interview", begin).unwrap();
        assert_eq!(
            (b["existing"].clone(), b["interview_id"].clone()),
            (json!(true), id)
        );
        assert_eq!(
            call(&mut app, "status", json!({})).unwrap()["state"],
            "active"
        );
    }

    #[test]
    fn user_answers_need_a_prompt_hook_call() {
        let mut app = started();
        q(&mut app, "a");
        let user = json!({"id": "a", "answer": "x", "source": "user", "rationale": "r"});
        let e = call(&mut app, "resolve", user.clone()).unwrap_err();
        assert!(e.contains("hooks are not firing"));
        let p = json!({"id": "a", "reason": "r", "parked_by": "user"});
        assert!(
            call(&mut app, "park", p)
                .unwrap_err()
                .contains("not firing")
        );
        let v = json!({"kind": "question", "text": "t", "answer": "x", "source": "user", "rationale": "r"});
        assert!(
            call(&mut app, "log_item", v)
                .unwrap_err()
                .contains("not firing")
        );
        call(
            &mut app,
            "note_prompt",
            json!({"session_id": "s", "prompt": "yes"}),
        )
        .unwrap();
        call(&mut app, "resolve", user).unwrap();
    }

    fn resolve_args(id: &str) -> Value {
        json!({"id": id, "answer": "an", "source": "model", "rationale": "why"})
    }

    #[test]
    fn resolve_park_reopen_flow() {
        let mut app = started();
        let p = json!({"session_id": "s", "prompt": "park b"});
        call(&mut app, "note_prompt", p).unwrap();
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
        let mut app = fresh();
        let e = call(&mut app, "resolve", resolve_args("q1")).unwrap_err();
        assert!(e.contains("no such item: q1"));
        let e = call(&mut app, "reopen", json!({"id": "q1", "reason": "r"})).unwrap_err();
        assert!(e.contains("no such item: q1"));
        assert_eq!(
            call(&mut app, "status", json!({})).unwrap()["state"],
            "none"
        );
    }

    fn stop(app: &mut App, msg: &str) -> Value {
        let args = json!({"session_id": "s", "last_assistant_message": msg});
        let r = app.call("check_stop", &args, false).expect("known tool");
        assert!(!r.is_error, "{}", r.text);
        serde_json::from_str(&r.text).unwrap()
    }

    fn prompt(app: &mut App) {
        let args = json!({"session_id": "s", "prompt": "answer"});
        assert!(!app.call("note_prompt", &args, false).unwrap().is_error);
    }

    #[test]
    fn stop_reason_lists_ten_items_then_n_more() {
        let mut app = started();
        for n in 0..10 {
            q(&mut app, &format!("a{n}"));
        }
        let r = stop(&mut app, "done");
        let reason = r["reason"].as_str().unwrap();
        assert!(
            reason.contains("a9") && !reason.contains(" more"),
            "{reason}"
        );
        prompt(&mut app);
        q(&mut app, "a10");
        q(&mut app, "a11");
        let r = stop(&mut app, "done");
        let reason = r["reason"].as_str().unwrap();
        assert!(reason.contains("a9") && !reason.contains("a10"), "{reason}");
        assert!(reason.ends_with("2 more"), "{reason}");
    }

    #[test]
    fn caps_fail_open_and_a_prompt_resets_only_its_own_count() {
        let mut app = started();
        q(&mut app, "a");
        assert_eq!(stop(&mut app, "done")["decision"], "block");
        assert_eq!(stop(&mut app, "done")["decision"], "block");
        assert_eq!(stop(&mut app, "done"), json!({}));
        assert_eq!(
            app.blocks,
            Blocks {
                prompt: 2,
                finish: 2,
                unlogged: 0
            }
        );
        prompt(&mut app);
        assert_eq!(
            app.blocks,
            Blocks {
                prompt: 0,
                finish: 2,
                unlogged: 0
            }
        );
        assert_eq!(
            stop(&mut app, "What about the weather today?")["decision"],
            "block"
        );
        assert_eq!(
            app.blocks,
            Blocks {
                prompt: 1,
                finish: 2,
                unlogged: 1
            }
        );
    }

    #[test]
    fn unknown_classification_fails_open_and_gives_the_flat_budget() {
        let mut app = started();
        q(&mut app, "a");
        let v = app.gate(Class::Unknown);
        assert_eq!(v.reason, None);
        assert_eq!((v.classification, v.presented), ("unknown", None));
        prompt(&mut app);
        assert_eq!(app.allowance, FLAT_BUDGET);
        // an empty presented list gives 1, which is distinct from unknown
        prompt(&mut app);
        assert_eq!(app.allowance, 1);
    }

    #[test]
    fn clean_finish_completes_and_a_mutation_returns_to_active() {
        let mut app = started();
        assert_eq!(stop(&mut app, "done"), json!({}));
        assert!(app.complete);
        assert_eq!(stop(&mut app, "anything at all"), json!({}));
        q(&mut app, "late");
        assert!(!app.complete);
    }

    #[test]
    fn session_change_and_second_stop_end_interviews() {
        let project = std::env::temp_dir().join(format!("regspike-s-{}", std::process::id()));
        // SAFETY: no other test reads CLAUDE_PROJECT_DIR for anything but a journal key.
        unsafe { std::env::set_var("CLAUDE_PROJECT_DIR", &project) };
        let mut app = fresh();
        let state = |a: &App| a.status(&Map::new()).unwrap()["state"].clone();
        prompt(&mut app);
        q(&mut app, "q1");
        assert_eq!(state(&app), "active");
        // A hook call from another session abandons the bound interview.
        let other = json!({"session_id": "other", "prompt": "hello"});
        call(&mut app, "note_prompt", other).unwrap();
        assert_eq!(state(&app), "none");
        // A pending interview survives one Stop check and is stood down by the second.
        let sk = json!({"tool_schema": 4, "session_id": "other", "skill": "grill-me"});
        call(&mut app, "skill_started", sk).unwrap();
        let stop = json!({"session_id": "other", "last_assistant_message": "hm?"});
        call(&mut app, "check_stop", stop.clone()).unwrap();
        assert_eq!(state(&app), "pending");
        call(&mut app, "check_stop", stop).unwrap();
        assert_eq!(state(&app), "none");
        let _ = std::fs::remove_dir_all(&project);
    }

    #[test]
    fn end_interview_follows_the_state_matrix() {
        // Decision logs go under the project path; keep them out of the source tree.
        let project = std::env::temp_dir().join(format!("regspike-p-{}", std::process::id()));
        // SAFETY: no other test reads CLAUDE_PROJECT_DIR for anything but a journal key.
        unsafe { std::env::set_var("CLAUDE_PROJECT_DIR", &project) };
        let mut app = fresh();
        let err = call(&mut app, "end_interview", json!({})).unwrap_err();
        assert!(err.contains("no interview to end"));
        let err = call(&mut app, "end_interview", json!({"abandon": true})).unwrap_err();
        assert!(err.contains("invalid arguments"));
        q(&mut app, "q1");
        let err = call(&mut app, "end_interview", json!({})).unwrap_err();
        assert!(err.contains("q1"));
        let err = call(&mut app, "end_interview", json!({"mode": "plain"})).unwrap_err();
        assert!(err.contains("mode plain applies only to a pending interview"));
        let ended = call(
            &mut app,
            "end_interview",
            json!({"abandon": true, "reason": "stop"}),
        )
        .unwrap();
        assert_eq!(ended["end_kind"], "ended_incomplete_by_model");
        assert_eq!(app.status(&Map::new()).unwrap()["state"], "none");
    }

    #[test]
    fn undersized_parent_blocks_unless_the_user_resolved_it() {
        let mut app = started();
        prompt(&mut app);
        let ok = |app: &mut App, n: &str, args: Value| {
            let r = app.call(n, &args, true).unwrap();
            assert!(!r.is_error, "{}", r.text);
        };
        ok(
            &mut app,
            "log_item",
            json!({"id": "p", "kind": "question", "text": "parent"}),
        );
        ok(
            &mut app,
            "resolve",
            json!({"id": "p", "answer": "a", "source": "model", "rationale": "r"}),
        );
        ok(
            &mut app,
            "log_item",
            json!({"id": "c", "kind": "subquestion", "text": "child", "parent": "p", "answer": "a", "source": "model", "rationale": "r"}),
        );
        let r = stop(&mut app, "done");
        assert!(r["reason"].as_str().unwrap().contains("p parent"), "{r}");
    }
}
