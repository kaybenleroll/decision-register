#!/usr/bin/env python3
"""Language-neutral black-box conformance suite driver (REQ-71, REQ-80). Stdlib only.

Usage:
  python3 conformance/run.py -- <server command and args>   run every scenario
  python3 conformance/run.py --only S-A1,S-A3 -- <command>   run selected ids (comma list, fnmatch globs)
  python3 conformance/run.py --list                          list scenario ids and titles
  python3 conformance/run.py --validate                      check scenario files against contract/tools.json
                                                             (no server needed)

Output is TAP-like (`ok N - id title`, `not ok N - id title`, diagnostics as `# ` lines). Exit status is
non-zero on any failure, on a validation error, or when --only matches nothing.

Contract: contract/runtime.md. The suite drives the server over stdio, starts the command directly, sets
CLAUDE_PLUGIN_DATA and CLAUDE_PROJECT_DIR to fresh per-scenario directories under a unique run directory in
.scratch/conformance/ and reads and writes files only below CLAUDE_PLUGIN_DATA. Scenario files:
conformance/scenarios/*.json (format: conformance/README.md).
"""
import fnmatch, glob, hashlib, importlib.util, json, os, queue, re, shutil, signal, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCEN_DIR = os.path.join(HERE, "scenarios")
CONTRACT = os.path.join(ROOT, "contract")
SCRATCH = os.path.join(ROOT, ".scratch", "conformance")
RUN_DIR = None  # unique per invocation (set in main), so concurrent runs never share directories
TIMEOUT = 20
EXIT_WAIT = 5
MODEL_META = {"claudecode/toolUseId": "toolu_conformance"}
TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|\+00:00)$")


FALLBACK_DIR = "decision-register-data"


def project_key(proj):
    """contract/runtime.md: first 16 lowercase hex digits of SHA-256 of the UTF-8 bytes of CLAUDE_PROJECT_DIR."""
    return hashlib.sha256(proj.encode("utf-8")).hexdigest()[:16]


def load_contract():
    with open(os.path.join(CONTRACT, "tools.json"), encoding="utf-8") as f:
        tools = json.load(f)
    with open(os.path.join(CONTRACT, "journal.schema.json"), encoding="utf-8") as f:
        tools["journal"] = json.load(f)  # machine-readable journal.md (kinds, envelope)
    spec = importlib.util.spec_from_file_location("check_contract", os.path.join(CONTRACT, "check_contract.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return tools, mod.check


def load_scenarios():
    out = []
    for p in sorted(glob.glob(os.path.join(SCEN_DIR, "*.json"))):
        with open(p) as f:
            d = json.load(f)
        d["_file"] = os.path.basename(p)
        out.append(d)
    return out


# ---------------------------------------------------------------- server driver

def parse_line(line):
    """Every stdout line of an MCP stdio server must be one JSON-RPC 2.0 message (anything else breaks real clients)."""
    try:
        m = json.loads(line)
    except ValueError:
        raise RuntimeError("non-JSON line on stdout: %r" % line.strip()[:100])
    if not isinstance(m, dict) or m.get("jsonrpc") != "2.0":
        raise RuntimeError("stdout line is not a JSON-RPC 2.0 message: %r" % line.strip()[:100])
    return m


class Server:
    def __init__(self, cmd, env, errpath, cwd):
        self.env = env
        self.errf = open(errpath, "ab")
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errf,
                                  env=env, cwd=cwd, encoding="utf-8", bufsize=1, start_new_session=True)
        self.q = queue.Queue()
        threading.Thread(target=self._rd, daemon=True).start()
        self.n = 0

    def initialize(self):
        m = self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                    "clientInfo": {"name": "conformance", "version": "0"}})
        self.init = m.get("result")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def kill(self):
        """Kill the server's whole process group (a launcher may have children)."""
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:
            pass

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
            m = parse_line(line)
            if m.get("id") == i and ("result" in m or "error" in m):
                return m

    def call(self, name, args, meta):
        """Return (kind, text), kind in ok | tool_error (result.isError) | protocol_error (JSON-RPC error object).
        meta None sends no _meta key at all (hook-style)."""
        params = {"name": name, "arguments": args}
        if meta is not None:
            params["_meta"] = meta
        m = self.rpc("tools/call", params)
        if "error" in m:
            return "protocol_error", json.dumps(m["error"])
        r = m["result"]
        text = "".join(c.get("text", "") for c in r.get("content", []) if isinstance(c, dict))
        return ("tool_error" if r.get("isError") else "ok"), text

    def close(self):
        """Close stdin and wait up to EXIT_WAIT seconds for the server to exit by itself (runtime.md: exits on
        stdin EOF). Always leaves no process behind. Returns True when the exit was graceful."""
        try:
            self.p.stdin.close()
        except Exception:
            pass
        graceful = True
        try:
            self.p.wait(timeout=EXIT_WAIT)
        except Exception:
            graceful = False
        self.kill()
        self.errf.close()
        time.sleep(0.05)  # let the reader thread drain the pipe
        while True:
            try:
                line = self.q.get_nowait()
            except queue.Empty:
                break
            if line is not None:
                parse_line(line)  # stdout noise after the last reply still fails
        return graceful


# ---------------------------------------------------------------- scenario execution

def subst(v, vars_):
    """Replace a whole-string "$name" with the saved variable (any type), and "${name}" inside longer
    strings with its text, recursively."""
    if isinstance(v, str) and v.startswith("$") and v[1:] in vars_:
        return vars_[v[1:]]
    if isinstance(v, str) and "${" in v:
        return re.sub(r"\$\{(\w+)\}", lambda m: str(vars_[m.group(1)]) if m.group(1) in vars_ else m.group(0), v)
    if isinstance(v, list):
        return [subst(x, vars_) for x in v]
    if isinstance(v, dict):
        return {k: subst(x, vars_) for k, x in v.items()}
    return v


def dig(obj, path):
    for part in path.split("."):
        if isinstance(obj, list):
            obj = obj[int(part)]
        else:
            obj = obj[part]
    return obj


IGNORED_SCHEMA_KEYS = ("description", "title", "$schema", "$id", "$defs", "definitions", "examples", "default")


