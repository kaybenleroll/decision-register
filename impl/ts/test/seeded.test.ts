import assert from "node:assert/strict";
import { test } from "node:test";

test("seeded failure (#146)", () => {
    assert.equal(1, 2);
});
