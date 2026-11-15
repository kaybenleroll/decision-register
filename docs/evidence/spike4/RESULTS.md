# Spike 4 results
Docs (hooks.md): mcp_tool `input` string values support `${path}` dotted substitution from hook JSON input (fields: session_id, prompt_id, cwd, etc; UserPromptSubmit adds `prompt`; UserPromptExpansion adds expansion_type, command_name, command_args, command_source, prompt). Per-event variable list not documented. PreToolUse matcher is any tool name, but Skill tool_input schema not documented. Doc states typed /skill bypasses PreToolUse; UserPromptExpansion fires instead (matcher = command_name).
Live (haiku, plugin spike4, neutral cwd), server.log in logs/server.log:
- UPS ${prompt}: PASS both runs ("Use the probe skill..." / "/probe hello world" raw typed line).
- PreToolUse matcher Skill, ${tool_input.skill}: PASS, got "spike4:probe" (plugin-qualified). tool_input.name/args empty -> field is `skill`.
- Typed /probe: no PreToolUse; UserPromptExpansion PASS: command_name="spike4:probe", command_args="hello world", prompt="/spike4:probe hello world". UPS also fired first.
- Hook-called tool gets no _meta; session_id substitution works (sid present).
Spend: two haiku -p runs, each capped --max-budget-usd 0.05; exact cost not captured (output truncated), est <0.10 total.
