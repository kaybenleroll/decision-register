// Tool handlers. The tool schemas themselves come from contract/tools.json.
//
// I1 and I2: the register tools and the three hook-facing tools, with a minimal journal.

import { randomBytes } from "node:crypto";
import { readFileSync, truncateSync } from "node:fs";
import { join } from "node:path";

import pkg from "../package.json" with { type: "json" };
import { type Classification, classify } from "./classifier.ts";
import {
  DecisionLog,
  type LogMode,
  type LogState,
  type ReopenRecord,
} from "./declog.ts";
import { Journal, projectDir } from "./journal.ts";
import { ClaimError, claim, pidPath, readPid, removeFile } from "./pidfile.ts";
import {
  type Actor,
  type Answer,
  type Item,
  isNonblank,
  isValidId,
  type Kind,
  makeAnswer,
  type NewItem,
  parseActor,
  parseKind,
  Register,
  RegisterError,
} from "./register.ts";
import { replay } from "./replay.ts";
import { archiveJournal, scanUnfinished } from "./store.ts";

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
  "skill_started",
  "check_stop",
  "end_interview",
  "resume",
  "discard",
];

/** Tools in the contract that a later slice builds (none: resume and discard only refuse until I4). */
const NOT_YET: readonly string[] = [];

/** Hook-facing tools reject any call carrying `_meta` `claudecode/toolUseId` (REQ-40). */
export const HOOK_FACING: readonly string[] = [
  "note_prompt",
  "skill_started",
  "check_stop",
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

function toolSchemaArg(args: JsonObject): number {
  const ts = required("tool_schema", args["tool_schema"]);
  if (typeof ts !== "number" || !Number.isInteger(ts) || ts < 1) {
    throw bad("tool_schema must be an integer >= 1");
  }
  return ts;
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

type State = "none" | "pending" | "active" | "complete";
type Route = "hook" | "begin_interview" | "implicit";

/** Skill names that start a pending interview by the hook route (REQ-24, exact match). */
const LISTED_SKILLS: readonly string[] = ["grill-me"];
/** A parent with at least one child but fewer than this is under-sized (REQ-35). */
const MIN_CHILDREN = 2;
const PROMPT_CAP = 2;
const FINISH_CAP = 8;
const UNLOGGED_CAP = 12;
const VOLUNTEERED_CAP = 4;
const NOT_FIRING =
  "hooks are not firing: no user prompt has reached the server (REQ-44)";

const truncate = (s: string): string => Array.from(s).slice(0, 120).join("");

export class App {
  private register = new Register();
  private reopens: ReopenRecord[] = [];
  private state: State = "none";
  private interviewId: string | null = null;
  private journal: Journal | null = null;
  private declog: DecisionLog | null = null;
  /** The session id the interview is bound to (REQ-26); null when none was known. */
  private boundSession: string | null = null;
  private implicitStart = false;
  /** A prompt arrived while no interview existed (cleared at a start and by a none Stop). */
  private promptSeen = false;
  private hookNotePrompt = 0;
  private hookSkillStarted = 0;
  private hookCheckStop = 0;
  /** Recorded from every hook call that carries a session id (REQ-27 rule 1). */
  private lastSeenSessionId: string | null = null;
  private versionMismatch: { plugin: number; server: number } | null = null;
  // User-answer rule (REQ-32 to 34, 44). Survives an interview start.
  private anyPrompt = false;
  private allowance = 0;
  private used = 0;
  private volunteered = 0;
  private promptSinceStop = false;
  private presented = new Set<string>();
  private presentedUnknown = false;
  // Block caps (REQ-42), per interview.
  private blocks = { prompt: 0, finish: 0, unlogged: 0 };

  /** The session id of the latest hook call that carried one (REQ-27 rule 1). */
  get lastSeen(): string | null {
    return this.lastSeenSessionId;
  }

  /** Runs a tool. `null` means the tool name is unknown (a protocol error). */
  call(name: string, args: unknown, modelFacing: boolean): ToolResult | null {
    // The guard runs before anything else and also covers hook-facing tools that
    // this build does not serve yet, so a forged call is rejected, never unknown.
    if (HOOK_FACING.includes(name) && modelFacing) {
      return {
        text: `${name} is hook-facing: a call carrying _meta claudecode/toolUseId is rejected`,
        isError: true,
      };
    }
    if (NOT_YET.includes(name)) {
      return {
        text: `${name} is not implemented in this build`,
        isError: true,
      };
    }
    if (!IMPLEMENTED.includes(name)) return null;
    try {
      return {
        text: JSON.stringify(this.dispatch(name, args)),
        isError: false,
      };
    } catch (e) {
      if (e instanceof ToolError || e instanceof RegisterError) {
        return { text: e.message, isError: true };
      }
      throw e;
    }
  }

  private dispatch(name: string, rawArgs: unknown): JsonObject {
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
      case "skill_started":
        return this.skillStarted(args);
      case "check_stop":
        return this.checkStopAndFlush(args);
      case "end_interview":
        return this.endInterview(args);
      case "resume":
        return this.resume(args);
      case "discard":
        return this.discard(args);
      default:
        throw new ToolError(`tool not implemented: ${name}`);
    }
  }

  /** Unfinished journals with no live owner, most recently modified first. */
  private resumable(): { interview_id: string; last_event: number }[] {
    return scanUnfinished(projectDir(), this.interviewId)
      .filter((j) => !j.alive)
      .map((j) => ({ interview_id: j.id, last_event: j.lastEvent }));
  }

  /** begin_interview and the item tools are refused while a journal waits (REQ-52). */
  private refuseWhileUnfinished(): void {
    if (
      (this.state === "none" || this.state === "pending") &&
      this.resumable().length > 0
    ) {
      throw new ToolError("unfinished journal: call resume or discard");
    }
  }

  private emit(kind: string, fields: Record<string, unknown>): void {
    this.journal?.append(kind, fields);
  }

  /** Starts an interview: pending for the hook route, active otherwise. */
  private startInterview(
    route: Route,
    skill: string | null,
    sessionId: string | null,
  ): string {
    let id = newInterviewId();
    let journal = new Journal(id);
    // The pid file is created exclusively before the first event; a clash draws a new id.
    while (!journal.createPidFile()) {
      id = newInterviewId();
      journal = new Journal(id);
    }
    this.interviewId = id;
    this.journal = journal;
    this.declog = new DecisionLog(id);
    this.implicitStart = route === "implicit";
    this.state = route === "hook" ? "pending" : "active";
    // The prompt before the start counts once; earlier inactive ones are not counted.
    const promptCount = this.promptSeen ? 1 : 0;
    this.hookNotePrompt = promptCount;
    this.hookSkillStarted = route === "hook" ? 1 : 0;
    this.hookCheckStop = 0;
    this.promptSeen = false;
    this.blocks = { prompt: 0, finish: 0, unlogged: 0 };
    const envSession = process.env["CLAUDE_CODE_SESSION_ID"];
    const session =
      sessionId ??
      this.lastSeenSessionId ??
      (envSession === undefined || envSession === "" ? null : envSession);
    this.boundSession = session;
    this.emit("start", {
      interview_id: id,
      route,
      skill,
      session_id: session,
      implicit: route === "implicit",
      prompt_count: promptCount,
    });
    // The file is created with its first event; later events are write-behind.
    journal.flush();
    return id;
  }

  /** Open, Stale and model-parked items: what `end_interview` calls open (REQ-23). */
  private openItems(): Item[] {
    return this.register.ordered().filter((i) => {
      const s = i.status;
      return (
        s.kind === "Open" ||
        s.kind === "Stale" ||
        (s.kind === "Parked" && s.by === "model")
      );
    });
  }

  /** Writes (replaces) the decision log from the current state (REQ-23, REQ-62). */
  private writeLog(
    state: LogState,
    mode: LogMode,
    endKind: string,
    note: string | null,
  ): void {
    if (this.declog === null || this.interviewId === null) return;
    this.journal?.flush(); // so the logged position is durable
    this.declog.write({
      interviewId: this.interviewId,
      journalPosition: this.journal?.seq ?? 0,
      state,
      mode,
      endKind,
      implicitStart: this.implicitStart,
      note,
      items: this.register.list(),
      reopens: this.reopens,
      hookHealth: {
        note_prompt: this.hookNotePrompt,
        skill_started: this.hookSkillStarted,
        check_stop: this.hookCheckStop,
      },
    });
  }

  /**
   * Ends the interview: the `end` event, the journal archived, the decision log written,
   * then the live state back to `none` (runtime.md "Interview ends and server exit").
   * Returns the log path (null if it could not be written).
   */
  private finish(
    endKind: string,
    journalMode: "register" | "plain" | "not_engaged",
    reason: string,
    note: string | null,
  ): string | null {
    const logMode: LogMode =
      journalMode === "not_engaged" ? "not engaged" : journalMode;
    this.emit("end", { end_kind: endKind, mode: journalMode, reason });
    this.journal?.archive();
    this.journal?.removePidFile();
    this.writeLog("ended", logMode, endKind, note);
    const path = this.declog?.path ?? null;
    this.register = new Register();
    this.reopens = [];
    this.state = "none";
    this.interviewId = null;
    this.journal = null;
    this.declog = null;
    this.boundSession = null;
    this.implicitStart = false;
    this.hookNotePrompt = 0;
    this.hookSkillStarted = 0;
    this.hookCheckStop = 0;
    return path;
  }

  /**
   * Graceful exit on stdin EOF (runtime.md "Interview ends and server exit"): pending is
   * stood down, complete is ended, an active interview keeps its journal unfinished
   * (pid file removed, log flagged `interrupted`), none does nothing.
   */
  onEof(): void {
    if (this.state === "pending") {
      this.finish(
        "stood_down",
        "not_engaged",
        "server exit while pending",
        "server exited before the skill engaged",
      );
    } else if (this.state === "complete") {
      this.finish("ended_after_completion", "register", "server exit", null);
    } else if (this.state === "active") {
      this.writeLog(
        "active",
        "register",
        "interrupted",
        "interrupted: the server exited while the interview was running",
      );
      this.journal?.removePidFile();
    }
  }

  /**
   * REQ-26: a hook call whose session id differs from the bound one ends the interview
   * (normal, abandoned if items are open, "ended after completion", or stood down if
   * pending); the caller then behaves as with no interview.
   */
  private sessionChange(sessionId: string): void {
    if (
      this.state === "none" ||
      this.boundSession === null ||
      sessionId === this.boundSession
    ) {
      return;
    }
    if (this.state === "pending") {
      this.finish(
        "stood_down",
        "not_engaged",
        "session change",
        "session changed before the skill engaged",
      );
    } else if (this.state === "complete") {
      this.finish("ended_after_completion", "register", "session change", null);
    } else if (this.openItems().length > 0) {
      this.finish(
        "abandoned",
        "register",
        "session change",
        "abandoned: items were open at the session change",
      );
    } else {
      this.finish("normal", "register", "session change", null);
    }
  }

  private endInterview(args: JsonObject): JsonObject {
    checkKnown(args, ["mode", "abandon", "reason"]);
    const mode = strArg(args, "mode");
    const abandon = args["abandon"];
    const reason = strArg(args, "reason");
    if (mode !== undefined) {
      if (mode !== "plain") throw bad("mode must be plain");
      if (abandon !== undefined || reason !== undefined) {
        throw bad("mode plain takes no abandon or reason");
      }
    } else if (abandon !== undefined) {
      if (abandon !== true) throw bad("abandon must be true");
      if (reason === undefined) throw bad("abandon requires reason");
    }
    if (reason !== undefined) {
      if (!isNonblank(reason)) throw bad("reason must not be blank");
      if (abandon === undefined) throw bad("reason requires abandon");
    }
    const id = this.interviewId;
    if (this.state === "none" || id === null) {
      throw new ToolError("no interview to end");
    }
    if (this.state === "pending") {
      if (mode !== "plain") {
        throw new ToolError("pending interview: use mode plain");
      }
      const path = this.finish(
        "stood_down",
        "plain",
        "end_interview mode plain",
        "skill ran in plain mode",
      );
      return this.endResult(id, "stood_down", path);
    }
    if (mode === "plain") {
      throw new ToolError("mode plain applies only to a pending interview");
    }
    if (this.state === "complete") {
      if (abandon === true) {
        throw new ToolError(
          "nothing is open: abandon applies only to open items",
        );
      }
      const path = this.finish(
        "ended_after_completion",
        "register",
        "end_interview",
        null,
      );
      return this.endResult(id, "ended_after_completion", path);
    }
    const open = this.openItems();
    if (abandon === true) {
      if (open.length === 0) {
        throw new ToolError(
          "nothing is open: abandon applies only to open items",
        );
      }
      const path = this.finish(
        "ended_incomplete_by_model",
        "register",
        reason ?? "",
        "ended incomplete by the model",
      );
      return this.endResult(id, "ended_incomplete_by_model", path);
    }
    if (open.length > 0) {
      const names = open.map((i) => i.id).join(", ");
      throw new ToolError(
        `items are still open: ${names}; resolve or park them, or pass abandon true with a reason`,
      );
    }
    const path = this.finish("normal", "register", "end_interview", null);
    return this.endResult(id, "normal", path);
  }

  /**
   * Picks the journal `resume` or `discard` acts on (REQ-52). Errors, in order: wrong
   * state; for an id, live-owned or unknown; without one, none or several.
   */
  private pickUnfinished(args: JsonObject): string {
    checkKnown(args, ["id"]);
    const id = optIdArg(args, "id");
    if (this.state === "active" || this.state === "complete") {
      throw new ToolError(`wrong state: ${this.state}`);
    }
    const all = scanUnfinished(projectDir(), this.interviewId);
    if (id !== null) {
      const found = all.find((j) => j.id === id);
      if (found === undefined && id !== this.interviewId) {
        throw new ToolError(`unknown interview id: ${id}`);
      }
      if (found === undefined || found.alive) {
        throw new ToolError(`journal is live-owned: ${id}`);
      }
      return id;
    }
    const dead = all.filter((j) => !j.alive);
    const first = dead[0];
    if (first === undefined) throw new ToolError("no unfinished journal");
    if (dead.length > 1) {
      const ids = dead.map((j) => j.id).join(", ");
      throw new ToolError(`several unfinished journals: ${ids}`);
    }
    return first.id;
  }

  /** `discard`: archives an unfinished journal and starts nothing (REQ-52). */
  private discard(args: JsonObject): JsonObject {
    const id = this.pickUnfinished(args);
    try {
      archiveJournal(projectDir(), id);
    } catch (e) {
      throw new ToolError(
        `cannot archive ${id}: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
    return { interview_id: id, archived: true };
  }

  /**
   * `resume` (REQ-52): claims the journal's pid file (rename-aside, verify, exclusive
   * create), replays it, rebinds to last_seen_session_id and returns to `active`. A
   * replay failure removes the new pid file and leaves the journal unfinished. A pending
   * interview is stood down once the resume has succeeded.
   */
  private resume(args: JsonObject): JsonObject {
    const id = this.pickUnfinished(args);
    const dir = projectDir();
    const pid = pidPath(dir, id);
    try {
      claim(dir, id, readPid(pid));
    } catch (e) {
      if (e instanceof ClaimError) throw new ToolError(e.message);
      throw new ToolError(
        `cannot claim ${id}: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
    const file = join(dir, `${id}.jsonl`);
    let result: ReturnType<typeof replay>;
    let text = "";
    try {
      text = readFileSync(file, "utf8");
      result = replay(text);
    } catch (e) {
      removeFile(pid);
      throw new ToolError(
        `cannot read ${id}: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
    if (!result.ok) {
      removeFile(pid);
      throw new ToolError(
        `replay failed at line ${result.line}: ${result.why}`,
      );
    }
    const s = result.state;
    try {
      // A torn final line must go, or the next event would be glued to it.
      if (text !== "" && !text.endsWith("\n")) {
        truncateSync(
          file,
          Buffer.byteLength(text.slice(0, text.lastIndexOf("\n") + 1)),
        );
      }
    } catch {
      // fail open: the append below may then fail and set write_error
    }
    if (this.state === "pending") {
      this.finish(
        "stood_down",
        "not_engaged",
        "replaced by resume",
        "replaced by resume",
      );
    }
    this.register = s.register;
    this.reopens = s.reopens;
    this.interviewId = id;
    const journal = new Journal(id, s.lastSeq);
    journal.adoptPidFile();
    this.journal = journal;
    this.declog = new DecisionLog(id);
    this.state = "active";
    this.implicitStart = s.implicit;
    this.boundSession = this.lastSeenSessionId;
    this.promptSeen = false;
    this.hookNotePrompt = s.hookNotePrompt;
    this.hookSkillStarted = s.hookSkillStarted;
    this.hookCheckStop = s.hookCheckStop;
    this.blocks = { ...s.blocks };
    this.emit("resume", {
      session_id: this.lastSeenSessionId,
      from_seq: s.lastSeq,
    });
    journal.flush();
    const c = s.register.counts();
    return {
      interview_id: id,
      state: "active",
      last_event: s.lastSeq,
      items: {
        open: c.open,
        resolved: c.resolved,
        parked: c.parked,
        stale: c.stale,
      },
    };
  }

  private endResult(
    id: string,
    endKind: string,
    path: string | null,
  ): JsonObject {
    return {
      interview_id: id,
      state: "ended",
      end_kind: endKind,
      decision_log: path ?? "",
    };
  }

  /** A successful mutating call engages a pending interview and wakes a complete one. */
  private engage(via: string): void {
    if (this.state === "pending") {
      this.state = "active";
      this.emit("engage", { via });
    } else if (this.state === "complete") {
      this.state = "active";
    }
  }

  private beginInterview(args: JsonObject): JsonObject {
    checkKnown(args, ["tool_schema", "skill", "mode"]);
    const ts = toolSchemaArg(args);
    const skill = textArg(args, "skill");
    if (required("mode", strArg(args, "mode")) !== "register") {
      throw bad("mode must be register");
    }
    if (ts !== TOOL_SCHEMA) {
      this.versionMismatch = { plugin: ts, server: TOOL_SCHEMA };
      throw new ToolError(
        `tool_schema mismatch: plugin ${ts}, server ${TOOL_SCHEMA}`,
      );
    }
    this.refuseWhileUnfinished();
    this.versionMismatch = null;
    if (this.state === "none") {
      const id = this.startInterview("begin_interview", skill, null);
      return { interview_id: id, state: "active", existing: false };
    }
    const wasPending = this.state === "pending";
    if (wasPending) this.engage("begin_interview");
    return {
      interview_id: this.interviewId,
      state: this.state === "complete" ? "complete" : "active",
      existing: true,
    };
  }

  /** The user-answer rule for a resolve or park by the user (REQ-32, 33, 44). */
  private checkBudget(): void {
    if (!this.anyPrompt) throw new ToolError(NOT_FIRING);
    if (this.used >= this.allowance) {
      throw new ToolError(
        `user-answer budget spent: ${this.used} of ${this.allowance} used for the current user prompt`,
      );
    }
  }

  /** Rules for a volunteered user answer in log_item (REQ-32). */
  private checkVolunteered(): void {
    if (!this.anyPrompt) throw new ToolError(NOT_FIRING);
    if (!this.promptSinceStop) {
      throw new ToolError(
        "no user prompt since the last Stop check: a volunteered user answer needs a new prompt",
      );
    }
    if (this.volunteered >= VOLUNTEERED_CAP) {
      throw new ToolError(
        `volunteered-answer cap reached: ${VOLUNTEERED_CAP} per user prompt`,
      );
    }
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
    const item: NewItem = { id, kind, text, parent, dependsOn, answer };
    this.refuseWhileUnfinished();
    // Rule errors, in contract order, leave the state untouched.
    this.register.checkLog(item);
    if (answer !== null) {
      if (kind === "decision" && answer.source === "model") {
        throw new ToolError(
          `decision item can be answered by the user only: ${id ?? "(new item)"}`,
        );
      }
      if (answer.source === "user") this.checkVolunteered();
    }
    const logged = this.register.log(item);
    if (this.state === "none") {
      this.startInterview("implicit", null, null);
    } else {
      this.engage("log_item");
    }
    if (answer?.source === "user") this.volunteered += 1;
    this.emit("item", {
      id: logged.id,
      item_kind: kind,
      text,
      parent,
      depends_on: dependsOn,
    });
    if (answer !== null) {
      this.emit("resolve", {
        id: logged.id,
        answer: answer.text,
        source: answer.source,
        rationale: answer.rationale,
      });
    }
    return { id: logged.id, status: logged.status };
  }

  private resolve(args: JsonObject): JsonObject {
    checkKnown(args, ["id", "answer", "source", "rationale"]);
    const id = idArg(args, "id");
    const answer = answerGroup(args, true);
    if (answer === null) throw bad("answer, source and rationale are required");
    this.refuseWhileUnfinished();
    const item = this.register.assertResolvable(id);
    if (answer.source === "model") {
      if (item.kind === "decision") {
        throw new ToolError(
          `decision item can be answered by the user only: ${id}`,
        );
      }
      const children = this.register.childCount(id);
      if (children > 0 && children < MIN_CHILDREN) {
        throw new ToolError(
          `parent ${id} has ${children} child item(s), fewer than ${MIN_CHILDREN}: only the user may resolve it (REQ-35)`,
        );
      }
    } else {
      this.checkBudget();
    }
    this.register.resolve(id, answer);
    if (answer.source === "user") this.used += 1;
    this.engage("resolve");
    this.emit("resolve", {
      id,
      answer: answer.text,
      source: answer.source,
      rationale: answer.rationale,
    });
    return { id, status: "Resolved" };
  }

  private park(args: JsonObject): JsonObject {
    checkKnown(args, ["id", "reason", "parked_by"]);
    const id = idArg(args, "id");
    const reason = textArg(args, "reason");
    const by = required("parked_by", actorArg(args, "parked_by"));
    this.refuseWhileUnfinished();
    const item = this.register.assertParkable(id);
    if (by === "model") {
      if (item.kind === "decision") {
        throw new ToolError(
          `decision item can be parked by the user only: ${id}`,
        );
      }
    } else {
      this.checkBudget();
    }
    this.register.park(id, by, reason);
    if (by === "user") this.used += 1;
    this.engage("park");
    this.emit("park", { id, reason, parked_by: by });
    return { id, status: "Parked" };
  }

  private reopen(args: JsonObject): JsonObject {
    checkKnown(args, ["id", "reason"]);
    const id = idArg(args, "id");
    const reason = textArg(args, "reason");
    this.refuseWhileUnfinished();
    const stale = this.register.reopen(id);
    this.engage("reopen");
    this.reopens.push({ id, reason, stale });
    this.emit("reopen", { id, reason, stale });
    return { id, status: "Open", stale };
  }

  private status(args: JsonObject): JsonObject {
    checkKnown(args, []);
    const c = this.register.counts();
    return {
      server_version: pkg.version,
      tool_schema: TOOL_SCHEMA,
      journal_schema: JOURNAL_SCHEMA,
      state: this.state,
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
        skill_started: this.hookSkillStarted,
        check_stop: this.hookCheckStop,
        prompt_seen: this.promptSeen,
      },
      last_event: this.journal?.seq ?? 0,
      resumable: this.resumable(),
      version_mismatch:
        this.versionMismatch === null
          ? null
          : {
              plugin_tool_schema: this.versionMismatch.plugin,
              server_tool_schema: this.versionMismatch.server,
            },
      decision_log: this.declog?.written ? this.declog.path : null,
      write_error: this.journal?.writeError ?? this.declog?.writeError ?? null,
    };
  }

  /**
   * Hook-facing. Always records the session id; a user prompt resets the per-prompt
   * allowance, volunteered count and block count (REQ-32, 42). A harness-injected
   * agent-message prompt is a complete no-op (REQ-41).
   */
  private notePrompt(args: JsonObject): JsonObject {
    checkKnown(args, ["session_id", "prompt"]);
    const sessionId = textArg(args, "session_id");
    const prompt = required("prompt", strArg(args, "prompt"));
    if (prompt.startsWith(AGENT_MESSAGE_PREFIX)) return {};
    this.sessionChange(sessionId);
    this.lastSeenSessionId = sessionId;
    this.anyPrompt = true;
    this.allowance = this.presentedUnknown
      ? 4
      : Math.max(1, this.presented.size);
    this.used = 0;
    this.volunteered = 0;
    this.promptSinceStop = true;
    this.presented = new Set();
    this.presentedUnknown = false;
    if (this.state === "none") {
      this.promptSeen = true;
      return {};
    }
    this.hookNotePrompt += 1;
    this.blocks.prompt = 0;
    this.emit("hook", { tool: "note_prompt", session_id: sessionId });
    this.emit("prompt", { session_id: sessionId, count: this.hookNotePrompt });
    return {};
  }

  /**
   * Hook-facing. A listed skill name (exact match) starts a pending interview even on a
   * tool_schema mismatch; the mismatch is only recorded (REQ-77). Never blocks.
   */
  private skillStarted(args: JsonObject): JsonObject {
    checkKnown(args, ["tool_schema", "session_id", "skill"]);
    const ts = toolSchemaArg(args);
    const sessionId = textArg(args, "session_id");
    const skill = textArg(args, "skill");
    this.sessionChange(sessionId);
    this.lastSeenSessionId = sessionId;
    const mismatch = ts !== TOOL_SCHEMA;
    this.versionMismatch = mismatch
      ? { plugin: ts, server: TOOL_SCHEMA }
      : null;
    if (this.state === "none") {
      if (LISTED_SKILLS.includes(skill)) {
        this.startInterview("hook", skill, sessionId);
      }
    } else {
      this.hookSkillStarted += 1;
      this.emit("hook", { tool: "skill_started", session_id: sessionId });
    }
    if (mismatch && this.state !== "none") {
      this.emit("mismatch", {
        plugin_tool_schema: ts,
        server_tool_schema: TOOL_SCHEMA,
      });
    }
    return {};
  }

  /** Open and Stale items, model-parked items and under-sized parents (REQ-35, 43). */
  private gateList(): Item[] {
    return this.register.ordered().filter((i) => {
      const s = i.status;
      if (s.kind === "Open" || s.kind === "Stale") return true;
      if (s.kind === "Parked") return s.by === "model";
      const children = this.register.childCount(i.id);
      return (
        s.answer.source === "model" && children > 0 && children < MIN_CHILDREN
      );
    });
  }

  private blockReason(open: readonly Item[]): string {
    const lines = open
      .slice(0, 10)
      .map((i) => `- ${i.id}: ${truncate(i.text)}`);
    const rest = open.length > 10 ? `\n${open.length - 10} more` : "";
    return `Items are still unresolved in the decision register. Resolve, park or ask about each before finishing:\n${lines.join("\n")}${rest}`;
  }

  /** A Stop check flushes the journal queue (journal.md). */
  private checkStopAndFlush(args: JsonObject): JsonObject {
    const r = this.checkStop(args);
    this.journal?.flush();
    return r;
  }

  /** Hook-facing. Always returns `{}` or a block decision (REQ-42, 43, 45). */
  private checkStop(args: JsonObject): JsonObject {
    checkKnown(args, ["session_id", "last_assistant_message"]);
    const sessionId = textArg(args, "session_id");
    const message = required(
      "last_assistant_message",
      strArg(args, "last_assistant_message"),
    );
    this.sessionChange(sessionId);
    this.lastSeenSessionId = sessionId;
    this.promptSinceStop = false;
    if (this.state === "none") {
      this.promptSeen = false;
      return {};
    }
    this.hookCheckStop += 1;
    this.emit("hook", { tool: "check_stop", session_id: sessionId });
    if (this.state === "pending" && this.hookCheckStop >= 2) {
      // The second Stop check with no engaging call: the skill never engaged (rule 3).
      this.finish(
        "stood_down",
        "not_engaged",
        "skill did not engage",
        "skill did not engage",
      );
      return {};
    }
    if (this.state !== "active") return {};

    const stop = (
      decision: "allow" | "block",
      classification: "finish" | "question" | "unknown",
      presented: string[] | null,
    ): void => {
      this.emit("stop", {
        decision,
        classification,
        presented,
        blocks: { ...this.blocks },
      });
    };
    let cls: Classification;
    try {
      cls = classify(
        message,
        this.register.list().map((i) => ({
          id: i.id,
          text: i.text,
          status: i.status.kind,
        })),
      );
    } catch {
      cls = { kind: "unknown" };
    }
    if (cls.kind === "empty") {
      stop("allow", "question", []);
      return {};
    }
    if (cls.kind === "unknown") {
      this.presentedUnknown = true;
      stop("allow", "unknown", null);
      return {};
    }
    if (cls.kind === "question") {
      if (cls.presented.length > 0) {
        for (const id of cls.presented) this.presented.add(id);
        stop("allow", "question", cls.presented);
        return {};
      }
      if (
        this.blocks.prompt < PROMPT_CAP &&
        this.blocks.unlogged < UNLOGGED_CAP
      ) {
        this.blocks.prompt += 1;
        this.blocks.unlogged += 1;
        stop("block", "question", []);
        return {
          decision: "block",
          reason:
            "That question is not in the decision register. Log it with log_item, then ask it again.",
        };
      }
      stop("allow", "question", []);
      return {};
    }
    const open = this.gateList();
    if (open.length === 0) {
      stop("allow", "finish", []);
      this.emit("complete", {});
      this.state = "complete";
      this.writeLog("complete", "register", "none", null);
      return {};
    }
    if (this.blocks.prompt < PROMPT_CAP && this.blocks.finish < FINISH_CAP) {
      this.blocks.prompt += 1;
      this.blocks.finish += 1;
      stop("block", "finish", []);
      return { decision: "block", reason: this.blockReason(open) };
    }
    stop("allow", "finish", []);
    this.writeLog("active", "register", "none", null);
    return {};
  }
}
