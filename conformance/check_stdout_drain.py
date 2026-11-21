#!/usr/bin/env python3
"""Stdout drain at stdin EOF (issue #102); stdlib only, Linux. Run: python3 -I conformance/check_stdout_drain.py [--expect-bounded-drain] -- <server command...>

Guards contract/runtime.md "EOF shutdown completes pending replies": every reply to a request the server read before stdin EOF is
delivered whole before the process exits 0, even when the reply does not fit in the stdout pipe. The stdout pipe is forced
to 4096 and 8192 bytes (F_SETPIPE_SZ), so the outcome does not depend on how many pipe pages the host grants. Each run sends all the
requests, closes stdin at once, and starts reading only after that (a late reader; the server cannot finish writing before it does).
Cells: one status reply of about 16385, 65536 and 200000 bytes, and 2000 pipelined ping replies. Not covered: the signal path,
which may cut a reply the client has not read (runtime.md "Signals and shutdown limits").
A server that never closes stdout or never exits fails the run (a select deadline of READ_CEILING and EXIT_WAIT seconds; the child is killed), so the check cannot hang CI.
--expect-bounded-drain (TypeScript build only; the Rust build has no ceiling there and would fail it) adds the stalled-reader cell: the stub
closes stdin and never reads stdout, with the pipe forced to 4096 and 8192 bytes and a 65536-byte reply, so the drain cannot finish. The server
must exit 0 by itself after the bound of the build (5 s, DRAIN_BOUND_MS in impl/ts/src/shutdown.ts; accepted window MIN_BOUND to BOUND_CEILING
seconds after the close of stdin) and leave no process behind in its process group. Adds about 10 s.
Scratch: <repo>/.scratch/stdout-drain/ (removed on pass). Wall time about 10 s.
"""
import fcntl, json, os, select, shutil, subprocess, sys, tempfile, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCH = json.load(open(os.path.join(ROOT, "contract", "tools.json")))["tool_schema"]
F_SETPIPE_SZ, F_GETPIPE_SZ = getattr(fcntl, "F_SETPIPE_SZ", 1031), getattr(fcntl, "F_GETPIPE_SZ", 1032)
PIPES = (4096, 8192)
REPS = 2
READER_DELAY = 0.2   # seconds after the last byte and the close of stdin before the first read
SEND_CAP = 1.0       # a server that stops reading stdin while its stdout is full (Rust) must not stall the check
READ_CEILING = 30    # seconds from the first read to the end of stdout, enforced with select (a server that never closes stdout fails the run)
EXIT_WAIT = 10       # seconds from the end of stdout to the exit
MIN_BOUND = 4.0      # stalled-reader cell: an exit earlier than this means the bound is not the 5 s declared (or the drain did not wait)
BOUND_CEILING = 12.0 # stalled-reader cell: the server must have exited by then (the bound is 5 s)
rpc = lambda i, m, p=None: {"jsonrpc": "2.0", "id": i, "method": m, **({"params": p} if p is not None else {})}
call = lambda i, n, a: rpc(i, "tools/call", {"name": n, "arguments": a})


def big_status(n):
    return [rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "drain", "version": "0"}}),
            call(2, "begin_interview", {"tool_schema": SCH, "skill": "grill-me", "mode": "register"}),
            call(3, "log_item", {"kind": "question", "text": "x" * n}), call(4, "status", {})], [1, 2, 3, 4], n


CELLS = [("status-reply-%d" % n, big_status(n)) for n in (16385, 65536, 200000)]
CELLS.append(("2000-pipelined-pings", ([rpc(i, "ping") for i in range(1, 2001)], list(range(1, 2001)), 0)))


def spawn(cmd, work, name, pipe, new_session=False):
    """Start the server with a small stdout pipe; returns (process, pipe size, None) or (None, None, reason)."""
    d = tempfile.mkdtemp(prefix=name + "-", dir=work)
    os.makedirs(d + "/proj/.scratch")
    env = dict(os.environ, CLAUDE_PLUGIN_DATA=d + "/data", CLAUDE_PROJECT_DIR=d + "/proj")
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    err = open(d + "/stderr.log", "wb")
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, env=env, cwd=d + "/proj", start_new_session=new_session)
    err.close()
    try:
        fcntl.fcntl(p.stdout.fileno(), F_SETPIPE_SZ, pipe)
    except OSError:
        pass
    got = fcntl.fcntl(p.stdout.fileno(), F_GETPIPE_SZ)
    if got > 8192:
        p.kill()
        p.wait()
        p.stdout.close()
        return None, None, "cannot force a small stdout pipe (got %d bytes)" % got
    return p, got, None


