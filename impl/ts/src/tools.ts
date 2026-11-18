// Tool handlers. The tool schemas themselves come from contract/tools.json.
//
// I1 scope: the register tools plus note_prompt. There is no journal yet, so
// `last_event` counts the state changes a journal would record.

import { randomBytes } from "node:crypto";

import pkg from "../package.json" with { type: "json" };
import {
  type Actor,
  type Answer,
  type Item,
  isNonblank,
  isValidId,
  type Kind,
  makeAnswer,
  parseActor,
  parseKind,
  Register,
  RegisterError,
} from "./register.ts";

export type JsonObject = Record<string, unknown>;

/** Tools this build implements, in the order they are listed. */
export const IMPLEMENTED: readonly string[] = [
  "begin_interview",
  "log_item",
  "resolve",
  "park",
  "reopen",
  "status",
  "note_prompt",
];

const TOOL_SCHEMA = 4;
const JOURNAL_SCHEMA = 2;
/** Harness-injected subagent hand-back prompts start with exactly this (REQ-41). */
const AGENT_MESSAGE_PREFIX = "<agent-message ";

/** The outcome of a tools/call: text plus the isError flag. */
export interface ToolResult {
  text: string;
  isError: boolean;
}

/** A domain failure; becomes an `isError: true` result, never a JSON-RPC error. */
class ToolError extends Error {}

const bad = (msg: string): ToolError =>
  new ToolError(`invalid arguments: ${msg}`);

const isObject = (v: unknown): v is JsonObject =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function checkKnown(args: JsonObject, allowed: readonly string[]): void {
  const k = Object.keys(args).find((key) => !allowed.includes(key));
  if (k !== undefined) throw bad(`unknown argument ${k}`);
}

function strArg(args: JsonObject, name: string): string | undefined {
  const v = args[name];
  if (v === undefined) return undefined;
  if (typeof v === "string") return v;
  throw bad(`${name} must be a string`);
}

function required<T>(name: string, v: T | undefined): T {
  if (v === undefined) throw bad(`missing required parameter ${name}`);
  return v;
}

function textArg(args: JsonObject, name: string): string {
  const s = required(name, strArg(args, name));
  if (!isNonblank(s)) throw bad(`${name} must not be blank`);
  return s;
}

function idValue(name: string, s: string): string {
  if (!isValidId(s)) throw bad(`${name} must match ^[A-Za-z0-9_.-]{1,64}$`);
  return s;
}

const idArg = (args: JsonObject, name: string): string =>
  idValue(name, required(name, strArg(args, name)));

function optIdArg(args: JsonObject, name: string): string | null {
  const s = strArg(args, name);
  return s === undefined ? null : idValue(name, s);
}

function actorArg(args: JsonObject, name: string): Actor | undefined {
  const s = strArg(args, name);
  if (s === undefined) return undefined;
  const a = parseActor(s);
  if (a === undefined) throw bad(`${name} must be one of user, model`);
  return a;
}

/**
 * `answer`, `source` and `rationale` go together (REQ-31). Absent entirely gives null
 * unless `mustExist`.
 */
function answerGroup(args: JsonObject, mustExist: boolean): Answer | null {
  const any = ["answer", "source", "rationale"].some((k) =>
    Object.hasOwn(args, k),
  );
  if (!any && !mustExist) return null;
  const answer = strArg(args, "answer");
  const source = actorArg(args, "source");
  const rationale = strArg(args, "rationale");
  if (answer === undefined || source === undefined || rationale === undefined) {
    const missing = (
      [
        ["answer", answer],
        ["source", source],
        ["rationale", rationale],
      ] as const
    )
      .filter(([, v]) => v === undefined)
      .map(([n]) => n);
    throw bad(
      `answer, source and rationale are required together; missing ${missing.join(", ")}`,
    );
  }
  const r = makeAnswer(answer, source, rationale);
  if (!r.ok) throw bad(`${r.param} ${r.why}`);
  return r.answer;
}

function itemJson(i: Item): JsonObject {
  const s = i.status;
  const answered =
    s.kind === "Resolved" || s.kind === "Stale" ? s.answer : null;
  const parked = s.kind === "Parked" ? s : null;
  return {
    id: i.id,
    kind: i.kind,
    text: i.text,
    parent: i.parent,
    depends_on: i.dependsOn,
    status: s.kind,
    answer: answered?.text ?? null,
    source: answered?.source ?? null,
    rationale: answered?.rationale ?? null,
    parked_by: parked?.by ?? null,
    park_reason: parked?.reason ?? null,
  };
}

/** `iv-YYYYMMDDTHHMMSSZ-<8 hex>`. */
function newInterviewId(): string {
  const stamp = new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d+/, "");
  return `iv-${stamp}-${randomBytes(4).toString("hex")}`;
}

export class App {
  private readonly register = new Register();
  private interviewId: string | null = null;
  private events = 0;
  private promptSeen = false;
  private hookNotePrompt = 0;
  private versionMismatch: { plugin: number; server: number } | null = null;

  /** Runs a tool. `null` means the tool name is unknown (a protocol error). */
  call(name: string, args: unknown, modelFacing: boolean): ToolResult | null {
    if (!IMPLEMENTED.includes(name)) return null;
    try {
      return {
        text: JSON.stringify(this.dispatch(name, args, modelFacing)),
        isError: false,
      };
    } catch (e) {
      if (e instanceof ToolError || e instanceof RegisterError) {
        return { text: e.message, isError: true };
      }
      throw e;
    }
  }

