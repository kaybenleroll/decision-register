#!/usr/bin/env python3
"""Pre-flight probe for smoke/run.sh: starts a server build locally (no claude, no spend), calls status, prints
"<server_version> <tool_schema>" on one line. Usage: python3 -I smoke/probe_server.py <data-dir> <command> [args...]
The data dir is used as CLAUDE_PLUGIN_DATA and CLAUDE_PROJECT_DIR, so the probe never touches a real data root."""
import json, os, subprocess, sys

data, cmd = sys.argv[1], sys.argv[2:]
os.makedirs(data, exist_ok=True)
env = dict(os.environ, CLAUDE_PLUGIN_DATA=data, CLAUDE_PROJECT_DIR=data)
msgs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke-probe", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "status", "arguments": {}}},
]
try:
    r = subprocess.run(cmd, input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True, env=env, timeout=20)
except (OSError, subprocess.TimeoutExpired) as e:
    print("probe failed: %s" % e, file=sys.stderr)
    sys.exit(1)
for line in r.stdout.splitlines():
    try:
        m = json.loads(line)
    except ValueError:
        continue
    if m.get("id") == 2:
        try:
            body = m["result"].get("structuredContent") or json.loads(m["result"]["content"][0]["text"])
            print(body["server_version"], body["tool_schema"])
            sys.exit(0)
        except (KeyError, IndexError, ValueError, TypeError):
            break
print("probe failed: no usable status reply", file=sys.stderr)
sys.exit(1)
