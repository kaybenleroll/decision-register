"""Shared by the arm G hook entry points. Every hook exits 0 on internal error, logs it to hook-log.jsonl
and fails open (spec section 3)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from hook_common import read_stdin, state_dir, log  # noqa: E402,F401
import register_store as store  # noqa: E402

LEDGER_FLAG = "--ledger"


def ledger_mode(argv=None):
    return LEDGER_FLAG in (sys.argv[1:] if argv is None else argv)


def ledger_path(payload):
    return os.path.join(payload.get("cwd") or os.getcwd(), ".scratch", "ledger.md")


def current_state(payload, ledger):
    """(state, malformed). G: replay of register.jsonl. G-lite: the parsed ledger file."""
    if ledger:
        return store.load_ledger(ledger_path(payload))
    return store.load(state_dir(payload)), 0


def skip_set():
    return bool(os.environ.get("SEQ_GATE_SKIP"))


def emit_context(event_name, text):
    if text:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}))
