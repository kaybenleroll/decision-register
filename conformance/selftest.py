#!/usr/bin/env python3
"""Self-test of the conformance driver (run.py): every construct the scenario format offers is parsed, and is rejected
when malformed. Stdlib only. No server needed.

Usage: python3 -I conformance/selftest.py      (non-zero exit on any failure)
"""
import importlib.util, json, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("conf_run", os.path.join(HERE, "run.py"))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
TOOLS, VALIDATOR = run.load_contract()

TESTS = []


def test(f):
    TESTS.append(f)
    return f


def scen(*steps, **extra):
    return dict(id="T", title="t", req=["REQ-0"], steps=list(steps), _file="T.json", **extra)


def errors(*steps, **extra):
    return run.validate([scen(*steps, **extra)], TOOLS, VALIDATOR)


def accepts(*steps, **extra):
    errs = errors(*steps, **extra)
    assert not errs, "expected no validation error, got %s" % errs


def rejects(fragment, *steps, **extra):
    errs = errors(*steps, **extra)
    assert any(fragment in e for e in errs), "expected an error containing %r, got %s" % (fragment, errs)


# ---------------------------------------------------------------- a tiny fake server for the execution tests
# `python3 -I selftest.py --fake`: speaks just enough MCP over stdio for the driver. Every tools/call appends the
# tool name to <CLAUDE_PLUGIN_DATA>/calls.log (as "<tool> <pid>") and returns empty text (valid for the hook-facing tools); exits at EOF.

def fake_server():
    data = os.environ.get("CLAUDE_PLUGIN_DATA", ".")
    for line in sys.stdin:
        m = json.loads(line)
        if "id" not in m:
            continue
        if m["method"] == "tools/call":
            with open(os.path.join(data, "calls.log"), "a") as f:
                f.write("%s %d\n" % (m["params"]["name"], os.getpid()))
            res = {"content": [{"type": "text", "text": ""}]}
        else:
            res = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "0"}}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": res}) + "\n")
        sys.stdout.flush()


FAKE = [sys.executable, "-I", os.path.abspath(__file__), "--fake"]
PLAY_DIR = None


def play(*steps, **extra):
    """Run a scenario against the fake server; returns (ok, detail)."""
    global PLAY_DIR
    if PLAY_DIR is None:
        os.makedirs(run.SCRATCH, exist_ok=True)
        PLAY_DIR = run.RUN_DIR = tempfile.mkdtemp(prefix="selftest-", dir=run.SCRATCH)
    out = run.run_scenario(FAKE, scen(*steps, **extra), TOOLS, VALIDATOR)
    shutil.rmtree(os.path.join(PLAY_DIR, "T"), ignore_errors=True)  # failures are expected here
    return out


def plays(*steps, **extra):
    ok, detail = play(*steps, **extra)
    assert ok, detail


def fails(fragment, *steps, **extra):
    ok, detail = play(*steps, **extra)
    assert not ok and fragment in detail, "expected a failure containing %r, got ok=%s %s" % (fragment, ok, detail)


NOTE = {"call": "note_prompt", "args": {"session_id": "s", "prompt": "p"}}

BEGIN = {"call": "begin_interview", "args": {"tool_schema": 3, "skill": "grill-me", "mode": "register"}}


def log(i):
    return {"call": "log_item", "args": {"id": i, "kind": "question", "text": "t"}}


# ---------------------------------------------------------------- repeat

@test
def repeat_count_parsed():
    accepts(BEGIN, {"op": "repeat", "count": 3, "var": "i", "steps": [log("q${i}")]})


@test
def repeat_rows_parsed():
    accepts(BEGIN, {"op": "repeat", "rows": [{"id": "a1", "t": "x"}, {"id": "b2", "t": "y"}],
                    "steps": [{"call": "log_item", "args": {"id": "$id", "kind": "question", "text": "$t"}}]})


