import { createInterface } from "node:readline";

import { Server } from "./protocol.ts";

const server = new Server();
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  if (line.trim() === "") return;
  const reply = server.handleLine(line);
  if (reply !== null) process.stdout.write(reply + "\n");
});
rl.on("close", () => server.onEof());
// The process exits by itself once stdin reaches EOF and stdout has drained.
