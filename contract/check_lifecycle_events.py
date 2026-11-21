"""Check that the REQ-27 lifecycle (docs/requirements.md) is covered by the journal events of contract/journal.md.

What is derived from the documents on every run (nothing is copied from them into this script):
  * the REQ-27 state rows, every transition clause of their "Entered by" and "Left by" cells, rules 1-5;
  * the 13 event names, each event's "Written when" cell and the "End kinds" table of journal.md;
  * the end-event column of the "Interview ends and server exit" table of runtime.md.
What is pinned here is the review: MAPPING says which journal events record each derived text, keyed by a
SHA-256 prefix of the whitespace-normalised text. Changing any pinned text (a REQ-27 row, clause or rule, a
"Written when" cell, an End kinds row, a runtime.md ends row), adding a clause or removing one fails the check
until the mapping is reviewed and updated: the failure prints the new text and key. Derived checks on top:
every mapped event exists, every one of the 13 events is produced by some clause or rule, the engaging calls
of REQ-27 are exactly the tools flagged engages_pending in tools.json, the state rows are the tools.json state
enum plus ended/unfinished, the End kinds table agrees with the `end` event row, tools.json and runtime.md.
Usage: python3 -I contract/check_lifecycle_events.py   (stdlib only)"""
import hashlib, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
req = open(os.path.join(ROOT, "docs/requirements.md"), encoding="utf-8").read()
jr = open(os.path.join(ROOT, "contract/journal.md"), encoding="utf-8").read()
rt = open(os.path.join(ROOT, "contract/runtime.md"), encoding="utf-8").read()
tools = json.load(open(os.path.join(ROOT, "contract/tools.json"), encoding="utf-8"))
fails = []


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def key(s):
    return hashlib.sha256(norm(s).encode("utf-8")).hexdigest()[:10]


def split_top(s, sep=";"):
    out, depth, cur = [], 0, ""
    for c in s:
        depth += c == "("
        depth -= c == ")"
        if c == sep and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += c
    out.append(cur)
    return [norm(x) for x in out if norm(x)]


# ---- reviewed mapping: key of a derived text -> journal events that record it (None: no event, with the reason)
MAPPING = {
    # REQ-27 clauses ("Entered by" and "Left by" columns), key = (state, column, text key)
    ("none", "entered", "8c31d44276"): (None, "server start: no interview, no journal"),
    ("none", "entered", "2f8d3f4b73"): (None, "after ended: the journal was archived"),
    ("none", "left", "bc8397e7fe"): (["start"], ""),
    ("pending", "entered", "c9305aac55"): (["start"], ""),
    ("pending", "entered", "fc4febd050"): (["start"], "start.prompt_count"),
    ("pending", "left", "95d8058db0"): (["engage"], ""),
    ("pending", "left", "f0c4bef382"): (["hook", "end"], "second Stop check is a hook event; every stand-down writes end"),
    ("active", "entered", "495b93a386"): (["engage"], ""),
    ("active", "entered", "8b8164cdfa"): (["item", "resolve", "park", "reopen"], "the mutating call itself"),
    ("active", "left", "72d2ebd3ec"): (["stop", "complete"], ""),
    ("active", "left", "3e4228e270"): (["end"], ""),
    ("active", "left", "a1c1463def"): (["end"], ""),
    ("active", "left", "4adb480f31"): (None, "server exit (stdin EOF or SIGINT, SIGTERM, SIGHUP) on an active interview writes no end: the absence is the record (rule 5)"),
    ("complete", "entered", "6baf436377"): (["stop", "complete"], ""),
    ("complete", "left", "c45636a237"): (["end"], ""),
    ("ended", "entered", "b8f7e2d4ed"): (None, "see the other rows"),
    ("ended", "left", "553ed0246a"): (["start"], ""),
    ("unfinished", "entered", "aa86de4e63"): (None, "a journal without end whose owner is dead"),
    ("unfinished", "left", "1805ce561d"): (["resume"], "resume; discard and pruning are file moves and deletions"),
}
# whole-row pins (cover the Stop gate and Model-facing calls columns, which hold no state change)
# Re-pinned 2026-11-21 (issue #90): the active row's 'Left by' cell now names SIGINT, SIGTERM and SIGHUP next to stdin EOF; no
# transition and no event changes (see the active/left clause below).
# Re-pinned 2026-11-17 (walk Q5): REQ-21/REQ-27 now say SUCCESSFUL mutating calls start or engage; a failing call changes
# nothing. Mapping reviewed: the same events record the same transitions (start, engage), only the trigger is narrowed.
ROW_PINS = {"none": "ecc91373f5", "pending": "cd95e3e70e", "active": "02b7a748c2", "complete": "20e802a278",
            "ended": "1b5dd6ac26", "unfinished": "70622b77fb"}