  private dispatch(
    name: string,
    rawArgs: unknown,
    modelFacing: boolean,
  ): JsonObject {
    if (name === "note_prompt" && modelFacing) {
      throw new ToolError(
        "note_prompt is hook-facing: a call carrying _meta claudecode/toolUseId is rejected",
      );
    }
    if (!isObject(rawArgs)) throw bad("arguments must be an object");
    const args = rawArgs;
    switch (name) {
      case "begin_interview":
        return this.beginInterview(args);
      case "log_item":
        return this.logItem(args);
      case "resolve":
        return this.resolve(args);
      case "park":
        return this.park(args);
      case "reopen":
        return this.reopen(args);
      case "status":
        return this.status(args);
      case "note_prompt":
        return this.notePrompt(args);
      default:
        throw new ToolError(`tool not implemented: ${name}`);
    }
  }

  /** Starts the interview if none is active; returns its id. */
  private ensureStarted(): string {
    if (this.interviewId !== null) return this.interviewId;
    this.interviewId = newInterviewId();
    this.events += 1;
    // The prompt before the start counts once; earlier inactive ones are not counted.
    this.hookNotePrompt = this.promptSeen ? 1 : 0;
    return this.interviewId;
  }

  private beginInterview(args: JsonObject): JsonObject {
    checkKnown(args, ["tool_schema", "skill", "mode"]);
    const ts = required("tool_schema", args["tool_schema"]);
    if (typeof ts !== "number" || !Number.isInteger(ts) || ts < 1) {
      throw bad("tool_schema must be an integer >= 1");
    }
    textArg(args, "skill");
    if (required("mode", strArg(args, "mode")) !== "register") {
      throw bad("mode must be register");
    }
    if (ts !== TOOL_SCHEMA) {
      this.versionMismatch = { plugin: ts, server: TOOL_SCHEMA };
      throw new ToolError(
        `tool_schema mismatch: plugin ${ts}, server ${TOOL_SCHEMA}`,
      );
    }
    this.versionMismatch = null;
    const existing = this.interviewId !== null;
    const id = this.ensureStarted();
    return { interview_id: id, state: "active", existing };
  }

  private logItem(args: JsonObject): JsonObject {
    checkKnown(args, [
      "id",
      "kind",
      "text",
      "parent",
      "depends_on",
      "answer",
      "source",
      "rationale",
    ]);
    const id = optIdArg(args, "id");
    const kind: Kind | undefined = parseKind(
      required("kind", strArg(args, "kind")),
    );
    if (kind === undefined)
      throw bad("kind must be one of question, subquestion, decision");
    const text = textArg(args, "text");
    const parent = optIdArg(args, "parent");
    const rawDeps = args["depends_on"];
    let dependsOn: string[] = [];
    if (rawDeps !== undefined) {
      if (!Array.isArray(rawDeps))
        throw bad("depends_on must be an array of ids");
      dependsOn = rawDeps.map((v: unknown) => {
        if (typeof v !== "string")
          throw bad("depends_on must be an array of ids");
        return idValue("depends_on", v);
      });
    }
    const answer = answerGroup(args, false);
    const logged = this.register.log({
      id,
      kind,
      text,
      parent,
      dependsOn,
      answer,
    });
    // A rule error above leaves the state untouched; success starts an interview.
    this.ensureStarted();
    this.events += 1;
    return { id: logged.id, status: logged.status };
  }

  private resolve(args: JsonObject): JsonObject {
    checkKnown(args, ["id", "answer", "source", "rationale"]);
    const id = idArg(args, "id");
    const answer = answerGroup(args, true);
    if (answer === null) throw bad("answer, source and rationale are required");
    this.register.resolve(id, answer);
    this.events += 1;
    return { id, status: "Resolved" };
  }

  private park(args: JsonObject): JsonObject {
    checkKnown(args, ["id", "reason", "parked_by"]);
    const id = idArg(args, "id");
    const reason = textArg(args, "reason");
    const by = required("parked_by", actorArg(args, "parked_by"));
    this.register.park(id, by, reason);
    this.events += 1;
    return { id, status: "Parked" };
  }

  private reopen(args: JsonObject): JsonObject {
    checkKnown(args, ["id", "reason"]);
    const id = idArg(args, "id");
    textArg(args, "reason"); // required now; kept once the journal exists
    const stale = this.register.reopen(id);
    this.events += 1;
    return { id, status: "Open", stale };
  }

  private status(args: JsonObject): JsonObject {
    checkKnown(args, []);
    const c = this.register.counts();
    return {
      server_version: pkg.version,
      tool_schema: TOOL_SCHEMA,
      journal_schema: JOURNAL_SCHEMA,
      state: this.interviewId !== null ? "active" : "none",
      interview_id: this.interviewId,
      items: {
        open: c.open,
        resolved: c.resolved,
        parked: c.parked,
        stale: c.stale,
      },
      tree: this.register.ordered().map(itemJson),
      hook_health: {
        note_prompt: this.hookNotePrompt,
        skill_started: 0,
        check_stop: 0,
        prompt_seen: this.promptSeen,
      },
      last_event: this.events,
      resumable: [],
      version_mismatch:
        this.versionMismatch === null
          ? null
          : {
              plugin_tool_schema: this.versionMismatch.plugin,
              server_tool_schema: this.versionMismatch.server,
            },
      decision_log: null,
      write_error: null,
    };
  }

  /** Hook-facing. In I1 this only keeps the prompt counters; nothing consumes them yet. */
  private notePrompt(args: JsonObject): JsonObject {
    checkKnown(args, ["session_id", "prompt"]);
    textArg(args, "session_id");
    const prompt = required("prompt", strArg(args, "prompt"));
    if (!prompt.startsWith(AGENT_MESSAGE_PREFIX)) {
      this.promptSeen = true;
      if (this.interviewId !== null) this.hookNotePrompt += 1;
    }
    return {};
  }
}
