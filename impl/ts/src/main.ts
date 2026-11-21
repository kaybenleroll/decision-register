import { createInterface } from "node:readline";

import { projectDir } from "./journal.ts";
import { Server } from "./protocol.ts";
import { Shutdown, SIGNALS } from "./shutdown.ts";
import { clockMs, prune } from "./store.ts";

// Installed first, so a signal during start-up still takes the shutdown path. Node resets any
// disposition inherited as ignored, so SIGHUP, SIGINT and SIGTERM are always handled, whatever
// the parent set. The shutdown is fully synchronous and a listener runs between event-loop
// callbacks, never inside a request, so there is no wait bound: a request is wholly processed
// or not started. A signal exits 128 + its number (129, 130, 143); EOF exits 0, explicitly: leaving
// by itself would let Node reset the dispositions in its tear-down and a late SIGTERM kill it (-15).
let server: Server | undefined;
const shutdown = new Shutdown(
  () => server?.onEof(),
  (code) => process.exit(code),
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
// After EOF the shutdown calls process.exit(0); stdout to a pipe or file is written synchronously.
