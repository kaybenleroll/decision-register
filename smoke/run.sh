#!/usr/bin/env bash
# Live smoke suite (slice P4, issue #31; REQ-82, REQ-92, REQ-13, REQ-20, REQ-41, REQ-42, REQ-51, REQ-12).
#
#   smoke/run.sh                          DRY-RUN (default): prints every command and the cost cap; spends nothing; starts no claude
#   SMOKE_SPEND_OK=1 smoke/run.sh --spend paid run: six `claude -p` calls on the cheapest model (haiku), checks (1) to (8)
#
# A paid run needs BOTH the --spend flag and SMOKE_SPEND_OK=1; with either missing claude is never invoked.
# Isolation (nothing under ~/.claude is read or written):
#   - CLAUDE_CONFIG_DIR points at a throwaway directory under .scratch/ (docs: "All settings, session history, and plugins
#     are stored under this path"; credentials move with it). The stub user skill is copied to <config dir>/skills/grill-me,
#     the user-level skills location. The docs state `~/.claude/skills` for personal skills and do not spell out that
#     CLAUDE_CONFIG_DIR relocates it; check (4) and (5) are the proof, and fail rather than pass if the skill is not found.
#   - a fresh config dir holds no login, so a paid run needs ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN in the environment
#     (docs, authentication precedence); the script refuses to run without one rather than guess.
#   - claude runs from <scratch>/work, not from plugin/ (REQ-82; plugin/README.md: run it from outside the plugin), with
#     the plugin loaded by --plugin-dir. Ancestor CLAUDE.md files of the repository still load (adds some input tokens).
# Subscription login (opt-in): SMOKE_USE_LOGIN=1 skips the API key guard, for an account with no API key. The script never
#   copies or reads a login file: after it creates the throwaway config dir it prints the exact `cp` command, and you run it
#   yourself within SMOKE_LOGIN_WAIT seconds (default 120); if the file does not appear the script exits 2. The file
#   ($cfg/.credentials.json) is chmod 600 and is removed on every exit path (trap on EXIT, INT, TERM, HUP); the final report
#   says so. On a subscription the USD figure is a client-side estimate, NOT a charge; the caps still bound the estimate.
# Cost: --max-budget-usd per call (a client-side estimate that can be passed, so the per-call cap is small), the running
# total is printed after each call, and a total of USD 0.25 or more fails check (8) and the suite. Beyond about USD 1 needs
# the user (REQ-3). Each call has a `timeout`; exit 124 is a FAIL, not a hang (an earlier probe timed out with no output).
# Server build: SMOKE_IMPL=ts (default) or rs; the matching build must exist (see plugin/README.md).
# A paid run first probes the chosen build locally (smoke/probe_server.py, no claude) and refuses a stale one. Claude Code ignores CLAUDE_PLUGIN_DATA for a --plugin-dir plugin (data root is $cfg/plugins/data/decision-register-inline/<hash>/, shared by all calls), so that directory is wiped before each call.
# Requires: bash, jq, timeout, awk; claude for a paid run. Spends nothing and writes nothing in dry-run.
set -u

root=$(cd "$(dirname "$0")/.." && pwd -P) || exit 1
spend_flag=0
for a in "$@"; do
    case $a in
    --spend) spend_flag=1 ;;
    -h | --help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
    esac
done
spend=0
[ "$spend_flag" = 1 ] && [ "${SMOKE_SPEND_OK:-}" = 1 ] && spend=1

model=haiku
suite_cap=0.25
call_cap=${SMOKE_CALL_CAP:-0.04}
call_timeout=${SMOKE_TIMEOUT:-150}
impl=${SMOKE_IMPL:-ts}
prefix=mcp__plugin_decision-register_register__
scratch=$root/.scratch/smoke-run
cfg=$scratch/config
work=$scratch/work
fix=$root/smoke/fixtures
skill=$(jq -r .skill "$fix/fixtures.json") || exit 1
p_typed=$(jq -r .prompts.typed "$fix/fixtures.json") || exit 1
p_model=$(jq -r .prompts.model "$fix/fixtures.json") || exit 1
p_unrelated=$(jq -r .prompts.unrelated "$fix/fixtures.json") || exit 1

if [ "$spend" = 1 ]; then
    echo "MODE: PAID RUN (--spend and SMOKE_SPEND_OK=1)"
else
    echo "MODE: DRY-RUN (no claude is invoked, nothing is spent). A paid run needs --spend AND SMOKE_SPEND_OK=1."
fi
echo "model=$model implementation=$impl per-call cap=USD $call_cap suite cap=USD $suite_cap timeout=${call_timeout}s calls=6"
echo "worst case by caps: USD $(awk -v c="$call_cap" 'BEGIN{printf "%.2f", c*6}') (estimates can overshoot a cap; the suite fails at USD $suite_cap)"

