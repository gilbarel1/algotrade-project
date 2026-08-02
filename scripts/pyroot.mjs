#!/usr/bin/env node
// Run a Python command in the venv from the REPO ROOT (py.mjs runs from quant_service/).
//
// Repo-wide tooling has to see the whole tree and the root pyproject.toml: pytest
// collects tests/ and reads its config there, and ruff must lint tests/, eval/ and
// scripts/ too — run from quant_service/ they silently cover only part of the repo.
//
//   node scripts/pyroot.mjs -m pytest
//   node scripts/pyroot.mjs -m ruff check .

import { spawnSync } from "node:child_process";

import { REPO_ROOT, requireVenvPython, serviceEnv } from "./lib/env.mjs";

const args = process.argv.slice(2);
if (args.length === 0) {
  console.error("usage: node scripts/pyroot.mjs <python args...>");
  process.exit(2);
}

const res = spawnSync(requireVenvPython(), args, {
  cwd: REPO_ROOT,
  env: serviceEnv(),
  stdio: "inherit",
  windowsHide: true,
});
process.exit(res.status ?? 1);