# rules 1-5 of REQ-27: events carrying what the rule needs
# Re-pinned 2026-11-21 (issue #92): rule 4 gains "when it owns the interview (REQ-15)", a skill-side ownership clause; no server transition or event changes.
RULES = {1: ("68449d82be", ["start", "hook", "resume"]),      # session ids on start, hook and resume
         2: ("dfd955191a", ["prompt", "start"]),               # prompt_count on start; prompt events
         3: ("939f8bee41", ["engage", "end", "mismatch"]),     # engagement, stand-down, mismatch before plain
         4: ("74a002ee26", ["complete", "item", "resolve", "park", "reopen", "end"]),
         5: ("612af85d55", ["end", "resume"])}        # signals added to the exit triggers (2026-11-21): same events, no new one
# "Written when" cell of every event kind (key) and the End kinds / runtime.md ends tables
WRITTEN = {"start": "35db742d85", "engage": "6d28cc8b60", "prompt": "5d519c11cb", "hook": "52f929a950", "item": "05f2987824",
           "resolve": "6cd3fd08a5", "park": "7feeba7820", "reopen": "b81686baa7", "stop": "177316c034", "complete": "667c72ed60",
           "mismatch": "43e35bf3cc", "resume": "f820c8213f", "end": "82c95031fb"}
END_KINDS = {"normal": "1b1b5a9fad", "ended_incomplete_by_model": "a0c34b692b", "abandoned": "a4f773e622",
             "ended_after_completion": "a2fc387f00", "stood_down": "c5d3bc556e"}   # re-pinned 2026-11-21: trigger "server exit" names the three signals; same end_kind and modes
RUNTIME_ENDS = {"256ebe44ed": True, "9a9670271d": True, "c34dae1ee4": True, "15313763bf": True, "593958bd63": True,
                "4f7c2dc408": False, "8b855358b0": True, "20ea22636c": False}  # True: an end event is written
# The last four rows (pending, active, complete, none) were the stdin EOF rows; since 2026-11-21 their event cell reads
# "stdin EOF, SIGINT, SIGTERM or SIGHUP" (a signal runs the EOF shutdown, issue #90): the end-event column is unchanged in
# each (stood_down; none; ended_after_completion; none), so the polarity of each row is kept.


def pin(what, text, wanted, extra=""):
    k = key(text)
    if k != wanted:
        fails.append("%s changed (key %s, pinned %s): review the event mapping, then update the pin. Text: %s%s"
                     % (what, k, wanted, norm(text)[:160], extra))


# ---- derive: events
ev_sec = jr.split("## Event kinds")[1].split("## End kinds")[0]
events = re.findall(r"^\| `(\w+)` \| (.*?) \| (.*) \|$", ev_sec, re.M)
EV = [e[0] for e in events]
if len(EV) != 13 or len(set(EV)) != 13:
    fails.append("journal.md event table lists %d kinds (%s), expected 13 distinct" % (len(EV), EV))
written = {k: w for k, _, w in events}
for k in EV:
    if k not in WRITTEN:
        fails.append("journal event %s has no pinned 'Written when' cell" % k)
    else:
        pin("journal.md 'Written when' of %s" % k, written[k], WRITTEN[k])
for k in WRITTEN:
    if k not in EV:
        fails.append("pinned event %s is no longer in journal.md" % k)

# ---- derive: REQ-27 rows, clauses, rules
tbl = req.split("| State | Entered by")[1].split("\n\n")[0]
rows = re.findall(r"^\| ([a-z]+)(?: \(journal\))? \|(.*)\|$", tbl.split("\n", 1)[1], re.M)  # every row, whatever its name
states = [s for s, _ in rows]
seen = set()
for st, rest in rows:
    cells = [norm(c) for c in rest.split("|")]
    if len(cells) != 4:
        fails.append("REQ-27 row %s has %d cells after the state, expected 4" % (st, len(cells)))
        continue
    pin("REQ-27 row %s" % st, st + "|" + rest, ROW_PINS.get(st, "-"))
    for col, idx in (("entered", 0), ("left", 3)):
        for seg in split_top(cells[idx]):
            k = (st, col, key(seg))
            seen.add(k)
            if k not in MAPPING:
                fails.append("unmapped REQ-27 transition clause (%s, %s, key %s): %s" % (st, col, k[2], seg[:160]))
for k in MAPPING:
    if k not in seen:
        fails.append("mapping for REQ-27 clause %s no longer matches any clause" % (k,))
rules = {int(n): t for n, t in re.findall(r"^  - Rule (\d),(.*)$", req, re.M)}
if set(rules) != set(RULES):
    fails.append("REQ-27 rules %s differ from the reviewed rules %s" % (sorted(rules), sorted(RULES)))
for n, t in rules.items():
    if n in RULES:
        pin("REQ-27 rule %d" % n, t, RULES[n][0])

# ---- every mapped event exists; every event is produced by something
produced = set()
for k, (evs, why) in MAPPING.items():
    if evs is None:
        if not why:
            fails.append("clause %s maps to no event without a reason" % (k,))
        continue
    produced |= set(evs)
    bad = [e for e in evs if e not in EV]
    if bad:
        fails.append("clause %s maps to unknown events %s" % (k, bad))
