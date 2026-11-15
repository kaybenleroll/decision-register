"""Offline stub interviewer for fixtures with an `items` key (no model, no spend).

`faithful` writes, for every tracked item, exactly the behaviour and final-record text its checks look for.
`lossy` omits the items in `drop` (default: a fixed list in the key's `stub_lossy_drop`, else every third item)
so the scorer must report them as lost. The stub exercises the driver (multi-compaction, schedule delivery,
post-done delivery, final request) and the scorer plumbing; it says nothing about how a real model behaves.

`log_to_transcript()` rebuilds a Claude-Code-shaped transcript from the driver log so score.py can run on a
stub run exactly as it does on a real one.
"""
import hashlib
import json

from common import norm


def _first(g):
    return g[0]


def flag_sentence(chk):
    return "Stale and needs revisiting: %s. Should we revisit this: %s?" % (" ".join(_first(g) for g in chk["topic"]),
                                                                           " ".join(_first(g) for g in chk["topic"]))


def resolved_sentence(chk):
    return "Resolved and recorded: %s." % " ".join(_first(g) for g in chk["topic"])


def final_sentences(item, lossy_drop):
    out = []
    if item["id"] in lossy_drop:
        return ["Region UK is mentioned here."] if item["id"] == "C6" else []
    for chk in item["checks"]:
        if chk["kind"] == "final_has":
            out.append("Decision %s: %s." % (item["id"], " ".join(_first(g) for g in chk["groups"])))
        elif chk["kind"] == "branch_subs":
            for sq in chk["subs"]:
                out.append("Branch value %s: %s." % (sq["id"], " ".join(_first(g) for g in sq["value"])))
    return out


class StubItemBackend:
    def __init__(self, a, key, mode="faithful", drop=None, n_questions=None):
        self.a, self.key, self.mode = a, key, mode
        self.target = n_questions or key["expected_questions"]["target"]
        if mode == "lossy":
            ids = [it["id"] for it in key["items"]]
            self.drop = set(drop or key.get("stub_lossy_drop") or ids[::3])
        else:
            self.drop = set()
        self.n_interviewer = self.n_answerer = 0
        self.asked = 0
        self.queue = []         # sub-question sentences waiting to be asked
        self.fired = set()

    def _behaviours(self, prompt):
        np_ = norm(prompt)
        out = []
        for it in self.key["items"]:
            if it["id"] in self.drop:
                continue
            for i, chk in enumerate(it["checks"]):
                tag = (it["id"], i)
                trig = chk.get("trigger") or it.get("trigger")
                if tag in self.fired:
                    continue
                if chk["kind"] == "flag" and trig and " ".join(norm(trig).split()[:8]) in np_:
                    self.fired.add(tag)
                    out.append(resolved_sentence(chk) if chk.get("cue_set") == "resolved" else flag_sentence(chk))
                if chk["kind"] == "branch_subs" and " ".join(norm(chk["open_statement"]).split()[:8]) in np_:
                    self.fired.add(tag)
                    self.queue += ["Question on %s: %s?" % (sq["id"], " ".join(_first(g) for g in sq["topic"])) for sq in chk["subs"]]
        return out

    def interviewer(self, prompt, resume, tag):
        self.n_interviewer += 1
        ok = dict(session="stub-session", usage={"input_tokens": 10, "output_tokens": 5}, cost=0.0, error=None, init=None)
        salt = " ref " + hashlib.sha256(str(self.n_interviewer).encode()).hexdigest()[:24]
        if resume is None:
            self.asked = 1
            return dict(ok, text="I read the plan. Q1: stub opening question about the scope?" + salt)
        if "-compact" in tag:
            return dict(ok, text="Compacted.")
        if "-status" in tag:
            return dict(ok, text="Still open: parked representment approver, partial settlements, regulatory extract." + salt)
        if "-final" in tag:
            lines = ["Decision record."]
            for it in self.key["items"]:
                lines += final_sentences(it, self.drop)
            return dict(ok, text="\n".join(lines))
        if "-postdone-" in tag:
            return dict(ok, text="Noted." + salt)
        beh = self._behaviours(prompt)
        if "-continue" in tag or "-gate" in tag or self.asked >= self.target:
            return dict(ok, text="Summary recorded. Ready to write the plan." + salt)
        self.asked += 1
        parts = ["Q%d: stub question %d, which setting should apply? " % (self.asked, self.asked)] + beh
        if self.queue:
            parts.append(self.queue.pop(0))
        return dict(ok, text=" ".join(parts) + salt)

    def answerer(self, prompt, tag):
        self.n_answerer += 1
        return dict(text=json.dumps({"ids": [], "reply": "stub answer"}), usage={}, cost=0.0, error=None)


def log_to_transcript(log, first_prompt):
    recs, mid = [], [0]

    def user(text, **extra):
        r = {"type": "user", "message": {"role": "user", "content": text}}
        r.update(extra)
        recs.append(r)

    def asst(text):
        mid[0] += 1
        recs.append({"type": "assistant", "message": {"id": "msg_%d" % mid[0], "role": "assistant",
                     "content": [{"type": "text", "text": text}],
                     "usage": {"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100}}})

    pending = first_prompt
    for r in log:
        k = r["kind"]
        if k == "answerer":
            pending = r["text"]
        elif k == "inject_compact":
            recs.append({"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"})
            recs.append({"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": "Summary of the interview so far."}})
        elif k.startswith("inject_"):
            user(r.get("prompt") or "(injected)")
            asst(r["text"])
        elif k == "interviewer":
            user(pending)
            asst(r["text"])
            pending = None
    return recs
