"""Shared helpers: transcript JSONL reading, recursive block walking, turn splitting.

Works on both Claude Code session transcripts and `--output-format stream-json`
logs: both are JSONL whose records may nest `tool_use` objects inside
`message.content` arrays, so every search here is recursive.
"""
import json
import re

SEQ_TOOL = "mcp__sequential-thinking__sequentialthinking"
TREE_TOOL = "mcp__sequential-thinking__get_thought_tree"


def read_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def walk(obj):
    """Yield every dict nested anywhere inside obj (including obj)."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def tool_uses(record):
    """All tool_use blocks nested anywhere in one record, in document order."""
    found = []

    def rec(o):
        if isinstance(o, dict):
            if o.get("type") == "tool_use" and "name" in o:
                found.append(o)
            for v in o.values():
                rec(v)
        elif isinstance(o, list):
            for v in o:
                rec(v)

    rec(record)
    return found


def tool_results(record):
    found = []

    def rec(o):
        if isinstance(o, dict):
            if o.get("type") == "tool_result":
                found.append(o)
            for v in o.values():
                rec(v)
        elif isinstance(o, list):
            for v in o:
                rec(v)

    rec(record)
    return found


def result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def result_is_error(block):
    if block.get("is_error"):
        return True
    t = result_text(block)
    return '"status": "failed"' in t or '"status":"failed"' in t


def content_blocks(record):
    msg = record.get("message")
    if not isinstance(msg, dict):
        return []
    c = msg.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return c if isinstance(c, list) else []


def record_text(record):
    return "\n".join(b.get("text", "") for b in content_blocks(record)
                     if isinstance(b, dict) and b.get("type") == "text")


def is_plain_user_prompt(record):
    """A real user turn (harness-sent prompt), not a tool_result or meta record."""
    if record.get("type") != "user":
        return False
    if record.get("isMeta") or record.get("isCompactSummary") or record.get("isSidechain"):
        return False
    blocks = content_blocks(record)
    if not blocks:
        return False
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
        return False
    txt = record_text(record)
    if not txt.strip():
        return False
    if "<command-name>" in txt or "<local-command" in txt or "<command-message>" in txt:
        return False
    return True


def is_compact_boundary(record):
    return (record.get("type") == "system" and record.get("subtype") == "compact_boundary") \
        or bool(record.get("isCompactSummary"))


def split_turns(records):
    """Split records into turns. A turn starts at a plain user prompt.

    Returns list of dicts: {index, user_text, records, assistant_text (list of
    text chunks in order), final_text (last assistant text chunk), compact_before}.
    Records before the first user prompt are dropped.
    """
    turns = []
    cur = None
    compact_pending = False
    for rec in records:
        if is_compact_boundary(rec):
            compact_pending = True
        if is_plain_user_prompt(rec):
            cur = {"index": len(turns), "user_text": record_text(rec), "records": [rec],
                   "compact_before": compact_pending}
            compact_pending = False
            turns.append(cur)
            continue
        if cur is not None:
            cur["records"].append(rec)
    for t in turns:
        chunks = []
        for rec in t["records"]:
            if rec.get("type") == "assistant":
                txt = record_text(rec).strip()
                if txt:
                    chunks.append(txt)
        t["assistant_text"] = chunks
        t["final_text"] = chunks[-1] if chunks else ""
        t["all_text"] = "\n".join(chunks)
    return turns


def seq_calls(records):
    """Ordered list of sequentialthinking calls: dicts with input, id, record_index, ok."""
    results = {}
    for rec in records:
        for tr in tool_results(rec):
            results[tr.get("tool_use_id")] = tr
    calls = []
    for i, rec in enumerate(records):
        if rec.get("type") not in (None, "assistant"):
            continue
        for tu in tool_uses(rec):
            if tu.get("name") == SEQ_TOOL and isinstance(tu.get("input"), dict):
                res = results.get(tu.get("id"))
                ok = not (res is not None and result_is_error(res))
                calls.append({"input": tu["input"], "id": tu.get("id"), "record_index": i, "ok": ok})
    return calls


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", s.lower())).strip()


# Post-compaction status query. v1 = the fixture key.json text (flagged by the API classifier in pilot run B/p1);
# v2 asks for task state only, never for the interviewer's own reasoning.
STATUS_QUERY_V2 = ("Please list the current state of the task: the decisions still open, the decisions already settled, "
                   "the branches (with their branch ids) and any reopened items, and the constraints established so far. "
                   "Give it as a numbered list of items.")


def status_query_text(key, version):
    return key["status_query"]["text"] if version == "v1" else STATUS_QUERY_V2
