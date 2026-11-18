#!/usr/bin/env python3
"""REQ-46 classifier replay (slice I2); stdlib only.

Run: python3 -I conformance/replay_classifier.py --corpus <final30.jsonl> --labels <labels.json> -- <server command...>

For each corpus turn: start a fresh server, set up a register state implied by the label's `presented` flag using only
the public tools (begin_interview, log_item, and the hook-facing note_prompt / check_stop sent without _meta, as the
hooks do), send the turn text through check_stop as last_assistant_message, and read the classification the build made
from the journal `stop` event (the block reply alone cannot tell a finish block from an unlogged-question block).
It never calls internals and never prints turn text.

Labels file (JSON): {"labels": [{"id": "F01", "class": "finish"|"question", "presented": true|false|null}, ...]}
  presented true  : the register holds one Open item whose text is built from content words of the turn (3+ shared
                    words, classifier.md section 5(b)), so the turn concerns it;
  presented false : one Open item with unrelated text, so the turn concerns nothing;
  presented null  : only the finish/question class is compared (the label does not fix the presented items).
A turn agrees when the build's class equals the label class and, where `presented` is true/false, a question turn's
presented list is non-empty/empty accordingly.  Pass: agreement >= 90% and zero finish-labelled turns classified as
question.  The exit status is 0 on pass, 1 on fail.  Corpus lines: {"id", "text", ...}; turn text is data only.
"""
import argparse
import glob
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = json.load(open(os.path.join(ROOT, "contract", "tools.json"), encoding="utf-8"))
SCHEMA = TOOLS["tool_schema"]
THRESHOLD = 0.9  # declared constants (REQ-46)
STOP = set("""about all also an and any are as at be been but by can could did do does done each for from get got had has
have he her how if in into is it just let may me might more most must my no not of on or other our out over shall she
should so some such than that the their them then there they this to too up us very was we were what when where which who
whom why will with would you your""".split())
FILLER = "zzquux yyplugh xxwibble"  # words no real turn contains


def content_words(text):
    seen, order = {}, []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if len(w) > 1 and w not in STOP:
            if w not in seen:
                order.append(w)
            seen[w] = seen.get(w, 0) + 1
    order.sort(key=lambda w: -seen[w])  # stable: most frequent first, then first appearance
    return order


class Server:
    def __init__(self, cmd, data, proj):
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=data, CLAUDE_PROJECT_DIR=proj)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  env=env, cwd=proj)
        self.n = 0
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "replay", "version": "0"}})
        self.p.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.p.stdin.flush()

    def rpc(self, method, params):
        self.n += 1
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n").encode())
        self.p.stdin.flush()
        while True:
            if not select.select([self.p.stdout], [], [], 30)[0]:
                raise RuntimeError("server timeout")
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("server closed")
            m = json.loads(line)
            if m.get("id") == self.n:
                return m

    def call(self, name, args):  # no _meta key at all: hook-style, and fine for model-facing tools
        m = self.rpc("tools/call", {"name": name, "arguments": args})
        if "error" in m:
            raise RuntimeError("protocol error from %s" % name)
        r = m["result"]
        text = "".join(c.get("text", "") for c in r.get("content", []))
        return bool(r.get("isError")), text

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=15)
        except Exception:
            self.p.kill()


def replay_turn(cmd, work, tid, text, presented):
    data, proj = os.path.join(work, "data"), os.path.join(work, "proj")
    os.makedirs(data)
    os.makedirs(proj)
    srv = Server(cmd, data, proj)
    sid = "replay-" + tid
    try:
        err, out = srv.call("note_prompt", {"session_id": sid, "prompt": "replay prompt"})
        assert not err, "note_prompt: " + out[:80]
        err, out = srv.call("begin_interview", {"tool_schema": SCHEMA, "skill": "grill-me", "mode": "register"})
        assert not err, "begin_interview: " + out[:80]
        words = content_words(text)
        item = " ".join(words[:5]) if presented and len(words) >= 3 else FILLER
        err, out = srv.call("log_item", {"kind": "question", "text": item})
        assert not err, "log_item: " + out[:80]
        err, out = srv.call("check_stop", {"session_id": sid, "last_assistant_message": text})
        assert not err, "check_stop: " + out[:80]
        blocked = bool(out.strip()) and json.loads(out).get("decision") == "block"
    finally:
        srv.close()
    stops = []
    for jp in glob.glob(os.path.join(data, "*", "*.jsonl")):
        for line in open(jp, encoding="utf-8"):
            e = json.loads(line)
            if e.get("kind") == "stop":
                stops.append(e)
    assert len(stops) == 1, "expected one stop event, found %d" % len(stops)
    s = stops[0]
    return {"class": s["classification"], "decision": s["decision"], "presented": s["presented"], "blocked": blocked}


def main():
    argv = sys.argv[1:]
    if "--" not in argv:
        sys.exit("usage: replay_classifier.py --corpus F --labels F -- <server command...>")
    cmd = argv[argv.index("--") + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--work", default=None, help="scratch directory (default: a new directory under .scratch/)")
    a = ap.parse_args(argv[:argv.index("--")])
    labels = {l["id"]: l for l in json.load(open(a.labels, encoding="utf-8"))["labels"]}
    turns = [json.loads(l) for l in open(a.corpus, encoding="utf-8") if l.strip()]
    base = a.work or os.path.join(ROOT, ".scratch", "replay-%d" % int(time.time()))
    os.makedirs(base, exist_ok=True)
    rows, conf = [], {}
    for t in turns:
        lab = labels[t["id"]]
        w = tempfile.mkdtemp(prefix=t["id"] + "-", dir=base)
        r = replay_turn(cmd, w, t["id"], t["text"], lab["presented"])
        shutil.rmtree(w, ignore_errors=True)
        got = "finish" if r["class"] == "finish" else "question"
        ok = got == lab["class"]
        if ok and got == "question" and lab["presented"] is not None:
            ok = bool(r["presented"]) == bool(lab["presented"])
        conf[(lab["class"], got)] = conf.get((lab["class"], got), 0) + 1
        rows.append((t["id"], lab["class"], got, bool(r["presented"]), r["decision"], ok))
        print("%s label=%s got=%s presented=%s decision=%s %s" % (t["id"], lab["class"], got, "yes" if r["presented"] else "no",
                                                                   r["decision"], "agree" if ok else "DISAGREE"))
    n, agree = len(rows), sum(1 for r in rows if r[5])
    fq = conf.get(("finish", "question"), 0)
    pct = 100.0 * agree / n
    print("turns=%d agree=%d agreement=%.1f%%" % (n, agree, pct))
    print("confusion label->got: finish->finish %d, finish->question %d, question->finish %d, question->question %d" % (
        conf.get(("finish", "finish"), 0), fq, conf.get(("question", "finish"), 0), conf.get(("question", "question"), 0)))
    p1, p2 = agree / n >= THRESHOLD, fq == 0
    print("criterion agreement>=90%%: %s; zero finish attempts classified as question turns: %s" % (
        "pass" if p1 else "FAIL", "pass" if p2 else "FAIL"))
    print("disagreements: %s" % (", ".join(r[0] for r in rows if not r[5]) or "none"))
    sys.exit(0 if p1 and p2 else 1)


if __name__ == "__main__":
    main()
