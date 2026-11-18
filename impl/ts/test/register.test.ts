import assert from "node:assert/strict";
import { test } from "node:test";

import {
  type Actor,
  type Answer,
  isValidId,
  makeAnswer,
  type NewItem,
  Register,
  RegisterError,
} from "../src/register.ts";

function ans(): Answer {
  const r = makeAnswer("a", "model", "because");
  assert.ok(r.ok);
  return r.answer;
}

const item = (id: string, deps: string[] = []): NewItem => ({
  id,
  kind: "question",
  text: `text ${id}`,
  parent: null,
  dependsOn: deps,
  answer: null,
});

function reg(specs: [string, string[]][]): Register {
  const r = new Register();
  for (const [id, deps] of specs) r.log(item(id, deps));
  return r;
}

const kindOf = (r: Register, id: string) => r.get(id)?.status.kind;

test("answer rejects blank and multiline", () => {
  const blank = makeAnswer(" \t", "user", "r");
  assert.ok(!blank.ok && blank.param === "answer");
  for (const bad of [
    "",
    "  ",
    "a\nb",
    "a\r\nb",
    "a\n",
    "a\u2028b",
    "a\u0085b",
  ]) {
    const r = makeAnswer("x", "user", bad);
    assert.ok(!r.ok && r.param === "rationale", JSON.stringify(bad));
  }
  assert.ok(makeAnswer("x", "user", "one line; fine.").ok);
});

test("id pattern", () => {
  assert.ok(isValidId("n-resolve.Open_1"));
  for (const bad of ["", "bad id", "q1\n", "a".repeat(65)])
    assert.ok(!isValidId(bad), bad);
});

test("log rules", () => {
  const r = reg([["q1", []]]);
  assert.throws(() => r.log(item("q1")), /duplicate id: q1/);
  assert.throws(
    () => r.log({ ...item("s"), kind: "subquestion" }),
    /subquestion needs a parent/,
  );
  assert.throws(() => r.log(item("d", ["nope"])), /missing item: nope/);
  assert.deepEqual(r.log({ ...item("x"), id: null }), {
    id: "Q1",
    status: "Open",
  });
  assert.equal(r.length, 2);
  assert.deepEqual(r.log({ ...item("y"), answer: ans() }), {
    id: "y",
    status: "Resolved",
  });
});

test("transition table is exhaustive", () => {
  // [status, resolve allowed, park allowed, reopen allowed]
  const cases: [string, boolean, boolean, boolean][] = [
    ["Open", true, true, false],
    ["Stale", true, true, false],
    ["ParkedModel", true, true, false],
    ["ParkedUser", true, false, false],
    ["Resolved", false, false, true],
  ];
  for (const [name, canResolve, canPark, canReopen] of cases) {
    const build = (): Register => {
      const r = reg([
        ["b", []],
        ["x", ["b"]],
      ]);
      if (name === "Stale") {
        r.resolve("b", ans());
        r.resolve("x", ans());
        r.reopen("b");
      } else if (name === "ParkedModel") r.park("x", "model", "r");
      else if (name === "ParkedUser") r.park("x", "user", "r");
      else if (name === "Resolved") r.resolve("x", ans());
      return r;
    };
    const ok = (f: () => unknown): boolean => {
      try {
        f();
        return true;
      } catch (e) {
        assert.ok(e instanceof RegisterError);
        return false;
      }
    };
    assert.equal(
      ok(() => build().resolve("x", ans())),
      canResolve,
      `resolve ${name}`,
    );
    assert.equal(
      ok(() => build().park("x", "model", "r")),
      canPark,
      `park ${name}`,
    );
    assert.equal(
      ok(() => build().reopen("x")),
      canReopen,
      `reopen ${name}`,
    );
  }
});

test("rejection changes nothing and names id and status", () => {
  const r = reg([["q", []]]);
  r.resolve("q", ans());
  assert.throws(() => r.park("q", "model", "r"), {
    message: "cannot park q: item is Resolved",
  });
  assert.equal(kindOf(r, "q"), "Resolved");
  assert.throws(() => r.resolve("zz", ans()), /no such item: zz/);
});

test("reopen marks transitive resolved dependants stale", () => {
  const r = reg([
    ["a", []],
    ["b", ["a"]],
    ["c", ["b"]],
    ["d", ["a"]],
    ["e", ["a"]],
  ]);
  for (const id of ["a", "b", "c", "d"]) r.resolve(id, ans());
  assert.deepEqual(r.reopen("a"), ["b", "c", "d"]);
  assert.equal(kindOf(r, "a"), "Open");
  assert.equal(kindOf(r, "c"), "Stale");
  assert.equal(kindOf(r, "e"), "Open");
  const b = r.get("b")?.status;
  assert.ok(b?.kind === "Stale" && b.answer.text === "a");
  r.resolve("b", ans()); // a Stale item can be resolved again
});

test("transitive Stale passes through a Parked intermediate; parents never propagate", () => {
  const r = reg([
    ["a", []],
    ["b", ["a"]],
    ["c", ["b"]],
    ["d", ["c"]],
  ]);
  r.log({ ...item("kid"), kind: "subquestion", parent: "a", answer: ans() });
  r.resolve("a", ans());
  r.park("b", "model", "later");
  r.resolve("c", ans());
  r.resolve("d", ans());
  assert.deepEqual(r.reopen("a"), ["c", "d"]);
  assert.equal(kindOf(r, "b"), "Parked");
  assert.equal(kindOf(r, "kid"), "Resolved");
  r.resolve("c", ans());
  r.resolve("a", ans());
  assert.deepEqual(r.reopen("a"), ["c"]); // d is already Stale, not listed again
  assert.equal(kindOf(r, "d"), "Stale");
});

test("ordering and counts", () => {
  const r = reg([
    ["a", []],
    ["b", ["a"]],
    ["c", []],
    ["d", []],
  ]);
  r.resolve("a", ans());
  r.resolve("b", ans());
  r.reopen("a");
  r.park("c", "user" satisfies Actor, "x");
  r.resolve("d", ans());
  assert.deepEqual(
    r.ordered().map((i) => i.id),
    ["a", "b", "c", "d"],
  );
  assert.deepEqual(r.counts(), { open: 1, resolved: 1, parked: 1, stale: 1 });
});
