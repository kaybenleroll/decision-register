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
        self.srv = None
        self.env = None
        self.jl = {}  # loaded journals by name
        self.obs = []  # observed values behind each passed assertion, for the TAP detail

    def start(self):
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
        self.srv = Server(self.cmd, env, os.path.join(self.dir, "server.stderr.log"), self.cwd)
        self.srv.initialize()
        self.vars["pid"] = self.srv.p.pid

    def stop_server(self):
        """Close the running server and require a graceful exit on stdin EOF."""
        srv, self.srv = self.srv, None
        if srv and not srv.close():
            raise AssertionError("server did not exit within %d s of stdin EOF (runtime.md: exits on stdin EOF)"
                                 % EXIT_WAIT)

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

    def do_tools_list(self):
        """REQ-71: the initialize reply and the tool list (names, input schemas) match contract/tools.json."""
        init = self.srv.init
        assert isinstance(init, dict), "initialize returned no result"
        assert init.get("protocolVersion") == "2024-11-05", "initialize protocolVersion is %r" % init.get("protocolVersion")
        assert isinstance(init.get("capabilities", {}).get("tools"), dict), "initialize lacks capabilities.tools"
        info = init.get("serverInfo")
        assert isinstance(info, dict) and all(isinstance(info.get(k), str) and info[k].strip() for k in ("name", "version")), \
            "initialize serverInfo needs a name and a version"
        tools, cursor = [], None
        while True:
            m = self.srv.rpc("tools/list", {} if cursor is None else {"cursor": cursor})
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

    def do_fs(self, st):
        root = self.root()
        for a in st["assert"]:
            rel = a["path"].replace("$key", self.vars["key"]).replace("$iid", self.vars["iid"])
            p = os.path.join(root, rel) if rel else root
            want = a.get("exists", True)
            if os.path.exists(p) != want:
                raise AssertionError("%s %s" % (p, "missing" if want else "should not exist"))
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
        if op == "restart":
            self.stop_server()
            self.start()
        elif op == "stop":
            self.stop_server()
        elif op == "start":
            self.start()
        elif op == "journal":
            self.do_journal(st)
        elif op == "fs":
            self.do_fs(st)
        elif op == "tools_list":
            self.do_tools_list()
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
        kind, text = self.srv.call(name, args, meta)
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
        `prefix` (the lines of another loaded journal are the first lines of this one)."""
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
        if "prefix" in st:
            pre = self.journal_lines(st["prefix"])
            if lines[:len(pre)] != pre:
                raise AssertionError("journal %s does not start with the lines of journal %s" % (name, st["prefix"]))
        self.obs.append("journal %s: %d events, checks passed" % (name, len(evs)))

    def close(self):
        """End of scenario: the running server must also exit on stdin EOF."""
        self.stop_server()

    def abort(self):
        if self.srv:
            self.srv.kill()
            self.srv.errf.close()
            self.srv = None


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
    "call": {"op", "call", "args", "meta", "expect", "mention", "not_mention", "matches", "save", "assert"},
    "restart": {"op"}, "stop": {"op"}, "start": {"op"},
    "journal": {"op", "action", "form", "where", "into", "from", "ended", "events", "last", "count", "absent", "total", "prefix"},
    "fs": {"op", "assert"},
    "tools_list": {"op"},
}
FS_KEYS = {"path", "exists", "mode", "matches", "nonempty"}
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
            where = "%s step %d" % (f, i + 1)
            op = st.get("op", "call")
            allowed = STEP_KEYS.get(op)
            if allowed is None:
                errs.append("%s: unknown op %r" % (where, op))
                continue
            for k in st:
                if allowed is not None and k not in allowed:
                    errs.append("%s: unknown key %r for op %s" % (where, k, op))
            if op == "call":
                name = st.get("call")
                expect = st.get("expect", "ok")
                if expect not in EXPECTS:
                    errs.append("%s: bad expect %r" % (where, expect))
                if name not in tools["tools"]:
                    if not (expect == "protocol_error" and isinstance(name, str)):
                        errs.append("%s: unknown tool %r" % (where, name))
                    continue
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
                              "assert_events": {"from", "events", "last", "count", "absent", "total", "prefix"}}
                for k in st:
                    if k not in ("op", "action") and k not in per_action.get(action, set()):
                        errs.append("%s: key %r does not apply to journal %s" % (where, k, action))
                if action == "write" and st.get("form") not in ("orig", "v1_all", "v1_but_last", "orig_plus_v3"):
                    errs.append("%s: journal write needs a known form" % where)
                if action == "assert_events" and not any(k in st for k in ("events", "last", "count", "absent", "total", "prefix")):
                    errs.append("%s: assert_events asserts nothing" % where)
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
