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


# ---- pinned expectations: what a reviewer approved. Changing the contract on purpose means changing these too.
EXPECTED_ERRORS = [  # (tools, precedence check, fragment of message_names); the table must hold exactly these rows
    ("begin_interview", 4, "both values"),
    ("begin_interview|log_item|resolve|park|reopen", 5, "unfinished journal: call resume or discard"),
    ("resolve|park|reopen", 8, "current status"),
    ("log_item", 7, "missing item: <id>"),
    ("resolve", 9, "the item id"),
    ("park", 9, "the item id"),
    ("resolve|park|log_item", 10, "hooks are not firing"),
    ("log_item", 7, "duplicate id: q1"),
    ("end_interview", 11, "the open item ids"),
    ("note_prompt|skill_started|check_stop", 2, "hook-facing"),
    ("end_interview", 6, "no interview to end"),
    ("end_interview", 6, "pending interview: use mode plain"),
    ("end_interview", 6, "mode plain applies only to a pending interview"),
    ("end_interview", 6, "nothing is open: abandon is not needed"),
    ("resolve|park|reopen", 7, "no such item: <id>"),
    ("resume|discard", 6, "no unfinished journal"),
    ("resume|discard", 6, "unknown interview id: <id>"),
    ("resume|discard", 6, "journal is live-owned: <id>"),
    ("resume|discard", 6, "several unfinished journals:"),
    ("resume|discard", 6, "wrong state:"),
    ("resume", 6, "journal already claimed"),
    ("resume", 6, "replay failed at line"),
    ("*", 3, "invalid arguments:"),
    ("log_item", 7, "subquestion needs a parent"),
    ("log_item", 9, "decision item can be answered by the user only: <id>"),
    ("resolve|log_item", 10, "user-answer budget spent:"),
    ("log_item", 10, "no user prompt since the"),
    ("log_item", 10, "volunteered-answer cap reached"),
]
MUTATING = {"log_item", "resolve", "park", "reopen"}
ENGAGES_PENDING = {"begin_interview", "log_item", "resolve", "park", "reopen"}
HOOK_FACING = {"note_prompt", "skill_started", "check_stop"}
ENUMS = {"kind": ["question", "subquestion", "decision"], "source": ["user", "model"],
         "status": ["Open", "Resolved", "Parked", "Stale"], "state": ["none", "pending", "active", "complete"]}
ITEM_STATUS_CONSTS = ["Open", "Stale", "Parked", "Resolved"]
STATUS_REQUIRED = {"server_version", "tool_schema", "journal_schema", "state", "interview_id", "items", "tree", "hook_health",
                   "last_event", "resumable", "version_mismatch", "decision_log", "write_error"}
EXAMPLE_FLOORS = {  # input_good, input_bad, result_good, result_bad
    "begin_interview": [1, 4, 3, 5], "log_item": [3, 11, 2, 3], "resolve": [1, 5, 1, 2], "park": [1, 3, 1, 2],
    "reopen": [1, 2, 1, 1], "status": [1, 1, 4, 11], "resume": [2, 2, 1, 2], "discard": [2, 3, 1, 1],
    "end_interview": [3, 8, 1, 3], "note_prompt": [2, 3, 1, 2], "skill_started": [1, 3, 1, 1], "check_stop": [2, 2, 2, 3]}


