import assert from "node:assert/strict";
import { test } from "node:test";

import contract from "../../../contract/tools.json" with { type: "json" };
import { PROTOCOL_VERSION, Server } from "../src/protocol.ts";

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
