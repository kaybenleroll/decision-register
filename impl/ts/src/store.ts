// The project folder as a store of journals: finding the unfinished ones and archiving.
// An unfinished journal has no `end` event and no live owner (journal.md, runtime.md).

import {
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  statSync,
} from "node:fs";
import { join } from "node:path";

import {
  holdsClaim,
  ownerAlive,
  pidPath,
  readPid,
  removeFile,
} from "./pidfile.ts";

export interface JournalInfo {
  id: string;
  /** The `seq` of the last valid event (a v1 line counts as its line number). */
  lastEvent: number;
  /** Modification time, milliseconds. */
  mtimeMs: number;
  /** Whether a live process owns it (pid file, pid exists, same start time). */
  alive: boolean;
}

/** Tolerant scan of a journal's text: whether it has an `end` event and its last `seq`. */
export function scanText(text: string): { ended: boolean; lastEvent: number } {
  let ended = false;
  let lastEvent = 0;
  for (const [i, line] of text.split("\n").entries()) {
    try {
      const e: unknown = JSON.parse(line);
      if (typeof e !== "object" || e === null) continue;
      const o = e as Record<string, unknown>;
      if (o["kind"] === "end") ended = true;
      lastEvent = typeof o["seq"] === "number" ? o["seq"] : i + 1;
    } catch {
      // blank, torn or invalid: skipped here; replay reports it
    }
  }
  return { ended, lastEvent };
}

/**
 * Every journal in `dir` (not the archive) without an `end` event, except `ownId`, which
 * is this server's own interview. Most recently modified first, ties by id ascending.
 */
export function scanUnfinished(
  dir: string,
  ownId: string | null,
): JournalInfo[] {
  let names: string[];
  try {
    names = readdirSync(dir);
  } catch {
    return [];
  }
  const found: JournalInfo[] = [];
  for (const name of names) {
    if (!name.endsWith(".jsonl")) continue;
    const id = name.slice(0, -".jsonl".length);
    if (id === ownId) continue;
    try {
      const file = join(dir, name);
      const { ended, lastEvent } = scanText(readFileSync(file, "utf8"));
      if (ended) continue;
      const alive = ownerAlive(readPid(pidPath(dir, id)), holdsClaim(id));
      found.push({ id, lastEvent, mtimeMs: statSync(file).mtimeMs, alive });
    } catch {
      // unreadable: not offered
    }
  }
  return found.sort((a, b) =>
    a.mtimeMs !== b.mtimeMs ? b.mtimeMs - a.mtimeMs : a.id < b.id ? -1 : 1,
  );
}

/** Moves `<id>.jsonl` to `archive/` and removes its pid file; throws on a failed move. */
export function archiveJournal(dir: string, id: string): void {
  const archive = join(dir, "archive");
  mkdirSync(archive, { recursive: true, mode: 0o700 });
  renameSync(join(dir, `${id}.jsonl`), join(archive, `${id}.jsonl`));
  removeFile(pidPath(dir, id));
}

export const PRUNE_AGE_MS = 30 * 24 * 3600 * 1000;
export const STALE_AGE_MS = 3600 * 1000;

/** The server clock in ms: the real clock plus `REGISTER_CLOCK_OFFSET_SECONDS` (test-only). */
export function clockMs(): number {
  const n = Number.parseInt(
    process.env["REGISTER_CLOCK_OFFSET_SECONDS"] ?? "",
    10,
  );
  return Date.now() + (Number.isFinite(n) ? n * 1000 : 0);
}

function ageMs(file: string, nowMs: number): number | null {
  try {
    return nowMs - statSync(file).mtimeMs;
  } catch {
    return null;
  }
}

/**
 * Start-up pruning (REQ-61): archives and unfinished journals older than 30 days by
 * modification time; `<id>.pid` and `<id>.pid.stale-*` older than 30 days whose journal is
 * gone; any `.stale-*` older than one hour. Fails open: errors are ignored.
 */
export function prune(dir: string, nowMs: number): void {
  const remove = (file: string, limit: number): void => {
    const age = ageMs(file, nowMs);
    if (age !== null && age > limit) removeFile(file);
  };
  const list = (d: string): string[] => {
    try {
      return readdirSync(d);
    } catch {
      return [];
    }
  };
  for (const name of list(join(dir, "archive"))) {
    if (name.endsWith(".jsonl")) {
      remove(join(dir, "archive", name), PRUNE_AGE_MS);
    }
  }
  const names = list(dir);
  const journals = new Set(names.filter((n) => n.endsWith(".jsonl")));
  for (const name of names) {
    const file = join(dir, name);
    if (name.endsWith(".jsonl")) {
      remove(file, PRUNE_AGE_MS);
      continue;
    }
    const m = /^(.*)\.pid(\.stale-.*)?$/.exec(name);
    if (m === null) continue;
    if (m[2] !== undefined) remove(file, STALE_AGE_MS);
    if (!journals.has(`${m[1]}.jsonl`)) remove(file, PRUNE_AGE_MS);
  }
}
