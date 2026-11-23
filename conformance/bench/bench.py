#!/usr/bin/env python3
"""Performance and robustness comparison of the Rust and TypeScript register servers (stdlib only).

Not part of the conformance suite and not one of the retirement experiment's five changes: it measures, it never
asserts a threshold (REQ-90 is asserted by scenario S-90a). Every section writes <out>/<section>.json; `report`
renders the JSON as markdown tables.

  python3 conformance/bench/bench.py build            # rebuild both implementations in place (as the task asks)
  python3 conformance/bench/bench.py all [--out DIR]  # every measurement below, in order
  python3 conformance/bench/bench.py startup|rss|latency|scaling|robust|artefacts [--out DIR]
  python3 conformance/bench/bench.py report [--out DIR]

Defaults: N=50 cold starts per configuration, N=30 RSS runs per build, 3 server instances x 200 timed calls per
tool, 30 replays per journal size. All data lives under <out>/work (default .scratch/bench/out); nothing is
written outside the repository's .scratch/. The launcher is run exactly as the plugin runs it (plugin/bin/register-
launch, REGISTER_IMPL=rs|ts); `register-rs` is put on PATH through a symlink in <out>/bin, as an installed
binary would be. Fixed corpus, fixed order, no randomness: re-running on an idle machine gives comparable numbers
(not identical ones: the machine adds noise, see the load average recorded in meta.json).
"""
import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import signal
import statistics
import subprocess
import sys
import time

ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "conformance"))
import run as conf  # noqa: E402  (group_rss_kib: the same RSS reader S-90a uses)

LAUNCHER = os.path.join(ROOT, "plugin", "bin", "register-launch")
RS_BIN = os.path.join(ROOT, "impl", "rust", "target", "release", "register-rs")
TS_BUNDLE = os.path.join(ROOT, "impl", "ts", "dist", "register.mjs")
CARGO = os.path.expanduser("~/.cargo/bin/cargo")
IMPLS = ("rs", "ts")
META = {"claudecode/toolUseId": "bench"}
SESSION = "bench-session"
NOW = "2026-11-19T10:00:00Z"


# ---------------------------------------------------------------- helpers

def stats(xs):
    """n, min, median, p95 (nearest rank: the ceil(0.95 n)-th smallest), max; same unit as the input."""
    s = sorted(xs)
    n = len(s)
    return {"n": n, "min": s[0], "median": statistics.median(s), "p95": s[min(n - 1, math.ceil(0.95 * n) - 1)],
            "max": s[-1]}


def mkdirs(*paths):
    for p in paths:
        os.makedirs(p, exist_ok=True)


def fresh(path):
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path


class Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise Timeout("no reply within the timeout")


def command(impl, via):
    """(argv, extra env) for an implementation: through the launcher, or the binary/bundle directly."""
    if via == "launcher":
        return [LAUNCHER], {"REGISTER_IMPL": impl, "PATH": os.path.join(OUT, "bin") + os.pathsep + os.environ["PATH"]}
    return ([RS_BIN] if impl == "rs" else ["node", TS_BUNDLE]), {}


class Client:
    """A minimal newline-delimited JSON-RPC client over a child's stdio. Each call returns its own round-trip time."""

    def __init__(self, impl, via, base, journal_data=None):
        self.dirs = {k: os.path.join(base, k) for k in ("data", "project", "cwd")}
        mkdirs(*self.dirs.values())
        argv, extra = command(impl, via)
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=journal_data or self.dirs["data"], CLAUDE_PROJECT_DIR=self.dirs["project"],
                   **extra)
        for k in ("CLAUDE_CODE_SESSION_ID", "REGISTER_CLOCK_OFFSET_SECONDS"):
            env.pop(k, None)
        self.err_path = os.path.join(base, "stderr.log")
        self.err = open(self.err_path, "wb")
        self.n = 0
        self.t0 = time.perf_counter()
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err, env=env,
                                  cwd=self.dirs["cwd"], start_new_session=True)

    def _send(self, obj):
        self.p.stdin.write((json.dumps(obj) + "\n").encode())
        self.p.stdin.flush()

    def rpc(self, method, params):
        self.n += 1
        i = self.n
        t0 = time.perf_counter()
        self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        signal.alarm(60)
        try:
            while True:
                line = self.p.stdout.readline()
                t1 = time.perf_counter()  # stop the clock before parsing: parse cost is the client's, not the server's
                if not line:
                    raise RuntimeError("server exited")
                m = json.loads(line)
                if m.get("id") == i and ("result" in m or "error" in m):
                    break
        finally:
            signal.alarm(0)
        return m, t1 - t0

    def initialize(self):
        m, dt = self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                        "clientInfo": {"name": "bench", "version": "0"}})
        assert "result" in m, m
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return dt

    def call(self, name, args, hook=False, ok=True):
        """tools/call; hook-style calls carry no _meta. Returns (text, seconds); raises on a tool error unless ok=False."""
        params = {"name": name, "arguments": args}
        if not hook:
            params["_meta"] = META
        m, dt = self.rpc("tools/call", params)
        if "error" in m:
            raise RuntimeError("%s: JSON-RPC error %s" % (name, m["error"]))
        r = m["result"]
        text = "".join(c.get("text", "") for c in r.get("content", []))
        if r.get("isError") and ok:
            raise RuntimeError("%s %s: %s" % (name, json.dumps(args)[:120], text[:200]))
        return text, dt

    def pgid(self):
        return self.p.pid  # start_new_session: the child leads its own group

    def close(self, graceful_wait=10):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.wait(timeout=graceful_wait)
        except Exception:
            pass
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:
            pass
        self.err.close()


