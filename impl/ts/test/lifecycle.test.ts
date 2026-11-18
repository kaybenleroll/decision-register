import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import { projectKey } from "../src/journal.ts";
import { App } from "../src/tools.ts";

const HOOK = ["note_prompt", "skill_started", "check_stop"];

// The start-up fallback session id must not leak in from the environment running the tests.
delete process.env["CLAUDE_CODE_SESSION_ID"];

type R = Record<string, any>;

const base = join(process.cwd(), ".scratch");
mkdirSync(base, { recursive: true });
const data = mkdtempSync(join(base, "life-data-"));
const proj = mkdtempSync(join(base, "life-proj-"));
process.env["CLAUDE_PLUGIN_DATA"] = data;
process.env["CLAUDE_PROJECT_DIR"] = proj;

function call(
  app: App,
  name: string,
  args: unknown,
  modelFacing = !HOOK.includes(name),
): R {
  const r = app.call(name, args, modelFacing);
  assert.ok(r !== null, `unknown tool ${name}`);
  if (r.isError) throw new Error(r.text);
  return JSON.parse(r.text);
}

const fails = (app: App, name: string, args: unknown, re: RegExp): void =>
  assert.throws(() => call(app, name, args), re);

const BEGIN = { tool_schema: 4, skill: "grill-me", mode: "register" };
const hook = (app: App, name: string, args: Record<string, unknown>): R =>
  call(app, name, args, false);

function archived(id: string): string[] {
  const f = join(data, projectKey(), "archive", `${id}.jsonl`);
  assert.ok(existsSync(f), `archived journal missing: ${f}`);
  return readFileSync(f, "utf8").trim().split("\n");
}

test("end_interview validates its arguments before the state", () => {
  const app = new App();
  fails(app, "end_interview", { abandon: true }, /invalid arguments/);
  fails(app, "end_interview", { reason: "r" }, /invalid arguments/);
  fails(
    app,
    "end_interview",
    { abandon: false, reason: "r" },
    /invalid arguments/,
  );
  fails(
    app,
    "end_interview",
    { mode: "plain", abandon: true, reason: "r" },
    /invalid arguments/,
  );
  fails(app, "end_interview", { mode: "register" }, /invalid arguments/);
  fails(app, "end_interview", {}, /no interview to end/);
  fails(app, "end_interview", { mode: "plain" }, /no interview to end/);
});

test("end_interview ends an active interview with nothing open as normal", () => {
  const app = new App();
  const { interview_id: id } = call(app, "begin_interview", BEGIN);
  call(app, "log_item", {
    id: "q1",
    kind: "question",
    text: "t",
    answer: "a",
    source: "model",
    rationale: "r",
  });
  const r = call(app, "end_interview", {});
  assert.equal(r["end_kind"], "normal");
  assert.equal(r["state"], "ended");
  assert.match(r["decision_log"], new RegExp(`decision-${id}\\.md$`));
  const lines = archived(id).map((l) => JSON.parse(l));
  assert.equal(lines.at(-1)["kind"], "end");
  assert.equal(lines.at(-1)["end_kind"], "normal");
  assert.equal(lines.at(-1)["mode"], "register");
  const log = readFileSync(r["decision_log"], "utf8");
  assert.match(log, /^interview_id: /);
  assert.match(log, /\nstate: ended\n/);
  assert.match(log, /\nend_kind: normal\n/);
  const st = call(app, "status", {});
  assert.equal(st["state"], "none");
  assert.equal(st["interview_id"], null);
  assert.equal(st["decision_log"], null);
  assert.equal(st["items"]["resolved"], 0);
});

test("end_interview with items open needs abandon and a reason", () => {
  const app = new App();
  const { interview_id: id } = call(app, "begin_interview", BEGIN);
  call(app, "log_item", { id: "q1", kind: "question", text: "t" });
  fails(app, "end_interview", {}, /still open: q1/);
  fails(app, "end_interview", { abandon: true }, /invalid arguments/);
  const r = call(app, "end_interview", { abandon: true, reason: "user left" });
  assert.equal(r["end_kind"], "ended_incomplete_by_model");
  const end = archived(id)
    .map((l) => JSON.parse(l))
    .at(-1);
  assert.equal(end["reason"], "user left");
  assert.match(
    readFileSync(r["decision_log"], "utf8"),
    /ended incomplete by the model/,
  );
});

test("abandon with nothing open and mode plain outside pending are refused", () => {
  const app = new App();
  call(app, "begin_interview", BEGIN);
  fails(
    app,
    "end_interview",
    { abandon: true, reason: "r" },
    /nothing is open/,
  );
  fails(app, "end_interview", { mode: "plain" }, /only to a pending/);
});

test("a pending interview is stood down by mode plain only", () => {
  const app = new App();
  hook(app, "skill_started", {
    tool_schema: 4,
    session_id: "s1",
    skill: "grill-me",
  });
  fails(app, "end_interview", {}, /pending interview: use mode plain/);
  fails(app, "end_interview", { abandon: true, reason: "r" }, /pending/);
  const id = call(app, "status", {})["interview_id"];
  const r = call(app, "end_interview", { mode: "plain" });
  assert.equal(r["end_kind"], "stood_down");
  const end = archived(id)
    .map((l) => JSON.parse(l))
    .at(-1);
  assert.equal(end["mode"], "plain");
  assert.match(readFileSync(r["decision_log"], "utf8"), /\nmode: plain\n/);
  assert.equal(call(app, "status", {})["state"], "none");
});

