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
            if os.environ.get("FAKE_STDERR"):
                sys.stderr.write(os.environ["FAKE_STDERR"] + "\n")
                sys.stderr.flush()
            if os.environ.get("FAKE_PROJECT_FILE"):  # a stand-in for the decision log, below the project path
                path = os.path.join(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(), os.environ["FAKE_PROJECT_FILE"])
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "w").write("hello\n")
            with open(os.path.join(data, "meta.log"), "a") as f:
                f.write(json.dumps(m["params"].get("_meta", "none"), sort_keys=True) + "\n")
            res = {"content": [{"type": "text", "text": os.environ.get("FAKE_TEXT", "")}]}
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

BEGIN = {"call": "begin_interview", "args": {"tool_schema": 4, "skill": "grill-me", "mode": "register"}}


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


# ---------------------------------------------------------------- fs root (decision log below the project path)

@test
def fs_root_parsed():
    accepts({"op": "fs", "assert": [{"path": "decision-logs/d.md", "root": "project", "mode": "600"},
                                    {"path": "x", "root": "data", "exists": False}]})


@test
def fs_root_rejected_when_malformed():
    rejects("fs root is one of", {"op": "fs", "assert": [{"path": "a", "root": "home"}]})
    rejects("fs root is one of", {"op": "fs", "assert": [{"path": "a", "root": None}]})
    rejects("relative to its root", {"op": "fs", "assert": [{"path": "/etc/passwd", "root": "project"}]})
    rejects("relative to its root", {"op": "fs", "assert": [{"path": "a/../../b"}]})
    rejects("unknown fs assert key", {"op": "fs", "assert": [{"path": "a", "base": "project"}]})


@test
def fs_root_selects_the_directory():
    file = {"FAKE_PROJECT_FILE": "decision-logs/d.md"}
    plays(NOTE, {"op": "fs", "assert": [{"path": "decision-logs/d.md", "root": "project", "matches": "^hello"},
                                        {"path": "decision-logs/d.md", "exists": False},
                                        {"path": "decision-logs/d.md", "root": "data", "exists": False}]}, env=file)
    # no CLAUDE_PROJECT_DIR: the project path is the working directory
    plays(NOTE, {"op": "fs", "assert": [{"path": "decision-logs/d.md", "root": "project", "nonempty": True}]},
          env=file, unset_env=["CLAUDE_PROJECT_DIR"])
    fails("missing", NOTE, {"op": "fs", "assert": [{"path": "decision-logs/d.md", "root": "project"}]})


# ---------------------------------------------------------------- seeding files

@test
def seed_parsed():
    accepts({"op": "seed", "path": "$key/x.pid", "json": {"pid": "$dead_pid", "start": None, "interview_id": "x"}},
            {"op": "seed", "path": "$key/x.jsonl", "lines": [{"v": 2, "seq": 1, "kind": "start"}, "not json"], "mode": "0644"},
            {"op": "seed", "path": "a.txt", "content": "text\n", "root": "project"},
            {"op": "seed", "path": ".scratch", "mkdir": True, "root": "project"})


@test
def seed_rejected_when_malformed():
    rejects("exactly one of content, lines, json and mkdir", {"op": "seed", "path": "a"})
    rejects("exactly one of content, lines, json and mkdir", {"op": "seed", "path": "a", "content": "", "json": {}})
    rejects("seed needs a path", {"op": "seed", "content": "x"})
    rejects("seed needs a path", {"op": "seed", "path": "", "content": "x"})
    rejects("stays inside", {"op": "seed", "path": "../a", "content": "x"})
    rejects("stays inside", {"op": "seed", "path": "/tmp/a", "content": "x"})
    rejects("fs root is one of", {"op": "seed", "path": "a", "content": "x", "root": "tmp"})
    rejects("content is a string", {"op": "seed", "path": "a", "content": 3})
    rejects("lines is a list", {"op": "seed", "path": "a", "lines": [1]})
    rejects("lines is a list", {"op": "seed", "path": "a", "lines": "x"})
    rejects("json is an object", {"op": "seed", "path": "a", "json": []})
    rejects("mkdir is true", {"op": "seed", "path": "a", "mkdir": False})
    rejects("octal string", {"op": "seed", "path": "a", "content": "x", "mode": 600})
    rejects("octal string", {"op": "seed", "path": "a", "content": "x", "mode": "999"})
    rejects("unknown key", {"op": "seed", "path": "a", "content": "x", "exists": True})