def group_hwm_kib(pgid):
    """Sum of VmHWM (peak resident set) over the process group."""
    total = 0
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % d) as f:
                if int(f.read().rsplit(")", 1)[1].split()[2]) != pgid:
                    continue
            with open("/proc/%s/status" % d) as f:
                for line in f:
                    if line.startswith("VmHWM:"):
                        total += int(line.split()[1])
        except (OSError, ValueError, IndexError):
            continue
    return total


def group_threads(pgid):
    total = 0
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % d) as f:
                if int(f.read().rsplit(")", 1)[1].split()[2]) != pgid:
                    continue
            with open("/proc/%s/status" % d) as f:
                for line in f:
                    if line.startswith("Threads:"):
                        total += int(line.split()[1])
        except (OSError, ValueError, IndexError):
            continue
    return total


def item_text(i):
    return "Question %d: which option should we choose for component number %d of the system under design?" % (i, i)


def start_interview(c):
    """The route Claude Code takes: the skill_started hook, then begin_interview (the session id is established first,
    otherwise the first hook with a session id is read as a session change and ends the interview)."""
    c.call("skill_started", {"tool_schema": 5, "session_id": SESSION, "skill": "grill-me"}, hook=True)
    c.call("begin_interview", {"tool_schema": 5, "skill": "grill-me", "mode": "register"})


def log_one(c, i):
    """Ids are server-assigned (Q1, Q2, ...): only that form can be cited by label (classifier.md section 3)."""
    txt, dt = c.call("log_item", {"kind": "question", "text": item_text(i)})
    assert json.loads(txt)["id"] == "Q%d" % i, txt
    return txt, dt


def resolve_one(c, i):
    """A user-sourced answer needs a user prompt first (budget: one answer per prompt); the prompt call is not timed."""
    c.call("note_prompt", {"session_id": SESSION, "prompt": "I choose option B for component %d." % i}, hook=True)
    return c.call("resolve", {"id": "Q%d" % i, "answer": "Option B", "source": "user",
                              "rationale": "The user compared cost and risk and picked option B."})


def note_prompt(c):
    return c.call("note_prompt", {"session_id": SESSION, "prompt": "Please carry on with the next question about the design."},
                  hook=True)


def check_stop(c, label):
    """A single labelled request about an open item: a question turn that concerns it, so the gate allows (no cap is
    spent and no decision log is written), and every call is the same work."""
    txt, dt = c.call("check_stop", {"session_id": SESSION, "last_assistant_message": "%s: which store should we use here?" % label},
                     hook=True)
    assert txt == "{}", "check_stop for %s was not an allow: %s" % (label, txt[:100])
    return txt, dt


def sample_ms(xs):
    return stats([x * 1000 for x in xs])


# ---------------------------------------------------------------- build

def build(args):
    for cmd, cwd in ((["npm", "run", "build"], os.path.join(ROOT, "impl", "ts")),
                     ([CARGO, "build", "--release"], os.path.join(ROOT, "impl", "rust"))):
        r = subprocess.run(cmd, cwd=cwd)
        if r.returncode != 0:
            sys.exit("build failed: %s" % " ".join(cmd))


# ---------------------------------------------------------------- 1. cold start

