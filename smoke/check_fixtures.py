#!/usr/bin/env python3
"""Offline smoke fixtures check (slices P3, P4; REQ-82); stdlib only; spends nothing and starts no claude session.
Run: python3 -I smoke/check_fixtures.py [--rs CMD] [--ts CMD]   (defaults: the release Rust binary and the TS bundle; build both first)

1. Static: the stub skill is named exactly grill-me and lives outside plugin/; the hook inputs in plugin/hooks/hooks.json
   carry the argument names the scripted prompts below use; the skill name matches the hook matcher and tool_schema.
2. Replay: the scripted prompts of smoke/fixtures/fixtures.json are sent as the three hook-facing tools (note_prompt,
   skill_started, check_stop) to BOTH builds through conformance/run.py, one scenario per live-smoke check (2) to (6).
"""
import importlib.util, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "smoke", "fixtures")
F = json.load(open(os.path.join(FIX, "fixtures.json")))
S, P, A, SK = F["sessions"], F["prompts"], F["assistant"], F["skill"]
TS = json.load(open(os.path.join(ROOT, "contract", "tools.json")))["tool_schema"]
bad = []


def check(ok, what):
    print(("ok" if ok else "not ok") + " - " + what)
    if not ok:
        bad.append(what)


def static():
    text = open(os.path.join(FIX, F["skill_file"])).read()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    front = dict(l.split(": ", 1) for l in m.group(1).splitlines()) if m else {}
    check(front.get("name") == SK and os.path.basename(os.path.dirname(os.path.join(FIX, F["skill_file"]))) == SK,
          "stub skill file is named exactly " + SK + " (frontmatter and directory)")
    check(not os.path.exists(os.path.join(ROOT, "plugin", "skills")) and not FIX.startswith(os.path.join(ROOT, "plugin")),
          "stub skill is outside plugin/ and the plugin has no skills directory")
    hooks = json.load(open(os.path.join(ROOT, "plugin", "hooks", "hooks.json")))["hooks"]
    inputs = {h["tool"]: h["input"] for g in hooks.values() for x in g for h in x["hooks"]}
    check(set(inputs["note_prompt"]) == {"session_id", "prompt"}, "note_prompt hook input keys match the scripted prompts")
    check(set(inputs["check_stop"]) == {"session_id", "last_assistant_message"}, "check_stop hook input keys match the scripted messages")
    check(set(inputs["skill_started"]) == {"tool_schema", "session_id", "skill"} and inputs["skill_started"]["tool_schema"] == TS,
          "skill_started hook input keys and tool_schema %s match" % TS)
    matchers = [x["matcher"] for g in hooks.values() for x in g if "matcher" in x]
    check(any(re.search(x, SK) for x in matchers if x != "Skill") and "Skill" in matchers, "hook matchers cover the skill name " + SK)
    check(P["typed"].startswith("/" + SK) and SK in P["model"] and "?" not in A["finish"] and "?" in A["question"] and "?" in P["unrelated"],
          "scripted prompts have the shapes the live checks need")


def np(sess, prompt):
    return {"call": "note_prompt", "args": {"session_id": sess, "prompt": prompt}}


def sk(sess):
    return {"call": "skill_started", "args": {"tool_schema": TS, "session_id": sess, "skill": SK}}


def cs(sess, msg, block):
    return {"call": "check_stop", "args": {"session_id": sess, "last_assistant_message": msg}, "assert": [{"decision_block": block}]}


def st(**kw):
    return {"call": "status", "args": {}, "assert": [{"path": k.replace("__", "."), "eq": v} for k, v in kw.items()]}


def scenarios():
    def sc(i, title, steps):
        return {"id": "SM-" + i, "title": title, "req": ["REQ-82"], "steps": steps}
    t, m, u, g = S["typed"], S["model"], S["unrelated"], S["gate"]
    return [
        sc("2", "check 2: hooks fire, so the status hook counts are non-zero", [
            np(t, P["typed"]), sk(t), cs(t, A["question"], False),
            st(state="pending", hook_health__note_prompt=1, hook_health__skill_started=1, hook_health__check_stop=1)]),
        sc("3", "check 3: a finish attempt with an unlogged item is blocked", [
            np(g, P["typed"]), {"call": "begin_interview", "args": {"tool_schema": TS, "skill": SK, "mode": "register"}},
            {"call": "log_item", "args": {"id": "q1", "kind": "question", "text": "Which eviction policy for the cache?"}},
            cs(g, A["finish"], True)]),
        sc("4", "check 4: typed route, the prompt then the expansion hook start a pending interview", [
            np(t, P["typed"]), sk(t), st(state="pending", hook_health__skill_started=1)]),
        sc("5", "check 5: model-invoked Skill route (PreToolUse), the same tool starts a pending interview", [
            np(m, P["model"]), sk(m), st(state="pending", hook_health__skill_started=1)]),
        sc("6", "check 6: a question in an unrelated session is never blocked and starts nothing", [
            np(u, P["unrelated"]), cs(u, A["question"], False), cs(u, A["finish"], False), st(state="none")]),
    ]


def replay(name, cmd):
    spec = importlib.util.spec_from_file_location("conf_run", os.path.join(ROOT, "conformance", "run.py"))
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    d = os.path.join(ROOT, ".scratch", "smoke", "scenarios")
    os.makedirs(d, exist_ok=True)
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    for s in scenarios():
        json.dump(s, open(os.path.join(d, s["id"] + ".json"), "w"), indent=1)
    run.SCEN_DIR = d
    print("# replay on " + name)
    rc = run.main(["--"] + cmd)
    check(rc == 0, "replay of %d scenarios passes on %s" % (len(scenarios()), name))


def main(a):
    cmds = {"rs": [os.path.join(ROOT, "impl/rust/target/release/register-rs")],
            "ts": ["node", os.path.join(ROOT, "impl/ts/dist/register.mjs")]}
    while a:
        x = a.pop(0)
        if x in ("--rs", "--ts") and a:
            cmds[x[2:]] = a.pop(0).split()
        else:
            print(__doc__.strip())
            return 2
    static()
    for n, c in cmds.items():
        if not os.path.exists(c[-1]):
            check(False, "build for %s exists (%s); build it first" % (n, c[-1]))
        else:
            replay(n, c)
    print("# %d failed" % len(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
