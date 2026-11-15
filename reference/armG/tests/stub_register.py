"""Offline stub interviewer for arms G and G-lite (no model, no spend).

Subclasses the key-aware item stub (stub_items.StubItemBackend: faithful / lossy final records) and, on top of it, drives
the REAL mechanism the way the live arm would: the UserPromptSubmit, SessionStart[compact] and Stop hooks are run as
subprocesses with synthetic payloads, and (arm G) the register tools are called on the real stdio MCP server over a pipe
(one server process per call, like the per-call restarts under `claude -p --resume`). For G-lite the stub edits
.scratch/ledger.md directly. It logs questions with server-assigned ids, resolves them on the next turn, opens one branch
and reopens one decision (so stale marking runs), obeys Stop-hook blocks, and writes decisions that carry every anchor
phrase of the key into the register (so the scorer-isolation check, M8, has something to be contaminated by).

`transcript()` rebuilds a Claude-Code-shaped transcript from the driver log AND the stub's trace (tool_use / tool_result
pairs and hook-context attachments) so score.py runs on it exactly as on a real run. Says nothing about a real model.
"""
import json
import os
import re
import subprocess
import sys

import stub_items

HERE = os.path.dirname(os.path.abspath(__file__))
READBACK = os.path.join(HERE, "readback")
sys.path.insert(0, READBACK)
import register_store as store  # noqa: E402

ANCHOR_CAP = 380       # decision length cap in the register is 400


