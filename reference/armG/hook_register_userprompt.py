#!/usr/bin/env python3
"""Arm G UserPromptSubmit hook: count the prompt, reset the per-call block counters, inject <register>.

user_prompts counts only prompts sent while SEQ_GATE_SKIP is unset (the harness status query, /compact turn, scripted
final request and post-done delivery carry it); otherwise user_counter_excluded is incremented. Every prompt starts a new
claude call, so both chain block counters and the per-call firing counter are reset. --ledger: G-lite (ledger.md)."""
import sys
from hook_register_common import (read_stdin, state_dir, log, store, ledger_mode, current_state, skip_set, emit_context)

if __name__ == "__main__":
    p = read_stdin()
    try:
        skip = skip_set()
        prompt = p.get("prompt") if isinstance(p.get("prompt"), str) else ""

        def bump(g):
            g["prompt_seq"] += 1
            if skip:
                g["user_counter_excluded"] += 1
            else:
                g["user_prompts"] += 1
                if prompt.startswith(store.CONTINUE_PREFIX):
                    g["nudge_prompts"] += 1
            g["chain_finish"] = g["chain_unlogged"] = g["stop_firings"] = 0
            return dict(g)
        try:
            g = store.gate_update(state_dir(p), bump)
        except store.LockTimeout:
            g = {}
            log(p, "UserPromptSubmit:lock_timeout")
        st, _mal = current_state(p, ledger_mode())
        text = store.render_block(st)
        log(p, "UserPromptSubmit", {"chars": len(text), "skip": skip, "user_prompts": g.get("user_prompts"),
                                    "user_counter_excluded": g.get("user_counter_excluded"), "prompt_seq": g.get("prompt_seq"),
                                    "mode": "ledger" if ledger_mode() else "register"})
        emit_context("UserPromptSubmit", text)
    except Exception as exc:  # noqa: BLE001
        log(p, "UserPromptSubmit:error", {"error": repr(exc)})
    sys.exit(0)
