// Journal replay for `resume` (contract/journal.md "Replay failure modes", "Compatibility").
//
// Rebuilds the register, the hook counts and the finish-gate caps from the events. A torn
// final line is ignored; any other defect fails, naming the 1-based line.

import type { ReopenRecord } from "./declog.ts";
import {
  type Actor,
  isNonblank,
  makeAnswer,
  parseActor,
  parseKind,
  Register,
  RegisterError,
} from "./register.ts";

export interface Blocks {
  prompt: number;
  finish: number;
  unlogged: number;
}

/** What a replay rebuilds. */
export interface Replayed {
  register: Register;
  reopens: ReopenRecord[];
  interviewId: string | null;
  skill: string | null;
  route: string | null;
  sessionId: string | null;
  implicit: boolean;
  /** True when an `end` event was seen: the journal is not unfinished. */
  ended: boolean;
  /** The `seq` of the last valid event (0 if none). */
  lastSeq: number;
  hookNotePrompt: number;
  hookSkillStarted: number;
  hookCheckStop: number;
  blocks: Blocks;
}

export type ReplayResult =
  | { ok: true; state: Replayed }
  | { ok: false; line: number; why: string };

const KINDS: readonly string[] = [
  "start",
  "engage",
  "prompt",
  "hook",
  "item",
  "resolve",
  "park",
  "reopen",
  "stop",
  "complete",
  "mismatch",
  "resume",
  "end",
];
/** The only kinds a v1 (spike 3) line may have. */
const V1_KINDS: readonly string[] = ["item", "resolve", "park", "reopen"];

class Defect extends Error {}

const str = (e: Record<string, unknown>, k: string): string => {
  const v = e[k];
  if (typeof v !== "string") throw new Defect(`${k} must be a string`);
  return v;
};

const strOrNull = (e: Record<string, unknown>, k: string): string | null => {
  const v = e[k];
  return typeof v === "string" ? v : null;
};

const actor = (e: Record<string, unknown>, k: string): Actor => {
  const a = parseActor(str(e, k));
  if (a === undefined) throw new Defect(`${k} must be user or model`);
  return a;
};

function resolveEvent(r: Register, e: Record<string, unknown>): void {
  const made = makeAnswer(
    str(e, "answer"),
    actor(e, "source"),
    str(e, "rationale"),
  );
  if (!made.ok) throw new Defect(`${made.param} ${made.why}`);
  r.resolve(str(e, "id"), made.answer);
}

function apply(s: Replayed, kind: string, e: Record<string, unknown>): void {
  switch (kind) {
    case "start": {
      s.interviewId = strOrNull(e, "interview_id");
      s.route = strOrNull(e, "route");
      s.skill = strOrNull(e, "skill");
      s.sessionId = strOrNull(e, "session_id");
      s.implicit = e["implicit"] === true;
      s.hookNotePrompt =
        typeof e["prompt_count"] === "number" ? e["prompt_count"] : 0;
      s.hookSkillStarted = s.route === "hook" ? 1 : 0;
      break;
    }
    case "engage":
    case "resume": {
      // A claim: the call that wrote the event took the interview for this skill.
      const claimed = strOrNull(e, "skill");
      if (claimed !== null) s.skill = claimed;
      break;
    }
    case "hook": {
      const tool = e["tool"];
      if (tool === "note_prompt") s.hookNotePrompt += 1;
      else if (tool === "skill_started") s.hookSkillStarted += 1;
      else if (tool === "check_stop") s.hookCheckStop += 1;
      break;
    }
    case "item": {
      const kindName = parseKind(str(e, "item_kind"));
      if (kindName === undefined) throw new Defect("item_kind is not a kind");
      const text = str(e, "text");
      if (!isNonblank(text)) throw new Defect("text must not be blank");
      const deps = e["depends_on"];
      s.register.log({
        id: str(e, "id"),
        kind: kindName,
        text,
        parent: strOrNull(e, "parent"),
        dependsOn: Array.isArray(deps)
          ? deps.filter((d): d is string => typeof d === "string")
          : [],
        answer: null,
      });
      break;
    }
    case "resolve":
      resolveEvent(s.register, e);
      break;
    case "park": {
      const reason = str(e, "reason");
      if (!isNonblank(reason)) throw new Defect("reason must not be blank");
      s.register.park(str(e, "id"), actor(e, "parked_by"), reason);
      break;
    }
    case "reopen": {
      const id = str(e, "id");
      const stale = s.register.reopen(id);
      s.reopens.push({ id, reason: strOrNull(e, "reason") ?? "", stale });
      break;
    }
    case "stop": {
      const b = e["blocks"];
      if (typeof b === "object" && b !== null) {
        const o = b as Record<string, unknown>;
        const n = (k: string): number => (typeof o[k] === "number" ? o[k] : 0);
        s.blocks = {
          prompt: n("prompt"),
          finish: n("finish"),
          unlogged: n("unlogged"),
        };
      }
      break;
    }
    case "end":
      s.ended = true;
      break;
    default:
      break; // prompt, complete, mismatch: no register data
  }
}

/** Replays the whole text of a journal file. */
export function replay(text: string): ReplayResult {
  const s: Replayed = {
    register: new Register(),
    reopens: [],
    interviewId: null,
    skill: null,
    route: null,
    sessionId: null,
    implicit: false,
    ended: false,
    lastSeq: 0,
    hookNotePrompt: 0,
    hookSkillStarted: 0,
    hookCheckStop: 0,
    blocks: { prompt: 0, finish: 0, unlogged: 0 },
  };
  const lines = text.split("\n");
  const torn = !text.endsWith("\n");
  if (!torn) lines.pop(); // the empty piece after the final newline
  let prev = 0;
  for (const [i, raw] of lines.entries()) {
    const lineNo = i + 1;
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      if (torn && i === lines.length - 1) break; // a torn final line is ignored
      return { ok: false, line: lineNo, why: "invalid JSON" };
    }
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      Array.isArray(parsed)
    ) {
      return { ok: false, line: lineNo, why: "not an object" };
    }
    const e = parsed as Record<string, unknown>;
    const v = e["v"] === undefined ? 1 : e["v"];
    if (v !== 1 && v !== 2)
      return { ok: false, line: lineNo, why: "unsupported v" };
    const kind = e["kind"];
    if (typeof kind !== "string" || !KINDS.includes(kind)) {
      return { ok: false, line: lineNo, why: "unknown kind" };
    }
    if (v === 1 && !V1_KINDS.includes(kind)) {
      return { ok: false, line: lineNo, why: "kind not valid in v1" };
    }
    const seq = v === 1 && e["seq"] === undefined ? lineNo : e["seq"];
    if (typeof seq !== "number" || !Number.isInteger(seq) || seq !== prev + 1) {
      return { ok: false, line: lineNo, why: "seq out of sequence" };
    }
    try {
      apply(s, kind, e);
    } catch (err) {
      if (err instanceof Defect || err instanceof RegisterError) {
        return { ok: false, line: lineNo, why: err.message };
      }
      throw err;
    }
    prev = seq;
    s.lastSeq = seq;
  }
  return { ok: true, state: s };
}
