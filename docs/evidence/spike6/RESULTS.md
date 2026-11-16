# Spike 6: session_id in UPS/Stop mcp_tool hooks; UPS return-text visibility
Claude Code 2.1.325, haiku, plugin spike6 (from spike5). Hooks: UserPromptSubmit->note_prompt{session_id,prompt}; Stop->check_stop{session_id,last_assistant_message} + nonexistent_tool{session_id}.
Run (from spike6/cwd): claude -p --plugin-dir ../plug --model haiku --max-budget-usd 0.15 --output-format json "$(cat ../prompt.txt)" </dev/null ; mode.txt set to 'HOOKMARK-7731 prompt noted' (run1) then '{}' (run2).
Q-A PASS: session_id substituted in UPS and Stop; both hooks logged the same id per run, equal to result session_id (run1 d7c7659d-..., run2 9d54dafe-...). No _meta in params.
Q-B: run1 model: "**No** HOOKMARK-7731 in my context." run2 (returns {}) model: "No ... no bare {}". UPS mcp_tool return text was NOT visible to the model in either run.
Q-C: Stop '{}' allowed the turn (normal result). nonexistent_tool was still called on the server (logged); no 'hook error' or 'nonexistent' text in the json output.
Cost: 0.00508 + 0.00311 USD.
