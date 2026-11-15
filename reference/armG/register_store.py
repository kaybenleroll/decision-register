"""Arm G register store, rendering and the shared finish-attempt predicate (stdlib only).

Used by the MCP server (register_server.py), the three hooks (hook_register_*.py) and the driver
(run_arm.py). Spec: notes/seq-study-arm-g-spec-20261114.md sections 2 to 4.

Files under $SEQ_STATE_DIR:
  register.jsonl   append-only event log, fsync after each write (the source of truth)
  register.lock    persistent sidecar lock (never replaced)
  register.json    derived state, tmp + os.replace, rebuildable from the log
  gate.json        hook counters; gate.lock is its persistent sidecar lock
  hook-log.jsonl   hook diagnostics (written by hook_common.log)
"""
import contextlib
import fcntl
import json
import os
import re
import time

STATUSES = ("OPEN", "RESOLVED", "PARKED", "STALE")
UNRESOLVED = ("OPEN", "STALE")
ID_PATTERN = r"^Q[0-9]+$"
BRANCH_PATTERN = r"^[a-z0-9][a-z0-9.-]{0,39}$"
CHAIN_CAP = 2
RUN_CAP_UNLOGGED = 12
RUN_CAP_FINISH = 8
INJECT_CAP = 2500
SESSIONSTART_CAP = 12000
LIST_OPEN_CAP = 6000
LIST_OPEN_RESOLVED_CAP = 24000
TEXT_FLOOR = 60
LOCK_TIMEOUT_HOOK = 2.0
LOCK_TIMEOUT_SERVER = 10.0
CONTINUE_PREFIX = "Thanks. Please continue with the interview."
GATE_SKIP_TAGS = ("-status", "-compact", "-final", "-postdone-")


# ---------------------------------------------------------------- locks and files

class LockTimeout(Exception):
    pass


@contextlib.contextmanager
def locked(path, timeout=LOCK_TIMEOUT_SERVER):
    """flock on a persistent sidecar file (never replaced, per the os.replace hazard)."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    raise LockTimeout(path)
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)        # closing the descriptor releases the lock


def _atomic_write_json(path, obj):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def paths(state_dir):
    j = lambda n: os.path.join(state_dir, n)  # noqa: E731
    return dict(log=j("register.jsonl"), lock=j("register.lock"), derived=j("register.json"),
                gate=j("gate.json"), gate_lock=j("gate.lock"), hooklog=j("hook-log.jsonl"))


def read_events(state_dir):
    out = []
    try:
        with open(paths(state_dir)["log"], encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue            # a torn trailing line is ignored
    except FileNotFoundError:
        pass
    return out


def _append_event(state_dir, ev):
    with open(paths(state_dir)["log"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ---------------------------------------------------------------- gate counters (gate.json)

GATE_DEFAULTS = dict(user_prompts=0, user_counter_excluded=0, nudge_prompts=0, prompt_seq=0, compactions=0,
                     stop_firings=0, chain_finish=0, chain_unlogged=0, run_finish=0, run_unlogged=0,
                     chain_cap_hit_finish=0, chain_cap_hit_unlogged=0, run_cap_hit_finish=0, run_cap_hit_unlogged=0,
                     payload_missing=0, source_user_rejected=0, extra_q_marks=0, counter_lost=0, lock_timeouts=0)


def read_gate(state_dir):
    g = dict(GATE_DEFAULTS)
    try:
        with open(paths(state_dir)["gate"]) as fh:
            g.update(json.load(fh))
    except Exception:  # noqa: BLE001
        pass
    return g


def gate_update(state_dir, fn, timeout=LOCK_TIMEOUT_HOOK):
    """Run fn(counters) under gate.lock and write the result atomically. Returns fn's value. May raise LockTimeout."""
    p = paths(state_dir)
    os.makedirs(state_dir, exist_ok=True)
    with locked(p["gate_lock"], timeout):
        g = read_gate(state_dir)
        val = fn(g)
        _atomic_write_json(p["gate"], g)
    return val


# ---------------------------------------------------------------- state and replay

def empty_state():
    return {"entries": {}, "branches": {}, "next_id": 1, "events": 0}


def _new_entry(state, ev, text, branch, deps, kind, status, decision=None, source=None):
    qid = "Q%d" % state["next_id"]
    state["next_id"] += 1
    state["entries"][qid] = dict(
        id=qid, text=text, branch=branch or "main", deps=list(deps or []), kind=kind, status=status,
        decision=decision, source=source, previous=None, reason=None, owner=None, why=None, new_value=None,
        logged_up=ev.get("user_prompts", 0), logged_nudge=ev.get("nudge_prompts", 0), prompt_seq=ev.get("prompt_seq", 0),
        n=int(qid[1:]))
    return qid


def dependants(state, root):
    """Entries that list `root` in deps, transitively (the root itself excluded)."""
    seen, stack = set(), [root]
    while stack:
        cur = stack.pop()
        for qid, e in state["entries"].items():
            if cur in e["deps"] and qid not in seen and qid != root:
                seen.add(qid)
                stack.append(qid)
    return seen