@test
def seed_writes_what_it_says():
    plays({"op": "seed", "path": "$key/i1.pid", "json": {"pid": "$dead_pid", "start": None, "interview_id": "i1"}},
          {"op": "seed", "path": "$key/i1.jsonl", "lines": [{"kind": "start", "n": "${pid}"}, "raw", {"s": "$start"}], "mode": "640"},
          {"op": "seed", "path": "$key/archive", "mkdir": True},
          {"op": "seed", "path": "docs/a.txt", "content": "one\n", "root": "project"},
          {"op": "seed", "path": "docs/a.txt", "content": "two ${pid}\n", "root": "project"},
          {"op": "fs", "assert": [
              {"path": "$key/i1.pid", "mode": "600", "matches": r'\A\{"pid": ${dead_pid}, "start": null, "interview_id": "i1"\}\n\Z'},
              {"path": "$key/i1.jsonl", "mode": "640", "matches": r'\A\{"kind": "start", "n": "${pid}"\}\nraw\n\{"s": \d+\}\n\Z'},
              {"path": "$key/archive", "mode": "700"},
              {"path": "$key", "mode": "700"},
              {"path": "docs/a.txt", "root": "project", "matches": r"\Atwo ${pid}\n\Z"}]})


@test
def seed_dead_pid_is_not_running():
    plays({"op": "seed", "path": "p", "content": "${dead_pid}"},
          {"op": "fs", "assert": [{"path": "p", "matches": r"\A\d+\Z"}]})
    r_pid = run.proc_start(os.getpid())
    assert r_pid is None or r_pid > 0
    assert run.proc_start(2 ** 22 + 12345) is None


# ---------------------------------------------------------------- stderr assertions

@test
def stderr_parsed():
    accepts({"op": "stderr", "assert": [{"empty": True}, {"empty": False}, {"contains": "x"}, {"not_contains": "y"},
                                        {"matches": r"^warn \d+$"}, {"not_matches": "Traceback"}]})
    accepts({"op": "stderr", "assert": [{"contains": "pid ${pid}"}]})


@test
def stderr_rejected_when_malformed():
    rejects("non-empty assert list", {"op": "stderr"})
    rejects("non-empty assert list", {"op": "stderr", "assert": []})
    rejects("exactly one operator", {"op": "stderr", "assert": [{}]})
    rejects("exactly one operator", {"op": "stderr", "assert": [{"contains": "a", "empty": False}]})
    rejects("exactly one operator", {"op": "stderr", "assert": [{"eq": "a"}]})
    rejects("exactly one operator", {"op": "stderr", "assert": ["a"]})
    rejects("empty needs a boolean", {"op": "stderr", "assert": [{"empty": "yes"}]})
    rejects("contains needs a string", {"op": "stderr", "assert": [{"contains": 3}]})
    rejects("bad regex", {"op": "stderr", "assert": [{"matches": "("}]})
    rejects("bad regex", {"op": "stderr", "assert": [{"not_matches": "["}]})
    rejects("unknown key", {"op": "stderr", "assert": [{"empty": True}], "server": "main"})


@test
def stderr_runs():
    plays({"op": "stderr", "assert": [{"empty": True}]}, NOTE,
          {"op": "stderr", "assert": [{"empty": False}, {"contains": "boom 1"}, {"not_contains": "boom 2"},
                                      {"matches": r"^boom \d$"}, {"not_matches": "Traceback"}]}, env={"FAKE_STDERR": "boom 1"})
    plays({"op": "stderr", "assert": [{"empty": True}]}, NOTE, {"op": "stderr", "assert": [{"empty": True}]})
    fails("stderr is empty", NOTE, {"op": "stderr", "assert": [{"empty": False}]})
    for bad in ({"contains": "boom 2"}, {"not_contains": "boom"}, {"matches": "^x"}, {"not_matches": "boom"}):
        fails("stderr", NOTE, {"op": "stderr", "assert": [bad]}, env={"FAKE_STDERR": "boom 1"})
    fails("stderr is not empty", NOTE, {"op": "stderr", "assert": [{"empty": True}]}, env={"FAKE_STDERR": "boom 1"})


