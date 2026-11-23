import assert from "node:assert/strict";
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { projectDir } from "../src/journal.ts";
import { startTime } from "../src/pidfile.ts";
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

const begin = { tool_schema: 5, skill: "grill-me", mode: "register" };

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

function seedUnfinished(dir: string, id: string, pidLine: string | null): void {
  mkdirSync(dir, { recursive: true });
  const lines = [
    {
      v: 2,
      ts: "2026-11-16T09:30:15Z",
      seq: 1,
      kind: "start",
      interview_id: id,
      route: "begin_interview",
      skill: "grill-me",
      session_id: "old",
      implicit: false,
      prompt_count: 0,
    },
    {
      v: 2,
      ts: "2026-11-16T09:30:15Z",
      seq: 2,
      kind: "item",
      id: "q1",
      item_kind: "question",
      text: "t",
      parent: null,
      depends_on: [],
    },
  ];
  writeFileSync(
    join(dir, `${id}.jsonl`),
    `${lines.map((l) => JSON.stringify(l)).join("\n")}\n`,
  );
  if (pidLine !== null) writeFileSync(join(dir, `${id}.pid`), pidLine);
}

test("status lists unfinished journals with dead owners, and the item tools refuse", () => {
  const dir = sandbox();
  seedUnfinished(
    dir,
    "iv-a",
    '{"pid":1073741824,"start":null,"interview_id":"iv-a"}\n',
  );
  seedUnfinished(dir, "iv-b", null);
  const live = {
    pid: process.ppid,
    start: startTime(process.ppid),
    interview_id: "iv-c",
  };
  seedUnfinished(dir, "iv-c", `${JSON.stringify(live)}\n`);
  const app = new App();
  const ids = call(app, "status")
    ["resumable"].map((r: R) => r.interview_id)
    .sort();
  assert.deepEqual(ids, ["iv-a", "iv-b"]);
  assert.equal(call(app, "status")["resumable"][0].last_event, 2);
  for (const [name, args] of [
    ["begin_interview", begin],
    ["log_item", { kind: "question", text: "x" }],
    ["resolve", { id: "q1", answer: "a", source: "model", rationale: "r" }],
    ["park", { id: "q1", reason: "r", parked_by: "model" }],
    ["reopen", { id: "q1", reason: "r" }],
  ] as const) {
    const r = app.call(name, args, true);
    assert.equal(r?.isError, true, name);
    assert.match(r?.text ?? "", /unfinished journal: call resume or discard/);
  }
  assert.equal(call(app, "status")["state"], "none");
});

const live = (id: string): string =>
  `${JSON.stringify({ pid: process.ppid, start: startTime(process.ppid), interview_id: id })}\n`;
const dead = (id: string): string =>
  `${JSON.stringify({ pid: 2 ** 30, start: null, interview_id: id })}\n`;

test("discard errors: nothing, unknown, live-owned, several, wrong state", () => {
  const dir = sandbox();
  const app = new App();
  const err = (name: string, args: R = {}): string => {
    const r = app.call(name, args, true);
    assert.equal(r?.isError, true);
    return r?.text ?? "";
  };
  assert.match(err("discard"), /no unfinished journal/);
  seedUnfinished(dir, "iv-a", dead("iv-a"));
  seedUnfinished(dir, "iv-b", null);
  seedUnfinished(dir, "iv-c", live("iv-c"));
  assert.match(
    err("discard", { id: "iv-zzz" }),
    /unknown interview id: iv-zzz/,
  );
  assert.match(err("discard", { id: "iv-c" }), /journal is live-owned: iv-c/);
  assert.match(
    err("discard"),
    /several unfinished journals: .*iv-a.*iv-b|several unfinished journals: .*iv-b.*iv-a/,
  );
  assert.deepEqual(call(app, "discard", { id: "iv-a" }), {
    interview_id: "iv-a",
    archived: true,
  });
  assert.equal(existsSync(join(dir, "iv-a.jsonl")), false);
  assert.equal(existsSync(join(dir, "iv-a.pid")), false);
  assert.ok(existsSync(join(dir, "archive", "iv-a.jsonl")));
  // The one left is taken without an id; an archived id is unknown.
  assert.equal(call(app, "discard")["interview_id"], "iv-b");
  assert.match(err("discard", { id: "iv-a" }), /unknown interview id/);
  call(app, "begin_interview", begin);
  assert.match(err("discard"), /wrong state: active/);
  assert.match(err("resume"), /wrong state: active/);
});

