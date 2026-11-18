import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import { DecisionLog, type LogSnapshot } from "../src/declog.ts";
import { Journal, projectKey } from "../src/journal.ts";

function scratch(): string {
  const base = join(process.cwd(), ".scratch");
  mkdirSync(base, { recursive: true });
  return mkdtempSync(join(base, "declog-"));
}

const snap = (over: Partial<LogSnapshot> = {}): LogSnapshot => ({
  interviewId: "iv-1",
  journalPosition: 3,
  state: "active",
  mode: "register",
  endKind: "none",
  implicitStart: false,
  note: null,
  items: [],
  hookHealth: { note_prompt: 1 },
  ...over,
});

test("log goes to decision-logs/ with an ignore file when .scratch/ is absent", () => {
  const proj = scratch();
  process.env["CLAUDE_PROJECT_DIR"] = proj;
  const log = new DecisionLog("iv-1");
  log.write(snap());
  const path = log.path as string;
  assert.equal(path, join(proj, "decision-logs", "decision-iv-1.md"));
  assert.equal(
    readFileSync(join(proj, "decision-logs", ".gitignore"), "utf8"),
    "*\n",
  );
  assert.equal(statSync(path).mode & 0o777, 0o600);
  const lines = readFileSync(path, "utf8").split("\n");
  assert.deepEqual(lines.slice(0, 6), [
    "interview_id: iv-1",
    "journal_position: 3",
    "state: active",
    "mode: register",
    "end_kind: none",
    "implicit_start: false",
  ]);
  assert.equal(existsSync(`${path}.tmp`), false);
});

test("log goes to .scratch/ when it exists", () => {
  const proj = scratch();
  mkdirSync(join(proj, ".scratch"));
  process.env["CLAUDE_PROJECT_DIR"] = proj;
  const log = new DecisionLog("iv-2");
  log.write(snap({ interviewId: "iv-2" }));
  assert.equal(log.path, join(proj, ".scratch", "decision-iv-2.md"));
});

test("an unwritable log directory fails open", () => {
  const file = join(scratch(), "a-file");
  writeFileSync(file, "x");
  process.env["CLAUDE_PROJECT_DIR"] = file;
  const log = new DecisionLog("iv-3");
  log.write(snap());
  assert.equal(log.path, null);
  assert.notEqual(log.writeError, null);
});

test("archive moves the journal into archive/", () => {
  const data = scratch();
  process.env["CLAUDE_PLUGIN_DATA"] = data;
  process.env["CLAUDE_PROJECT_DIR"] = scratch();
  const j = new Journal("iv-4");
  j.append("start", {});
  j.archive();
  assert.equal(j.writeError, null);
  const dir = join(data, projectKey());
  assert.equal(existsSync(join(dir, "iv-4.jsonl")), false);
  assert.equal(existsSync(join(dir, "archive", "iv-4.jsonl")), true);
});
