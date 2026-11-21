#!/usr/bin/env python3
"""Plugin skeleton checks (slice P1, REQ-73, REQ-77); stdlib only. Run: python3 -I conformance/check_launcher.py

Positive checks that the launcher reaches the REAL server and does not mask a missing one:
  rs   register-rs found on PATH (set REGISTER_RS_DIR to the directory holding it, else the PATH is used);
  ts   REGISTER_IMPL=ts reaches impl/ts/dist/register.mjs (build it first: cd impl/ts && npm ci && npm run build);
  off  REGISTER_IMPL=off, an unknown value, and a missing binary serve the stub (serverInfo name register-stub).
The TypeScript bundle order (REQ-73) is checked on copies of the launcher in a temp tree under .scratch/launcher-check/,
so the repository-relative path cannot match unless a check builds it: 1. $REGISTER_TS_BUNDLE (a missing file serves the
stub, no fall-through), 2. <plugin>/../../impl/ts/dist/register.mjs, 3. the first register.mjs on PATH. A decoy
register.mjs that is not a server sits on PATH in the ordering checks, so a wrong order fails instead of passing. The
stub and no-PATH-hit checks use an explicit PATH (node, python3, /usr/bin:/bin, no mise dirs) so an ambient bundle cannot
change them and a missing node cannot pass them vacuously.
A real server is recognised by serverInfo.name not being register-stub, only contract tools listed (all twelve once I2 to I4 land), and a `status`
answer that carries tool_schema equal to contract/tools.json. A build that is absent is reported SKIP, not ok.
Also checks the static plugin files: JSON validity, hook tools and tool_schema against tools.json, no skill text.
"""
import json
import os
import shutil
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


def talk(env_extra, path=None, drop=(), launch=LAUNCH):
    env = dict(os.environ)
    for k in ("REGISTER_IMPL", "REGISTER_TS_BUNDLE") + tuple(drop):
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
    p = subprocess.run([launch], input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True,
                       text=True, env=env, timeout=30)
    out = {}
    try:
        for line in p.stdout.splitlines():
            r = json.loads(line)
            out[r["id"]] = r["result"]
        status = json.loads(out[3]["content"][0]["text"])
        return out[1]["serverInfo"]["name"], [t["name"] for t in out[2]["tools"]], status, p.stderr
    except (ValueError, KeyError, TypeError):  # not a server (a decoy ran, or nothing answered)
        return "<no server reply>", [], {}, p.stderr


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


DECOY_MARK = "decoy register.mjs ran"


def sh_which(cmd):
    return subprocess.run(["sh", "-c", "command -v " + cmd], capture_output=True, text=True).stdout.strip()


def has_bundle(d):
    return os.path.isfile(os.path.join(d, "register.mjs"))


