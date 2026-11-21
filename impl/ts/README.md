# register-ts

TypeScript build of the decision register MCP server. Behavioural reference: `../rust`.
Needs Node 26 (`.nvmrc`). Build the single-file bundle: `npm ci && npm run build` (writes `dist/register.mjs`); launch it with `node dist/register.mjs`.
Checks: `npm run lint` (Biome; `npm run format` applies fixes), `npm run typecheck`, `npm test` (offline, `node:test`). Suite: `python3 -I conformance/run.py -- node impl/ts/dist/register.mjs`.
