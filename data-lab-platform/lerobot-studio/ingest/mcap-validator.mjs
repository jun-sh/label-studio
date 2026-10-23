/**
 * MCAP archive validation (spawn validate-mcap-archive.py).
 */

import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VALIDATE_SCRIPT = path.join(__dirname, "..", "scripts", "validate-mcap-archive.py");

function resolvePython() {
  for (const bin of ["python3", "python"]) {
    const res = spawnSync(bin, ["--version"], { encoding: "utf8" });
    if (res.status === 0) return bin;
  }
  return "python3";
}

export function validationErrorFromMcapResult(result) {
  const issues = Array.isArray(result?.issues) ? result.issues : ["mcap_invalid"];
  return {
    code: issues[0] || "MCAP_VALIDATION_FAILED",
    message: `MCAP validation failed: ${issues.join("; ")}`,
    category: "ingest",
  };
}

let cachedPython = null;

function runValidator(archivePath) {
  cachedPython = cachedPython || resolvePython();
  return new Promise((resolve) => {
    const proc = spawn(cachedPython, [VALIDATE_SCRIPT, archivePath]);
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => (stdout += d));
    proc.stderr.on("data", (d) => (stderr += d));
    proc.on("error", (err) => resolve({ status: -1, stdout, stderr: String(err?.message || err) }));
    proc.on("close", (status) => resolve({ status, stdout, stderr }));
  });
}

export async function validateMcapArchive(archivePath) {
  const res = await runValidator(archivePath);
  const raw = String(res.stdout || res.stderr || "").trim();
  let parsed;
  try {
    const line = raw.split("\n").filter(Boolean).pop() || "{}";
    parsed = JSON.parse(line);
  } catch {
    parsed = { ok: false, issues: ["mcap_validate_parse_error"], error: raw.slice(0, 300) };
  }
  if (res.status !== 0 && parsed.ok !== false) {
    parsed.ok = false;
    parsed.issues = parsed.issues || [parsed.error || "mcap_validate_exit_nonzero"];
  }
  return parsed;
}

export function mcapValidatorAvailable() {
  return fs.existsSync(VALIDATE_SCRIPT);
}