# ---------------------------------------------------------------- mtimes

DAY = 86400


@test
def mtimes_parsed():
    accepts({"op": "utime", "path": "$key/i1.pid", "age_seconds": 31 * DAY},
            {"op": "utime", "path": "decision-logs", "root": "project", "age_seconds": 0.5},
            {"op": "fs", "assert": [{"path": "a", "age_ge": 3600}, {"path": "b", "age_le": 5.5}, {"path": "c", "age_ge": 1, "age_le": 9}]})


@test
def mtimes_rejected_when_malformed():
    rejects("utime needs age_seconds", {"op": "utime", "path": "a"})
    rejects("utime needs age_seconds", {"op": "utime", "path": "a", "age_seconds": -1})
    rejects("utime needs age_seconds", {"op": "utime", "path": "a", "age_seconds": "31d"})
    rejects("utime needs age_seconds", {"op": "utime", "path": "a", "age_seconds": True})
    rejects("utime needs a path", {"op": "utime", "age_seconds": 5})
    rejects("stays inside", {"op": "utime", "path": "../a", "age_seconds": 5})
    rejects("unknown key", {"op": "utime", "path": "a", "age_seconds": 5, "mtime": 1})
    rejects("fs age_ge is a number", {"op": "fs", "assert": [{"path": "a", "age_ge": -1}]})
    rejects("fs age_le is a number", {"op": "fs", "assert": [{"path": "a", "age_le": "1h"}]})
    rejects("age applies to a file that exists", {"op": "fs", "assert": [{"path": "a", "exists": False, "age_le": 1}]})


@test
def mtimes_run():
    plays({"op": "seed", "path": "old.txt", "content": "x"}, {"op": "seed", "path": "new.txt", "content": "x"},
          {"op": "fs", "assert": [{"path": "old.txt", "age_le": 30}]},
          {"op": "utime", "path": "old.txt", "age_seconds": 31 * DAY},
          {"op": "fs", "assert": [{"path": "old.txt", "age_ge": 30 * DAY}, {"path": "new.txt", "age_le": 30},
                                  {"path": "new.txt", "age_ge": 0}]})
    plays({"op": "seed", "path": "d", "mkdir": True, "root": "project"},
          {"op": "utime", "path": "d", "root": "project", "age_seconds": 7200},
          {"op": "fs", "assert": [{"path": "d", "root": "project", "age_ge": 3600, "age_le": 7300}]})
    fails("was modified", {"op": "seed", "path": "f", "content": "x"}, {"op": "utime", "path": "f", "age_seconds": 100},
          {"op": "fs", "assert": [{"path": "f", "age_le": 50}]})
    fails("was modified", {"op": "seed", "path": "f", "content": "x"}, {"op": "fs", "assert": [{"path": "f", "age_ge": 3600}]})
    fails("does not exist", {"op": "utime", "path": "nothing", "age_seconds": 5})


@test
def measure_start_parsed_and_enforced():
    stop, ms = {"op": "stop"}, {"op": "measure_start", "starts": 3, "settle_seconds": 0.1}
    accepts(stop, ms, {"op": "measure_start"})
    rejects("needs every server stopped", ms)
    rejects("starts is an integer", stop, {"op": "measure_start", "starts": 1})
    rejects("starts is an integer", stop, {"op": "measure_start", "starts": True})
    rejects("settle_seconds", stop, {"op": "measure_start", "settle_seconds": 0})
    rejects("unknown key", stop, {"op": "measure_start", "runs": 3})
    plays(stop, ms)
    old = run.START_MAX_SECONDS
    run.START_MAX_SECONDS = 0.0
    try:
        fails("REQ-90", stop, ms)
    finally:
        run.START_MAX_SECONDS = old
    old = run.IDLE_RSS_MAX_KIB
    run.IDLE_RSS_MAX_KIB = 1
    try:
        fails("idle RSS", stop, ms)
    finally:
        run.IDLE_RSS_MAX_KIB = old


# ---------------------------------------------------------------- SIGKILL

@test
def kill_parsed():
    accepts({"op": "kill"}, {"op": "start"})
    accepts({"op": "start", "server": "b"}, {"op": "kill", "server": "b"}, {"op": "start", "server": "b"})


