import assert from "node:assert/strict";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
} from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import { Journal, projectKey } from "../src/journal.ts";

const base = join(process.cwd(), ".scratch");
mkdirSync(base, { recursive: true });
const data = mkdtempSync(join(base, "queue-data-"));
process.env["CLAUDE_PLUGIN_DATA"] = data;
process.env["CLAUDE_PROJECT_DIR"] = join(base, "queue-proj");

const seqs = (file: string): number[] =>
  readFileSync(file, "utf8")
    .trim()
    .split("\n")
    .map((l) => JSON.parse(l).seq);

test("events are queued until flush, in order, with seq counting queued events", () => {
  const j = new Journal("iv-q1");
  j.append("start", {});
  j.append("hook", {});
  const file = join(data, projectKey(), "iv-q1.jsonl");
  assert.ok(!existsSync(file));
  assert.equal(j.seq, 2);
  j.flush();
  assert.deepEqual(seqs(file), [1, 2]);
  j.append("stop", {});
  j.flush();
  assert.deepEqual(seqs(file), [1, 2, 3]);
});

test("a failed flush keeps the queue and sets writeError; a later flush writes in order", () => {
  const j = new Journal("iv-q2");
  const key = projectKey();
  const dir = join(data, key);
  mkdirSync(dir, { recursive: true });
  chmodSync(dir, 0o500);
  j.append("start", {});
  j.append("hook", {});
  j.flush();
  if (process.getuid?.() !== 0) assert.ok(j.writeError !== null);
  j.archive(); // queued events: no rename attempted
  assert.ok(!existsSync(join(dir, "archive", "iv-q2.jsonl")));
  chmodSync(dir, 0o700);
  j.flush();
  assert.equal(j.writeError, null);
  assert.deepEqual(seqs(join(dir, "iv-q2.jsonl")), [1, 2]);
  j.archive();
  assert.ok(existsSync(join(dir, "archive", "iv-q2.jsonl")));
});
