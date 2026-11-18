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
  return { kind: "question", presented };
}
