#!/usr/bin/env node
// Run a Python command in the venv, from quant_service/, with .env loaded and paths absolutised.
// Used by the smoke / db:init / costs npm scripts so they behave identically to `npm run dev`.
//
//   node scripts/py.mjs store_init.py
//   node scripts/py.mjs -m ops.cost_report
//   node scripts/py.mjs smoke_test.py @service-url    (@service-url -> QUANT_SERVICE_URL)

import { spawnSync } from "node:child_process";
import { SERVICE_DIR, requireVenvPython, serviceEnv, serviceTarget } from "./lib/env.mjs";

const args = process.argv.slice(2);
if (args.length === 0) {
  console.error("usage: node scripts/py.mjs <python args...>");
  process.exit(2);
}

const env = serviceEnv();
const argv = args.map((a) => (a === "@service-url" ? serviceTarget(env).baseUrl : a));

const res = spawnSync(requireVenvPython(), argv, {
  cwd: SERVICE_DIR,
  env,
  stdio: "inherit",
  windowsHide: true,
});
process.exit(res.status ?? 1);