@test
def kill_rejected_when_malformed():
    rejects("server b is not running", {"op": "kill", "server": "b"})
    rejects("server main is not running", {"op": "kill"}, {"op": "kill"})
    rejects("server main is not running", {"op": "kill"}, NOTE)
    rejects("unknown key", {"op": "kill", "signal": 9})
    rejects("server is a lower-case name", {"op": "kill", "server": "Main"})


@test
def kill_runs_without_a_graceful_exit():
    # a server that ignores stdin EOF would fail a stop; a kill does not wait for it, and the name can start again
    plays({"op": "kill"}, {"op": "start"}, NOTE,
          {"op": "fs", "assert": [{"path": "calls.log", "matches": r"\Anote_prompt ${pid}\n\Z"}]})
    fails("not running", {"op": "kill"}, NOTE)
    fails("not running", {"op": "kill"}, {"op": "stop"})


# ---------------------------------------------------------------- explicit _meta

@test
def meta_object_parsed():
    accepts({"call": "note_prompt", "args": {"session_id": "s", "prompt": "p"}, "meta": {"progressToken": "x"}})
    accepts({"call": "note_prompt", "args": {"session_id": "s", "prompt": "p"}, "meta": {}})
    accepts({"call": "note_prompt", "args": {"session_id": "s", "prompt": "p"}, "meta": {"claudecode/toolUseId": ""},
             "expect": "error"})
    accepts({"call": "status", "args": {}, "meta": {"claudecode/toolUseId": None}})


@test
def meta_object_rejected_when_malformed():
    rejects("must expect error", {"call": "note_prompt", "args": {"session_id": "s", "prompt": "p"},
                                  "meta": {"claudecode/toolUseId": ""}})
    rejects("meta must be", {"call": "status", "args": {}, "meta": "x"})
    rejects("meta must be", {"call": "status", "args": {}, "meta": [1]})
    rejects("must be a scalar", {"call": "status", "args": {}, "meta": {"a": {"b": 1}}})


@test
def meta_object_is_sent_as_given():
    plays({"call": "note_prompt", "args": {"session_id": "s", "prompt": "p"}, "meta": {"progressToken": "x"}},
          {"call": "note_prompt", "args": {"session_id": "s", "prompt": "p"}, "meta": {}},
          {"call": "note_prompt", "args": {"session_id": "s", "prompt": "p"}},
          {"op": "fs", "assert": [{"path": "meta.log", "matches": r'\A\{"progressToken": "x"\}\n\{\}\n"none"\n\Z'}]})


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


# ---------------------------------------------------------------- launcher and strip_path

@test
def launcher_keys_parsed():
    accepts(NOTE, launcher="plugin/bin/register-launch")
    accepts(NOTE, launcher="plugin/bin/register-launch", strip_path="cat")
    accepts(NOTE, launcher="l.sh", strip_path="my-bin_1.x")


@test
def launcher_keys_rejected_when_malformed():
    for bad in ("", "/abs/launch", "../launch", "a/../b", "a//b", "a/", "a b", 3, None, ["x"]):
        rejects("launcher must be", NOTE, launcher=bad)
    for bad in ("", "a/b", "/cat", "-x", "a b", 3, None):
        rejects("strip_path must be", NOTE, launcher="l.sh", strip_path=bad)
    rejects("strip_path needs launcher", NOTE, strip_path="cat")


@test
def launcher_absent_is_pending_not_failure():
    sc = scen(NOTE, launcher="no/such/launcher")
    assert "pending P1" in run.pending_reason(sc), run.pending_reason(sc)
    assert run.pending_reason(scen(NOTE)) is None
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        assert run.main(["--only", "S-A1", "--validate"]) == 0


def with_launcher(body, f):
    old = run.LAUNCHER_ROOT
    d = tempfile.mkdtemp(prefix="launcher-", dir=run.SCRATCH if os.path.isdir(run.SCRATCH) else None)
    try:
        run.LAUNCHER_ROOT = d
        path = os.path.join(d, "launch.sh")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n" + body)
        os.chmod(path, 0o755)
        f()
    finally:
        run.LAUNCHER_ROOT = old
        shutil.rmtree(d, ignore_errors=True)


