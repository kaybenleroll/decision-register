// Newline-delimited JSON-RPC 2.0 for MCP over stdio (protocol 2024-11-05).

import contract from "../../../contract/tools.json" with { type: "json" };
import pkg from "../package.json" with { type: "json" };
import { App, type JsonObject } from "./tools.ts";

export const PROTOCOL_VERSION = "2024-11-05";

type Id = unknown;

const isObject = (v: unknown): v is JsonObject =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const replyOk = (id: Id, result: unknown): string =>
  JSON.stringify({ jsonrpc: "2.0", id, result });

const replyErr = (id: Id, code: number, message: string): string =>
  JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } });

const MAX_REF_HOPS = 8;

/**
 * Copies `node` with every `{"$ref": "#/$defs/<name>"}` replaced by the body of that def, so the
 * served schema needs no `$defs`. `hops` counts `$ref` resolutions only. An unknown def, a ref
 * that is not `#/$defs/<name>` with one path segment, a `$ref` object with sibling keys, or more
 * than 8 resolutions throws.
 */
export function inlineRefs(node: unknown, defs: unknown, hops = 0): unknown {
  if (Array.isArray(node)) return node.map((v) => inlineRefs(v, defs, hops));
  if (!isObject(node)) return node;
  if ("$ref" in node) {
    if (Object.keys(node).length !== 1) {
      throw new Error("$ref object has sibling keys");
    }
    const ref = node["$ref"];
    const prefix = "#/$defs/";
    if (typeof ref !== "string" || !ref.startsWith(prefix)) {
      throw new Error(`unsupported $ref ${String(ref)}`);
    }
    const name = ref.slice(prefix.length);
    if (name === "" || name.includes("/")) {
      throw new Error(`unsupported $ref ${ref}`);
    }
    if (hops >= MAX_REF_HOPS) {
      throw new Error(`more than ${MAX_REF_HOPS} $ref resolutions at ${ref}`);
    }
    const table = defs as Record<string, unknown>;
    if (!Object.hasOwn(table, name)) throw new Error(`unknown $ref ${ref}`);
    return inlineRefs(table[name], defs, hops + 1);
  }
  const out: JsonObject = {};
  for (const [k, v] of Object.entries(node)) out[k] = inlineRefs(v, defs, hops);
  return out;
}

/** `tools/list` body: the implemented tools with their contract input schemas, refs inlined and no `$defs` (issue #114). */
function toolList(): unknown {
  const doc = contract as unknown as {
    $defs: unknown;
    tools: Record<string, { description: unknown; input: JsonObject }>;
  };
  const tools = Object.keys(doc.tools).map((name) => {
    const tool = doc.tools[name];
    if (tool === undefined) throw new Error(`contract lacks tool ${name}`);
    return {
      name,
      description: tool.description,
      inputSchema: inlineRefs(tool.input, doc.$defs),
    };
  });
  return { tools };
}

export class Server {
  private readonly app = new App();

  /** Handles one input line; `null` when no reply is due (notifications). */
  handleLine(line: string): string | null {
    let msg: unknown;
    try {
      msg = JSON.parse(line);
    } catch {
      return replyErr(null, -32700, "parse error");
    }
    if (!isObject(msg)) return replyErr(null, -32600, "invalid request");
    const id = msg["id"];
    const method = msg["method"];
    if (typeof method !== "string") {
      const isResponse = "result" in msg || "error" in msg;
      return isResponse
        ? null
        : replyErr(id ?? null, -32600, "invalid request");
    }
    if (id === undefined) return null; // a notification, never answered
    const params = msg["params"] ?? null;
    switch (method) {
      case "initialize":
        return replyOk(id, {
          protocolVersion: PROTOCOL_VERSION,
          capabilities: { tools: {} },
          serverInfo: { name: "register-ts", version: pkg.version },
        });
      case "ping":
        return replyOk(id, {});
      case "tools/list":
        return replyOk(id, toolList());
      case "tools/call":
        return this.toolsCall(id, params);
      default:
        return replyErr(id, -32601, `method not found: ${method}`);
    }
  }

  /** Called once at stdin EOF, before the process exits. */
  onEof(): void {
    this.app.onEof();
  }

  private toolsCall(id: Id, params: unknown): string {
    const p = isObject(params) ? params : {};
    const name = p["name"];
    if (typeof name !== "string") {
      return replyErr(id, -32602, "malformed params: name is required");
    }
    const rawArgs = p["arguments"];
    if (rawArgs !== undefined && rawArgs !== null && !isObject(rawArgs)) {
      return replyErr(
        id,
        -32602,
        "malformed params: arguments must be an object",
      );
    }
    const meta = p["_meta"];
    const modelFacing = isObject(meta) && "claudecode/toolUseId" in meta;
    const r = this.app.call(name, rawArgs ?? {}, modelFacing);
    if (r === null) return replyErr(id, -32602, `unknown tool: ${name}`);
    return replyOk(id, {
      content: [{ type: "text", text: r.text }],
      isError: r.isError,
    });
  }
}
