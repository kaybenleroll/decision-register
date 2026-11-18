import assert from "node:assert/strict";
import { test } from "node:test";

import { PROTOCOL_VERSION, Server } from "../src/protocol.ts";

function rpc(s: Server, line: string): Record<string, any> {
  const r = s.handleLine(line);
  assert.notEqual(r, null);
  return JSON.parse(r as string);
}

test("initialize reports the protocol version", () => {
  const r = rpc(new Server(), '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}');
  assert.equal(r["result"].protocolVersion, PROTOCOL_VERSION);
  assert.equal(r["id"], 1);
});

test("a notification gets no reply", () => {
  const s = new Server();
  assert.equal(s.handleLine('{"jsonrpc":"2.0","method":"notifications/initialized"}'), null);
});

test("malformed input is a protocol error", () => {
  const s = new Server();
  assert.equal(rpc(s, "{nope")["error"].code, -32700);
  assert.equal(rpc(s, '{"id":2,"method":"x"}')["error"].code, -32601);
  assert.equal(rpc(s, '{"id":3,"method":"tools/call","params":{"name":"zzz"}}')["error"].code, -32602);
  assert.equal(rpc(s, "[1]")["error"].code, -32600);
});
