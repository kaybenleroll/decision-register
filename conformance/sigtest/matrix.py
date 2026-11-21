"""The 16-run matrix: for each build (rust, ts), flushed and unflushed, EOF and the three signals through the launcher, then a
fresh server resumes. Prints one row per run, whether it equals its EOF row, and (with both builds) the cross-build comparison.
Usage: python3 -I conformance/sigtest/matrix.py [rust] [ts]   (default: both). Output below $SIGTEST_OUT (see drive.py)."""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive

WANT = {"EOF": 0, "SIGHUP": 129, "SIGINT": 130, "SIGTERM": 143}


def mask(x):
    """Interview ids differ in every run; mask them before comparing."""
    return re.sub(r"iv-\d{8}T\d{6}Z-[0-9a-f]+", "<ID>", json.dumps(x, sort_keys=True))


def norm(r):
    return dict(lines=r.get("lines"), last=r.get("last"), ends_nl=r.get("ends_nl"), kinds=r.get("kinds"), pid_files=len(r["pid_files"]),
                declog=len(r["declog"]), status1=mask(r.get("status1")), items=r.get("items"),
                tree=[(t["id"], t["status"]) for t in (r.get("tree") or [])], resume_err=r.get("resume_err"))


def end_kinds(d):
    out = []
    for a, _, fs in os.walk(d + "/project"):  # os.walk: glob's ** skips the hidden .scratch folder
        for f in fs:
            if f.startswith("decision-"):
                m = re.search(r"^end_kind: (.*)$", open(os.path.join(a, f)).read(), re.M)
                out.append(m.group(1) if m else None)
    return out


builds = sys.argv[1:] or ["rust", "ts"]
allr = {}
for build in builds:
    rows = {}
    for variant in ("unflushed", "flushed"):
        for sig in WANT:
            rows[(variant, sig)] = drive.run(build, variant, sig)
    allr[build] = rows
    for (variant, sig), r in rows.items():
        same = norm(r) == norm(rows[(variant, "EOF")])
        ek = end_kinds("%s/%s-%s-%s" % (drive.BASE, build, variant, sig))
        print("%-5s %-9s %-8s rc=%-4s (want %d) events=%s last=%s pidfiles=%d declog=%d end_kind=%s resumable=%s items=%s tree=%s match_EOF_row=%s"
              % (build, variant, sig, r["rc"], WANT[sig], r.get("lines"), r.get("last"), len(r["pid_files"]), len(r["declog"]), ek,
                 bool(r["status1"].get("resumable")), r.get("items"), [(t["id"], t["status"]) for t in (r.get("tree") or [])], same),
              flush=True)
if len(allr) == 2:
    a, b = list(allr)
    for k in allr[a]:
        print("CROSS", k, "rc", allr[a][k]["rc"], allr[b][k]["rc"], "normalised-equal", norm(allr[a][k]) == norm(allr[b][k]))