def normalise_schema(s, defs):
    """Canonical form for comparing a server's inputSchema with tools.json: $ref resolved against the given
    definitions, annotation keys dropped, set-like lists (required, enum, oneOf) sorted."""
    while isinstance(s, dict) and "$ref" in s:
        s = defs[s["$ref"].split("/")[-1]]
    if isinstance(s, list):
        return [normalise_schema(x, defs) for x in s]
    if not isinstance(s, dict):
        return s
    out = {}
    for k, v in s.items():
        if k in IGNORED_SCHEMA_KEYS:
            continue
        if k in ("properties", "dependentRequired"):
            v = {pk: normalise_schema(pv, defs) if k == "properties" else sorted(pv) for pk, pv in v.items()}
        else:
            v = normalise_schema(v, defs)
        if k in ("required", "enum", "oneOf") and isinstance(v, list):
            v = sorted(v, key=lambda x: json.dumps(x, sort_keys=True))
        out[k] = v
    return out


def derived_counts(st):
    c = {"open": 0, "resolved": 0, "parked": 0, "stale": 0}
    for it in st["tree"]:
        c[it["status"].lower()] += 1
    return c


def as_list(v):
    return [] if v is None else ([v] if isinstance(v, str) else list(v))


OPERATORS = ("eq", "ne", "contains", "not_contains", "matches", "len", "ge", "absent", "decision_block")


def check_assert(name, a, body, vars_):
    """One assert entry: optional `path` plus exactly one operator (validated by `validate`)."""
    path = a.get("path")
    where = "%s: %s" % (name, path or "result")
    op = [k for k in a if k != "path"][0]
    want = subst(a[op], vars_)
    if op == "decision_block":
        blocked = body.get("decision") == "block"
        if want and not blocked:
            raise AssertionError("%s: not blocked: %s" % (name, json.dumps(body)[:150]))
        if not want and "decision" in body:
            raise AssertionError("%s: a decision was returned: %s" % (name, json.dumps(body)[:150]))
        return "%s decision=%s" % (name, body.get("decision", "none"))
    if op == "absent":
        try:
            val = dig(body, path)
        except (KeyError, IndexError, ValueError):
            return "%s absent" % where
        raise AssertionError("%s should be absent, is %r" % (where, val))
    val = body if path is None else dig(body, path)
    if op == "eq" and val != want:
        raise AssertionError("%s is %r, expected %r" % (where, val, want))
    if op == "ne" and val == want:
        raise AssertionError("%s is %r, expected anything else" % (where, val))
    if op == "contains" and want not in val:
        raise AssertionError("%s lacks %r: %r" % (where, want, str(val)[:150]))
    if op == "not_contains" and want in val:
        raise AssertionError("%s should not contain %r: %r" % (where, want, str(val)[:150]))
    if op == "matches" and not (isinstance(val, str) and re.search(want, val)):
        raise AssertionError("%s does not match /%s/: %r" % (where, want, str(val)[:150]))
    if op == "len" and len(val) != want:
        raise AssertionError("%s has length %d, expected %r" % (where, len(val), want))
    if op == "ge" and not val >= want:
        raise AssertionError("%s is %r, expected at least %r" % (where, val, want))
    return "%s=%s" % (where, json.dumps(val)[:60])


