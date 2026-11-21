import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

import { DRAIN_BOUND_MS, Shutdown, SIGNALS } from "../src/shutdown.ts";

function make(runThrows = false, boundMs?: number) {
  const calls = { run: 0, exit: [] as number[], drain: [] as boolean[] };
  const s = new Shutdown(
    () => {
      calls.run += 1;
      if (runThrows) throw new Error("boom");
    },
    (code, drain) => {
      calls.exit.push(code);
      calls.drain.push(drain);
    },
    boundMs,
  );
  return { s, calls };
}

test("signal numbers give the shell exit codes", () => {
  assert.deepEqual(
    Object.entries(SIGNALS).map(([n, v]) => [n, 128 + v]),
    [
      ["SIGHUP", 129],
      ["SIGINT", 130],
      ["SIGTERM", 143],
    ],
  );
});

test("EOF twice runs the shutdown once and exits 0 once", () => {
  const { s, calls } = make();
  s.atEof();
  s.atEof();
  assert.equal(calls.run, 1);
  assert.deepEqual(calls.exit, [0]);
});

test("a failing EOF shutdown does not exit 0 behind its error", () => {
  const { s, calls } = make(true);
  assert.throws(() => s.atEof(), /boom/);
  assert.equal(calls.run, 1);
  assert.deepEqual(calls.exit, []);
});

test("signal twice runs the shutdown once", () => {
  const { s, calls } = make();
  s.atSignal(SIGNALS.SIGTERM);
  s.atSignal(SIGNALS.SIGINT);
  assert.equal(calls.run, 1);
  assert.deepEqual(calls.exit, [143, 130]);
});

test("a signal first exits 128 + n", () => {
  for (const [, num] of Object.entries(SIGNALS)) {
    const { s, calls } = make();
    s.atSignal(num);
    assert.equal(calls.run, 1);
    assert.deepEqual(calls.exit, [128 + num]);
  }
});

test("EOF drains pending output before exit 0; a signal exits without draining", () => {
  const eof = make();
  eof.s.atEof();
  assert.deepEqual(eof.calls.exit, [0]);
  assert.deepEqual(eof.calls.drain, [true]);
  for (const [, num] of Object.entries(SIGNALS)) {
    const sig = make();
    sig.s.atSignal(num);
    assert.deepEqual(sig.calls.drain, [false]);
  }
});

test("EOF then signal: no second shutdown, the signal ends the drain, status stays 0", () => {
  const { s, calls } = make();
  s.atEof();
  s.atSignal(SIGNALS.SIGINT);
  s.atSignal(SIGNALS.SIGTERM);
  assert.equal(calls.run, 1);
  assert.deepEqual(calls.exit, [0, 0, 0]);
  assert.deepEqual(calls.drain, [true, false, false]);
});

test("signal then EOF: EOF does not shut down again", () => {
  const { s, calls } = make();
  s.atSignal(SIGNALS.SIGHUP);
  s.atEof();
  assert.equal(calls.run, 1);
  assert.deepEqual(calls.exit, [129]);
});

test("a failing shutdown still exits with the signal code, once run", () => {
  const { s, calls } = make(true);
  assert.throws(() => s.atSignal(SIGNALS.SIGTERM), /boom/);
  assert.equal(calls.run, 1);
  assert.deepEqual(calls.exit, [143]);
});

test("the default drain bound is the 5 s of the Rust signal bound", () => {
  assert.equal(DRAIN_BOUND_MS, 5000);
});

test("a stalled EOF drain ends with exit(0, false) after the bound, once", async () => {
  const { s, calls } = make(false, 20);
  s.atEof();
  assert.deepEqual(calls.exit, [0]);
  await sleep(5);
  assert.deepEqual(calls.drain, [true], "not before the bound");
  await sleep(80);
  assert.deepEqual(calls.exit, [0, 0]);
  assert.deepEqual(calls.drain, [true, false]);
  assert.equal(calls.run, 1);
});

test("drained() clears the bound timer: no second exit", async () => {
  const { s, calls } = make(false, 20);
  s.atEof();
  s.drained();
  await sleep(80);
  assert.deepEqual(calls.drain, [true]);
});

test("a signal shutdown starts no bound timer", async () => {
  const { s, calls } = make(false, 10);
  s.atSignal(SIGNALS.SIGTERM);
  await sleep(50);
  assert.deepEqual(calls.exit, [143]);
});

test("the bound timer is unref'd: a process with nothing else pending exits at once", () => {
  const mod = fileURLToPath(new URL("../src/shutdown.ts", import.meta.url));
  const script = `import { Shutdown } from ${JSON.stringify(mod)};
new Shutdown(() => {}, () => {}, 60000).atEof();`;
  const t0 = Date.now();
  const r = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
    timeout: 20000,
  });
  assert.equal(r.status, 0, String(r.stderr));
  assert.ok(Date.now() - t0 < 20000, "did not wait for the 60 s timer");
});
