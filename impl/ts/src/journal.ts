// Minimal append-only JSONL journal (contract/journal.md, runtime.md "Journal location").
//
// I2 scope: every event is appended as it happens and a write failure never fails a call
// (it sets `writeError`). Write-behind queueing, the pid file, archive, pruning and
// resume are I4.

import { createHash } from "node:crypto";
import { appendFileSync, mkdirSync, renameSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

import { createPid, ownRecord, pidPath, removeFile } from "./pidfile.ts";

const nonEmpty = (v: string | undefined): string | undefined =>
  v === undefined || v === "" ? undefined : v;

/** The data root: CLAUDE_PLUGIN_DATA, else `<temp>/decision-register-data`. */
export const dataRoot = (): string =>
  nonEmpty(process.env["CLAUDE_PLUGIN_DATA"]) ??
  join(tmpdir(), "decision-register-data");

/** The project path, verbatim: CLAUDE_PROJECT_DIR, else the working directory. */
export const projectPath = (): string =>
  nonEmpty(process.env["CLAUDE_PROJECT_DIR"]) ?? process.cwd();

/** First 16 hex digits of the SHA-256 of the UTF-8 project path. */
export const projectKey = (): string =>
  createHash("sha256").update(projectPath(), "utf8").digest("hex").slice(0, 16);

/** The project folder `<data root>/<project key>` that holds journals and pid files. */
export const projectDir = (): string => join(dataRoot(), projectKey());

export type JournalFields = Record<string, unknown>;

export class Journal {
  /** The `seq` of the last event; also `status.last_event`. */
  seq = 0;
  writeError: string | null = null;
  /** True while this journal's pid file was created (or claimed) by this process. */
  hasPid = false;
  private readonly dir: string;
  private readonly file: string;
  private readonly id: string;

  /** `seq` is the last event number already in the file (a resumed journal). */
  constructor(interviewId: string, seq = 0) {
    this.id = interviewId;
    this.seq = seq;
    this.dir = projectDir();
    this.file = join(this.dir, `${interviewId}.jsonl`);
  }

  /**
   * Creates the pid file exclusively, before the first event (REQ-52). Returns false when
   * the id is already taken (draw another); any other failure fails open and returns true.
   */
  createPidFile(): boolean {
    try {
      mkdirSync(this.dir, { recursive: true, mode: 0o700 });
      createPid(pidPath(this.dir, this.id), ownRecord(this.id));
      this.hasPid = true;
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code === "EEXIST") return false;
    }
    return true;
  }

  /** Marks the pid file as claimed by this process (after a resume claim). */
  adoptPidFile(): void {
    this.hasPid = true;
  }

  /** Removes this journal's pid file, if this process owns one. */
  removePidFile(): void {
    if (this.hasPid) removeFile(pidPath(this.dir, this.id));
    this.hasPid = false;
  }

  append(kind: string, fields: JournalFields): void {
    this.seq += 1;
    const event = {
      v: 2,
      ts: new Date().toISOString(),
      seq: this.seq,
      kind,
      ...fields,
    };
    try {
      mkdirSync(this.dir, { recursive: true, mode: 0o700 });
      appendFileSync(this.file, `${JSON.stringify(event)}\n`, { mode: 0o600 });
      this.writeError = null;
    } catch (e) {
      this.writeError = e instanceof Error ? e.message : String(e);
    }
  }

  /**
   * Moves the journal to `archive/` (renamed, not copied; REQ-23). Fails open: a failure
   * sets `writeError` and leaves the journal where it is.
   */
  archive(): void {
    try {
      const dest = join(this.dir, "archive");
      mkdirSync(dest, { recursive: true, mode: 0o700 });
      renameSync(this.file, join(dest, basename(this.file)));
    } catch (e) {
      this.writeError = e instanceof Error ? e.message : String(e);
    }
  }
}
