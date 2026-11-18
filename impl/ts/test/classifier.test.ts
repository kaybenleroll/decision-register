import assert from "node:assert/strict";
import { test } from "node:test";

import {
  classify,
  closureSentence,
  type ItemView,
  isClosingReplay,
} from "../src/classifier.ts";

const items: ItemView[] = [
  { id: "Q1", text: "storage engine choice", status: "Open" },
  { id: "Q2", text: "retention period policy", status: "Resolved" },
];

test("empty and whitespace messages are empty", () => {
  assert.deepEqual(classify("  \n", items), { kind: "empty" });
});

test("no question mark is a finish attempt", () => {
  assert.deepEqual(classify("all done", items), { kind: "finish" });
});

test("a question mark inside code or a quote does not ask", () => {
  assert.deepEqual(classify("run `a?b` now\n> why?", items), {
    kind: "finish",
  });
});

test("a label cites an open item; resolved items are not concerned", () => {
  assert.deepEqual(classify("Q1: which one?", items), {
    kind: "question",
    presented: ["Q1"],
  });
  assert.deepEqual(classify("Q2: which one?", items), {
    kind: "question",
    presented: [],
  });
  assert.deepEqual(classify("Q01: which one?", items), {
    kind: "question",
    presented: [],
  });
});

test("a single labelled request without a question mark is a question turn", () => {
  assert.deepEqual(classify("**Q1**. pick one", items), {
    kind: "question",
    presented: ["Q1"],
  });
});

test("three shared content words concern an item", () => {
  assert.deepEqual(classify("Is the storage engine choice settled?", items), {
    kind: "question",
    presented: ["Q1"],
  });
});

// --- closing-replay rule e5 (contract section 4) ---

const CLOSURE = "Everything is resolved.";
const OFFER = "Shall I wrap up?";

test("closure sentence: each accepted phrase", () => {
  for (const s of [
    "All of these are resolved",
    "everything we raised is resolved",
    "Nothing is unresolved",
    "none left unresolved",
    "There is no unresolved item",
    "We have no open items",
    "no open threads",
    "no open branches",
    "no open questions",
    "no open decisions",
  ]) {
    assert.equal(closureSentence(s), true, s);
  }
});

test("closure sentence: distance limits and missing phrases", () => {
  assert.equal(closureSentence("all one two three four resolved"), false);
  assert.equal(closureSentence("everything one two three resolved"), true);
  assert.equal(closureSentence("all one two three resolved"), true);
  assert.equal(closureSentence("nothing a b unresolved"), false);
  assert.equal(closureSentence("nothing a unresolved"), true);
  assert.equal(closureSentence("no open files"), false);
  assert.equal(closureSentence("resolved all"), false);
  assert.equal(closureSentence("we are done"), false);
});

test("closure sentence: every guard word vetoes", () => {
  for (const g of [
    "except",
    "but",
    "apart",
    "besides",
    "remaining",
    "still",
    "only",
    "though",
    "however",
  ]) {
    assert.equal(closureSentence(`Everything is resolved ${g} one`), false, g);
  }
});

test("closing replay: accepted offer openers", () => {
  for (const o of [
    "Shall I wrap up?",
    "Should I wrap up?",
    "Want me to wrap up?",
    "Ready to wrap up?",
    "Do you want me to wrap up?",
    "Would you like me to wrap up?",
  ]) {
    assert.equal(isClosingReplay(`${CLOSURE} ${o}`), true, o);
  }
});

test("closing replay: other openers are rejected", () => {
  for (const o of [
    "Can I wrap up?",
    "Do you want a wrap up?",
    "Is that all right?",
  ]) {
    assert.equal(isClosingReplay(`${CLOSURE} ${o}`), false, o);
  }
});

test("closing replay: exactly one question mark, only trailing characters after it", () => {
  assert.equal(isClosingReplay(`${CLOSURE} ${OFFER}`), true);
  assert.equal(isClosingReplay(`${CLOSURE} ${OFFER}  \n`), true);
  assert.equal(isClosingReplay(`${CLOSURE} **${OFFER}**`), true);
  assert.equal(isClosingReplay(`${CLOSURE} Shall I wrap up?**`), true);
  assert.equal(isClosingReplay(`${CLOSURE} Shall I wrap up?_)"'`), true);
  assert.equal(isClosingReplay(`${CLOSURE} Shall I wrap up?!`), false);
  assert.equal(isClosingReplay(`${CLOSURE} Shall I wrap up? Yes.`), false);
  assert.equal(isClosingReplay(`Done? ${CLOSURE} ${OFFER}`), false);
  assert.equal(isClosingReplay(`${CLOSURE} Shall I wrap up??`), false);
  assert.equal(isClosingReplay(`${CLOSURE} Shall I wrap up`), false);
});

