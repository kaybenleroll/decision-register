"""Shared by the hook entry points. Hooks never fail the turn: any error is logged and
the hook exits 0 with no output."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import render_tree  # noqa: E402
import tree_lib  # noqa: E402


def read_stdin():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def state_dir(payload):
    base = os.environ.get("SEQ_STATE_DIR") or os.path.join(payload.get("cwd") or os.getcwd(), ".harness")
    os.makedirs(base, exist_ok=True)
    return base


def log(payload, event, extra=None):
    try:
        rec = {"ts": time.time(), "event": event, "session_id": payload.get("session_id")}
        rec.update(extra or {})
        with open(os.path.join(state_dir(payload), "hook-log.jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def store_mode(argv=None):
    return "--store" in (sys.argv[1:] if argv is None else argv)


def inject(event_name, store=False):
    payload = read_stdin()
    if store:
        return inject_store(payload, event_name)
    try:
        side = os.path.join(state_dir(payload), "tree.jsonl")
        calls = render_tree.collect(payload.get("transcript_path"), side)
        text = tree_lib.render(calls)
    except Exception as exc:  # noqa: BLE001
        log(payload, event_name + ":error", {"error": repr(exc)})
        return 0
    log(payload, event_name, {"source": payload.get("source"), "chars": len(text),
                              "calls": len(calls)})
    if text:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": event_name, "additionalContext": text}}))
    return 0


def inject_store(payload, event_name):
    """Arm F: inject the <open-branches> summary from the replayed question register."""
    try:
        import tree_store
        text = tree_store.render_injection(tree_store.load(state_dir(payload)))
    except Exception as exc:  # noqa: BLE001
        log(payload, event_name + ":error", {"error": repr(exc)})
        return 0
    log(payload, event_name, {"source": payload.get("source"), "chars": len(text), "mode": "store"})
    if text:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": event_name, "additionalContext": text}}))
    return 0
