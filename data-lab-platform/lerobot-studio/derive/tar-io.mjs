/**
 * tar.zst extract helpers for derive (read-only raw archives).
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { ensureDir } from "./io.mjs";
import { validateTarZstArchive } from "../ingest/tar-validator.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const EXTRACT_SCRIPT = path.join(__dirname, "..", "scripts", "extract-tar-zst.py");

function resolvePython() {
  for (const bin of ["python3", "python"]) {
    const res = spawnSync(bin, ["--version"], { encoding: "utf8" });
    if (res.status === 0) return bin;
  }
  return "python3";
}

export async function validateRawArchive(archivePath) {
  return validateTarZstArchive(archivePath);
}

export function extractTarZstSync(archivePath, destDir, { expectedSha256 = null } = {}) {
  ensureDir(destDir);
  const py = resolvePython();
  const args = [EXTRACT_SCRIPT, archivePath, destDir];
  if (expectedSha256) args.push("--expected-sha256", expectedSha256);
  const res = spawnSync(py, args, { encoding: "utf8" });
  if (res.status !== 0) {
    let msg = String(res.stderr || res.stdout || "extract failed").trim();
    try {
      const parsed = JSON.parse(msg.split("\n").filter(Boolean).pop() || "{}");
      if (parsed.error) msg = parsed.error;
    } catch {
      /* keep */
    }
    throw new Error(msg.slice(0, 500));
  }
}

export function makeExtractDir(root, sessionId, segmentId) {
  const dir = path.join(root, ".upload", "extract", `derive_${sessionId}_${segmentId}_${Date.now()}`);
  ensureDir(dir);
  return dir;
}

export function cleanupExtractDir(extractDir) {
  try {
    if (extractDir && fs.existsSync(extractDir)) {
      fs.rmSync(extractDir, { recursive: true, force: true });
    }
  } catch {
    /* ignore */
  }
}
