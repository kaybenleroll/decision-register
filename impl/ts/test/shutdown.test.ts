import assert from "node:assert/strict";
import { test } from "node:test";

import { Shutdown, SIGNALS } from "../src/shutdown.ts";

function make(runThrows = false) {
  const calls = { run: 0, exit: [] as number[] };
  const s = new Shutdown(
    () => {
      calls.run += 1;
      if (runThrows) throw new Error("boom");
    },
    (code) => {
      calls.exit.push(code);
    },
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

test("EOF then signal: no second shutdown, no second exit, status stays 0", () => {
  const { s, calls } = make();
  s.atEof();
  s.atSignal(SIGNALS.SIGINT);
  s.atSignal(SIGNALS.SIGTERM);
  assert.equal(calls.run, 1);
  assert.deepEqual(calls.exit, [0]);
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
