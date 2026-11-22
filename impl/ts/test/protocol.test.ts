import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import ceilings from "../../../conformance/tools_list_ceilings.json" with {
  type: "json",
};
import contract from "../../../contract/tools.json" with { type: "json" };
import { inlineRefs, PROTOCOL_VERSION, Server } from "../src/protocol.ts";

// Journals and pid files go to a fresh directory, never the shared default data root.
process.env["CLAUDE_PLUGIN_DATA"] = mkdtempSync(join(tmpdir(), "reg-data-"));
process.env["CLAUDE_PROJECT_DIR"] = mkdtempSync(join(tmpdir(), "reg-proj-"));

function rpc(s: Server, line: string): Record<string, any> {
  const r = s.handleLine(line);
  assert.notEqual(r, null);
  return JSON.parse(r as string);
}

test("initialize reports the protocol version", () => {
  const r = rpc(
    new Server(),
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
  );
  assert.equal(r["result"].protocolVersion, PROTOCOL_VERSION);
  assert.equal(r["id"], 1);
});

test("a notification gets no reply", () => {
  const s = new Server();
  assert.equal(
    s.handleLine('{"jsonrpc":"2.0","method":"notifications/initialized"}'),
    null,
  );
});

test("malformed input is a protocol error", () => {
  const s = new Server();
  assert.equal(rpc(s, "{nope")["error"].code, -32700);
  assert.equal(rpc(s, '{"id":2,"method":"x"}')["error"].code, -32601);
  assert.equal(
    rpc(s, '{"id":3,"method":"tools/call","params":{"name":"zzz"}}')["error"]
      .code,
    -32602,
  );
  assert.equal(rpc(s, "[1]")["error"].code, -32600);
});

test("tools/list serves an object-typed inputSchema for every implemented tool", () => {
  const r = rpc(new Server(), '{"jsonrpc":"2.0","id":1,"method":"tools/list"}');
  const tools = r["result"].tools as { inputSchema: { type?: unknown } }[];
  assert.ok(tools.length > 0);
  for (const t of tools) assert.equal(t.inputSchema.type, "object");
});

// Claude Code rejects a tool whose inputSchema has no top-level type object, and one
// rejection hides every tool of the server; this covers the tools this build does not
// serve yet too (end_interview is a oneOf).
test("every contract tool input schema has a top-level object type", () => {
  const tools = Object.entries(
    contract.tools as unknown as Record<string, { input: { type?: unknown } }>,
  );
  assert.equal(tools.length, 12);
  for (const [name, tool] of tools) {
    assert.equal(tool.input.type, "object", `${name} input type`);
  }
});

// Issue #114: the served list inlines the shared $defs instead of repeating them per tool.
type ContractDoc = {
  $defs: Record<string, unknown>;
  tools: Record<string, { audience: string; input: unknown }>;
};

// Independent of inlineRefs: contract refs are all `#/$defs/<name>` leaves today.
function resolveExpected(
  node: unknown,
  defs: Record<string, unknown>,
): unknown {
  if (Array.isArray(node)) return node.map((v) => resolveExpected(v, defs));
  if (typeof node !== "object" || node === null) return node;
  const o = node as Record<string, unknown>;
  if (typeof o["$ref"] === "string") {
    return defs[o["$ref"].replace("#/$defs/", "")];
  }
  return Object.fromEntries(
    Object.entries(o).map(([k, v]) => [k, resolveExpected(v, defs)]),
  );
}

const hasBannedKey = (v: unknown): boolean => {
  if (Array.isArray(v)) return v.some(hasBannedKey);
  if (typeof v !== "object" || v === null) return false;
  return Object.entries(v).some(
    ([k, c]) =>
      k === "$defs" || k === "definitions" || k === "$ref" || hasBannedKey(c),
  );
};

test("tools/list serves the contract tools with refs inlined, no $defs, within the size ceilings", () => {
  const doc = contract as unknown as ContractDoc;
  const r = rpc(new Server(), '{"jsonrpc":"2.0","id":1,"method":"tools/list"}');
  const tools = r["result"].tools as {
    name: string;
    inputSchema: { type?: unknown };
  }[];
  const want = Object.keys(doc.tools).sort();
  assert.equal(want.length, 12);
  assert.deepEqual(tools.map((t) => t.name).sort(), want);
  for (const t of tools) {
    assert.ok(!hasBannedKey(t.inputSchema), `${t.name} has $defs or $ref`);
    assert.equal(t.inputSchema.type, "object", `${t.name} top-level type`);
    assert.deepEqual(
      t.inputSchema,
      resolveExpected(doc.tools[t.name]?.input, doc.$defs),
      `${t.name} inputSchema differs from the resolved contract input`,
    );
  }
  const model = tools.filter((t) => doc.tools[t.name]?.audience === "model");
  assert.equal(model.length, 9);
  const allChars = JSON.stringify(tools).length;
  const modelChars = JSON.stringify(model).length;
  assert.ok(
    allChars <= ceilings.all_tools_chars,
    `all tools ${allChars} chars`,
  );
  assert.ok(
    modelChars <= ceilings.model_tools_chars,
    `model tools ${modelChars} chars`,
  );
});

test("inlineRefs rejects bad refs", () => {
  const defs = {
    a: { type: "string" },
    loop: { $ref: "#/$defs/loop" },
    n: { x: { y: 1 } },
  };
  assert.deepEqual(inlineRefs({ p: { $ref: "#/$defs/a" } }, defs), {
    p: { type: "string" },
  });
  for (const bad of [
    { $ref: "#/$defs/missing" },
    { $ref: "#/definitions/a" },
    { $ref: "#/$defs/a", description: "x" },
    { $ref: "#/$defs/n/x" },
    { $ref: "#/$defs/loop" },
  ]) {
    assert.throws(() => inlineRefs(bad, defs), Error, JSON.stringify(bad));
  }
});
