// The pid file and the claim race (contract/runtime.md "Pid file and claim"; REQ-52, REQ-61).
//
// `<dir>/<id>.pid` holds {"pid", "start", "interview_id"}. The owner is alive iff the pid
// exists and, when `start` is recorded, the pid's current start time equals it. There is
// no lock primitive: a claim is an exclusive (`wx`) create, and replacing a stale file is
// rename-aside, verify, exclusive create.

import { randomBytes } from "node:crypto";
import {
  closeSync,
  existsSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeSync,
} from "node:fs";
import { join } from "node:path";

export interface PidRecord {
  pid: number;
  start: number | null;
  interview_id: string;
}

/**
 * Interview ids whose pid file this process currently holds. A pid file naming this
 * process is alive only if it is one of these (a leftover from an earlier run that
 * reused the pid is not).
 */
const heldClaims = new Set<string>();
export const holdsClaim = (id: string): boolean => heldClaims.has(id);
export const noteClaim = (id: string): void => void heldClaims.add(id);
export const dropClaim = (id: string): void => void heldClaims.delete(id);

/** Raised when another claimer wins (message `journal already claimed`). */
export class ClaimError extends Error {}

export const pidPath = (dir: string, id: string): string =>
  join(dir, `${id}.pid`);

/**
 * The start time of `pid` in clock ticks since boot (field 22 of /proc/<pid>/stat: the
 * 20th token after the last `)`), or null if it cannot be read.
 */
export function startTime(pid: number): number | null {
  try {
    const text = readFileSync(`/proc/${pid}/stat`, "utf8");
    const rest = text.slice(text.lastIndexOf(")") + 1).trim();
    const field = rest.split(/\s+/)[19];
    if (field === undefined) return null;
    const n = Number(field);
    return Number.isInteger(n) ? n : null;
  } catch {
    return null;
  }
}

/** Whether a process with this pid exists (signal 0; EPERM still means it exists). */
export function pidExists(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === "EPERM";
  }
}

/** The record this process writes for itself. */
export const ownRecord = (id: string): PidRecord => ({
  pid: process.pid,
  start: startTime(process.pid),
  interview_id: id,
});

export const encode = (rec: PidRecord): string => `${JSON.stringify(rec)}\n`;

/** Parses pid-file content; null when empty, unparseable or the wrong shape. */
export function parsePid(content: string | null): PidRecord | null {
  if (content === null) return null;
  try {
    const v: unknown = JSON.parse(content);
    if (typeof v !== "object" || v === null) return null;
    const o = v as Record<string, unknown>;
    const pid = o["pid"];
    const start = o["start"];
    if (typeof pid !== "number" || !Number.isInteger(pid) || pid < 1) {
      return null;
    }
    if (
      start !== null &&
      !(typeof start === "number" && Number.isInteger(start))
    ) {
      return null;
    }
    return {
      pid,
      start,
      interview_id:
        typeof o["interview_id"] === "string" ? o["interview_id"] : "",
    };
  } catch {
    return null;
  }
}

/**
 * Liveness of the owner named by `content`. A missing, empty or unparseable file means
 * no owner. The reader's own pid is alive only when `ownClaim` (it is its current claim).
 */
export function ownerAlive(content: string | null, ownClaim: boolean): boolean {
  const rec = parsePid(content);
  if (rec === null) return false;
  if (rec.pid === process.pid) return ownClaim;
  if (!pidExists(rec.pid)) return false;
  return rec.start === null || startTime(rec.pid) === rec.start;
}

/** The file's content, or null if it cannot be read. */
export function readPid(path: string): string | null {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return null;
  }
}

/** Exclusive create (`O_CREAT|O_EXCL|O_WRONLY`, mode 0600); EEXIST propagates. */
export function createPid(path: string, rec: PidRecord): void {
  const fd = openSync(path, "wx", 0o600);
  try {
    writeSync(fd, encode(rec));
  } finally {
    closeSync(fd);
  }
}

/** Removes a file, ignoring one that is already gone. */
export function removeFile(path: string): void {
  try {
    unlinkSync(path);
  } catch {
    // already gone, or not removable: fail open
  }
}

/**
 * Claims `<id>.pid` for this process, replacing the stale content `expected` (null if
 * the caller saw no file): rename aside, verify, exclusive create, delete the aside.
 * Throws `ClaimError` when another claimer won. `between` runs after the rename-aside,
 * for tests of the race.
 */
export function claim(
  dir: string,
  id: string,
  expected: string | null,
  between?: () => void,
): void {
  const path = pidPath(dir, id);
  let aside: string | null = null;
  if (expected !== null || existsSync(path)) {
    const target = `${path}.stale-${process.pid}-${randomBytes(4).toString("hex")}`;
    try {
      renameSync(path, target);
      aside = target;
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "ENOENT") {
        throw new ClaimError("journal already claimed");
      }
    }
  }
  between?.();
  if (aside !== null && readPid(aside) !== expected) {
    // Another claimer's live file: put it back unless a new one exists, then fail.
    if (!existsSync(path)) {
      try {
        renameSync(aside, path);
      } catch {
        // leave it
      }
    }
    throw new ClaimError("journal already claimed");
  }
  try {
    createPid(path, ownRecord(id));
  } catch {
    throw new ClaimError("journal already claimed");
  }
  if (aside !== null) removeFile(aside);
}
