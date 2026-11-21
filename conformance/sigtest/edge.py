"""Edge cases, one mode per run.
Usage: python3 -I conformance/sigtest/edge.py <mode> <build> [SIGNAL]
  mid      an 8 MiB note_prompt in flight when SIGTERM arrives: journal has no torn line, one decision log, no pid file
  double   SIGTERM, SIGTERM, SIGINT, SIGHUP in a row: one decision log, one exit
  pings    2000 pipelined pings, then SIGTERM: how many replies were read before the exit
  ignore   SIGNAL (SIGHUP|SIGINT|SIGTERM) sent to a server whose parent ignored SIGHUP and SIGINT (`trap "" HUP INT; exec ...`)
  race     60 runs of EOF and SIGTERM 0 to 3 ms apart, in both orders: counts per order, exit code and whether the files are right
           (one decision log, no pid file, 2 journal events); expected exit code 0 (EOF first) or 143 (signal first)
Output below $SIGTEST_OUT/edge-<mode>."""
import glob, json, os, shlex, signal, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive

mode, build = sys.argv[1], sys.argv[2]
OUT = "%s/edge-%s" % (drive.BASE, mode)


def spawn(tag, ignore=False):
    d = "%s/%s-%s" % (OUT, build, tag)
    os.makedirs(d + "/data")
    os.makedirs(d + "/proj/.scratch")
    argv = drive.command(build)
    if ignore:
        argv = ["sh", "-c", 'trap "" HUP INT; exec ' + shlex.join(argv)]
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         env=drive.server_env(d + "/data", d + "/proj"), cwd=d + "/proj", encoding="utf-8", bufsize=1)
    time.sleep(0.5)
    return p, d


def send(p, o):
    p.stdin.write(json.dumps(o) + "\n")
    p.stdin.flush()


def setup(p):
    send(p, drive.init_msg())
    p.stdout.readline()
    n = [1]

    def call(name, args, meta=True):
        n[0] += 1
        send(p, drive.call_msg(n[0], name, args, meta))
        return json.loads(p.stdout.readline())
    call("note_prompt", {"session_id": "s", "prompt": "grill me"}, False)
    call("begin_interview", {"tool_schema": 4, "skill": "grill-me", "mode": "register"})
    call("log_item", {"id": "q1", "kind": "question", "text": "first"})


def count(d, pat):
    return len(glob.glob(d + pat, recursive=True))


if mode == "ignore":
    sigp = getattr(signal, sys.argv[3])
    p, d = spawn(sys.argv[3], ignore=True)
    setup(p)
    os.kill(p.pid, sigp)  # the pid is the sh that exec'd the server: the same process
    time.sleep(2)
    out = {"build": build, "sig": sys.argv[3], "alive_after_2s": p.poll() is None, "rc": p.returncode}
    if out["alive_after_2s"]:
        p.stdin.close()
        out["rc_after_eof"] = p.wait(10)
    out.update(declog=count(d, "/proj/.scratch/decision-*.md"), pid=count(d, "/data/**/*.pid"))
    print(json.dumps(out))
elif mode == "mid":
    p, d = spawn("mid")
    setup(p)
    big = "x" * (8 << 20)

    def w():
        try:
            send(p, drive.call_msg(99, "note_prompt", {"session_id": "s", "prompt": big}, False))
        except Exception:
            pass
    t = threading.Thread(target=w)
    t.start()
    time.sleep(0.01)
    os.kill(p.pid, signal.SIGTERM)
    rc = p.wait(10)
    raw = open(glob.glob(d + "/data/**/*.jsonl", recursive=True)[0]).read()
    bad = 0
    for line in raw.split("\n"):
        if line.strip():
            try:
                json.loads(line)
            except Exception:
                bad += 1
    print(json.dumps({"build": build, "rc": rc, "lines": raw.count("\n"), "bad_lines": bad, "ends_nl": raw.endswith("\n"),
                      "declog": count(d, "/proj/.scratch/decision-*.md"), "pid": count(d, "/data/**/*.pid")}))
elif mode == "double":
    p, d = spawn("double")
    setup(p)
    for s in (signal.SIGTERM, signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            os.kill(p.pid, s)
        except ProcessLookupError:
            pass
    rc = p.wait(10)
    print(json.dumps({"build": build, "rc": rc, "declog": count(d, "/proj/.scratch/decision-*.md"), "pid": count(d, "/data/**/*.pid")}))
elif mode == "pings":
    p, d = spawn("pings")
    setup(p)
    N = 2000

    def w():
        try:
            for i in range(N):
                send(p, {"jsonrpc": "2.0", "id": 1000 + i, "method": "ping"})
        except Exception:
            pass
    t = threading.Thread(target=w)
    t.start()
    time.sleep(0.02)
    os.kill(p.pid, signal.SIGTERM)
    got = sum(1 for _ in p.stdout)
    print(json.dumps({"build": build, "rc": p.wait(10), "replies_read": got, "sent_max": N}))
elif mode == "race":
    res = {}
    for i in range(60):
        p, d = spawn("race-%d" % i)
        send(p, drive.init_msg())
        p.stdout.readline()
        send(p, drive.call_msg(2, "begin_interview", {"tool_schema": 4, "skill": "grill-me", "mode": "register"}))
        send(p, drive.call_msg(3, "log_item", {"id": "q1", "kind": "question", "text": "t"}))
        p.stdout.readline()
        p.stdout.readline()
        if i % 2 == 0:  # EOF, then the signal 0 to 3 ms later
            p.stdin.close()
            time.sleep(i * 0.00005)
            try:
                os.kill(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:           # the signal, then EOF 0 to 3 ms later
            os.kill(p.pid, signal.SIGTERM)
            time.sleep(i * 0.00005)
            try:
                p.stdin.close()
            except Exception:
                pass
        rc = p.wait(timeout=10)
        ev = sum(1 for _ in open(glob.glob(d + "/data/**/*.jsonl", recursive=True)[0]))
        ok = count(d, "/data/**/*.pid") == 0 and count(d, "/proj/.scratch/decision-*.md") == 1 and ev == 2  # the files, not the exit code
        k = ("EOF-first" if i % 2 == 0 else "signal-first", rc, ok)
        res[k] = res.get(k, 0) + 1
    print(build, {"%s rc=%s ok=%s" % k: v for k, v in res.items()})
else:
    sys.exit(__doc__)
