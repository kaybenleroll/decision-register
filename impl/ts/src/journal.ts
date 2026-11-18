// Minimal append-only JSONL journal (contract/journal.md, runtime.md "Journal location").
//
// I2 scope: every event is appended as it happens and a write failure never fails a call
// (it sets `writeError`). Write-behind queueing, the pid file, archive, pruning and
// resume are I4.

import { createHash } from "node:crypto";
import { appendFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

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

export type JournalFields = Record<string, unknown>;

export class Journal {
  /** The `seq` of the last event; also `status.last_event`. */
  seq = 0;
  writeError: string | null = null;
  private readonly dir: string;
  private readonly file: string;

  constructor(interviewId: string) {
    this.dir = join(dataRoot(), projectKey());
    this.file = join(this.dir, `${interviewId}.jsonl`);
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
}