for n, (_, evs) in RULES.items():
    produced |= set(evs)
    bad = [e for e in evs if e not in EV]
    if bad:
        fails.append("rule %d maps to unknown events %s" % (n, bad))
if set(EV) - produced:
    fails.append("journal events produced by no REQ-27 clause or rule: %s" % sorted(set(EV) - produced))

# ---- states and engaging calls against tools.json
enum_states = set(tools["$defs"]["state"]["enum"])
if set(states) - {"ended", "unfinished"} != enum_states:
    fails.append("REQ-27 live states %s differ from tools.json state enum %s" % (sorted(set(states) - {"ended", "unfinished"}), sorted(enum_states)))
if set(tools["state_matrix"].get("status", {})) != enum_states:
    fails.append("tools.json state_matrix states differ from the state enum")
pend = next((r for s, r in rows if s == "pending"), "")
calls_cell = norm(pend.split("|")[2]) if pend else ""
m = re.search(r"first successful one of (.*?) engages it", calls_cell)
engaging = set(re.findall(r"`(\w+)`", m.group(1))) if m else set()
flagged = {n for n, t in tools["tools"].items() if t["engages_pending"]}
if not engaging or engaging != flagged:
    fails.append("engaging calls in REQ-27 (%s) differ from tools.json engages_pending (%s)" % (sorted(engaging), sorted(flagged)))

# ---- End kinds table, `end` event row, tools.json, runtime.md
ek_sec = jr.split("## End kinds (journal `end` event) and their triggers")[1].split("A pending interview holds")[0]
end_rows = {r[0]: (r[1], r[2]) for r in re.findall(r"^\| `(\w+)` \| (.*?) \| (.*) \|$", ek_sec, re.M) if r[0] != "end_kind"}
end_row = next((e for e in events if e[0] == "end"), None)
if set(end_rows) != set(END_KINDS):
    fails.append("End kinds table lists %s, reviewed %s" % (sorted(end_rows), sorted(END_KINDS)))
for kind, (trig, mode) in end_rows.items():
    if kind in END_KINDS:
        pin("journal.md End kinds row %s" % kind, trig, END_KINDS[kind])
if end_row:
    jk = re.search(r"`end_kind` \(([^)]*)\)", end_row[1])
    jk = set(re.findall(r"`(\w+)`", jk.group(1))) if jk else set()
    if jk != set(end_rows):
        fails.append("`end` event row end_kind values %s differ from the End kinds table %s" % (sorted(jk), sorted(end_rows)))
    modes = set(re.findall(r"`(\w+)`", re.search(r"`mode` \(([^)]*)\)", end_row[1]).group(1)))
    used = set(re.findall(r"`(register|plain|not_engaged)`", " ".join(m for _, m in end_rows.values())))
    if used - modes or modes - used:
        fails.append("`end` modes %s differ from the modes used in the End kinds table %s" % (sorted(modes), sorted(used)))
    tk = set(tools["tools"]["end_interview"]["result"]["properties"]["end_kind"]["enum"])
    if jk - tk != {"abandoned"} or tk - jk:
        fails.append("end_kind enums differ: tools.json %s journal.md %s (only `abandoned` may be journal-only)" % (sorted(tk), sorted(jk)))
    if "interrupted" in jk:
        fails.append("interrupted is in the journal end enum")
ends_sec = rt.split("## Interview ends and server exit")[1].split("Every row removes")[0]
for r in re.findall(r"^\| (.*) \|$", ends_sec, re.M):
    k = key(r)
    if r.startswith("Event |"):
        continue
    if k not in RUNTIME_ENDS:
        fails.append("unreviewed runtime.md ends row (key %s): %s" % (k, norm(r)[:120]))
        continue
    cell = [norm(c) for c in r.split("|")][2]
    has_end = cell != "none"
    if has_end != RUNTIME_ENDS[k]:
        fails.append("runtime.md ends row %s: end event column does not match the reviewed expectation" % k)
    for kind in re.findall(r"`(\w+)`", cell):
        if kind not in end_rows and kind not in ("plain", "not_engaged"):
            fails.append("runtime.md ends row %s names end kind %s that journal.md lacks" % (k, kind))
if len(re.findall(r"^\| ", ends_sec, re.M)) - 1 != len(RUNTIME_ENDS):
    fails.append("runtime.md ends table has a different number of rows than reviewed (%d)" % len(RUNTIME_ENDS))

for k, (evs, why) in sorted(MAPPING.items(), key=str):
    print("ok" if evs else "file-level", k[0], k[1], "->", ",".join(evs) if evs else why)
for n, (_, evs) in sorted(RULES.items()):
    print("ok rule", n, "->", ",".join(evs))
for f in fails:
    print("GAP/FAIL", f)
print("PASS" if not fails else "FAIL")
sys.exit(1 if fails else 0)