def apply_event(state, ev):
    """Pure, deterministic. Does not validate (the server validates before it appends)."""
    op, a = ev["op"], ev.get("args") or {}
    if op == "list_open":           # a read, logged only so the call can be counted
        return state
    state["events"] += 1
    if op == "log_question":
        if a.get("kind") == "constraint":
            _new_entry(state, ev, a["text"], a.get("branch"), a.get("deps"), "constraint", "RESOLVED", a.get("decision"), "user")
        else:
            _new_entry(state, ev, a["text"], a.get("branch"), a.get("deps"), "question", "OPEN")
    elif op == "resolve":
        e = state["entries"][a["id"]]
        e.update(status="RESOLVED", decision=a["decision"], source=a["source"], reason=None, owner=None)
    elif op == "park":
        e = state["entries"][a["id"]]
        e.update(status="PARKED", reason=a["reason"], owner=a.get("owner"))
    elif op == "reopen":
        e = state["entries"][a["id"]]
        e["previous"] = e.get("decision") or e.get("previous")
        e.update(status="OPEN", decision=None, source=None, why=a["why"], new_value=a.get("new_value"), reason=None, owner=None)
        targets = dependants(state, a["id"]) | set(a.get("affects") or [])
        targets.discard(a["id"])
        for t in sorted(targets, key=lambda x: int(x[1:])):
            if state["entries"][t]["status"] in ("RESOLVED", "PARKED"):
                state["entries"][t]["status"] = "STALE"
    elif op == "open_branch":
        parent = state["entries"][a["from_id"]]["branch"]
        state["branches"][a["branch"]] = dict(from_id=a["from_id"], topic=a["topic"], parent=parent)
        for sq in a["subquestions"]:
            _new_entry(state, ev, sq, a["branch"], [a["from_id"]], "question", "OPEN")
    # list_open (read) and unknown ops change nothing
    return state


def replay(events):
    st = empty_state()
    for ev in events:
        apply_event(st, ev)
    return st


def load(state_dir):
    """Current state from the event log (the source of truth)."""
    return replay(read_events(state_dir))


def counts(state):
    c = {s: 0 for s in STATUSES}
    for e in state["entries"].values():
        c[e["status"]] += 1
    return c


def ids_with(state, *statuses):
    return [q for q, e in state["entries"].items() if e["status"] in statuses]


def unresolved_ids(state):
    return ids_with(state, *UNRESOLVED)


# ---------------------------------------------------------------- tool schemas and validation

def _s(**kw):
    return kw


TOOL_SCHEMAS = {
    "log_question": dict(
        description="Enter a question in the register before asking it. Returns the id to put at the start of the question. "
                    "With kind=constraint, enter a fact or value the user stated, with its decision.",
        inputSchema={"type": "object", "properties": {
            "text": {"type": "string", "minLength": 3, "maxLength": 400},
            "branch": {"type": "string", "pattern": BRANCH_PATTERN, "description": "A name created by open_branch. Omit for the main line."},
            "deps": {"type": "array", "items": {"type": "string", "pattern": ID_PATTERN}, "maxItems": 12,
                     "description": "Ids of earlier entries this one relies on."},
            "kind": {"type": "string", "enum": ["question", "constraint"], "default": "question"},
            "decision": {"type": "string", "minLength": 1, "maxLength": 400,
                         "description": "Only with kind=constraint: the value the user stated. The entry is created resolved."}},
            "required": ["text"], "additionalProperties": False}),
    "resolve": dict(
        description="Mark an entry resolved with a one-line decision.",
        inputSchema={"type": "object", "properties": {
            "id": {"type": "string", "pattern": ID_PATTERN},
            "decision": {"type": "string", "minLength": 1, "maxLength": 400},
            "source": {"type": "string", "enum": ["user", "recommended", "explored"],
                       "description": "user: the user answered. recommended: decided under the recommend-don't-defer step. explored: answered from the codebase."}},
            "required": ["id", "decision", "source"], "additionalProperties": False}),
    "park": dict(
        description="Mark an entry parked, with the reason and who or what it is waiting on.",
        inputSchema={"type": "object", "properties": {
            "id": {"type": "string", "pattern": ID_PATTERN},
            "reason": {"type": "string", "minLength": 3, "maxLength": 300},
            "owner": {"type": "string", "maxLength": 80, "description": "Who or what the entry is waiting on."}},
            "required": ["id", "reason"], "additionalProperties": False}),
    "reopen": dict(
        description="Reopen an entry the user changed. Entries that listed it in deps (and any in affects) become stale.",
        inputSchema={"type": "object", "properties": {
            "id": {"type": "string", "pattern": ID_PATTERN},
            "why": {"type": "string", "minLength": 3, "maxLength": 300, "description": "What the user changed."},
            "new_value": {"type": "string", "maxLength": 400, "description": "The changed value, if already stated."},
            "affects": {"type": "array", "items": {"type": "string", "pattern": ID_PATTERN}, "maxItems": 20,
                        "description": "Other entries that relied on the old value, beyond those that listed it in deps."}},
            "required": ["id", "why"], "additionalProperties": False}),
    "open_branch": dict(
        description="Open a branch of sub-questions that exist only because of one answer; logs each sub-question as an open entry.",
        inputSchema={"type": "object", "properties": {
            "from_id": {"type": "string", "pattern": ID_PATTERN,
                        "description": "The entry whose answer opens the branch. May be an entry inside another branch (nesting)."},
            "branch": {"type": "string", "pattern": BRANCH_PATTERN},
            "topic": {"type": "string", "minLength": 3, "maxLength": 200},
            "subquestions": {"type": "array", "items": {"type": "string", "minLength": 3, "maxLength": 400}, "minItems": 1, "maxItems": 8,
                             "description": "The questions needed to fix the branch's values completely."}},
            "required": ["from_id", "branch", "topic", "subquestions"], "additionalProperties": False}),
    "list_open": dict(
        description="Show the register as an indented tree: open and stale entries, parked entries, counts. "
                    "With include_resolved also the decisions.",
        inputSchema={"type": "object", "properties": {
            "include_resolved": {"type": "boolean", "default": False},
            "branch": {"type": "string", "pattern": BRANCH_PATTERN}},
            "additionalProperties": False}),
}


