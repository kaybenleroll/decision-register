import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  utimesSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import { prune } from "../src/store.ts";

const base = join(process.cwd(), ".scratch");
mkdirSync(base, { recursive: true });
const DAY = 24 * 3600;

function seed(file: string, ageS: number): string {
  writeFileSync(file, "x\n");
  const t = Date.now() / 1000 - ageS;
  utimesSync(file, t, t);
  return file;
}

test("prune removes old archives, journals, orphans and old stale files only", () => {
  const dir = mkdtempSync(join(base, "prune-"));
  mkdirSync(join(dir, "archive"));
  const oldArc = seed(join(dir, "archive", "a.jsonl"), 31 * DAY);
  const newArc = seed(join(dir, "archive", "b.jsonl"), 29 * DAY);
  const oldJ = seed(join(dir, "c.jsonl"), 31 * DAY);
  const newJ = seed(join(dir, "d.jsonl"), 5 * DAY);
  const orphan = seed(join(dir, "e.pid"), 31 * DAY);
  const young = seed(join(dir, "f.pid"), 29 * DAY);
  const oldStale = seed(join(dir, "f.pid.stale-1-aa"), 7200);
  const newStale = seed(join(dir, "f.pid.stale-2-aa"), 600);
  const keptPid = seed(join(dir, "d.pid"), 31 * DAY);
  prune(dir, Date.now());
  for (const f of [oldArc, oldJ, orphan, oldStale]) {
    assert.ok(!existsSync(f), f);
  }
  for (const f of [newArc, newJ, young, newStale, keptPid]) {
    assert.ok(existsSync(f), f);
  }
});

test("prune on a missing directory does nothing", () => {
  prune(join(base, "no-such-dir"), Date.now());
});
