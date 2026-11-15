#!/usr/bin/env python3
"""Render the thought tree from a transcript JSONL and/or a PostToolUse sidecar.

  render_tree.py --transcript T.jsonl            # tree text on stdout
  render_tree.py --sidecar tree.jsonl            # from the sidecar
  render_tree.py --transcript T --sidecar S      # merge: sidecar wins on tool_use_id clashes
  render_tree.py --calls-json calls.json         # list of tool inputs (tests)
  render_tree.py --header ...                    # print only the header line to copy
Exit code 0 always; empty output when there are no calls.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import tree_lib  # noqa: E402
from common import read_jsonl, seq_calls  # noqa: E402


def calls_from_transcript(path):
    if not path or not os.path.exists(path):
        return []
    return [c["input"] for c in seq_calls(read_jsonl(path)) if c["ok"]]


def calls_from_sidecar(path):
    if not path or not os.path.exists(path):
        return []
    seen = {}
    order = []
    for rec in read_jsonl(path):
        tid = rec.get("tool_use_id") or "n%d" % len(order)
        if tid not in seen:
            order.append(tid)
        seen[tid] = rec.get("tool_input") or {}
    return [seen[t] for t in order]


def collect(transcript=None, sidecar=None):
    side = calls_from_sidecar(sidecar)
    if side:
        return side
    return calls_from_transcript(transcript)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript")
    ap.add_argument("--sidecar")
    ap.add_argument("--calls-json")
    ap.add_argument("--cap", type=int, default=tree_lib.CAP)
    ap.add_argument("--header", action="store_true")
    a = ap.parse_args(argv)
    if a.calls_json:
        with open(a.calls_json) as fh:
            calls = json.load(fh)
    else:
        calls = collect(a.transcript, a.sidecar)
    if a.header:
        sys.stdout.write(tree_lib.header_line(tree_lib.build(calls)) + "\n" if calls else "")
    else:
        out = tree_lib.render(calls, a.cap)
        sys.stdout.write(out + ("\n" if out else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