def run_stalled(cmd, work, pipe):
    """Stalled reader: stdin closed after the requests, stdout never read. None on pass, else the reason."""
    msgs = big_status(65536)[0]
    p, got, why = spawn(cmd, work, "stalled-reader", pipe, new_session=True)
    if why:
        return why
    pgid = p.pid  # start_new_session: the server leads its own process group, so a stray child shows up in it
    try:
        try:
            p.stdin.write("".join(json.dumps(m) + "\n" for m in msgs).encode())
            p.stdin.close()
        except OSError:
            pass
        t0 = time.monotonic()
        try:
            rc = p.wait(BOUND_CEILING)
        except subprocess.TimeoutExpired:
            return "no exit within %.0f s of the close of stdin with stdout unread (pipe %d); child killed" % (BOUND_CEILING, got)
        took = time.monotonic() - t0
        if rc != 0:
            return "exit status %s after %.1f s, expected 0 (pipe %d)" % (rc, took, got)
        if took < MIN_BOUND:
            return "exit after %.1f s, before the %.0f s bound: the drain did not wait or the bound is shorter than declared (pipe %d)" % (took, MIN_BOUND, got)
        for _ in range(20):
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return None
            time.sleep(0.1)
        try:
            os.killpg(pgid, 9)
        except ProcessLookupError:
            pass
        return "a process of the server's process group is still alive 2 s after the exit (killed)"
    finally:
        if p.poll() is None:
            try:
                os.killpg(pgid, 9)
            except ProcessLookupError:
                pass
        p.wait()
        p.stdout.close()


def run(cmd, work, name, spec, pipe):
    msgs, ids, minlen = spec
    p, got, why = spawn(cmd, work, name, pipe)
    if why:
        return why
    sent = threading.Event()

    def send():
        try:
            p.stdin.write("".join(json.dumps(m) + "\n" for m in msgs).encode())
            p.stdin.close()
        except OSError:
            pass
        sent.set()
    threading.Thread(target=send, daemon=True).start()
    sent.wait(SEND_CAP)
    time.sleep(READER_DELAY)
    out = bytearray()
    try:
        fd = p.stdout.fileno()
        deadline = time.monotonic() + READ_CEILING
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return "no end of output within %d s (%d bytes read, pipe %d); child killed" % (READ_CEILING, len(out), got)
            if not select.select([fd], [], [], left)[0]:
                continue
            b = os.read(fd, 65536)
            if not b:
                break
            out += b
        try:
            rc = p.wait(EXIT_WAIT)
        except subprocess.TimeoutExpired:
            return "no exit within %d s of the end of output; child killed" % EXIT_WAIT
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
        p.stdout.close()
    lines = bytes(out).split(b"\n")
    tail = lines.pop()
    try:
        got_ids = [json.loads(x)["id"] for x in lines]
    except (ValueError, KeyError, TypeError):
        return "an invalid reply line; %d bytes read, pipe %d, exit %s" % (len(out), got, rc)
    if tail or got_ids != ids:
        return "replies lost or cut: %d of %d whole lines, %d bytes read, trailing partial line %s, pipe %d, exit %s" % (
            len(lines), len(ids), len(out), bool(tail), got, rc)
    if minlen and len(lines[-1]) < minlen:
        return "the last reply is %d bytes, expected at least %d" % (len(lines[-1]), minlen)
    if rc != 0:
        return "exit status %s, expected 0" % rc
    return None


def main(argv):
    if "--" not in argv or not argv[argv.index("--") + 1:]:
        print(__doc__.strip())
        return 2
    flags = argv[:argv.index("--")]
    if any(f != "--expect-bounded-drain" for f in flags):
        print(__doc__.strip())
        return 2
    bounded = "--expect-bounded-drain" in flags
    cmd = argv[argv.index("--") + 1:]
    cmd = [os.path.abspath(c) if (not os.path.isabs(c) and os.sep in c and os.path.exists(c)) else c for c in cmd]
    base = os.path.join(ROOT, ".scratch", "stdout-drain")
    os.makedirs(base, exist_ok=True)
    work = tempfile.mkdtemp(prefix="run-", dir=base)
    n = bad = 0
    print("TAP version 13")
    for name, spec in CELLS:
        for pipe in PIPES:
            for rep in range(REPS):
                n += 1
                why = run(cmd, work, name, spec, pipe)
                bad += why is not None
                print("%s %d - %s, stdout pipe %d, run %d%s" % ("not ok" if why else "ok", n, name, pipe, rep + 1, (" # " + why) if why else ""))
    if bounded:
        for pipe in PIPES:
            n += 1
            why = run_stalled(cmd, work, pipe)
            bad += why is not None
            print("%s %d - stalled reader (stdin closed, stdout never read), exit 0 after the bound, no stray process, stdout pipe %d%s" % (
                "not ok" if why else "ok", n, pipe, (" # " + why) if why else ""))
    print("1..%d\n# %d/%d passed" % (n, n - bad, n))
    if bad:
        print("# failed runs kept in %s" % work)
        return 1
    shutil.rmtree(work, ignore_errors=True)
    try:
        os.rmdir(base)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
