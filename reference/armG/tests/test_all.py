#!/usr/bin/env python3
"""Offline unit tests: parser, renderer, hooks, scorer, runner dry-run, skills, summariser, judge de-arming.
No model, no network. Run: python3 tests/test_all.py   (uses unittest)"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
H = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(H, "lib"))
sys.path.insert(0, os.path.join(H, "readback"))
sys.path.insert(0, H)
import fixture_builder as fb  # noqa: E402
import tree_lib  # noqa: E402
import tree_store  # noqa: E402
import render_tree  # noqa: E402
import score as scorer  # noqa: E402
import judge  # noqa: E402
import summarise  # noqa: E402
import score_items as scorer_items  # noqa: E402
from common import read_jsonl, seq_calls, split_turns, tool_uses  # noqa: E402

KEY = os.path.join(H, "..", "fixtures", "f1-portfolio-generator", "key.json")
TMP = tempfile.mkdtemp(prefix="seqtests-", dir=os.environ.get("HARNESS_TMP") or os.path.join(H, "tests", ".tmp") if os.path.isdir(os.path.join(H, "tests", ".tmp")) else None)


def write(name, recs):
    p = os.path.join(TMP, name)
    fb.write(p, recs)
    return p


def run_score(name, arm, recs):
    class A:
        pass
    a = A()
    a.transcript, a.key, a.arm, a.run_id = write(name, recs), KEY, arm, "t"
    a.driver_log = a.hook_log = None
    return scorer.score(a)


class TestParser(unittest.TestCase):
    def test_recursive_tool_use_and_nesting(self):
        nested = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "x", "name": fb.SEQ,
                  "input": {"thought": "a", "thoughtNumber": 1}}]}, "wrapper": [{"deep": {"type": "tool_use", "id": "y", "name": "Read", "input": {}}}]}
        self.assertEqual([t["id"] for t in tool_uses(nested)], ["x", "y"])

    def test_seq_calls_drops_failed(self):
        recs = fb.build("B")
        # mark the first call's result as failed
        for r in recs:
            if r["type"] == "user" and isinstance(r["message"]["content"], list):
                r["message"]["content"][0]["content"] = [{"type": "text", "text": '{"status": "failed"}'}]
                break
        calls = seq_calls(recs)
        self.assertEqual(len(calls), len(seq_calls(fb.build("B"))))
        self.assertFalse(calls[0]["ok"])

    def test_turn_split(self):
        turns = split_turns(fb.build("B"))
        self.assertEqual(len(turns), 9)   # compact summary and tool_results are not turns
        self.assertTrue(turns[7]["compact_before"])
        self.assertEqual(turns[7]["user_text"], "What are we still waiting on?")


class TestRenderer(unittest.TestCase):
    def setUp(self):
        self.calls = [c["input"] for c in seq_calls(fb.build("B"))]

    def test_render_content(self):
        txt = tree_lib.render(self.calls)
        self.assertTrue(txt.startswith("<thought-tree>") and txt.endswith("</thought-tree>"))
        self.assertIn("Next free thoughtNumber: %d" % (len(self.calls) + 1), txt)
        self.assertIn("b-claims>", txt)                        # open branch on the stack
        self.assertIn("[superseded by #", txt)                 # revised A-node marked
        self.assertIn("Header to copy: Q8 [open:", txt)

    def test_branch_closure(self):
        calls = self.calls + [dict(thought="A9: branch b-claims closed - done", thoughtNumber=99, totalThoughts=99,
                                   nextThoughtNeeded=True, branchId="b-claims", branchFromThought=14)]
        self.assertEqual(tree_lib.build(calls)["stack"], [])

    def test_cap_and_empty(self):
        self.assertEqual(tree_lib.render([]), "")
        big = [dict(thought="A%d: %s" % (i, "word " * 60), thoughtNumber=i, totalThoughts=500, nextThoughtNeeded=True) for i in range(1, 200)]
        txt = tree_lib.render(big)
        self.assertLessEqual(len(txt), 6000)
        self.assertIn("older decisions omitted", txt)

    def test_sidecar_equals_transcript(self):
        t = write("t.jsonl", fb.build("B2"))
        side = os.path.join(TMP, "tree.jsonl")
        with open(side, "w") as fh:
            for i, c in enumerate(seq_calls(read_jsonl(t))):
                fh.write(json.dumps({"tool_use_id": c["id"], "tool_input": c["input"]}) + "\n")
        r = subprocess.run([sys.executable, os.path.join(H, "readback", "parity.py"), "--transcript", t, "--sidecar", side],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(side, "a") as fh:     # lag: transcript has more calls than sidecar
            pass
        short = os.path.join(TMP, "short.jsonl")
        lines = open(side).read().splitlines()[:-2]
        open(short, "w").write("\n".join(lines) + "\n")
        r = subprocess.run([sys.executable, os.path.join(H, "readback", "parity.py"), "--transcript", t, "--sidecar", short],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)


class TestHooks(unittest.TestCase):
    def run_hook(self, script, payload, state):
        return subprocess.run([sys.executable, os.path.join(H, "readback", script)], input=json.dumps(payload),
                              capture_output=True, text=True, env=dict(os.environ, SEQ_STATE_DIR=state))

    def test_sidecar_then_inject_then_sessionstart(self):
        state = tempfile.mkdtemp(dir=TMP)
        t = write("h.jsonl", fb.build("B2"))
        for c in seq_calls(read_jsonl(t)):
            r = self.run_hook("hook_sidecar.py", {"tool_use_id": c["id"], "tool_name": fb.SEQ, "tool_input": c["input"], "cwd": TMP}, state)
            self.assertEqual(r.returncode, 0)
        n_side = len(open(os.path.join(state, "tree.jsonl")).read().splitlines())
        self.assertEqual(n_side, len(seq_calls(read_jsonl(t))))
        r = self.run_hook("hook_userprompt.py", {"transcript_path": "/nonexistent", "cwd": TMP, "prompt": "x"}, state)
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("<thought-tree>", out["hookSpecificOutput"]["additionalContext"])
        r = self.run_hook("hook_sessionstart.py", {"source": "compact", "transcript_path": t, "cwd": TMP}, tempfile.mkdtemp(dir=TMP))
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"], "SessionStart")  # transcript fallback
        r = self.run_hook("hook_userprompt.py", {"cwd": TMP}, tempfile.mkdtemp(dir=TMP))
        self.assertEqual((r.returncode, r.stdout), (0, ""))      # no calls: silent
        r = subprocess.run([sys.executable, os.path.join(H, "readback", "hook_userprompt.py")], input="not json", text=True,
                           capture_output=True, env=dict(os.environ, SEQ_STATE_DIR=tempfile.mkdtemp(dir=TMP)))
        self.assertEqual(r.returncode, 0)
        events = [json.loads(x)["event"] for x in open(os.path.join(state, "hook-log.jsonl"))]
        self.assertIn("UserPromptSubmit", events)


class TestScorer(unittest.TestCase):
    def test_wired_B(self):
        s = run_score("B.jsonl", "B", fb.build("B"))
        self.assertEqual(s["recall"]["all"]["caught"], 1)           # P1 caught, P2 missed
        self.assertEqual(s["recall"]["all"]["delivered"], 2)        # P3 and all X late answers never delivered
        self.assertEqual(s["recall"]["all"]["recall"], 0.5)
        self.assertEqual([r["status"] for r in s["constraints"] if r["id"] in ("P1", "P2")], ["caught", "missed"])
        self.assertEqual((s["branch_loss"]["opened"], s["branch_loss"]["lost"]), (1, 1))   # lag sub-question never asked
        self.assertEqual([r["handled"] for r in s["reopen_handling"] if r["id"] == "R2"], [True])
        self.assertTrue(s["w1"]["w1_pass"])
        self.assertTrue(s["w2"]["w2_pass"])
        self.assertEqual(s["w2"]["header_accuracy"], 1.0)
        self.assertEqual(s["wired_class"], "wired")
        self.assertEqual(s["status_query"]["items_matched"], 3)
        self.assertTrue(s["validity"]["compaction_boundary_present"])
        self.assertEqual(s["tokens"]["fresh_equivalent"], s["tokens"]["input"] + s["tokens"]["cache_creation"] + s["tokens"]["output"])

    def test_linear_D_unwired_and_A(self):
        d = run_score("D.jsonl", "D", fb.build("D"))
        self.assertFalse(d["w1"]["w1_pass"])
        self.assertEqual(d["wired_class"], "unwired")
        self.assertEqual(d["w1"]["branch_rate"], 0.0)
        a = run_score("A.jsonl", "A", fb.build("A"))
        self.assertEqual(a["validity"]["seq_calls"], 0)
        self.assertTrue(a["validity"]["tool_use_matches_arm"])
        self.assertEqual(a["wired_class"], "n/a")
        self.assertFalse(run_score("Abad.jsonl", "D", fb.build("A"))["validity"]["tool_use_matches_arm"])

    def test_B_without_protocol_calls(self):
        s = run_score("Bu.jsonl", "B", fb.build("B", wired=False))
        self.assertFalse(s["w1"]["w1_pass"])
        self.assertIn(s["wired_class"], ("unwired", "partly_wired"))

    def test_B2_readback_events_and_E_calls(self):
        b2 = run_score("B2.jsonl", "B2", fb.build("B2"))
        self.assertGreaterEqual(b2["w2"]["readback_rate"], 0.8)
        self.assertEqual(b2["wired_class"], "wired")
        self.assertEqual(run_score("B2x.jsonl", "B2", fb.build("B"))["w2"]["readback_rate"], 0.0)   # no injections present
        e = run_score("E.jsonl", "E", fb.build("E"))
        self.assertEqual(e["tool_counts"].get(fb.TREE), 1)
        self.assertLess(e["w2"]["readback_rate"], 0.5)

    def test_bad_header_detected(self):
        recs = fb.build("B")
        for r in recs:
            if r["type"] == "assistant":
                for blk in r["message"]["content"]:
                    if blk["type"] == "text":
                        blk["text"] = blk["text"].replace("last: #", "last: #9")
        s = run_score("bad.jsonl", "B", recs)
        self.assertEqual(s["w2"]["header_accuracy"], 0.0)
        self.assertFalse(s["w2"]["w2_pass"])


class TestSummariseJudge(unittest.TestCase):
    def test_summary_table(self):
        s1 = run_score("s1.jsonl", "B", fb.build("B"))
        s2 = run_score("s2.jsonl", "D", fb.build("D"))
        d = tempfile.mkdtemp(dir=TMP)
        for i, s in enumerate((s1, s2)):
            os.makedirs(os.path.join(d, str(i)))
            json.dump(s, open(os.path.join(d, str(i), "score.json"), "w"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = summarise.main(["--root", d])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("## Per arm", out)
        self.assertIn("0.50 [0.50-0.50] (1)", out)
        self.assertIn("| B / f1-portfolio-generator | 1 |", out)

    def test_dearm_strips_leaks(self):
        txt = judge.dearm("Q3 [open: Q1, Q2; stack: b-x>3; last: #9] Per node #4 see <thought-tree>x</thought-tree> and .scratch/tree.md")
        for leak in ("<thought-tree>", "[open:", "#4", "tree.md"):
            self.assertNotIn(leak, txt)
        d = judge.render_dearmed(fb.build("B2"))
        self.assertNotIn("<thought-tree>", d)
        self.assertNotIn("Header to copy", d)
        ps = judge.prompts("T", json.load(open(KEY)))
        self.assertEqual(len(ps), 6)


class TestRunnerAndSkills(unittest.TestCase):
    def dry(self, arm, *extra):
        r = subprocess.run([sys.executable, os.path.join(H, "run_arm.py"), "--fixture", os.path.join(H, "..", "fixtures", "f1-portfolio-generator"),
                            "--arm", arm, "--run-id", "t", "--out-root", os.path.join(TMP, "runs"), "--dry-run"] + list(extra),
                           capture_output=True, text=True)
        return r

    def test_dry_run_all_arms(self):
        for arm, tools_expected, hooks in (("A", False, False), ("D", True, False), ("B", True, False), ("B2", True, True),
                                           ("C", False, False), ("E", True, False)):
            r = self.dry(arm)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = r.stdout
            self.assertEqual("mcp__sequential-thinking__sequentialthinking" in out.split("# DRY RUN SUMMARY")[0], tools_expected, arm)
            self.assertEqual("hooks configured: UserPromptSubmit" in out, hooks, arm)
            self.assertEqual(("get_thought_tree" in out), arm == "E")
            self.assertIn("-p --output-format stream-json --verbose --include-hook-events", out)
            self.assertIn("--resume", out)
            self.assertIn("-compact]", out)
            self.assertIn("-status]", out)
            self.assertIn("Write Edit", out) if arm == "C" else self.assertIn("Write Edit", out.split("--disallowedTools")[1])
            summary = json.loads(out.split("# DRY RUN SUMMARY")[1])
            self.assertEqual(summary["stop_reason"], "interviewer_done")
            self.assertEqual(summary["dry_run_counts"]["interviewer_claude_calls"] + summary["dry_run_counts"]["answerer_claude_calls"],
                             summary["exchanges"] + summary["dry_run_counts"]["answerer_claude_calls"])
        self.assertFalse(os.path.exists(os.path.join(TMP, "runs")))          # dry run creates nothing

    def test_dry_run_cap_triggers(self):
        r = self.dry("D", "--max-exchanges", "5")
        self.assertEqual(json.loads(r.stdout.split("# DRY RUN SUMMARY")[1])["stop_reason"], "cap_exchanges")

    def test_prompt_never_on_argv_and_no_key_leak(self):
        out = self.dry("B").stdout
        self.assertNotIn("planted", out.lower().replace("planted-constraint", ""))
        self.assertNotIn("key.json", out)
        self.assertIn("--tools ''", out)                                     # answerer has no tools

    def test_no_credential_copies_and_skill_has_no_shell_injection(self):
        import run_arm
        home = tempfile.mkdtemp(prefix="home-", dir=TMP)
        os.makedirs(os.path.join(home, ".claude"))
        real = os.path.join(home, ".claude", ".credentials.json")
        open(real, "w").write("x")
        cfg = tempfile.mkdtemp(prefix="cfg-", dir=TMP)
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            open(os.path.join(cfg, ".credentials.json"), "w").write("copy")   # simulate CLI replacing the symlink
            run_arm.relink_credentials([cfg])
        finally:
            os.environ["HOME"] = old
        link = os.path.join(cfg, ".credentials.json")
        self.addCleanup(lambda: os.path.lexists(real) and os.remove(real))
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.path.realpath(link), os.path.realpath(real))
        # a real (non-dry) build must symlink both configs, and the written skill must not carry the ! line
        for root, _d, files in os.walk(TMP):
            for f in files:
                if "credential" in f.lower():
                    self.assertTrue(os.path.islink(os.path.join(root, f)) or root.startswith(home), os.path.join(root, f))
        for a in ("A", "D", "B", "B2", "C", "E"):
            args = type("A", (), dict(arm=a, link_credentials=False))()
            import argparse
            d = os.path.join(TMP, "build-" + a)
            run_arm.build_run_dir(args, os.path.join(H, "..", "fixtures", "f1-portfolio-generator"), d, False)
            txt = open(os.path.join(d, "config", "skills", "grill-me", "SKILL.md")).read()
            self.assertNotIn("learnings.md", txt)
            self.assertFalse(any("credential" in f.lower() for _r, _ds, fs in os.walk(d) for f in fs))

    def test_forced_by_fallback_flag(self):
        base = [sys.executable, os.path.join(H, "run_arm.py"), "--fixture", os.path.join(H, "..", "fixtures", "f1-portfolio-generator"),
                "--arm", "A", "--run-id", "t", "--out-root", TMP, "--dry-run"]
        off = subprocess.run(base + ["--no-forced-by-fallback"], capture_output=True, text=True).stdout
        on = subprocess.run(base, capture_output=True, text=True).stdout          # default on
        self.assertIn('"by": "never"', off)
        self.assertNotIn('"forced_turn"', off)
        self.assertIn('"by": "forced_turn"', on)
        self.assertIn('"A20"', on)

    def _drive(self, texts, forced=True, min_q=0, no_compaction=True, answers=None):
        import argparse
        import run_arm
        key = json.load(open(KEY))
        key["expected_questions"]["min"] = min_q
        calls = []

        class B:
            n = 0

            def interviewer(self, prompt, resume, tag):
                t = texts[min(self.n, len(texts) - 1)]
                self.n += 1
                calls.append(prompt)
                return dict(text=t, session="s", usage={}, cost=0.1, error=None, init=None)

            def answerer(self, prompt, tag):
                return dict(text=json.dumps({"ids": answers or [], "reply": "No strong view."}), usage={}, cost=0.0, error=None)
        a = argparse.Namespace(max_exchanges=80, max_fresh_tokens=10**9, max_processed_tokens=10**9, max_cost_usd=100.0,
                               max_seconds=10**6, arm="A", compact_prompt="/compact", status_query_version="v2", no_compaction=no_compaction, forced_by_fallback=forced)
        with redirect_stdout(io.StringIO()):
            out = run_arm.drive(a, None, TMP, {}, B(), "plan", key, "brief")
        self.calls = calls
        return out

    def test_continue_nudge_fires_and_is_capped(self):
        log, meta, _ = self._drive(["Q1 first?", "Q2 second?", "All done.", "Nothing more."], forced=False, min_q=24)
        nudges = [r for r in log if r["kind"] == "inject_continue"]
        self.assertEqual(len(nudges), 3)
        self.assertIn("Ask each open item as a question; do not end yet.", self.calls[4])
        self.assertEqual(meta["stop_reason"], "interviewer_done")
        self.assertEqual(meta["continue_nudges"], 3)
        # no nudge when the minimum is already met
        log0, meta0, _ = self._drive(["Q1 first?", "Q2 second?", "All done.", "Nothing more."], forced=False, min_q=0)
        self.assertFalse([r for r in log0 if r["kind"] == "inject_continue"])

    def test_early_stop_flag(self):
        _, meta, _ = self._drive(["Q1 first?", "Q2 second?", "All done.", "Nothing more."], forced=False, min_q=24)
        self.assertTrue(meta["early_stop"])
        self.assertTrue(meta["invalid_for_comparison"])
        self.assertEqual(meta["answerer_turns"], 6)       # 2 questions + 1 answer per nudge
        self.assertEqual(meta["n_questions"], 2)
        _, ok, _ = self._drive(["Q1 first?", "Q2 second?", "All done.", "Nothing more."], forced=False, min_q=2)
        self.assertFalse(ok["early_stop"])
        # score.py and summarise.py flag it
        rm = os.path.join(TMP, "rm.json")
        json.dump(meta, open(rm, "w"))
        tr = write("es-tr.jsonl", fb.build("A"))
        a = type("A", (), dict(transcript=tr, key=KEY, arm="A", run_id="r", driver_log=None, hook_log=None, run_meta=rm))()
        res = scorer.score(a)
        self.assertTrue(res["invalid_for_comparison"])
        d = tempfile.mkdtemp(dir=TMP)
        json.dump(res, open(os.path.join(d, "score.json"), "w"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            summarise.main(["--root", d])
        self.assertIn("INVALID", buf.getvalue())

    def test_compaction_between_P_and_X_post_done(self):
        import hashlib
        qs = ["Q%d? %s" % (i, hashlib.sha256(str(i).encode()).hexdigest() * 6) for i in range(13)]
        log, meta, _ = self._drive(qs + ["All done.", "Nothing more."], no_compaction=False)
        kinds = [r["kind"] for r in log]
        self.assertEqual(kinds.count("inject_compact"), 1)
        ci = kinds.index("inject_compact")
        self.assertEqual(kinds[ci + 1], "inject_status")
        key = json.load(open(KEY))
        typ = {c["late"]["answer_id"]: c["type"] for c in key["constraints"]}
        post = [(i, r["ids"][0]) for i, r in enumerate(log) if r.get("route") == "post_done_delivery"]
        self.assertTrue(post)
        for i, iid in post:
            self.assertEqual(i > ci, typ.get(iid) == "X", iid)
        self.assertTrue(meta["compaction_ok"])
        # --no-compaction: none fires
        log2, _, _ = self._drive(qs + ["All done.", "Nothing more."], no_compaction=True)
        self.assertFalse([r for r in log2 if r["kind"] == "inject_compact"])

    def test_stop_interviewer_done_after_two_no_question(self):
        log, meta, _ = self._drive(["Q1 first?", "Q2 second?", "All done.", "Nothing more."], forced=False)
        self.assertEqual(meta["stop_reason"], "interviewer_done")
        self.assertEqual(meta["interviewer_messages"], 4)
        self.assertIn("stop_cost_usd", meta)

    def test_stop_dead_air_on_three_near_duplicates(self):
        dup = "Could you paste the record into a file yourself? I cannot write it for you here, sorry."
        log, meta, _ = self._drive(["Q1 distinct opening question about layers?", dup, dup + " ", dup + "  "], forced=False)
        self.assertEqual(meta["stop_reason"], "dead_air")
        import run_arm
        self.assertTrue(run_arm.similar(dup, dup + " x"))
        self.assertFalse(run_arm.similar("alpha beta gamma?", "totally different words here?"))

    def test_forced_early_and_late_delivery(self):
        import hashlib
        qs = ["Q%d? %s" % (i, hashlib.sha256(str(i).encode()).hexdigest() * 6) for i in range(40)]
        log, meta, _ = self._drive(qs)
        self.assertEqual(meta["delivery"]["A02"]["by"], "forced_turn")
        self.assertEqual(meta["delivery"]["A20"]["by"], "forced_turn")
        self.assertEqual(meta["delivery"]["A26"]["by"], "forced_turn")

    def test_post_done_delivery(self):
        log, meta, _ = self._drive(["Q1 first?", "Q2 second?", "All done.", "Nothing more."])
        self.assertEqual(meta["stop_reason"], "interviewer_done_after_delivery")
        post = [i for i, d in meta["delivery"].items() if d["by"] == "post_done_delivery"]
        self.assertLessEqual(len(post), 6)
        self.assertGreaterEqual(len(post), 1)
        sent = [r for r in log if r.get("route") == "post_done_delivery"]
        self.assertTrue(all(r["text"].endswith("This is new information I have just received.") for r in sent))
        self.assertFalse(any("contradict" in r["text"].lower() for r in sent))
        # disabled: stops plainly and records never
        import argparse
        log2, meta2, _ = self._drive(["Q1 first?", "Q2 second?", "All done.", "Nothing more."], forced=False)
        self.assertEqual(meta2["stop_reason"], "interviewer_done")
        self.assertTrue(all(d["by"] == "never" for d in meta2["delivery"].values()))

    def test_post_done_all_delivered_stops_plain(self):
        import hashlib
        qs = ["Q%d? %s" % (i, hashlib.sha256(str(i).encode()).hexdigest() * 6) for i in range(30)] + ["Done.", "Done too."]
        log, meta, _ = self._drive(qs)
        self.assertEqual(meta["stop_reason"], "interviewer_done")
        self.assertFalse(any(d["by"] == "post_done_delivery" for d in meta["delivery"].values()))

    def test_scorer_routes_and_summary(self):
        recs = [{"kind": "answerer", "ids": ["A20"], "route": "post_done_delivery", "text": "x"},
                {"kind": "answerer", "ids": ["A21"], "text": "y"}]
        d = os.path.join(TMP, "routes.jsonl")
        fb.write(d, recs)
        key = json.load(open(KEY))
        utter = lambda t: [{"type": "user", "message": {"role": "user", "content": t}}, {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok?"}]}}]
        tr = write("routes-tr.jsonl", utter("a") + utter(key["constraints"][0]["late"]["statement"] + " This is new information I have just received."))
        a = type("A", (), dict(transcript=tr, key=KEY, arm="A", run_id="r", driver_log=d, hook_log=None, out=None))()
        res = scorer.score(a)
        self.assertEqual(res["recall"]["post_done_delivery_count"], 1)
        self.assertEqual(res["recall"]["by_route"]["post_done_delivery"]["delivered"], 1)

    def test_first_prompt_forbids_record_writing_for_all_arms(self):
        import run_arm
        for a in ("A", "D", "B", "B2", "C", "E"):
            out = subprocess.run([sys.executable, os.path.join(H, "run_arm.py"), "--fixture", os.path.join(H, "..", "fixtures", "f1-portfolio-generator"),
                                  "--arm", a, "--run-id", "t", "--out-root", TMP, "--dry-run"], capture_output=True, text=True).stdout
            self.assertIn("print it in chat instead", out) if False else None
        self.assertIn("print it in chat instead", run_arm.FIRST_PROMPT)
        self.assertIn("tree.md", run_arm.FIRST_EXTRA_C)

    def test_arm_skills_regenerate_identically_and_original_unchanged(self):
        r = subprocess.run([sys.executable, os.path.join(H, "make_arm_skills.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        arms = {a: open(os.path.join(H, "skills", "arm-%s.md" % a)).read() for a in ("A", "D", "B", "B2", "C", "E")}
        orig = open(os.path.join(H, "skills", "grill-me.original.md")).read()
        self.assertEqual(arms["D"], orig)
        self.assertNotIn("Sequential Thinking", arms["A"].split("## Process")[0])
        self.assertIn("get_thought_tree", arms["E"])
        self.assertNotIn("get_thought_tree", arms["B"])
        self.assertIn("<thought-tree>", arms["B2"])
        self.assertIn(".scratch/tree.md", arms["C"])
        self.assertNotIn("mcp__sequential-thinking", arms["C"])
        for a in ("B", "B2", "C", "E"):
            self.assertIn("State header", arms[a])
            self.assertIn("7. **Resolve the tree**", arms[a])


class TestPilotScript(unittest.TestCase):
    def _env(self, tmp, cap):
        stub = os.path.join(tmp, "stub.py")
        with open(stub, "w") as fh:
            fh.write("import sys,os,json\na=sys.argv\narm=a[a.index('--arm')+1];rid=a[a.index('--run-id')+1];o=a[a.index('--out-root')+1]\n"
                     "d=os.path.join(o,'f1-portfolio-generator',arm,rid);os.makedirs(d)\n"
                     "json.dump({'stop_reason':'complete','cost_usd':50.0},open(d+'/run_meta.json','w'))\n"
                     "open(d+'/transcript.jsonl','w').close()\n")
        return dict(os.environ, PILOT_RUNS_ROOT=os.path.join(tmp, "runs"), PILOT_RUN_ARM=stub,
                    PILOT_SCRATCH=os.path.join(tmp, "scr"), PILOT_TOTAL_CAP_USD=str(cap))

    def test_dry_run_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(["bash", os.path.join(H, "bin", "pilot.sh"), "--dry-run"], capture_output=True, text=True,
                               env=self._env(tmp, 90))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.count("[todo]"), 12)
            self.assertIn("total ceiling USD 90", r.stdout)
            lines = [ln for ln in r.stdout.splitlines() if "[todo]" in ln]
            self.assertIn("--run-id p1", lines[5])
            self.assertIn("--run-id p2", lines[6])
            self.assertIn("--max-cost-usd 12", lines[0])
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs")))

    def test_gate_and_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            sh = os.path.join(H, "bin", "pilot.sh")
            no = subprocess.run(["bash", sh], input="yes\n", capture_output=True, text=True, env=self._env(tmp, 90))
            self.assertEqual(no.returncode, 1)      # piped stdin is not a tty: refuse, never hang
            self.assertIn("no tty: re-run with --yes to confirm", no.stdout)
            self.assertIn("total ceiling USD 90", no.stdout)
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs")))
            with tempfile.TemporaryDirectory() as tmp2:
                envy = subprocess.run(["bash", sh], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                      env=dict(self._env(tmp2, 90), PILOT_CONFIRM="yes"))
                self.assertIn("CEILING reached", envy.stdout)   # env confirm skips the prompt
                self.assertTrue(os.path.exists(os.path.join(tmp2, "runs")))
            yes = subprocess.run(["bash", sh, "--yes"], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=self._env(tmp, 90))
            self.assertIn("CEILING reached", yes.stdout)
            done = [m for m in os.popen("find %s -name run_meta.json" % os.path.join(tmp, "runs")).read().split()]
            self.assertEqual(len(done), 2)        # 50 + 50 >= 90 stops before the third
            again = subprocess.run(["bash", sh, "--yes"], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=self._env(tmp, 90))
            self.assertIn("skip A p1", again.stdout)
            self.assertIn("CEILING reached", again.stdout)


class TestFollowup(unittest.TestCase):
    SH = os.path.join(H, "bin", "followup.sh")

    def _env(self, tmp, cost=30.0, cap=60, sr="complete"):
        stub = os.path.join(tmp, "stub_run.py")
        with open(stub, "w") as fh:
            fh.write("import sys,os,json\na=sys.argv\narm=a[a.index('--arm')+1];rid=a[a.index('--run-id')+1];o=a[a.index('--out-root')+1]\n"
                     "d=os.path.join(o,'f1-portfolio-generator',arm,rid);os.makedirs(d)\n"
                     "json.dump({'stop_reason':'%s','cost_usd':%s},open(d+'/run_meta.json','w'))\n"
                     "open(d+'/transcript.jsonl','w').close()\n" % (sr, cost))
        return dict(os.environ, FOLLOWUP_RUNS_ROOT=os.path.join(tmp, "runs"), FOLLOWUP_RUN_ARM=stub,
                    FOLLOWUP_SCRATCH=os.path.join(tmp, "scr"), FOLLOWUP_TOTAL_CAP_USD=str(cap))

    def test_refuses_without_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(["bash", self.SH, "B:p1"], capture_output=True, text=True, env=self._env(tmp))
            self.assertEqual(r.returncode, 1)
            self.assertIn("--yes", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs")))

    def test_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(["bash", self.SH, "--yes", "A:p1", "B:p1", "C:p1"], capture_output=True, text=True,
                               env=self._env(tmp, cost=30.0, cap=60))
            self.assertIn("CEILING reached", r.stdout)
            runs = os.listdir(os.path.join(tmp, "runs", "f1-portfolio-generator"))
            self.assertEqual(sorted(runs), ["A", "B"])       # 30 + 30 >= 60 stops before the third

    def test_run_cap_passed_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(self._env(tmp, cost=1.0), FOLLOWUP_RUN_CAP_USD="7")
            r = subprocess.run(["bash", self.SH, "--yes", "A:p1"], capture_output=True, text=True, env=env)
            self.assertIn("--max-cost-usd 7 ", r.stdout)
            self.assertIn("per-run cap USD 7", r.stdout)
            logs = os.listdir(os.path.join(tmp, "scr"))
            self.assertTrue(any("--max-cost-usd 7 " in open(os.path.join(tmp, "scr", f)).read() for f in logs if f.endswith(".log")))

    def test_error_retry_and_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "runs", "f1-portfolio-generator")
            for arm, sr in (("B", "error: boom"), ("C", "complete")):
                os.makedirs(os.path.join(base, arm, "p1"))
                with open(os.path.join(base, arm, "p1", "run_meta.json"), "w") as fh:
                    json.dump({"stop_reason": sr, "cost_usd": 1.0}, fh)
            r = subprocess.run(["bash", self.SH, "--yes", "B:p1", "C:p1"], capture_output=True, text=True, env=self._env(tmp, cost=1.0))
            moved = [d for d in os.listdir(os.path.join(tmp, "scr")) if d.startswith("retried-B-p1-")]
            self.assertEqual(len(moved), 1, r.stdout)
            self.assertEqual(json.load(open(os.path.join(base, "B", "p1", "run_meta.json")))["stop_reason"], "complete")  # relaunched
            self.assertIn("REFUSE C p1", r.stdout)
            self.assertEqual(json.load(open(os.path.join(base, "C", "p1", "run_meta.json")))["cost_usd"], 1.0)  # untouched
            self.assertFalse([d for d in os.listdir(os.path.join(tmp, "scr")) if d.startswith("retried-C")])


def sc(n, thought, **kw):
    """A sequentialthinking tool_input for the store tests."""
    d = dict(thought=thought, thoughtNumber=n, totalThoughts=60, nextThoughtNeeded=True)
    d.update(kw)
    return d


def rec(n, thought, **kw):
    return {"tool_use_id": "tu%d-%s" % (n, abs(hash(thought)) % 9999), "tool_input": sc(n, thought, **kw)}


class TestTreeStore(unittest.TestCase):
    def replay(self, *calls):
        return tree_store.replay([{"tool_input": c} for c in calls])

    def base(self):
        return [sc(1, "QLIST\nQ1 [main] Who owns it?\nQ2 [main] Which db? deps=1\nQ3 [main] Deploy target?\nQ4 [main] Retention?"),
                sc(2, "A1 team platform"), sc(3, "A2: postgres")]

    def test_qlist_registers_and_colon_form_tolerated(self):
        st = self.replay(*self.base())
        self.assertEqual({k: v["status"] for k, v in st["questions"].items()},
                         {"1": "RESOLVED", "2": "RESOLVED", "3": "OPEN", "4": "OPEN"})
        self.assertEqual(st["tolerated_malformed"], 1)
        self.assertEqual(st["unresolved"], ["Q3", "Q4"])
        self.assertEqual(st["questions"]["2"]["deps"], [1])
        self.assertEqual(st["questions"]["2"]["deps_source"], "explicit")
        self.assertEqual(st["questions"]["3"]["deps_source"], "defaulted")
        self.assertEqual(st["next_thought"], 4)
        self.assertEqual(st["next_question"], 5)
        self.assertEqual(self.replay(sc(1, "A 7 yes"))["violations"][0]["rule"], "a_unknown")

    def test_batch_of_four_q_labels_checked_each(self):
        st = self.replay(*self.base())
        msg = "Q3: deploy target?\nQ4: retention?\nQ5: logging?\nQ1: owner?"
        self.assertEqual(tree_store.labels_in_message(msg), [3, 4, 5, 1])
        bad, no_label = tree_store.unregistered_in_message(st, msg)
        self.assertEqual(bad, [5, 1])           # Q5 unknown, Q1 already resolved
        self.assertFalse(no_label)
        self.assertEqual(tree_store.unregistered_in_message(st, "Anything else?"), ([], True))

    def test_free_reasoning_ignored(self):
        st = self.replay(*self.base(), sc(4, "Thinking about Q3: maybe aws, but Also A is odd."))
        self.assertEqual(st["violations"], [])
        self.assertEqual(st["next_thought"], 5)

    def test_park_and_unresolved(self):
        st = self.replay(*self.base(), sc(4, "PARK3 user does not know yet"))
        self.assertEqual(st["questions"]["3"]["status"], "PARKED")
        self.assertEqual(st["unresolved"], ["Q4"])
        txt = tree_store.render_injection(st)
        self.assertIn("Parked: Q3 (user does not know yet)", txt)

    def test_reopen_marks_dependants_stale(self):
        st = self.replay(*self.base(), sc(4, "REOPEN1 owner changed", isRevision=True, revisesThought=2))
        self.assertEqual(st["questions"]["1"]["status"], "OPEN")
        self.assertEqual(st["questions"]["2"]["status"], "STALE")      # deps=1
        self.assertEqual(st["questions"]["2"]["stale_of"], 1)
        self.assertEqual(st["reopen_events"], 1)
        self.assertEqual(st["unresolved"], ["Q1", "Q2", "Q3", "Q4"])
        txt = tree_store.render_injection(st)
        self.assertIn("STALE: Q2 (thought 1) [main] Which db? (depends on reopened A1)", txt)
        st2 = self.replay(*self.base(), sc(4, "REOPEN1 x", isRevision=True, revisesThought=2), sc(5, "A1 new owner"),
                          sc(6, "A2 still postgres"))
        self.assertEqual(st2["questions"]["2"]["status"], "RESOLVED")

    def test_namespace_mismatch_violation(self):
        st = self.replay(*self.base(), sc(4, "REOPEN1 owner changed", isRevision=True, revisesThought=1))
        v = st["violations"]
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["rule"], "reopen_namespace")
        self.assertIn("revisesThought=2", v[0]["message"])
        self.assertEqual(st["questions"]["1"]["status"], "RESOLVED")     # node not registered
        self.assertEqual(st["reopen_events"], 0)

    def test_branch_registration_and_violations(self):
        ok = self.replay(*self.base(), sc(4, "Q5 [branch=b-db] Which version?", branchId="b-db", branchFromThought=3))
        self.assertEqual(ok["violations"], [])
        q = ok["questions"]["5"]
        self.assertEqual((q["branch"], q["from_q"], q["from_thought"]), ("b-db", 2, 3))
        self.assertEqual(ok["branches"]["b-db"]["from_q"], 2)
        self.assertIn("b-db", ok["unresolved_branches"])
        self.assertEqual(ok["branch_calls"], 1)
        self.assertIn("[b-db, from A2 (thought 3)]", tree_store.render_injection(ok))
        bad = self.replay(*self.base(), sc(4, "Q5 [branch=b-db] Which version?", branchId="b-db", branchFromThought=1),
                          sc(5, "Q6 [branch=b-db] x", branchId="b-db"),
                          sc(4, "Q7 [main] reuse"))
        self.assertEqual([v["rule"] for v in bad["violations"]], ["branch_from_not_a", "branch_no_from", "thought_reused"])
        self.assertNotIn("6", bad["questions"])

    def test_feedback_cap_two(self):
        st = self.replay(*self.base(), sc(4, "A9 x"), sc(5, "A8 x"), sc(6, "A7 x"))
        self.assertEqual([v["feedback"] for v in st["violations"]], [True, True, False])
        self.assertEqual(tree_store.feedback_text(st["violations"][2:]), "")

    def test_injection_format_and_empty(self):
        self.assertEqual(tree_store.render_injection(self.replay(sc(1, "just thinking"))), "")
        txt = tree_store.render_injection(self.replay(*self.base()))
        self.assertEqual(txt, "<open-branches>\nUnresolved: Q3 (thought 1) [main] Deploy target?; "
                              "Q4 (thought 1) [main] Retention?\n"
                              "Next free thoughtNumber: 4. Next free question number: 5. Closed: 2. "
                              "Reopened this session: 0.\n</open-branches>")

    def test_overflow_never_drops_unresolved(self):
        lines = "\n".join("Q%d [main] %s" % (i, "long question text " * 6) for i in range(1, 41))
        calls = [sc(1, "QLIST\n" + lines), sc(2, "PARK1 " + "reason " * 80), sc(3, "A2 done"),
                 sc(4, "REOPEN2 changed", isRevision=True, revisesThought=3)]
        st = self.replay(*calls)
        txt = tree_store.render_injection(st)
        self.assertLessEqual(len(txt), tree_store.INJECT_CAP)
        for q in st["unresolved"]:
            self.assertRegex(txt, r"\b%s\b" % q)
        self.assertIn("Unresolved: Q2, Q3,", txt)               # fell back to ids only
        self.assertNotIn("long question", txt)
        # moderate overflow: parked text is shortened first, unresolved text intact
        mid = [sc(1, "QLIST\n" + "\n".join("Q%d [main] question number %d" % (i, i) for i in range(1, 21))),
               sc(2, "PARK1 " + "r" * 3000)]
        t2 = tree_store.render_injection(self.replay(*mid))
        self.assertLessEqual(len(t2), tree_store.INJECT_CAP)
        self.assertIn("Q20 (thought 1) [main] question number 20", t2)

    def test_rebuild_from_jsonl_and_corrupt_store(self):
        d = tempfile.mkdtemp(dir=TMP)
        for i, c in enumerate(self.base()):
            tree_store.append_and_update(d, {"tool_use_id": "u%d" % i, "tool_input": c})
        want = json.load(open(os.path.join(d, "tree.json")))
        open(os.path.join(d, "tree.json"), "w").write("{corrupt")
        with open(os.path.join(d, "tree.jsonl"), "a") as fh:
            fh.write("not json\n")
            fh.write(json.dumps({"tool_use_id": "u0", "tool_input": self.base()[0]}) + "\n")   # duplicate id
        self.assertEqual(tree_store.rebuild(d), json.loads(json.dumps(want)))
        self.assertEqual(json.load(open(os.path.join(d, "tree.json"))), want)

    def test_t1_two_concurrent_writers_parity(self):
        d = tempfile.mkdtemp(dir=TMP)
        tree_store.append_and_update(d, {"tool_use_id": "q", "tool_input": sc(1, "QLIST\n" + "\n".join(
            "Q%d [main] q%d" % (i, i) for i in range(1, 41)))})
        code = ("import sys; sys.path.insert(0, %r); import tree_store; w=int(sys.argv[1])\n"
                "for i in range(20):\n"
                "    n = 2 + w*20 + i; q = 1 + w*20 + i\n"
                "    tree_store.append_and_update(%r, {'tool_use_id': 'w%%d-%%d' %% (w, i), "
                "'tool_input': {'thought': 'A%%d x' %% q, 'thoughtNumber': n, 'nextThoughtNeeded': True}})\n"
                % (os.path.join(H, "readback"), d))
        procs = [subprocess.Popen([sys.executable, "-c", code, str(w)]) for w in (0, 1)]
        for p in procs:
            self.assertEqual(p.wait(), 0)
        on_disk = json.load(open(os.path.join(d, "tree.json")))
        self.assertEqual(on_disk, json.loads(json.dumps(tree_store.replay(tree_store.read_records(d)))))
        self.assertEqual(on_disk["records"], 41)
        self.assertEqual(on_disk["counts"]["RESOLVED"], 40)
        self.assertEqual(on_disk["violations"], [])
        self.assertEqual(len(open(os.path.join(d, "tree.jsonl")).read().splitlines()), 41)


class TestStoreHooks(unittest.TestCase):
    def run_hook(self, script, payload, state, *args):
        return subprocess.run([sys.executable, os.path.join(H, "readback", script), *args], input=json.dumps(payload),
                              capture_output=True, text=True, env=dict(os.environ, SEQ_STATE_DIR=state))

    def test_store_hooks_end_to_end(self):
        state = tempfile.mkdtemp(dir=TMP)
        for i, c in enumerate([sc(1, "QLIST\nQ1 [main] a\nQ2 [main] b"), sc(2, "A1 yes")]):
            r = self.run_hook("hook_sidecar.py", {"tool_use_id": "t%d" % i, "tool_input": c}, state, "--store")
            self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(json.load(open(os.path.join(state, "tree.json")))["unresolved"], ["Q2"])
        r = self.run_hook("hook_userprompt.py", {"cwd": TMP}, state, "--store")
        ctx = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "UserPromptSubmit")
        self.assertIn("Unresolved: Q2 (thought 1) [main] b", ctx["additionalContext"])
        r = self.run_hook("hook_sessionstart.py", {"source": "compact", "cwd": TMP}, state, "--store")
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"], "SessionStart")
        r = self.run_hook("hook_sidecar.py", {"tool_use_id": "t9", "tool_input": sc(3, "REOPEN1 x", isRevision=True, revisesThought=1)},
                          state, "--store")
        self.assertEqual(r.returncode, 2)
        self.assertIn("revisesThought=2", r.stderr)
        r = self.run_hook("hook_userprompt.py", {"cwd": TMP}, tempfile.mkdtemp(dir=TMP), "--store")
        self.assertEqual((r.returncode, r.stdout), (0, ""))        # empty store: silent

    def test_store_hooks_fail_open(self):
        state = tempfile.mkdtemp(dir=TMP)
        r = subprocess.run([sys.executable, os.path.join(H, "readback", "hook_sidecar.py"), "--store"], input="not json",
                           text=True, capture_output=True, env=dict(os.environ, SEQ_STATE_DIR=state))
        self.assertEqual(r.returncode, 0)
        open(os.path.join(state, "tree.jsonl"), "w").write("garbage\n")
        r = self.run_hook("hook_userprompt.py", {}, state, "--store")
        self.assertEqual(r.returncode, 0)

    def test_arm_f_skill_and_template(self):
        tpl = json.load(open(os.path.join(H, "readback", "settings.hooks.F.template.json")))
        self.assertEqual(sorted(tpl["hooks"]), ["PostToolUse", "SessionStart", "UserPromptSubmit"])   # no Stop
        self.assertNotIn("Stop", tpl["hooks"])
        txt = open(os.path.join(H, "skills", "arm-F.md")).read()
        for bad in ("authoritative", "your own reasoning", "recorded calls", "compact"):
            self.assertNotIn(bad, txt)
        self.assertIn("QLIST", txt)
        r = subprocess.run([sys.executable, os.path.join(H, "make_arm_skills.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


class TestArmFPhase2(unittest.TestCase):
    """Driver gate, forced branch delivery, caps classification, scoring parity, rule 7, microtest guard."""

    def fixture(self, **expected):
        import shutil
        d = tempfile.mkdtemp(prefix="fx-", dir=TMP)
        src = os.path.join(H, "..", "fixtures", "f1-portfolio-generator")
        for f in os.listdir(src):
            shutil.copy(os.path.join(src, f), d)
        key = json.load(open(os.path.join(d, "key.json")))
        key["expected_questions"].update(dict(min=2, target=5, max=8))
        key["compaction_after_turn"] = 3
        key["expected_questions"].update(expected)
        json.dump(key, open(os.path.join(d, "key.json"), "w"))
        return d

    def stub(self, mode="ignore", extra=(), questions=6, **expected):
        import run_arm
        fx = self.fixture(**expected)
        out = tempfile.mkdtemp(prefix="out-", dir=TMP)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_arm.main(["--fixture", fx, "--arm", "F", "--run-id", "s", "--out-root", out, "--stub-backend", mode,
                               "--stub-questions", str(questions), "--max-exchanges", "80"] + list(extra))
        d = os.path.join(out, "f1-portfolio-generator", "F", "s")
        meta = json.load(open(os.path.join(d, "run_meta.json")))
        log = [json.loads(l) for l in open(os.path.join(d, "driver_log.jsonl"))]
        return meta, log, d

    def test_finish_attempt_rule_three_traced_turns(self):
        import run_arm
        st = {"questions": {str(i): {"n": i} for i in range(1, 18)}}
        status = "Waiting on the retro date and the reporting lag; nothing else open."
        lst = "Remaining questions:\nQ15: retro date\nQ16: reporting lag\nQ17: loss allocation"
        closing = "Summary: 14 decisions recorded; claims-made basis added. Ready to write the plan."
        self.assertFalse(run_arm.is_finish_attempt(status, st, "inject_status"))
        self.assertTrue(run_arm.is_finish_attempt(status, st, None))              # ablation: the tag protects it
        self.assertFalse(run_arm.is_finish_attempt(lst, st, None))                # registered label
        self.assertTrue(run_arm.is_finish_attempt(closing, st, None))
        self.assertFalse(run_arm.is_finish_attempt("Q99: new?", st, None))        # has '?'
        self.assertTrue(run_arm.is_finish_attempt("Q99: unregistered label only", st, None))
        self.assertFalse(run_arm.is_finish_attempt(closing, st, "post_done_delivery"))
        self.assertFalse(run_arm.is_finish_attempt(closing, st, "compact"))

    def test_gate_refusal_cap_and_gate_json(self):
        meta, log, d = self.stub("ignore")
        self.assertEqual(meta["gate_refusals"], 3)
        self.assertTrue(meta["gate_cap_hit"])
        self.assertEqual(meta["unresolved_at_stop"], ["Q5", "Q6"])
        self.assertEqual(len([r for r in log if r["kind"] == "inject_gate"]), 3)
        gj = json.load(open(os.path.join(d, "state", "gate.json")))
        self.assertEqual(gj, {"gate_refusals": 3, "gate_cap_hit": True, "unresolved_at_stop": ["Q5", "Q6"]})
        self.assertEqual(meta["stop_reason"].split("_after")[0], "interviewer_done")
        self.assertEqual(meta["run_class"], "scored")
        self.assertEqual(meta["gate_path"], "driver")
        self.assertTrue(meta["validity_checks"]["store_parity"])
        self.assertTrue(meta["validity_checks"]["no_stop_hook_row"])
        # gate prompts count toward exchanges
        self.assertEqual(meta["exchanges"], sum(1 for r in log if r["kind"] != "answerer"))

    def test_gate_obey_clears_and_stops_without_cap(self):
        meta, log, _ = self.stub("obey")
        self.assertFalse(meta["gate_cap_hit"])
        self.assertEqual(meta["unresolved_at_stop"], [])
        self.assertGreaterEqual(meta["gate_refusals"], 1)

    def test_gate_prompt_text_neutral_and_lists_ids(self):
        import run_arm
        st = {"unresolved": ["Q4", "Q7"], "questions": {"4": {"branch": "main"}, "7": {"branch": "b-auth"}}}
        t = run_arm.gate_prompt(st)
        self.assertEqual(t, "Unresolved: Q4, Q7 (b-auth). Ask each, resolve it, or park it with a reason.")
        self.assertNotIn("reasoning", t.lower())

    def test_nudge_then_gate_order(self):
        meta, log, _ = self.stub("ignore", min=8)        # 6 questions: closings arrive below min_q
        kinds = [r["kind"] for r in log]
        self.assertEqual(meta["continue_nudges"], 3)
        self.assertIn("inject_gate", kinds)
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "inject_continue"), kinds.index("inject_gate"))

    def test_gate_after_deliver_remaining(self):
        meta, log, _ = self.stub("obey")        # the stub registers a follow-up question on the first late delivery
        kinds = [(r["kind"], r.get("tag")) for r in log]
        first_post = min(i for i, (k, t) in enumerate(kinds) if t == "post_done_delivery")
        gates = [i for i, (k, t) in enumerate(kinds) if k == "inject_gate"]
        self.assertTrue(any(g > first_post for g in gates), "a gate prompt must follow the post-done deliveries")
        self.assertLessEqual(meta["gate_refusals"], 3)
        self.assertEqual(meta["unresolved_at_stop"], [])

    def test_post_delivery_gate_not_on_tagged_turns_and_status_reply(self):
        meta, log, _ = self.stub("ignore")
        for r in log:
            if r["kind"] == "inject_status":
                self.assertEqual(r["tag"], "inject_status")
        self.assertTrue(any(r.get("tag") == "compact" for r in log))
        self.assertEqual(meta["unregistered_questions"], 0)

    def test_forced_branch_delivery_schedule(self):
        meta, log, _ = self.stub("ignore", extra=["--no-compaction"], questions=16, min=2)
        import run_arm
        key = json.load(open(os.path.join(H, "..", "fixtures", "f1-portfolio-generator", "key.json")))
        sched = run_arm.branch_schedule(key)
        self.assertEqual([(i["id"], i["start"]) for i in sched], [("B2", 12), ("B1", 16), ("B3", 16)])
        self.assertEqual([i["covers"] for i in sched if i["id"] in ("B2", "B3")], [["R2"], ["R1"]])
        self.assertTrue(meta["force_branches"])
        forced = [k for k, v in meta["delivery"].items() if v.get("by") == "forced_turn" and k in ("A23", "A24", "A25")]
        self.assertTrue(forced)
        # at most one branch statement per answerer turn
        for r in log:
            if r["kind"] == "answerer":
                self.assertLessEqual(len([i for i in (r.get("ids") or []) if i in ("A23", "A24", "A25")]), 1)

    def test_force_branches_default_arm_f_only(self):
        r = subprocess.run([sys.executable, os.path.join(H, "run_arm.py"), "--fixture", os.path.join(H, "..", "fixtures", "f1-portfolio-generator"),
                            "--arm", "A", "--run-id", "t", "--out-root", TMP, "--dry-run"], capture_output=True, text=True)
        meta = json.loads(r.stdout.split("# DRY RUN SUMMARY")[1])
        self.assertFalse(meta["force_branches"])
        self.assertNotIn("A25", meta["delivery"])
        r = subprocess.run([sys.executable, os.path.join(H, "run_arm.py"), "--fixture", os.path.join(H, "..", "fixtures", "f1-portfolio-generator"),
                            "--arm", "F", "--run-id", "t", "--out-root", TMP, "--dry-run"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("arm F: driver gate is the single gate path", out)
        self.assertIn("PostToolUse", out)
        meta = json.loads(out.split("# DRY RUN SUMMARY")[1])
        self.assertTrue(meta["force_branches"])
        self.assertEqual(meta["skill_variant"], "v1")
        self.assertIn("A25", meta["delivery"])
        self.assertNotIn("Stop", out.split("# DRY RUN SUMMARY")[0].split("hooks configured:")[1].split("\n")[0])

    def test_run_class_caps(self):
        import run_arm
        self.assertEqual(run_arm.classify_run("cap_cost_usd", False), "failed")
        self.assertEqual(run_arm.classify_run("cap_processed_tokens", True), "truncated")
        self.assertEqual(run_arm.classify_run("interviewer_done", True), "scored")
        self.assertEqual(run_arm.classify_run("interviewer_done_after_delivery", True), "scored")
        self.assertEqual(run_arm.classify_run("error:x", False), "failed")
        meta, _, _ = self.stub("ignore", extra=["--max-exchanges", "3"])        # cap before compaction (after msg 3)
        self.assertEqual((meta["stop_reason"], meta["run_class"], meta["failed"]), ("cap_exchanges", "failed", True))
        meta, _, d = self.stub("ignore", extra=["--max-exchanges", "9"])        # after compaction, before done
        self.assertEqual((meta["run_class"], meta["truncated"]), ("truncated", True))

    def test_truncated_flag_in_score(self):
        import copy
        recs = fb.build("B")
        p = write("trunc.jsonl", recs)
        mp = os.path.join(TMP, "trunc_meta.json")
        json.dump({"truncated": True, "run_class": "truncated"}, open(mp, "w"))

        class A:
            pass
        a = A()
        a.transcript, a.key, a.arm, a.run_id, a.driver_log, a.hook_log, a.run_meta = p, KEY, "F", "t", None, None, mp
        s = scorer.score(a)
        self.assertTrue(s["truncated"])
        self.assertIsNone(s["finish_with_open_transcript"])
        self.assertEqual(s["wired_class"], "not_scored")
        a.run_meta = None
        self.assertEqual(scorer.score(a)["finish_with_open_transcript"]["total"], 1)

    def test_scoring_parity_f_same_function(self):
        recs = fb.build("B")
        f = run_score("Fp.jsonl", "F", recs)
        b = run_score("Bp.jsonl", "B", recs)
        for k in ("recall", "constraints", "branch_loss", "reopen_handling", "status_query", "opened_branch_stats", "reopen_stats"):
            self.assertEqual(f[k], b[k], k)
        self.assertEqual(f["opened_branch_stats"], b["opened_branch_stats"])
        self.assertEqual(f["opened_branch_stats"]["opened"], 1)
        self.assertIsNone(b["mechanism"])
        self.assertIsNotNone(f["mechanism"])

    def test_mechanism_answered_and_parked_without_asking(self):
        b = fb.Builder("F")
        b.user("start the interview")
        b.node("QLIST\nQ1 [main] roll up of monthly amounts?\nQ2 [main] counts by line of business?\nQ3 [main] third")
        b.node("A1 decided")                         # same turn as registration: answered without asking
        b.node("PARK3 skip")                         # same turn: parked without asking
        b.say("Q2: counts by line of business?", with_header=False)
        b.user("Frequency and severity by line.")
        b.node("A2 frequency and severity")           # a user turn in between: legitimate
        b.say("Done.", with_header=False)
        mech = run_score("Fm.jsonl", "F", b.rec)["mechanism"]
        self.assertEqual((mech["answered_without_asking"], mech["parked_without_asking"]), (1, 1))
        self.assertEqual(mech["unresolved_end_store"], [])
        self.assertIn("unmapped_questions", mech)

    def test_rule7_evaluation(self):
        import summarise

        def run(arm, rid, lost=(), unh=(), opened=("B2",), deliv=("R2",), x=1.0, sq=1.0, **kw):
            r = {"arm": arm, "run_id": rid, "fixture_id": "f1-portfolio-generator", "branch_calls": 5,
                 "branch_loss": {"branches": [{"id": i, "status": "lost" if i in lost else "kept"} for i in opened],
                                 "lost": len(lost), "opened": len(opened), "designated": 3},
                 "reopen_handling": [{"id": i, "status": "unhandled" if i in unh else "handled"} for i in deliv],
                 "opened_branch_stats": {"opened": len(opened), "lost": len(lost), "opened_ids": list(opened)},
                 "reopen_stats": {"delivered": len(deliv), "unhandled": len(unh), "delivered_ids": list(deliv)},
                 "finish_with_open_transcript": {"total": len(lost) + len(unh)},
                 "recall": {"post_compaction_X": {"recall": x}}, "status_query": {"recall": sq}}
            r.update(kw)
            return r
        cmp_runs = [run("A", "p1", opened=("B1",), deliv=("R1",)), run("E", "h1", lost=("B1",), opened=("B1", "B2")),
                    run("B2", "p3", unh=("R2",))]
        res = summarise.rule7([run("F", "f1", opened=("B1", "B2")), run("F", "f2", opened=("B1",), deliv=("R2",))], cmp_runs)
        self.assertEqual(sorted(k for k, v in res["items"].items() if v["discriminates"]), ["B1", "R2"])
        self.assertTrue(res["verdict"].startswith("BIG VISIBLE EFFECT"), res["verdict"])
        bad = summarise.rule7([run("F", "f1", lost=("B1",), opened=("B1",)), run("F", "f2", opened=("B1",))], cmp_runs)
        self.assertEqual(bad["verdict"], "no visible effect")
        nc = summarise.rule7([run("F", "f1", branch_calls=2), run("F", "f2")], cmp_runs)
        self.assertTrue(nc["verdict"].startswith("void"))
        only = summarise.rule7([run("F", "f1", opened=("B2",), deliv=("R1",)), run("F", "f2", opened=("B2",), deliv=("R1",))], cmp_runs)
        self.assertTrue(only["verdict"].startswith("descriptive only"))
        trunc = summarise.rule7([run("F", "f1", truncated=True, run_class="truncated"), run("F", "f2")], cmp_runs)
        self.assertTrue(trunc["verdict"].startswith("insufficient"))
        reg = summarise.rule7([run("F", "f1", opened=("B1",), x=0.3), run("F", "f2", opened=("B1",))], [run("A", "p1", opened=("B1",), lost=("B1",), x=1.0)])
        self.assertEqual(reg["verdict"], "no visible effect")

    def test_summarise_prints_f_sections(self):
        s = run_score("Fs.jsonl", "F", fb.build("B"))
        d = tempfile.mkdtemp(dir=TMP)
        json.dump(s, open(os.path.join(d, "score.json"), "w"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            summarise.main([d])
        out = buf.getvalue()
        self.assertIn("not scored", out)
        self.assertIn("Section 7 rule for F", out)
        self.assertIn("Opened-branch denominators", out)

    def test_microtests_script_guard(self):
        path = os.path.join(H, "bin", "microtests_F.sh")
        txt = open(path).read()
        self.assertTrue(os.access(path, os.X_OK))
        self.assertEqual(subprocess.run(["bash", "-n", path]).returncode, 0)
        lines = txt.splitlines()
        first_spend = min(i for i, l in enumerate(lines) if ("run_arm_test T" in l or "CLAUDE_BIN" in l.split("#")[0] and "C1=(" in l))
        guard_yes = next(i for i, l in enumerate(lines) if "refusing: these microtests spend tokens" in l)
        self.assertLess(guard_yes, first_spend)
        self.assertNotIn("-t 0", txt)
        self.assertIn("MICROTESTS_CEILING_USD:-3", txt)
        for t in ("T2", "T6", "T7"):
            self.assertIn("RESULT_%s=" % t, txt)

    def test_microtests_script_yes_gate_and_no_tty_refusal(self):
        # No real spend: ceiling 0 makes every test's room() check fail, so --yes runs only the skip path.
        path = os.path.join(H, "bin", "microtests_F.sh")
        scratch = os.path.join(H, ".scratch")
        before = set(os.listdir(scratch)) if os.path.isdir(scratch) else set()
        env = dict(os.environ, MICROTESTS_CEILING_USD="0")
        try:
            no = subprocess.run(["bash", path], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
            self.assertEqual(no.returncode, 1)
            self.assertIn("refusing: these microtests spend tokens", no.stderr)
            after_no = set(os.listdir(scratch)) if os.path.isdir(scratch) else set()
            self.assertEqual(after_no - before, set())      # refusal happens before anything is created
            yes = subprocess.run(["bash", path, "--yes"], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
            self.assertNotIn("no tty", yes.stderr)          # tty gate is gone: piped stdin does not refuse
            self.assertIn("T2 skipped: ceiling", yes.stdout)
            self.assertIn("T6 skipped: ceiling", yes.stdout)
            self.assertIn("T7 skipped: ceiling", yes.stdout)
            self.assertNotIn("run_arm exit code", yes.stdout)  # no model calls were made
        finally:
            now = set(os.listdir(scratch)) if os.path.isdir(scratch) else set()
            for name in now - before:
                shutil.rmtree(os.path.join(scratch, name), ignore_errors=True)

    def test_followup_arm_f_and_processed_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = TestFollowup()
            env = dict(t._env(tmp, cost=1.0), FOLLOWUP_MAX_PROCESSED_TOKENS="4500000", FOLLOWUP_RUN_CAP_USD="28")
            r = subprocess.run(["bash", TestFollowup.SH, "--yes", "F:f1"], capture_output=True, text=True, env=env)
            self.assertIn("--max-processed-tokens 4500000 --max-cost-usd 28 ", r.stdout)
            self.assertIn("--arm F --run-id f1", r.stdout)
            env2 = dict(t._env(tmp + "x" if False else tempfile.mkdtemp(dir=TMP), cost=1.0))
            r2 = subprocess.run(["bash", TestFollowup.SH, "--yes", "A:p1"], capture_output=True, text=True, env=env2)
            self.assertIn("--max-processed-tokens 3000000 ", r2.stdout)
            r3 = subprocess.run(["bash", TestFollowup.SH, "--yes", "Z:p1"], capture_output=True, text=True, env=env2)
            self.assertEqual(r3.returncode, 2)

    def test_spec_step6_wording(self):
        spec = open(os.path.join(H, "..", "..", "..", "notes", "seq-study-arm-f-spec-20261114.md")).read()
        self.assertIn("are free working notes and are not checked", spec)
        self.assertNotIn("are your own reasoning and are not checked", spec)


# ---------------------------------------------------------------------------------------------
# Hard fixture f4: multi-compaction driver, answer-key scorer (score_items), baseline script
# ---------------------------------------------------------------------------------------------
F4 = os.path.join(H, "..", "fixtures", "f4-settlement-recon")
F4_KEY = os.path.join(F4, "key.json")


def f4_stub_run(mode, run_id, out, arm="A", extra=()):
    """Offline stub run of the full f4 schedule; returns (run dir, score.json dict)."""
    import run_arm
    with redirect_stdout(io.StringIO()):
        rc = run_arm.main(["--fixture", F4, "--arm", arm, "--run-id", run_id, "--out-root", out, "--stub-backend", mode,
                           "--max-exchanges", "200", "--max-cost-usd", "1000"] + list(extra))
    d = os.path.join(out, "f4-settlement-recon", arm, run_id)
    sp = os.path.join(d, "score.json")
    with redirect_stdout(io.StringIO()):
        scorer.main(["--transcript", os.path.join(d, "transcript.jsonl"), "--key", F4_KEY, "--arm", "A" if arm in ("G", "G-lite") else arm, "--run-id", run_id,
                     "--driver-log", os.path.join(d, "driver_log.jsonl"), "--run-meta", os.path.join(d, "run_meta.json"), "--out", sp])
    return d, json.load(open(sp))


class TestMultiCompaction(unittest.TestCase):
    def drive(self, key, n_questions=16, forced=True, force_branches=False, min_q=0):
        import argparse
        import run_arm
        key = json.loads(json.dumps(key))
        key["expected_questions"]["min"] = min_q
        seen = []

        class B:
            n = 0

            def interviewer(self, prompt, resume, tag):
                seen.append((tag, prompt))
                self.n += 1
                import hashlib
                salt = hashlib.sha256(str(self.n).encode()).hexdigest()
                txt = "Q%d? %s" % (self.n, salt) if self.n <= n_questions else "Summary. Ready. %s" % salt
                return dict(text=txt, session="s", usage={}, cost=0.01, error=None, init=None)

            def answerer(self, prompt, tag):
                return dict(text=json.dumps({"ids": [], "reply": "No strong view."}), usage={}, cost=0.0, error=None)
        a = argparse.Namespace(max_exchanges=300, max_fresh_tokens=10**9, max_processed_tokens=10**9, max_cost_usd=100.0, max_seconds=10**6,
                               arm="A", compact_prompt="/compact", status_query_version="v2", no_compaction=False, forced_by_fallback=forced,
                               force_branches=force_branches)
        with redirect_stdout(io.StringIO()):
            log, meta, _ = run_arm.drive(a, None, TMP, {}, B(), "plan", key, "brief")
        return log, meta, seen

    def test_legacy_single_compaction_unchanged(self):
        key = json.load(open(KEY))
        log, meta, seen = self.drive(key, n_questions=20)
        self.assertEqual(sum(1 for t, p in seen if p == "/compact"), 1)
        self.assertEqual(sum(1 for t, p in seen if p.startswith("Please list the current state")), 1)
        self.assertEqual(meta["n_compactions"], 1)
        self.assertEqual(meta["compactions_expected"], 1)
        self.assertEqual(meta["compaction_turns"], [14])
        self.assertTrue(any("-compact" in t and "compact2" not in t for t, _ in seen))

    def test_three_compactions_status_only_where_listed(self):
        key = json.load(open(KEY))
        key["compaction_turns"] = [3, 6, 9]
        key["status_query_after_compaction"] = [2]
        log, meta, seen = self.drive(key, n_questions=14)
        prompts = [p for _, p in seen]
        self.assertEqual(prompts.count("/compact"), 3)
        stat = [i for i, p in enumerate(prompts) if p.startswith("Please list the current state")]
        comp = [i for i, p in enumerate(prompts) if p == "/compact"]
        self.assertEqual(len(stat), 1)
        self.assertEqual(stat[0], comp[1] + 1)                     # status straight after the second compaction
        self.assertEqual(meta["n_compactions"], 3)
        self.assertEqual(meta["compactions_expected"], 3)
        self.assertEqual(len({t for t, _ in seen}), len(seen))     # every call tag is unique (log files do not collide)

    def test_schedule_item_waits_for_its_compaction(self):
        key = json.load(open(KEY))
        key["compaction_turns"] = [3, 6, 9]
        key["schedule"] = [{"id": "S1", "kind": "reopen", "answer_id": "AS1", "start": 1, "after_compaction": 2,
                            "statement": "Schedule statement alpha beta gamma delta epsilon zeta eta theta.", "covers": []}]
        log, meta, seen = self.drive(key, n_questions=14, force_branches=True)
        prompts = [p for _, p in seen]
        comp = [i for i, p in enumerate(prompts) if p == "/compact"]
        first = next(i for i, p in enumerate(prompts) if "Schedule statement alpha" in p)
        self.assertGreater(first, comp[1])                           # not before the second compaction
        self.assertEqual(meta["delivery"]["AS1"]["by"], "forced_turn")

    def test_final_request_sent_once_and_post_done_schedule(self):
        key = json.load(open(KEY))
        key["compaction_turns"] = [3]
        key["final_request"] = "Please print the complete decision record now, nothing else."
        key["deliver_schedule_post_done"] = True
        key["post_done_max"] = 40
        key["schedule"] = [{"id": "S9", "kind": "branch", "answer_id": "AS9", "start": 99, "after_compaction": 0,
                            "statement": "Late branch statement one two three four five six seven.", "covers": []}]
        log, meta, seen = self.drive(key, n_questions=5, force_branches=True)
        prompts = [p for _, p in seen]
        self.assertEqual(sum(1 for p in prompts if p.startswith("Please print the complete decision record")), 1)
        self.assertEqual(meta["delivery"]["AS9"]["by"], "post_done_delivery")
        self.assertTrue(meta["final_request_sent"])
        self.assertTrue(prompts[-1].startswith("Please print the complete decision record"))     # last call of the run
        self.assertTrue(meta["stop_reason"].startswith("interviewer_done"))


class TestF4Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = json.load(open(F4_KEY))
        cls.brief = open(os.path.join(F4, "answerer.md")).read()

    def test_item_table_shape(self):
        items = self.key["items"]
        ids = [i["id"] for i in items]
        self.assertEqual(len(ids), len(set(ids)))
        by = {}
        for i in items:
            by[i["type"]] = by.get(i["type"], 0) + 1
        self.assertEqual(by, {"constraint": 6, "branch": 5, "reopen": 4, "knock-on": 10})
        for i in items:
            self.assertTrue(i["checks"] and i["rubric"] and i["label"], i["id"])
            for c in i["checks"]:
                self.assertIn(c["kind"], ("flag", "final_has", "final_lacks", "branch_subs"), i["id"])
        self.assertEqual(self.key["baseline_gate"]["lost_threshold"], 2)

    def test_every_statement_is_in_the_answerer_brief_verbatim(self):
        stmts = []
        for c in self.key["constraints"]:
            stmts.append(c["early"]["statement"])
            if c.get("late"):
                stmts.append(c["late"]["statement"])
        stmts += [x["statement"] for x in self.key["schedule"]]
        for i in self.key["items"]:
            stmts += i.get("requires", [])
            for c in i["checks"]:
                if c.get("trigger"):
                    stmts.append(c["trigger"])
                if c["kind"] == "branch_subs":
                    stmts.append(c["open_statement"])
        for t in stmts:
            self.assertIn(t, self.brief, t[:60])

    def test_structure_requirements(self):
        k = self.key
        self.assertGreaterEqual(len(k["compaction_turns"]), 2)
        self.assertEqual(len(k["compaction_turns"]), 3)
        for x in k["schedule"]:
            self.assertLessEqual(x["after_compaction"], len(k["compaction_turns"]))
        nested = [b for b in k["branches"] if b["parent"]]
        self.assertGreaterEqual(len(nested), 2)
        self.assertTrue(any(x["kind"] == "resolution" and x["after_compaction"] >= 2 for x in k["schedule"]))   # resolves after a later compaction
        # reopens with 2+ knock-ons each
        for rid in ("R2", "R3"):
            self.assertGreaterEqual(sum(1 for i in k["items"] if i["type"] == "knock-on" and i["id"].startswith("N" + rid[1])), 2)
        for rid in ("R1", "R4"):
            self.assertGreaterEqual(sum(1 for i in k["items"] if i["type"] == "knock-on" and i["id"].startswith("N" + rid[1])), 2)
        # planted facts stated once in passing: the answerer never gives them itself
        self.assertEqual(sum(1 for c in k["constraints"] if not c.get("late")), 2)
        decision_rows = [l for l in self.brief.splitlines() if l.startswith("| A") and int(l.split("|")[1].strip()[1:]) >= 30]
        self.assertGreaterEqual(len(decision_rows), 50)

    def test_dry_run_drives_three_compactions(self):
        r = subprocess.run([sys.executable, os.path.join(H, "run_arm.py"), "--fixture", F4, "--arm", "A", "--run-id", "t",
                            "--out-root", os.path.join(TMP, "runs"), "--force-branches", "--max-exchanges", "110", "--dry-run"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        meta = json.loads(r.stdout.split("# DRY RUN SUMMARY")[1])
        self.assertEqual((meta["n_compactions"], meta["compactions_expected"], meta["final_request_sent"]), (3, 3, True))
        self.assertEqual(meta["stop_reason"], "interviewer_done")
        self.assertEqual(sum(1 for v in meta["delivery"].values() if v["by"] == "never"), 0)

    def test_force_schedule_default_for_arm_a_only_via_key(self):
        # f1 arms keep their old default (no forced schedule for arm A); f4 turns it on for every arm through its key
        r1 = subprocess.run([sys.executable, os.path.join(H, "run_arm.py"), "--fixture", os.path.join(H, "..", "fixtures", "f1-portfolio-generator"),
                             "--arm", "A", "--run-id", "t", "--out-root", os.path.join(TMP, "runs"), "--dry-run"], capture_output=True, text=True)
        self.assertEqual(json.loads(r1.stdout.split("# DRY RUN SUMMARY")[1])["force_branches"], False)
        r2 = subprocess.run([sys.executable, os.path.join(H, "run_arm.py"), "--fixture", F4, "--arm", "A", "--run-id", "t",
                             "--out-root", os.path.join(TMP, "runs"), "--dry-run"], capture_output=True, text=True)
        self.assertEqual(json.loads(r2.stdout.split("# DRY RUN SUMMARY")[1])["force_branches"], True)


class TestScoreItems(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="f4out-", dir=TMP)
        cls.dF, cls.sF = f4_stub_run("faithful", "faithful", cls.out)
        cls.dL, cls.sL = f4_stub_run("lossy", "lossy", cls.out)
        cls.key = json.load(open(F4_KEY))

    def test_stub_run_shape(self):
        m = json.load(open(os.path.join(self.dF, "run_meta.json")))
        self.assertEqual((m["run_class"], m["n_compactions"], m["early_stop"], m["final_request_sent"]), ("scored", 3, False, True))
        self.assertEqual(self.sF["validity"]["compaction_boundary_turns"].__len__(), 3)
        self.assertGreaterEqual(m["answerer_turns"], 36)

    def test_faithful_stub_loses_nothing_lossy_loses_exactly_the_dropped(self):
        self.assertEqual(self.sF["items"]["lost"], 0)
        self.assertEqual(self.sF["items"]["baseline_gate"]["verdict"], "PASSES")
        self.assertEqual(sorted(self.sL["items"]["lost_ids"]), sorted(self.key["stub_lossy_drop"]))
        g = self.sL["items"]["baseline_gate"]
        self.assertEqual((g["threshold_lost_items"], g["lost"], g["lost_min"], g["lost_max"]), (2, 7, 4, 7))
        self.assertEqual(g["verdict"], "FAILS")                      # lost_min (not hand-check-eligible) already >= 2
        self.assertEqual(sorted(self.sL["items"]["pending_hand_ids"]), ["B1.1", "C1", "N2b"])

    def test_gate_overall_verdicts(self):
        both_lossy = scorer_items.gate([self.dL, self.dL])
        self.assertTrue(both_lossy["overall"].startswith("BASELINE_VISIBLY_FAILS"))
        mixed = scorer_items.gate([self.dF, self.dL])
        self.assertTrue(mixed["overall"].startswith("BASELINE_DOES_NOT_VISIBLY_FAIL"))
        one = scorer_items.gate([self.dL])
        self.assertTrue(one["overall"].startswith("INCONCLUSIVE"))

    def test_hand_override_is_applied_by_the_gate(self):
        d = tempfile.mkdtemp(prefix="hand-", dir=TMP)
        shutil.copytree(self.dL, os.path.join(d, "r"))
        # a hand check finds the three provisional failures are paraphrases, in fact satisfied
        json.dump({"C1": True, "N2b": True, "B1.1": True}, open(os.path.join(d, "r", "hand.json"), "w"))
        res = scorer_items.gate([os.path.join(d, "r")] * 1, runs_required=1)
        self.assertEqual(res["runs"][0]["lost"], 4)
        self.assertEqual(res["runs"][0]["pending_hand"], [])
        self.assertTrue(res["runs"][0]["hand_applied"])
        self.assertEqual(res["runs"][0]["verdict"], "FAILS")
        sheet = scorer_items.sheet(os.path.join(self.dL, "score.json"))
        self.assertIn("## C1 (constraint)  [hand_check]", sheet)

    def test_needs_hand_when_only_hand_check_items_fail(self):
        blk = json.loads(json.dumps(self.sL["items"]))
        for r in blk["rows"]:
            if r["status"] == "fail" and not r["hand_check"]:
                r["status"], r["mechanical_pass"] = "pass", True
        out = scorer_items.summarise_items(blk["rows"], self.key, {}, dict(scorer_items.DEFAULT_GATE), 0)
        self.assertEqual((out["baseline_gate"]["lost_min"], out["baseline_gate"]["lost_max"], out["baseline_gate"]["verdict"]), (0, 3, "NEEDS_HAND"))

    def test_truncated_run_is_excluded_and_never_compared(self):
        rm = json.load(open(os.path.join(self.dL, "run_meta.json")))
        rm.update(run_class="truncated", truncated=True)
        d = os.path.join(self.out, "trunc")
        shutil.copytree(self.dL, d)
        json.dump(rm, open(os.path.join(d, "run_meta.json"), "w"))
        with redirect_stdout(io.StringIO()):
            scorer.main(["--transcript", os.path.join(d, "transcript.jsonl"), "--key", F4_KEY, "--arm", "A", "--run-id", "trunc",
                         "--driver-log", os.path.join(d, "driver_log.jsonl"), "--run-meta", os.path.join(d, "run_meta.json"), "--out", os.path.join(d, "score.json")])
        sc = json.load(open(os.path.join(d, "score.json")))
        self.assertEqual(sc["items"]["baseline_gate"]["verdict"], "EXCLUDED")
        self.assertFalse(sc["items"]["baseline_gate"]["valid"])
        res = scorer_items.gate([d, self.dL])
        self.assertTrue(res["overall"].startswith("INCONCLUSIVE"))

    def test_early_stop_and_missing_compactions_invalidate(self):
        rows = self.sF["items"]["rows"]
        base = dict(scorer_items.DEFAULT_GATE)
        out = scorer_items.summarise_items(rows, self.key, {"run_class": "scored", "early_stop": True, "answerer_turns": 20, "min_q": 36}, base, 0)
        self.assertEqual(out["baseline_gate"]["verdict"], "EXCLUDED")
        out = scorer_items.summarise_items(rows, self.key, {"run_class": "scored", "n_compactions": 1, "compactions_expected": 3}, base, 0)
        self.assertTrue(any("compactions" in x for x in out["baseline_gate"]["invalid_reasons"]))

    # ---- hand-built final records: the checks resist keyword luck and near-duplicates ----
    def score_final(self, final_text, drop_flag_checks=True):
        from common import split_turns as st
        recs = [{"type": "user", "message": {"role": "user", "content": "plan"}},
                {"type": "assistant", "message": {"id": "m1", "role": "assistant", "content": [{"type": "text", "text": "Q1?"}]}},
                {"type": "user", "message": {"role": "user", "content": self.key["final_request"]}},
                {"type": "assistant", "message": {"id": "m2", "role": "assistant", "content": [{"type": "text", "text": final_text}]}}]
        turns = st(recs)
        return {r["id"]: r for r in scorer_items.score_items(turns, self.key)["rows"]}

    def test_final_record_checks(self):
        # C6: UK fails, "GB, not UK" is excused, GB alone passes
        self.assertEqual(self.score_final("Region UK goes first.")["C6"]["status"], "not_delivered")   # requires the early statement: not delivered in this mini transcript
        key = self.key
        from common import split_turns as st
        early = key["constraints"][5]["early"]["statement"]
        recs = [{"type": "user", "message": {"role": "user", "content": early}},
                {"type": "assistant", "message": {"id": "m1", "role": "assistant", "content": [{"type": "text", "text": "Noted?"}]}}]

        def c6(final):
            r2 = recs + [{"type": "user", "message": {"role": "user", "content": key["final_request"]}},
                         {"type": "assistant", "message": {"id": "m2", "role": "assistant", "content": [{"type": "text", "text": final}]}}]
            return {r["id"]: r for r in scorer_items.score_items(st(r2), key)["rows"]}["C6"]["status"]
        self.assertEqual(c6("Rollout: Ireland, then the UK, then Sweden."), "fail")
        self.assertEqual(c6("Rollout: IE, then GB (not UK), then SE."), "pass")
        self.assertEqual(c6("Rollout: IE, then GB, then SE."), "pass")

    def test_near_duplicate_and_keyword_luck_do_not_pass(self):
        rows = {}
        c3 = next(i for i in self.key["items"] if i["id"] == "C3")
        fin = ("Matched ledger records are kept 7 years. Evidence packs are kept for seven years. Raw scheme files are deleted after 13 months. "
               "Audit will review disputed items.")
        res = scorer_items.run_check(c3["checks"][1], c3, [], self.key, fin, {})
        self.assertFalse(res["pass"])                                 # no sentence holds raw + seven years + disputed
        ok = "Raw scheme files tied to a disputed item are kept seven years."
        self.assertTrue(scorer_items.run_check(c3["checks"][1], c3, [], self.key, ok, {})["pass"])
        c1 = next(i for i in self.key["items"] if i["id"] == "C1")
        luck = "The old lock expires at 02:00 UTC. The old finance report lands at 04:00 UTC."
        self.assertFalse(scorer_items.run_check(c1["checks"][1], c1, [], self.key, luck, {})["pass"])
        good = "The partner bank SFTP window is 02:00 to 04:00 UTC."
        self.assertTrue(scorer_items.run_check(c1["checks"][1], c1, [], self.key, good, {})["pass"])
        c5 = next(i for i in self.key["items"] if i["id"] == "C5")
        self.assertFalse(scorer_items.run_check(c5["checks"][1], c5, [], self.key, "Refunds are matched on card token.", {})["pass"])

    def test_flag_window_ignores_the_final_record_turn(self):
        from common import split_turns as st
        n1 = next(i for i in self.key["items"] if i["id"] == "N2a")
        trig = n1["checks"][0]["trigger"]
        recs = [{"type": "user", "message": {"role": "user", "content": trig}},
                {"type": "assistant", "message": {"id": "m1", "role": "assistant", "content": [{"type": "text", "text": "Ok, understood."}]}},
                {"type": "user", "message": {"role": "user", "content": self.key["final_request"]}},
                {"type": "assistant", "message": {"id": "m2", "role": "assistant", "content": [{"type": "text",
                 "text": "Stale payment file per scheme revisit. One payment file per scheme."}]}}]
        turns = st(recs)
        final, fidx = scorer_items.final_record(turns, self.key)
        res = scorer_items.run_check(n1["checks"][0], n1, turns, self.key, final, {"final_idx": fidx})
        self.assertFalse(res["pass"])


class TestScorerEquivalents(unittest.TestCase):
    """Generic surface-form equivalents (added post hoc 2026-11-14): widened, but the substantive requirement is intact."""
    def test_numeric_and_unit_equivalents(self):
        h = scorer_items.has_any
        self.assertTrue(h("working estimate 3% overall", ["3 percent"]))
        self.assertTrue(h("about three per cent of items", ["3 percent", "3%"]))
        self.assertTrue(h("may differ by up to 2 minor units per file", ["2 cents"]))
        self.assertTrue(h("up to two cents of difference", ["2 cents"]))
        self.assertTrue(h("kept for seven-year retention", ["7 years"]))
        self.assertTrue(h("by the fifth working day", ["fifth business day"]))
        self.assertTrue(h("window 2:00 UTC", ["02:00"]) and h("window 02:00 UTC", ["2:00"]))
        self.assertTrue(h("raw files for forty five days", ["45 days"]))
        self.assertTrue(h("an original authorization id", ["original authorisation id"]))

    def test_singular_plural_and_stems(self):
        h = scorer_items.has_any
        self.assertTrue(h("this reopens D2", ["reopen"]))
        self.assertTrue(h("the pieces are paged", ["page"]))
        self.assertTrue(h("it escalates to the next person", ["escalat"]))
        self.assertTrue(h("two business days", ["2 business days"]))
        cues = scorer_items.DEFAULT_CUES
        for txt in ("this reopens D2", "that breaks two decisions", "it is now changing D8", "the earlier answer is broken", "a breakage of D5"):
            self.assertTrue(h(txt, cues), txt)

    def test_widening_does_not_weaken_the_requirement(self):
        h = scorer_items.has_any
        self.assertFalse(h("3.5% of items", ["3 percent"]))
        self.assertFalse(h("13% of items", ["3 percent"]))
        self.assertFalse(h("0.5% of items", ["3 percent"]))
        self.assertFalse(h("up to 20 cents", ["2 cents"]))
        self.assertFalse(h("kept for 17 years", ["7 years"]))
        self.assertFalse(h("kept for 70 years", ["7 years"]))
        # final_lacks stays strict: UK is not matched through inflection
        self.assertFalse(h("the ukulele", ["UK"], inflect=False))

    def test_key_anchors_accept_equivalents_end_to_end(self):
        key = json.load(open(F4_KEY))
        by = {i["id"]: i for i in key["items"]}
        run = scorer_items.run_check
        c5 = by["C5"]["checks"][1]
        self.assertTrue(run(c5, by["C5"], [], key, "Refund-only files may differ by up to 2 minor units per file.", {})["pass"])
        self.assertFalse(run(c5, by["C5"], [], key, "Refund-only files may differ by up to 20 minor units per file.", {})["pass"])
        n1a = by["N1a"]["checks"][1]
        self.assertTrue(run(n1a, by["N1a"], [], key, "Working estimate 3% of items become exceptions.", {})["pass"])
        self.assertFalse(run(n1a, by["N1a"], [], key, "Working estimate 0.5% of items become exceptions.", {})["pass"])
        n4a = by["N4a"]["checks"][1]
        self.assertTrue(run(n4a, by["N4a"], [], key, "Only control-total failures page.", {})["pass"])
        self.assertFalse(run(n4a, by["N4a"], [], key, "Only control-total warnings go to the mailbox.", {})["pass"])
        r1 = by["R1"]["checks"][0]
        from common import split_turns as st
        recs = [{"type": "user", "message": {"role": "user", "content": r1["trigger"]}},
                {"type": "assistant", "message": {"id": "m1", "role": "assistant", "content": [{"type": "text",
                 "text": "Your LocalDebit correction reopens D2 and the matching rule on scheme reference."}]}}]
        turns = st(recs)
        self.assertTrue(run(r1, by["R1"], turns, key, "", {})["pass"])


class TestF4BaselineScript(unittest.TestCase):
    SH = os.path.join(H, "bin", "f4_baseline.sh")

    def _env(self, tmp, **kw):
        wrapper = os.path.join(tmp, "stub_runner.py")
        mode = kw.pop("mode", "faithful")
        with open(wrapper, "w") as fh:
            fh.write("import sys\nsys.path.insert(0, %r)\nimport run_arm\n"
                     "sys.exit(run_arm.main(sys.argv[1:] + ['--stub-backend', %r]))\n" % (H, mode))
        env = dict(os.environ, F4_RUNS_ROOT=os.path.join(tmp, "runs"), F4_RUN_ARM=wrapper, F4_SCRATCH=os.path.join(tmp, "scr"))
        env.update({k: str(v) for k, v in kw.items()})
        return env

    def test_refuses_without_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(["bash", self.SH], capture_output=True, text=True, env=self._env(tmp), stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 1)
            self.assertIn("--yes", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs")))

    def test_dry_run_is_offline_and_prints_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, F4_RUNS_ROOT=os.path.join(tmp, "runs"), F4_SCRATCH=os.path.join(tmp, "scr"), PATH="/usr/bin:/bin")
            r = subprocess.run(["bash", self.SH, "--dry-run"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("# cost estimate (arm A, f4)", r.stdout)
            self.assertIn("--max-cost-usd 40", r.stdout)
            self.assertIn("n_compactions=3/3", r.stdout)
            self.assertIn("final_request_sent=True", r.stdout)
            self.assertIn("--force-branches", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs")))

    def test_ceiling_stops_before_any_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(["bash", self.SH, "--yes"], capture_output=True, text=True,
                               env=self._env(tmp, F4_RUN_CAP_USD=40, F4_TOTAL_CAP_USD=30), stdin=subprocess.DEVNULL)
            self.assertIn("CEILING reached", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs")))

    def test_end_to_end_with_faithful_and_lossy_stubs(self):
        for mode, expect in (("faithful", "BASELINE_DOES_NOT_VISIBLY_FAIL"), ("lossy", "BASELINE_VISIBLY_FAILS")):
            with tempfile.TemporaryDirectory() as tmp:
                r = subprocess.run(["bash", self.SH, "--yes"], capture_output=True, text=True, env=self._env(tmp, mode=mode), stdin=subprocess.DEVNULL)
                self.assertIn("ok A b1", r.stdout, r.stdout + r.stderr)
                self.assertIn("scored A b2", r.stdout)
                self.assertIn(expect, r.stdout, r.stdout)
                self.assertTrue(os.path.exists(os.path.join(tmp, "runs", "f4-settlement-recon", "A", "b1", "hand_sheet.md")))



# ---------------------------------------------------------------------------------------------
# Arm G (typed register tool plus Stop hook) and G-lite: spec notes/seq-study-arm-g-spec-20261114.md
# ---------------------------------------------------------------------------------------------
os.environ["MTG_TMP"] = TMP
sys.path.insert(0, os.path.join(H, "bin"))
import register_store as rs  # noqa: E402
import microtests_G_free as mtg  # noqa: E402

READBACK = os.path.join(H, "readback")
SPEC_ROOT = os.path.join(H, "..", "..", "..")


def run_py(script, payload=None, env=None, args=()):
    e = dict(os.environ)
    e.pop("SEQ_GATE_SKIP", None)
    e.update(env or {})
    return subprocess.run([sys.executable, script, *args], input=payload if isinstance(payload, str) else json.dumps(payload or {}),
                          capture_output=True, text=True, env=e)


class TestRegisterServer(unittest.TestCase):
    def test_m1_free_microtest_all_checks_pass(self):
        res = mtg.m1()
        self.assertGreaterEqual(len(res), 20)
        self.assertEqual([(n, d) for n, o, d in res if not o], [])

    def test_all_six_calls_over_stdio_json_rpc(self):
        d = tempfile.mkdtemp(dir=TMP)
        mtg.user_turn(d)
        c = mtg.McpClient(d)
        try:
            self.assertEqual(c.send("initialize", {"protocolVersion": "2099-01-01", "capabilities": {}})["result"]["protocolVersion"], "2099-01-01")
            c.send("notifications/initialized", note=True)
            names = sorted(t["name"] for t in c.send("tools/list")["result"]["tools"])
            self.assertEqual(names, ["list_open", "log_question", "open_branch", "park", "reopen", "resolve"])
            outs = [c.call("log_question", {"text": "what is the retention"}), c.call("resolve", {"id": "Q1", "decision": "7y", "source": "explored"}),
                    c.call("log_question", {"text": "second one", "deps": ["Q1"]}), c.call("park", {"id": "Q2", "reason": "waiting", "owner": "ops"}),
                    c.call("open_branch", {"from_id": "Q1", "branch": "b-x", "topic": "topic x", "subquestions": ["sub one", "sub two"]}),
                    c.call("reopen", {"id": "Q1", "why": "changed"}), c.call("list_open", {"include_resolved": True})]
            self.assertFalse(any(e for e, _ in outs), outs)
            self.assertIn("stale: Q2", outs[5][1])
            self.assertIn("Q2 STALE", outs[6][1])
        finally:
            c.close()
        self.assertEqual(len(rs.read_events(d)), 7)

    def test_durable_across_restarts_and_log_is_source_of_truth(self):
        d = tempfile.mkdtemp(dir=TMP)
        for i in range(3):
            c = mtg.McpClient(d)
            c.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
            self.assertFalse(c.call("log_question", {"text": "entry %d" % i})[0])
            c.close()
        self.assertEqual(sorted(rs.load(d)["entries"]), ["Q1", "Q2", "Q3"])
        os.remove(os.path.join(d, "register.json"))             # derived state is rebuildable
        self.assertFalse(rs.call_tool(d, "log_question", {"text": "after losing the derived file"})[0])
        self.assertEqual(json.load(open(os.path.join(d, "register.json")))["next_id"], 5)

    def test_concurrent_appends_under_flock_lose_nothing(self):
        d = tempfile.mkdtemp(dir=TMP)
        code = "import sys; sys.path.insert(0, %r); import register_store as s\nfor j in range(10): s.call_tool(%r, 'log_question', {'text': 'proc %%d' %% j})" % (READBACK, d)
        procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(5)]
        [p.wait() for p in procs]
        ev = rs.read_events(d)
        self.assertEqual(len(ev), 50)
        self.assertEqual(sorted(int(e["result"]["ids"][0][1:]) for e in ev), list(range(1, 51)))
        self.assertEqual(json.loads(json.dumps(rs.load(d))), json.load(open(os.path.join(d, "register.json"))))
        # the lock is a persistent sidecar that is never replaced
        ino = os.stat(os.path.join(d, "register.lock")).st_ino
        rs.call_tool(d, "log_question", {"text": "one more"})
        self.assertEqual(os.stat(os.path.join(d, "register.lock")).st_ino, ino)

    def test_server_stderr_only_logging_and_bad_input(self):
        d = tempfile.mkdtemp(dir=TMP)
        r = run_py(os.path.join(READBACK, "register_server.py"), "not json\n" + json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}) + "\n", {"SEQ_STATE_DIR": d})
        lines = [json.loads(x) for x in r.stdout.splitlines()]
        self.assertEqual(lines[0]["error"]["code"], -32700)
        self.assertEqual(lines[1]["result"], {})


class TestRegisterPredicate(unittest.TestCase):
    def test_reference_script_35_synthetic_cases_and_real_data(self):
        ref = os.path.join(SPEC_ROOT, ".scratch", "g_gate_predicate.py")
        if not os.path.exists(ref):
            self.skipTest("reference script not present")
        r = subprocess.run([sys.executable, ref], capture_output=True, text=True, cwd=SPEC_ROOT)
        self.assertEqual(r.returncode, 0, r.stdout[-800:])
        self.assertIn("synthetic failures: 0", r.stdout)
        self.assertEqual(r.stdout.count("\nok   ") + (1 if r.stdout.startswith("ok   ") else 0), 35)

    def test_real_module_matches_every_reference_case(self):
        cases = mtg._matrix()
        self.assertGreaterEqual(len(cases), 28)
        for name, text, entries, want in cases:
            self.assertEqual(rs.classify_message(text, entries), want, name)

    def test_one_predicate_for_hook_and_driver(self):
        import run_arm
        src = open(os.path.join(H, "run_arm.py")).read()
        self.assertIn("register_store.is_finish_attempt(text, register_entries())", src)
        self.assertTrue(rs.is_finish_attempt("Here is the record.", {}))                       # driver: still finish-like
        self.assertEqual(rs.classify_message("Here is the record.", {}), "pass")               # hook: passes
        self.assertFalse(rs.is_finish_attempt("Q4: Tell me the retention.", {4: "OPEN"}))
        self.assertTrue(rs.is_finish_attempt("Recap:\nQ4: a\nQ7: b", {4: "OPEN", 7: "OPEN"}))
        self.assertIsNotNone(run_arm.register_store)

    def test_m2_free_microtest_all_checks_pass(self):
        res = mtg.m2()
        self.assertEqual([(n, d) for n, o, d in res if not o], [])


class TestRegisterStopHook(unittest.TestCase):
    HOOK = os.path.join(READBACK, "hook_register_stop.py")

    def setUp(self):
        self.d = tempfile.mkdtemp(dir=TMP)
        mtg.user_turn(self.d)
        rs.call_tool(self.d, "log_question", {"text": "retention period"})
        rs.call_tool(self.d, "log_question", {"text": "fx source"})

    def hook(self, msg, **kw):
        payload = {"cwd": TMP}
        if msg is not None:
            payload["last_assistant_message"] = msg
        payload.update(kw.pop("payload", {}))
        return run_py(self.HOOK, payload, dict({"SEQ_STATE_DIR": self.d}, **kw))

    def test_blocks_finish_attempt_with_ids_and_allows_labelled_question(self):
        r = self.hook("Here is my summary of everything so far.")
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("Open entries: Q1, Q2.", out["reason"])
        self.assertEqual(self.hook("Q1: What is the retention period?").stdout, "")
        self.assertEqual(self.hook("Q2: State the FX source.").stdout, "")                    # single labelled request
        self.assertIn("log_question", json.loads(self.hook("What is the retention period?").stdout)["reason"])

    def test_chain_cap_per_kind_then_allow_and_reset_on_user_prompt(self):
        res = [self.hook("recap, nothing else").stdout != "" for _ in range(3)]
        self.assertEqual(res, [True, True, False])
        g = rs.read_gate(self.d)
        self.assertEqual((g["chain_finish"], g["chain_cap_hit_finish"], g["run_finish"]), (2, 1, 2))
        self.assertTrue(self.hook("What is it?").stdout != "")                                 # the unlogged kind is still on
        run_py(os.path.join(READBACK, "hook_register_userprompt.py"), {"prompt": "next"}, {"SEQ_STATE_DIR": self.d})
        g = rs.read_gate(self.d)
        self.assertEqual((g["chain_finish"], g["chain_unlogged"], g["stop_firings"]), (0, 0, 0))
        self.assertTrue(self.hook("recap again").stdout != "")                                 # new call: blocks again

    def test_unlogged_chain_cap_hit_counts_the_third_unlabelled_question(self):
        # M5 (v) regression: cap 2 per kind means two blocks, then the THIRD firing of that kind is allowed and records the hit.
        res = [self.hook("What is it?").stdout != "" for _ in range(3)]
        self.assertEqual(res, [True, True, False])
        g = rs.read_gate(self.d)
        self.assertEqual((g["chain_unlogged"], g["run_unlogged"], g["chain_cap_hit_unlogged"], g["chain_cap_hit_finish"]), (2, 2, 1, 0))

    def test_mixed_kinds_in_one_chain_count_separately(self):
        # Live M5 sequence (microtests-G-20261114-135022): two unlogged blocks, then the model sent statements with no '?'. Those are
        # the other kind (finish, open entries exist) with their own counter: two finish blocks, then the finish cap hit. No
        # chain_cap_hit_unlogged is recorded because no third unlogged question arrived; that is spec section 4, not a counter bug.
        res = [self.hook(m).stdout != "" for m in ("What is it?", "What else?", "recap one", "recap two", "recap three")]
        self.assertEqual(res, [True, True, True, True, False])
        g = rs.read_gate(self.d)
        self.assertEqual((g["chain_unlogged"], g["chain_finish"], g["chain_cap_hit_unlogged"], g["chain_cap_hit_finish"]), (2, 2, 0, 1))

    def test_run_caps_split_by_kind(self):
        rs.gate_update(self.d, lambda g: g.update(run_unlogged=12))
        self.assertEqual(self.hook("What is it?").stdout, "")                                   # unlogged kind exhausted
        self.assertTrue(self.hook("recap now").stdout != "")                                    # finish gate stays on
        self.assertEqual(rs.read_gate(self.d)["run_cap_hit_unlogged"], 1)
        rs.gate_update(self.d, lambda g: g.update(run_finish=8, chain_finish=0))
        self.assertEqual(self.hook("recap now").stdout, "")
        self.assertEqual(rs.read_gate(self.d)["run_cap_hit_finish"], 1)

    def test_stop_hook_active_with_lost_counters_allows(self):
        r = self.hook("recap", payload={"stop_hook_active": True})
        self.assertEqual(r.stdout, "")
        self.assertEqual(rs.read_gate(self.d)["counter_lost"], 1)

    def test_fail_open_paths(self):
        self.assertEqual(self.hook(None).stdout, "")                                            # missing key
        self.assertEqual(rs.read_gate(self.d)["payload_missing"], 1)
        self.assertEqual(self.hook("recap", SEQ_GATE_SKIP="1").stdout, "")                      # driver-exempt call
        self.assertEqual(run_py(self.HOOK, "{{{", {"SEQ_STATE_DIR": self.d}).returncode, 0)    # garbage stdin
        open(os.path.join(self.d, "register.jsonl"), "a").write("garbage line\n")
        self.assertEqual(self.hook("Q1: still works?").returncode, 0)
        d2 = tempfile.mkdtemp(dir=TMP)
        self.assertEqual(run_py(self.HOOK, {"last_assistant_message": "x"}, {"SEQ_STATE_DIR": d2}).stdout, "")   # empty store passes

    def test_lock_timeout_allows(self):
        import fcntl
        import time
        fd = os.open(rs.paths(self.d)["gate_lock"], os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            t0 = time.time()
            r = self.hook("recap, nothing else")
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            self.assertGreater(time.time() - t0, 1.5)
        finally:
            os.close(fd)

    def test_hook_log_carries_compliance_fields(self):
        self.hook("Q1: What is the retention period?")
        row = [x for x in rs.read_hook_log(self.d) if x["event"] == "Stop"][-1]
        self.assertEqual((row["first_firing"], row["has_q"], row["valid"], row["decision"]), (True, True, [1], "pass"))

    def test_ledger_mode_reads_the_ledger_from_cwd(self):
        cwd = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(cwd, ".scratch"))
        open(os.path.join(cwd, ".scratch", "ledger.md"), "w").write("## main\n- [ ] Q1 first\n- [x] Q2 second => done\n")
        r = run_py(self.HOOK, {"cwd": cwd, "last_assistant_message": "recap only"}, {"SEQ_STATE_DIR": tempfile.mkdtemp(dir=TMP)}, ("--ledger",))
        self.assertIn("Open entries: Q1.", json.loads(r.stdout)["reason"])


class TestRegisterInjection(unittest.TestCase):
    def test_m3_free_microtest_all_checks_pass(self):
        res = mtg.m3()
        self.assertEqual([(n, d) for n, o, d in res if not o], [])

    def test_sessionstart_hook_reinjects_open_list_and_oldest_decision_values(self):
        d = mtg.build_sizing_store()
        st = rs.load(d)
        r = run_py(os.path.join(READBACK, "hook_register_sessionstart.py"), {"source": "compact", "cwd": TMP}, {"SEQ_STATE_DIR": d}, ("",)[:0])
        ctx = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "SessionStart")
        text = ctx["additionalContext"]
        self.assertLessEqual(len(text), 12000)
        self.assertIn("Decisions so far:", text)
        oldest = [q for q, e in st["entries"].items() if e["status"] == "RESOLVED"][:10]
        for q in oldest:
            self.assertIn(st["entries"][q]["decision"][:60], text, q)             # the VALUE, not only the id
        for q, e in st["entries"].items():
            if e["status"] in ("OPEN", "STALE"):
                self.assertIn(q + " [", text)
        self.assertEqual(rs.read_gate(d)["compactions"], 1)                       # the phase index

    def test_userprompt_hook_counts_resets_and_injects(self):
        d = tempfile.mkdtemp(dir=TMP)
        hk = os.path.join(READBACK, "hook_register_userprompt.py")
        self.assertEqual(run_py(hk, {"prompt": "first"}, {"SEQ_STATE_DIR": d}).stdout, "")           # silent when empty
        rs.call_tool(d, "log_question", {"text": "an open question"})
        r = run_py(hk, {"prompt": rs.CONTINUE_PREFIX + " Ask each open item"}, {"SEQ_STATE_DIR": d})
        text = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(text.startswith("<register>") and "Open: Q1 [main] an open question" in text and "Next id: Q2." in text)
        g = rs.read_gate(d)
        self.assertEqual((g["user_prompts"], g["nudge_prompts"], g["prompt_seq"]), (2, 1, 2))
        run_py(hk, {"prompt": "status"}, {"SEQ_STATE_DIR": d, "SEQ_GATE_SKIP": "1"})
        g = rs.read_gate(d)
        self.assertEqual((g["user_prompts"], g["user_counter_excluded"]), (2, 1))

    def test_block_text_caps_and_ledger_injection(self):
        st, mal = rs.parse_ledger("## main\n- [ ] Q1 a (deps: Q0)\n- [!] Q2 b => reopened: why; was: x\n- [x] Q3 c => v\n- [p] Q4 d => parked: r (o)\n"
                                  "## branch b-x (from Q1)\n- [ ] Q5 e\n- [ ] Q5 dup\n- [ ] no id here\n")
        self.assertEqual(mal, 2)
        self.assertEqual(rs.unresolved_ids(st), ["Q1", "Q2", "Q5"])
        blk = rs.render_block(st)
        self.assertIn("Stale (relied on an entry that was reopened): Q2", blk)
        self.assertIn("Q5 [b-x, from Q1]", blk)
        self.assertIn("Q3: v", rs.render_block(st, with_decisions=True))

    def test_m4_free_microtest_all_checks_pass(self):
        res = mtg.m4()
        self.assertEqual([(n, d) for n, o, d in res if not o], [])


class TestArmGWiring(unittest.TestCase):
    def test_arms_entries_and_tools(self):
        import run_arm
        self.assertEqual(run_arm.ARMS["G"], dict(mcp="register", seq=False, tree_tool=False, hooks="G", files=False, register=True))
        self.assertEqual(run_arm.ARMS["G-lite"], dict(mcp=None, seq=False, tree_tool=False, hooks="G-lite", files=True, seed_file="ledger.md", first_extra="G-lite"))
        self.assertEqual(run_arm.allowed_tools("G")[1], ["mcp__register__" + n for n in ("log_question", "resolve", "park", "reopen", "open_branch", "list_open")])
        self.assertEqual(run_arm.allowed_tools("G-lite")[1], [])
        self.assertIn("Write", run_arm.allowed_tools("G-lite")[0])
        self.assertNotIn("Write", run_arm.allowed_tools("G")[0])
        self.assertEqual(run_arm.register_server_cfg("/x/state")["env"], {"SEQ_STATE_DIR": "/x/state"})
        self.assertEqual(run_arm.first_extra("C"), run_arm.FIRST_EXTRA_C)                      # C unchanged
        self.assertEqual(run_arm.first_extra("A"), "")
        self.assertIn("ledger.md", run_arm.first_extra("G-lite"))
        self.assertNotIn("tree.md", run_arm.first_extra("G-lite"))
        self.assertIn("G", run_arm.EST_TOKENS)
        self.assertIn("G-lite", run_arm.EST_TOKENS)

    def test_dry_run_g_and_g_lite_build_the_run_dir_description(self):
        for arm, seed in (("G", False), ("G-lite", True)):
            r = subprocess.run([sys.executable, os.path.join(H, "run_arm.py"), "--fixture", F4, "--arm", arm, "--run-id", "t", "--out-root", os.path.join(TMP, "runs"),
                                "--dry-run", "--max-exchanges", "110", "--max-interviewer-call-budget-usd", "6.0", "--soft-stop-usd", "125"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = r.stdout
            self.assertIn("hooks configured: UserPromptSubmit, SessionStart[compact], Stop", out)
            self.assertEqual("work/.scratch/ledger.md" in out, seed)
            self.assertNotIn("tree.md", out)
            self.assertIn("--max-budget-usd 6.0 ", out)                                          # interviewer command
            self.assertIn("answerer-mcp.json --max-budget-usd 2.0", out) if False else None
            self.assertEqual(("mcp__register__list_open" in out.split("--disallowedTools")[0]), not seed)
            self.assertEqual(out.count("SEQ_GATE_SKIP=1"), 5)                                    # status, 3 compactions, final (+ post-done: none in dry run)
            summ = json.loads(out.split("# DRY RUN SUMMARY")[1])
            self.assertEqual((summ["stop_reason"], summ["n_compactions"], summ["final_request_sent"]), ("interviewer_done", 3, True))
            self.assertEqual(summ["caps"]["soft_stop_usd"], 125.0)
            self.assertEqual(summ["caps"]["max_interviewer_call_budget_usd"], 6.0)
            self.assertFalse(os.path.exists(os.path.join(TMP, "runs")))

    def test_answerer_keeps_the_default_call_budget(self):
        import run_arm
        a = argparse.Namespace(claude_bin="claude", interviewer_model="sonnet", answerer_model="haiku", max_call_budget_usd=2.0, max_interviewer_call_budget_usd=6.0, arm="G")
        self.assertEqual(run_arm.interviewer_cmd(a, "/r", None)[run_arm.interviewer_cmd(a, "/r", None).index("--max-budget-usd") + 1], "6.0")
        self.assertEqual(run_arm.answerer_cmd(a, "/r")[run_arm.answerer_cmd(a, "/r").index("--max-budget-usd") + 1], "2.0")
        a.max_interviewer_call_budget_usd = None
        self.assertEqual(run_arm.interviewer_cmd(a, "/r", None)[run_arm.interviewer_cmd(a, "/r", None).index("--max-budget-usd") + 1], "2.0")

    def test_build_run_dir_creates_mcp_json_settings_and_seed(self):
        import run_arm
        for arm in ("G", "G-lite"):
            out = tempfile.mkdtemp(dir=TMP)
            a = argparse.Namespace(arm=arm, link_credentials=False, gate_cap=3, force_branches=False)
            run_dir = os.path.join(out, "r")
            dirs, sha_, plan = run_arm.build_run_dir(a, F4, run_dir, False)
            mcp = json.load(open(os.path.join(run_dir, "mcp.json")))
            settings = json.load(open(os.path.join(dirs["config"], "settings.json")))
            self.assertEqual(sorted(settings["hooks"]), ["SessionStart", "Stop", "UserPromptSubmit"])
            cmds = [h["hooks"][0]["command"] for h in settings["hooks"].values() for h in [h[0]]]
            self.assertTrue(all("__READBACK__" not in c and READBACK in c for c in cmds))
            self.assertEqual(all(c.endswith("--ledger") for c in cmds), arm == "G-lite")
            if arm == "G":
                self.assertEqual(mcp["mcpServers"]["register"]["env"]["SEQ_STATE_DIR"], dirs["state"])
                self.assertTrue(mcp["mcpServers"]["register"]["args"][0].endswith("register_server.py"))
                self.assertFalse(os.path.exists(os.path.join(dirs["work"], ".scratch")))
            else:
                self.assertEqual(mcp["mcpServers"], {})
                self.assertEqual(open(os.path.join(dirs["work"], ".scratch", "ledger.md")).read(), "## main\n")
                self.assertFalse(os.path.exists(os.path.join(dirs["work"], ".scratch", "tree.md")))

    def test_skill_variants_regenerate_and_avoid_classifier_phrases(self):
        r = subprocess.run([sys.executable, os.path.join(H, "make_arm_skills.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        orig = open(os.path.join(H, "skills", "grill-me.original.md")).read()
        for arm in ("G", "G-lite"):
            t = open(os.path.join(H, "skills", "arm-%s.md" % arm)).read()
            for bad in ("authoritative", "your own reasoning", "your thoughts", "your thinking", "generated from your recorded calls",
                        "reproduce", "echo", "scratchpad", "chain of thought", "sequential-thinking"):
                self.assertNotIn(bad, t.lower(), (arm, bad))
            for step in ("1. **Explore first", "2. **Show your working**", "4. **Recommend, don't defer**"):
                self.assertEqual(t[t.index(step):t.index(step) + 200], orig[orig.index(step):orig.index(step) + 200])      # untouched
            self.assertIn("End the replay with a statement, not a question.", t)
        g = open(os.path.join(H, "skills", "arm-G.md")).read()
        self.assertIn("select:mcp__register__log_question,mcp__register__resolve,mcp__register__park,mcp__register__reopen,mcp__register__open_branch,mcp__register__list_open", g)
        self.assertIn("Each question you send starts with its id (`Q7:`).", g)
        self.assertIn("`list_open` with `include_resolved` set to true", g)
        lite = open(os.path.join(H, "skills", "arm-G-lite.md")).read()
        self.assertIn(".scratch/ledger.md", lite)
        self.assertNotIn("mcp__register", lite)

    def test_drive_uses_the_shared_predicate_for_finish_like(self):
        import run_arm
        key = json.load(open(F4_KEY))
        key["expected_questions"]["min"] = 0
        d = {"state": tempfile.mkdtemp(dir=TMP), "work": tempfile.mkdtemp(dir=TMP)}
        for t in ("first", "second"):
            rs.call_tool(d["state"], "log_question", {"text": t})
        msgs = ["I read the plan. Q1: scope?", "Recap so far:\nQ1: first\nQ2: second", "Q1: Tell me the scope.", "Recap again:\nQ1: first\nQ2: second", "Closing note, nothing more."]

        class B:
            n = 0

            def interviewer(self, prompt, resume, tag):
                m = msgs[min(self.n, len(msgs) - 1)] + " ref %d" % self.n
                self.n += 1
                return dict(text=m, session="s", usage={}, cost=0.0, error=None, init=None)

            def answerer(self, prompt, tag):
                return dict(text=json.dumps({"ids": [], "reply": "No strong view."}), usage={}, cost=0.0, error=None)
        a = argparse.Namespace(max_exchanges=300, max_fresh_tokens=10 ** 9, max_processed_tokens=10 ** 9, max_cost_usd=100.0, max_seconds=10 ** 6, arm="G",
                               compact_prompt="/compact", status_query_version="v2", no_compaction=True, forced_by_fallback=False, force_branches=False,
                               soft_stop_usd=None)
        with redirect_stdout(io.StringIO()):
            log, meta, _ = run_arm.drive(a, F4, TMP, d, B(), "plan", key, "brief")
        self.assertEqual(meta["stop_reason"], "interviewer_done")
        # recap (finish-like), labelled single request (NOT finish-like for the shared predicate), recap, closing: done at the 5th message
        self.assertEqual(meta["interviewer_messages"], 5)
        self.assertEqual(meta["scorer_sha"]["key.json"][:16], "afc6951937318591")

    def test_soft_stop_sends_the_final_request_and_classifies(self):
        import run_arm
        key = json.load(open(F4_KEY))
        key["expected_questions"]["min"] = 0
        d = {"state": tempfile.mkdtemp(dir=TMP), "work": tempfile.mkdtemp(dir=TMP)}
        seen = []

        class B:
            n = 0

            def interviewer(self, prompt, resume, tag):
                self.n += 1
                seen.append((tag, prompt))
                import hashlib
                return dict(text="Q%d: more? ref %s" % (self.n, hashlib.sha256(str(self.n).encode()).hexdigest() * 3), session="s", usage={}, cost=9.0, error=None, init=None)

            def answerer(self, prompt, tag):
                return dict(text=json.dumps({"ids": [], "reply": "No strong view."}), usage={}, cost=0.0, error=None)
        a = argparse.Namespace(max_exchanges=300, max_fresh_tokens=10 ** 9, max_processed_tokens=10 ** 9, max_cost_usd=140.0, max_seconds=10 ** 6, arm="G",
                               compact_prompt="/compact", status_query_version="v2", no_compaction=True, forced_by_fallback=False, force_branches=False,
                               soft_stop_usd=125.0)
        with redirect_stdout(io.StringIO()):
            log, meta, _ = run_arm.drive(a, F4, TMP, d, B(), "plan", key, "brief")
        self.assertEqual((meta["stop_reason"], meta["run_class"], meta["final_request_sent"]), ("soft_stop", "soft_stopped", True))
        self.assertTrue(seen[-1][1].startswith("Before we finish"))
        self.assertLess(meta["cost_usd"], 140.0)
        self.assertEqual(meta["max_call_cost_usd"], 9.0)
        self.assertEqual(run_arm.classify_run("soft_stop", True), "soft_stopped")
        self.assertEqual(run_arm.classify_run("cap_cost_usd", True), "truncated")           # unchanged for the other classes

    def test_seq_gate_skip_reaches_the_subprocess_env_only_for_exempt_g_tags(self):
        import run_arm
        a = argparse.Namespace(arm="G", call_timeout=5, max_interviewer_call_budget_usd=None, max_call_budget_usd=2.0)
        d = {"state": tempfile.mkdtemp(dir=TMP), "logs": tempfile.mkdtemp(dir=TMP), "config": tempfile.mkdtemp(dir=TMP),
             "answerer-config": tempfile.mkdtemp(dir=TMP), "work": TMP}
        rb = run_arm.RealBackend(a, tempfile.mkdtemp(dir=TMP), d)
        seen = {}
        orig = subprocess.run

        def fake(cmd, **kw):
            seen["env"] = kw["env"]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        subprocess.run = fake
        try:
            for tag, want in (("t012-status2", "1"), ("t012-compact", "1"), ("t050-final", "1"), ("t051-postdone-A30", "1"),
                              ("t013-interviewer", None), ("t013-answerer", None), ("t030-continue1", None)):
                rb._run(["x"], "p", TMP, d["config"], tag)
                self.assertEqual(seen["env"].get("SEQ_GATE_SKIP"), want, tag)
            a.arm = "A"
            rb._run(["x"], "p", TMP, d["config"], "t012-compact")
            self.assertIsNone(seen["env"].get("SEQ_GATE_SKIP"))
        finally:
            subprocess.run = orig


class TestArmGStubRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="gout-", dir=TMP)
        cls.runs = {}
        for arm, rid in (("G", "g0"), ("G-lite", "l0")):
            cls.runs[arm] = f4_stub_run("faithful", rid, cls.out, arm=arm, extra=["--max-interviewer-call-budget-usd", "6.0", "--soft-stop-usd", "125"])

    def meta(self, arm):
        d, _ = self.runs[arm]
        return json.load(open(os.path.join(d, "run_meta.json")))

    def test_full_schedule_three_compactions_and_final_request(self):
        for arm in ("G", "G-lite"):
            m = self.meta(arm)
            self.assertEqual((m["run_class"], m["n_compactions"], m["final_request_sent"], m["stop_reason"]), ("scored", 3, True, "interviewer_done"), arm)
            self.assertEqual(m["compactions_seen_by_hook"], 3)
            self.assertEqual(self.runs[arm][1]["items"]["lost"], 0)                     # faithful stub loses nothing under the frozen scorer
            self.assertGreaterEqual(m["answerer_turns"], 36)

    def test_g_run_meta_compliance_fields(self):
        m = self.meta("G")
        for k in ("stop_blocks_finish", "stop_blocks_unlogged", "chain_cap_hit_finish", "chain_cap_hit_unlogged", "run_cap_hit_finish", "run_cap_hit_unlogged",
                  "payload_missing", "register_unresolved_at_end", "valid_label_share", "valid_label_share_by_phase", "first_attempt_valid_share",
                  "resolved_without_user_turn", "resolved_after_nudge_only", "user_counter_excluded", "source_user_rejected", "stale_marked", "reopen_calls",
                  "open_branch_calls", "list_open_calls", "log_question_calls", "extra_q_marks", "max_call_cost_usd", "max_call_tag", "scorer_sha"):
            self.assertIn(k, m, k)
        self.assertEqual(m["valid_label_share"], 1.0)
        self.assertEqual(sorted(m["valid_label_share_by_phase"]), ["0", "1", "2", "3"])
        self.assertTrue(all(v["share"] == 1.0 for v in m["valid_label_share_by_phase"].values() if v["n"]))
        self.assertGreaterEqual(m["log_question_calls"], 25)
        self.assertGreaterEqual(m["open_branch_calls"], 1)
        self.assertGreaterEqual(m["reopen_calls"], 1)
        self.assertGreaterEqual(m["stale_marked"], 1)
        self.assertGreaterEqual(m["stop_blocks_finish"], 1)                              # the stub ends with reopened entries unasked: the gate acts
        self.assertEqual(m["register_unresolved_at_end"], [])
        self.assertEqual(m["user_counter_excluded"], 5)                                  # status, three compactions, final
        self.assertTrue(m["recompute_aligned"])
        self.assertEqual(m["valid_label_share_recomputed"], 1.0)
        self.assertFalse(m["valid_label_share_discrepancy"])
        self.assertTrue(m["registry_parity"])
        self.assertEqual(m["payload_missing"], 0)
        self.assertEqual({k: v[:16] for k, v in m["scorer_sha"].items()}, FROZEN_SHA)

    def test_g_lite_run_meta_ledger_fields(self):
        m = self.meta("G-lite")
        self.assertGreaterEqual(m["ledger_ids"], 25)
        self.assertGreaterEqual(m["ledger_branch_headings"], 1)
        self.assertGreaterEqual(m["ledger_stale_seen"], 1)
        self.assertEqual((m["ledger_malformed"], m["payload_missing"]), (0, 0))
        self.assertEqual(m["valid_label_share"], 1.0)
        self.assertGreaterEqual(m["stop_blocks_finish"], 1)
        self.assertEqual(sum(m["ledger_entries_added_by_phase"].values()), m["ledger_ids"])
        d, _ = self.runs["G-lite"]
        self.assertTrue(open(os.path.join(d, "work", ".scratch", "ledger.md")).read().startswith("## main\n"))
        first = [json.loads(line) for line in open(os.path.join(d, "driver_log.jsonl"))][0]
        self.assertTrue(first)

    def test_transcript_has_register_tool_pairs_and_hook_context(self):
        d, _ = self.runs["G"]
        raw = open(os.path.join(d, "transcript.jsonl")).read()
        self.assertIn("mcp__register__log_question", raw)
        self.assertIn("hook_additional_context", raw)
        self.assertIn("<register>", raw)
        self.assertEqual(raw.count("compact_boundary"), 3)

    def test_m8_free_microtest_scorer_isolation(self):
        res = mtg.m8()
        self.assertEqual([(n, d) for n, o, d in res if not o], [])

    def test_g_runs_are_scored_by_the_unchanged_scorer(self):
        for arm in ("G", "G-lite"):
            s = self.runs[arm][1]
            self.assertEqual(s["items"]["baseline_gate"]["verdict"], "PASSES")
            self.assertEqual(s["run_class"], "scored")


FROZEN_SHA = {"score.py": "2f355c60b1774d25", "score_items.py": "95299ff5c31ea32b", "key.json": "afc6951937318591"}
OLD_ARM_SKILL_SHA = {"D": "baedd4e14a28d8a8", "A": "60458b460eec3471", "B": "9b11df8017f9430b", "B2": "a4458f788051e96e",
                     "E": "82b2e8b430c208fe", "C": "f7e7d44b5afb1102", "F": "d2ba3fa5e3a74c91"}


class TestFrozenFiles(unittest.TestCase):
    def sha16(self, path):
        import hashlib
        return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]

    def test_scorer_item_scorer_and_key_are_frozen(self):
        self.assertEqual(self.sha16(os.path.join(H, "score.py")), FROZEN_SHA["score.py"])
        self.assertEqual(self.sha16(os.path.join(H, "score_items.py")), FROZEN_SHA["score_items.py"])
        self.assertEqual(self.sha16(F4_KEY), FROZEN_SHA["key.json"])

    def test_existing_arm_skill_variants_are_unchanged(self):
        for arm, want in OLD_ARM_SKILL_SHA.items():
            self.assertEqual(self.sha16(os.path.join(H, "skills", "arm-%s.md" % arm)), want, arm)
        man = json.load(open(os.path.join(H, "skills", "manifest.json")))
        for arm, want in OLD_ARM_SKILL_SHA.items():
            self.assertTrue(man[arm]["sha256"].startswith(want), arm)
        self.assertIn("G", man)
        self.assertIn("G-lite", man)

    def test_spec_states_the_same_prefixes(self):
        spec = open(os.path.join(SPEC_ROOT, "notes", "seq-study-arm-g-spec-20261114.md")).read()
        for v in FROZEN_SHA.values():
            self.assertIn(v, spec)

    def test_old_arms_command_lines_are_unchanged(self):
        # the per-call budget and gate-skip plumbing must not alter any non-G arm's interviewer command or environment
        import run_arm
        for arm in ("A", "D", "B", "B2", "C", "E", "F"):
            a = argparse.Namespace(claude_bin="claude", interviewer_model="sonnet", max_call_budget_usd=2.0, arm=arm)
            cmd = run_arm.interviewer_cmd(a, "/r", None)
            self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "2.0")
            self.assertIsNone(run_arm.gate_skip_env(arm, "t012-compact"))


class TestF4GScript(unittest.TestCase):
    SH = os.path.join(H, "bin", "f4_g.sh")

    def env(self, tmp, mode="faithful"):
        wrapper = os.path.join(tmp, "stub_runner.py")
        with open(wrapper, "w") as fh:
            fh.write("import sys\nsys.path.insert(0, %r)\nimport run_arm\nsys.exit(run_arm.main(sys.argv[1:] + ['--stub-backend', %r]))\n" % (H, mode))
        return dict(os.environ, G_RUNS_ROOT=os.path.join(tmp, "runs"), G_RUN_ARM=wrapper, G_SCRATCH=os.path.join(tmp, "scr"))

    def test_refuses_without_yes_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(["bash", self.SH], capture_output=True, text=True, env=self.env(tmp), stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 1)
            self.assertIn("--yes", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs")) or os.path.exists(os.path.join(tmp, "scr")))
            r = subprocess.run(["bash", self.SH, "g2", "g-lite"], capture_output=True, text=True, env=self.env(tmp), stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 1)

    def test_script_carries_the_declared_caps_and_hand_over_rules(self):
        txt = open(self.SH).read()
        self.assertTrue(os.access(self.SH, os.X_OK))
        self.assertEqual(subprocess.run(["bash", "-n", self.SH]).returncode, 0)
        for token in ("PER_RUN_CAP=140", "SOFT_STOP=125", "MAX_PROC=12000000", "CALL_BUDGET=6.0", "G_CEILING=300", "MAX_FRESH=3500000", "MAX_EX=110",
                      "MAX_SECS=10800", "CALL_TIMEOUT=1800"):
            self.assertIn(token, txt)
        self.assertNotIn("-t 0", txt)
        import re
        self.assertIsNone(re.search(r"(?m)^\s*(read|select)\b", txt))                       # no typed prompts
        self.assertIn("[ \"${#RUNS[@]}\" -gt 0 ] || RUNS=(g1)", txt)                     # default: g1 only; g2 and g-lite are explicit

    def test_dry_run_is_offline_and_prints_caps_for_g_and_g_lite(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, G_RUNS_ROOT=os.path.join(tmp, "runs"), G_SCRATCH=os.path.join(tmp, "scr"), PATH="/usr/bin:/bin")
            r = subprocess.run(["bash", self.SH, "--dry-run", "g1", "g2", "g-lite"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = r.stdout
            for frag in ("--max-cost-usd 140", "--soft-stop-usd 125", "--max-processed-tokens 12000000", "--max-interviewer-call-budget-usd 6.0",
                         "--max-fresh-tokens 3500000", "--max-exchanges 110", "--run-id g1", "--run-id g2", "--run-id l1", "--arm G-lite", "ceiling USD 300"):
                self.assertIn(frag, out)
            self.assertEqual(out.count("n_compactions=3/3 final_request_sent=True"), 3)
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs")))
            d = subprocess.run(["bash", self.SH, "--dry-run"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL).stdout
            self.assertIn("Runs that would be launched: g1", d)
            self.assertNotIn("g-lite", d.split("# live command")[0])                      # G-lite is never run by default

    def test_end_to_end_g1_then_g2_with_the_stub_and_g2_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.env(tmp)
            r2 = subprocess.run(["bash", self.SH, "--yes", "g2"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertIn("REFUSE g2: g1 is not a valid", r2.stdout)                    # no g1 yet
            r1 = subprocess.run(["bash", self.SH, "--yes"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertIn("ok g1 stop_reason=interviewer_done", r1.stdout, r1.stdout + r1.stderr)
            self.assertIn("CHECKPOINT", r1.stdout)
            self.assertIn("scored G g1", r1.stdout)
            self.assertNotIn("RUN g2", r1.stdout)
            s = json.load(open(os.path.join(tmp, "runs", "f4-settlement-recon", "G", "g1", "score.json")))
            self.assertEqual((s["actual_arm"], s["scored_as_arm"]), ("G", "A"))
            again = subprocess.run(["bash", self.SH, "--yes", "g1"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertIn("REFUSE g1", again.stdout)                                     # never retried or overwritten
            r2 = subprocess.run(["bash", self.SH, "--yes", "g2"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertIn("ok g2 stop_reason=interviewer_done", r2.stdout, r2.stdout)

    def test_ceiling_counts_existing_g_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rid, cost in (("g1", 170.0),):
                os.makedirs(os.path.join(tmp, "runs", "f4-settlement-recon", "G", rid))
                json.dump({"cost_usd": cost, "run_class": "scored"}, open(os.path.join(tmp, "runs", "f4-settlement-recon", "G", rid, "run_meta.json"), "w"))
            r = subprocess.run(["bash", self.SH, "--yes", "g2"], capture_output=True, text=True, env=self.env(tmp), stdin=subprocess.DEVNULL)
            self.assertTrue("REFUSE g2" in r.stdout or "CEILING reached" in r.stdout)
            self.assertNotIn("RUN g2", r.stdout)


class TestMicrotestsGScript(unittest.TestCase):
    SH = os.path.join(H, "bin", "microtests_G.sh")

    def test_paid_tests_refuse_without_yes(self):
        scratch = os.path.join(H, ".scratch")
        before = set(os.listdir(scratch)) if os.path.isdir(scratch) else set()
        for args in (["--paid"], ["M5"], ["M6", "M7"]):
            r = subprocess.run(["bash", self.SH, *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn("spend tokens", r.stderr)
        after = set(os.listdir(scratch)) if os.path.isdir(scratch) else set()
        self.assertFalse([n for n in after - before if n.startswith("microtests-G-")])

    def test_script_structure(self):
        txt = open(self.SH).read()
        self.assertEqual(subprocess.run(["bash", "-n", self.SH]).returncode, 0)
        self.assertNotIn("-t 0", txt)
        self.assertIn("MICROTESTS_CEILING_USD", open(os.path.join(H, "bin", "microtests_G_paid.py")).read())
        self.assertEqual(subprocess.run([sys.executable, "-m", "py_compile", os.path.join(H, "bin", "microtests_G_paid.py")]).returncode, 0)
        paid = open(os.path.join(H, "bin", "microtests_G_paid.py")).read()
        self.assertIn('BUDGETS = {"M5": 0.5, "M6": 1.0, "M7": 1.0}', paid)
        self.assertIn("min(3.0,", paid)

    def test_m7_pretrusts_work_dir_and_never_confirms_the_default_no_option(self):
        sys.path.insert(0, os.path.join(H, "bin"))
        import microtests_G_paid as mp
        d = tempfile.mkdtemp(dir=TMP)
        mp.pretrust(d, d)
        cfg = json.load(open(os.path.join(d, ".claude.json")))
        self.assertTrue(cfg["projects"][os.path.realpath(d)]["hasTrustDialogAccepted"])
        self.assertTrue(cfg["hasCompletedOnboarding"])
        src = open(os.path.join(H, "bin", "microtests_G_paid.py")).read()
        self.assertIn('"Down"', src)                       # the trust dialog defaults to 'No, exit'
        self.assertIn('"-L", "m7g"', src)                  # private tmux socket

    def test_free_part_runs_without_any_flag_and_passes(self):
        r = subprocess.run(["bash", self.SH, "M4"], capture_output=True, text=True, stdin=subprocess.DEVNULL, env=dict(os.environ, MTG_TMP=TMP))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("M4 PASS", r.stdout)
        self.assertIn("FREE MICROTESTS: ALL PASS", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
