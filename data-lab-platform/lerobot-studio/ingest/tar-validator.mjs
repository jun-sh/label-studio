/**
 * tar.zst integrity validation (Phase0 Appendix + §3.2.4).
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VALIDATE_SCRIPT = path.join(__dirname, "..", "scripts", "extract-tar-zst.py");

function resolvePython() {
  for (const bin of ["python3", "python"]) {
    const res = spawnSync(bin, ["--version"], { encoding: "utf8" });
    if (res.status === 0) return bin;
  }
  return "python3";
}

export async function validateTarZstArchive(archivePath) {
  if (!fs.existsSync(archivePath)) {
    return {
      ok: false,
      error_code: "TAR_MISSING_ARCHIVE",
      issues: ["archive_missing"],
      manifest: null,
      frame_count: 0,
    };
  }
  const py = resolvePython();
  const res = spawnSync(py, [VALIDATE_SCRIPT, archivePath, "--validate-json"], {
    encoding: "utf8",
  });
  const raw = String(res.stdout || "").trim();
  if (!raw) {
    return {
      ok: false,
      error_code: "TAR_VALIDATION_FAILED",
      issues: [String(res.stderr || "validate_failed").slice(0, 200)],
      manifest: null,
      frame_count: 0,
    };
  }
  try {
    return JSON.parse(raw);
  } catch {
    return {
      ok: false,
      error_code: "TAR_VALIDATION_FAILED",
      issues: ["validate_json_parse_failed"],
      manifest: null,
      frame_count: 0,
    };
  }
}

export function validationErrorFromResult(result) {
  return {
    code: result.error_code || "TAR_VALIDATION_FAILED",
    message: (result.issues || []).join("; ") || "tar validation failed",
    category: "ingest",
  };
}
