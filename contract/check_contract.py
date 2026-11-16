#!/usr/bin/env python3
"""Check contract/tools.json: every schema accepts its good examples and rejects its bad ones.

Stdlib only (no jsonschema). Usage: python3 -I contract/check_contract.py
Exit status is non-zero on any failure.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def is_type(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, TYPES[t])


def check(v, s, defs, path="$"):
    """Return a list of error strings (empty when v satisfies s)."""
    if "$ref" in s:
        return check(v, defs[s["$ref"].split("/")[-1]], defs, path)
    e = []
    if "type" in s and not is_type(v, s["type"]):
        return ["%s: expected %s" % (path, s["type"])]
    if "const" in s and (v != s["const"] or type(v) is not type(s["const"])):
        e.append("%s: expected const %r" % (path, s["const"]))
    if "enum" in s and v not in s["enum"]:
        e.append("%s: not in enum" % path)
    if "oneOf" in s:
        n = sum(1 for sub in s["oneOf"] if not check(v, sub, defs, path))
        if n != 1:
            e.append("%s: matches %d of oneOf" % (path, n))
    if isinstance(v, str):
        if "minLength" in s and len(v) < s["minLength"]:
            e.append("%s: too short" % path)
        if "pattern" in s and not re.search(s["pattern"], v):
            e.append("%s: pattern" % path)
    if isinstance(v, int) and not isinstance(v, bool) and "minimum" in s and v < s["minimum"]:
        e.append("%s: below minimum" % path)
    if isinstance(v, list) and "items" in s:
        for i, x in enumerate(v):
            e += check(x, s["items"], defs, "%s[%d]" % (path, i))
    if isinstance(v, dict):
        props = s.get("properties", {})
        for k in s.get("required", []):
            if k not in v:
                e.append("%s: missing %s" % (path, k))
        if "maxProperties" in s and len(v) > s["maxProperties"]:
            e.append("%s: too many properties" % path)
        for k, req in s.get("dependentRequired", {}).items():
            if k in v:
                e += ["%s: %s needs %s" % (path, k, r) for r in req if r not in v]
        for k, x in v.items():
            if k in props:
                e += check(x, props[k], defs, path + "." + k)
            elif s.get("additionalProperties") is False:
                e.append("%s: unexpected %s" % (path, k))
    return e


def main():
    d = json.load(open(os.path.join(HERE, "tools.json")))
    defs = d["$defs"]
    fails, n_good, n_bad = [], 0, 0
    if d["tool_schema"] != 1:
        fails.append("tool_schema is %r, expected 1" % d["tool_schema"])
    expected = {"begin_interview", "log_item", "resolve", "park", "reopen", "status", "resume",
                "discard", "end_interview", "note_prompt", "skill_started", "check_stop"}
    if set(d["tools"]) != expected:
        fails.append("tool list differs from REQ-40: %s" % sorted(set(d["tools"]) ^ expected))
    for name, t in d["tools"].items():
        ex = t["examples"]
        if not (ex["input_good"] and ex["input_bad"] and ex["result_good"] and ex["result_bad"]):
            fails.append("%s: needs at least one good and one bad example for input and result" % name)
        for kind, schema in (("input", t["input"]), ("result", t["result"])):
            for x in ex[kind + "_good"]:
                n_good += 1
                errs = check(x, schema, defs)
                if errs:
                    fails.append("%s %s good example rejected %s: %s" % (name, kind, json.dumps(x), errs))
            for x in ex[kind + "_bad"]:
                n_bad += 1
                if not check(x, schema, defs):
                    fails.append("%s %s bad example accepted %s" % (name, kind, json.dumps(x)))
        if (t["audience"] == "hook") != t["rejects_meta"]:
            fails.append("%s: rejects_meta must be true exactly for hook-facing tools" % name)
    # (c) tool names used by the spike-3 script are kept
    src = open(os.path.join(ROOT, "docs/evidence/spike3/acceptance.py")).read()
    used = set(re.findall(r'(?:\.ok|\.err|\.call)\("([a-z_]+)"', src))
    missing = used - set(d["tools"])
    if missing:
        fails.append("spike-3 tool names missing from contract: %s" % sorted(missing))
    # (b) tool_schema 1 is documented
    for f in ("contract/CHANGES", "contract/runtime.md"):
        if "tool_schema" not in open(os.path.join(ROOT, f)).read():
            fails.append("%s does not mention tool_schema" % f)
    print("tools: %d, good examples: %d, bad examples: %d, spike-3 names checked: %d"
          % (len(d["tools"]), n_good, n_bad, len(used)))
    for f in fails:
        print("FAIL", f)
    print("OK" if not fails else "FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
