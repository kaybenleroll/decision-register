// The decision log (contract/runtime.md "Decision log"; REQ-23, REQ-62, REQ-63).
//
// A markdown file written by the server, never by the model. The header is machine-read
// (`key: value` lines, fixed order); the body is for people. Written to
// `<project>/.scratch/` if that directory exists at the interview's first log, else
// `<project>/decision-logs/` (created with a `.gitignore` holding `*`).

import { mkdirSync, renameSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { projectPath } from "./journal.ts";
import type { Item } from "./register.ts";

export type LogState = "active" | "complete" | "ended";
export type LogMode = "register" | "plain" | "not engaged";

export interface LogSnapshot {
  interviewId: string;
  /** The `seq` of the last journal event at the moment of writing (0 if none). */
  journalPosition: number;
  state: LogState;
  mode: LogMode;
  /** `none` while running, else an end_interview kind, `abandoned` or `interrupted`. */
  endKind: string;
  implicitStart: boolean;
  /** A human note such as "skill did not engage" (null when there is none). */
  note: string | null;
  items: readonly Item[];
  hookHealth: Record<string, number>;
}

/** Keeps a server-created `decision-logs/` folder out of version control (REQ-62). */
const IGNORE_FILE = ".gitignore";

const isDir = (p: string): boolean => {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
};

function itemLines(i: Item): string[] {
  const s = i.status;
  const head = `- ${i.id} (${i.kind}, ${s.kind}): ${i.text}`;
  if (s.kind === "Resolved" || s.kind === "Stale") {
    return [
      head,
      `  - answer (${s.answer.source}): ${s.answer.text}`,
      `  - rationale: ${s.answer.rationale}`,
    ];
  }
  if (s.kind === "Parked") return [head, `  - parked by ${s.by}: ${s.reason}`];
  return [head];
}

export function render(snap: LogSnapshot): string {
  const head = [
    `interview_id: ${snap.interviewId}`,
    `journal_position: ${snap.journalPosition}`,
    `state: ${snap.state}`,
    `mode: ${snap.mode}`,
    `end_kind: ${snap.endKind}`,
    `implicit_start: ${snap.implicitStart}`,
  ];
  const body = [
    "",
    "# Decision log",
    "",
    ...(snap.note === null ? [] : [`Note: ${snap.note}`, ""]),
    "## Items",
    "",
    ...(snap.items.length === 0 ? ["(none)"] : snap.items.flatMap(itemLines)),
    "",
    "## Hook health",
    "",
    ...Object.entries(snap.hookHealth).map(([k, v]) => `- ${k}: ${v}`),
    "",
  ];
  return [...head, ...body].join("\n");
}

export class DecisionLog {
  /** The log's absolute path once its directory is chosen, even if writing failed. */
  path: string | null = null;
  /** True once a write has succeeded. */
  written = false;
  writeError: string | null = null;
  private dir: string | null = null;
  private readonly interviewId: string;

  constructor(interviewId: string) {
    this.interviewId = interviewId;
  }

  /** Writes (replaces) the log atomically. Fails open: a failure sets `writeError`. */
  write(snap: LogSnapshot): void {
    const dir = this.chooseDir();
    const file = join(dir, `decision-${this.interviewId}.md`);
    this.path = file;
    try {
      if (!isDir(dir)) {
        mkdirSync(dir, { recursive: true, mode: 0o700 });
        writeFileSync(join(dir, IGNORE_FILE), "*\n", { mode: 0o600 });
      }
      const tmp = `${file}.tmp`;
      writeFileSync(tmp, render(snap), { mode: 0o600 });
      renameSync(tmp, file);
      this.written = true;
      this.writeError = null;
    } catch (e) {
      this.writeError = e instanceof Error ? e.message : String(e);
    }
  }

  /** `.scratch/` if it exists at the first write, else `decision-logs/`; then kept. */
  private chooseDir(): string {
    if (this.dir !== null) return this.dir;
    const project = projectPath();
    const scratch = join(project, ".scratch");
    this.dir = isDir(scratch) ? scratch : join(project, "decision-logs");
    return this.dir;
  }
}
