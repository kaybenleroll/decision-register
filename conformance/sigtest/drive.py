"""Shared helpers and the single-run driver of the signal-shutdown evidence tooling (issue #90, docs/evidence/signal-shutdown.md).

Measurement tooling, not part of the conformance suite (CI does not run it). Standard library only.
Usage: python3 -I conformance/sigtest/drive.py <rust|ts> <unflushed|flushed> <EOF|SIGINT|SIGTERM|SIGHUP>
Builds are the release binary impl/rust/target/release/register-rs and the bundle impl/ts/dist/register.mjs; override with the
environment variables REGISTER_RS_BIN and REGISTER_TS_BUNDLE. Output goes below $SIGTEST_OUT (default <repo>/.scratch/sigtest).
Sequence of one run: note_prompt, begin_interview, log_item q1 and q2, resolve q1, [flushed: check_stop], then EOF or the signal;
then a snapshot of the files, then a fresh server on the same data: status, resume, status."""
import glob, json, os, queue, shutil, signal, subprocess, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RS_BIN = os.environ.get("REGISTER_RS_BIN", os.path.join(ROOT, "impl/rust/target/release/register-rs"))
TS_BUNDLE = os.environ.get("REGISTER_TS_BUNDLE", os.path.join(ROOT, "impl/ts/dist/register.mjs"))
BASE = os.environ.get("SIGTEST_OUT", os.path.join(ROOT, ".scratch", "sigtest"))
LAUNCHER = os.path.join(ROOT, "plugin", "bin", "register-launch")


def command(build):
    """argv of the server for a build, started directly (no launcher)."""
    return [RS_BIN] if build == "rust" else [shutil.which("node") or "node", TS_BUNDLE]


def server_env(data, proj):
    env = dict(os.environ, CLAUDE_PLUGIN_DATA=data, CLAUDE_PROJECT_DIR=proj)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return env


def init_msg(i=1):
    return {"jsonrpc": "2.0", "id": i, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "sigtest", "version": "0"}}}


def call_msg(i, name, args, meta=True):
    p = {"name": name, "arguments": args}
    if meta:
        p["_meta"] = {}
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": p}


class S:
    """A server started through plugin/bin/register-launch, as the plugin starts it (the launcher execs: the child is the server)."""

    def __init__(s, build, data, proj, err):
        env = server_env(data, proj)
        if build == "rust":
            env["REGISTER_IMPL"] = "rs"
            env["PATH"] = os.path.dirname(RS_BIN) + ":" + env["PATH"]
        else:
            env["REGISTER_IMPL"] = "ts"
            env["REGISTER_TS_BUNDLE"] = TS_BUNDLE
        s.p = subprocess.Popen([LAUNCHER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(err, "ab"), env=env,
                               cwd=proj, encoding="utf-8", bufsize=1)
        s.q = queue.Queue()
        s.n = 0
        threading.Thread(target=s._rd, daemon=True).start()
        s.rpc("initialize", init_msg()["params"])
        s._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _rd(s):
        for line in s.p.stdout:
            s.q.put(line)
        s.q.put(None)

    def _send(s, o):
        s.p.stdin.write(json.dumps(o) + "\n")
        s.p.stdin.flush()

    def rpc(s, method, params):
        s.n += 1
        i = s.n
        s._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        while True:
            line = s.q.get(timeout=10)
            if line is None:
                raise RuntimeError("exited")
            x = json.loads(line)
            if x.get("id") == i:
                return x

    def call(s, name, args, meta=True):
        p = {"name": name, "arguments": args}
        if meta:
            p["_meta"] = {}
        r = s.rpc("tools/call", p)["result"]
        t = "".join(c.get("text", "") for c in r["content"])
        if r.get("isError"):
            raise RuntimeError(name + ": " + t)
        return json.loads(t) if t else {}


def files_below(root, rel):
    return [os.path.relpath(os.path.join(a, f), rel) for a, _, fs in os.walk(root) for f in fs]


def run(build, variant, sig, base=None):
    base = base or BASE
    name = "%s-%s-%s" % (build, variant, sig)
    d = os.path.join(base, name)
    data, proj = d + "/data", d + "/project"
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(data)
    os.makedirs(proj + "/.scratch")
    s = S(build, data, proj, d + "/stderr.log")
    assert os.readlink("/proc/%d/cwd" % s.p.pid).startswith(d), "not the server this run started"
    cmd = open("/proc/%d/cmdline" % s.p.pid).read().replace("\0", " ")
    sid = "sess-" + name
    s.call("note_prompt", {"session_id": sid, "prompt": "grill me"}, meta=False)
    s.call("begin_interview", {"tool_schema": 4, "skill": "grill-me", "mode": "register"})
    s.call("log_item", {"id": "q1", "kind": "question", "text": "first question"})
    s.call("log_item", {"id": "q2", "kind": "question", "text": "second question"})
    s.call("resolve", {"id": "q1", "answer": "ans one", "source": "model", "rationale": "test"})
    if variant == "flushed":
        s.call("check_stop", {"session_id": sid, "last_assistant_message": "working"}, meta=False)
    time.sleep(0.3)
    if sig == "EOF":
        s.p.stdin.close()
    else:
        os.kill(s.p.pid, getattr(signal, sig))  # the pid is the child this script started
    try:
        rc = s.p.wait(timeout=10)
    except Exception:
        rc = "HUNG"
        s.p.kill()
        s.p.wait()
    res = {"run": name, "cmd": cmd, "rc": rc}
    js = glob.glob(data + "/**/*.jsonl", recursive=True)
    if js:
        raw = open(js[0]).read()
        ev = [json.loads(line) for line in raw.split("\n") if line.strip()]
        res.update(lines=len(ev), ends_nl=raw.endswith("\n"), last=(ev[-1].get("seq"), ev[-1].get("kind")),
                   kinds=[e["kind"] for e in ev])
    res["pid_files"] = glob.glob(data + "/**/*.pid", recursive=True)
    res["declog"] = files_below(proj, d)  # read before the second server starts: its own exit writes a log
    s2 = S(build, data, proj, d + "/stderr2.log")
    st = s2.call("status", {})
    res["status1"] = {k: st.get(k) for k in ("state", "resumable")}
    try:
        s2.call("resume", {})
        st2 = s2.call("status", {})
        res["tree"], res["items"] = st2.get("tree"), st2.get("items")
    except Exception as e:
        res["resume_err"] = str(e)
    s2.p.stdin.close()
    try:
        s2.p.wait(timeout=10)
    except Exception:
        s2.p.kill()
    json.dump(res, open(d + "/result.json", "w"), indent=1, default=str)
    return res


if __name__ == "__main__":
    print(json.dumps(run(*sys.argv[1:4]), indent=1, default=str))