pass=0
fail=0
total=0
result() { # $1 id, $2 PASS|FAIL, $3 detail
    echo "$2 check $1: $3"
    if [ "$2" = PASS ]; then pass=$((pass + 1)); else fail=$((fail + 1)); fi
}

# Environment for every claude call; exported only for the call (env), never to this shell's config.
claude_env=(CLAUDE_CONFIG_DIR="$cfg" REGISTER_IMPL="$impl")
case $impl in
rs) claude_env+=(PATH="$root/impl/rust/target/release:$PATH") ;;
ts) ;;
*) echo "SMOKE_IMPL must be ts or rs" >&2; exit 2 ;;
esac

# call <id> <prompt>: runs one claude -p, leaves the stream in $out and sets cost/rc.
call() {
    id=$1; shift
    prompt=$1
    out=$scratch/call-$id.jsonl
    remaining=$(awk -v t="$total" -v s="$suite_cap" 'BEGIN{printf "%.4f", s-t}')
    cap=$(awk -v c="$call_cap" -v r="$remaining" 'BEGIN{printf "%.4f", (r<c)?r:c}')
    max_turns=8
    case $id in c | d) max_turns=10 ;; esac
    cmd=(env "${claude_env[@]}")
    # call f: force the ts launcher path with a missing bundle, so the launcher serves the stub whichever SMOKE_IMPL is set.
    [ "$id" = f ] && cmd+=(REGISTER_IMPL=ts "REGISTER_TS_BUNDLE=$scratch/absent.mjs")
    cmd+=(timeout "$call_timeout" claude -p --model "$model" --max-budget-usd "$cap" --max-turns "$max_turns"
        --plugin-dir "$root/plugin" --allowedTools "${prefix}*,Skill" --no-session-persistence
        --output-format stream-json --verbose --include-hook-events -- "$prompt")
    if [ "$spend" != 1 ]; then
        printf 'DRY-RUN call %s (cap USD %s), cwd %s:\n  ' "$id" "$cap" "$work"
        printf '%q ' "${cmd[@]}"
        printf '\n'
        rc=dry
        cost=0
        return 0
    fi
    if awk -v r="$remaining" 'BEGIN{exit !(r<=0.005)}'; then
        echo "call $id skipped: suite budget exhausted"
        rc=skipped; cost=0; : >"$out"
        return 0
    fi
    # Isolate calls: Claude Code gives a --plugin-dir plugin its own data root under the throwaway config dir, shared by
    # all calls (it ignores CLAUDE_PLUGIN_DATA), so an unfinished interview left by one call would make a later
    # begin_interview refuse (REQ-52). Wipe only that directory, only under $cfg, never the credentials file.
    # State still continues within one call. Added after the dry-run print, so dry-run is unchanged.
    case $cfg in "$root"/.scratch/?*) ;; *) echo "refusing to wipe: unsafe config dir '$cfg'" >&2; return 1 ;; esac
    rm -rf -- "$cfg/plugins/data/decision-register-inline"
    (cd "$work" && "${cmd[@]}" </dev/null >"$out" 2>"$scratch/call-$id.err")
    rc=$?
    cost=$(jq -s '[.[]|select(.type=="result")|.total_cost_usd]|last // 0' "$out" 2>/dev/null) || cost=0
    [ -n "$cost" ] || cost=0
    total=$(awk -v t="$total" -v c="$cost" 'BEGIN{printf "%.4f", t+c}')
    echo "call $id done: exit=$rc cost=USD $cost running total=USD $total"
}

# Fact extractors over the stream file $out (jq slurp; a malformed stream yields empty facts and so a FAIL).
init_tools() { jq -s -r '.[]|select(.type=="system" and .subtype=="init")|.tools[]?' "$out" 2>/dev/null; }
used_tools() { jq -s -r '.[]|select(.type=="assistant")|.message.content[]?|select(.type=="tool_use")|.name' "$out" 2>/dev/null; }
skill_inputs() { jq -s -r '.[]|select(.type=="assistant")|.message.content[]?|select(.type=="tool_use" and .name=="Skill")|.input.skill' "$out" 2>/dev/null; }
tool_results() { jq -s -r '.[]|select(.type=="user")|.message.content[]?|select(.type=="tool_result")|(.content|if type=="array" then map(.text? // "")|join("") else tostring end)' "$out" 2>/dev/null; }
all_text() { jq -s -r '.[]|tostring' "$out" 2>/dev/null; }
why() { # explain a non-zero exit in the FAIL line
    case $rc in 124) echo "timeout after ${call_timeout}s" ;; skipped) echo "skipped, suite budget exhausted" ;; *) echo "claude exit $rc" ;; esac
}