test("discard in pending leaves the pending interview alone", () => {
  const dir = sandbox();
  seedUnfinished(dir, "iv-a", null);
  const app = new App();
  app.call(
    "skill_started",
    { tool_schema: 5, session_id: "s", skill: "grill-me" },
    false,
  );
  assert.equal(call(app, "status")["state"], "pending");
  call(app, "discard");
  const st = call(app, "status");
  assert.equal(st["state"], "pending");
  assert.deepEqual(st["resumable"], []);
});

test("resume claims the pid file, replays, appends a resume event and returns active", () => {
  const dir = sandbox();
  seedUnfinished(dir, "iv-a", dead("iv-a"));
  const app = new App();
  app.call("note_prompt", { session_id: "sess-2", prompt: "hi" }, false);
  const r = call(app, "resume");
  assert.deepEqual(r, {
    interview_id: "iv-a",
    state: "active",
    last_event: 2,
    items: { open: 1, resolved: 0, parked: 0, stale: 0 },
  });
  const rec = JSON.parse(readFileSync(join(dir, "iv-a.pid"), "utf8"));
  assert.equal(rec.pid, process.pid);
  const lines = readFileSync(join(dir, "iv-a.jsonl"), "utf8")
    .trim()
    .split("\n");
  const last = JSON.parse(lines[lines.length - 1] ?? "");
  assert.deepEqual(
    [last.kind, last.seq, last.from_seq, last.session_id],
    ["resume", 3, 2, "sess-2"],
  );
  const st = call(app, "status");
  assert.equal(st["state"], "active");
  assert.equal(st["interview_id"], "iv-a");
  assert.equal(st["last_event"], 3);
  assert.equal(st["tree"][0].id, "q1");
  assert.deepEqual(st["resumable"], []);
  // Wrong state once active.
  assert.equal(app.call("resume", {}, true)?.isError, true);
});

test("resume restores status.owner_skill from the start event (REQ-15)", () => {
  const dir = sandbox();
  seedUnfinished(dir, "iv-a", dead("iv-a"));
  const app = new App();
  app.call("note_prompt", { session_id: "sess-2", prompt: "hi" }, false);
  assert.equal(call(app, "status")["owner_skill"], null);
  call(app, "resume");
  assert.equal(call(app, "status")["owner_skill"], "grill-me");
});

test("resume replay failure names the line, removes the new pid file and keeps the journal", () => {
  const dir = sandbox();
  seedUnfinished(dir, "iv-a", dead("iv-a"));
  appendFileSync(
    join(dir, "iv-a.jsonl"),
    `${JSON.stringify({ v: 3, seq: 3, kind: "hook" })}\n`,
  );
  const app = new App();
  const r = app.call("resume", { id: "iv-a" }, true);
  assert.equal(r?.isError, true);
  assert.match(r?.text ?? "", /replay failed at line 3(?!\d)/);
  assert.equal(existsSync(join(dir, "iv-a.pid")), false);
  assert.ok(existsSync(join(dir, "iv-a.jsonl")));
  assert.equal(call(app, "status")["state"], "none");
});

test("resume cuts a torn final line before appending", () => {
  const dir = sandbox();
  seedUnfinished(dir, "iv-a", null);
  appendFileSync(join(dir, "iv-a.jsonl"), '{"v":2,"ts"');
  const app = new App();
  assert.equal(call(app, "resume")["last_event"], 2);
  for (const l of readFileSync(join(dir, "iv-a.jsonl"), "utf8")
    .trim()
    .split("\n")) {
    JSON.parse(l);
  }
});

test("resume while pending stands the pending interview down after success", () => {
  const dir = sandbox();
  seedUnfinished(dir, "iv-a", dead("iv-a"));
  const app = new App();
  app.call(
    "skill_started",
    { tool_schema: 5, session_id: "s", skill: "grill-me" },
    false,
  );
  const pendingId = call(app, "status")["interview_id"];
  call(app, "resume");
  assert.equal(call(app, "status")["interview_id"], "iv-a");
  const archived = readFileSync(
    join(dir, "archive", `${pendingId}.jsonl`),
    "utf8",
  );
  assert.match(archived, /"reason":"replaced by resume"/);
  assert.equal(existsSync(join(dir, `${pendingId}.pid`)), false);
});
