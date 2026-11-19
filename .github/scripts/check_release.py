#!/usr/bin/env python3
"""Release consistency check (slice R1, #32; REQ-77); Python standard library only; no network.

Run: python3 -I .github/scripts/check_release.py [--tag vX.Y.Z] [--rs PATH] [--ts PATH]

Asserts that the plugin, the Rust crate and the TypeScript package carry one version, equal to the tag when --tag is
given (otherwise equal to the plugin version), and that the tool_schema in contract/tools.json equals the one the plugin
hooks send. With --rs and/or --ts it also starts that built artefact, calls `status`, and checks that its server_version
and tool_schema equal the expected values (so the asset that would be uploaded is the one checked). Exit status 1 on any
failure.

Note on REQ-77 ("versioned separately"): the build plan's R1 acceptance asks for the plugin version and tool_schema to be
asserted equal across plugin and binary before release. This script enforces equal versions at release time only (one tag
is one repository state); ordinary CI does not call it with --tag.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
bad = []


def check(ok, what):
    print(("ok" if ok else "not ok") + " - " + what)
    if not ok:
        bad.append(what)


def jload(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return json.load(f)


def status_of(cmd):
    """Start the server, send initialize and one status call, close stdin; return the status result."""
    scratch = os.path.join(ROOT, ".scratch", "release-check")
    os.makedirs(os.path.join(scratch, "data"), exist_ok=True)
    os.makedirs(os.path.join(scratch, "project"), exist_ok=True)
    env = dict(os.environ, CLAUDE_PLUGIN_DATA=os.path.join(scratch, "data"), CLAUDE_PROJECT_DIR=os.path.join(scratch, "project"))
    for k in ("REGSPIKE_CLOCK_OFFSET_SECONDS", "CLAUDE_CODE_SESSION_ID"):
        env.pop(k, None)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05",
         "capabilities": {}, "clientInfo": {"name": "check_release", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "status", "arguments": {}}},
    ]
    p = subprocess.run(cmd, input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True,
                       env=env, cwd=scratch, timeout=30)
    replies = {}
    for line in p.stdout.splitlines():
        r = json.loads(line)
        replies[r["id"]] = r["result"]
    return json.loads(replies[2]["content"][0]["text"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag")
    ap.add_argument("--rs")
    ap.add_argument("--ts")
    a = ap.parse_args()

    plugin = jload("plugin/.claude-plugin/plugin.json")["version"]
    with open(os.path.join(ROOT, "impl/rust/Cargo.toml"), "rb") as f:
        cargo = tomllib.load(f)["package"]["version"]
    npm = jload("impl/ts/package.json")["version"]
    if a.tag is not None:
        check(re.fullmatch(r"v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", a.tag) is not None, "tag %r has the form vMAJOR.MINOR.PATCH" % a.tag)
        want = a.tag[1:]
    else:
        want = plugin
    check(plugin == want, "plugin.json version %s equals %s" % (plugin, want))
    check(cargo == want, "Cargo.toml version %s equals %s" % (cargo, want))
    check(npm == want, "package.json version %s equals %s" % (npm, want))

    schema = jload("contract/tools.json")["tool_schema"]
    hooks = jload("plugin/hooks/hooks.json")["hooks"]
    sent = {h["input"]["tool_schema"] for g in hooks.values() for x in g for h in x["hooks"] if "tool_schema" in h["input"]}
    check(sent == {schema}, "plugin hooks send tool_schema %s, contract is %s" % (sorted(sent), schema))

    for name, cmd in (("regspike-rs", [a.rs] if a.rs else None), ("regspike.mjs", ["node", a.ts] if a.ts else None)):
        if cmd is None:
            continue
        st = status_of([os.path.abspath(c) if os.path.exists(c) else c for c in cmd])
        check(st.get("server_version") == want, "%s reports server_version %r, expected %s" % (name, st.get("server_version"), want))
        check(st.get("tool_schema") == schema, "%s reports tool_schema %r, expected %s" % (name, st.get("tool_schema"), schema))

    print("# %d failed" % len(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