def proc_start(pid):
    """Start time of a process in clock ticks since boot (field 22 of /proc/<pid>/stat), None where unreadable
    (contract/runtime.md, Pid file and claim)."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return int(f.read().rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def repeat_rows(st):
    """The variable bindings of each pass of a `repeat` step: `count` passes with `var` = 1..count, or one pass
    per object of `rows` (each key becomes a variable)."""
    if "count" in st:
        return [{st["var"]: i} for i in range(1, st["count"] + 1)]
    return st["rows"]


class Run:
    def __init__(self, cmd, scen, tools, validator):
        self.cmd, self.scen, self.tools, self.check = cmd, scen, tools, validator
        self.dir = os.path.join(RUN_DIR, scen["id"])
        shutil.rmtree(self.dir, ignore_errors=True)
        self.data = os.path.join(self.dir, "data")
        self.proj = os.path.join(self.dir, "project")
        self.cwd = os.path.join(self.dir, "cwd")  # never the project dir, so a server using cwd for the key is caught
        for d in (self.data, self.proj, self.cwd):
            os.makedirs(d)
        self.vars = {"dir": self.dir, "data": self.data, "proj": self.proj, "cwd": os.path.realpath(self.cwd)}
        self.lines = None
        self.servers = {}  # running server processes by name; the scenario's own server is "main"
        self.env = None
        self.jl = {}  # loaded journals by name
        self.obs = []  # observed values behind each passed assertion, for the TAP detail

    def server(self, name="main"):
        if name not in self.servers:
            raise AssertionError("server %r is not running" % name)
        return self.servers[name]

    def start(self, name="main"):
        if name in self.servers:
            raise AssertionError("server %r is already running" % name)
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=self.data, CLAUDE_PROJECT_DIR=self.proj)
        # the contract's optional variables are set only by the scenarios that test them (runtime.md)
        for k in ("CLAUDE_CODE_SESSION_ID", "REGSPIKE_CLOCK_OFFSET_SECONDS"):
            env.pop(k, None)
        for k, v in self.scen.get("env", {}).items():
            env[k] = v.replace("$dir", self.dir) if isinstance(v, str) else v
        for k in self.scen.get("unset_env", []):
            env.pop(k, None)
        self.env = env
        # contract/runtime.md: project path = CLAUDE_PROJECT_DIR if set and non-empty, else the working directory
        # as the operating system reports it
        self.vars["key"] = project_key(env.get("CLAUDE_PROJECT_DIR") or os.path.realpath(self.cwd))
        srv = Server(self.cmd, env, os.path.join(self.dir, "server.stderr.log"), self.cwd)  # all servers share the log
        self.servers[name] = srv
        srv.initialize()
        suffix = "" if name == "main" else "_" + name
        self.vars["pid" + suffix] = srv.p.pid
        self.vars["start" + suffix] = proc_start(srv.p.pid)

    def stop_server(self, name="main"):
        """Close the named server and require a graceful exit on stdin EOF."""
        srv = self.server(name)
        del self.servers[name]
        if not srv.close():
            raise AssertionError("server %s did not exit within %d s of stdin EOF (runtime.md: exits on stdin EOF)"
                                 % (name, EXIT_WAIT))

    def kill_server(self, name="main"):
        """SIGKILL the named server's process group: no flush, no clean-up, pid file and journal as they were."""
        srv = self.server(name)
        del self.servers[name]
        srv.kill()
        srv.errf.close()

    def journal_path(self, where="active"):
        """Journal path per contract/runtime.md: <data>/<project key>/<id>.jsonl (active) or
        <data>/<project key>/archive/<id>.jsonl (archived); data is the temp fallback when
        CLAUDE_PLUGIN_DATA is unset or empty."""
        parts = [self.root(), self.vars["key"]] + (["archive"] if where == "archive" else []) + [self.vars["iid"] + ".jsonl"]
        p = os.path.join(*parts)
        assert os.path.isfile(p), "%s journal not found at %s" % (where, p)
        return p

    def root(self):
        if self.env.get("CLAUDE_PLUGIN_DATA"):  # unset and empty both mean the temp fallback (runtime.md)
            return self.env["CLAUDE_PLUGIN_DATA"]
        return os.path.join(self.env.get("TMPDIR", "/tmp"), FALLBACK_DIR)

    def do_tools_list(self, st):
        """REQ-71: the initialize reply and the tool list (names, input schemas) match contract/tools.json."""
        srv = self.server(st.get("server", "main"))
        init = srv.init
        assert isinstance(init, dict), "initialize returned no result"
        assert init.get("protocolVersion") == "2024-11-05", "initialize protocolVersion is %r" % init.get("protocolVersion")
        assert isinstance(init.get("capabilities", {}).get("tools"), dict), "initialize lacks capabilities.tools"
        info = init.get("serverInfo")
        assert isinstance(info, dict) and all(isinstance(info.get(k), str) and info[k].strip() for k in ("name", "version")), \
            "initialize serverInfo needs a name and a version"
        tools, cursor = [], None
        while True:
            m = srv.rpc("tools/list", {} if cursor is None else {"cursor": cursor})
            assert "result" in m, "tools/list failed: %s" % json.dumps(m.get("error"))
            tools += m["result"].get("tools", [])
            cursor = m["result"].get("nextCursor")
            if not cursor:
                break
        names = [x.get("name") for x in tools]
        assert len(names) == len(set(names)), "duplicate tool names in tools/list: %s" % names
        want = set(self.tools["tools"])
        if set(names) != want:
            raise AssertionError("tools/list names differ from tools.json: missing %s, unexpected %s"
                                 % (sorted(want - set(names)), sorted(set(names) - want)))
        for x in tools:
            n = x["name"]
            assert isinstance(x.get("description"), str) and x["description"].strip(), "%s has no description" % n
            local = dict(self.tools["$defs"])
            for key in ("$defs", "definitions"):
                local.update((x.get("inputSchema") or {}).get(key, {}))
            got = normalise_schema(x.get("inputSchema"), local)
            exp = normalise_schema(self.tools["tools"][n]["input"], self.tools["$defs"])
            if got != exp:
                raise AssertionError("tools/list inputSchema of %s differs from tools.json: %s" % (n, json.dumps(got, sort_keys=True)[:200]))
        self.obs.append("%d tools, input schemas equal tools.json" % len(tools))

    def project_root(self):
        """The project path of contract/runtime.md: CLAUDE_PROJECT_DIR if set and non-empty, else the working directory."""
        return self.env.get("CLAUDE_PROJECT_DIR") or os.path.realpath(self.cwd)

    def fs_path(self, a):
        """Absolute path of an fs or seed entry: `path` (with $key, $iid and ${variables} expanded) below its `root`."""
        root = self.project_root() if a.get("root") == "project" else self.root()
        rel = a["path"].replace("$key", self.vars["key"]).replace("$iid", str(self.vars.get("iid", "$iid")))
        rel = subst(rel, self.vars)
        return os.path.join(root, rel) if rel else root

    def do_seed(self, st):
        """Create a file or directory below the data or project root, replacing any file there, as a crashed or
        earlier server might have left it: `content` (text), `lines` (objects dumped as JSON lines, strings verbatim),
        `json` (one JSON line, e.g. a pid file) or `mkdir`. Parent directories get mode 0700, files 0600 unless `mode`.
        `$dead_pid` (the pid of a process that has exited) is available to the content."""
        if "dead_pid" in json.dumps(st) and "dead_pid" not in self.vars:
            done = subprocess.Popen([sys.executable, "-c", "pass"])
            done.wait()
            self.vars["dead_pid"] = done.pid
        p = self.fs_path(st)
        mode = int(st.get("mode", "700" if st.get("mkdir") else "600"), 8)
        os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
        if st.get("mkdir"):
            os.makedirs(p, mode=mode, exist_ok=True)
            os.chmod(p, mode)
            self.obs.append("seeded directory %s" % st["path"])
            return
        if "json" in st:
            text = json.dumps(subst(st["json"], self.vars)) + "\n"
        elif "lines" in st:
            text = "".join((l if isinstance(l, str) else json.dumps(l)) + "\n" for l in subst(st["lines"], self.vars))
        else:
            text = subst(st["content"], self.vars)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(p, mode)
        self.obs.append("seeded %s (%d bytes)" % (st["path"], len(text)))

    def do_utime(self, st):
        """Set the access and modification times of an existing file or directory to `age_seconds` ago, so that
        the age rules of contract/runtime.md (pruning after 30 days, stale files after an hour) can be exercised."""
        p = self.fs_path(st)
        if not os.path.exists(p):
            raise AssertionError("utime: %s does not exist" % p)
        t = time.time() - st["age_seconds"]
        os.utime(p, (t, t))
        self.obs.append("%s aged %s s" % (st["path"], st["age_seconds"]))

    def do_stderr(self, st):
        """Assertions on what the server processes wrote to standard error so far (server.stderr.log, shared by
        every named server): `empty`, `contains`, `not_contains`, `matches`, `not_matches`."""
        with open(os.path.join(self.dir, "server.stderr.log"), encoding="utf-8", errors="replace") as f:
            text = f.read()
        for a in st["assert"]:
            op = [k for k in a][0]
            want = subst(a[op], self.vars)
            if op == "empty" and bool(text) == want:
                raise AssertionError("stderr %s: %r" % ("is not empty" if want else "is empty", text[:150]))
            if op == "contains" and want not in text:
                raise AssertionError("stderr lacks %r: %r" % (want, text[:150]))
            if op == "not_contains" and want in text:
                raise AssertionError("stderr should not contain %r: %r" % (want, text[:150]))
            if op == "matches" and not re.search(want, text):
                raise AssertionError("stderr does not match /%s/: %r" % (want, text[:150]))
            if op == "not_matches" and re.search(want, text):
                raise AssertionError("stderr should not match /%s/: %r" % (want, text[:150]))
            self.obs.append("stderr %s %s (%d bytes)" % (op, json.dumps(want)[:40], len(text)))

    def do_fs(self, st):
        for a in st["assert"]:
            p = self.fs_path(a)
            want = a.get("exists", True)
            if os.path.exists(p) != want:
                raise AssertionError("%s %s" % (p, "missing" if want else "should not exist"))
            if want and ("age_ge" in a or "age_le" in a):
                age = time.time() - os.stat(p).st_mtime
                if "age_ge" in a and age < a["age_ge"]:
                    raise AssertionError("%s was modified %.1f s ago, expected at least %s s" % (p, age, a["age_ge"]))
                if "age_le" in a and age > a["age_le"]:
                    raise AssertionError("%s was modified %.1f s ago, expected at most %s s" % (p, age, a["age_le"]))
                self.obs.append("%s modified %.0f s ago" % (a["path"] or "<root>", age))
            if want and "mode" in a:
                got = "%o" % (os.stat(p).st_mode & 0o777)
                if got != a["mode"]:
                    raise AssertionError("%s mode %s, expected %s" % (p, got, a["mode"]))
                self.obs.append("%s mode %s" % (a["path"] or "<data root>", got))
            elif not want:
                self.obs.append("%s absent" % a["path"])
            if want and ("matches" in a or a.get("nonempty")):
                with open(p, encoding="utf-8") as f:
                    content = f.read()
                if a.get("nonempty") and not content.strip():
                    raise AssertionError("%s is empty" % p)
                rx = subst(a.get("matches"), self.vars)
                if rx is not None and not re.search(rx, content):
                    raise AssertionError("%s content does not match /%s/: %r" % (p, rx, content[:120]))
                self.obs.append("%s content ok (%d bytes)" % (a["path"], len(content)))

    def step(self, st):
        op = st.get("op", "call")
        if op == "call":
            return self.do_call(st)
        name = st.get("server", "main")
        if op == "restart":
            self.stop_server(name)
            self.start(name)
        elif op == "stop":
            self.stop_server(name)
        elif op == "start":
            self.start(name)
        elif op == "kill":
            self.kill_server(name)
        elif op == "journal":
            self.do_journal(st)
        elif op == "fs":
            self.do_fs(st)
        elif op == "seed":
            self.do_seed(st)
        elif op == "stderr":
            self.do_stderr(st)
        elif op == "utime":
            self.do_utime(st)
        elif op == "tools_list":
            self.do_tools_list(st)
        elif op == "repeat":
            for row in repeat_rows(st):
                self.vars.update(row)
                for sub in st["steps"]:
                    self.step(sub)
        else:
            raise AssertionError("unknown op %r" % op)

    def do_call(self, st):
        name = st["call"]
        args = subst(st.get("args", {}), self.vars)
        tool = self.tools["tools"].get(name, {"audience": "model", "result": {}})  # unknown name: protocol_error only
        meta = st.get("meta")
        if meta is None:
            meta = MODEL_META if tool["audience"] == "model" else None
        elif meta is True:
            meta = MODEL_META
        elif meta is False:
            meta = None
        kind, text = self.server(st.get("server", "main")).call(name, args, meta)
        expect = st.get("expect", "ok")
        if expect in ("error", "protocol_error"):
            want = "tool_error" if expect == "error" else "protocol_error"
            if kind != want:
                raise AssertionError("%s %s: expected %s, got %s: %s" % (name, args, want, kind, text[:150]))
            for m in as_list(st.get("mention")):
                if subst(m, self.vars) not in text:
                    raise AssertionError("error for %s lacks %r: %s" % (name, subst(m, self.vars), text[:150]))
            for m in as_list(st.get("not_mention")):
                if subst(m, self.vars) in text:
                    raise AssertionError("error for %s should not contain %r: %s" % (name, subst(m, self.vars), text[:150]))
            for rx in as_list(st.get("matches")):
                if not re.search(subst(rx, self.vars), text):
                    raise AssertionError("error for %s does not match /%s/: %s" % (name, subst(rx, self.vars), text[:150]))
            return
        if kind != "ok":
            raise AssertionError("%s %s unexpectedly failed (%s): %s" % (name, args, kind, text[:150]))
        body = {} if (text.strip() == "" and tool["audience"] == "hook") else json.loads(text)
        errs = self.check(body, tool["result"], self.tools["$defs"])
        if errs:
            raise AssertionError("%s result violates tools.json: %s" % (name, "; ".join(errs[:3])))
        if name == "status" and body["items"] != derived_counts(body):
            raise AssertionError("status items %s != counts derived from tree %s"
                                 % (body["items"], derived_counts(body)))
        for var, path in st.get("save", {}).items():
            self.vars[var] = dig(body, path)
        for a in st.get("assert", []):
            self.obs.append(check_assert(name, a, body, self.vars))

    def check_events(self, ended=None):
        """Every journal line against contract/journal.schema.json: valid JSON, known kind, per-kind fields,
        v 2, RFC 3339 UTC ts, seq gapless from 1; the first event is `start`."""
        js = self.tools["journal"]
        defs = dict(self.tools["$defs"], **js["$defs"])
        assert self.lines, "journal is empty"
        for n, line in enumerate(self.lines, 1):
            try:
                e = json.loads(line)
            except ValueError:
                raise AssertionError("journal line %d is not JSON: %r" % (n, line[:80]))
            kind = e.get("kind") if isinstance(e, dict) else None
            if kind not in js["kinds"]:
                raise AssertionError("journal line %d has unknown kind %r" % (n, kind))
            errs = self.check(e, js["kinds"][kind], defs)
            if errs:
                raise AssertionError("journal line %d (%s) violates journal.schema.json: %s" % (n, kind, "; ".join(errs[:3])))
            if e["seq"] != n:
                raise AssertionError("journal line %d has seq %d (gapless 1-based expected)" % (n, e["seq"]))
            if n == 1 and kind != "start":
                raise AssertionError("journal starts with %r, expected start" % kind)
        last = json.loads(self.lines[-1])["kind"]
        if ended is True and last != "end":
            raise AssertionError("journal does not end with an end event (last is %r)" % last)
        if ended is False and any(json.loads(l)["kind"] == "end" for l in self.lines):
            raise AssertionError("journal of a running or unfinished interview has an end event")
        self.obs.append("%d events valid, seq 1..%d" % (len(self.lines), len(self.lines)))

    def journal_lines(self, name):
        assert name in self.jl, "journal %r was not loaded" % name
        return self.jl[name]

    def do_journal(self, st):
        action = st["action"]
        if action == "load":
            name = st.get("into", "main")
            with open(self.journal_path(st.get("where", "active")), encoding="utf-8") as f:
                lines = [l.rstrip("\n") for l in f if l.strip()]
            self.jl[name] = lines
            self.vars["n_" + name] = len(lines)
            self.vars["np1_" + name] = len(lines) + 1
            if name == "main":
                self.vars["n"] = len(lines)
                self.vars["n1"] = str(len(lines) + 1)
                self.vars["np1"] = len(lines) + 1
            self.obs.append("journal %s has %d events" % (name, len(lines)))
        elif action == "check_events":
            self.lines = self.journal_lines(st.get("from", "main"))
            self.check_events(st.get("ended"))
        elif action == "assert_events":
            self.assert_events(st)
        elif action == "write":
            lines = self.journal_lines(st.get("from", "main"))
            evs = [json.loads(l) for l in lines]
            four = [e for e in evs if e["kind"] in ("item", "resolve", "park", "reopen")]  # the only kinds v1 had

            def v1(e):
                e = dict(e)
                for k in ("v", "ts", "seq"):
                    e.pop(k, None)
                return json.dumps(e)

            form = st["form"]
            if form == "orig":
                out = list(lines)
            elif form == "v1_all":  # spike-3 journal: only item, resolve, park, reopen; no v, ts, seq
                out = [v1(e) for e in four]
                self.vars["v1_n"] = len(out)
            elif form == "v1_but_last":  # v1 lines, then one v2 line whose seq continues the line numbers
                last = dict(four[-1], seq=len(four))
                out = [v1(e) for e in four[:-1]] + [json.dumps(last)]
                self.vars["v1_n"] = len(out)
            elif form == "orig_plus_v3":
                bad = dict(evs[-1], v=3, seq=len(evs) + 1)
                out = list(lines) + [json.dumps(bad)]
            else:
                raise AssertionError("unknown journal form %r" % form)
            with open(self.journal_path("active"), "w", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
        else:
            raise AssertionError("unknown journal action %r" % action)

    def assert_events(self, st):
        """Assertions on a loaded journal: `events` (partial objects matching distinct events in order),
        `last` (the final event), `count` ({kind: n}), `absent` (kinds that must not occur), `total` (line count),
        `prefix` (the lines of another loaded journal are the first lines of this one), `at` ({position: partial object};
        positions count from 1, negative ones from the end) and `each` ({kind or "*": partial object} that every event
        of that kind, at least one, must match)."""
        name = st.get("from", "main")
        lines = self.journal_lines(name)
        evs = [json.loads(l) for l in lines]

        def match(e, part):
            return all(k in e and e[k] == v for k, v in subst(part, self.vars).items())

        if "total" in st and len(evs) != subst(st["total"], self.vars):
            raise AssertionError("journal %s has %d events, expected %s" % (name, len(evs), subst(st["total"], self.vars)))
        pos = 0
        for part in st.get("events", []):
            while pos < len(evs) and not match(evs[pos], part):
                pos += 1
            if pos >= len(evs):
                raise AssertionError("journal %s lacks (in order) an event matching %s" % (name, json.dumps(subst(part, self.vars))))
            pos += 1
        if "last" in st and not match(evs[-1], st["last"]):
            raise AssertionError("last journal event %s does not match %s" % (json.dumps(evs[-1])[:150], json.dumps(subst(st["last"], self.vars))))
        for kind, n in st.get("count", {}).items():
            got = sum(1 for e in evs if e["kind"] == kind)
            if got != n:
                raise AssertionError("journal %s has %d %s events, expected %d" % (name, got, kind, n))
        for kind in st.get("absent", []):
            if any(e["kind"] == kind for e in evs):
                raise AssertionError("journal %s has a %s event" % (name, kind))
        for key, part in st.get("at", {}).items():
            n = int(key)
            if not 1 <= abs(n) <= len(evs):
                raise AssertionError("journal %s has %d events, none at position %s" % (name, len(evs), key))
            e = evs[n - 1 if n > 0 else n]
            if not match(e, part):
                raise AssertionError("journal %s event %s is %s, expected %s" % (name, key, json.dumps(e)[:150], json.dumps(subst(part, self.vars))))
        for kind, part in st.get("each", {}).items():
            of = [e for e in evs if kind == "*" or e["kind"] == kind]
            if not of:
                raise AssertionError("journal %s has no %s event to check" % (name, kind))
            for e in of:
                if not match(e, part):
                    raise AssertionError("journal %s %s event %s does not match %s" % (name, kind, json.dumps(e)[:150], json.dumps(subst(part, self.vars))))
        if "prefix" in st:
            pre = self.journal_lines(st["prefix"])
            if lines[:len(pre)] != pre:
                raise AssertionError("journal %s does not start with the lines of journal %s" % (name, st["prefix"]))
        self.obs.append("journal %s: %d events, checks passed" % (name, len(evs)))

    def close(self):
        """End of scenario: every running server must also exit on stdin EOF."""
        for name in list(self.servers):
            self.stop_server(name)

    def abort(self):
        for srv in self.servers.values():
            srv.kill()
            srv.errf.close()
        self.servers = {}


def run_scenario(cmd, scen, tools, validator):
    r = None
    try:
        r = Run(cmd, scen, tools, validator)
        r.start()
        for st in scen["steps"]:
            r.step(st)
        r.close()
        return True, "; ".join(r.obs)[:300]
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e).replace("\n", " ")[:300])
    finally:
        if r:
            r.abort()


