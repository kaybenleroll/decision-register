#!/usr/bin/env python3
"""Seeded-failure demonstration (#146; R1 acceptance: "unit and suite jobs fail on a seeded failure"). Standard library only.

Run: python3 -I .github/scripts/seeded_failure_demo.py rust|ts|conformance [-- <server command...>]

For the chosen target it copies the sources to a temporary directory, runs the command that ci.yml runs (the control, which
must exit 0), injects one deliberate failure into the copy, runs the same command again (which must exit non-zero), and exits
0 only when both hold. Targets: rust (`cargo test --locked` with a failing unit test appended to src/main.rs), ts (`npm test`
with a failing node test file added; needs `npm ci` done in impl/ts), conformance (`conformance/run.py -- <server command>`
with a failing scenario file added; the server command is given after `--`). The working tree is never modified, and nothing
seeded exists unless this script is run: it is called only by .github/workflows/seeded-failure-demo.yml (workflow_dispatch).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKIP = shutil.ignore_patterns("target", "node_modules", "dist", ".scratch", ".git", "reference")

RUST_SEED = '\n#[cfg(test)]\nmod seeded_failure {\n    #[test]\n    fn seeded_failure() {\n        panic!("seeded failure (#146)");\n    }\n}\n'
TS_SEED = 'import assert from "node:assert/strict";\nimport { test } from "node:test";\n\ntest("seeded failure (#146)", () => {\n    assert.equal(1, 2);\n});\n'
SCENARIO_SEED = {
    "id": "S-zz-seeded",
    "title": "seeded failure (#146): status after nothing happened must report an impossible state",
    "req": ["REQ-71"],
    "steps": [{"call": "status", "args": {}, "assert": [{"path": "state", "eq": "seeded-impossible"}]}],
}


def run(cmd, cwd, env=None):
    print("+ %s  (in %s)" % (" ".join(cmd), cwd), flush=True)
    return subprocess.run(cmd, cwd=cwd, env=env).returncode


def prepare(work):
    # The whole tree is copied (the Rust crate includes files from conformance/ at compile time).
    shutil.copytree(ROOT, work, ignore=SKIP, dirs_exist_ok=True)
    if sys.argv[1] == "rust":
        env = dict(os.environ, CARGO_TARGET_DIR=os.path.join(work, "target"))
        cmd, cwd = ["cargo", "test", "--locked"], os.path.join(work, "impl", "rust")

        def seed():
            with open(os.path.join(cwd, "src", "main.rs"), "a", encoding="utf-8") as f:
                f.write(RUST_SEED)
        return cmd, cwd, env, seed
    if sys.argv[1] == "ts":
        src = os.path.join(ROOT, "impl", "ts")
        if not os.path.isdir(os.path.join(src, "node_modules")):
            sys.exit("run `npm ci` in impl/ts first")
        cwd = os.path.join(work, "impl", "ts")
        os.symlink(os.path.join(src, "node_modules"), os.path.join(cwd, "node_modules"))

        def seed():
            with open(os.path.join(cwd, "test", "seeded.test.ts"), "w", encoding="utf-8") as f:
                f.write(TS_SEED)
        return ["npm", "test"], cwd, None, seed
    if sys.argv[1] == "conformance":
        server = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
        if not server:
            sys.exit("usage: ... conformance -- <server command...>")
        server = [os.path.abspath(a) if os.path.exists(a) else a for a in server]
        cwd = work

        def seed():
            with open(os.path.join(work, "conformance", "scenarios", "S-zz-seeded.json"), "w", encoding="utf-8") as f:
                json.dump(SCENARIO_SEED, f)
        return ["python3", "-I", "conformance/run.py", "--"] + server, cwd, None, seed
    sys.exit(__doc__)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    with tempfile.TemporaryDirectory(prefix="seeded-failure-") as work:
        cmd, cwd, env, seed = prepare(work)
        control = run(cmd, cwd, env)
        print("control exit status: %d" % control, flush=True)
        if control != 0:
            sys.exit("control run failed before seeding; the demonstration proves nothing")
        seed()
        seeded = run(cmd, cwd, env)
        print("seeded exit status: %d" % seeded, flush=True)
        if seeded == 0:
            sys.exit("FAIL: the seeded failure did not make the command exit non-zero")
        print("OK: control exit 0, seeded exit %d (the command fails on a seeded failure)" % seeded)


if __name__ == "__main__":
    main()
