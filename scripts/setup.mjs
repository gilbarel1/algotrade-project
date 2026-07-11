#!/usr/bin/env node
// One-command bootstrap for a fresh clone. Idempotent — safe to re-run at any time.
//
//   npm run setup
//
// venv -> pip install -> playwright chromium -> .env -> DuckDB schema.
// It never overwrites an existing .env (it holds real keys).

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, SERVICE_DIR, serviceEnv, venvPythonPath } from "./lib/env.mjs";

const OK = "✓";
let step = 0;
const heading = (text) => console.log(`\n${++step}. ${text}`);
const done = (text) => console.log(`   ${OK} ${text}`);

function run(command, args, options = {}) {
  const res = spawnSync(command, args, { stdio: "inherit", ...options });
  if (res.status !== 0) {
    console.error(`\nFailed: ${command} ${args.join(" ")}`);
    process.exit(res.status ?? 1);
  }
}

function findSystemPython() {
  const candidates = process.platform === "win32" ? ["python", "py"] : ["python3", "python"];
  for (const candidate of candidates) {
    const argv = candidate === "py" ? ["-3", "--version"] : ["--version"];
    const res = spawnSync(candidate, argv, { encoding: "utf8" });
    if (res.status !== 0) continue;
    const version = `${res.stdout}${res.stderr}`.trim();
    const m = version.match(/(\d+)\.(\d+)/);
    if (m && (Number(m[1]) > 3 || (Number(m[1]) === 3 && Number(m[2]) >= 11))) {
      return { command: candidate, prefix: candidate === "py" ? ["-3"] : [], version };
    }
  }
  return null;
}

const venvPython = venvPythonPath();

heading("Python 3.11+");
const systemPython = findSystemPython();
if (fs.existsSync(venvPython)) {
  done("virtualenv already exists — skipping the interpreter check");
} else if (!systemPython) {
  console.error("   No Python 3.11+ on PATH. Install it, then re-run `npm run setup`.");
  process.exit(1);
} else {
  done(`${systemPython.version} (${systemPython.command})`);
}

heading("Virtualenv at quant_service/.venv");
if (fs.existsSync(venvPython)) {
  done("already exists");
} else {
  run(systemPython.command, [...systemPython.prefix, "-m", "venv", ".venv"], { cwd: SERVICE_DIR });
  done("created");
}

heading("Python dependencies (quant_service/requirements.txt)");
run(venvPython, ["-m", "pip", "install", "--upgrade", "pip", "--quiet"]);
run(venvPython, ["-m", "pip", "install", "-r", "requirements.txt"], { cwd: SERVICE_DIR });
done("installed");

heading("Playwright Chromium (the Earnings agent scrapes Maya in a headless browser)");
run(venvPython, ["-m", "playwright", "install", "chromium"]);
done("ready");

heading("Environment file");
const envPath = path.join(REPO_ROOT, ".env");
if (fs.existsSync(envPath)) {
  done(".env already exists — left untouched");
} else {
  fs.copyFileSync(path.join(REPO_ROOT, ".env.example"), envPath);
  done("created .env from .env.example — fill in your keys");
}

heading("DuckDB schema");
// DUCKDB_PATH from .env is repo-root-relative; serviceEnv() absolutises it, so store_init
// writes the one true store.duckdb no matter which directory we launch it from.
const env = serviceEnv();
run(venvPython, ["store_init.py"], { cwd: SERVICE_DIR, env });

const filled = (key) => env[key] && !env[key].startsWith("your-");
const next = [];
if (!filled("OPENROUTER_API_KEY") || !filled("NEWSAPI_API_KEY")) {
  const keys = [];
  if (!filled("OPENROUTER_API_KEY")) keys.push("       OPENROUTER_API_KEY  (n8n -> every LLM call)          https://openrouter.ai");
  if (!filled("NEWSAPI_API_KEY")) keys.push("       NEWSAPI_API_KEY     (optional; RSS-only without it)  https://newsapi.org/register");
  next.push(["Add your keys to .env:", ...keys].join("\n"));
}
next.push("npm run dev        # quant service + n8n, both wired up");
next.push("npm run smoke      # in a second terminal — verifies the endpoints");

console.log(`\n${OK} Setup complete.\n`);
console.log("Next:");
next.forEach((line, i) => console.log(`  ${i + 1}. ${line}`));
console.log("\nImporting the n8n workflows and wiring the OpenRouter credential stays a UI step:");
console.log("  see n8n/README_credentials.md");
