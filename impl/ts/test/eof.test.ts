import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import { projectKey } from "../src/journal.ts";
import { App } from "../src/tools.ts";

delete process.env["CLAUDE_CODE_SESSION_ID"];

const base = join(process.cwd(), ".scratch");
mkdirSync(base, { recursive: true });
const data = mkdtempSync(join(base, "eof-data-"));
const proj = mkdtempSync(join(base, "eof-proj-"));
process.env["CLAUDE_PLUGIN_DATA"] = data;
process.env["CLAUDE_PROJECT_DIR"] = proj;

function call(app: App, name: string, args: unknown, model = true) {
  const r = app.call(name, args, model);
  assert.ok(r !== null && !r.isError, r === null ? "unknown tool" : r.text);
  return JSON.parse(r.text) as Record<string, any>;
}

const BEGIN = { tool_schema: 5, skill: "grill-me", mode: "register" };
const dir = join(data, projectKey());
const logOf = (id: string): string =>
  readFileSync(join(proj, "decision-logs", `decision-${id}.md`), "utf8");

test("EOF with no interview does nothing", () => {
  new App().onEof();
});

test("EOF on an active interview keeps the journal unfinished and flags the log", () => {
  const app = new App();
  const id = call(app, "begin_interview", BEGIN)["interview_id"];
  call(app, "log_item", { kind: "question", text: "open" });
  app.onEof();
  assert.ok(existsSync(join(dir, `${id}.jsonl`)));
  assert.ok(!existsSync(join(dir, `${id}.pid`)));
  assert.ok(!existsSync(join(dir, "archive", `${id}.jsonl`)));
  assert.match(logOf(id), /^end_kind: interrupted$/m);
  assert.doesNotMatch(
    readFileSync(join(dir, `${id}.jsonl`), "utf8"),
    /"kind":"end"/,
  );
});

test("EOF on a pending interview stands it down and archives it", () => {
  const app = new App();
  call(
    app,
    "skill_started",
    { tool_schema: 5, session_id: "s-eof", skill: "grill-me" },
    false,
  );
  const id = call(app, "status", {})["interview_id"];
  app.onEof();
  const lines = readFileSync(join(dir, "archive", `${id}.jsonl`), "utf8");
  assert.match(lines, /server exit while pending/);
  assert.match(logOf(id), /^mode: not engaged$/m);
});
