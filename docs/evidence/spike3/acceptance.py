#!/usr/bin/env python3
"""Black-box acceptance script for scenarios A1-A7 (spike 3). Stdlib only.

Usage: python3 acceptance.py -- <server command and args>

Assumptions (spec leaves these open; only the stated parts are asserted):
- MCP stdio transport is newline-delimited JSON-RPC. Tool results are
  result.content[0].text; failures have result.isError true (a JSON-RPC
  error is also accepted as an error).
- status() text is a JSON object with an "items" object of counts keyed by
  status name; keys are matched case-insensitively ("Open"/"open"); a
  missing key counts as 0. "journal_schema" is a top-level key.
- check_stop block text is JSON {"decision":"block","reason":"..."}.
- Journal path is $JOURNAL_DIR/<interview_id>.jsonl, one JSON object per line.
  Because the v1 event schema is not given in the spec, "v1 lines" are made
  by taking events the server wrote itself and deleting their "v" and "ts"
  keys. The v=3 line is a copy of the last event with "v" set to 3.
- Error "mentioning the id" = the error text contains the id string.
- Failed resume must report the 1-based line number as a digit string in
  the error text (checked as the substring, e.g. "4"); after a failed resume
  in a live server, status() counts are unchanged.
- Argument names follow SPEC.md (reopen: id, reason; log_item: depends_on
  list; park: id, reason, parked_by; resolve: id, answer, source, rationale).
"""
import json, os, shutil, subprocess, sys, threading, queue, time, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMPROOT = os.path.join(HERE, "acc-tmp")
TIMEOUT = 20


class Server:
    def __init__(self, cmd, jdir):
        env = dict(os.environ, JOURNAL_DIR=jdir)
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
        self.q = queue.Queue()
        threading.Thread(target=self._rd, daemon=True).start()
        self.n = 0
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "acceptance", "version": "0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _rd(self):
        for line in self.p.stdout:
            self.q.put(line)
        self.q.put(None)

    def _send(self, o):
        self.p.stdin.write(json.dumps(o) + "\n")
        self.p.stdin.flush()

    def rpc(self, method, params):
        self.n += 1
        i = self.n
        self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        end = time.time() + TIMEOUT
        while True:
            try:
                line = self.q.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                raise RuntimeError("timeout waiting for %s" % method)
            if line is None:
                raise RuntimeError("server exited")
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("id") == i and ("result" in m or "error" in m):
                return m

    def call(self, name, **args):
        """Return (is_error, text)."""
        m = self.rpc("tools/call", {"name": name, "arguments": args})
        if "error" in m:
            return True, json.dumps(m["error"])
        r = m["result"]
        text = "".join(c.get("text", "") for c in r.get("content", []) if isinstance(c, dict))
        return bool(r.get("isError")), text

    def ok(self, name, **args):
        e, t = self.call(name, **args)
        if e:
            raise AssertionError("%s %s unexpectedly failed: %s" % (name, args, t[:150]))
        return t

    def err(self, name, mention=None, **args):
        e, t = self.call(name, **args)
        if not e:
            raise AssertionError("%s %s should have errored, got: %s" % (name, args, t[:150]))
        if mention and mention not in t:
            raise AssertionError("error for %s lacks %r: %s" % (name, mention, t[:150]))
        return t

    def status(self):
        return json.loads(self.ok("status"))

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.wait(timeout=3)
        except Exception:
            self.p.kill()


def cnt(st, key):
    for k, v in st.get("items", {}).items():
        if k.lower() == key:
            return v
    return 0


def counts(st):
    return {k.lower(): v for k, v in st.get("items", {}).items() if v}


def resolve(s, i, source="model"):
    s.ok("resolve", id=i, answer="a", source=source, rationale="r")


def chain3(s):
    s.ok("log_item", id="q1", kind="question", text="t1")
    s.ok("log_item", id="q2", kind="question", text="t2", depends_on=["q1"])
    s.ok("log_item", id="q3", kind="question", text="t3", depends_on=["q2"])
    for i in ("q1", "q2", "q3"):
        resolve(s, i)


def new(cmd, jdir, iid="acc"):
    s = Server(cmd, jdir)
    s.ok("begin_interview", interview_id=iid)
    return s


def a1(cmd, jdir):
    s = new(cmd, jdir)
    try:
        chain3(s)
        s.ok("reopen", id="q1", reason="r")
        st = s.status()
        assert cnt(st, "open") == 1 and cnt(st, "stale") == 2, "counts after reopen %s" % counts(st)
        resolve(s, "q1")
        resolve(s, "q2")
        st = s.status()
        assert cnt(st, "stale") == 1 and cnt(st, "resolved") == 2 and cnt(st, "open") == 0, \
            "final counts %s" % counts(st)
        return "open=1 stale=2 after reopen; q2 resolved, q3 stale"
    finally:
        s.close()


def a2(cmd, jdir):
    s = new(cmd, jdir)
    try:
        s.ok("log_item", id="q1", kind="question", text="t")
        s.err("reopen", id="q1", reason="r")
        s.err("log_item", mention="nope", id="q2", kind="question", text="t", depends_on=["nope"])
        return "reopen on Open errors; unknown depends_on errors"
    finally:
        s.close()