test("an allowed finish attempt writes the log; status reports its path", () => {
  const app = new App();
  hook(app, "note_prompt", { session_id: "s2", prompt: "go" });
  const { interview_id: id } = call(app, "begin_interview", BEGIN);
  assert.equal(call(app, "status", {})["decision_log"], null);
  call(app, "log_item", { id: "q1", kind: "question", text: "t" });
  hook(app, "check_stop", { session_id: "s2", last_assistant_message: "done" });
  assert.equal(
    call(app, "status", {})["decision_log"],
    null,
    "blocked: no log",
  );
  call(app, "resolve", {
    id: "q1",
    answer: "a",
    source: "model",
    rationale: "r",
  });
  hook(app, "check_stop", { session_id: "s2", last_assistant_message: "done" });
  const done = call(app, "status", {});
  assert.equal(done["state"], "complete");
  const lines = readFileSync(done["decision_log"], "utf8").split("\n");
  assert.equal(lines[1], `journal_position: ${done["last_event"]}`);
  assert.equal(lines[2], "state: complete");
  const r = call(app, "end_interview", {});
  assert.equal(r["end_kind"], "ended_after_completion");
});

test("resume and discard refuse: nothing unfinished, or the wrong state", () => {
  const app = new App();
  fails(app, "resume", {}, /no unfinished journal/);
  fails(app, "discard", { id: "iv-1" }, /no unfinished journal/);
  call(app, "begin_interview", BEGIN);
  fails(app, "resume", {}, /wrong state: active/);
  fails(app, "discard", {}, /wrong state: active/);
});

test("the second Stop check stands a pending interview down", () => {
  const app = new App();
  hook(app, "skill_started", {
    tool_schema: 4,
    session_id: "s3",
    skill: "grill-me",
  });
  const id = call(app, "status", {})["interview_id"];
  hook(app, "check_stop", { session_id: "s3", last_assistant_message: "hi" });
  assert.equal(call(app, "status", {})["state"], "pending");
  hook(app, "check_stop", { session_id: "s3", last_assistant_message: "hi" });
  assert.equal(call(app, "status", {})["state"], "none");
  const end = archived(id)
    .map((l) => JSON.parse(l))
    .at(-1);
  assert.equal(end["end_kind"], "stood_down");
  assert.equal(end["mode"], "not_engaged");
  assert.equal(end["reason"], "skill did not engage");
});

test("an engaged interview is not stood down by later Stop checks", () => {
  const app = new App();
  hook(app, "skill_started", {
    tool_schema: 4,
    session_id: "s4",
    skill: "grill-me",
  });
  hook(app, "check_stop", { session_id: "s4", last_assistant_message: "hi" });
  call(app, "begin_interview", BEGIN);
  for (let i = 0; i < 3; i += 1) {
    hook(app, "check_stop", { session_id: "s4", last_assistant_message: "x" });
  }
  assert.notEqual(call(app, "status", {})["state"], "none");
});

test("a session change ends the interview and the triggering call acts as with none", () => {
  const app = new App();
  hook(app, "skill_started", {
    tool_schema: 4,
    session_id: "a",
    skill: "grill-me",
  });
  const pending = call(app, "status", {})["interview_id"];
  hook(app, "note_prompt", { session_id: "b", prompt: "p" });
  assert.equal(call(app, "status", {})["state"], "none");
  const end = archived(pending)
    .map((l) => JSON.parse(l))
    .at(-1);
  assert.equal(end["end_kind"], "stood_down");
  assert.equal(end["mode"], "not_engaged");

  const { interview_id: act } = call(app, "begin_interview", BEGIN);
  call(app, "log_item", { id: "q1", kind: "question", text: "t" });
  hook(app, "check_stop", { session_id: "b", last_assistant_message: "x" });
  assert.equal(call(app, "status", {})["state"], "active");
  hook(app, "check_stop", { session_id: "c", last_assistant_message: "x" });
  assert.equal(call(app, "status", {})["state"], "none");
  const abandoned = archived(act)
    .map((l) => JSON.parse(l))
    .at(-1);
  assert.equal(abandoned["end_kind"], "abandoned");
  assert.equal(abandoned["mode"], "register");
});

test("a session change with nothing open ends normally; a complete one is not downgraded", () => {
  const app = new App();
  hook(app, "note_prompt", { session_id: "a", prompt: "p" });
  const { interview_id: one } = call(app, "begin_interview", BEGIN);
  hook(app, "skill_started", {
    tool_schema: 4,
    session_id: "z",
    skill: "other",
  });
  const first = archived(one)
    .map((l) => JSON.parse(l))
    .at(-1);
  assert.equal(first["end_kind"], "normal");

  const { interview_id: two } = call(app, "begin_interview", BEGIN);
  hook(app, "check_stop", { session_id: "z", last_assistant_message: "done" });
  assert.equal(call(app, "status", {})["state"], "complete");
  hook(app, "note_prompt", { session_id: "y", prompt: "p" });
  const second = archived(two)
    .map((l) => JSON.parse(l))
    .at(-1);
  assert.equal(second["end_kind"], "ended_after_completion");
});
