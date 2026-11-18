import assert from "node:assert/strict";
import { test } from "node:test";

import { classify, type ItemView } from "../src/classifier.ts";

const items: ItemView[] = [
  { id: "Q1", text: "storage engine choice", status: "Open" },
  { id: "Q2", text: "retention period policy", status: "Resolved" },
];

test("empty and whitespace messages are empty", () => {
  assert.deepEqual(classify("  \n", items), { kind: "empty" });
});

test("no question mark is a finish attempt", () => {
  assert.deepEqual(classify("all done", items), { kind: "finish" });
});

test("a question mark inside code or a quote does not ask", () => {
  assert.deepEqual(classify("run `a?b` now\n> why?", items), {
    kind: "finish",
  });
});

test("a label cites an open item; resolved items are not concerned", () => {
  assert.deepEqual(classify("Q1: which one?", items), {
    kind: "question",
    presented: ["Q1"],
  });
  assert.deepEqual(classify("Q2: which one?", items), {
    kind: "question",
    presented: [],
  });
  assert.deepEqual(classify("Q01: which one?", items), {
    kind: "question",
    presented: [],
  });
});

test("a single labelled request without a question mark is a question turn", () => {
  assert.deepEqual(classify("**Q1**. pick one", items), {
    kind: "question",
    presented: ["Q1"],
  });
});

test("three shared content words concern an item", () => {
  assert.deepEqual(classify("Is the storage engine choice settled?", items), {
    kind: "question",
    presented: ["Q1"],
  });
});
