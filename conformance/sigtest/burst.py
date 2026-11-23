"""4000 pipelined log_item calls with a signal sent mid-flight, repeated.
Usage: python3 -I conformance/sigtest/burst.py <rust|ts> <SIGINT|SIGTERM|SIGHUP> ITERS DELAY_S TAG <late|live>
`late`: the reader starts after the signal (the pipe fills, the server blocks in a reply write); `live`: the reader runs throughout.
Good = journal whole and gapless, journal items exceed received replies by 0 or 1 (a reply in flight at exit is journalled but not
delivered), no pid file, one decision log flagged interrupted, exit code 128 plus the signal number. Note: for ts the `late` reader is not a valid pass criterion: only part of the replies arrive (not diagnosed), so the
journal holds all 4000 items (the journal is still whole); use `live` for ts.
Output below $SIGTEST_OUT/burst-<build>-TAG (TAG must be new for each run)."""
import glob, json, os, signal, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive

build, signame, iters, delay, tag, reader = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), sys.argv[5], sys.argv[6]
sig = getattr(signal, signame)
want_rc = 128 + int(sig)
N = 4000
ok, bad, lat, diffs = 0, [], [], []
for i in range(iters):
    d = "%s/burst-%s-%s/%s-%d" % (drive.BASE, build, tag, signame, i)
    os.makedirs(d + "/data")
    os.makedirs(d + "/proj/.scratch")
    p = subprocess.Popen(drive.command(build), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         env=drive.server_env(d + "/data", d + "/proj"), cwd=d + "/proj")
    assert os.readlink("/proc/%d/cwd" % p.pid) == d + "/proj"
    msgs = [drive.init_msg(1), drive.call_msg(2, "begin_interview", {"tool_schema": 5, "skill": "grill-me", "mode": "register"})]
    msgs += [drive.call_msg(10 + k, "log_item", {"id": "q%d" % k, "kind": "question", "text": "x" * 50}) for k in range(N)]
    data = "".join(json.dumps(m) + "\n" for m in msgs).encode()
    chunks = []

    def feed():
        try:
            p.stdin.write(data)
            p.stdin.flush()
        except Exception:
            pass

    def rd():
        chunks.append(p.stdout.read())

    t = threading.Thread(target=feed)
    t.start()
    rt = None
    if reader == "live":
        rt = threading.Thread(target=rd)
        rt.start()
    time.sleep(delay)
    t0 = time.monotonic()
    os.kill(p.pid, sig)  # the pid is the child this script started
    rc = p.wait()
    lat.append(time.monotonic() - t0)
    if rt:
        rt.join()
    else:
        rd()
    t.join()
    replies = []
    for line in b"".join(chunks).decode().split("\n"):
        if line.strip():
            try:
                replies.append(json.loads(line))
            except Exception:
                pass  # a truncated final line
    items = sum(1 for r in replies if r.get("id", 0) >= 10 and not r["result"].get("isError"))
    js = glob.glob(d + "/data/**/*.jsonl", recursive=True)
    ev = [json.loads(line) for line in open(js[0]) if line.strip()] if js else []
    seq = [e["seq"] for e in ev]
    nitem = sum(1 for e in ev if e["kind"] == "item")
    pids = glob.glob(d + "/data/**/*.pid", recursive=True)
    decl = glob.glob(d + "/proj/.scratch/decision-*.md")
    diff = nitem - items
    diffs.append(diff)
    good = (seq == list(range(1, len(seq) + 1)) and 0 <= diff <= 1 and not pids and len(decl) == 1
            and "interrupted" in open(decl[0]).read() and rc == want_rc and nitem > 0)
    ok += good
    if not good:
        bad.append((i, rc, len(replies), items, nitem, len(pids), len(decl)))
lat.sort()
print(build, tag, signame, reader, "good", ok, "/", iters, "bad", bad[:5], "journal-minus-replies",
      {x: diffs.count(x) for x in sorted(set(diffs))}, "signal->exit ms median %.1f max %.1f" % (lat[len(lat) // 2] * 1000, lat[-1] * 1000))
