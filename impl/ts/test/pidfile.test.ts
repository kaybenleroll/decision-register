import assert from "node:assert/strict";
import {
  existsSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  ClaimError,
  claim,
  createPid,
  encode,
  ownerAlive,
  ownRecord,
  parsePid,
  pidPath,
  startTime,
} from "../src/pidfile.ts";

const tmp = (): string => mkdtempSync(join(tmpdir(), "pidtest-"));

test("start time of this process is an integer", () => {
  assert.ok(startTime(process.pid) !== null);
  assert.equal(startTime(2 ** 30), null);
});

test("parsePid rejects junk and accepts a record", () => {
  assert.equal(parsePid(null), null);
  assert.equal(parsePid(""), null);
  assert.equal(parsePid("not json"), null);
  assert.equal(parsePid('{"pid":"x","start":null}'), null);
  assert.deepEqual(parsePid('{"pid":5,"start":null,"interview_id":"a"}'), {
    pid: 5,
    start: null,
    interview_id: "a",
  });
});

test("liveness: own pid needs the current claim, a dead pid or other start is dead", () => {
  const mine = encode(ownRecord("a"));
  assert.equal(ownerAlive(mine, true), true);
  assert.equal(ownerAlive(mine, false), false);
  assert.equal(ownerAlive(null, false), false);
  assert.equal(ownerAlive("garbage", false), false);
  const parent = process.ppid;
  const live = { pid: parent, start: startTime(parent), interview_id: "a" };
  assert.equal(ownerAlive(encode(live), false), true);
  assert.equal(ownerAlive(encode({ ...live, start: 1 }), false), false);
  assert.equal(ownerAlive(encode({ ...live, start: null }), false), true);
  assert.equal(
    ownerAlive(encode({ pid: 2 ** 30, start: null, interview_id: "a" }), false),
    false,
  );
});

test("createPid is exclusive and mode 0600", () => {
  const dir = tmp();
  const p = pidPath(dir, "a");
  createPid(p, ownRecord("a"));
  assert.throws(() => createPid(p, ownRecord("a")), { code: "EEXIST" });
  assert.match(
    readFileSync(p, "utf8"),
    /^\{"pid":\d+,"start":\d+,"interview_id":"a"\}\n$/,
  );
});

test("claim replaces a stale file and removes the aside", () => {
  const dir = tmp();
  const p = pidPath(dir, "a");
  const stale = '{"pid":1073741824,"start":null,"interview_id":"a"}\n';
  writeFileSync(p, stale);
  claim(dir, "a", stale);
  assert.equal(parsePid(readFileSync(p, "utf8"))?.pid, process.pid);
  assert.deepEqual(readdirSync(dir), ["a.pid"]);
});

test("claim with no file creates exclusively", () => {
  const dir = tmp();
  claim(dir, "a", null);
  assert.ok(existsSync(pidPath(dir, "a")));
});

test("claim fails when the file changed between read and rename (another claimer's live file)", () => {
  const dir = tmp();
  const p = pidPath(dir, "a");
  writeFileSync(p, '{"pid":7,"start":null,"interview_id":"a"}\n');
  assert.throws(() => claim(dir, "a", "stale content"), ClaimError);
  // Put back because no new file existed.
  assert.equal(
    readFileSync(p, "utf8"),
    '{"pid":7,"start":null,"interview_id":"a"}\n',
  );
});

test("claim loses the race to a third process in the window after the rename", () => {
  const dir = tmp();
  const p = pidPath(dir, "a");
  const stale = '{"pid":1073741824,"start":null,"interview_id":"a"}\n';
  writeFileSync(p, stale);
  const other = '{"pid":9,"start":null,"interview_id":"a"}\n';
  assert.throws(
    () => claim(dir, "a", stale, () => writeFileSync(p, other)),
    ClaimError,
  );
  assert.equal(readFileSync(p, "utf8"), other);
});
