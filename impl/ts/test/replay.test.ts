import assert from "node:assert/strict";
import { test } from "node:test";

import { replay } from "../src/replay.ts";

const ev = (seq: number, kind: string, extra: object = {}): string =>
  JSON.stringify({ v: 2, ts: "2026-11-16T09:30:15Z", seq, kind, ...extra });

const start = ev(1, "start", {
  interview_id: "iv-1",
  route: "hook",
  skill: "grill-me",
  session_id: "s",
  implicit: false,
  prompt_count: 1,
});
const item = (seq: number, id: string): string =>
  ev(seq, "item", {
    id,
    item_kind: "question",
    text: `t-${id}`,
    parent: null,
    depends_on: [],
  });

const ok = (text: string) => {
  const r = replay(text);
  assert.ok(r.ok, JSON.stringify(r));
  return r.state;
};
const failLine = (text: string): number => {
  const r = replay(text);
  assert.ok(!r.ok);
  return r.ok ? -1 : r.line;
};

test("rebuilds the register, counts and caps", () => {
  const s = ok(
    [
      start,
      ev(2, "hook", { tool: "check_stop", session_id: "s" }),
      ev(3, "hook", { tool: "note_prompt", session_id: "s" }),
      item(4, "q1"),
      ev(5, "resolve", {
        id: "q1",
        answer: "a",
        source: "user",
        rationale: "r",
      }),
      ev(6, "stop", {
        decision: "block",
        classification: "finish",
        presented: [],
        blocks: { prompt: 1, finish: 1, unlogged: 0 },
      }),
    ].join("\n") + "\n",
  );
  assert.equal(s.interviewId, "iv-1");
  assert.equal(s.lastSeq, 6);
  assert.equal(s.hookNotePrompt, 2);
  assert.equal(s.hookSkillStarted, 1);
  assert.equal(s.hookCheckStop, 1);
  assert.deepEqual(s.blocks, { prompt: 1, finish: 1, unlogged: 0 });
  assert.equal(s.register.counts().resolved, 1);
  assert.equal(s.ended, false);
});

test("an end event marks the journal ended", () => {
  const s = ok(
    [
      start,
      ev(2, "end", { end_kind: "normal", mode: "register", reason: "x" }),
    ].join("\n") + "\n",
  );
  assert.equal(s.ended, true);
});

test("a torn final line is ignored, a bad middle line fails", () => {
  const good = `${start}\n${item(2, "q1")}\n`;
  assert.equal(ok(`${good}{"v":2,"ts"`).lastSeq, 2);
  assert.equal(failLine(`${start}\n{"v":2,"ts"\n${item(3, "q1")}\n`), 2);
});

test("seq gaps, repeats, unknown kinds and v above 2 fail naming the line", () => {
  assert.equal(failLine(`${start}\n${item(3, "q1")}\n`), 2);
  assert.equal(failLine(`${start}\n${item(1, "q1")}\n`), 2);
  assert.equal(failLine(`${start}\n${ev(2, "bogus")}\n`), 2);
  assert.equal(
    failLine(`${start}\n${JSON.stringify({ v: 3, seq: 2, kind: "hook" })}\n`),
    2,
  );
});

test("REQ-31 checks apply to resolve and park lines", () => {
  const bad = (extra: object) =>
    `${start}\n${item(2, "q1")}\n${ev(3, "resolve", { id: "q1", source: "user", ...extra })}\n`;
  assert.equal(failLine(bad({ answer: " ", rationale: "r" })), 3);
  assert.equal(failLine(bad({ answer: "a", rationale: "two\nlines" })), 3);
  assert.equal(
    failLine(
      `${start}\n${item(2, "q1")}\n${ev(3, "park", { id: "q1", reason: " ", parked_by: "user" })}\n`,
    ),
    3,
  );
});

test("v1 lines take the line number as seq and only four kinds", () => {
  const v1 = JSON.stringify({
    kind: "item",
    id: "q1",
    item_kind: "question",
    text: "t",
    parent: null,
    depends_on: [],
  });
  const s = ok(
    `${v1}\n${ev(2, "item", { id: "q2", item_kind: "question", text: "t", parent: null, depends_on: [] })}\n`,
  );
  assert.equal(s.lastSeq, 2);
  assert.equal(failLine(`${JSON.stringify({ kind: "start" })}\n`), 1);
});

test("a v0.1.2 journal whose engage and resume lines lack skill replays ownerless", () => {
  const implicit = ev(1, "start", {
    interview_id: "iv-1",
    route: "implicit",
    skill: null,
    session_id: "s",
    implicit: true,
    prompt_count: 0,
  });
  const engage = ev(2, "engage", { via: "begin_interview" });
  const resume = ev(3, "resume", { session_id: "s2", from_seq: 2 });
  assert.equal(ok([implicit, engage, resume].join("\n") + "\n").skill, null);
});