def startup(args):
    n = args.starts
    configs = [(i, v) for i in IMPLS for v in ("launcher", "direct")]
    res = {"%s-%s" % c: {"init_ms": [], "first_call_ms": []} for c in configs}
    base = fresh(os.path.join(OUT, "work", "startup"))
    for rep in range(n + 3):  # 3 discarded warm-up rounds (page cache, CPU frequency), then n measured rounds
        for k, (impl, via) in enumerate(configs[rep % 4:] + configs[:rep % 4]):  # rotate the order each round
            d = os.path.join(base, "%d-%s-%s" % (rep, impl, via))
            t0 = time.perf_counter()
            c = Client(impl, via, d)
            try:
                c.initialize()
                t_init = time.perf_counter() - t0
                _, t_first = c.call("status", {})
            finally:
                c.close()
            shutil.rmtree(d, ignore_errors=True)
            if rep >= 3:
                r = res["%s-%s" % (impl, via)]
                r["init_ms"].append(t_init * 1000)
                r["first_call_ms"].append(t_first * 1000)
    out = {k: {"init_ms": stats(v["init_ms"]), "first_status_call_ms": stats(v["first_call_ms"]), "raw_init_ms": v["init_ms"]}
           for k, v in res.items()}
    save("startup", out)
    return out


# ---------------------------------------------------------------- 2. RSS

def rss(args):
    n = args.rss_runs
    base = fresh(os.path.join(OUT, "work", "rss"))
    out = {}
    for impl in IMPLS:
        idle, idle_hwm, after, after_hwm, threads = [], [], [], [], []
        for rep in range(n):
            c = Client(impl, "launcher", os.path.join(base, "%s-%d" % (impl, rep)))
            try:
                c.initialize()
                time.sleep(1.0)
                idle.append(conf.group_rss_kib(c.pgid()) / 1024)
                idle_hwm.append(group_hwm_kib(c.pgid()) / 1024)
                threads.append(group_threads(c.pgid()))
                start_interview(c)
                for i in range(1, 26):          # the scripted interview: 25 log_item + 20 resolve = 45 calls (5 stay open)
                    log_one(c, i)
                for i in range(1, 21):
                    resolve_one(c, i)
                c.call("status", {})
                for _ in range(3):
                    note_prompt(c)
                    check_stop(c, "Q21")
                time.sleep(1.0)
                after.append(conf.group_rss_kib(c.pgid()) / 1024)
                after_hwm.append(group_hwm_kib(c.pgid()) / 1024)
            finally:
                c.close()
            shutil.rmtree(os.path.join(base, "%s-%d" % (impl, rep)), ignore_errors=True)
        out[impl] = {"idle_rss_mib": stats(idle), "idle_peak_mib": stats(idle_hwm),
                     "after_interview_rss_mib": stats(after), "after_interview_peak_mib": stats(after_hwm),
                     "threads_idle": stats(threads)}
    save("rss", out)
    return out


# ---------------------------------------------------------------- 3. per-call latency

def latency(args):
    inst, calls, warm = args.instances, args.calls, 30
    base = fresh(os.path.join(OUT, "work", "latency"))
    out = {}
    for impl in IMPLS:
        buckets = {k: [] for k in ("note_prompt", "skill_started", "check_stop", "log_item", "resolve", "status",
                                   "note_prompt_no_interview", "check_stop_no_interview", "skill_started_no_interview")}
        for k in range(inst):
            d = os.path.join(base, "%s-%d" % (impl, k))
            c = Client(impl, "launcher", d)
            try:
                c.initialize()
                # hooks while no interview exists (every prompt in every unrelated session pays this)
                for j in range(warm + calls):
                    _, dt = note_prompt(c)
                    _, dt2 = check_stop(c, "Q1")
                    _, dt3 = c.call("skill_started", {"tool_schema": 5, "session_id": SESSION, "skill": "other-skill"}, hook=True)
                    if j >= warm:
                        buckets["note_prompt_no_interview"].append(dt)
                        buckets["check_stop_no_interview"].append(dt2)
                        buckets["skill_started_no_interview"].append(dt3)
                c.call("skill_started", {"tool_schema": 5, "session_id": SESSION, "skill": "grill-me"}, hook=True)
                c.call("begin_interview", {"tool_schema": 5, "skill": "grill-me", "mode": "register"})
                total = warm + calls
                for i in range(1, total + 31):  # total timed+warm items, plus 30 that stay open
                    _, dt = log_one(c, i)
                    if warm < i <= total:
                        buckets["log_item"].append(dt)
                for i in range(1, total + 1):
                    _, dt = resolve_one(c, i)
                    if i > warm:
                        buckets["resolve"].append(dt)
                for j in range(warm + calls):
                    _, dt = c.call("status", {})
                    if j >= warm:
                        buckets["status"].append(dt)
                for j in range(warm + calls):
                    _, dt = note_prompt(c)
                    _, dt2 = c.call("skill_started", {"tool_schema": 5, "session_id": SESSION, "skill": "grill-me"}, hook=True)
                    _, dt3 = check_stop(c, "Q%d" % (total + 1))
                    if j >= warm:
                        buckets["note_prompt"].append(dt)
                        buckets["skill_started"].append(dt2)
                        buckets["check_stop"].append(dt3)
            finally:
                c.close()
            shutil.rmtree(d, ignore_errors=True)
        out[impl] = {k: dict(sample_ms(v), unit="ms") for k, v in buckets.items()}
    save("latency", out)
    return out