if [ "$spend" = 1 ]; then
    command -v claude >/dev/null 2>&1 || { echo "claude not found" >&2; exit 2; }
    for t in jq timeout awk; do command -v "$t" >/dev/null 2>&1 || { echo "$t not found" >&2; exit 2; }; done
    if [ "${SMOKE_USE_LOGIN:-}" != 1 ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
        echo "refusing: the throwaway config dir holds no login; set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN, or set SMOKE_USE_LOGIN=1 to supply a subscription login file yourself" >&2
        exit 2
    fi
    case $impl in
    ts) [ -f "$root/impl/ts/dist/register.mjs" ] || { echo "TS build missing: cd impl/ts && npm run build" >&2; exit 2; } ;;
    rs) [ -x "$root/impl/rust/target/release/register-rs" ] || { echo "Rust build missing: cd impl/rust && cargo build --release" >&2; exit 2; } ;;
    esac
    # Pre-flight (nothing is rebuilt): start the chosen build locally, ask it for status, compare with the repository.
    want_schema=$(jq -r .tool_schema "$root/contract/tools.json") || exit 2
    hook_schemas=$(jq -r '[.. | objects | select(has("tool_schema")) | .tool_schema] | unique | join(",")' "$root/plugin/hooks/hooks.json") || exit 2
    want_version=$(jq -r .version "$root/plugin/.claude-plugin/plugin.json") || exit 2
    if [ "$hook_schemas" != "$want_schema" ]; then
        echo "refusing: plugin/hooks/hooks.json tool_schema ($hook_schemas) differs from contract/tools.json ($want_schema)" >&2
        exit 2
    fi
    case $impl in
    ts) probe_cmd=(node "$root/impl/ts/dist/register.mjs"); rebuild="cd impl/ts && npm ci && npm run build" ;;
    rs) probe_cmd=("$root/impl/rust/target/release/register-rs"); rebuild="cd impl/rust && cargo build --release" ;;
    esac
    mkdir -p "$root/.scratch" || exit 1
    probe=$(python3 -I "$root/smoke/probe_server.py" "$root/.scratch/smoke-probe-data" "${probe_cmd[@]}") || {
        echo "refusing: the $impl build did not answer status; rebuild it: $rebuild" >&2
        exit 2
    }
    got_version=${probe% *}
    got_schema=${probe#* }
    if [ "$got_schema" != "$want_schema" ]; then
        echo "refusing: the $impl build is stale (tool_schema $got_schema, repository $want_schema); rebuild it: $rebuild" >&2
        exit 2
    fi
    if [ "$(printf '%s\n%s\n' "$got_version" "$want_version" | sort -V | head -n1)" != "$want_version" ]; then
        echo "refusing: the $impl build is older than the plugin manifest (server $got_version, manifest $want_version); rebuild it: $rebuild" >&2
        exit 2
    fi
    echo "pre-flight ok: $impl build server_version $got_version, tool_schema $got_schema"
    rm -rf "$scratch"
    mkdir -p "$cfg/skills" "$work" || exit 1
    cp -R "$fix/skills/$skill" "$cfg/skills/$skill" || exit 1
    # State the schema in the stub so the model does not guess it (tool_schema from contract/tools.json, read above).
    printf 'Use tool_schema %s on every begin_interview call.\n' "$want_schema" >>"$cfg/skills/$skill/SKILL.md" || exit 1
    if [ "${SMOKE_USE_LOGIN:-}" = 1 ]; then
        cred=$cfg/.credentials.json
        login_wait=${SMOKE_LOGIN_WAIT:-120}
        case $login_wait in '' | *[!0-9]*) echo "SMOKE_LOGIN_WAIT must be whole seconds" >&2; exit 2 ;; esac
        login_state=never-placed
        # Runs on every exit path; removes the login file whatever the exit status. Never prints file content.
        cleanup_login() {
            rc_exit=$?
            trap - EXIT
            if [ -e "$cred" ] || [ -L "$cred" ]; then
                rm -f "$cred"
                if [ -e "$cred" ] || [ -L "$cred" ]; then
                    echo "WARNING: could not remove $cred; delete it yourself" >&2
                else
                    echo "login file removed: $cred"
                fi
            elif [ "$login_state" = placed ]; then
                echo "login file already absent at exit: $cred"
            fi
            exit "$rc_exit"
        }
        trap cleanup_login EXIT
        trap 'exit 130' INT
        trap 'exit 143' TERM
        trap 'exit 129' HUP
        echo "SMOKE_USE_LOGIN=1: place your login file in the throwaway config dir yourself. The script does not copy or read it."
        echo "  Run in another terminal (the usual location of the login file is below; adjust it if yours differs):"
        echo "    cp ${HOME:-/home/you}/.claude/.credentials.json $cred"
        echo "  Waiting up to ${login_wait}s for $cred ..."
        waited=0
        while [ ! -f "$cred" ] && [ "$waited" -lt "$login_wait" ]; do
            sleep 2
            waited=$((waited + 2))
        done
        if [ ! -f "$cred" ]; then
            echo "login file did not appear within ${login_wait}s: $cred; nothing was run, nothing spent" >&2
            exit 2
        fi
        chmod 600 "$cred" || exit 2
        login_state=placed
        echo "login file present (mode 600); it is removed when this script exits. On a subscription the USD figures are client-side estimates, not charges."
    fi
