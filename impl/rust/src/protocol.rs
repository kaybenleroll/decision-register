//! Newline-delimited JSON-RPC 2.0 for MCP over stdio (protocol 2024-11-05).

use serde_json::{Value, json};

use crate::tools::{self, App};

pub const PROTOCOL_VERSION: &str = "2024-11-05";
const CONTRACT: &str = include_str!("../../../contract/tools.json");

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

/// `tools/list` body: the implemented tools with their contract input schemas.
fn tool_list() -> Value {
    let doc: Value = serde_json::from_str(CONTRACT).expect("contract/tools.json parses");
    let list: Vec<Value> = tools::IMPLEMENTED
        .iter()
        .map(|name| {
            let tool = &doc["tools"][*name];
            let mut schema = tool["input"].clone();
            schema["$defs"] = doc["$defs"].clone();
            json!({"name": name, "description": tool["description"], "inputSchema": schema})
        })
        .collect();
    json!({ "tools": list })
}

impl Server {
    pub fn new() -> Self {
        Self { app: App::new() }
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
                    "serverInfo": {"name": "regspike-rs", "version": env!("CARGO_PKG_VERSION")},
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
        let mut s = Server::new();
        let r = rpc(
            &mut s,
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}"#,
        );
        assert_eq!(r["result"]["protocolVersion"], PROTOCOL_VERSION);
        assert_eq!(r["id"], 1);
    }

    #[test]
    fn notification_gets_no_reply() {
        let mut s = Server::new();
        assert!(
            s.handle_line(r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#)
                .is_none()
        );
    }

    #[test]
    fn malformed_input_is_a_protocol_error() {
        let mut s = Server::new();
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
        let mut s = Server::new();
        let r = rpc(&mut s, r#"{"id":1,"method":"tools/list"}"#);
        let tools = r["result"]["tools"].as_array().unwrap();
        assert_eq!(tools.len(), tools::IMPLEMENTED.len());
        assert!(tools.iter().all(|t| t["inputSchema"]["type"] == "object"));
    }
}