# ---------------------------------------------------------------- 4. scaling

def journal_lines(shape, n):
    """A valid journal of exactly n events (journal_schema 2). churn: 40 items (20 resolved, 20 open) then hook/prompt/
    hook/stop cycles. items: item+resolve pairs (the last 20 items stay open), so the register itself grows."""
    iid = "iv-20261119T100000Z-0000b3nc"
    ev = [{"kind": "start", "interview_id": iid, "route": "hook", "skill": "grill-me", "session_id": SESSION, "implicit": False,
           "prompt_count": 1},
          {"kind": "engage", "via": "begin_interview"}]

    def add_item(i):
        ev.append({"kind": "item", "id": "Q%d" % i, "item_kind": "question", "text": item_text(i), "parent": None,
                   "depends_on": []})

    def add_resolve(i):
        ev.append({"kind": "resolve", "id": "Q%d" % i, "answer": "Option B", "source": "user",
                   "rationale": "The user compared cost and risk and picked option B."})

    if shape == "churn":
        for i in range(1, 41):
            add_item(i)
        for i in range(1, 21):
            add_resolve(i)
        k = 1
        while len(ev) < n:
            ev.append({"kind": "hook", "tool": "note_prompt", "session_id": SESSION})
            ev.append({"kind": "prompt", "session_id": SESSION, "count": k + 1})
            ev.append({"kind": "hook", "tool": "check_stop", "session_id": SESSION})
            ev.append({"kind": "stop", "decision": "allow", "classification": "question", "presented": ["Q21"],
                       "blocks": {"prompt": 0, "finish": 0, "unlogged": 0}})
            k += 1
        ev = ev[:n]
        open_label, items = "Q21", 40
    else:
        pairs = (n - 22) // 2
        for i in range(1, pairs + 1):
            add_item(i)
            add_resolve(i)
        for i in range(pairs + 1, pairs + 21):
            add_item(i)
        open_label, items = "Q%d" % (pairs + 1), pairs + 20
    assert len(ev) == n, (shape, n, len(ev))
    ev = ev[:n]
    lines = []
    for seq, e in enumerate(ev, 1):
        e = dict(e, v=2, ts=NOW, seq=seq)
        lines.append(json.dumps(e, separators=(",", ":")))
    return iid, "\n".join(lines) + "\n", open_label, items


def project_key(path):
    return hashlib.sha256(path.encode()).hexdigest()[:16]


def scaling(args):
    reps = args.replays
    base = fresh(os.path.join(OUT, "work", "scaling"))
    sizes = (1000, 10000, 100000)
    out = {}
    for shape in ("churn", "items"):
        for n in sizes:
            iid, text, open_label, items = journal_lines(shape, n)
            tpl = os.path.join(base, "%s-%d.jsonl" % (shape, n))
            with open(tpl, "w") as f:
                f.write(text)
            size_mib = os.path.getsize(tpl) / 1048576
            for impl in IMPLS:
                replay, rss_after, keep = [], [], None
                row = {"events": n, "journal_mib": size_mib, "shape": shape, "open_label": open_label}
                for rep in range(reps):
                    d = os.path.join(base, "%s-%d-%s-%d" % (shape, n, impl, rep))
                    mkdirs(d)
                    c = Client(impl, "launcher", d)
                    key = os.path.join(c.dirs["data"], project_key(c.dirs["project"]))
                    mkdirs(key)
                    shutil.copy(tpl, os.path.join(key, iid + ".jsonl"))
                    try:
                        c.initialize()
                        txt, dt = c.call("resume", {"id": iid})
                        r = json.loads(txt)
                        assert r.get("last_event") == n, "resume of %s %d on %s: %s" % (shape, n, impl, txt[:200])
                        replay.append(dt)
                        if rep == reps - 1:
                            rss_after.append(conf.group_rss_kib(c.pgid()) / 1024)
                            # warm calls on the resumed 'n'-event register, same server (hook calls use the journal's session)
                            for name, fn in (("status", lambda: c.call("status", {})),
                                             ("check_stop", lambda: check_stop(c, open_label)),
                                             ("note_prompt", lambda: note_prompt(c)),
                                             ("log_item", None)):
                                xs = []
                                if name == "log_item":
                                    for j in range(30):
                                        xs.append(c.call("log_item", {"kind": "question", "text": "added later"})[1])
                                else:
                                    for j in range(30):
                                        txt2, dt2 = fn()
                                        xs.append(dt2)
                                        if name == "status" and j == 0:
                                            row["status_reply_bytes"] = len(txt2)
                                        if name == "check_stop" and j == 0:
                                            row["check_stop_reply"] = txt2[:80]
                                row[name + "_ms"] = sample_ms(xs)
                            row["rss_after_mib"] = conf.group_rss_kib(c.pgid()) / 1024
                            row["peak_rss_mib"] = group_hwm_kib(c.pgid()) / 1024
                    finally:
                        c.close()
                    shutil.rmtree(d, ignore_errors=True)
                row["replay_ms"] = sample_ms(replay)
                row["resume_rss_mib"] = rss_after[0] if rss_after else None
                out["%s-%d-%s" % (shape, n, impl)] = row
            os.remove(tpl)
    save("scaling", out)
    return out


