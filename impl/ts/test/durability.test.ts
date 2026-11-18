import assert from "node:assert/strict";
import {
  existsSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { projectDir } from "../src/journal.ts";
import { App } from "../src/tools.ts";

type R = Record<string, any>;

/** A fresh data and project directory per test; returns the project folder. */
function sandbox(): string {
  process.env["CLAUDE_PLUGIN_DATA"] = mkdtempSync(join(tmpdir(), "dur-data-"));
  process.env["CLAUDE_PROJECT_DIR"] = mkdtempSync(join(tmpdir(), "dur-proj-"));
  delete process.env["CLAUDE_CODE_SESSION_ID"];
  return projectDir();
}

function call(app: App, name: string, args: unknown = {}): R {
  const r = app.call(name, args, true);
  assert.ok(r !== null);
  if (r.isError) throw new Error(r.text);
  return JSON.parse(r.text);
}

const begin = { tool_schema: 4, skill: "grill-me", mode: "register" };

test("a new interview owns a pid file with its pid and start time, mode 0600", () => {
  const dir = sandbox();
  const app = new App();
  const id = call(app, "begin_interview", begin)["interview_id"];
  const path = join(dir, `${id}.pid`);
  assert.equal(statSync(path).mode & 0o777, 0o600);
  const text = readFileSync(path, "utf8");
  assert.ok(text.endsWith("\n"));
  const rec = JSON.parse(text);
  assert.equal(rec.pid, process.pid);
  assert.equal(rec.interview_id, id);
  assert.equal(typeof rec.start, "number");
});

test("ending the interview removes the pid file and archives the journal", () => {
  const dir = sandbox();
  const app = new App();
  const id = call(app, "begin_interview", begin)["interview_id"];
  call(app, "end_interview", {});
  assert.equal(existsSync(join(dir, `${id}.pid`)), false);
  assert.equal(existsSync(join(dir, `${id}.jsonl`)), false);
  assert.ok(existsSync(join(dir, "archive", `${id}.jsonl`)));
  assert.deepEqual(readdirSync(dir), ["archive"]);
});
