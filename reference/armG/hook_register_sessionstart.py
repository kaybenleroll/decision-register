#!/usr/bin/env python3
"""Arm G SessionStart[compact] hook: bump the compactions counter (the phase index) and re-inject <register> plus
`Decisions so far:` (cap 12000, longest decision text cut first). --ledger: G-lite."""
import sys
from hook_register_common import (read_stdin, state_dir, log, store, ledger_mode, current_state, emit_context)

if __name__ == "__main__":
    p = read_stdin()
    try:
        try:
            g = store.gate_update(state_dir(p), lambda g: g.__setitem__("compactions", g["compactions"] + 1) or dict(g))
        except store.LockTimeout:
            g = {}
            log(p, "SessionStart:lock_timeout")
        st, _mal = current_state(p, ledger_mode())
        text = store.render_block(st, with_decisions=True)
        log(p, "SessionStart", {"source": p.get("source"), "chars": len(text), "compactions": g.get("compactions"),
                                "mode": "ledger" if ledger_mode() else "register"})
        emit_context("SessionStart", text)
    except Exception as exc:  # noqa: BLE001
        log(p, "SessionStart:error", {"error": repr(exc)})
    sys.exit(0)