# ---------------------------------------------------------------- static validation

def placeholder(schema, defs):
    """A value satisfying `schema`, used for a "$variable" argument whose real value is only known at run time."""
    while "$ref" in schema:
        schema = defs[schema["$ref"].split("/")[-1]]
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "oneOf" in schema:
        return placeholder(schema["oneOf"][-1], defs)
    ty = schema.get("type")
    if ty == "integer" or ty == "number":
        return max(1, schema.get("minimum", 1))
    if ty == "boolean":
        return True
    if ty == "array":
        return []
    if ty == "object":
        return {}
    if ty == "null":
        return None
    pat = schema.get("pattern", "")
    return "q1" if pat.startswith("^[A-Za-z0-9_.-]") else "x"  # an id, else any non-blank single-line text


def fill_variables(args, schema, defs):
    """Replace "$name" argument values by type-appropriate placeholders, so every other argument is still validated."""
    while isinstance(schema, dict) and "$ref" in schema:
        schema = defs[schema["$ref"].split("/")[-1]]
    if isinstance(args, str) and re.match(r"^\$[A-Za-z_]\w*$", args):
        return placeholder(schema, defs)
    if isinstance(args, list):
        sub = schema.get("items", {}) if isinstance(schema, dict) else {}
        return [fill_variables(x, sub, defs) for x in args]
    if isinstance(args, dict):
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if isinstance(schema, dict) and "oneOf" in schema:  # end_interview: pick the branch that knows every key
            for br in schema["oneOf"]:
                if set(args) <= set(br.get("properties", {})):
                    props = br["properties"]
        return {k: fill_variables(v, props.get(k, {}), defs) for k, v in args.items()}
    return args


