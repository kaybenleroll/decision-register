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


def pattern_py(p):
    """A trailing $ means end of string (ECMAScript and Rust semantics); Python's $ also matches before a final newline."""
    return p[:-1] + r"\Z" if p.endswith("$") else p


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
        if "pattern" in s and not re.search(pattern_py(s["pattern"]), v):
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
    if d["tool_schema"] != 3:
        fails.append("tool_schema is %r, expected 3" % d["tool_schema"])
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
    # status.items counts must equal the counts derived from tree
    for x in d["tools"]["status"]["examples"]["result_good"]:
        derived = {k.lower(): 0 for k in ("Open", "Resolved", "Parked", "Stale")}
        for node in x["tree"]:
            derived[node["status"].lower()] += 1
        if derived != x["items"]:
            fails.append("status items %s differ from counts derived from tree %s" % (x["items"], derived))
    # every (state x tool) cell of the REQ-27 table has a stated outcome; error precedence is numbered
    sm = d.get("state_matrix", {})
    for name in d["tools"]:
        for st in ("none", "pending", "active", "complete"):
            if not sm.get(name, {}).get(st):
                fails.append("state_matrix lacks %s in state %s" % (name, st))
    if len(d.get("error_precedence", [])) < 10:
        fails.append("error_precedence list missing or short")
    # tree order: Open, Stale, Parked, Resolved (creation order within a group is not visible in examples)
    rank = {"Open": 0, "Stale": 1, "Parked": 2, "Resolved": 3}
    for x in d["tools"]["status"]["examples"]["result_good"]:
        r = [rank[n["status"]] for n in x["tree"]]
        if r != sorted(r):
            fails.append("status tree not ordered Open, Stale, Parked, Resolved")
    # (c) tool names used by the spike-3 script are kept
    src = open(os.path.join(ROOT, "docs/evidence/spike3/acceptance.py")).read()
    used = set(re.findall(r'(?:\.ok|\.err|\.call)\("([a-z_]+)"', src))
    missing = used - set(d["tools"])
    if missing:
        fails.append("spike-3 tool names missing from contract: %s" % sorted(missing))
    # (b) the current tool_schema value is documented
    ts = d["tool_schema"]
    if not re.search(r"^tool_schema %d\b" % ts, open(os.path.join(ROOT, "contract/CHANGES")).read(), re.M):
        fails.append("contract/CHANGES has no 'tool_schema %d' entry" % ts)
    if "`tool_schema` is **%d**" % ts not in open(os.path.join(ROOT, "contract/runtime.md")).read():
        fails.append("contract/runtime.md does not state tool_schema **%d**" % ts)
    print("tools: %d, good examples: %d, bad examples: %d, spike-3 names checked: %d"
          % (len(d["tools"]), n_good, n_bad, len(used)))
    for f in fails:
        print("FAIL", f)
    print("OK" if not fails else "FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
