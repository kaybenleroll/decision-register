#!/usr/bin/env python3
"""Arm G Stop hook (spec section 4). Blocks a finish attempt while any entry is open or stale, and a question that
carries no open id. Uses only payload["last_assistant_message"]; transcript_path is never read. Fails open: any error,
a lock timeout, a missing key or SEQ_GATE_SKIP allows. --ledger: G-lite (entries parsed from <cwd>/.scratch/ledger.md)."""
import json
import sys
from hook_register_common import (read_stdin, state_dir, log, store, ledger_mode, current_state, skip_set, ledger_path)

LEDGER_SNAPSHOT_CAP = 400000      # a snapshot per Stop firing; large enough never to cut a real ledger


def main():
    p = read_stdin()
    ledger = ledger_mode()
    sd = state_dir(p)
    try:
        skip = skip_set()
        msg = p.get("last_assistant_message")
        st, malformed = current_state(p, ledger)
        turn_logged = []

        def decide(g):
            first = g["stop_firings"] == 0
            g["stop_firings"] += 1
            if not ledger and not skip:
                turn_logged.extend(q for q, e in st["entries"].items()
                                   if e["status"] == "OPEN" and e.get("prompt_seq") == g["prompt_seq"])
            d = store.stop_decision(msg, st, g, skip=skip, stop_hook_active=bool(p.get("stop_hook_active")), turn_logged=turn_logged)
            if d["allow"] and d["has_q"] and d["valid"] and first and not skip:
                # a second, unlabelled '?' in a labelled message (diagnostic, not enforced)
                g["extra_q_marks"] += max(0, store.strip_code_urls(msg).count("?") - max(1, len(d["valid"])))
            d.update(first=first, phase=g["compactions"], stop_hook_active=bool(p.get("stop_hook_active")))
            return d
        try:
            d = store.gate_update(sd, decide)
        except store.LockTimeout:
            log(p, "Stop", {"decision": "lock_timeout_allow"})
            return 0
        extra = dict(decision=d["tag"], kind=d["kind"], has_q=d["has_q"], ids=d["ids"], valid=d["valid"], first_firing=d["first"],
                     phase=d["phase"], skip=skip, stop_hook_active=d["stop_hook_active"],
                     open_ids=sorted(int(q[1:]) for q in store.unresolved_ids(st)), mode="ledger" if ledger else "register",
                     msg_chars=len(msg) if isinstance(msg, str) else None)
        if ledger:
            try:
                with open(ledger_path(p), encoding="utf-8") as fh:
                    extra["ledger"] = fh.read()[:LEDGER_SNAPSHOT_CAP]
            except Exception:  # noqa: BLE001
                extra["ledger"] = None
            extra["ledger_malformed"] = malformed
        log(p, "Stop", extra)
        if not d["allow"]:
            sys.stdout.write(json.dumps({"decision": "block", "reason": d["reason"]}))
    except Exception as exc:  # noqa: BLE001
        log(p, "Stop:error", {"error": repr(exc)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