STEP_KEYS = {
    "call": {"op", "server", "call", "args", "meta", "expect", "mention", "not_mention", "matches", "save", "assert"},
    "restart": {"op", "server"}, "stop": {"op", "server"}, "start": {"op", "server"}, "kill": {"op", "server"},
    "journal": {"op", "action", "form", "where", "into", "from", "ended", "events", "last", "count", "absent", "total", "prefix", "at", "each"},
    "fs": {"op", "assert"},
    "tools_list": {"op", "server"},
    "repeat": {"op", "count", "var", "rows", "steps"},
    "stderr": {"op", "assert"},
    "utime": {"op", "path", "root", "age_seconds"},
    "seed": {"op", "path", "root", "content", "lines", "json", "mkdir", "mode"},
}
MAX_REPEAT = 200
FS_KEYS = {"path", "root", "exists", "mode", "matches", "nonempty", "age_ge", "age_le"}
FS_ROOTS = ("data", "project")
STDERR_OPS = ("empty", "contains", "not_contains", "matches", "not_matches")
EXPECTS = ("ok", "error", "protocol_error")


def validate_assert(where, a, errs):
    if not isinstance(a, dict):
        errs.append("%s: assert entry must be an object" % where)
        return
    for k in a:
        if k != "path" and k not in OPERATORS:
            errs.append("%s: unknown assert key %r (operators: %s)" % (where, k, ", ".join(OPERATORS)))
    ops = [k for k in a if k in OPERATORS]
    if len(ops) != 1:
        errs.append("%s: assert entry needs exactly one operator, has %d (%s)" % (where, len(ops), ", ".join(ops) or "none"))
        return
    op = ops[0]
    if op in ("decision_block", "absent"):
        if not isinstance(a[op], bool):
            errs.append("%s: %s needs a boolean" % (where, op))
        if op == "absent" and ("path" not in a or a[op] is not True):
            errs.append("%s: absent needs a path and true" % where)
        if op == "decision_block" and "path" in a:
            errs.append("%s: decision_block applies to the whole result, not a path" % where)
    elif op in ("len", "ge") and (not isinstance(a[op], (int, float)) or isinstance(a[op], bool)):
        errs.append("%s: %s needs a number" % (where, op))
    elif op == "matches":
        try:
            re.compile(re.sub(r"\$\{\w+\}", "0", a[op]))
        except (re.error, TypeError) as e:
            errs.append("%s: bad regex: %s" % (where, e))