def a3(cmd, jdir):
    s = new(cmd, jdir)
    try:
        chain3(s)
        s.ok("reopen", id="q1", reason="r")
        resolve(s, "q1")  # q2, q3 remain Stale, nothing Open
        st = s.status()
        assert cnt(st, "open") == 0 and cnt(st, "stale") == 2, "setup counts %s" % counts(st)
        t = s.ok("check_stop", last_assistant_message="done")
        d = json.loads(t)
        assert d.get("decision") == "block", "not blocked: %s" % t[:150]
        r = d.get("reason", "")
        assert "q2" in r and "q3" in r, "reason lacks stale ids: %s" % r
        return "blocked, reason=%r" % r
    finally:
        s.close()


def a4(cmd, jdir):
    s = new(cmd, jdir)
    try:
        s.ok("log_item", id="d1", kind="decision", text="t")
        s.err("resolve", mention="d1", id="d1", answer="a", source="model", rationale="r")
        s.ok("note_prompt")
        resolve(s, "d1", "user")
        assert cnt(s.status(), "resolved") == 1, "d1 not Resolved"
        return "model resolve rejected naming d1; user resolve ok"
    finally:
        s.close()


def a5(cmd, jdir):
    s = new(cmd, jdir)
    try:
        s.ok("log_item", id="d1", kind="decision", text="t")
        s.err("park", id="d1", reason="r", parked_by="model")
        s.ok("log_item", id="q1", kind="question", text="t")
        s.ok("park", id="q1", reason="r", parked_by="model")
        return "model park of decision rejected; question park ok"
    finally:
        s.close()


def read_journal(jdir, iid="acc"):
    with open(os.path.join(jdir, iid + ".jsonl")) as f:
        return [l.rstrip("\n") for l in f if l.strip()]


def a6(cmd, jdir):
    s = new(cmd, jdir)
    try:
        s.ok("log_item", id="q1", kind="question", text="t1")
        s.ok("log_item", id="q2", kind="question", text="t2")
        resolve(s, "q1")
        want = counts(s.status())
    finally:
        s.close()
    lines = read_journal(jdir)
    evs = [json.loads(l) for l in lines]
    assert evs and all(e.get("v") == 2 for e in evs), "events lack v=2: %s" % lines[:1]
    assert all(isinstance(e.get("ts"), str) and e["ts"].endswith(("Z", "+00:00")) and "T" in e["ts"]
               for e in evs), "events lack RFC3339 UTC ts"
    path = os.path.join(jdir, "acc.jsonl")
    n = len(lines)

    def write(ls):
        with open(path, "w") as f:
            f.write("\n".join(ls) + "\n")

    def v1(e):
        e = dict(e)
        e.pop("v", None)
        e.pop("ts", None)
        return json.dumps(e)

    # all-v1 journal replays to the same counts
    write([v1(e) for e in evs])
    s = Server(cmd, jdir)
    try:
        s.ok("resume", interview_id="acc")
        assert counts(s.status()) == want, "v1 replay %s != %s" % (counts(s.status()), want)
    finally:
        s.close()
    # mixed v1/v2
    write([v1(e) for e in evs[:-1]] + [lines[-1]])
    s = Server(cmd, jdir)
    try:
        s.ok("resume", interview_id="acc")
        assert counts(s.status()) == want, "mixed replay %s != %s" % (counts(s.status()), want)
    finally:
        s.close()
    # v=3 line: fresh resume fails naming line n+1
    bad = dict(evs[-1])
    bad["v"] = 3
    write(lines + [json.dumps(bad)])
    s = Server(cmd, jdir)
    try:
        t = s.err("resume", interview_id="acc")
        assert str(n + 1) in t, "error lacks line number %d: %s" % (n + 1, t[:150])
    finally:
        s.close()
    # live server: failed resume leaves prior state unchanged
    write(lines)
    s = Server(cmd, jdir)
    try:
        s.ok("resume", interview_id="acc")
        before = counts(s.status())
        write(lines + [json.dumps(bad)])
        t = s.err("resume", interview_id="acc")
        assert str(n + 1) in t, "live error lacks line number: %s" % t[:150]
        assert counts(s.status()) == before, "state changed after failed resume"
    finally:
        s.close()
    return "v=2+ts written; v1/mixed replay ok; v=3 fails naming line %d; state unchanged" % (n + 1)


def a7(cmd, jdir):
    s = new(cmd, jdir)
    try:
        st = s.status()
        assert st.get("journal_schema") == 2, "journal_schema=%r" % st.get("journal_schema")
        return "journal_schema=2"
    finally:
        s.close()


def main():
    if "--" not in sys.argv or sys.argv.index("--") == len(sys.argv) - 1:
        print(__doc__.split("Assumptions")[0].strip())
        return 2
    cmd = sys.argv[sys.argv.index("--") + 1:]
    os.makedirs(TMPROOT, exist_ok=True)
    fails = 0
    for name, fn in [("A1", a1), ("A2", a2), ("A3", a3), ("A4", a4), ("A5", a5), ("A6", a6), ("A7", a7)]:
        jdir = tempfile.mkdtemp(prefix=name + "-", dir=TMPROOT)
        try:
            detail = fn(cmd, jdir)
            print("PASS %s: %s" % (name, detail))
        except Exception as e:
            fails += 1
            print("FAIL %s: %s: %s" % (name, type(e).__name__, str(e).replace("\n", " ")[:300]))
        shutil.rmtree(jdir, ignore_errors=True)
    print("%d/7 passed" % (7 - fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
