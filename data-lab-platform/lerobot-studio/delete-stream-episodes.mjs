/**
 * Run ego-delete-episodes.py for collection UI / API (L2 only, keeps raw).
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname);
const PLATFORM_ROOT = path.resolve(APP_ROOT, "..");

function resolveDeleteScript() {
  const fromEnv = process.env.EGO_DELETE_EPISODES_SCRIPT;
  if (fromEnv) return fromEnv;
  const candidates = [
    path.join(APP_ROOT, "scripts", "ego-delete-episodes.py"),
    path.join(PLATFORM_ROOT, "scripts", "ego-delete-episodes.py"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return candidates[0];
}

const DELETE_SCRIPT = resolveDeleteScript();

function streamRootEnv() {
  const fromEnv = process.env.EGO_STREAM_ROOT || process.env.STREAM_DATA_ROOT;
  if (fromEnv) return fromEnv;
  return path.join(PLATFORM_ROOT, "data-storage", "stream");
}

function normalizeIndices(raw) {
  if (!Array.isArray(raw)) return null;
  const out = [];
  for (const item of raw) {
    const n = Number(item);
    if (!Number.isInteger(n) || n < 0) return null;
    out.push(n);
  }
  return [...new Set(out)].sort((a, b) => a - b);
}

/**
 * @param {string} stationId
 * @param {number[]} indices
 * @param {{ apply?: boolean }} opts
 */
export function deleteStreamEpisodes(stationId, indices, { apply = false } = {}) {
  if (!/^ego-[a-z0-9-]+$/.test(stationId)) {
    return { ok: false, status: 400, error: "invalid_station_id" };
  }
  const normalized = normalizeIndices(indices);
  if (!normalized || normalized.length === 0) {
    return { ok: false, status: 400, error: "invalid_episode_indices" };
  }
  if (!fs.existsSync(DELETE_SCRIPT)) {
    return { ok: false, status: 500, error: "delete_script_missing", path: DELETE_SCRIPT };
  }

  const args = [
    DELETE_SCRIPT,
    stationId,
    "--index",
    normalized.join(","),
    "--json",
  ];
  if (apply) args.push("--apply");

  try {
    const stdout = execFileSync(process.env.PYTHON || "python3", args, {
      encoding: "utf8",
      env: {
        ...process.env,
        EGO_STREAM_ROOT: streamRootEnv(),
      },
      maxBuffer: 8 * 1024 * 1024,
    });
    const line = stdout
      .trim()
      .split("\n")
      .filter(Boolean)
      .pop();
    const payload = line ? JSON.parse(line) : { ok: true, removed: normalized, dry_run: !apply };
    return { ok: true, status: 200, ...payload };
  } catch (err) {
    const stderr = String(err.stderr || err.message || "");
    const code = typeof err.status === "number" ? err.status : 1;
    return {
      ok: false,
      status: code === 2 ? 400 : 500,
      error: "delete_failed",
      detail: stderr.trim() || String(err.message || ""),
      exitCode: code,
    };
  }
}
