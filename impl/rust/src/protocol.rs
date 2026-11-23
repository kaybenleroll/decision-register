//! Newline-delimited JSON-RPC 2.0 for MCP over stdio (protocol 2024-11-05).

use serde_json::{Value, json};

use crate::tools::{self, App};

pub const PROTOCOL_VERSION: &str = "2024-11-05";
const CONTRACT: &str = include_str!("../../../contract/tools.json");
#[cfg(test)]
const CEILINGS: &str = include_str!("../../../conformance/tools_list_ceilings.json");

pub struct Server {
    app: App,
}

impl Default for Server {
    fn default() -> Self {
        Self::new()
    }
}

fn reply_ok(id: &Value, result: Value) -> String {
    json!({"jsonrpc": "2.0", "id": id, "result": result}).to_string()
}

fn reply_err(id: &Value, code: i64, message: &str) -> String {
    json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}).to_string()
}

/// Most `$ref` resolutions along one chain before `inline_refs` gives up (a cycle).
const MAX_REF_HOPS: usize = 8;

/// Copies `node` with every `{"$ref": "#/$defs/<name>"}` replaced by the body of that def, so the
/// served schema needs no `$defs`. `hops` counts `$ref` resolutions only. An unknown def, a ref
/// that is not `#/$defs/<name>` with one path segment, a `$ref` object with sibling keys, or more
/// than `MAX_REF_HOPS` resolutions is an error.
fn inline_refs(node: &Value, defs: &Value, hops: usize) -> Result<Value, String> {
    match node {
        Value::Object(map) => {
            if let Some(r) = map.get("$ref") {
                if map.len() != 1 {
                    return Err("$ref object has sibling keys".to_string());
                }
                let r = r.as_str().ok_or("$ref is not a string")?;
                let name = r
                    .strip_prefix("#/$defs/")
                    .filter(|n| !n.is_empty() && !n.contains('/'))
                    .ok_or_else(|| format!("unsupported $ref {r}"))?;
                if hops >= MAX_REF_HOPS {
                    return Err(format!("more than {MAX_REF_HOPS} $ref resolutions at {r}"));
                }
                let def = defs.get(name).ok_or_else(|| format!("unknown $ref {r}"))?;
                return inline_refs(def, defs, hops + 1);
            }
            let mut out = serde_json::Map::new();
            for (k, v) in map {
                out.insert(k.clone(), inline_refs(v, defs, hops)?);
            }
            Ok(Value::Object(out))
        }
        Value::Array(items) => items
            .iter()
            .map(|v| inline_refs(v, defs, hops))
            .collect::<Result<Vec<_>, _>>()
            .map(Value::Array),
        other => Ok(other.clone()),
    }
}

/// `tools/list` body: all twelve contract tools (unbuilt ones answer calls with an error result) with their contract input schemas, refs inlined and no `$defs` block (issue #114).
fn tool_list() -> Value {
    let doc: Value = serde_json::from_str(CONTRACT).expect("contract/tools.json parses");
    let list: Vec<Value> = tools::CONTRACT_TOOLS
        .iter()
        .map(|name| {
            let tool = &doc["tools"][*name];
            let schema =
                inline_refs(&tool["input"], &doc["$defs"], 0).expect("contract refs resolve");
            json!({"name": name, "description": tool["description"], "inputSchema": schema})
        })
        .collect();
    json!({ "tools": list })
}

impl Server {
    pub fn new() -> Self {
        crate::claim::prune(&crate::journal::project_dir(), crate::journal::now_secs());
        Self { app: App::new() }
    }

    /// Called at end of input: flushes journal events queued since the last `check_stop`.
    pub fn finish(&mut self) {
        self.app.shutdown();
    }