# ---------------------------------------------------------------- 6. robustness

def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def robust(args):
    base = fresh(os.path.join(OUT, "work", "robust"))
    out = {}
    for impl in IMPLS:
        for how in ("SIGINT", "SIGTERM", "SIGHUP", "SIGKILL", "EOF"):
            for flushed in (False, True):
                d = os.path.join(base, "%s-%s-%s" % (impl, how, "flushed" if flushed else "queued"))
                c = Client(impl, "launcher", d)
                try:
                    c.initialize()
                    start_interview(c)
                    for i in range(1, 4):
                        log_one(c, i)
                    if flushed:
                        check_stop(c, "Q1")   # a Stop check flushes the write-behind queue (journal.md "File")
                    c.call("log_item", {"kind": "question", "text": "logged after the flush"})
                    t0 = time.perf_counter()
                    if how == "EOF":
                        c.p.stdin.close()
                    else:
                        os.kill(c.p.pid, getattr(signal, how))
                    try:
                        rc = c.p.wait(timeout=5)
                        exited = True
                    except subprocess.TimeoutExpired:
                        rc, exited = None, False
                    dt = time.perf_counter() - t0
                finally:
                    c.close()
                key = os.path.join(c.dirs["data"], project_key(c.dirs["project"]))
                files = sorted(os.listdir(key)) if os.path.isdir(key) else []
                journals = [os.path.join(key, f) for f in files if f.endswith(".jsonl")]
                lines = len((_read(journals[0]) or "").splitlines()) if journals else None
                logs = []
                for sub in (".scratch", "decision-logs"):
                    p = os.path.join(c.dirs["project"], sub)
                    if os.path.isdir(p):
                        logs += [f for f in os.listdir(p) if f.startswith("decision-")]
                resumed = None
                if how != "EOF":  # can the next server pick the interview up? (the stale pid file must not block it)
                    c2 = Client(impl, "launcher", d)
                    try:
                        c2.initialize()
                        txt, _ = c2.call("resume", {}, ok=False)
                        resumed = txt[:120]
                    finally:
                        c2.close()
                out["%s %s %s" % (impl, how, "after a Stop check" if flushed else "no Stop check")] = {
                    "exited_within_5s": exited, "returncode": rc, "exit_ms": dt * 1000,
                    "events_on_disk": lines,
                    "pid_file_left": any(f.endswith(".pid") for f in files), "decision_log_written": bool(logs), "resume_by_next_server": resumed,
                    "stderr": (_read(c.err_path) or "")[:200]}
                shutil.rmtree(d, ignore_errors=True)
    # a signal the parent had ignored (nohup, `cmd &` in a non-interactive shell) is inherited across exec: does the server reset it?
    inherited = {}
    for impl in IMPLS:
        argv, extra = command(impl, "direct")
        for sig in (signal.SIGHUP, signal.SIGINT):
            d = os.path.join(base, "ign-%s-%s" % (impl, sig.name))
            mkdirs(d)
            p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=d, start_new_session=True,
                                 env=dict(os.environ, CLAUDE_PLUGIN_DATA=d, CLAUDE_PROJECT_DIR=d),
                                 preexec_fn=lambda s=sig: signal.signal(s, signal.SIG_IGN))
            time.sleep(0.5)
            os.kill(p.pid, sig)
            try:
                inherited["%s %s (ignored by the parent)" % (impl, sig.name)] = {"returncode": p.wait(timeout=2)}
            except subprocess.TimeoutExpired:
                inherited["%s %s (ignored by the parent)" % (impl, sig.name)] = {"returncode": None, "note": "still running after 2 s"}
                p.kill()
                p.wait()
    out["inherited_ignored_signal"] = inherited
    # large payload: a 1 MiB and a 16 MiB prompt through a hook call, ten times each
    big = {}
    for impl in IMPLS:
        c = Client(impl, "launcher", os.path.join(base, "big-" + impl))
        try:
            c.initialize()
            start_interview(c)
            for mib in (1, 16):
                xs = [c.call("note_prompt", {"session_id": SESSION, "prompt": "x" * (mib * 1048576)}, hook=True)[1]
                      for _ in range(10)]
                big["%s %d MiB prompt" % (impl, mib)] = sample_ms(xs)
            big["%s rss after big prompts MiB" % impl] = conf.group_rss_kib(c.pgid()) / 1024
        finally:
            c.close()
    out["large_payload"] = big
    save("robust", out)
    return out


