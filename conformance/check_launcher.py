#!/usr/bin/env python3
"""Plugin skeleton checks (slice P1, REQ-73, REQ-77); stdlib only. Run: python3 -I conformance/check_launcher.py

Positive checks that the launcher reaches the REAL server and does not mask a missing one:
  rs   regspike-rs found on PATH (set REGSPIKE_RS_DIR to the directory holding it, else the PATH is used);
  ts   REGSPIKE_IMPL=ts reaches impl/ts/dist/regspike.mjs (build it first: cd impl/ts && npm ci && npm run build);
  off  REGSPIKE_IMPL=off, an unknown value, and a missing binary serve the stub (serverInfo name register-stub).
A real server is recognised by serverInfo.name not being register-stub, only contract tools listed (all twelve once I2 to I4 land), and a `status`
answer that carries tool_schema equal to contract/tools.json. A build that is absent is reported SKIP, not ok.
Also checks the static plugin files: JSON validity, hook tools and tool_schema against tools.json, no skill text.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCH = os.path.join(ROOT, "plugin", "bin", "register-launch")
TOOLS = json.load(open(os.path.join(ROOT, "contract", "tools.json")))
SCHEMA = TOOLS["tool_schema"]
NAMES = set(TOOLS["tools"] if isinstance(TOOLS["tools"], dict) else [t["name"] for t in TOOLS["tools"]])
fails, skips = [], []


def report(ok, name, why=""):
    print(("ok" if ok else "not ok") + " - " + name + (" # " + why if why and not ok else ""))
    if not ok:
        fails.append(name)


def talk(env_extra, path=None, drop=()):
    env = dict(os.environ)
    for k in ("REGSPIKE_IMPL", "REGSPIKE_TS_BUNDLE") + tuple(drop):
        env.pop(k, None)
    env.update(env_extra)
    if path is not None:
        env["PATH"] = path
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05",
         "capabilities": {}, "clientInfo": {"name": "check", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "status", "arguments": {}}},
    ]
    p = subprocess.run([LAUNCH], input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True,
                       text=True, env=env, timeout=30)
    out = {}
    for line in p.stdout.splitlines():
        r = json.loads(line)
        out[r["id"]] = r["result"]
    status = json.loads(out[3]["content"][0]["text"])
    return out[1]["serverInfo"]["name"], [t["name"] for t in out[2]["tools"]], status, p.stderr


def is_real(r):
    name, tools, status, _ = r
    if len(tools) < len(NAMES):
        print("# PENDING: %d of %d tools listed (the rest arrive in I2 to I4)" % (len(tools), len(NAMES)))
    return name != "register-stub" and "status" in tools and set(tools) <= NAMES and status.get("tool_schema") == SCHEMA


def is_stub(r, line):
    name, tools, status, err = r
    return name == "register-stub" and status.get("unavailable") and "tool_schema" not in status and err == line + "\n"


def static_checks():
    for rel in ("plugin/.claude-plugin/plugin.json", "plugin/.mcp.json", "plugin/hooks/hooks.json",
                ".claude-plugin/marketplace.json"):
        try:
            json.load(open(os.path.join(ROOT, rel)))
            report(True, "json " + rel)
        except Exception as e:  # noqa: BLE001
            report(False, "json " + rel, str(e))
    hooks = json.load(open(os.path.join(ROOT, "plugin/hooks/hooks.json")))["hooks"]
    calls = [(ev, h) for ev, groups in hooks.items() for g in groups for h in g["hooks"]]
    hook_tools = {"note_prompt", "check_stop", "skill_started"}
    report({h["tool"] for _, h in calls} == hook_tools and all(h["type"] == "mcp_tool" for _, h in calls)
           and all(h["server"] == "plugin:decision-register:register" for _, h in calls), "hooks call the three hook-facing tools")
    props = {n: TOOLS["tools"][n]["input"]["properties"] for n in hook_tools} if isinstance(TOOLS["tools"], dict) else {}
    ok = True
    for _, h in calls:
        ok = ok and set(h["input"]) <= set(props.get(h["tool"], h["input"]))
        if "tool_schema" in h["input"]:
            ok = ok and h["input"]["tool_schema"] == SCHEMA
    report(ok, "hook inputs use contract argument names and tool_schema %d" % SCHEMA)
    skill = [os.path.join(d, f) for d, _, fs in os.walk(os.path.join(ROOT, "plugin")) for f in fs if f.lower() == "skill.md"]
    report(not skill, "no SKILL.md under plugin/")
    mcp = json.load(open(os.path.join(ROOT, "plugin/.mcp.json")))["mcpServers"]
    report(list(mcp) == ["register"] and mcp["register"]["command"].endswith("/bin/register-launch"),
           ".mcp.json starts server register through the launcher")


def main():
    static_checks()
    missing = "register unavailable: binary not found"
    # rs: needs regspike-rs on PATH (REGSPIKE_RS_DIR prepended)
    rs_dir = os.environ.get("REGSPIKE_RS_DIR")
    base = os.environ.get("PATH", "")
    path = (rs_dir + os.pathsep + base) if rs_dir else base
    if any(os.access(os.path.join(d, "regspike-rs"), os.X_OK) for d in path.split(os.pathsep) if d):
        report(is_real(talk({"REGSPIKE_IMPL": "rs"}, path)), "REGSPIKE_IMPL=rs reaches the real Rust server")
        report(is_real(talk({}, path)), "REGSPIKE_IMPL unset reaches the real Rust server")
    else:
        print("SKIP - rs positive check: regspike-rs not on PATH (set REGSPIKE_RS_DIR)")
        skips.append("rs")
    bundle = os.path.join(ROOT, "impl", "ts", "dist", "regspike.mjs")
    if os.path.isfile(bundle):
        report(is_real(talk({"REGSPIKE_IMPL": "ts"}, path)), "REGSPIKE_IMPL=ts reaches the real TypeScript server")
    else:
        print("SKIP - ts positive check: %s not built" % bundle)
        skips.append("ts")
    report(is_stub(talk({"REGSPIKE_IMPL": "ts", "REGSPIKE_TS_BUNDLE": "/nonexistent/regspike.mjs"}, path), missing),
           "REGSPIKE_IMPL=ts with the bundle missing serves the stub, not success")
    report(is_stub(talk({"REGSPIKE_IMPL": "off"}, path), "register off: REGSPIKE_IMPL=off"), "REGSPIKE_IMPL=off serves the stub")
    report(is_stub(talk({"REGSPIKE_IMPL": "bogus"}, path), "register off: unknown REGSPIKE_IMPL=bogus"),
           "an unknown REGSPIKE_IMPL serves the stub")
    py = os.path.dirname(subprocess.run(["sh", "-c", "command -v python3"], capture_output=True, text=True).stdout.strip())
    report(is_stub(talk({"REGSPIKE_IMPL": "rs"}, py + os.pathsep + "/usr/bin" + os.pathsep + "/bin"), missing),
           "the Rust binary absent from PATH serves the stub")
    print("# %d failed, %d skipped" % (len(fails), len(skips)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
