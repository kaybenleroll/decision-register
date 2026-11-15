#!/usr/bin/env python3
"""Arm G: stdlib stdio MCP server `register` (newline-delimited JSON-RPC 2.0).

Methods: initialize (echoes the client's protocolVersion), notifications/initialized (no reply), ping, tools/list,
tools/call. Logs to stderr only. Holds no state in memory: every call replays the durable event log under the
sidecar lock (store module readback/register_store.py), so a restart per `claude -p --resume` call loses nothing.
State directory: $SEQ_STATE_DIR (the driver also passes it in mcp.json env).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import register_store as store  # noqa: E402

SERVER_INFO = {"name": "register", "version": "1.0.0"}
DEFAULT_PROTOCOL = "2025-06-18"


def state_dir():
    d = os.environ.get("SEQ_STATE_DIR")
    if not d:
        raise RuntimeError("SEQ_STATE_DIR is not set")
    return d


def log(*a):
    print("[register]", *a, file=sys.stderr, flush=True)


def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def tools_list():
    return {"tools": [dict(name=n, description=t["description"], inputSchema=t["inputSchema"])
                      for n, t in store.TOOL_SCHEMAS.items()]}


def handle(msg):
    """Handle one decoded message. Returns True if a reply was sent (or none is due)."""
    method = msg.get("method")
    id_ = msg.get("id")
    is_note = "id" not in msg
    params = msg.get("params") or {}
    if method == "initialize":
        reply(id_, {"protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL,
                    "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO})
    elif method in ("notifications/initialized", "initialized") or (is_note and (method or "").startswith("notifications/")):
        return
    elif method == "ping":
        reply(id_, {})
    elif method == "tools/list":
        reply(id_, tools_list())
    elif method == "tools/call":
        name = params.get("name")
        if name not in store.TOOL_SCHEMAS:
            reply(id_, error={"code": -32602, "message": "unknown tool: %s" % name})
            return
        try:
            is_err, text = store.call_tool(state_dir(), name, params.get("arguments") or {})
        except Exception as exc:  # noqa: BLE001
            log("tool error", repr(exc))
            is_err, text = True, "%s: internal error (%s); try the call again" % (name, type(exc).__name__)
        reply(id_, {"content": [{"type": "text", "text": text}], "isError": bool(is_err)})
    elif is_note:
        return
    else:
        reply(id_, error={"code": -32601, "message": "method not found: %s" % method})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError("not an object")
        except ValueError:
            reply(None, error={"code": -32700, "message": "parse error"})
            continue
        try:
            handle(msg)
        except Exception as exc:  # noqa: BLE001
            log("handler error", repr(exc))
            if "id" in msg:
                reply(msg["id"], error={"code": -32603, "message": "internal error"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