def check_pins(d, fails):
    tools = d["tools"]
    got = sorted((r["tools"], r["check"], r["message_names"]) for r in d["errors"])
    for tl, chk, frag in EXPECTED_ERRORS:
        rows = [r for r in d["errors"] if r["tools"] == tl and r["check"] == chk and frag in r["message_names"]]
        if len(rows) != 1:
            fails.append("errors table: expected exactly one row for tools %s, check %d, matched text %r, found %d" % (tl, chk, frag, len(rows)))
    if len(d["errors"]) != len(EXPECTED_ERRORS):
        fails.append("errors table has %d rows, the reviewed contract has %d" % (len(d["errors"]), len(EXPECTED_ERRORS)))
    for name, t in tools.items():
        if (name in MUTATING) != bool(t["mutating"]):
            fails.append("%s: mutating is %r, expected %r" % (name, t["mutating"], name in MUTATING))
        if (name in ENGAGES_PENDING) != bool(t["engages_pending"]):
            fails.append("%s: engages_pending is %r, expected %r" % (name, t["engages_pending"], name in ENGAGES_PENDING))
        if (name in HOOK_FACING) != (t["audience"] == "hook") or t["audience"] not in ("hook", "model"):
            fails.append("%s: audience %r contradicts the hook-facing set" % (name, t["audience"]))
        if not isinstance(t.get("description"), str) or len(t["description"].strip()) < 20:
            fails.append("%s: description missing or too short" % name)
        floors = EXAMPLE_FLOORS.get(name)
        ex = t["examples"]
        have = [len(ex[k]) for k in ("input_good", "input_bad", "result_good", "result_bad")]
        if floors is None or any(h < f for h, f in zip(have, floors)):
            fails.append("%s: example counts %s (input good/bad, result good/bad) fall below the reviewed floor %s" % (name, have, floors))
    for k, vals in ENUMS.items():
        if d["$defs"][k].get("enum") != vals:
            fails.append("$defs.%s enum is %s, expected %s" % (k, d["$defs"][k].get("enum"), vals))
    got_consts = [s["$ref"].split("/")[-1] for s in d["$defs"]["item"]["oneOf"]]
    consts = [d["$defs"][n]["properties"]["status"].get("const") for n in got_consts]
    if consts != ITEM_STATUS_CONSTS:
        fails.append("item oneOf status constants are %s, expected %s" % (consts, ITEM_STATUS_CONSTS))
    for n in got_consts:
        for fld in ("kind",):
            if d["$defs"][n]["properties"][fld] != {"$ref": "#/$defs/kind"}:
                fails.append("%s.%s does not reference $defs.kind" % (n, fld))
    if set(tools["status"]["result"]["required"]) != STATUS_REQUIRED or set(tools["status"]["result"]["properties"]) != STATUS_REQUIRED:
        fails.append("status result required/properties keys differ from the reviewed set")
    if tools["status"]["result"]["properties"]["journal_schema"] != {"const": d["journal_schema"]}:
        fails.append("status.journal_schema must be the const %r" % d["journal_schema"])
    if tools["status"]["result"]["properties"]["state"] != {"$ref": "#/$defs/state"}:
        fails.append("status.state must reference $defs.state")
    # tool_schema agreement: tools.json, runtime.md (heading and statement), CHANGES (an entry per value 1..N)
    ts = d["tool_schema"]
    rt = open(os.path.join(HERE, "runtime.md")).read()
    if not rt.startswith("# Run-time contract (tool_schema %d)\n" % ts):
        fails.append("runtime.md heading does not state tool_schema %d" % ts)
    if "`tool_schema` is **%d** (tools.json)" % ts not in rt:
        fails.append("runtime.md does not state `tool_schema` is **%d** (tools.json)" % ts)
    changes = open(os.path.join(HERE, "CHANGES")).read()
    entries = [int(n) for n in re.findall(r"^tool_schema (\d+)  \(", changes, re.M)]
    if entries != list(range(ts, 0, -1)):
        fails.append("CHANGES has log entries for tool_schema %s, expected %s (newest first, one per value)" % (entries, list(range(ts, 0, -1))))


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
        for field, prop in sch["properties"].items():
            if "enum" in prop and field not in envelope and field not in ("via", "prompt_count") and field not in md_enums(extra):
                fails.append("journal kind %s field %s: the schema has an enum but journal.md lists no values" % (kind, field))
    env_rows = re.findall(r"^\| `(\w+)` \| (\w+) \| (.*) \|$", md.split("## Event envelope")[1].split("## Event kinds")[0], re.M)
    if [r[0] for r in env_rows] != ["v", "ts", "seq", "kind"]:
        fails.append("journal.md envelope table rows are %s, expected v, ts, seq, kind" % [r[0] for r in env_rows])
    else:
        if not env_rows[0][2].startswith("`%d`." % d["journal_schema"]):
            fails.append("journal.md envelope row v does not state `%d`" % d["journal_schema"])
        if [r[1] for r in env_rows] != ["integer", "string", "integer", "string"]:
            fails.append("journal.md envelope field types differ from the schema (v, seq integer; ts, kind string)")
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
    if d["tool_schema"] != 4:
        fails.append("tool_schema is %r, expected 4" % d["tool_schema"])
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
        # Claude Code rejects a tools/list entry whose inputSchema lacks a top-level type object
        # (and with it every tool of the server), so a oneOf-only input is not allowed.
        if t["input"].get("type") != "object":
            fails.append('%s: input schema needs a top-level "type": "object"' % name)
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
    check_pins(d, fails)
    env_names = re.findall(r"^\| `(\w+)` \|", open(os.path.join(HERE, "runtime.md")).read().split("## Environment the suite injects")[1].split("##")[0], re.M)
    if env_names != ["CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID", "REGSPIKE_CLOCK_OFFSET_SECONDS"]:
        fails.append("runtime.md environment table lists %s" % env_names)
    jg, jb = check_journal(d, fails)
    print("tools: %d, good examples: %d, bad examples: %d, spike-3 names checked: %d, journal examples: %d good, %d bad"
          % (len(d["tools"]), n_good, n_bad, len(used), jg, jb))
    for f in fails:
        print("FAIL", f)
    print("OK" if not fails else "FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
