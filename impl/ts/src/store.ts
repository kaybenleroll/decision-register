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