@test
def repeat_rejected_when_malformed():
    body = [log("q${i}")]
    rejects("exactly one of count and rows", {"op": "repeat", "steps": body})
    rejects("exactly one of count and rows", {"op": "repeat", "count": 2, "rows": [{"a": 1}], "var": "i", "steps": body})
    rejects("count is an integer", {"op": "repeat", "count": 0, "var": "i", "steps": body})
    rejects("count is an integer", {"op": "repeat", "count": True, "var": "i", "steps": body})
    rejects("count is an integer", {"op": "repeat", "count": run.MAX_REPEAT + 1, "var": "i", "steps": body})
    rejects("needs var", {"op": "repeat", "count": 2, "steps": body})
    rejects("needs var", {"op": "repeat", "count": 2, "var": "1x", "steps": body})
    rejects("var applies to count", {"op": "repeat", "rows": [{"a": 1}], "var": "i", "steps": body})
    rejects("rows is a non-empty list", {"op": "repeat", "rows": [], "steps": body})
    rejects("rows is a non-empty list", {"op": "repeat", "rows": [{"a b": 1}], "steps": body})
    rejects("non-empty steps", {"op": "repeat", "count": 2, "var": "i", "steps": []})
    rejects("unknown key", {"op": "repeat", "count": 2, "var": "i", "steps": body, "each": 1})
    rejects("do not nest", {"op": "repeat", "count": 2, "var": "i",
                            "steps": [{"op": "repeat", "count": 2, "var": "j", "steps": body}]})


@test
def repeat_steps_are_validated_per_pass():
    # a bad value in one row only shows when that pass is expanded
    rejects("violate tools.json", {"op": "repeat", "rows": [{"id": "ok1"}, {"id": "bad id"}],
                                   "steps": [{"call": "log_item", "args": {"id": "$id", "kind": "question", "text": "t"}}]})
    rejects("unknown tool", {"op": "repeat", "count": 2, "var": "i", "steps": [{"call": "nope"}]})


@test
def repeat_runs_each_pass():
    plays({"op": "repeat", "count": 3, "var": "i", "steps": [NOTE]},
          {"op": "fs", "assert": [{"path": "calls.log", "matches": r"\A(note_prompt \d+\n){3}\Z"}]})
    plays({"op": "repeat", "rows": [{"p": "a"}, {"p": "b"}],
           "steps": [{"call": "note_prompt", "args": {"session_id": "s", "prompt": "$p"}}]},
          {"op": "fs", "assert": [{"path": "calls.log", "matches": r"\A(note_prompt \d+\n){2}\Z"}]})


@test
def repeat_binds_its_variable():
    # the count variable reaches the pass: a wrong expected count fails
    fails("does not match", {"op": "repeat", "count": 2, "var": "i", "steps": [NOTE]},
          {"op": "fs", "assert": [{"path": "calls.log", "matches": r"\A(note_prompt \d+\n){3}\Z"}]})


# ---------------------------------------------------------------- two servers

def on(name, step):
    return dict(step, server=name)


@test
def servers_parsed():
    accepts({"op": "start", "server": "b"}, on("b", NOTE), on("b", {"op": "tools_list"}), {"op": "restart", "server": "b"},
            {"op": "stop", "server": "b"}, {"op": "stop"}, {"op": "start"})
    accepts({"op": "repeat", "count": 2, "var": "i", "steps": [{"op": "restart", "server": "main"}]})


@test
def servers_rejected_when_malformed():
    rejects("server is a lower-case name", {"op": "start", "server": "B"})
    rejects("server is a lower-case name", on(7, NOTE))
    rejects("already running", {"op": "start"})
    rejects("already running", {"op": "start", "server": "b"}, {"op": "start", "server": "b"})
    rejects("server b is not running", on("b", NOTE))
    rejects("server b is not running", {"op": "stop", "server": "b"})
    rejects("server main is not running", {"op": "stop"}, NOTE)
    rejects("server main is not running", {"op": "stop"}, {"op": "stop"})
    rejects("same servers running", {"op": "repeat", "count": 2, "var": "i", "steps": [{"op": "start", "server": "b"}]})
    rejects("unknown key", {"op": "start", "server": "b", "env": {}})


