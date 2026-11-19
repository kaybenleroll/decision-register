#!/bin/bash
# Offline check of bin/register-use (no env var build choice). Drives the REAL plugin/bin/register-launch with
# REGSPIKE_IMPL unset and a temp bin dir first on PATH. Spends nothing; touches nothing outside .scratch/.
# Build both first: (cd impl/ts && npm run build); (cd impl/rust && ~/.cargo/bin/cargo build --release)
set -u
root=$(cd "$(dirname "$0")/.." && pwd -P)
tmp=$root/.scratch/register-use-check
rm -rf "$tmp"; mkdir -p "$tmp"
export REGISTER_BIN_DIR=$tmp/bin REGISTER_SHARE_DIR=$tmp/share
unset REGSPIKE_IMPL
fail=0
ok() { echo "ok - $1"; }
no() { echo "not ok - $1"; fail=1; }
check() { if [ "$2" = 0 ]; then ok "$1"; else no "$1"; fi; }

# drive <expected comm-pattern>: initialize + tools/list through the launcher; prints tool count and process cmdline
drive() {
    PATH=$REGISTER_BIN_DIR:$PATH python3 -I - "$root/plugin/bin/register-launch" <<'PY'
import json, subprocess, sys, time
p = subprocess.Popen([sys.argv[1]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
def rpc(i, m, params=None):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": m, "params": params or {}}) + "\n"); p.stdin.flush()
    return json.loads(p.stdout.readline())
rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"); p.stdin.flush()
n = len(rpc(2, "tools/list")["result"]["tools"])
cmd = open("/proc/%d/cmdline" % p.pid, "rb").read().replace(b"\0", b" ").decode().strip()
print("TOOLS=%d" % n); print("CMDLINE=" + cmd)
p.stdin.close(); rc = p.wait(timeout=10); print("EXIT=%d" % rc)
PY
}

mk() { "$root/bin/register-use" "$@" >"$tmp/out" 2>&1; }

for impl in ts rs; do
    mk $impl; check "register-use $impl exits 0" $?
    out=$(drive)
    echo "$out" | grep -q '^TOOLS=12$'; check "$impl: 12 tools through the launcher" $?
    cmdline=$(echo "$out" | sed -n 's/^CMDLINE=//p')
    if [ $impl = ts ]; then echo "$cmdline" | grep -Eq '^node .*impl/ts/dist/regspike\.mjs'; else echo "$cmdline" | grep -Eq '(^|/)regspike-rs( |$)'; fi
    check "$impl: launcher process is: $cmdline" $?
    echo "$out" | grep -q '^EXIT=0$'; check "$impl: exits 0 when stdin closes" $?
done

# idempotent
mk rs; mk rs; check "rs twice is idempotent" $?
[ "$("$root/bin/register-use" status | head -1)" = "active: rs" ]; check "status reports rs" $?
REGSPIKE_IMPL=ts "$root/bin/register-use" status | grep -q 'REGSPIKE_IMPL=ts is set'; check "status warns when REGSPIKE_IMPL is set" $?

# wrapper passes SIGINT, stdin/stdout and exit status (exec: same pid)
mk ts
"$REGISTER_SHARE_DIR/ts" </dev/null >/dev/null 2>&1; rc=$?
check "ts wrapper returns the server exit status on stdin EOF ($rc)" $rc
mkfifo "$tmp/in"; exec 3<>"$tmp/in"
"$REGISTER_SHARE_DIR/ts" <"$tmp/in" >/dev/null 2>&1 &
pid=$!; sleep 1
comm=$(tr '\0' ' ' </proc/$pid/cmdline); kill -INT $pid 2>/dev/null; wait $pid; rc=$?; exec 3>&-
echo "info: wrapper pid $pid is now: $comm; SIGINT exit status $rc"
case $comm in node*) ok "wrapper exec'd node (same pid)" ;; *) no "wrapper exec'd node (same pid)" ;; esac

# refuse a real file, and a foreign symlink
rm -f "$REGISTER_BIN_DIR/regspike-rs"; echo real >"$REGISTER_BIN_DIR/regspike-rs"
mk ts; rc=$?; [ $rc != 0 ] && [ "$(cat "$REGISTER_BIN_DIR/regspike-rs")" = real ]; check "refuses to overwrite a real file" $?
rm -f "$REGISTER_BIN_DIR/regspike-rs"; ln -s /bin/true "$REGISTER_BIN_DIR/regspike-rs"
mk ts; rc=$?; [ $rc != 0 ] && [ "$(readlink "$REGISTER_BIN_DIR/regspike-rs")" = /bin/true ]; check "refuses to replace a foreign symlink" $?

# missing builds
mkdir -p "$tmp/fake/bin"; cp "$root/bin/register-use" "$tmp/fake/bin/"
rm -f "$REGISTER_BIN_DIR/regspike-rs"
"$tmp/fake/bin/register-use" rs 2>&1 | grep -q 'cargo build --release'; check "missing Rust build names the cargo command" $?
"$tmp/fake/bin/register-use" ts 2>&1 | grep -q 'npm run build'; check "missing TS build names npm run build" $?

[ $fail = 0 ] && echo "all checks passed" || echo "FAILED"
exit $fail