def _typename(v):
    return type(v).__name__


def validate(schema, value, path="argument"):
    """Minimal JSON-schema subset used by the six tools. Returns an error message naming the fix, or None."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return "%s must be an object" % path
        props = schema.get("properties", {})
        for k in value:
            if k not in props:
                return "unknown argument '%s'; allowed: %s" % (k, ", ".join(sorted(props)))
        for k in schema.get("required", []):
            if k not in value:
                return "missing required argument '%s'" % k
        for k, v in value.items():
            err = validate(props[k], v, "'%s'" % k)
            if err:
                return err
        return None
    if t == "string":
        if not isinstance(value, str):
            return "%s must be a string, got %s" % (path, _typename(value))
        if "minLength" in schema and len(value) < schema["minLength"]:
            return "%s must be at least %d characters" % (path, schema["minLength"])
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return "%s must be at most %d characters (got %d); shorten it" % (path, schema["maxLength"], len(value))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            return "%s must match %s (got %r)" % (path, schema["pattern"], value[:60])
        if "enum" in schema and value not in schema["enum"]:
            return "%s must be one of %s (got %r)" % (path, ", ".join(schema["enum"]), value[:40])
        return None
    if t == "array":
        if not isinstance(value, list):
            return "%s must be an array" % path
        if "minItems" in schema and len(value) < schema["minItems"]:
            return "%s needs at least %d item(s)" % (path, schema["minItems"])
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return "%s allows at most %d items (got %d)" % (path, schema["maxItems"], len(value))
        for i, v in enumerate(value):
            err = validate(schema["items"], v, "%s[%d]" % (path, i))
            if err:
                return err
        return None
    if t == "boolean":
        return None if isinstance(value, bool) else "%s must be true or false" % path
    return None


def check_semantics(state, tool, a, gate):
    """Per-tool checks against the current state. Returns an error message naming the fix, or None."""
    E, B = state["entries"], state["branches"]

    def unknown(i):
        return "unknown id %s; known ids: %s" % (i, ", ".join(sorted(E, key=lambda x: int(x[1:]))[:30]) or "none yet")
    if tool == "log_question":
        if a.get("branch") and a["branch"] not in B:
            return "unknown branch '%s'; create it with open_branch first (known: %s)" % (a["branch"], ", ".join(sorted(B)) or "none")
        for d in a.get("deps") or []:
            if d not in E:
                return "unknown id %s in deps; " % d + unknown(d).split("; ", 1)[1]
        if a.get("decision") and a.get("kind", "question") != "constraint":
            return "'decision' is only allowed with kind=constraint; to record an answer call resolve"
        if a.get("kind") == "constraint" and not a.get("decision"):
            return "kind=constraint needs 'decision' (the value the user stated)"
    elif tool == "resolve":
        e = E.get(a["id"])
        if not e:
            return unknown(a["id"])
        if e["status"] == "RESOLVED":
            return "%s is already resolved; use reopen if the user changed it" % a["id"]
        if a["source"] == "user" and gate.get("user_prompts", 0) <= e.get("logged_up", 0):
            return "no user answer has arrived since %s was logged; use source recommended or explored, or park it" % a["id"]
    elif tool == "park":
        e = E.get(a["id"])
        if not e:
            return unknown(a["id"])
        if e["status"] in ("RESOLVED", "PARKED"):
            return "%s is %s; only open or stale entries can be parked" % (a["id"], e["status"].lower())
    elif tool == "reopen":
        e = E.get(a["id"])
        if not e:
            return unknown(a["id"])
        if e["status"] == "OPEN":
            return "%s is already open" % a["id"]
        for x in a.get("affects") or []:
            if x not in E:
                return "unknown id %s in affects; " % x + unknown(x).split("; ", 1)[1]
    elif tool == "open_branch":
        if a["from_id"] not in E:
            return unknown(a["from_id"])
        if a["branch"] == "main" or a["branch"] in B:
            return "branch name '%s' is taken; choose another name" % a["branch"]
    elif tool == "list_open":
        if a.get("branch") and a["branch"] not in B:
            return "unknown branch '%s' (known: %s)" % (a["branch"], ", ".join(sorted(B)) or "none")
    return None


# ---------------------------------------------------------------- rendering with longest-first truncation

def _clip(text, n):
    return text if len(text) <= n else text[:n] + "…"


def fit_cur(render, texts, cap, floor=TEXT_FLOOR, drop_first=()):
    """render(cur) -> str, where cur maps key -> text. Returns the text mapping that fits `cap`: first blank the
    `drop_first` keys, then repeatedly cut the LONGEST text to max(floor, half its length) (never oldest-first), then
    ids only (all texts blank) as the last resort."""
    cur = dict(texts)
    if len(render(cur)) <= cap:
        return cur
    for k in drop_first:
        if k in cur:
            cur[k] = ""
    if len(render(cur)) <= cap:
        return cur
    base = dict(cur)
    cut = {k: len(v) for k, v in base.items()}
    while True:
        cand = [k for k in cur if cut[k] > floor]
        if not cand:
            break
        k = max(cand, key=lambda x: (len(cur[x]), x))
        cut[k] = max(floor, cut[k] // 2)
        cur[k] = _clip(base[k], cut[k])
        if len(render(cur)) <= cap:
            return cur
    return {k: "" for k in cur}


def fit(render, texts, cap, floor=TEXT_FLOOR, drop_first=()):
    return render(fit_cur(render, texts, cap, floor, drop_first))


def _where(state, e):
    if e["branch"] == "main":
        return "main"
    b = state["branches"].get(e["branch"]) or {}
    return "%s, from %s" % (e["branch"], b.get("from_id")) if b.get("from_id") else e["branch"]


def render_block(state, with_decisions=False, cap=None):
    """The <register> block (UserPromptSubmit; with decisions: SessionStart[compact]). '' when the store is empty.
    Overflow: parked reasons go first, then the longest text is cut (down to 60 characters), ids only as the last
    resort; open and stale ids are never dropped. With decisions the open/stale/parked part keeps its own 2500 cap
    and the decision values share what is left of 12000."""
    E = state["entries"]
    if not E:
        return ""
    opn = [e for e in E.values() if e["status"] == "OPEN"]
    stale = [e for e in E.values() if e["status"] == "STALE"]
    parked = [e for e in E.values() if e["status"] == "PARKED"]
    resolved = [e for e in E.values() if e["status"] == "RESOLVED"]
    texts = {}
    for e in opn + stale:
        texts[("t", e["id"])] = e["text"]
    for e in parked:
        texts[("r", e["id"])] = e.get("reason") or ""

    def line(e, cur):
        t = cur.get(("t", e["id"]), "")
        return "%s [%s]%s" % (e["id"], _where(state, e), (" " + t) if t else "")

    def render(cur, decisions):
        out = ["<register>", "Open: " + ("; ".join(line(e, cur) for e in opn) if opn else "none")]
        if stale:
            out.append("Stale (relied on an entry that was reopened): " + "; ".join(line(e, cur) for e in stale))
        if parked:
            out.append("Parked: " + "; ".join(
                "%s (%s)" % (e["id"], cur[("r", e["id"])]) if cur.get(("r", e["id"])) else e["id"] for e in parked))
        out.append("Resolved: %d. Next id: Q%d." % (len(resolved), state["next_id"]))
        if decisions:
            out.append("Decisions so far:")
            for e in resolved:
                out.append("%s: %s" % (e["id"], cur.get(("d", e["id"]), "")))
        out.append("</register>")
        return "\n".join(out)
    drop = [k for k in texts if k[0] == "r"]
    if not with_decisions:
        return fit(lambda cur: render(cur, False), texts, cap or INJECT_CAP, drop_first=drop)
    head_cur = fit_cur(lambda cur: render(cur, False), texts, INJECT_CAP, drop_first=drop)
    dtexts = {("d", e["id"]): e.get("decision") or "" for e in resolved}
    return fit(lambda cur: render({**head_cur, **cur}, True), dtexts, cap or SESSIONSTART_CAP)


def render_list_open(state, include_resolved=False, branch=None):
    """Indented tree text for the list_open call. Cap 6000 (24000 with include_resolved), longest text cut first;
    open and stale ids are never dropped."""
    E, B = state["entries"], state["branches"]
    cap = LIST_OPEN_RESOLVED_CAP if include_resolved else LIST_OPEN_CAP
    c = counts(state)
    texts = {}
    for e in E.values():
        texts[("t", e["id"])] = e["text"]
        texts[("d", e["id"])] = e.get("decision") or ""
        texts[("r", e["id"])] = e.get("reason") or ""
        texts[("p", e["id"])] = e.get("previous") or ""
        texts[("w", e["id"])] = e.get("why") or ""

    def shown(e):
        return include_resolved or e["status"] != "RESOLVED"

    def in_subtree(name):       # branch filter: the named branch and everything nested under it
        cur = name
        while cur and cur != "main":
            if cur == branch:
                return True
            cur = (B.get(cur) or {}).get("parent")
        return False

    def entry_line(e, cur, ind):
        s = "%s%s %s %s" % ("  " * ind, e["id"], e["status"], cur[("t", e["id"])])
        if e["deps"]:
            s += " (deps: %s)" % ",".join(e["deps"])
        if e["status"] == "PARKED":
            s += " -- parked: %s%s" % (cur[("r", e["id"])], (" (%s)" % e["owner"]) if e.get("owner") else "")
        if e["status"] == "RESOLVED":
            s += " => %s [%s]" % (cur[("d", e["id"])], e.get("source") or "")
        if e.get("previous") and (include_resolved or e["status"] in UNRESOLVED):
            s += " (was: %s)" % cur[("p", e["id"])]
        if e["status"] in UNRESOLVED and e.get("why"):
            s += " (reopened: %s)" % cur[("w", e["id"])]
        return s

    def render(cur):
        out = ["Register: open=%d stale=%d parked=%d resolved=%d. Next id: Q%d." % (
            c["OPEN"], c["STALE"], c["PARKED"], c["RESOLVED"], state["next_id"])]

        def scope(name, ind):
            for e in E.values():
                if e["branch"] != name:
                    continue
                if shown(e):
                    out.append(entry_line(e, cur, ind))
                for bn, b in B.items():
                    if b["from_id"] == e["id"]:
                        if branch and not (bn == branch or in_subtree(bn)):
                            continue
                        out.append("%sbranch %s (from %s): %s" % ("  " * (ind + 1), bn, b["from_id"], b["topic"]))
                        scope(bn, ind + 2)
        if branch:
            b = B[branch]
            out.append("branch %s (from %s): %s" % (branch, b["from_id"], b["topic"]))
            scope(branch, 1)
        else:
            out.append("main:")
            scope("main", 1)
        return "\n".join(out)
    return fit(render, texts, cap, drop_first=[k for k in texts if k[0] in ("p", "w")])


def status_line(state, head, extra=None):
    c = counts(state)
    s = "ok %s | open=%d stale=%d parked=%d resolved=%d | open: %s" % (
        head, c["OPEN"], c["STALE"], c["PARKED"], c["RESOLVED"], ",".join(ids_with(state, "OPEN")) or "none")
    st = ids_with(state, "STALE")
    if st:
        s += " | stale: " + ",".join(st)
    return s + (" | " + extra if extra else "")


# ---------------------------------------------------------------- tool execution (shared by server and tests)

def call_tool(state_dir, tool, args):
    """Run one tool call durably. Returns (is_error, text). Validation problems are returned, not raised."""
    if tool not in TOOL_SCHEMAS:
        return True, "unknown tool '%s'" % tool
    err = validate(TOOL_SCHEMAS[tool]["inputSchema"], args if args is not None else {})
    if err:
        return True, "%s: %s" % (tool, err)
    args = dict(args or {})
    os.makedirs(state_dir, exist_ok=True)
    try:
        with locked(paths(state_dir)["lock"], LOCK_TIMEOUT_SERVER):
            return _call_locked(state_dir, tool, args)
    except LockTimeout:
        return True, "register is busy; retry the call"


def _call_locked(state_dir, tool, args):
    p = paths(state_dir)
    state = load(state_dir)
    gate = read_gate(state_dir)
    err = check_semantics(state, tool, args, gate)
    if err:
        if tool == "resolve" and args.get("source") == "user" and err.startswith("no user answer"):
            try:
                gate_update(state_dir, lambda g: g.__setitem__("source_user_rejected", g.get("source_user_rejected", 0) + 1))
            except Exception:  # noqa: BLE001
                pass
        return True, "%s: %s" % (tool, err)
    ev = dict(ts=time.time(), op=tool, args=args, prompt_seq=gate["prompt_seq"], user_prompts=gate["user_prompts"],
              nudge_prompts=gate["nudge_prompts"], compactions=gate["compactions"])
    if tool == "list_open":
        text = render_list_open(state, bool(args.get("include_resolved")), args.get("branch"))
        ev["chars"] = len(text)
        _append_event(state_dir, ev)            # a read; logged only so the call can be counted
        return False, text
    before_stale = set(ids_with(state, "STALE"))
    first_new = state["next_id"]
    if tool == "resolve":
        e = state["entries"][args["id"]]
        ev["since_logged_prompts"] = gate["user_prompts"] - e.get("logged_up", 0)
        ev["since_logged_nudges"] = gate["nudge_prompts"] - e.get("logged_nudge", 0)
    apply_event(state, ev)
    new_ids = ["Q%d" % i for i in range(first_new, state["next_id"])]
    newly_stale = sorted(set(ids_with(state, "STALE")) - before_stale, key=lambda x: int(x[1:]))
    ev["result"] = dict(ids=new_ids, stale=newly_stale)
    _append_event(state_dir, ev)
    _atomic_write_json(p["derived"], state)
    if tool == "log_question":
        head = "id=%s" % new_ids[0]
    elif tool == "open_branch":
        head = "branch=%s created=%s" % (args["branch"], ",".join(new_ids))
    elif tool == "reopen":
        head = "id=%s reopened" % args["id"]
    else:
        head = "id=%s" % args["id"]
    extra = "stale: none" if tool == "reopen" and not ids_with(state, "STALE") else None
    return False, status_line(state, head, extra)


# ---------------------------------------------------------------- ledger parser (G-lite)

_LEDGER_ENTRY = re.compile(r"^\s*-\s*\[([ xXpP!])\]\s*(.*)$")
_LEDGER_BRANCH = re.compile(r"^##\s+branch\s+([A-Za-z0-9._-]+)(?:\s*\(from\s+(Q\d+)\))?", re.I)
_MARK = {" ": "OPEN", "x": "RESOLVED", "p": "PARKED", "!": "STALE"}


def parse_ledger(text):
    """G-lite ledger -> (state-shaped dict, malformed count). Duplicate ids: last wins and counts as malformed;
    an entry line with no Q<n> id counts as malformed."""
    st = empty_state()
    malformed = 0
    branch = "main"
    seen = set()
    for raw in (text or "").splitlines():
        m = _LEDGER_BRANCH.match(raw)
        if m:
            branch = m.group(1)
            st["branches"][branch] = dict(from_id=m.group(2) or "", topic="", parent="main")
            continue
        if raw.startswith("## "):
            branch = "main" if raw.lower().startswith("## main") else branch
            continue
        m = _LEDGER_ENTRY.match(raw)
        if not m:
            continue
        mark, rest = m.group(1).lower(), m.group(2).strip()
        idm = re.match(r"\**`?Q(\d+)\b`?\**:?\s*(.*)$", rest)
        if not idm:
            malformed += 1
            continue
        qid = "Q" + idm.group(1)
        body = idm.group(2)
        if qid in seen:
            malformed += 1
        seen.add(qid)
        txt, _, tail = body.partition("=>")
        deps = re.findall(r"Q\d+", (re.search(r"\(deps?:([^)]*)\)", txt) or [None, ""])[1])
        txt = re.sub(r"\s*\(deps?:[^)]*\)", "", txt).strip()
        e = dict(id=qid, text=txt, branch=branch, deps=deps, kind="question", status=_MARK.get(mark, "OPEN"),
                 decision=None, source=None, previous=None, reason=None, owner=None, why=None, new_value=None,
                 logged_up=0, logged_nudge=0, prompt_seq=0, n=int(idm.group(1)))
        tail = tail.strip()
        if e["status"] == "RESOLVED":
            e["decision"] = tail
        elif e["status"] == "PARKED":
            e["reason"] = re.sub(r"^parked:\s*", "", tail, flags=re.I)
        elif e["status"] == "STALE":
            e["why"] = tail
        st["entries"][qid] = e
    st["next_id"] = (max([e["n"] for e in st["entries"].values()] or [0])) + 1
    return st, malformed


def load_ledger(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return parse_ledger(fh.read())
    except FileNotFoundError:
        return empty_state(), 0


# ---------------------------------------------------------------- the shared finish-attempt predicate

FENCE = re.compile(r"```.*?```", re.S)
INLINE = re.compile(r"`[^`\n]*`")
URL = re.compile(r"https?://\S+")
ANY_ID = re.compile(r"(?<![A-Za-z0-9_])[*`]{0,2}Q(\d+)(?::[*`]{0,2}|[*`]{1,2}:)")
LINE_ID = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*[*`]{0,2}Q(\d+)[*`]{0,2}[:.)]", re.M)
LINE_ID1 = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*[*`]{0,2}Q(\d+)[*`]{0,2}[:.)]")


def strip_code_urls(t):
    return URL.sub("", INLINE.sub("", FENCE.sub("", t or "")))


def extract_ids(t):
    """Q<n> labels from the UNSTRIPPED message: wrapped `Q7:` / **Q7:** / **Q7**: anywhere, or line-initial."""
    t = t or ""
    return {int(m) for m in ANY_ID.findall(t)} | {int(m) for m in LINE_ID.findall(t)}


def status_map(entries):
    """{int id -> status string} from a state dict, an entries dict keyed 'Q4', or a plain {4: 'OPEN'} map."""
    if isinstance(entries, dict) and isinstance(entries.get("entries"), dict):
        entries = entries["entries"]
    out = {}
    for k, v in (entries or {}).items():
        n = int(str(k).lstrip("Qq"))
        out[n] = v if isinstance(v, str) else v.get("status")
    return out


def _open_ids(entries):
    return {i for i, s in status_map(entries).items() if s in UNRESOLVED}


def single_label_request(t, open_ids):
    """One non-blank line (fences and URLs removed, inline code kept) that starts with a label of an OPEN/STALE id."""
    lines = [ln for ln in URL.sub("", FENCE.sub("", t or "")).splitlines() if ln.strip()]
    if len(lines) != 1:
        return False
    m = LINE_ID1.match(lines[0])
    return bool(m) and int(m.group(1)) in open_ids


def is_finish_attempt(text, entries):
    """Shared by the Stop hook and the driver: no '?' after stripping, and not a single labelled request for an open id."""
    return "?" not in strip_code_urls(text) and not single_label_request(text, _open_ids(entries))


def classify_message(text, entries):
    """pass | finish | unlogged | unlogged_resolved_only."""
    open_ids = _open_ids(entries)
    has_q = "?" in strip_code_urls(text)
    ids = extract_ids(text)
    if not has_q:
        return "finish" if (open_ids and is_finish_attempt(text, entries)) else "pass"
    if not (ids & open_ids):
        return "unlogged_resolved_only" if ids else "unlogged"
    return "pass"


def describe_unresolved(state_or_map):
    """'Q4, Q7 (b-auth)' for the block reason."""
    if isinstance(state_or_map, dict) and isinstance(state_or_map.get("entries"), dict):
        parts = []
        for e in state_or_map["entries"].values():
            if e["status"] in UNRESOLVED:
                parts.append(e["id"] if e["branch"] == "main" else "%s (%s)" % (e["id"], e["branch"]))
        return ", ".join(parts)
    return ", ".join("Q%d" % i for i in sorted(_open_ids(state_or_map)))


def stop_decision(text, entries, c, skip=False, stop_hook_active=False, turn_logged=()):
    """The Stop hook rule and loop safety (spec section 4). `c` is the gate-counter dict and is mutated.
    Returns dict(allow, tag, kind, reason, has_q, ids, valid)."""
    out = dict(allow=True, tag="pass", kind="pass", reason=None, has_q=False, ids=[], valid=[])
    if skip:
        out["tag"] = "skip"
        return out
    if not isinstance(text, str):
        c["payload_missing"] = c.get("payload_missing", 0) + 1
        out["tag"] = "payload_missing"
        return out
    k = classify_message(text, entries)
    open_ids = _open_ids(entries)
    ids = extract_ids(text)
    out.update(kind=k, has_q="?" in strip_code_urls(text), ids=sorted(ids), valid=sorted(ids & open_ids))
    if k == "pass":
        return out
    kind = "finish" if k == "finish" else "unlogged"
    ck, rk = "chain_" + kind, "run_" + kind
    if stop_hook_active and c.get("chain_finish", 0) + c.get("chain_unlogged", 0) == 0:
        c["counter_lost"] = c.get("counter_lost", 0) + 1
        out["tag"] = "counter_lost"
        return out
    if c.get(rk, 0) >= (RUN_CAP_FINISH if kind == "finish" else RUN_CAP_UNLOGGED):
        c["run_cap_hit_" + kind] = c.get("run_cap_hit_" + kind, 0) + 1
        out["tag"] = "run_cap_hit_" + kind
        return out
    if c.get(ck, 0) >= CHAIN_CAP:
        c["chain_cap_hit_" + kind] = c.get("chain_cap_hit_" + kind, 0) + 1
        out["tag"] = "chain_cap_hit_" + kind
        return out
    c[ck] = c.get(ck, 0) + 1
    c[rk] = c.get(rk, 0) + 1
    if kind == "finish":
        reason = "Open entries: %s. Ask each, resolve it, or park it with a reason before finishing." % describe_unresolved(entries)
    elif k == "unlogged_resolved_only":
        sm = status_map(entries)
        names = ", ".join("Q%d is %s" % (i, (sm.get(i) or "unknown").lower()) for i in sorted(ids))
        reason = "%s. Reopen it, or log a new entry." % names
    else:
        reason = "This message asks a question that has no open id. Log it with log_question and send it again starting with its id."
        missing = [q for q in turn_logged if int(q[1:]) not in ids]
        if missing:
            reason += " (logged this turn without a label in the message: %s)" % ", ".join(missing)
    out.update(allow=False, tag="block_" + kind, reason=reason)
    return out


def gate_skip_for_tag(tag):
    """True when the driver sets SEQ_GATE_SKIP for a claude call with this file tag."""
    return any(s in tag for s in GATE_SKIP_TAGS)


# ---------------------------------------------------------------- run metrics (compliance, spec sections 7 and 9)

EXEMPT_MSG_TAGS = ("inject_status", "post_done_delivery", "compact", "final_request")


def read_hook_log(state_dir):
    rows = []
    try:
        with open(paths(state_dir)["hooklog"], encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return rows


def _share(num, den):
    return round(num / den, 4) if den else None


def recompute_valid_label_share(driver_log, events, n_prompts):
    """Independent of the hook log: for every non-exempt interviewer-side claude call with a '?' in its (stripped) final
    message, is an id of an open/stale entry present, judged against the store as of that call (events with
    prompt_seq <= call ordinal). Uses the FINAL text of the call (after any gate repair), so it is an upper bound on the
    hook-log share. Returns (share, n_question_bearing, aligned); aligned is False when the number of claude calls in the
    driver log does not equal the UserPromptSubmit count (then nothing is computed)."""
    calls = [r for r in driver_log if r["kind"] == "interviewer" or r["kind"].startswith("inject_")]
    if len(calls) != n_prompts:
        return None, 0, False
    num = den = 0
    for n, r in enumerate(calls, 1):
        if r.get("tag") in EXEMPT_MSG_TAGS or r["kind"] in ("inject_compact", "inject_status", "inject_final"):
            continue
        text = r.get("text") or ""
        if "?" not in strip_code_urls(text):
            continue
        den += 1
        st = replay([ev for ev in events if ev.get("prompt_seq", 0) <= n])
        if extract_ids(text) & _open_ids(st):
            num += 1
    return _share(num, den), den, True


def register_meta(state_dir, driver_log=None, ledger_file=None):
    """The run_meta compliance fields for arms G (ledger_file None) and G-lite (ledger_file given)."""
    g = read_gate(state_dir)
    rows = read_hook_log(state_dir)
    stops = [r for r in rows if r.get("event") == "Stop"]
    first = [r for r in stops if r.get("first_firing") and not r.get("skip")]
    qb = [r for r in first if r.get("has_q")]
    by_phase = {}
    for ph in range(4):
        q = [r for r in qb if min(int(r.get("phase") or 0), 3) == ph]
        by_phase[str(ph)] = dict(n=len(q), share=_share(sum(1 for r in q if r.get("valid")), len(q)))
    m = dict(
        stop_blocks_finish=g["run_finish"], stop_blocks_unlogged=g["run_unlogged"],
        chain_cap_hit_finish=g["chain_cap_hit_finish"], chain_cap_hit_unlogged=g["chain_cap_hit_unlogged"],
        run_cap_hit_finish=g["run_cap_hit_finish"], run_cap_hit_unlogged=g["run_cap_hit_unlogged"],
        payload_missing=g["payload_missing"], counter_lost=g["counter_lost"], extra_q_marks=g["extra_q_marks"],
        user_counter_excluded=g["user_counter_excluded"], source_user_rejected=g["source_user_rejected"],
        user_prompts=g["user_prompts"], prompt_seq=g["prompt_seq"], compactions_seen_by_hook=g["compactions"],
        stop_firings_logged=len(stops),
        valid_label_share=_share(sum(1 for r in qb if r.get("valid")), len(qb)), question_bearing_first_firings=len(qb),
        valid_label_share_by_phase=by_phase,
        first_attempt_valid_share=_share(sum(1 for r in first if r.get("decision") == "pass"), len(first)))
    if ledger_file is None:
        events = read_events(state_dir)
        st = replay(events)
        ops = [e.get("op") for e in events]
        m.update(
            register_unresolved_at_end=unresolved_ids(st),
            log_question_calls=ops.count("log_question"), open_branch_calls=ops.count("open_branch"),
            reopen_calls=ops.count("reopen"), list_open_calls=ops.count("list_open"),
            resolve_calls=ops.count("resolve"), park_calls=ops.count("park"),
            stale_marked=sum(len((e.get("result") or {}).get("stale") or []) for e in events if e.get("op") == "reopen"),
            resolved_without_user_turn=sum(1 for e in events if e.get("op") == "resolve"
                                           and (e.get("args") or {}).get("source") in ("recommended", "explored")
                                           and e.get("since_logged_prompts", 1) == 0),
            resolved_after_nudge_only=sum(1 for e in events if e.get("op") == "resolve" and (e.get("args") or {}).get("source") == "user"
                                          and e.get("since_logged_prompts", 0) > 0
                                          and e.get("since_logged_nudges") == e.get("since_logged_prompts")),
            entries_total=len(st["entries"]), registry_parity=(
                json.loads(json.dumps(st)) == _load_json(paths(state_dir)["derived"])))
        if driver_log is not None:
            share, n, aligned = recompute_valid_label_share(driver_log, events, g["prompt_seq"])
            m.update(valid_label_share_recomputed=share, valid_label_share_recomputed_n=n, recompute_aligned=aligned,
                     valid_label_share_discrepancy=(abs((share or 0) * n - (m["valid_label_share"] or 0) * len(qb)) > 2
                                                    if aligned and share is not None else None))
    else:
        st, mal = load_ledger(ledger_file)
        try:
            raw = open(ledger_file, encoding="utf-8").read()
        except FileNotFoundError:
            raw = ""
        seen_stale = {q for q, e in st["entries"].items() if e["status"] == "STALE"}
        mal_max = mal
        snaps = []
        for r in stops:
            snap = r.get("ledger")
            if not snap:
                continue
            s2, mm = parse_ledger(snap)
            mal_max = max(mal_max, mm)
            seen_stale |= {q for q, e in s2["entries"].items() if e["status"] == "STALE"}
            snaps.append((min(int(r.get("phase") or 0), 3), len(s2["entries"])))
        per_phase, prev = {}, 0
        for ph in range(4):
            last = [n for p_, n in snaps if p_ == ph]
            if last:
                per_phase[str(ph)] = last[-1] - prev
                prev = last[-1]
        m.update(register_unresolved_at_end=unresolved_ids(st), ledger_ids=len(st["entries"]),
                 ledger_branch_headings=len(re.findall(r"(?mi)^##\s+branch\b", raw)),
                 ledger_stale_seen=len(seen_stale), ledger_malformed=mal_max, ledger_entries_added_by_phase=per_phase)
    return m


def _load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None