LAUNCH_BODY = ('if command -v cat >/dev/null 2>&1; then echo present; else echo absent; fi > "$CLAUDE_PLUGIN_DATA/path.txt"\n'
               'exec "%s" -I "%s" --fake\n' % (sys.executable, os.path.abspath(__file__)))


@test
def launcher_starts_instead_of_the_command():
    def go():
        assert run.pending_reason(scen(NOTE, launcher="launch.sh")) is None
        # the command after `--` (an exit-at-once program) is ignored: the launcher's server answers
        ok, detail = run.run_scenario([sys.executable, "-c", "pass"], scen(NOTE, {"op": "fs", "assert": [
            {"path": "path.txt", "root": "data", "matches": "present"}]}, launcher="launch.sh"), TOOLS, VALIDATOR)
        shutil.rmtree(os.path.join(run.RUN_DIR, "T"), ignore_errors=True)
        assert ok, detail
    global PLAY_DIR
    if PLAY_DIR is None:
        os.makedirs(run.SCRATCH, exist_ok=True)
        PLAY_DIR = run.RUN_DIR = tempfile.mkdtemp(prefix="selftest-", dir=run.SCRATCH)
    with_launcher(LAUNCH_BODY, go)


@test
def strip_path_removes_only_the_named_binary():
    if not shutil.which("cat") or not shutil.which("ls"):
        return
    farm = tempfile.mkdtemp(prefix="farm-", dir=run.SCRATCH if os.path.isdir(run.SCRATCH) else None)
    try:
        p = run.stripped_path("cat", os.environ["PATH"], farm)
        assert shutil.which("cat", path=p) is None, shutil.which("cat", path=p)
        assert shutil.which("ls", path=p), "other programs must stay reachable"
        assert shutil.which("cat", path=os.environ["PATH"]), "the original PATH is untouched"
    finally:
        shutil.rmtree(farm, ignore_errors=True)


@test
def strip_path_reaches_the_launcher():
    def go():
        sc = scen(NOTE, {"op": "fs", "assert": [{"path": "path.txt", "root": "data", "matches": "absent"}]},
                  launcher="launch.sh", strip_path="cat")
        ok, detail = run.run_scenario([sys.executable, "-c", "pass"], sc, TOOLS, VALIDATOR)
        shutil.rmtree(os.path.join(run.RUN_DIR, "T"), ignore_errors=True)
        assert ok, detail
    global PLAY_DIR
    if PLAY_DIR is None:
        os.makedirs(run.SCRATCH, exist_ok=True)
        PLAY_DIR = run.RUN_DIR = tempfile.mkdtemp(prefix="selftest-", dir=run.SCRATCH)
    with_launcher(LAUNCH_BODY, go)


STATUS = {"call": "status", "args": {}}


@test
def stub_step_parsed():
    accepts(dict(STATUS, stub=True), launcher="l.sh")
    rejects("stub steps need launcher", dict(STATUS, stub=True))
    for bad in (False, "yes", 1, None):
        rejects("stub is true", dict(STATUS, stub=bad), launcher="l.sh")
    rejects("stub is true", {"call": "status", "args": {}, "expect": "error", "stub": True}, launcher="l.sh")


@test
def stub_step_skips_the_result_schema():
    stub = {"call": "status", "args": {}, "stub": True, "assert": [{"path": "unavailable", "eq": "binary not found"}]}
    env = {"FAKE_TEXT": '{"unavailable":"binary not found"}'}
    fails("violates tools.json", STATUS, env=env)  # without the key the status schema applies
    def go():
        ok, detail = run.run_scenario([sys.executable, "-c", "pass"], scen(stub, launcher="launch.sh", env=env), TOOLS, VALIDATOR)
        shutil.rmtree(os.path.join(run.RUN_DIR, "T"), ignore_errors=True)
        assert ok, detail
        ok, detail = run.run_scenario([sys.executable, "-c", "pass"], scen(dict(stub, **{"assert": [{"path": "unavailable", "eq": "x"}]}),
                                      launcher="launch.sh", env=env), TOOLS, VALIDATOR)
        shutil.rmtree(os.path.join(run.RUN_DIR, "T"), ignore_errors=True)
        assert not ok, "a wrong stub value must fail"
    with_launcher(LAUNCH_BODY, go)


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
    if PLAY_DIR:
        shutil.rmtree(PLAY_DIR, ignore_errors=True)  # every scenario here is disposable
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