class StubRegisterBackend(stub_items.StubItemBackend):
    def __init__(self, a, key, mode, drop, run_dir, dirs):
        super().__init__(a, key, mode, drop)
        self.run_dir, self.dirs = run_dir, dirs
        self.lite = a.arm == "G-lite"
        self.work = dirs["work"]
        self.ledger = os.path.join(self.work, ".scratch", "ledger.md")
        self.calls = []             # one dict per interviewer-side claude call
        self.pending = None         # id asked and awaiting an answer
        self.todo = []              # ids to ask next (branch sub-questions, stale entries)
        self.blocks = []            # (tag, kind, reason)
        self.branch_done = self.reopen_done = False
        self.decision_text = self._anchor_text()
        self.ledger_n = 0

    # ------------------------------------------------------------ plumbing
    def _anchor_text(self):
        """One decision string carrying the first anchor phrase of every final_has / branch_subs check (capped)."""
        parts = []
        for it in self.key.get("items", []):
            parts += stub_items.final_sentences(it, set())
        return (" ".join(parts))[:ANCHOR_CAP] or "stub decision"

    def _env(self, tag):
        env = dict(os.environ, SEQ_STATE_DIR=self.dirs["state"])
        env.pop("SEQ_GATE_SKIP", None)
        if store.gate_skip_for_tag(tag):
            env["SEQ_GATE_SKIP"] = "1"
        return env

    def _hook(self, script, payload, tag):
        args = [sys.executable, os.path.join(READBACK, script)] + (["--ledger"] if self.lite else [])
        p = subprocess.run(args, input=json.dumps(dict(payload, cwd=self.work)), capture_output=True, text=True, env=self._env(tag))
        return p.stdout.strip()

    def _mcp(self, name, args):
        """One tools/call on a fresh register_server.py process over stdio. Returns (is_error, text)."""
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}}]
        p = subprocess.run([sys.executable, os.path.join(READBACK, "register_server.py")],
                           input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True,
                           env=dict(os.environ, SEQ_STATE_DIR=self.dirs["state"]))
        for line in p.stdout.splitlines():
            r = json.loads(line)
            if r.get("id") == 2:
                res = r["result"]
                return res["isError"], res["content"][0]["text"]
        return True, "no reply"

    def _record(self, call, name, args, text):
        call["tools"].append((name, args, text))

    # ------------------------------------------------------------ register operations (G: server; G-lite: ledger file)
    def _ledger_text(self):
        return open(self.ledger, encoding="utf-8").read()

    def _ledger_write(self, txt):
        with open(self.ledger, "w", encoding="utf-8") as fh:
            fh.write(txt)

    def op_log(self, call, text, user_up=None):
        if self.lite:
            self.ledger_n += 1
            st, _ = store.parse_ledger(self._ledger_text())
            qid = "Q%d" % st["next_id"]
            self._ledger_write(self._ledger_text() + "- [ ] %s %s\n" % (qid, text))
            self._record(call, "Edit", {"file_path": ".scratch/ledger.md", "append": "- [ ] %s %s" % (qid, text)}, "ok")
            return qid
        err, out = self._mcp("log_question", {"text": text})
        self._record(call, "mcp__register__log_question", {"text": text}, out)
        assert not err, out
        return re.search(r"id=(Q\d+)", out).group(1)

    def op_resolve(self, call, qid, source="user", decision=None):
        decision = decision or self.decision_text
        if self.lite:
            txt = self._ledger_text()
            new = re.sub(r"(?m)^- \[[ !p]\] (%s\b[^\n]*?)(?: =>[^\n]*)?$" % qid, lambda m: "- [x] %s => %s" % (m.group(1), decision), txt)
            self._ledger_write(new)
            self._record(call, "Edit", {"file_path": ".scratch/ledger.md", "resolve": qid}, "ok")
            return
        err, out = self._mcp("resolve", {"id": qid, "decision": decision, "source": source})
        self._record(call, "mcp__register__resolve", {"id": qid, "decision": decision, "source": source}, out)
        assert not err, out

    def op_branch(self, call, from_id, ids_texts):
        if self.lite:
            ids = []
            st, _ = store.parse_ledger(self._ledger_text())
            n = st["next_id"]
            add = "## branch b-stub (from %s)\n" % from_id
            for i, t in enumerate(ids_texts):
                add += "- [ ] Q%d %s\n" % (n + i, t)
                ids.append("Q%d" % (n + i))
            self._ledger_write(self._ledger_text() + add)
            self._record(call, "Edit", {"file_path": ".scratch/ledger.md", "append": add}, "ok")
            return ids
        args = {"from_id": from_id, "branch": "b-stub", "topic": "stub branch topic", "subquestions": ids_texts}
        err, out = self._mcp("open_branch", args)
        self._record(call, "mcp__register__open_branch", args, out)
        assert not err, out
        return re.search(r"created=([Q0-9,]+)", out).group(1).split(",")

    def op_reopen(self, call, qid, affects):
        if self.lite:
            txt = self._ledger_text()
            for q in [qid] + list(affects):
                txt = re.sub(r"(?m)^- \[[xp]\] (%s\b)" % q, r"- [!] \1", txt)
            self._ledger_write(txt)
            self._record(call, "Edit", {"file_path": ".scratch/ledger.md", "reopen": qid}, "ok")
            return list(affects)
        args = {"id": qid, "why": "the user changed this value", "affects": list(affects)}
        err, out = self._mcp("reopen", args)
        self._record(call, "mcp__register__reopen", args, out)
        assert not err, out
        return list(affects)

    def state(self):
        if self.lite:
            return store.parse_ledger(self._ledger_text())[0]
        return store.load(self.dirs["state"])

    # ------------------------------------------------------------ the interviewer
    def interviewer(self, prompt, resume, tag):
        call = dict(tag=tag, prompt=prompt, tools=[], ctx=None, ctx2=None, blocks=[])
        self.calls.append(call)
        call["ctx"] = self._hook("hook_register_userprompt.py", {"prompt": prompt}, tag)
        if "-compact" in tag:
            call["ctx2"] = self._hook("hook_register_sessionstart.py", {"source": "compact"}, tag)
            return super().interviewer(prompt, resume, tag)
        exempt = store.gate_skip_for_tag(tag)
        if self.pending and not exempt and resume is not None:
            self.op_resolve(call, self.pending, "user")
            self.pending = None
        base = super().interviewer(prompt, resume, tag)
        text = base["text"]
        if "-postdone-" in tag and not self.lite:
            _, out = self._mcp("log_question", {"text": "late information", "kind": "constraint", "decision": prompt[:200]})
            self._record(call, "mcp__register__log_question", {"text": "late information", "kind": "constraint", "decision": prompt[:200]}, out)
        if "-final" in tag and not self.lite:
            _, out = self._mcp("list_open", {"include_resolved": True})
            self._record(call, "mcp__register__list_open", {"include_resolved": True}, out)
        if not exempt and "?" in text and re.search(r"Q\d+:", text):
            text = self._label_question(call, text)
        text = self._stop(call, tag, text)
        base["text"] = text
        return base

    def _label_question(self, call, text):
        """Replace the item stub's own Q<n> label by a real register id (new entry, or one queued by branch / reopen)."""
        if self.todo:
            qid = self.todo.pop(0)
        else:
            qid = self.op_log(call, re.sub(r"\s+", " ", re.sub(r"^.*?Q\d+:\s*", "", text))[:120])
        self.pending = qid
        text = re.sub(r"Q\d+:", qid + ":", text, count=1)
        if not self.branch_done and self.asked >= 5:
            self.branch_done = True
            self.todo += self.op_branch(call, qid, ["stub sub-question A", "stub sub-question B"])
        elif not self.reopen_done and self.asked >= self.target - 1:
            st = self.state()
            resolved = [q for q, e in st["entries"].items() if e["status"] == "RESOLVED" and q != qid]
            if len(resolved) >= 2:
                self.reopen_done = True
                self.op_reopen(call, resolved[0], resolved[1:2])
                self.todo += [resolved[0], resolved[1]]
        return text

    def _stop(self, call, tag, text):
        """Run the Stop hook on the message; obey blocks (resolve what is open, or log and relabel)."""
        for attempt in range(4):
            out = self._hook("hook_register_stop.py", {"last_assistant_message": text, "stop_hook_active": attempt > 0}, tag)
            if not out:
                return text
            reason = json.loads(out)["reason"]
            call["blocks"].append(reason)
            self.blocks.append((tag, reason))
            st = self.state()
            if reason.startswith("Open entries"):
                for q in [q for q, e in st["entries"].items() if e["status"] in store.UNRESOLVED]:
                    self.op_resolve(call, q, "recommended", "stub: decided under the recommend step")
                self.pending, self.todo = None, []
                text = "All entries are handled. Ready to write the plan." + " ref " + str(len(self.blocks))
            else:
                qid = self.op_log(call, re.sub(r"\s+", " ", text)[:120])
                self.pending = qid
                text = qid + ": " + re.sub(r"^[^:]*:\s*", "", text) if re.match(r"^Q\d+:", text) else qid + ": " + text
        return text

    # ------------------------------------------------------------ transcript
    def transcript(self, log, first_prompt):
        recs, mid = [], [0]
        tid = [0]

        def user(text, **extra):
            r = {"type": "user", "message": {"role": "user", "content": text}}
            r.update(extra)
            recs.append(r)

        def asst_blocks(blocks):
            mid[0] += 1
            recs.append({"type": "assistant", "message": {"id": "msg_%d" % mid[0], "role": "assistant", "content": blocks,
                         "usage": {"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100}}})

        def attachment(text):
            if text:
                ctx = json.loads(text)["hookSpecificOutput"]["additionalContext"]
                recs.append({"type": "attachment", "attachment": {"type": "hook_additional_context", "content": ctx}})

        pending, k = first_prompt, 0
        for r in log:
            kind = r["kind"]
            if kind == "answerer":
                pending = r["text"]
                continue
            if kind == "interviewer" or kind.startswith("inject_"):
                call = self.calls[k]
                k += 1
                if kind == "inject_compact":
                    recs.append({"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"})
                    recs.append({"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": "Summary of the interview so far."}})
                    attachment(call["ctx2"])
                    continue
                user(pending if kind == "interviewer" else (r.get("prompt") or "(injected)"))
                attachment(call["ctx"])
                for name, args, out in call["tools"]:
                    tid[0] += 1
                    asst_blocks([{"type": "tool_use", "id": "toolu_%d" % tid[0], "name": name, "input": args}])
                    recs.append({"type": "user", "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_%d" % tid[0], "content": [{"type": "text", "text": out}]}]}})
                asst_blocks([{"type": "text", "text": r["text"]}])
                pending = None
        return recs
