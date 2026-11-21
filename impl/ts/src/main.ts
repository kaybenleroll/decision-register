import { createInterface } from "node:readline";

import { projectDir } from "./journal.ts";
import { Server } from "./protocol.ts";
import { Shutdown, SIGNALS } from "./shutdown.ts";
import { clockMs, prune } from "./store.ts";

// Installed first, so a signal during start-up still takes the shutdown path. Node resets any
// disposition inherited as ignored, so SIGHUP, SIGINT and SIGTERM are always handled, whatever
// the parent set. The shutdown is fully synchronous and a listener runs between event-loop
// callbacks, never inside a request, so there is no wait bound: a request is wholly processed
// or not started. A signal exits 128 + its number (129, 130, 143) at once; EOF exits 0, explicitly:
// leaving by itself would let Node reset the dispositions in its tear-down and a late SIGTERM kill
// it (-15). stdout is not written synchronously here: a pipe is non-blocking and what it does not
// take at once stays queued in libuv, which process.exit() discards. So EOF first waits for the
// queue (an empty write completes after the earlier ones), with the listeners still installed; a
// signal during that wait ends it, and so does the bound of Shutdown (DRAIN_BOUND_MS, 5 s; an
// unref'd timer): a client that closed stdin and never reads stdout would otherwise keep the server
// alive until SIGKILL. The journal is flushed before the wait, so the bound drops only the reply
// tail. A signal exit does not wait: it may cut a reply the client has
// not read (contract/runtime.md, "Signals and shutdown limits").
let server: Server | undefined;
const shutdown = new Shutdown(
  () => server?.onEof(),
  (code, drain) =>
    drain
      ? process.stdout.write("", () => {
          shutdown.drained();
          process.exit(code);
        })
      : process.exit(code),
);
for (const [name, num] of Object.entries(SIGNALS)) {
  process.on(name, () => shutdown.atSignal(num));
}

prune(projectDir(), clockMs());
const app = new Server();
server = app;
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  if (line.trim() === "") return;
  const reply = app.handleLine(line);
  if (reply !== null) process.stdout.write(reply + "\n");
});
rl.on("close", () => shutdown.atEof());
// After EOF the shutdown drains stdout, then calls process.exit(0).
