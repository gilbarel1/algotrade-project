#!/usr/bin/env node
// Run the quant service and n8n together, with every env var placed in the right process.
//
//   npm run dev                 both
//   npm run dev -- --reload     both, uvicorn auto-reload
//   npm run dev:service         quant service only
//   npm run dev:n8n             n8n only
//
// What this handles for you (each one is a documented footgun — see README "Known gotchas"):
//   * .env reaches the *service* process (the service does not read .env itself)
//   * DUCKDB_PATH / HF_HOME / REPORT_DIR become absolute (uvicorn runs with cwd=quant_service/)
//   * n8n gets N8N_BLOCK_ENV_ACCESS_IN_NODE=false, or every {{ $env.* }} expression is denied
//   * n8n reaches the service at 127.0.0.1, never localhost (IPv6 -> ECONNREFUSED ::1:8000)

import { spawn } from "node:child_process";
import fs from "node:fs";
import {
  REPO_ROOT,
  SERVICE_DIR,
  loadEnvFile,
  mask,
  n8nPort,
  portInUse,
  requireVenvPython,
  serviceEnv,
  serviceTarget,
} from "./lib/env.mjs";

const args = process.argv.slice(2);
const only = (args.find((a) => a.startsWith("--only=")) || "").split("=")[1] || "all";
const reload = args.includes("--reload");
const runService = only === "all" || only === "service";
const runN8n = only === "all" || only === "n8n";

const COLOR = process.env.NO_COLOR ? null : { svc: "\x1b[36m", n8n: "\x1b[35m", dim: "\x1b[2m", off: "\x1b[0m" };
const paint = (tag, text) => (COLOR ? `${COLOR[tag]}${text}${COLOR.off}` : text);
const note = (text) => console.log(COLOR ? `${COLOR.dim}${text}${COLOR.off}` : text);

const children = [];
let shuttingDown = false;
let exitCode = 0;

function killTree(child) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === "win32") {
    // child.kill() leaves the npx -> n8n grandchild orphaned; taskkill /T reaps the tree.
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const { child } of children) killTree(child);
}

/** Spawn a child, prefixing each of its output lines with [tag]. */
function start(tag, command, argv, options) {
  const child = spawn(command, argv, {
    ...options,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const prefix = paint(tag, `[${tag}]`);
  const tail = [];
  const pipe = (stream) => {
    let buffer = "";
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => {
      buffer += chunk;
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        tail.push(line);
        if (tail.length > 20) tail.shift();
        console.log(`${prefix} ${line}`);
      }
    });
  };
  pipe(child.stdout);
  pipe(child.stderr);

  const entry = { tag, child, tail };
  children.push(entry);

  child.on("exit", (code, signal) => {
    if (shuttingDown) return;
    console.log(`${prefix} exited (${signal ? `signal ${signal}` : `code ${code}`})`);
    exitCode = code === 0 ? exitCode : (code ?? 1);
    shutdown(); // one dies, both die
  });
  child.on("error", (err) => {
    console.error(`${prefix} failed to start: ${err.message}`);
    exitCode = 1;
    shutdown();
  });
  return entry;
}

async function waitForHealth(url, entry, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (shuttingDown || entry.child.exitCode !== null) return false;
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return true;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

async function main() {
  const fileEnv = loadEnvFile();
  const env = serviceEnv(fileEnv);
  const target = serviceTarget(env);

  if (!fs.existsSync(`${REPO_ROOT}/.env`)) {
    note("No .env found — using defaults. Run `npm run setup` to create one from .env.example.");
  }

  console.log("Starting the TA-35 stack");
  note(`  quant service   ${target.baseUrl}   (cwd quant_service/)`);
  note(`  n8n             http://localhost:${n8nPort(env)}   -> service at ${target.n8nUrl}`);
  note(`  NEWSAPI_API_KEY ${mask(env.NEWSAPI_API_KEY)}   N8N_API_KEY ${mask(env.N8N_API_KEY)}`);
  if (env.DUCKDB_PATH) note(`  DUCKDB_PATH     ${env.DUCKDB_PATH}`);
  console.log("");

  let service = null;
  if (runService) {
    const python = requireVenvPython();
    const uvicornArgs = [
      "-m", "uvicorn", "app:app",
      "--host", target.bindHost,
      "--port", String(target.port),
      ...(reload ? ["--reload"] : []),
    ];
    service = start("svc", python, uvicornArgs, { cwd: SERVICE_DIR, env });

    // Gate n8n on the service being live, so a dead service reads as a dead service
    // rather than as an ECONNREFUSED inside some HTTP node twenty minutes later.
    if (runN8n) {
      const healthy = await waitForHealth(target.healthUrl, service);
      if (!healthy) {
        if (!shuttingDown) {
          console.error(`\nThe quant service never answered ${target.healthUrl}. Last output:`);
          for (const line of service.tail) console.error(`  ${line}`);
          exitCode = 1;
          shutdown();
        }
        return;
      }
      note(`\nService healthy at ${target.healthUrl} — starting n8n\n`);
    }
  }

  if (runN8n && !shuttingDown) {
    // An n8n you started yourself is left alone and reused — killing it, or aborting the
    // whole run over it, would both be worse than saying so.
    const port = n8nPort(env);
    if (await portInUse(port)) {
      note(`n8n is already running on port ${port} — leaving it alone and reusing it.`);
      note(`  It must have been started with N8N_BLOCK_ENV_ACCESS_IN_NODE=false and`);
      note(`  QUANT_SERVICE_URL=${target.n8nUrl}, or its {{ $env.* }} expressions will fail.`);
      note(`  Stop it and re-run \`npm run dev\` to have the runner set those for you.`);
      return;
    }

    // Node >= 20 refuses to spawn npx.cmd directly (EINVAL, CVE-2024-27980), and
    // `shell: true` is deprecated with an args array — so invoke cmd.exe ourselves.
    // taskkill /T on the cmd.exe pid still reaps the whole npx -> n8n tree.
    const [npx, npxArgs] =
      process.platform === "win32"
        ? ["cmd.exe", ["/d", "/s", "/c", "npx -y n8n"]]
        : ["npx", ["-y", "n8n"]];
    start("n8n", npx, npxArgs, {
      cwd: REPO_ROOT,
      env: {
        ...env,
        N8N_BLOCK_ENV_ACCESS_IN_NODE: "false", // the gate is `!== 'false'` — unset means blocked
        QUANT_SERVICE_URL: target.n8nUrl,
      },
    });
  }
}

for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, shutdown);
process.on("exit", () => shutdown());

main().catch((err) => {
  console.error(err.message);
  exitCode = 1;
  shutdown();
});

// Keep the exit code of the first child that failed.
process.on("beforeExit", () => process.exit(exitCode));
