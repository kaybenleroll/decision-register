"""Thought-tree builder and renderer (the rendering spec shared with the JS port).

Input: an ordered list of sequentialthinking call inputs (dicts). Output: the
`<thought-tree>` text. The JS port in patched-server/render.mjs must produce
byte-identical output for the same calls (checked by patched-server/parity_test.py).

Spec (all ASCII; lengths counted in Unicode code points):
  nodes     keyed by thoughtNumber; a later call with the same number replaces it.
  OPEN      latest node (by call order) whose thought starts with "OPEN:".
            Open ids = tokens "Q<n>[open]" in that text.
  branches  a branchId is open once a call carries branchFromThought+branchId and
            stays open until a later call with the same branchId has a thought that
            contains the word "closed" (case-insensitive). Stack is ordered by
            first appearance; entry text is "<branchId>><branchFromThought>".
  closed    nodes whose thought matches ^A<digits>'?: ; superseded when a later
            call has isRevision true and revisesThought == the node number.
  header    "Q<next> [open: <ids|none>; stack: <entries|none>; last: #<k>]"
"""
import re

CAP = 6000
LEDGER_CAP = 1500
NODE_CAP = 160
DECISION_CAP = 140
LAST_N = 5


def _trunc(s, n):
    s = " ".join(s.split())
    cps = list(s)
    return s if len(cps) <= n else "".join(cps[:n - 3]) + "..."


def _boolish(v):
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return bool(v)


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def build(calls):
    nodes = {}
    order = []          # thoughtNumbers in call order (last occurrence kept)
    ledger = None       # (thoughtNumber, text)
    branches = {}       # id -> {"from": n, "closed": bool}
    branch_order = []
    revised = {}        # thoughtNumber -> revising thoughtNumber
    for c in calls:
        n = _num(c.get("thoughtNumber"))
        if n is None:
            continue
        text = str(c.get("thought", ""))
        node = {"n": n, "text": text, "branchId": c.get("branchId") or None,
                "branchFrom": _num(c.get("branchFromThought")),
                "isRevision": _boolish(c.get("isRevision")),
                "revises": _num(c.get("revisesThought"))}
        nodes[n] = node
        if n in order:
            order.remove(n)
        order.append(n)
        if text.startswith("OPEN:"):
            ledger = (n, text)
        bid = node["branchId"]
        if bid:
            if bid not in branches:
                branches[bid] = {"from": node["branchFrom"], "closed": False}
                branch_order.append(bid)
            elif branches[bid]["from"] is None and node["branchFrom"]:
                branches[bid]["from"] = node["branchFrom"]
            if "closed" in text.lower() and not text.startswith("OPEN:"):
                branches[bid]["closed"] = True
        if node["isRevision"] and node["revises"] is not None:
            revised[node["revises"]] = n
    open_ids = []
    if ledger:
        open_ids = ["Q" + m for m in re.findall(r"Q(\d+)\s*\[open\]", ledger[1])]
    stack = ["%s>%s" % (b, branches[b]["from"] if branches[b]["from"] is not None else "?")
             for b in branch_order if not branches[b]["closed"]]
    decisions = []
    for n in sorted(nodes):
        t = nodes[n]["text"]
        if re.match(r"^A\d+'?:", t):
            decisions.append((n, t, revised.get(n)))
    next_free = (max(nodes) + 1) if nodes else 1
    last = order[-1] if order else 0
    return {"nodes": nodes, "order": order, "ledger": ledger, "open_ids": open_ids,
            "stack": stack, "decisions": decisions, "next_free": next_free, "last": last}


def header_line(state):
    qn = 1
    mx = 0
    for n in state["nodes"].values():
        m = re.match(r"^Q(\d+)", n["text"])
        if m:
            mx = max(mx, int(m.group(1)))
    qn = mx + 1
    return "Q%d [open: %s; stack: %s; last: #%d]" % (
        qn, ", ".join(state["open_ids"]) or "none", ", ".join(state["stack"]) or "none",
        state["last"])


def render(calls, cap=CAP):
    st = build(calls)
    if not st["nodes"]:
        return ""
    ledger_txt = _trunc(st["ledger"][1], LEDGER_CAP) if st["ledger"] else "(none recorded)"
    ledger_n = st["ledger"][0] if st["ledger"] else 0
    dec_lines = []
    for n, t, by in st["decisions"]:
        line = "  #%d %s" % (n, _trunc(t, DECISION_CAP))
        if by is not None:
            line += " [superseded by #%d]" % by
        dec_lines.append(line)
    last_nodes = [st["nodes"][n] for n in st["order"][-LAST_N:]]

    def last_line(nd):
        tags = ""
        if nd["branchId"]:
            tags += " [branch %s]" % nd["branchId"]
        if nd["isRevision"]:
            tags += " [revises #%s]" % (nd["revises"] if nd["revises"] is not None else "?")
        return "  #%d%s %s" % (nd["n"], tags, _trunc(nd["text"], NODE_CAP))

    def assemble(decs, omitted, ledger_text):
        out = ["<thought-tree>",
               "Facts derived from your recorded sequentialthinking calls (latest state):",
               "Next free thoughtNumber: %d" % st["next_free"],
               "Header to copy: %s" % header_line(st),
               "Latest OPEN ledger (thought #%d): %s" % (ledger_n, ledger_text),
               "Branch stack: %s" % ("; ".join(st["stack"]) or "(empty)"),
               "Decisions (A-nodes):"]
        if omitted:
            out.append("  (%d older decisions omitted)" % omitted)
        out.extend(decs or ["  (none)"])
        out.append("Last %d nodes:" % len(last_nodes))
        out.extend(last_line(nd) for nd in last_nodes)
        out.append("</thought-tree>")
        return "\n".join(out)

    omitted = 0
    decs = list(dec_lines)
    text = assemble(decs, omitted, ledger_txt)
    while len(text) > cap and decs:
        decs.pop(0)
        omitted += 1
        text = assemble(decs, omitted, ledger_txt)
    if len(text) > cap:
        over = len(text) - cap
        keep = max(40, len(ledger_txt) - over - 3)
        text = assemble(decs, omitted, _trunc(ledger_txt, keep))
    return text
