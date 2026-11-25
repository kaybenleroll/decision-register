/** Shutdown coordination: stdin EOF and the termination signals share one once-guarded path. */

/** Signals that take the EOF path, with their numbers (the exit code is 128 + number). */
export const SIGNALS = { SIGHUP: 1, SIGINT: 2, SIGTERM: 15 } as const;

/**
 * How long an EOF shutdown waits for the client to read the queued stdout before it gives up and exits 0
 * with the reply tail dropped. The figure is the Rust build's SIGNAL_BOUND (impl/rust/src/shutdown.rs),
 * reused so the two builds share one declared number; the reason for a bound at all is that a client
 * that has closed stdin but never reads stdout would otherwise keep the server alive until SIGKILL.
 * Rust mirrors the figure (`DRAIN_BOUND` in impl/rust/src/main.rs): its reply writer runs on its own thread.
 */
export const DRAIN_BOUND_MS = 5000;

export class Shutdown {
  private ran = false;
  private fromEof = false;
  private draining = false;
  private readonly run: () => void;
  private readonly exit: (code: number, drain: boolean) => void;
  private readonly drainBoundMs: number;
  private drainTimer: ReturnType<typeof setTimeout> | undefined;

  /**
   * `run` is the shared shutdown work (flush, decision log, pid file); it is called at most once.
   * `exit` ends the process: 0 after an EOF shutdown, 128 + n after a signal. Its `drain` argument is true
   * only for the EOF shutdown: wait until the queued stdout has been written, then exit. A signal during
   * that wait calls `exit(0, false)`: the first trigger (EOF) keeps deciding the status, the wait ends.
   * A signal shutdown never drains (it may cut a reply the client has not read). Ending it explicitly
   * after EOF matters: if Node left by itself, it would reset the signal dispositions to their
   * default action during tear-down, and a SIGTERM in that window would kill the process (-15).
   * `process.exit` keeps the handlers installed until the process is gone.
   * The drain wait is bounded by `drainBoundMs` (default DRAIN_BOUND_MS): when it expires before `drained()`
   * is called, `exit(0, false)` ends the process with the queued tail dropped (the journal is already
   * flushed). The timer is `unref()`'d, so it never keeps the process alive by itself; the pending
   * stdout write does that.
   */
  constructor(
    run: () => void,
    exit: (code: number, drain: boolean) => void,
    drainBoundMs: number = DRAIN_BOUND_MS,
  ) {
    this.run = run;
    this.exit = exit;
    this.drainBoundMs = drainBoundMs;
  }

  /** The stdout drain finished: stop the bound timer. Call it just before ending the process. */
  drained(): void {
    if (this.drainTimer !== undefined) clearTimeout(this.drainTimer);
    this.drainTimer = undefined;
  }

  /** stdin reached EOF. A no-op if shutdown already ran. Exits 0 once the shutdown is done and stdout is drained, or after the drain bound. */
  atEof(): void {
    if (this.ran) return;
    this.ran = true;
    this.fromEof = true;
    this.run();
    this.draining = true;
    this.drainTimer = setTimeout(() => this.exit(0, false), this.drainBoundMs);
    this.drainTimer.unref();
    this.exit(0, true);
  }

  /**
   * A termination signal arrived. If an EOF shutdown already ran, do nothing, except that a signal during the stdout drain ends the wait and exits 0. Otherwise run the shutdown (unless a signal already did) and exit 128 + signal.
   */
  atSignal(num: number): void {
    if (this.fromEof) {
      if (this.draining) this.exit(0, false);
      return;
    }
    try {
      if (!this.ran) {
        this.ran = true;
        this.run();
      }
    } finally {
      this.exit(128 + num, false);
    }
  }
}
