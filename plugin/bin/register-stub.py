#!/usr/bin/env python3
"""No-op stand-in for the register MCP server (REQ-77), standard library only.

Served by register-launch when the binary is missing or the register is switched off, so that the
plugin's hooks succeed silently instead of failing. Hook-facing tools return {} (the exact empty
shape the harness ignores); every other tool, status included, returns {"unavailable": <reason>}
with no tool_schema and no version_mismatch key. It never blocks and never writes anything.
Exits on stdin EOF. The launcher prints the one-line stderr message; this process prints none.
"""
import json
import os
import sys

REASON = os.environ.get("REGSPIKE_STUB_REASON") or "binary not found"
HOOK_TOOLS = ("note_prompt", "skill_started", "check_stop")
MODEL_TOOLS = ("begin_interview", "log_item", "resolve", "park", "reopen", "status", "resume", "discard",
               "end_interview")
OPEN_SCHEMA = {"type": "object", "additionalProperties": True}


def result(text):
    return {"content": [{"type": "text", "text": text}], "isError": False}


def handle(req):
    method, params = req.get("method"), req.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion") or "2024-11-05",
                "capabilities": {"tools": {}}, "serverInfo": {"name": "register-stub", "version": "0"}}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": "unavailable (stub)", "inputSchema": OPEN_SCHEMA}
                          for n in HOOK_TOOLS + MODEL_TOOLS]}
    if method == "tools/call":
        if params.get("name") in HOOK_TOOLS:
            return result("{}")
        return result(json.dumps({"unavailable": REASON}))
    if method == "ping":
        return {}
    raise KeyError(method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if not isinstance(req, dict) or "id" not in req:  # notifications get no reply
            continue
        try:
            out = {"jsonrpc": "2.0", "id": req["id"], "result": handle(req)}
        except KeyError:
            out = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": "method not found"}}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