def bundle_order_checks(bundle, clean_path):
    """The ts resolution order on launcher copies in a temp tree; see the module docstring."""
    node, py = sh_which("node"), sh_which("python3")
    if not node or not py:
        print("SKIP - ts order checks: node or python3 not found")
        skips.append("ts-order")
        return
    # explicit PATH: no ambient dirs (a mise-installed register.mjs, a tool shim), node and python3 present
    plain = os.pathsep.join([os.path.dirname(node), os.path.dirname(py), "/usr/bin", "/bin"])
    if any(has_bundle(d) for d in plain.split(os.pathsep)):
        print("SKIP - ts order checks: a register.mjs sits in %s" % plain)
        skips.append("ts-order")
        return
    tmp = os.path.join(ROOT, ".scratch", "launcher-check")
    shutil.rmtree(tmp, ignore_errors=True)

    def tree(name, with_checkout_bundle):
        base = os.path.join(tmp, name)
        os.makedirs(os.path.join(base, "plugin", "bin"))
        for f in ("register-launch", "register-stub.py"):
            shutil.copy(os.path.join(ROOT, "plugin", "bin", f), os.path.join(base, "plugin", "bin", f))
        if with_checkout_bundle:  # <plugin>/../../impl/ts/dist/register.mjs relative to plugin/bin
            os.makedirs(os.path.join(base, "impl", "ts", "dist"))
            shutil.copy(bundle, os.path.join(base, "impl", "ts", "dist", "register.mjs"))
        return os.path.join(base, "plugin", "bin", "register-launch")

    bare, checkout = tree("bare", False), tree("checkout", True)
    decoy_dir = os.path.join(tmp, "decoy")
    os.makedirs(decoy_dir)
    with open(os.path.join(decoy_dir, "register.mjs"), "w") as f:
        f.write('process.stderr.write("%s\\n");\nprocess.exit(1);\n' % DECOY_MARK)
    real_dir, other_dir = os.path.join(tmp, "installed"), os.path.join(tmp, "elsewhere")
    os.makedirs(real_dir)
    os.makedirs(other_dir)
    shutil.copy(bundle, os.path.join(real_dir, "register.mjs"))
    shutil.copy(bundle, os.path.join(other_dir, "register.mjs"))
    with_decoy = decoy_dir + os.pathsep + plain
    ts = {"REGISTER_IMPL": "ts"}
    missing = "register unavailable: binary not found"

    r = talk(ts, with_decoy, launch=bare)
    report(DECOY_MARK in r[3] and r[0] == "<no server reply>",
           "ts order control: a register.mjs on PATH is the one the launcher runs (the decoy ran)")
    report(is_real(talk(ts, real_dir + os.pathsep + plain, launch=bare)),
           "ts 3: a register.mjs on PATH is used when neither the override nor the checkout bundle exists")
    report(is_real(talk(dict(ts, REGISTER_TS_BUNDLE=os.path.join(other_dir, "register.mjs")), with_decoy, launch=bare)),
           "ts 1 beats 3: REGISTER_TS_BUNDLE wins over a (decoy) register.mjs on PATH")
    r = talk(dict(ts, REGISTER_TS_BUNDLE=os.path.join(tmp, "nonexistent", "register.mjs")), with_decoy, launch=checkout)
    report(is_stub(r, missing), "ts 1 does not fall through: a missing REGISTER_TS_BUNDLE serves the stub, not the checkout or PATH bundle")
    report(is_real(talk(dict(ts, REGISTER_TS_BUNDLE=os.path.join(other_dir, "register.mjs")), with_decoy, launch=checkout)),
           "ts 1 beats 2: REGISTER_TS_BUNDLE wins over the checkout bundle")
    report(is_real(talk(ts, with_decoy, launch=checkout)),
           "ts 2 beats 3: the checkout bundle wins over a (decoy) register.mjs on PATH")
    report(is_real(talk(dict(ts, REGISTER_TS_BUNDLE=""), real_dir + os.pathsep + plain, launch=bare)),
           "ts: an empty REGISTER_TS_BUNDLE counts as unset (falls to PATH)")
    report(is_stub(talk(ts, plain, launch=bare), missing), "ts: no override, no checkout bundle and no register.mjs on PATH serves the stub")


def main():
    static_checks()
    missing = "register unavailable: binary not found"
    # rs: needs register-rs on PATH (REGISTER_RS_DIR prepended)
    rs_dir = os.environ.get("REGISTER_RS_DIR")
    base = os.environ.get("PATH", "")
    path = (rs_dir + os.pathsep + base) if rs_dir else base
    if any(os.access(os.path.join(d, "register-rs"), os.X_OK) for d in path.split(os.pathsep) if d):
        report(is_real(talk({"REGISTER_IMPL": "rs"}, path)), "REGISTER_IMPL=rs reaches the real Rust server")
        report(is_real(talk({}, path)), "REGISTER_IMPL unset reaches the real Rust server")
    else:
        print("SKIP - rs positive check: register-rs not on PATH (set REGISTER_RS_DIR)")
        skips.append("rs")
    bundle = os.path.join(ROOT, "impl", "ts", "dist", "register.mjs")
    # no ambient register.mjs (an installed release) on the PATH of the ts checks
    clean = os.pathsep.join(d for d in path.split(os.pathsep) if d and not has_bundle(d))
    if os.path.isfile(bundle):
        report(is_real(talk({"REGISTER_IMPL": "ts"}, clean)), "REGISTER_IMPL=ts reaches the real TypeScript server")
        bundle_order_checks(bundle, clean)
    else:
        print("SKIP - ts positive check: %s not built" % bundle)
        skips.append("ts")
    report(is_stub(talk({"REGISTER_IMPL": "ts", "REGISTER_TS_BUNDLE": "/nonexistent/register.mjs"}, clean), missing),
           "REGISTER_IMPL=ts with the bundle missing serves the stub, not success")
    report(is_stub(talk({"REGISTER_IMPL": "off"}, path), "register off: REGISTER_IMPL=off"), "REGISTER_IMPL=off serves the stub")
    report(is_stub(talk({"REGISTER_IMPL": "bogus"}, path), "register off: unknown REGISTER_IMPL=bogus"),
           "an unknown REGISTER_IMPL serves the stub")
    py = os.path.dirname(subprocess.run(["sh", "-c", "command -v python3"], capture_output=True, text=True).stdout.strip())
    report(is_stub(talk({"REGISTER_IMPL": "rs"}, py + os.pathsep + "/usr/bin" + os.pathsep + "/bin"), missing),
           "the Rust binary absent from PATH serves the stub")
    print("# %d failed, %d skipped" % (len(fails), len(skips)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
