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


def md_fields(cell):
    """Field names of a journal.md 'Extra fields' cell: backticked tokens outside parentheses."""
    names, depth, i = [], 0, 0
    while i < len(cell):
        c = cell[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "`":
            j = cell.index("`", i + 1)
            if depth == 0:
                names.append(cell[i + 1:j])
            i = j
        i += 1
    return names


def md_enums(cell):
    """{field: set of values} for 'field (`a`, `b`)' patterns in a journal.md cell."""
    out = {}
    for m in re.finditer(r"`(\w+)` \(((?:`[\w]+`(?:, |\)))+)", cell):
        out[m.group(1)] = set(re.findall(r"`(\w+)`", m.group(2)))
    return out


def check_journal(d, fails):
    """contract/journal.schema.json against its own examples, journal.md's event table and tools.json."""
    js = json.load(open(os.path.join(HERE, "journal.schema.json")))
    md = open(os.path.join(HERE, "journal.md")).read()
    defs = dict(d["$defs"], **js["$defs"])
    for what, v in (("tools.json", d["journal_schema"]), ("journal.schema.json", js["journal_schema"]),
                    ("status result const", d["tools"]["status"]["result"]["properties"]["journal_schema"].get("const"))):
        if v != 2:
            fails.append("journal_schema in %s is %r, expected 2" % (what, v))
    m = re.match(r"# Journal contract \(journal_schema (\d+)\)", md)
    if not m or int(m.group(1)) != d["journal_schema"]:
        fails.append("journal.md heading does not state journal_schema %d" % d["journal_schema"])
    rows = re.findall(r"^\| `(\w+)` \| (.*?) \| (.*) \|$", md.split("## Event kinds")[1].split("## End kinds")[0], re.M)
    if [r[0] for r in rows] != list(js["kinds"]):
        fails.append("journal.schema.json kinds %s differ from journal.md event table %s" % (list(js["kinds"]), [r[0] for r in rows]))
    envelope = {"v", "ts", "seq", "kind"}
    for kind, extra, _ in rows:
        sch = js["kinds"].get(kind)
        if not sch:
            continue
        want = [] if extra.strip() == "none" else md_fields(extra)
        got = [k for k in sch["properties"] if k not in envelope]
        if sorted(want) != sorted(got):
            fails.append("journal kind %s: journal.md fields %s differ from journal.schema.json %s" % (kind, sorted(want), sorted(got)))
        if set(sch["required"]) != set(sch["properties"]) or sch.get("additionalProperties") is not False:
            fails.append("journal kind %s: every field must be required and additionalProperties false" % kind)
        if sch["properties"]["kind"] != {"const": kind} or sch["properties"]["v"] != {"const": 2}:
            fails.append("journal kind %s: envelope kind/v constants wrong" % kind)
        for field, vals in md_enums(extra).items():
            enum = sch["properties"].get(field, {}).get("enum")
            if enum is not None and set(enum) != vals:
                fails.append("journal kind %s field %s: journal.md values %s differ from schema enum %s" % (kind, field, sorted(vals), enum))
            if enum is None and field in sch["properties"] and field not in ("skill", "session_id"):
                fails.append("journal kind %s field %s: journal.md lists values but the schema has no enum" % (kind, field))
    for x in js["examples"]["good"]:
        k = x.get("kind")
        errs = check(x, js["kinds"][k], defs) if k in js["kinds"] else ["unknown kind"]
        if errs:
            fails.append("journal good example rejected %s: %s" % (json.dumps(x), errs))
    for x in js["examples"]["bad"]:
        k = x["event"].get("kind")
        if k in js["kinds"] and not check(x["event"], js["kinds"][k], defs):
            fails.append("journal bad example accepted (%s): %s" % (x["why"], json.dumps(x["event"])))
        if k not in js["kinds"] and x["why"] != "unknown kind":
            fails.append("journal bad example has an unknown kind but is not the unknown-kind case: %s" % x["why"])
    seen = {x["kind"] for x in js["examples"]["good"]}
    if seen != set(js["kinds"]):
        fails.append("journal good examples miss kinds %s" % sorted(set(js["kinds"]) - seen))
    end_enum = js["kinds"]["end"]["properties"]["end_kind"]["enum"]
    tools_end = d["tools"]["end_interview"]["result"]["properties"]["end_kind"]["enum"]
    if set(end_enum) - set(tools_end) != {"abandoned"} or set(tools_end) - set(end_enum):
        fails.append("end_kind enums: journal %s vs end_interview result %s (only abandoned may be journal-only)" % (end_enum, tools_end))
    return len(js["examples"]["good"]), len(js["examples"]["bad"])


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
    # errors table: every row names known tools and a precedence number that exists
    for r in d["errors"]:
        names = r["tools"].split("|")
        if names != ["*"] and not set(names) <= set(d["tools"]):
            fails.append("errors row names unknown tools: %s" % r["tools"])
        if not 1 <= r.get("check", 0) <= len(d["error_precedence"]):
            fails.append("errors row lacks a valid check number: %s" % r["tools"])
        if not r.get("message_names"):
            fails.append("errors row without message_names: %s" % r["tools"])
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
    jg, jb = check_journal(d, fails)
    print("tools: %d, good examples: %d, bad examples: %d, spike-3 names checked: %d, journal examples: %d good, %d bad"
          % (len(d["tools"]), n_good, n_bad, len(used), jg, jb))
    for f in fails:
        print("FAIL", f)
    print("OK" if not fails else "FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
