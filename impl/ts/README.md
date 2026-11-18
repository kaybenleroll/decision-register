# regspike-ts

TypeScript build of the decision register MCP server (working name). Behavioural reference: `../rust`.
Needs Node 26 (`.nvmrc`). Build the single-file bundle: `npm ci && npm run build` (writes `dist/regspike.mjs`); launch it with `node dist/regspike.mjs`.
Checks: `npm run lint` (Biome; `npm run format` applies fixes), `npm run typecheck`, `npm test` (offline, `node:test`). Suite: `python3 -I conformance/run.py -- node impl/ts/dist/regspike.mjs`.
