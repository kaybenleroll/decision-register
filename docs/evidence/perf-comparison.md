# Performance and footprint comparison: Rust (`regspike-rs`) against TypeScript (`regspike.mjs`)

Date 2026-11-19. Refs #32. This is not one of the retirement experiment's five changes and it does not touch ADR 0003: the experiment compares the two languages on tokens used (ADR 0003 section 4), and nothing here moves that decision. The numbers below inform only which build to run day to day.

Scope: measurement only. Nothing in `impl/`, `contract/` or the launcher was changed and no `claude` session was run (no spend). Behaviour that looked odd is reported, not fixed.

## 1. Method

Script: `conformance/bench/bench.py` (stdlib Python, no randomness, fixed corpus, fixed order; sections `startup`, `rss`, `latency`, `scaling`, `robust`, `artefacts`; `python3 conformance/bench/bench.py all` runs them, `report` renders the tables). It reuses `conformance/run.py`'s `group_rss_kib` (the S-90a RSS reader) and, like S-90a, starts each server on fresh data, project and working directories. Raw JSON goes to `.scratch/bench/` (gitignored, not committed).

Machine and tools (from the run's `meta.json`): AMD Ryzen 9 9955HX3D (32 logical CPUs), Linux `7.0.0-43-generic`, Node `v26.10.0`, `rustc 1.93.0 (254b59607 2026-01-19)`, Python 3.14.7. Both builds were rebuilt from the working tree before measuring (`npm run build` in `impl/ts`; `~/.cargo/bin/cargo build --release` in `impl/rust`). The machine was a normal desktop session, not quiesced: load average at the start of the full run was 4.33 (1 minute), and just before the repeat run it was 3.05 / 4.64 / 8.45 (1, 5, 15 minutes; the 15-minute figure includes the first scaling sweep). Other processes may have added noise. Page cache warm, CPU frequency governor untouched, no pinning.

How each measurement is taken:

1. Cold start: `plugin/bin/register-launch` with `REGSPIKE_IMPL=rs|ts`, exactly as the plugin starts it (`regspike-rs` reached through `PATH` by a symlink to the release binary, as an installed binary would be). Clock from `Popen` to the `initialize` reply line, so it includes fork/exec, the launcher's `/bin/sh` and `command -v`, runtime start (Node boot or Rust `main`) and one JSON-RPC round trip. Also the direct command (binary, or `node regspike.mjs`) to separate the launcher's own cost, and the first `status` call after `initialize` (first-use cost). 50 measured starts per configuration after 3 discarded rounds; the four configurations rotate through each round.
2. Peak RSS: 30 fresh servers per build through the launcher; summed `VmRSS` and `VmHWM` of the process group 1 s after `initialize` (idle), and 1 s after a scripted interview of 25 `log_item` and 20 `resolve` calls (45 model-style calls; user-sourced answers need a user prompt each, so 20 untimed `note_prompt` hook calls interleave), plus `status`, 3 `note_prompt` and 3 `check_stop`.
3. Per-call latency: round trip from write to the reply line over stdio (the clock stops before the reply is parsed), 30 untimed warm-up calls then 200 timed per tool per server instance, 3 instances per build (n = 600 per tool). Hook-style calls carry no `_meta`; model-style calls carry `claudecode/toolUseId`. State: an active interview with the hook session established first (`skill_started`, then `begin_interview`), up to 260 items. `check_stop` sends a single labelled request about an open item (`Q<n>: ...`), which the classifier allows as a question turn, so every call does the same work and no cap is spent; the call includes the journal flush that `check_stop` triggers. `*_no_interview` rows are the same hooks with no interview, which is what every prompt in an unrelated session costs.
4. Scaling: valid `journal_schema` 2 journals generated deterministically, resumed with `resume` on a fresh server per repetition (30 repetitions per size and build; replay time is the `resume` round trip). Two shapes: `churn` (40 items, 20 open, then hook/prompt/hook/stop cycles, so the event count grows and the register does not) and `items` (resolved item/resolve pairs plus 20 open items, so the register itself grows to about n/2 items; deliberately extreme). Sizes 1,000, 10,000 and 100,000 events. After the last replay of each configuration, 30 calls each of `status`, `check_stop`, `note_prompt` and `log_item` on that same server.
5. Artefacts: sizes with `stat`, build times in a copy under `.scratch/` (so the tree is untouched) with `cargo build --release --offline` after deleting `target/` and `npm run build` after deleting `dist/`, 3 each; `npm ci --offline` once.
6. Robustness: a signal sent to the server pid (the launcher `exec`s, so it is the server) with 3 items queued and no Stop check yet, and with one Stop check before the last event; then the next server is started on the same data and asked to `resume`. SIGINT, SIGTERM, SIGHUP, SIGKILL and stdin EOF (control). Dispositions are reset to default in the harness so the result is the server's own behaviour.

Statistics are median, p95 (nearest rank: the 95th percentile value of the sorted sample) and max. Latencies in ms.

Limits: the client is a Python process over pipes, not Claude Code's MCP client, so absolute figures are lower bounds on what a hook costs end to end. The two builds see the same client, so the differences are fair. Claude Code's own hook dispatch and hook time limits were not measured and are not cited.

## 2. Headline

| | Rust | TypeScript |
|---|---|---|
| Cold start to `initialize`, through the launcher: median / p95 / max (ms) | 4.1 / 5.0 / 5.8 | 41.0 / 45.8 / 46.6 |
| Idle RSS, median (max), MiB | 2.8 (2.8) | 53.4 (53.5) |
| RSS after a 45-call interview, median (max), MiB | 3.0 (3.0) | 55.3 (55.4) |
| `note_prompt`, warm: median / p95 / max (ms) | 0.017 / 0.023 / 0.101 | 0.019 / 0.075 / 0.478 |
| `skill_started`, warm: median / p95 / max (ms) | 0.016 / 0.020 / 0.081 | 0.016 / 0.035 / 0.139 |
| `check_stop`, warm: median / p95 / max (ms) | 0.089 / 0.125 / 0.265 | 0.144 / 0.259 / 0.366 |
| Artefact | 997,816 byte binary | 139,787 byte bundle plus Node 26 |
| Runtime dependencies | glibc, `libgcc_s` | Node 26 or later (150 MB binary) |
| Build from clean | 4.4 s (3 runs: 4.36 to 4.43) | 0.12 s bundle; `npm ci` 1.1 s |

## 3. Raw numbers

Cold start (ms), n = 50:

| Configuration | Metric | median | p95 | max |
|---|---|---|---|---|
| rs, launcher | to `initialize` reply | 4.1 | 5.0 | 5.8 |
| rs, launcher | first `status` call after it | 0.05 | 0.07 | 0.09 |
| rs, direct | to `initialize` reply | 1.1 | 1.6 | 1.7 |
| rs, direct | first `status` call after it | 0.05 | 0.07 | 0.09 |
| ts, launcher | to `initialize` reply | 41.0 | 45.8 | 46.6 |
| ts, launcher | first `status` call after it | 0.64 | 0.88 | 1.00 |
| ts, direct | to `initialize` reply | 39.4 | 42.9 | 45.4 |
| ts, direct | first `status` call after it | 0.63 | 0.96 | 1.02 |

The launcher adds about 3 ms to the Rust start (1.1 to 4.1) and about 1.6 ms to the TypeScript one (39.4 to 41.0; close to the noise). For reference, S-90a (5 starts, launcher-free) measured 1 ms and 40 ms on 2026-11-18.

Resident memory (MiB, process group, n = 30):

| Metric | Rust median / p95 / max | TypeScript median / p95 / max |
|---|---|---|
| idle RSS | 2.8 / 2.8 / 2.8 | 53.4 / 53.5 / 53.5 |
| idle peak (`VmHWM`) | 2.8 / 2.8 / 2.8 | 53.4 / 53.5 / 53.5 |
| RSS after the scripted interview | 3.0 / 3.0 / 3.0 | 55.3 / 55.4 / 55.4 |
| peak after the scripted interview | 3.0 / 3.0 / 3.0 | 55.3 / 55.4 / 55.4 |
| threads, idle | 1 | 7 |

Per-call latency, warm (ms, n = 600 each):

| Tool | Rust median / p95 / max | TypeScript median / p95 / max |
|---|---|---|
| `note_prompt` (interview active) | 0.017 / 0.023 / 0.101 | 0.019 / 0.075 / 0.478 |
| `skill_started` (active) | 0.016 / 0.020 / 0.081 | 0.016 / 0.035 / 0.139 |
| `check_stop` (active, 260 items, includes journal flush) | 0.089 / 0.125 / 0.265 | 0.144 / 0.259 / 0.366 |
| `note_prompt` (no interview) | 0.012 / 0.017 / 0.085 | 0.016 / 0.028 / 0.299 |
| `skill_started` (no interview) | 0.012 / 0.016 / 0.081 | 0.015 / 0.025 / 0.355 |
| `check_stop` (no interview) | 0.012 / 0.017 / 0.161 | 0.016 / 0.028 / 0.310 |
| `log_item` | 0.030 / 0.055 / 0.193 | 0.019 / 0.034 / 0.166 |
| `resolve` (user-sourced) | 0.017 / 0.024 / 0.057 | 0.021 / 0.039 / 0.372 |
| `status` (about 260 items) | 1.236 / 1.471 / 2.589 | 0.279 / 0.446 / 0.989 |

Scaling. Replay is the `resume` call on a fresh server (n = 30 per cell). The other columns are medians of 30 calls on the resumed server; p95 in the JSON. `items` rows: the register holds about n/2 items.

| Shape | Events | Journal MiB | Build | replay median / p95 / max (ms) | `status` median (ms) | `check_stop` median (ms) | `log_item` median (ms) | RSS after resume (MiB) | peak RSS (MiB) |
|---|---|---|---|---|---|---|---|---|---|
| churn | 1,000 | 0.1 | rs | 2.0 / 2.8 / 2.9 | 0.189 | 0.065 | 0.023 | 3.0 | 3.0 |
| churn | 1,000 | 0.1 | ts | 7.0 / 10.0 / 10.7 | 0.096 | 0.188 | 0.021 | 58.7 | 58.7 |
| churn | 10,000 | 1.2 | rs | 16.0 / 17.7 / 17.9 | 0.187 | 0.063 | 0.023 | 4.2 | 4.2 |
| churn | 10,000 | 1.2 | ts | 26.3 / 28.9 / 29.1 | 0.132 | 0.191 | 0.021 | 69.4 | 69.4 |
| churn | 100,000 | 12.0 | rs | 166.9 / 173.6 / 176.3 | 0.197 | 0.070 | 0.027 | 16.4 | 16.4 |
| churn | 100,000 | 12.0 | ts | 194.7 / 203.7 / 206.4 | 0.087 | 0.180 | 0.019 | 125.4 | 125.4 |
| items | 1,000 | 0.2 | rs | 3.4 / 4.7 / 4.9 | 2.476 | 0.068 | 0.095 | 3.6 | 4.1 |
| items | 1,000 | 0.2 | ts | 11.0 / 13.6 / 14.1 | 0.628 | 0.199 | 0.024 | 66.9 | 66.9 |
| items | 10,000 | 2.0 | rs | 99.4 / 106.2 / 106.5 | 25.306 | 0.108 | 0.788 | 9.5 | 17.0 |
| items | 10,000 | 2.0 | ts | 262.8 / 273.1 / 273.1 | 5.155 | 0.399 | 0.070 | 108.1 | 108.1 |
| items | 100,000 | 20.0 | rs | 11,139 / 11,897 / 11,907 | 262.9 | 0.294 | 8.690 | 84.1 | 150.8 |
| items | 100,000 | 20.0 | ts | 8,081 / 8,894 / 9,058 | 72.3 | 1.512 | 0.551 | 245.5 | 365.1 |

(`status` replies are 0.16 MiB at 1,000 `items` events, 1.57 MiB at 10,000 and 15.85 MiB at 100,000; about 0.01 MiB for `churn`.)

Artefacts and install:

| | Rust | TypeScript |
|---|---|---|
| Deliverable | `target/release/regspike-rs`, 997,816 bytes (422,182 gzipped) | `dist/regspike.mjs`, 139,787 bytes (29,180 gzipped), esbuild single file |
| Runtime dependencies | none from crates (`serde_json`, `sha2` and their 11 transitive crates are linked in); dynamic: `libc.so.6`, `libgcc_s.so.1` (glibc) | none from npm (`dependencies` is empty); needs Node `>=26` (`engines`; the bundle targets `node26`) |
| What a user must install | the binary | the bundle plus Node 26 or later; the Node binary here is 149,711,504 bytes (143 MiB) |
| Build from clean (not needed by users) | 4.43, 4.42, 4.36 s wall (about 14 s CPU), dependencies already in `~/.cargo/registry` (19 MB); toolchain 1.93.0 is 1.3 GB under `~/.rustup` | bundle 0.13, 0.11, 0.12 s; `npm ci --offline` 1.06 s; `node_modules` 139 MiB (dev tooling only) |

Both builds embed `contract/tools.json` at build time (`include_str!`, esbuild JSON import), so neither reads it at run time.

Robustness (`bench.py robust`; identical in both builds unless stated). `EOF` is the control: stdin closed, the contract's exit path.

| Case | Rust | TypeScript |
|---|---|---|
| EOF, no Stop check before it | exit 0; 6 events on disk; pid file removed; decision log written | same |
| SIGINT, SIGTERM, SIGHUP, SIGKILL, no Stop check | killed by the signal (return code -2, -15, -1, -9); 1 event on disk; pid file left; no decision log | same |
| same, one Stop check before the last event | killed by the signal; 7 events on disk (8 on EOF); pid file left; no decision log | same |
| A new server on the same data directory, then `resume` | succeeds; reports `last_event` 1 or 7 (the events after the last flush are gone) | same |
| Signal already ignored by the parent (`nohup`, `cmd &` in a non-interactive shell) | SIGHUP and SIGINT stay ignored: still running after 2 s | the signal is reset to its default and kills the process (return codes -1, -2) |
| 1 MiB / 16 MiB `note_prompt` (median, ms; n = 10) | 3.8 / 71.7 | 4.0 / 72.4 |
| RSS after those prompts (MiB) | 6.9 | 149.7 |

SIGINT: neither build installs a handler, so SIGINT, like SIGTERM and SIGHUP, ends the process at once by the default action. The EOF path (queue flush, decision log, pid file removal) is not reached by any signal; whatever the write-behind queue holds since the last Stop-hook flush (or `end_interview`) is lost, and the stale pid file is handled by the next server's start-time liveness check (`resume` succeeded in every signal case). This is as `contract/journal.md` describes for a hard crash; it is reported, not fixed. Nothing differs between the builds except the inherited-ignored behaviour in the table (a Node runtime behaviour, confirmed by running).

Update 2026-11-21 (issue #90): superseded for signals. Both builds now handle SIGINT, SIGTERM and SIGHUP like stdin EOF, and handle them even if the parent ignored them; see `docs/evidence/signal-shutdown.md`. The paragraph above and the robustness table describe the builds as measured on 2026-11-19.

## 4. Interpretation

Material at human timescales: none of the speed or memory differences.

- Hook latency. Every hook-facing call, including the journal flush in `check_stop`, completes in under 0.5 ms (max over 600 calls) in both builds; p95 is at most 0.26 ms. A prompt costs two hook calls (`note_prompt`, `check_stop`), so a few tenths of a millisecond against a 100 ms threshold for feeling instant. Rust is about 1.6 times faster on `check_stop` and has a tighter tail, and TypeScript is no slower on `skill_started` or `log_item`; this is noise at the scale a person can perceive. The end-to-end hook time inside Claude Code was not measured.
- Cold start. 4 ms against 41 ms, once per session when the plugin's server starts; both are far under the REQ-90 limit of 1,000 ms and invisible next to Claude Code's own start-up (not measured). The launcher costs 1.6 to 3 ms.
- Memory. 2.8 against 53.4 MiB idle, 3.0 against 55.3 after an interview: a 19-fold ratio, trivial in absolute terms on a workstation. REQ-90's limit is 200 MiB.
- Scaling. A real interview has tens to a few hundred items and under a couple of thousand events. At that size everything above is a few milliseconds or less in both builds, including replay (2.0 against 7.0 ms at 1,000 events). Replay stays under 0.2 s for a 100,000-event journal with a small register in both. The only places the builds separate in a way a person could notice are at register sizes no interview reaches: replay of a register of 50,000 items takes 11.1 s (Rust) and 8.1 s (TypeScript), both superlinear in the item count; there Rust's `status` (263 ms against 72 ms) and `log_item` (8.7 ms against 0.55 ms) also grow faster. Even at 500 items Rust's `status` is 2.5 ms against 0.63 ms and at 5,000 items 25 against 5 ms; `status` is model-facing, not a hook. TypeScript's `check_stop` also grows with the register (0.2, 0.4, 1.5 ms at 500, 5,000, 50,000 items) where Rust's stays at 0.07 to 0.3 ms. These are code observations for later, not reasons to choose.
- Noise. The workload was run three times for cold start and warm latency (the scaling sweep twice). The first full run started at 1-minute load 4.33 and overlapped my own polling shells: its warm calls and small-journal replays were 1.5 to 4 times slower than the other two for both builds. The second and third runs agree closely. Medians over the three runs: Rust launcher start 4.4, 4.1, 4.1 ms; TypeScript launcher start 43.0, 41.0, 41.7 ms; `check_stop` Rust 0.151, 0.089, 0.087 ms and TypeScript 0.312, 0.144, 0.144 ms. The tables above are the second run (cold start, latency, scaling; the third run's cold start and latency agree within a few percent). RSS, robustness and artefacts come from the first run (not timing-sensitive). The ranking of the builds was the same in every run.

Where the builds differ in a way a user would notice: what has to be installed. The Rust build is one 1 MB file with glibc; the TypeScript build is a 136 KiB file that works only with Node 26 or later, which the user must have or install (143 MiB on disk here). Node 26 is the newer of the two requirements and the launcher's `ts` route gives only a "binary not found" stub when it is missing.

## 5. Recommendation

Start with the Rust build as the one to run: it is the launcher's default (`REGSPIKE_IMPL` unset means `rs`), needs nothing installed beyond the one binary, and is no worse on any measure a person can feel; its lower memory, faster start and tighter hook tails are real but small. The TypeScript build is a fine second: it performs the same at human timescales, and it replays and serves a very large register faster, which does not matter for an interview. The choice on speed alone is a coin flip, so the install footprint (no Node 26 prerequisite) decides it.

Caveat: this does not bear on the retirement experiment. ADR 0003 decides that on tokens only, so a faster or smaller build gains nothing in it. These measurements say which build to run first, not which language to retire.

Reproduce: `python3 conformance/bench/bench.py build`, then `python3 conformance/bench/bench.py all` (about 15 minutes; the scaling sweep is most of it), then `report`. Raw output stays in `.scratch/bench/out/` (not committed).