@test
def servers_run_side_by_side():
    plays({"op": "start", "server": "b"}, NOTE, on("b", NOTE), NOTE,
          {"op": "fs", "assert": [{"path": "calls.log", "matches": r"\A(note_prompt ${pid}\n)(note_prompt ${pid_b}\n)(note_prompt ${pid}\n)\Z"}]},
          {"op": "stop", "server": "b"}, {"op": "start", "server": "b"}, {"op": "restart", "server": "b"})


@test
def servers_a_stopped_one_cannot_be_called():
    fails("not running", {"op": "start", "server": "b"}, {"op": "stop", "server": "b"}, on("b", NOTE))
    fails("already running", {"op": "start", "server": "b"}, {"op": "start", "server": "b"})


# ---------------------------------------------------------------- per-event journal assertions

def journal_asserts(**kw):
    return {"op": "journal", "action": "assert_events", **kw}


def assert_events_on(lines, **kw):
    """Run an assert_events step on a journal given as lines (no server involved)."""
    play()  # sets up the scratch directory
    r = run.Run(FAKE, scen(), TOOLS, VALIDATOR)
    r.jl["main"] = [json.dumps(e) for e in lines]
    r.assert_events(journal_asserts(**kw))


EVS = [{"v": 2, "seq": 1, "kind": "start"}, {"v": 2, "seq": 2, "kind": "item", "id": "q1"},
       {"v": 2, "seq": 3, "kind": "item", "id": "q2"}, {"v": 2, "seq": 4, "kind": "end"}]


@test
def per_event_assertions_parsed():
    accepts(BEGIN, journal_asserts(at={"1": {"kind": "start"}, "-1": {"kind": "end"}}, each={"item": {"v": 2}, "*": {"v": 2}}))


@test
def per_event_assertions_rejected_when_malformed():
    rejects("non-empty object keyed by position", BEGIN, journal_asserts(at={}))
    rejects("non-zero integer", BEGIN, journal_asserts(at={"0": {"kind": "start"}}))
    rejects("non-zero integer", BEGIN, journal_asserts(at={"first": {"kind": "start"}}))
    rejects("non-empty object of fields", BEGIN, journal_asserts(at={"1": {}}))
    rejects("non-empty object keyed by kind", BEGIN, journal_asserts(each=[]))
    rejects("unknown journal kind", BEGIN, journal_asserts(each={"nope": {"v": 2}}))
    rejects("non-empty object of fields", BEGIN, journal_asserts(each={"item": "x"}))
    rejects("asserts nothing", BEGIN, journal_asserts())


@test
def per_event_assertions_run():
    assert_events_on(EVS, at={"1": {"kind": "start"}, "3": {"id": "q2"}, "-1": {"kind": "end"}, "-2": {"id": "q2"}},
                     each={"item": {"v": 2}, "*": {"v": 2}})
    for kw, frag in (({"at": {"2": {"id": "q2"}}}, "event 2 is"), ({"at": {"5": {"kind": "end"}}}, "none at position 5"),
                     ({"at": {"-5": {"kind": "end"}}}, "none at position -5"), ({"each": {"item": {"id": "q1"}}}, "does not match"),
                     ({"each": {"park": {"v": 2}}}, "no park event")):
        try:
            assert_events_on(EVS, **kw)
        except AssertionError as e:
            assert frag in str(e), "wrong failure for %s: %s" % (kw, e)
        else:
            raise AssertionError("%s should have failed" % kw)


def main():
    if "--fake" in sys.argv:
        return fake_server()
    nfail = 0
    for f in TESTS:
        try:
            f()
            print("ok - %s" % f.__name__)
        except Exception as e:
            nfail += 1
            print("not ok - %s: %s: %s" % (f.__name__, type(e).__name__, e))
    print("# %d/%d passed" % (len(TESTS) - nfail, len(TESTS)))
    if PLAY_DIR and not os.listdir(PLAY_DIR):
        os.rmdir(PLAY_DIR)
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
