import assert from "node:assert/strict";
import { test } from "node:test";

import { App } from "../src/tools.ts";

type R = Record<string, any>;

function call(
  app: App,
  name: string,
  args: unknown,
  modelFacing = name !== "note_prompt",
): R {
  const r = app.call(name, args, modelFacing);
  assert.ok(r !== null, `unknown tool ${name}`);
  if (r.isError) throw new Error(r.text);
  return JSON.parse(r.text);
}

const fails = (
  app: App,
  name: string,
  args: unknown,
  re: RegExp,
  modelFacing?: boolean,
): void => assert.throws(() => call(app, name, args, modelFacing), re);

const BEGIN = { tool_schema: 4, skill: "grill-me", mode: "register" };

function started(): App {
  const app = new App();
  call(app, "begin_interview", BEGIN);
  return app;
}

const q = (app: App, id: string, extra: R = {}): R =>
  call(app, "log_item", { id, kind: "question", text: `t ${id}`, ...extra });

const ANS = { answer: "a", source: "model", rationale: "because" };

test("begin is idempotent and checks the schema version", () => {
  const app = new App();
  const a = call(app, "begin_interview", BEGIN);
  const b = call(app, "begin_interview", BEGIN);
  assert.equal(a["existing"], false);
  assert.equal(b["existing"], true);
  assert.equal(a["interview_id"], b["interview_id"]);
  assert.match(a["interview_id"], /^iv-\d{8}T\d{6}Z-[0-9a-f]{8}$/);
  fails(app, "begin_interview", { ...BEGIN, tool_schema: 3 }, /3.*4/);
  assert.equal(
    call(app, "status", {})["version_mismatch"].plugin_tool_schema,
    3,
  );
  call(app, "begin_interview", BEGIN);
  assert.equal(call(app, "status", {})["version_mismatch"], null);
});

test("log_item starts implicitly; a rule error does not", () => {
  const app = new App();
  fails(
    app,
    "log_item",
    { kind: "subquestion", text: "t" },
    /subquestion needs a parent/,
  );
  assert.equal(call(app, "status", {})["state"], "none");
  q(app, "q1");
  const s = call(app, "status", {});
  assert.equal(s["state"], "active");
  assert.equal(s["last_event"], 2); // start, item
});

test("argument validation names the parameter", () => {
  const app = started();
  fails(
    app,
    "log_item",
    { kind: "question", text: "t", answer: "a" },
    /missing source, rationale/,
  );
  fails(
    app,
    "log_item",
    { kind: "question", text: "t", extra: 1 },
    /unknown argument extra/,
  );
  fails(app, "log_item", { kind: "nope", text: "t" }, /kind must be one of/);
  fails(
    app,
    "log_item",
    { id: "bad id", kind: "question", text: "t" },
    /id must match/,
  );
  fails(
    app,
    "log_item",
    { kind: "question", text: " " },
    /text must not be blank/,
  );
  fails(
    app,
    "log_item",
    { kind: "question", text: "t", depends_on: [1] },
    /depends_on/,
  );
  fails(
    app,
    "log_item",
    { kind: "question", text: "t", ...ANS, rationale: "a\nb" },
    /rationale/,
  );
  fails(app, "resolve", { id: "q1" }, /missing answer, source, rationale/);
  fails(
    app,
    "park",
    { id: "q1", reason: "r" },
    /missing required parameter parked_by/,
  );
  fails(
    app,
    "park",
    { id: "q1", reason: "r", parked_by: "robot" },
    /parked_by must be one of/,
  );
  fails(app, "reopen", { id: "q1" }, /missing required parameter reason/);
  fails(app, "status", { x: 1 }, /unknown argument x/);
  fails(app, "status", "nope", /arguments must be an object/);
  assert.equal(call(app, "status", {})["items"].open, 0);
});

test("resolve, park, reopen and Stale through the tools", () => {
  const app = started();
  q(app, "a");
  q(app, "b", { depends_on: ["a"] });
  call(app, "resolve", { id: "a", ...ANS });
  assert.deepEqual(
    call(app, "log_item", {
      id: "c",
      kind: "decision",
      text: "c",
      depends_on: ["b"],
      ...ANS,
    }),
    {
      id: "c",
      status: "Resolved",
    },
  );
  call(app, "resolve", { id: "b", ...ANS, source: "user" });
  assert.deepEqual(call(app, "reopen", { id: "a", reason: "why" }), {
    id: "a",
    status: "Open",
    stale: ["b", "c"],
  });
});

test("rejections name the id and status", () => {
  const app = started();
  q(app, "a");
  call(app, "resolve", { id: "a", ...ANS });
  fails(
    app,
    "resolve",
    { id: "a", ...ANS },
    /cannot resolve a: item is Resolved/,
  );
  fails(
    app,
    "park",
    { id: "a", reason: "r", parked_by: "model" },
    /cannot park a: item is Resolved/,
  );
  fails(app, "reopen", { id: "nope", reason: "r" }, /no such item: nope/);
  q(app, "b");
  call(app, "park", { id: "b", reason: "r", parked_by: "user" });
  fails(
    app,
    "park",
    { id: "b", reason: "r", parked_by: "model" },
    /cannot park b: item is Parked/,
  );
  call(app, "resolve", { id: "b", ...ANS, source: "user" });
});

test("status carries every contract field and a consistent tree", () => {
  const app = started();
  q(app, "a");
  q(app, "b");
  call(app, "park", { id: "b", reason: "later", parked_by: "user" });
  call(app, "resolve", { id: "a", ...ANS });
  const s = call(app, "status", {});
  for (const k of [
    "server_version",
    "tool_schema",
    "journal_schema",
    "state",
    "interview_id",
    "items",
    "tree",
    "hook_health",
    "last_event",
    "resumable",
    "version_mismatch",
    "decision_log",
    "write_error",
  ])
    assert.ok(k in s, k);
  assert.deepEqual(
    s["tree"].map((i: R) => i["status"]),
    ["Parked", "Resolved"],
  );
  assert.equal(s["tree"][0].parked_by, "user");
  assert.equal(s["tree"][0].answer, null);
  assert.equal(s["tree"][1].answer, "a");
  assert.deepEqual(s["items"], { open: 0, resolved: 1, parked: 1, stale: 0 });
});

test("note_prompt: hook-facing only, no-op while idle, agent messages ignored", () => {
  const app = new App();
  const np = (prompt: string): R =>
    call(app, "note_prompt", { session_id: "s", prompt }, false);
  assert.deepEqual(np("hello"), {});
  assert.equal(call(app, "status", {})["hook_health"].note_prompt, 0);
  assert.equal(call(app, "status", {})["hook_health"].prompt_seen, true);
  fails(
    app,
    "note_prompt",
    { session_id: "s", prompt: "x" },
    /hook-facing/,
    true,
  );
  call(app, "begin_interview", BEGIN);
  assert.equal(call(app, "status", {})["hook_health"].note_prompt, 1);
  np('<agent-message from="a">\n[Subagent hand-back]');
  np(" <agent-message ");
  np("text <agent-message ");
  assert.equal(call(app, "status", {})["hook_health"].note_prompt, 3);
});

test("unknown tool is not handled here", () => {
  assert.equal(new App().call("check_stop", {}, false), null);
});