test("closing replay: final sentence boundaries", () => {
  assert.equal(
    isClosingReplay("Everything is resolved.\nShall I wrap up?"),
    true,
  );
  assert.equal(
    isClosingReplay("Everything is resolved! Shall I wrap up?"),
    true,
  );
  assert.equal(
    isClosingReplay("Everything is resolved: shall I wrap up?"),
    true,
  );
  // A full stop with no following space is not a boundary: the final sentence then
  // starts at the closure text, so it does not open with an offer.
  assert.equal(
    isClosingReplay("Everything is resolved.Shall I wrap up?"),
    false,
  );
  // The later of the newline and the sentence break wins.
  assert.equal(
    isClosingReplay("Nothing is unresolved. Ok.\nShall I stop?"),
    true,
  );
  assert.equal(
    isClosingReplay("Intro\nNothing is unresolved. Shall I stop?"),
    true,
  );
});

test("closing replay: needs a closure sentence before the final sentence", () => {
  assert.equal(isClosingReplay("Shall I wrap up?"), false);
  assert.equal(
    isClosingReplay("We have made progress. Shall I wrap up?"),
    false,
  );
  assert.equal(
    isClosingReplay("Everything is resolved except one. Shall I wrap up?"),
    false,
  );
  // The closure sentence is judged alone: a guard word elsewhere does not veto it.
  assert.equal(
    isClosingReplay(
      "Nothing is unresolved. We are still keen. Shall I wrap up?",
    ),
    true,
  );
  // The closure phrase inside the final sentence does not count.
  assert.equal(
    isClosingReplay("Shall I confirm everything is resolved?"),
    false,
  );
});

test("classify: closing replay is a finish with nothing presented", () => {
  assert.deepEqual(classify(`${CLOSURE} ${OFFER}`, items), { kind: "finish" });
});

test("classify: a closing replay that concerns an Open item stays a question", () => {
  assert.deepEqual(classify(`Q1: everything is resolved. ${OFFER}`, items), {
    kind: "question",
    presented: ["Q1"],
  });
  assert.deepEqual(
    classify(
      `The storage engine choice is resolved. Everything is resolved. ${OFFER}`,
      items,
    ),
    { kind: "question", presented: ["Q1"] },
  );
});

test("classify: a Resolved item's overlap does not block a closing replay", () => {
  assert.deepEqual(
    classify(`The retention period policy is set. ${CLOSURE} ${OFFER}`, items),
    { kind: "finish" },
  );
});

test("classify: a single labelled request is exempt from the rule", () => {
  assert.deepEqual(classify("Q1: all resolved, should I stop?", items), {
    kind: "question",
    presented: ["Q1"],
  });
});

test("classify: a failed clause leaves a question turn", () => {
  assert.deepEqual(classify("Everything is resolved. Is it ok?", items), {
    kind: "question",
    presented: [],
  });
});

test("e6 closure sentence: completion verb followed by last or final", () => {
  for (const s of [
    "The import completed the final step",
    "this finishes the last review",
    "That closes the last item",
    "It concluded final checks",
    "Work finished last night",
  ]) {
    assert.equal(closureSentence(s), true, s);
  }
  assert.equal(closureSentence("this closes the review"), false);
  assert.equal(closureSentence("completed one two last"), false);
  assert.equal(closureSentence("last step completed"), false);
  assert.equal(
    closureSentence("This completes the final step but not more"),
    false,
  );
});

test("e6 closing replay: want to and would you like to openers", () => {
  for (const o of [
    "Want to move on?",
    "Do you want to continue?",
    "Would you like to proceed?",
  ]) {
    assert.equal(
      isClosingReplay(`The import completed the final step. ${o}`),
      true,
      o,
    );
  }
  assert.equal(
    isClosingReplay("The import completed the final step. Do you want it?"),
    false,
  );
  assert.equal(
    isClosingReplay("The import completed a step. Want to move on?"),
    false,
  );
  assert.equal(
    isClosingReplay("Everything is resolved. Do we want to move on?"),
    false,
  );
});

test("e6 classify: a completion report offering follow-on work finishes", () => {
  assert.deepEqual(
    classify(
      "This finishes the last review.\nWould you like to continue?",
      items,
    ),
    { kind: "finish" },
  );
});
