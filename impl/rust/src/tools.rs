//! Tool handlers. The tool schemas themselves come from `contract/tools.json`.

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

/// The outcome of a `tools/call`: text plus the `isError` flag.
pub struct ToolResult {
    pub text: String,
    pub is_error: bool,
}

#[derive(Default)]
pub struct App {}

impl App {
    pub fn new() -> Self {
        Self::default()
    }

    /// Runs a tool. `None` means the tool name is unknown (a protocol error).
    pub fn call(
        &mut self,
        _name: &str,
        _args: &serde_json::Value,
        _model_facing: bool,
    ) -> Option<ToolResult> {
        None
    }
}
