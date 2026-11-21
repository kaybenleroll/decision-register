/** Shutdown coordination: stdin EOF and the termination signals share one once-guarded path. */

/** Signals that take the EOF path, with their numbers (the exit code is 128 + number). */
export const SIGNALS = { SIGHUP: 1, SIGINT: 2, SIGTERM: 15 } as const;

export class Shutdown {
  private ran = false;
  private fromEof = false;
  private readonly run: () => void;
  private readonly exit: (code: number) => void;

  /**
   * `run` is the shared shutdown work (flush, decision log, pid file); it is called at most once.
   * `exit` ends the process: 0 after an EOF shutdown, 128 + n after a signal. Ending it explicitly
   * after EOF matters: if Node left by itself, it would reset the signal dispositions to their
   * default action during tear-down, and a SIGTERM in that window would kill the process (-15).
   * `process.exit` keeps the handlers installed until the process is gone.
   */
  constructor(run: () => void, exit: (code: number) => void) {
    this.run = run;
    this.exit = exit;
  }

  /** stdin reached EOF. A no-op if shutdown already ran. Exits 0 once the shutdown is done. */
  atEof(): void {
    if (this.ran) return;
    this.ran = true;
    this.fromEof = true;
    this.run();
    this.exit(0);
  }

  /**
   * A termination signal arrived. If an EOF shutdown already ran, do nothing (it exits 0 itself). Otherwise run the shutdown (unless a signal already did) and exit 128 + signal.
   */
  atSignal(num: number): void {
    if (this.fromEof) return;
    try {
      if (!this.ran) {
        this.ran = true;
        this.run();
      }
    } finally {
      this.exit(128 + num);
    }
  }
}