# ---------------------------------------------------------------- 5. artefacts

def run_timed(cmd, cwd, env=None):
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return time.perf_counter() - t0, r.returncode


def artefacts(args):
    base = fresh(os.path.join(OUT, "work", "artefacts"))
    out = {}
    # Clean builds happen in copies below <out>/work, so the working tree and its target/ are untouched.
    shutil.copytree(os.path.join(ROOT, "contract"), os.path.join(base, "contract"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    rs = os.path.join(base, "impl", "rust")  # the sources include ../../../contract/*.json
    mkdirs(rs)
    for f in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml"):
        shutil.copy(os.path.join(ROOT, "impl", "rust", f), rs)
    shutil.copytree(os.path.join(ROOT, "impl", "rust", "src"), os.path.join(rs, "src"))
    times = []
    for i in range(3):
        shutil.rmtree(os.path.join(rs, "target"), ignore_errors=True)
        dt, rc = run_timed([CARGO, "build", "--release", "--offline"], rs)
        assert rc == 0, "cargo build failed"
        times.append(dt)
    out["rust_clean_build_s"] = times
    out["rust_binary_bytes"] = os.path.getsize(os.path.join(rs, "target", "release", "register-rs"))
    ldd = subprocess.run(["ldd", os.path.join(rs, "target", "release", "register-rs")], stdout=subprocess.PIPE, text=True).stdout
    out["rust_ldd"] = [l.split()[0] for l in ldd.strip().splitlines()]
    tree = subprocess.run([CARGO, "tree", "--offline", "--prefix", "none", "-e", "normal"], cwd=rs, stdout=subprocess.PIPE,
                          text=True).stdout
    out["rust_crates"] = sorted(set(l.split(" (")[0] for l in tree.strip().splitlines()))
    ts = os.path.join(base, "impl", "ts")
    mkdirs(ts)
    for f in ("package.json", "package-lock.json", "tsconfig.json"):
        shutil.copy(os.path.join(ROOT, "impl", "ts", f), ts)
    shutil.copytree(os.path.join(ROOT, "impl", "ts", "src"), os.path.join(ts, "src"))
    dt, rc = run_timed(["npm", "ci", "--offline", "--no-audit", "--no-fund"], ts)
    assert rc == 0, "npm ci failed"
    out["ts_npm_ci_offline_s"] = dt
    times = []
    for i in range(3):
        shutil.rmtree(os.path.join(ts, "dist"), ignore_errors=True)
        dt, rc = run_timed(["npm", "run", "build"], ts)
        assert rc == 0, "npm run build failed"
        times.append(dt)
    out["ts_clean_bundle_build_s"] = times
    out["ts_bundle_bytes"] = os.path.getsize(os.path.join(ts, "dist", "register.mjs"))
    gz = subprocess.run(["gzip", "-9", "-c", os.path.join(ts, "dist", "register.mjs")], stdout=subprocess.PIPE).stdout
    out["ts_bundle_gzip_bytes"] = len(gz)
    gz = subprocess.run(["gzip", "-9", "-c", os.path.join(rs, "target", "release", "register-rs")], stdout=subprocess.PIPE).stdout
    out["rust_binary_gzip_bytes"] = len(gz)
    du = subprocess.run(["du", "-sb", os.path.join(ts, "node_modules")], stdout=subprocess.PIPE, text=True).stdout.split()[0]
    out["ts_node_modules_bytes_dev_only"] = int(du)
    out["ts_runtime_dependencies"] = json.load(open(os.path.join(ROOT, "impl", "ts", "package.json"))).get("dependencies", {})
    out["ts_engines"] = json.load(open(os.path.join(ROOT, "impl", "ts", "package.json"))).get("engines")
    node = os.path.realpath(shutil.which("node"))
    out["node_binary"] = node
    out["node_binary_bytes"] = os.path.getsize(node)
    out["node_version"] = subprocess.run(["node", "--version"], stdout=subprocess.PIPE, text=True).stdout.strip()
    out["rustc_version"] = subprocess.run([os.path.expanduser("~/.cargo/bin/rustc"), "--version"], cwd=rs,
                                          stdout=subprocess.PIPE, text=True).stdout.strip()
    out["sloc"] = {
        "rust_src_lines": sum(sum(1 for _ in open(os.path.join(dp, f))) for dp, _, fs in os.walk(os.path.join(ROOT, "impl", "rust", "src"))
                              for f in fs if f.endswith(".rs")),
        "ts_src_lines": sum(sum(1 for _ in open(os.path.join(dp, f))) for dp, _, fs in os.walk(os.path.join(ROOT, "impl", "ts", "src"))
                            for f in fs if f.endswith(".ts"))}
    save("artefacts", out)
    return out


# ---------------------------------------------------------------- output

def meta():
    def sh(*cmd):
        try:
            return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout.strip()
        except OSError:
            return "n/a"
    cpu = ""
    with open("/proc/cpuinfo") as f:
        for line in f:
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    return {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "kernel": platform.release(), "cpu": cpu, "logical_cpus": os.cpu_count(),
            "node": sh("node", "--version"), "rustc": sh(os.path.expanduser("~/.cargo/bin/rustc"), "--version"),
            "python": platform.python_version(), "loadavg_start": open("/proc/loadavg").read().strip(),
            "git_head": sh("git", "-C", ROOT, "rev-parse", "--short", "HEAD")}


def save(name, obj):
    with open(os.path.join(OUT, name + ".json"), "w") as f:
        json.dump(obj, f, indent=1)


def f(x, p=2):
    return ("%." + str(p) + "f") % x


def srow(label, s, p=2):
    return "| %s | %s | %s | %s | %s |" % (label, f(s["median"], p), f(s["p95"], p), f(s["max"], p), s["n"])


def report(args):
    def load(n):
        with open(os.path.join(OUT, n + ".json")) as fh:
            return json.load(fh)
    lines = []
    if os.path.exists(os.path.join(OUT, "meta.json")):
        lines += ["```", json.dumps(load("meta"), indent=1), "```", ""]
    if os.path.exists(os.path.join(OUT, "startup.json")):
        s = load("startup")
        lines += ["### Cold start (ms)", "", "| Configuration | Metric | median | p95 | max | n |", "|---|---|---|---|---|---|"]
        for k, v in s.items():
            lines.append("| %s | to `initialize` reply | %s | %s | %s | %d |" % (k, f(v["init_ms"]["median"], 1), f(v["init_ms"]["p95"], 1),
                                                                                 f(v["init_ms"]["max"], 1), v["init_ms"]["n"]))
            w = v["first_status_call_ms"]
            lines.append("| %s | first `status` call after it | %s | %s | %s | %d |" % (k, f(w["median"], 2), f(w["p95"], 2), f(w["max"], 2), w["n"]))
        lines.append("")
    if os.path.exists(os.path.join(OUT, "rss.json")):
        s = load("rss")
        lines += ["### Resident memory (MiB, process group)", "", "| Metric | median | p95 | max | n |", "|---|---|---|---|---|"]
        for impl, v in s.items():
            for k in ("idle_rss_mib", "idle_peak_mib", "after_interview_rss_mib", "after_interview_peak_mib"):
                lines.append(srow("%s %s" % (impl, k), v[k], 1))
            lines.append(srow("%s threads idle" % impl, v["threads_idle"], 0))
        lines.append("")
    if os.path.exists(os.path.join(OUT, "latency.json")):
        s = load("latency")
        lines += ["### Per-call latency, warm (ms, round trip over stdio)", "", "| Tool | median | p95 | max | n |", "|---|---|---|---|---|"]
        for impl, v in s.items():
            for k, st in v.items():
                lines.append(srow("%s %s" % (impl, k), st, 3))
        lines.append("")
    if os.path.exists(os.path.join(OUT, "scaling.json")):
        s = load("scaling")
        lines += ["### Scaling (ms unless stated)", "",
                  "| Shape | Events | Journal MiB | Build | replay median | replay p95 | replay max | status median | status p95 | check_stop median | check_stop p95 | log_item median | note_prompt median | status reply MiB | RSS after resume MiB | peak RSS MiB |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for k, r in s.items():
            lines.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["shape"], r["events"], f(r["journal_mib"], 1), k.rsplit("-", 1)[1], f(r["replay_ms"]["median"], 1),
                f(r["replay_ms"]["p95"], 1), f(r["replay_ms"]["max"], 1), f(r["status_ms"]["median"], 3), f(r["status_ms"]["p95"], 3),
                f(r["check_stop_ms"]["median"], 3), f(r["check_stop_ms"]["p95"], 3), f(r["log_item_ms"]["median"], 3),
                f(r["note_prompt_ms"]["median"], 3), f(r["status_reply_bytes"] / 1048576, 2), f(r["rss_after_mib"], 1), f(r["peak_rss_mib"], 1)))
        lines.append("")
    if os.path.exists(os.path.join(OUT, "robust.json")):
        s = load("robust")
        lines += ["### Robustness", "", "| Case | exited in 5 s | return code | exit ms | events on disk | pid file left | decision log |", "|---|---|---|---|---|---|---|"]
        for k, r in s.items():
            if k in ("large_payload", "inherited_ignored_signal"):
                continue
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (k, r["exited_within_5s"], r["returncode"], f(r["exit_ms"], 1),
                                                                      r["events_on_disk"], r["pid_file_left"], r["decision_log_written"]))
        lines.append("")
        lines += ["Signal ignored by the parent before exec:", ""]
        for k, v in s["inherited_ignored_signal"].items():
            lines.append("- %s: %s" % (k, json.dumps(v)))
        lines += ["", "Large payload (note_prompt, ms):", ""]
        for k, v in s["large_payload"].items():
            lines.append("- %s: %s" % (k, json.dumps(v) if isinstance(v, dict) else f(v, 1)))
        lines.append("")
    if os.path.exists(os.path.join(OUT, "artefacts.json")):
        lines += ["### Artefacts", "", "```", json.dumps(load("artefacts"), indent=1), "```"]
    text = "\n".join(lines)
    with open(os.path.join(OUT, "report.md"), "w") as fh:
        fh.write(text)
    print(text)


