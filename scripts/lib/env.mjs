// Shared helpers for the dev runner: .env loading, path resolution, venv discovery.
// Node stdlib only — the repo has no npm dependencies on purpose.

import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const REPO_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);
export const SERVICE_DIR = path.join(REPO_ROOT, "quant_service");

const DEFAULT_SERVICE_URL = "http://127.0.0.1:8000";

// Paths that .env states relative to the repo root, but that are read by a process
// whose cwd is quant_service/ — they must be absolutised or they resolve one level deep.
const REPO_RELATIVE_PATH_VARS = ["DUCKDB_PATH", "HF_HOME", "REPORT_DIR"];

/**
 * Parse a KEY=VALUE .env file. Handles `export ` prefixes, quoted values, and — because
 * .env is copied from .env.example — unquoted trailing `# comments`, which a naive parser
 * would glue onto the value.
 */
export function loadEnvFile(file = path.join(REPO_ROOT, ".env")) {
  if (!fs.existsSync(file)) return {};
  const env = {};
  for (const rawLine of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const match = line.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match) continue;
    const [, key] = match;
    let value = match[2].trim();
    const quote = value[0];
    if ((quote === '"' || quote === "'") && value.length > 1) {
      const end = value.indexOf(quote, 1);
      value = end === -1 ? value.slice(1) : value.slice(1, end);
    } else {
      value = value.replace(/\s+#.*$/, "").trim(); // strip trailing comment
    }
    env[key] = value;
  }
  return env;
}

/** Rewrite repo-root-relative path vars to absolute paths so cwd can't change their meaning. */
export function resolveRepoPaths(env, repoRoot = REPO_ROOT) {
  const out = { ...env };
  for (const key of REPO_RELATIVE_PATH_VARS) {
    const value = out[key];
    if (!value) continue;
    if (!path.isAbsolute(value)) out[key] = path.resolve(repoRoot, value);
  }
  return out;
}

/**
 * Build the environment for the quant service: real process env, then .env (a shell
 * override still wins), then absolute paths.
 */
export function serviceEnv(fileEnv = loadEnvFile()) {
  const merged = { ...fileEnv };
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined && value !== "") merged[key] = value;
  }
  return resolveRepoPaths(merged);
}

export function venvPythonPath(repoRoot = REPO_ROOT) {
  return process.platform === "win32"
    ? path.join(repoRoot, "quant_service", ".venv", "Scripts", "python.exe")
    : path.join(repoRoot, "quant_service", ".venv", "bin", "python");
}

/** The venv interpreter, or a pointed error — system Python is the cause of the
 *  CERTIFICATE_VERIFY_FAILED / "playwright not installed" degraded-agent confusion. */
export function requireVenvPython(repoRoot = REPO_ROOT) {
  const python = venvPythonPath(repoRoot);
  if (!fs.existsSync(python)) {
    throw new Error(
      `No virtualenv at ${path.relative(repoRoot, python)}.\n` +
        `Run  npm run setup  first (it creates the venv and installs requirements).`,
    );
  }
  return python;
}

/**
 * Single source of truth for where the service listens and how n8n reaches it.
 * n8n must use the IPv4 literal: Node resolves `localhost` to ::1 first, uvicorn binds
 * IPv4, and every HTTP node then fails with ECONNREFUSED ::1:8000.
 */
export function serviceTarget(env = {}) {
  const raw = env.QUANT_SERVICE_URL || DEFAULT_SERVICE_URL;
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`QUANT_SERVICE_URL is not a valid URL: ${raw}`);
  }
  const port = Number(url.port || (url.protocol === "https:" ? 443 : 80));
  const host = url.hostname;

  const n8nUrl = new URL(url);
  if (host === "localhost") n8nUrl.hostname = "127.0.0.1"; // host.docker.internal passes through

  return {
    host,
    port,
    // bind the loopback interface for any loopback hostname; otherwise bind all interfaces
    bindHost: host === "localhost" || host === "127.0.0.1" ? "127.0.0.1" : "0.0.0.0",
    baseUrl: url.origin,
    healthUrl: new URL("/health", url).toString(),
    n8nUrl: n8nUrl.origin,
  };
}

/** n8n's own port — it reads N8N_PORT itself, so honour the same variable. */
export function n8nPort(env = {}) {
  return Number(env.N8N_PORT || 5678);
}

/** The §6.5 chat front end's port (frontend/app.py). */
export function frontendPort(env = {}) {
  return Number(env.FRONTEND_PORT || 8001);
}

/** True if something is already listening on host:port. */
export function portInUse(port, host = "127.0.0.1") {
  return new Promise((resolve) => {
    const socket = net.createConnection({ port, host });
    const settle = (result) => {
      socket.destroy();
      resolve(result);
    };
    socket.setTimeout(1000);
    socket.once("connect", () => settle(true));
    socket.once("timeout", () => settle(false));
    socket.once("error", () => settle(false));
  });
}

/** Report a secret's presence without ever printing any part of it. */
export function mask(value) {
  if (!value) return "(unset)";
  if (value.startsWith("your-")) return "(placeholder — not filled in)";
  return `set (${value.length} chars)`;
}
