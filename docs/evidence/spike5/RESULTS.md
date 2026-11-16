# Spike 5: Stop hook mcp_tool ${last_assistant_message}
Claude Code 2.1.325, haiku, plugin spike5 (copied from spike4; Stop hook mcp_tool check_stop + command hook logging raw stdin).
Commands: python3 -I mkprompt.py; cd cwd && claude -p --plugin-dir ../plug --model haiku --allowedTools 'mcp__plugin_spike5_stub__*' --output-format json "$(cat ../prompt_long.txt)" </dev/null; python3 -I cmp.py
Result (1 run): claude result 6869 chars; MCP server received last_assistant_message identical (len 6869, sha256 match, string equality True), including quotes, backslashes, newlines, ?, unicode, JSON fragment. No _meta in tools/call params. Argument keys: only last_assistant_message.
Raw Stop hook stdin keys: background_tasks, cwd, effort, hook_event_name, last_assistant_message, permission_mode, prompt_id, session_crons, session_id, stop_hook_active, transcript_path. Command-hook field equals result too.
Short-message run: not done. Cost: $0.0074 (1 run).
