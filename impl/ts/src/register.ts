// Data model, transition table and Stale propagation (REQ-30, 31, 36, 38).
//
// A Resolved or Stale item carries its Answer inside the status variant, and an Answer
// can only be built through makeAnswer (the brand symbol is not exported), so a Resolved
// item without an answer, source or one-line rationale cannot be written without a cast
// (REQ-31). TypeScript enforces this at compile time only.

export type Actor = "user" | "model";
export type Kind = "question" | "subquestion" | "decision";

export const parseActor = (s: string): Actor | undefined =>
  s === "user" || s === "model" ? s : undefined;

export const parseKind = (s: string): Kind | undefined =>
  s === "question" || s === "subquestion" || s === "decision" ? s : undefined;

export const isNonblank = (s: string): boolean => /[^\p{White_Space}]/u.test(s);

/** A single line with at least one non-whitespace character (contract `oneline`). */
export const isOneline = (s: string): boolean =>
  isNonblank(s) && !/[\r\n\v\f\x85\u2028\u2029]/.test(s);

/** Ids match `^[A-Za-z0-9_.-]{1,64}$`. */
export const isValidId = (s: string): boolean => /^[A-Za-z0-9_.-]{1,64}$/.test(s);

declare const answerBrand: unique symbol;

/** An answer with its source and one-line rationale; only makeAnswer produces one. */
export interface Answer {
  readonly [answerBrand]: true;
  readonly text: string;
  readonly source: Actor;
  readonly rationale: string;
}

export type AnswerResult =
  | { readonly ok: true; readonly answer: Answer }
  | { readonly ok: false; readonly param: "answer" | "rationale"; readonly why: string };

/** The one validating factory for Answer. */
export function makeAnswer(text: string, source: Actor, rationale: string): AnswerResult {
  if (!isNonblank(text)) return { ok: false, param: "answer", why: "must not be blank" };
  if (!isOneline(rationale)) {
    return { ok: false, param: "rationale", why: "must be a single non-blank line" };
  }
  const answer = Object.freeze({ text, source, rationale }) as Answer;
  return { ok: true, answer };
}

export type Status =
  | { readonly kind: "Open" }
  | { readonly kind: "Resolved"; readonly answer: Answer }
  | { readonly kind: "Parked"; readonly by: Actor; readonly reason: string }
  /** Was Resolved; a dependency was reopened. Keeps its old answer. */
  | { readonly kind: "Stale"; readonly answer: Answer };

export type StatusName = Status["kind"];

const OPEN: Status = Object.freeze({ kind: "Open" });

/** Position in the `status` tree order: Open, Stale, Parked, Resolved. */
const RANK: Record<StatusName, number> = { Open: 0, Stale: 1, Parked: 2, Resolved: 3 };

export interface Item {
  readonly id: string;
  readonly kind: Kind;
  readonly text: string;
  readonly parent: string | null;
  readonly dependsOn: readonly string[];
  status: Status;
}

export interface NewItem {
  id: string | null;
  kind: Kind;
  text: string;
  parent: string | null;
  dependsOn: string[];
  answer: Answer | null;
}

export class RegisterError extends Error {}

export interface Counts {
  open: number;
  resolved: number;
  parked: number;
  stale: number;
}

export class Register {
  private readonly items: Item[] = [];
  private nextAuto = 0;

  get length(): number {
    return this.items.length;
  }

  get(id: string): Item | undefined {
    return this.items.find((i) => i.id === id);
  }

  private freshId(): string {
    for (;;) {
      this.nextAuto += 1;
      const id = `i${this.nextAuto}`;
      if (this.get(id) === undefined) return id;
    }
  }

  /** Logs an item; returns its id and the status name it starts in. */
  log(n: NewItem): { id: string; status: StatusName } {
    if (n.id !== null && this.get(n.id) !== undefined) {
      throw new RegisterError(`duplicate id: ${n.id}`);
    }
    if (n.kind === "subquestion" && n.parent === null) {
      throw new RegisterError("subquestion needs a parent");
    }
    for (const dep of [...(n.parent === null ? [] : [n.parent]), ...n.dependsOn]) {
      if (this.get(dep) === undefined) throw new RegisterError(`missing item: ${dep}`);
    }
    const id = n.id ?? this.freshId();
    const status: Status =
      n.answer === null ? OPEN : { kind: "Resolved", answer: n.answer };
    this.items.push({
      id,
      kind: n.kind,
      text: n.text,
      parent: n.parent,
      dependsOn: [...n.dependsOn],
      status,
    });
    return { id, status: status.kind };
  }

  private lookup(id: string): Item {
    const item = this.get(id);
    if (item === undefined) throw new RegisterError(`no such item: ${id}`);
    return item;
  }

  private static reject(action: string, item: Item): never {
    throw new RegisterError(`cannot ${action} ${item.id}: item is ${item.status.kind}`);
  }

  /** Resolve accepts Open, Stale and Parked (either parker); rejects Resolved. */
  resolve(id: string, answer: Answer): void {
    const item = this.lookup(id);
    if (item.status.kind === "Resolved") Register.reject("resolve", item);
    item.status = { kind: "Resolved", answer };
  }

  /** Park accepts Open, Stale and model-Parked; rejects Resolved and user-Parked. */
  park(id: string, by: Actor, reason: string): void {
    const item = this.lookup(id);
    const s = item.status;
    const allowed =
      s.kind === "Open" || s.kind === "Stale" || (s.kind === "Parked" && s.by === "model");
    if (!allowed) Register.reject("park", item);
    item.status = { kind: "Parked", by, reason };
  }

  /**
   * Reopen accepts Resolved only. The item becomes Open and every Resolved item that
   * depends on it, directly or through any intermediate (whatever its status), becomes
   * Stale. Parent links never propagate. Returns the stale ids in creation order.
   */
  reopen(id: string): string[] {
    const item = this.lookup(id);
    if (item.status.kind !== "Resolved") Register.reject("reopen", item);
    item.status = OPEN;
    const seen = new Set<Item>([item]);
    const queue: Item[] = [item];
    const stale = new Set<Item>();
    for (let cur = queue.pop(); cur !== undefined; cur = queue.pop()) {
      for (const j of this.items) {
        if (seen.has(j) || !j.dependsOn.includes(cur.id)) continue;
        seen.add(j);
        queue.push(j);
        if (j.status.kind === "Resolved") {
          j.status = { kind: "Stale", answer: j.status.answer };
          stale.add(j);
        }
      }
    }
    return this.items.filter((i) => stale.has(i)).map((i) => i.id);
  }

  /** Items in `status` order: Open, Stale, Parked, Resolved; creation order within. */
  ordered(): Item[] {
    return [...this.items].sort((a, b) => RANK[a.status.kind] - RANK[b.status.kind]);
  }

  counts(): Counts {
    const c: Counts = { open: 0, resolved: 0, parked: 0, stale: 0 };
    for (const i of this.items) {
      const k = i.status.kind;
      if (k === "Open") c.open += 1;
      else if (k === "Resolved") c.resolved += 1;
      else if (k === "Parked") c.parked += 1;
      else c.stale += 1;
    }
    return c;
  }
}
