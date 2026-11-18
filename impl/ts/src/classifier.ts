// Finish-attempt classifier (contract/classifier.md, normative).

export type ItemView = {
  readonly id: string;
  readonly text: string;
  readonly status: "Open" | "Resolved" | "Parked" | "Stale";
};

export type Classification =
  | { readonly kind: "empty" }
  | { readonly kind: "finish" }
  | { readonly kind: "question"; readonly presented: string[] }
  | { readonly kind: "unknown" };

const STOP_WORDS: ReadonlySet<string> = new Set(
  `about all also an and any are as at be been but by can could did do does done each for from get
got had has have he her how if in into is it just let may me might more most must my no not of
on or other our out over shall she should so some such than that the their them then there they
this to too up us very was we were what when where which who whom why will with would you your`.split(
    /\s+/,
  ),
);

const WRAP = "[*`]";

/** Fenced code, then inline code (single-backtick spans), then URLs. */
function stripFencesAndUrls(u: string): string {
  return u.replace(/```[\s\S]*?```/g, "").replace(/https?:\/\/\S+/g, "");
}

function stripForQuestionMark(u: string): string {
  const noInline = u
    .replace(/```[\s\S]*?```/g, "")
    .replace(/`[^`\n]*`/g, "")
    .replace(/https?:\/\/\S+/g, "");
  return noInline
    .split("\n")
    .filter((l) => !/^\s*>/.test(l))
    .join("\n");
}

const midLabel = (digits: string): RegExp =>
  new RegExp(`(?<![A-Za-z0-9_])${WRAP}{0,2}[Qq]${digits}(?::|${WRAP}{1,2}:)`);

const startLabel = (digits: string): RegExp =>
  new RegExp(
    `^[ \\t]*(?:(?:[-*+]|\\d+[.)])[ \\t]+)?${WRAP}{0,2}[Qq]${digits}(?:[:.)]${WRAP}{0,2}|${WRAP}{1,2}[:.)])`,
  );

const labelDigits = (id: string): string | null => {
  const m = /^Q(\d+)$/.exec(id);
  return m === null ? null : (m[1] as string);
};

function contentWords(s: string): Set<string> {
  const lower = s.replace(/[A-Z]/g, (c) => c.toLowerCase());
  const out = new Set<string>();
  for (const t of lower.match(/[a-z0-9]+/g) ?? []) {
    if (t.length > 1 && !STOP_WORDS.has(t)) out.add(t);
  }
  return out;
}

/** Section 5 tokeniser: lower-case maximal runs of `[a-z0-9]`. */
const tokensOf = (s: string): string[] =>
  s.replace(/[A-Z]/g, (c) => c.toLowerCase()).match(/[a-z0-9]+/g) ?? [];

const GUARD_WORDS: ReadonlySet<string> = new Set([
  "except",
  "but",
  "apart",
  "besides",
  "remaining",
  "still",
  "only",
  "though",
  "however",
]);

const OPEN_NOUNS: ReadonlySet<string> = new Set([
  "items",
  "threads",
  "branches",
  "questions",
  "decisions",
]);

/** Closure sentence (contract section 4(c)): no guard word, and a closing phrase. */
export function closureSentence(sentence: string): boolean {
  const t = tokensOf(sentence);
  if (t.some((w) => GUARD_WORDS.has(w))) return false;
  for (let i = 0; i < t.length; i++) {
    const w = t[i] as string;
    if (
      (w === "all" || w === "everything") &&
      t.slice(i + 1, i + 5).includes("resolved")
    ) {
      return true;
    }
    if (
      (w === "nothing" || w === "none") &&
      t.slice(i + 1, i + 3).includes("unresolved")
    ) {
      return true;
    }
    if (w === "no" && t[i + 1] === "unresolved") return true;
    if (
      w === "no" &&
      t[i + 1] === "open" &&
      OPEN_NOUNS.has(t[i + 2] as string)
    ) {
      return true;
    }
  }
  return false;
}

const OPENERS: readonly (readonly string[])[] = [
  ["shall", "i"],
  ["should", "i"],
  ["want", "me", "to"],
  ["ready", "to"],
  ["do", "you", "want", "me", "to"],
  ["would", "you", "like", "me", "to"],
];

/**
 * Closing replay, tail rules (contract section 4(a) and (b)) on the stripped message `S`:
 * exactly one `?`, only whitespace or `*_)"'` after it, and a final sentence that starts
 * with a closed offer. Returns the text before the final sentence, or null.
 */
export function closingReplayPrefix(stripped: string): string | null {
  const q = stripped.indexOf("?");
  if (q === -1 || q !== stripped.lastIndexOf("?")) return null;
  if (!/^[\s*_)"']*$/.test(stripped.slice(q + 1))) return null;
  // Final sentence: after the last newline, or after the last `.`, `!` or `:` that is
  // followed by a space, whichever is later.
  let start = 0;
  const nl = stripped.lastIndexOf("\n", q);
  if (nl !== -1) start = nl + 1;
  for (const p of [". ", "! ", ": "]) {
    const k = stripped.lastIndexOf(p, q);
    if (k !== -1 && k + 2 > start) start = k + 2;
  }
  const ft = tokensOf(stripped.slice(start, q + 1));
  if (!OPENERS.some((o) => o.every((w, i) => ft[i] === w))) return null;
  return stripped.slice(0, start);
}

/** Closing replay (section 4): tail rules plus a closure sentence before the final sentence. */
export function isClosingReplay(stripped: string): boolean {
  const prefix = closingReplayPrefix(stripped);
  if (prefix === null) return false;
  return prefix
    .split(/\n|(?<=[.!:])\s+/)
    .some((piece) => closureSentence(piece));
}

const unresolved = (i: ItemView): boolean =>
  i.status === "Open" || i.status === "Stale";

/** Classifies `message` against the register; `items` are in creation order. */
export function classify(
  message: string,
  items: readonly ItemView[],
): Classification {
  if (message.trim() === "") return { kind: "empty" };
  const u = message;
  const asks = stripForQuestionMark(u).includes("?");
  const live = items.filter(unresolved);

  const cited = (i: ItemView): boolean => {
    const d = labelDigits(i.id);
    if (d === null) return false;
    return (
      midLabel(d).test(u) || u.split("\n").some((l) => startLabel(d).test(l))
    );
  };

  // A single labelled request: one non-blank line (fences and URLs removed) that starts
  // with the label of an Open or Stale item.
  const lines = stripFencesAndUrls(u)
    .split("\n")
    .filter((l) => l.trim() !== "");
  if (!asks && lines.length === 1) {
    for (const i of live) {
      const d = labelDigits(i.id);
      if (d !== null && startLabel(d).test(lines[0] as string)) {
        return { kind: "question", presented: [i.id] };
      }
    }
  }
  if (!asks) return { kind: "finish" };

  const words = contentWords(u);
  const presented = live
    .filter((i) => {
      if (cited(i)) return true;
      let shared = 0;
      for (const w of contentWords(i.text)) if (words.has(w)) shared += 1;
      return shared >= 3;
    })
    .map((i) => i.id);
  // Closing replay (section 4): a message that concerns no Open/Stale item and offers a
  // closed next step after a closure sentence is a finish attempt, not a question turn.
  if (presented.length === 0 && isClosingReplay(stripForQuestionMark(u))) {
    return { kind: "finish" };
  }
  return { kind: "question", presented };
}
