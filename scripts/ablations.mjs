#!/usr/bin/env node
// Run the ablation harness (`python -m eval.ablations`) from the repo root with .env
// loaded and the venv python. Same cwd/env contract as scripts/eval.mjs — the `eval/`
// package lives at the repo root, so this runs with cwd = REPO_ROOT.
//
//   node scripts/ablations.mjs                  # all arms (service up + OPENROUTER_API_KEY)
//   node scripts/ablations.mjs --critique-only   # critique arm only; free, no key needed

import { spawnSync } from "node:child_process";
import { REPO_ROOT, requireVenvPython, serviceEnv } from "./lib/env.mjs";

const res = spawnSync(requireVenvPython(), ["-m", "eval.ablations", ...process.argv.slice(2)], {
  cwd: REPO_ROOT,
  env: serviceEnv(),
  stdio: "inherit",
  windowsHide: true,
});
process.exit(res.status ?? 1);