else
    echo "dry-run would create $cfg/skills/$skill (copy of smoke/fixtures/skills/$skill) and run from $work"
fi

stop_phrase="Items are still unresolved in the decision register"

# Calls 1 and 2 share one run: tools listed, status answers, note_prompt hook fired (prompt_seen true; in state none the
# note_prompt counter is not incremented, so counters are only meaningful once an interview has started).
call a "Call the register status tool and report its JSON result verbatim. Do nothing else."
if [ "$spend" = 1 ]; then
    if [ "$rc" = 0 ] && init_tools | grep -q "^${prefix}status\$"; then
        result 1 PASS "plugin loaded; ${prefix}status is in the session tool list"
    else
        result 1 FAIL "$(why); ${prefix}status not in the tool list"
    fi
    hooks=$(tool_results | grep -m1 -o '"hook_health":{[^}]*}')
    if [ "$rc" = 0 ] && tool_results | grep -qF '"prompt_seen":true'; then
        result 2 PASS "hooks fire: prompt_seen true (hook_health: ${hooks:-absent})"
    else
        result 2 FAIL "$(why); status prompt_seen true not found (hook_health: ${hooks:-not found})"
    fi
fi

call b "Do these steps in order. 1) Call begin_interview on the register with tool_schema 5, skill \"$skill\", mode \"register\". 2) Call log_item with id \"q1\", kind \"question\", text \"Which eviction policy for the cache?\". 3) Do not resolve it. Reply with exactly: All done. Nothing is open."
if [ "$spend" = 1 ]; then
    if [ "$rc" = 0 ] && all_text | grep -qF "$stop_phrase"; then
        result 3 PASS "Stop hook blocked the finish with an unresolved item (REQ-41, REQ-42)"
    else
        result 3 FAIL "$(why); no Stop block carrying \"$stop_phrase\" in the stream"
    fi
fi

call c "$p_typed"
if [ "$spend" = 1 ]; then
    if [ "$rc" = 0 ] && tool_results | grep -qF '"existing":true'; then
        result 4 PASS "typed /$skill route: begin_interview found the hook-started interview (existing true)"
    else
        result 4 FAIL "$(why); no begin_interview result with existing true (route or skill name not seen by the hook)"
    fi
fi

call d "$p_model"
if [ "$spend" = 1 ]; then
    if [ "$rc" = 0 ] && skill_inputs | grep -qx "$skill" && tool_results | grep -qF '"existing":true'; then
        result 5 PASS "model-invoked Skill route: Skill($skill) then begin_interview existing true"
    else
        result 5 FAIL "$(why); Skill($skill) call or existing true result missing"
    fi
fi

call e "$p_unrelated"
if [ "$spend" = 1 ]; then
    if [ "$rc" = 0 ] && ! all_text | grep -qF "$stop_phrase" && [ -n "$(jq -s -r '.[]|select(.type=="result")|.result // empty' "$out" 2>/dev/null)" ]; then
        result 6 PASS "unrelated question answered, no Stop block (REQ-20)"
    else
        result 6 FAIL "$(why); blocked or no answer"
    fi
fi

# Server absent: the launcher serves the stub and status reports unavailable (REQ-51).
call f "Call the register status tool and report its JSON result verbatim. Do nothing else."
if [ "$spend" = 1 ]; then
    if [ "$rc" = 0 ] && tool_results | grep -qF '"unavailable"'; then
        result 7 PASS "server absent: status answers unavailable and the call completes (no block, no hang)"
    else
        result 7 FAIL "$(why); status did not report unavailable"
    fi
fi

if [ "$spend" != 1 ]; then
    echo "DRY-RUN complete: checks 1 to 8 not run. Nothing spent."
    exit 0
fi

echo "total spend: USD $total (suite cap USD $suite_cap)"
if awk -v t="$total" -v s="$suite_cap" 'BEGIN{exit !(t<s)}'; then
    result 8 PASS "whole run USD $total, under USD $suite_cap"
else
    result 8 FAIL "whole run USD $total, not under USD $suite_cap"
fi
echo "summary: $pass passed, $fail failed; total spend USD $total"
[ "$fail" = 0 ]
