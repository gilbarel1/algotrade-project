#!/usr/bin/env node
// Run the evaluation harness (`python -m eval.run`) from the repo root with .env
// loaded and the venv python. The `eval/` package lives at the repo root (not under
// quant_service/), so — unlike scripts/py.mjs — this runs with cwd = REPO_ROOT.
//
//   node scripts/eval.mjs            # full run (needs the service up + OPENROUTER_API_KEY)
//   node scripts/eval.mjs --no-llm   # FinBERT/HeBERT arm only

import { spawnSync } from "node:child_process";
import { REPO_ROOT, requireVenvPython, serviceEnv } from "./lib/env.mjs";

const res = spawnSync(requireVenvPython(), ["-m", "eval.run", ...process.argv.slice(2)], {
  cwd: REPO_ROOT,
  env: serviceEnv(),
  stdio: "inherit",
  windowsHide: true,
});
process.exit(res.status ?? 1);