SECTIONS = {"startup": startup, "rss": rss, "latency": latency, "scaling": scaling, "robust": robust, "artefacts": artefacts}
OUT = ""


def main(argv):
    global OUT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["build", "all", "report"] + list(SECTIONS))
    ap.add_argument("--out", default=os.path.join(ROOT, ".scratch", "bench", "out"))
    ap.add_argument("--starts", type=int, default=50)
    ap.add_argument("--rss-runs", type=int, default=30)
    ap.add_argument("--instances", type=int, default=3)
    ap.add_argument("--calls", type=int, default=200)
    ap.add_argument("--replays", type=int, default=30)
    args = ap.parse_args(argv)
    for sig in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):  # children must not inherit an ignored signal (background
        signal.signal(sig, signal.SIG_DFL)                      # shells and nohup set SIGINT/SIGHUP to ignored, and Rust keeps it)
    signal.signal(signal.SIGALRM, _alarm)
    OUT = os.path.abspath(args.out)
    mkdirs(OUT, os.path.join(OUT, "bin"))
    link = os.path.join(OUT, "bin", "register-rs")
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(RS_BIN, link)
    if args.what == "build":
        return build(args)
    if args.what == "report":
        return report(args)
    for p in (RS_BIN, TS_BUNDLE):
        if not os.path.exists(p):
            sys.exit("missing %s: run `bench.py build` first" % p)
    todo = list(SECTIONS) if args.what == "all" else [args.what]
    if args.what == "all":
        with open(os.path.join(OUT, "meta.json"), "w") as fh:
            json.dump(meta(), fh, indent=1)
    for name in todo:
        t0 = time.time()
        SECTIONS[name](args)
        print("%s done in %.0f s" % (name, time.time() - t0), flush=True)
    if args.what == "all":
        m = json.load(open(os.path.join(OUT, "meta.json")))
        m["loadavg_end"] = open("/proc/loadavg").read().strip()
        json.dump(m, open(os.path.join(OUT, "meta.json"), "w"), indent=1)
        report(args)


if __name__ == "__main__":
    main(sys.argv[1:])