    /// Handles one input line; `None` when no reply is due (notifications).
    pub fn handle_line(&mut self, line: &str) -> Option<String> {
        let Ok(msg) = serde_json::from_str::<Value>(line) else {
            return Some(reply_err(&Value::Null, -32700, "parse error"));
        };
        let Some(obj) = msg.as_object() else {
            return Some(reply_err(&Value::Null, -32600, "invalid request"));
        };
        let id = obj.get("id");
        let Some(method) = obj.get("method").and_then(Value::as_str) else {
            let is_response = obj.contains_key("result") || obj.contains_key("error");
            return if is_response {
                None
            } else {
                Some(reply_err(
                    id.unwrap_or(&Value::Null),
                    -32600,
                    "invalid request",
                ))
            };
        };
        let id = id?; // no id: a notification, never answered
        let params = obj.get("params").unwrap_or(&Value::Null);
        Some(match method {
            "initialize" => reply_ok(
                id,
                json!({
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "register-rs", "version": env!("CARGO_PKG_VERSION")},
                }),
            ),
            "ping" => reply_ok(id, json!({})),
            "tools/list" => reply_ok(id, tool_list()),
            "tools/call" => self.tools_call(id, params),
            _ => reply_err(id, -32601, &format!("method not found: {method}")),
        })
    }

    fn tools_call(&mut self, id: &Value, params: &Value) -> String {
        let Some(name) = params.get("name").and_then(Value::as_str) else {
            return reply_err(id, -32602, "malformed params: name is required");
        };
        let args = match params.get("arguments") {
            None | Some(Value::Null) => json!({}),
            Some(v) if v.is_object() => v.clone(),
            Some(_) => {
                return reply_err(id, -32602, "malformed params: arguments must be an object");
            }
        };
        let model_facing = params
            .get("_meta")
            .and_then(Value::as_object)
            .is_some_and(|m| m.contains_key("claudecode/toolUseId"));
        match self.app.call(name, &args, model_facing) {
            Some(r) => reply_ok(
                id,
                json!({"content": [{"type": "text", "text": r.text}], "isError": r.is_error}),
            ),
            None => reply_err(id, -32602, &format!("unknown tool: {name}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rpc(server: &mut Server, line: &str) -> Value {
        serde_json::from_str(&server.handle_line(line).expect("reply")).unwrap()
    }

    #[test]
    fn initialize_reports_protocol_version() {
        let mut s = Server {
            app: App::with_dir(tools::tests::scratch_dir()),
        };
        let r = rpc(
            &mut s,
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}"#,
        );
        assert_eq!(r["result"]["protocolVersion"], PROTOCOL_VERSION);
        assert_eq!(r["id"], 1);
    }

    #[test]
    fn notification_gets_no_reply() {
        let mut s = Server {
            app: App::with_dir(tools::tests::scratch_dir()),
        };
        assert!(
            s.handle_line(r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#)
                .is_none()
        );
    }

    #[test]
    fn malformed_input_is_a_protocol_error() {
        let mut s = Server {
            app: App::with_dir(tools::tests::scratch_dir()),
        };
        assert_eq!(rpc(&mut s, "{nope")["error"]["code"], -32700);
        assert_eq!(
            rpc(&mut s, r#"{"id":2,"method":"x"}"#)["error"]["code"],
            -32601
        );
        let bad = r#"{"id":3,"method":"tools/call","params":{"name":"zzz"}}"#;
        assert_eq!(rpc(&mut s, bad)["error"]["code"], -32602);
    }

    #[test]
    fn tools_list_serves_contract_schemas() {
        let mut s = Server {
            app: App::with_dir(tools::tests::scratch_dir()),
        };
        let r = rpc(&mut s, r#"{"id":1,"method":"tools/list"}"#);
        let tools = r["result"]["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 12);
        assert!(tools.iter().all(|t| t["inputSchema"].is_object()));
        let doc: Value = serde_json::from_str(CONTRACT).unwrap();
        let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
        let mut want: Vec<&str> = doc["tools"]
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        let mut got = names.clone();
        want.sort_unstable();
        got.sort_unstable();
        assert_eq!(got, want);
    }

    /// Independent resolver for the expectation (does not call `inline_refs`): contract refs are
    /// all `#/$defs/<name>` leaves today.
    fn resolve_expected(node: &Value, defs: &Value) -> Value {
        match node {
            Value::Object(m) => match m.get("$ref").and_then(Value::as_str) {
                Some(r) => defs[r.trim_start_matches("#/$defs/")].clone(),
                None => Value::Object(
                    m.iter()
                        .map(|(k, v)| (k.clone(), resolve_expected(v, defs)))
                        .collect(),
                ),
            },
            Value::Array(a) => Value::Array(a.iter().map(|v| resolve_expected(v, defs)).collect()),
            o => o.clone(),
        }
    }

    fn has_key(v: &Value, banned: &[&str]) -> bool {
        match v {
            Value::Object(m) => m
                .iter()
                .any(|(k, c)| banned.contains(&k.as_str()) || has_key(c, banned)),
            Value::Array(a) => a.iter().any(|c| has_key(c, banned)),
            _ => false,
        }
    }

    #[test]
    fn tools_list_inlines_refs_and_drops_defs() {
        let doc: Value = serde_json::from_str(CONTRACT).unwrap();
        let ceilings: Value = serde_json::from_str(CEILINGS).unwrap();
        let mut s = Server {
            app: App::with_dir(tools::tests::scratch_dir()),
        };
        let r = rpc(&mut s, r#"{"id":1,"method":"tools/list"}"#);
        let tools = r["result"]["tools"].as_array().unwrap();
        let mut names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
        let mut want: Vec<&str> = doc["tools"]
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        names.sort_unstable();
        want.sort_unstable();
        assert_eq!(want.len(), 12);
        assert_eq!(names, want);
        for t in tools {
            let name = t["name"].as_str().unwrap();
            let schema = &t["inputSchema"];
            assert!(
                !has_key(schema, &["$defs", "definitions", "$ref"]),
                "{name} still has $defs or $ref"
            );
            assert_eq!(schema["type"], "object", "{name} top-level type");
            assert_eq!(
                *schema,
                resolve_expected(&doc["tools"][name]["input"], &doc["$defs"]),
                "{name} inputSchema differs from the resolved contract input"
            );
        }
        let chars = |list: &[&Value]| serde_json::to_string(list).unwrap().chars().count();
        let all: Vec<&Value> = tools.iter().collect();
        let model: Vec<&Value> = tools
            .iter()
            .filter(|t| doc["tools"][t["name"].as_str().unwrap()]["audience"] == "model")
            .collect();
        assert_eq!(model.len(), 9);
        let all_max = ceilings["all_tools_chars"].as_u64().unwrap() as usize;
        let model_max = ceilings["model_tools_chars"].as_u64().unwrap() as usize;
        assert!(
            chars(&all) <= all_max,
            "all tools {} chars > {all_max}",
            chars(&all)
        );
        assert!(
            chars(&model) <= model_max,
            "model tools {} chars > {model_max}",
            chars(&model)
        );
    }

    #[test]
    fn inline_refs_rejects_bad_refs() {
        let defs = json!({"a": {"type": "string"}, "loop": {"$ref": "#/$defs/loop"}, "n": {"x": {"y": 1}}});
        assert_eq!(
            inline_refs(&json!({"p": {"$ref": "#/$defs/a"}}), &defs, 0).unwrap(),
            json!({"p": {"type": "string"}})
        );
        for bad in [
            json!({"$ref": "#/$defs/missing"}),
            json!({"$ref": "#/definitions/a"}),
            json!({"$ref": "#/$defs/a", "description": "x"}),
            json!({"$ref": "#/$defs/n/x"}),
            json!({"$ref": "#/$defs/loop"}),
        ] {
            assert!(inline_refs(&bad, &defs, 0).is_err(), "{bad} should fail");
        }
    }

    #[test]
    fn unbuilt_tool_returns_not_implemented_error_result() {
        let mut s = Server {
            app: App::with_dir(tools::tests::scratch_dir()),
        };
        // Every contract tool is built from I3 on, so the fallback is unreachable for them.
        // `resume` and `discard` answer with a domain error result, never a protocol error.
        for name in ["discard", "resume"] {
            let line = format!(
                r#"{{"id":1,"method":"tools/call","params":{{"name":"{name}","arguments":{{}}}}}}"#
            );
            let r = rpc(&mut s, &line);
            assert!(r.get("error").is_none());
            assert_eq!(r["result"]["isError"], true);
            let text = r["result"]["content"][0]["text"].as_str().unwrap();
            assert_eq!(text, "no unfinished journal");
        }
    }

    #[test]
    fn built_tool_is_unaffected_by_unbuilt_handling() {
        let mut s = Server {
            app: App::with_dir(tools::tests::scratch_dir()),
        };
        let r = rpc(
            &mut s,
            r#"{"id":1,"method":"tools/call","params":{"name":"status","arguments":{}}}"#,
        );
        let text = r["result"]["content"][0]["text"].as_str().unwrap();
        assert!(!text.starts_with("not implemented"));
    }

    /// Claude Code rejects a tool whose inputSchema has no top-level `type: object`, and one
    /// rejection hides every tool of the server; this covers all contract tools, including
    /// those this build does not serve yet (end_interview is a oneOf).
    #[test]
    fn every_contract_tool_input_has_a_top_level_object_type() {
        let doc: Value = serde_json::from_str(CONTRACT).unwrap();
        let tools = doc["tools"].as_object().unwrap();
        assert_eq!(tools.len(), 12);
        for (name, tool) in tools {
            assert_eq!(tool["input"]["type"], "object", "{name} input type");
        }
    }
}
