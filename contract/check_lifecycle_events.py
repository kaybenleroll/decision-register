"""Check that every REQ-27 table transition and rule 1-5 maps to >=1 of the 13 journal event names.
Usage: python3 -I contract/check_lifecycle_events.py   (stdlib only)"""
import os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
req = open(os.path.join(ROOT, "docs/requirements.md")).read()
jr = open(os.path.join(ROOT, "contract/journal.md")).read()
EV = ["start","engage","prompt","hook","item","resolve","park","reopen","stop","complete","mismatch","resume","end"]
doc_ev = re.findall(r"^\| `(\w+)` \|", jr.split("## Event kinds")[1].split("## End kinds")[0], re.M)
fails = []
if doc_ev != EV: fails.append("journal.md event names %s != frozen 13 %s" % (doc_ev, EV))
# transitions: (state row in REQ-27 table, transition) -> events. None means file-level, no replayable event needed (reason given).
T = {
 ("none","hook start into pending"): ["start"],
 ("none","mutating call starts into active (implicit)"): ["start","item"],
 ("none","begin_interview register starts into active"): ["start"],
 ("none","hooks record last_seen_session_id / prompt_seen (no interview, no journal)"): None,
 ("pending","engagement into active"): ["engage"],
 ("pending","second Stop check, no call, into ended stood down"): ["stop","end"],
 ("pending","end_interview mode plain into ended stood down"): ["end"],
 ("pending","session change into ended stood down"): ["end"],
 ("pending","successful resume replaces the pending interview, ended stood down"): ["end","resume"],
 ("pending","server exit (stdin EOF) while pending, ended stood down"): ["end"],
 ("active","allowed finish attempt, nothing open, into complete"): ["stop","complete"],
 ("active","end_interview into ended (normal / incomplete by model)"): ["end"],
 ("active","session change into ended (abandoned / normal)"): ["end"],
 ("active","server exit keeps journal unfinished, log interrupted"): None,  # absence of `end` IS the record (REQ-27 rule 5); log-only kind
 ("complete","mutating call returns to active"): ["item","resolve","park","reopen"],
 ("complete","end_interview / session change / server exit into ended after completion"): ["end"],
 ("ended","a new start"): ["start"],
 ("unfinished","resume into active"): ["resume"],
 ("unfinished","discard archives journal"): None,  # file move to archive/; nothing to replay
 ("unfinished","pruned at 30 days"): None,  # file deletion
}
R = {
 1: ["start","hook","resume"],      # session ids on start.session_id, hook.session_id, resume.session_id
 2: ["prompt","start"],             # prompt_count on start; prompt events
 3: ["engage","end","mismatch"],    # engagement, stand-down, schema mismatch before plain
 4: ["complete","item","resolve","park","reopen","end"],
 5: ["end","resume"],               # exit leaves no end; resume event
}
# every state row and rule in the requirement must be covered by the maps
rows = set(re.findall(r"^\| (none|pending|active|complete|ended|unfinished)(?: \(journal\))? \|", req, re.M))
if rows != {s for s,_ in T}: fails.append("state rows in requirements %s vs checked %s" % (sorted(rows), sorted({s for s,_ in T})))
rules = set(int(n) for n in re.findall(r"^  - Rule (\d),", req, re.M))
if rules != set(R): fails.append("rules in requirements %s vs checked %s" % (sorted(rules), sorted(R)))
for k, evs in list(T.items()) + [(("rule", n), e) for n, e in R.items()]:
    if evs is None: print("file-level (no event needed):", k); continue
    bad = [e for e in evs if e not in EV]
    if bad or not evs: fails.append("%s maps to unknown or no events %s" % (k, bad))
    else: print("ok", k, "->", ",".join(evs))
# end kinds in journal.md and tools.json must agree, and exclude interrupted
import json
d = json.load(open(os.path.join(ROOT, "contract/tools.json")))
tk = d["tools"]["end_interview"]["result"]["properties"]["end_kind"]["enum"]
jk = re.search(r"\| `end` \| `end_kind` \(([^)]*)\)", jr).group(1)
jk = re.findall(r"`(\w+)`", jk)
# the tool result never returns `abandoned` (session change only); every other journal kind must be returnable
if set(jk) - set(tk) != {"abandoned"} or set(tk) - set(jk): fails.append("end_kind enums differ: tools.json %s journal.md %s (only `abandoned` may be journal-only)" % (tk, jk))
if "interrupted" in jk: fails.append("interrupted is in the journal end enum")
for f in fails: print("GAP/FAIL", f)
print("PASS" if not fails else "FAIL")
sys.exit(1 if fails else 0)
