#!/usr/bin/env node
// Read-only preflight: is this machine ready to `npm run dev`? Starts nothing, changes nothing.
//
//   npm run doctor

import { spawnSync } from "node:child_process";
import net from "node:net";
import fs from "node:fs";
import path from "node:path";
import {
  REPO_ROOT,
  loadEnvFile,
  mask,
  n8nPort,
  portInUse,
  serviceEnv,
  serviceTarget,
  venvPythonPath,
} from "./lib/env.mjs";

const rows = [];
let problems = 0;
const ok = (name, detail) => rows.push(["✓", name, detail]);
const warn = (name, detail) => rows.push(["!", name, detail]);
const bad = (name, detail) => {
  problems++;
  rows.push(["✗", name, detail]);
};

const portFree = (port, host) =>
  new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => server.close(() => resolve(true)));
    server.listen(port, host);
  });

const fileEnv = loadEnvFile();
const env = serviceEnv(fileEnv);
const target = serviceTarget(env);

// Node
const [major] = process.versions.node.split(".").map(Number);
if (major >= 20) ok("Node.js", process.version);
else bad("Node.js", `${process.version} — need >= 20`);

// venv + Python
const python = venvPythonPath();
if (fs.existsSync(python)) {
  const res = spawnSync(python, ["--version"], { encoding: "utf8" });
  ok("Virtualenv", `${path.relative(REPO_ROOT, python)} (${(res.stdout + res.stderr).trim()})`);
} else {
  bad("Virtualenv", "missing — run `npm run setup`");
}

// .env
if (fs.existsSync(path.join(REPO_ROOT, ".env"))) ok(".env", `${Object.keys(fileEnv).length} vars`);
else bad(".env", "missing — run `npm run setup`");

// Keys (masked). Both degrade by design, so neither is fatal.
const keyState = (key, note) => {
  const value = env[key];
  if (value && !value.startsWith("your-")) ok(key, mask(value));
  else warn(key, `${value ? "placeholder" : "unset"} — ${note}`);
};
keyState("OPENROUTER_API_KEY", "n8n needs it for every LLM call (paste into the n8n credential)");
keyState("NEWSAPI_API_KEY", "/news/fetch falls back to RSS-only");
keyState("N8N_API_KEY", "/costs/harvest degrades — no costs rows, run still succeeds");

// DuckDB
const dbPath = env.DUCKDB_PATH || path.join(REPO_ROOT, "quant_service", "store.duckdb");
if (fs.existsSync(dbPath)) ok("DuckDB", dbPath);
else warn("DuckDB", `${dbPath} — not yet created; run \`npm run db:init\``);

// Ports
if (await portFree(target.port, target.bindHost)) ok("Service port", `${target.port} free`);
else bad("Service port", `${target.port} already in use — stop the other process`);

const n8n = n8nPort(env);
if (await portInUse(n8n)) warn("n8n port", `${n8n} in use — an n8n is already running; the runner will reuse it`);
else ok("n8n port", `${n8n} free`);

ok("n8n reaches service at", target.n8nUrl);

const width = Math.max(...rows.map(([, name]) => name.length));
console.log("");
for (const [sign, name, detail] of rows) console.log(` ${sign} ${name.padEnd(width)}  ${detail}`);
console.log("");
console.log(problems === 0 ? "Ready — run `npm run dev`." : `${problems} blocking problem(s) above.`);
process.exit(problems === 0 ? 0 : 1);