def is_seconds(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def check_path(where, a, errs):
    """An fs or seed path is relative to its root and stays inside it; the root is known."""
    if "path" in a and (not isinstance(a["path"], str) or a["path"].startswith("/") or ".." in a["path"].split("/")):
        errs.append("%s: fs path is relative to its root and stays inside it" % where)
    if a.get("root", "data") not in FS_ROOTS:
        errs.append("%s: fs root is one of %s" % (where, ", ".join(FS_ROOTS)))


def validate_step(where, st, tools, validator, errs):
    op = st.get("op", "call")
    allowed = STEP_KEYS.get(op)
    if allowed is None:
        errs.append("%s: unknown op %r" % (where, op))
        return
    for k in st:
        if allowed is not None and k not in allowed:
            errs.append("%s: unknown key %r for op %s" % (where, k, op))
    if "server" in st and not (isinstance(st["server"], str) and re.match(r"^[a-z][a-z0-9_]*$", st["server"])):
        errs.append("%s: server is a lower-case name" % where)
    if op == "repeat":
        validate_repeat(where, st, tools, validator, errs)
        return
    if op == "call":
        name = st.get("call")
        expect = st.get("expect", "ok")
        if expect not in EXPECTS:
            errs.append("%s: bad expect %r" % (where, expect))
        if name not in tools["tools"]:
            if not (expect == "protocol_error" and isinstance(name, str)):
                errs.append("%s: unknown tool %r" % (where, name))
            return
        tool = tools["tools"][name]
        args = st.get("args", {})
        if expect == "ok":
            args = fill_variables(args, tool["input"], tools["$defs"])
            for e in validator(args, tool["input"], tools["$defs"]):
                errs.append("%s: %s args violate tools.json: %s" % (where, name, e))
        if tool["audience"] == "hook" and st.get("meta") not in (None, False) and expect == "ok":
            errs.append("%s: hook-facing %s sent with _meta must expect error" % (where, name))
        if expect == "ok":
            for k in ("mention", "not_mention", "matches"):
                if k in st:
                    errs.append("%s: %s only applies to expect error or protocol_error" % (where, k))
        else:
            for k in ("save", "assert"):
                if k in st:
                    errs.append("%s: %s only applies to expect ok" % (where, k))
            for rx in as_list(st.get("matches")):
                try:
                    re.compile(re.sub(r"\$\{\w+\}", "0", rx))
                except re.error as e:
                    errs.append("%s: bad regex: %s" % (where, e))
        for j, a in enumerate(st.get("assert", [])):
            validate_assert("%s assert %d" % (where, j + 1), a, errs)
    elif op == "journal":
        action = st.get("action")
        if action not in ("load", "check_events", "assert_events", "write"):
            errs.append("%s: bad journal action" % where)
        per_action = {"load": {"where", "into"}, "check_events": {"from", "ended"}, "write": {"form", "from"},
                      "assert_events": {"from", "events", "last", "count", "absent", "total", "prefix", "at", "each"}}
        for k in st:
            if k not in ("op", "action") and k not in per_action.get(action, set()):
                errs.append("%s: key %r does not apply to journal %s" % (where, k, action))
        if action == "write" and st.get("form") not in ("orig", "v1_all", "v1_but_last", "orig_plus_v3"):
            errs.append("%s: journal write needs a known form" % where)
        if action == "assert_events" and not any(k in st for k in ("events", "last", "count", "absent", "total", "prefix", "at", "each")):
            errs.append("%s: assert_events asserts nothing" % where)
        if action == "assert_events":
            kinds = tools["journal"]["kinds"]
            for key, label in (("at", "position"), ("each", "kind")):
                if key not in st:
                    continue
                if not isinstance(st[key], dict) or not st[key]:
                    errs.append("%s: %s is a non-empty object keyed by %s" % (where, key, label))
                    continue
                for k, part in st[key].items():
                    if key == "at" and not re.match(r"^-?[1-9]\d*$", k):
                        errs.append("%s: at position %r is a non-zero integer" % (where, k))
                    if key == "each" and k != "*" and k not in kinds:
                        errs.append("%s: each names unknown journal kind %r" % (where, k))
                    if not isinstance(part, dict) or not part:
                        errs.append("%s: %s %r needs a non-empty object of fields to match" % (where, key, k))
        if st.get("where", "active") not in ("active", "archive"):
            errs.append("%s: where is active or archive" % where)
    elif op == "fs":
        if not st.get("assert"):
            errs.append("%s: fs step needs assert" % where)
        for a in st.get("assert", []):
            for k in a:
                if k not in FS_KEYS:
                    errs.append("%s: unknown fs assert key %r" % (where, k))
            if "path" not in a:
                errs.append("%s: fs assert needs path" % where)
            check_path(where, a, errs)
            for k in ("age_ge", "age_le"):
                if k in a and not is_seconds(a[k]):
                    errs.append("%s: fs %s is a number of seconds, zero or more" % (where, k))
            if a.get("exists", True) is not True and ("age_ge" in a or "age_le" in a):
                errs.append("%s: fs age applies to a file that exists" % where)
    elif op == "utime":
        check_path(where, st, errs)
        if not st.get("path"):
            errs.append("%s: utime needs a path" % where)
        if not is_seconds(st.get("age_seconds")):
            errs.append("%s: utime needs age_seconds, a number of seconds, zero or more" % where)
    elif op == "stderr":
        if not isinstance(st.get("assert"), list) or not st["assert"]:
            errs.append("%s: stderr step needs a non-empty assert list" % where)
        for a in st.get("assert", []) if isinstance(st.get("assert"), list) else []:
            if not isinstance(a, dict) or len(a) != 1 or next(iter(a)) not in STDERR_OPS:
                errs.append("%s: a stderr assert entry has exactly one operator (%s)" % (where, ", ".join(STDERR_OPS)))
                continue
            op_, val = next(iter(a.items()))
            if op_ == "empty":
                if not isinstance(val, bool):
                    errs.append("%s: stderr empty needs a boolean" % where)
            elif not isinstance(val, str):
                errs.append("%s: stderr %s needs a string" % (where, op_))
            elif op_ in ("matches", "not_matches"):
                try:
                    re.compile(re.sub(r"\$\{\w+\}", "0", val))
                except re.error as e:
                    errs.append("%s: bad regex: %s" % (where, e))
    elif op == "seed":
        check_path(where, st, errs)
        if "path" not in st or st.get("path") == "":
            errs.append("%s: seed needs a path" % where)
        given = [k for k in ("content", "lines", "json", "mkdir") if k in st]
        if len(given) != 1:
            errs.append("%s: seed needs exactly one of content, lines, json and mkdir" % where)
        elif given[0] == "content" and not isinstance(st["content"], str):
            errs.append("%s: seed content is a string" % where)
        elif given[0] == "lines" and not (isinstance(st["lines"], list) and all(isinstance(l, (str, dict)) for l in st["lines"])):
            errs.append("%s: seed lines is a list of objects and strings" % where)
        elif given[0] == "json" and not isinstance(st["json"], dict):
            errs.append("%s: seed json is an object" % where)
        elif given[0] == "mkdir" and st["mkdir"] is not True:
            errs.append("%s: seed mkdir is true" % where)
        if "mode" in st and not (isinstance(st["mode"], str) and re.match(r"^[0-7]{3,4}$", st["mode"])):
            errs.append("%s: seed mode is an octal string such as 600" % where)


def validate_repeat(where, st, tools, validator, errs):
    """A repeat step has exactly one of `count` (with `var`) or `rows`, and non-empty `steps` that are not
    themselves repeats. Each pass is validated as the concrete steps it expands to."""
    has = [k for k in ("count", "rows") if k in st]
    if len(has) != 1:
        errs.append("%s: repeat needs exactly one of count and rows" % where)
        return
    if has[0] == "count":
        n = st["count"]
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_REPEAT:
            errs.append("%s: repeat count is an integer from 1 to %d" % (where, MAX_REPEAT))
            return
        if not isinstance(st.get("var"), str) or not re.match(r"^[A-Za-z_]\w*$", st["var"]):
            errs.append("%s: repeat count needs var, a variable name" % where)
            return
        passes = [{st["var"]: 1}, {st["var"]: n}]  # the first and the last pass
    else:
        rows = st["rows"]
        if "var" in st:
            errs.append("%s: var applies to count, not rows" % where)
        if not isinstance(rows, list) or not rows or len(rows) > MAX_REPEAT or \
                not all(isinstance(r, dict) and r and all(re.match(r"^[A-Za-z_]\w*$", k) for k in r) for r in rows):
            errs.append("%s: repeat rows is a non-empty list of objects keyed by variable names (at most %d)" % (where, MAX_REPEAT))
            return
        passes = rows
    steps = st.get("steps")
    if not isinstance(steps, list) or not steps or not all(isinstance(x, dict) for x in steps):
        errs.append("%s: repeat needs a non-empty steps list of objects" % where)
        return
    for j, sub in enumerate(steps):
        if sub.get("op") == "repeat":
            errs.append("%s step %d: repeats do not nest" % (where, j + 1))
            return
    for n, row in enumerate(passes):
        for j, sub in enumerate(steps):
            validate_step("%s pass %d step %d" % (where, n + 1, j + 1), subst(sub, row), tools, validator, errs)


def track_servers(where, steps, running, errs):
    """Follow which servers run, from the one the driver starts ("main"): start needs a stopped name; stop, kill,
    restart, tools_list and a call need a running one. A repeat must leave the running set as it found it.
    Returns the running set after the steps."""
    running = set(running)
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            continue
        op, name = st.get("op", "call"), st.get("server", "main")
        if not isinstance(name, str):
            continue
        at = "%s step %d" % (where, i + 1)
        if op == "start":
            if name in running:
                errs.append("%s: server %s is already running" % (at, name))
            running.add(name)
        elif op in ("stop", "kill", "restart", "tools_list", "call"):
            if name not in running:
                errs.append("%s: server %s is not running" % (at, name))
            elif op in ("stop", "kill"):
                running.discard(name)
        elif op == "repeat" and isinstance(st.get("steps"), list):
            if track_servers(at, st["steps"], running, errs) != running:
                errs.append("%s: a repeat must leave the same servers running" % at)
    return running


def validate(scenarios, tools, validator):
    errs, ids = [], set()
    top = {"id", "title", "req", "steps", "env", "unset_env"}
    for s in scenarios:
        f = s["_file"]
        for k in s:
            if k != "_file" and k not in top:
                errs.append("%s: unknown top-level key %s" % (f, k))
        for k in ("id", "title", "req", "steps"):
            if k not in s:
                errs.append("%s: missing %s" % (f, k))
        if s.get("id") in ids:
            errs.append("%s: duplicate id %s" % (f, s.get("id")))
        ids.add(s.get("id"))
        if s.get("id") and f != s["id"] + ".json":
            errs.append("%s: file name must be <id>.json" % f)
        for i, st in enumerate(s.get("steps", [])):
            validate_step("%s step %d" % (f, i + 1), st, tools, validator, errs)
        track_servers(f, s.get("steps", []), {"main"}, errs)
    return errs


# ---------------------------------------------------------------- main

def main(argv):
    only, cmd = [], None
    mode = "run"
    a = list(argv)
    if "--" in a:
        i = a.index("--")
        cmd = a[i + 1:]
        a = a[:i]
    while a:
        x = a.pop(0)
        if x == "--only" and a:
            only += [p for p in a.pop(0).split(",") if p]
        elif x == "--list":
            mode = "list"
        elif x == "--validate":
            mode = "validate"
        else:
            print(__doc__.strip())
            return 2
    if cmd:
        # the server runs in a scenario directory, so relative file arguments are made absolute first
        cmd = [os.path.abspath(c) if (not os.path.isabs(c) and os.sep in c and os.path.exists(c)) else c for c in cmd]
    tools, validator = load_contract()
    scenarios = load_scenarios()
    if only:
        scenarios = [s for s in scenarios if any(fnmatch.fnmatchcase(s["id"], p) for p in only)]
        if not scenarios:
            print("# no scenario matches --only %s" % ",".join(only))
            return 1
    if mode == "list":
        for s in scenarios:
            print("%s\t%s" % (s["id"], s["title"]))
        return 0
    errs = validate(scenarios, tools, validator)
    if mode == "validate":
        for e in errs:
            print("not ok - " + e)
        print("# %d scenarios, %d validation errors (tool_schema %s)" % (len(scenarios), len(errs), tools["tool_schema"]))
        return 1 if errs else 0
    if errs:
        for e in errs:
            print("not ok - " + e)
        return 1
    if not cmd:
        print(__doc__.strip())
        return 2
    global RUN_DIR
    os.makedirs(SCRATCH, exist_ok=True)
    RUN_DIR = tempfile.mkdtemp(prefix="run-", dir=SCRATCH)
    print("TAP version 13")
    print("1..%d" % len(scenarios))
    fails = 0
    for n, s in enumerate(scenarios, 1):
        ok, detail = run_scenario(cmd, s, tools, validator)
        if ok:
            shutil.rmtree(os.path.join(RUN_DIR, s["id"]), ignore_errors=True)
        else:
            fails += 1
        print("%s %d - %s %s%s" % ("ok" if ok else "not ok", n, s["id"], s["title"],
                                   (" # " + detail) if detail else ""))
    print("# %d/%d passed" % (len(scenarios) - fails, len(scenarios)))
    try:
        os.rmdir(RUN_DIR)  # empty when every scenario passed
    except OSError:
        print("# failed scenario directories kept in %s" % RUN_DIR)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
