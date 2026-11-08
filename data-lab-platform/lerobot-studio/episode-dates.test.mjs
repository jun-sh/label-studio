import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WRITE_TASKS = path.join(__dirname, "scripts", "write-tasks-parquet.py");

function tmpStation() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "episode-dates-"));
}

function writeCanonicalTasksParquet(root, rows) {
  const pending = path.join(root, "live", ".tasks-meta-pending.json");
  fs.mkdirSync(path.dirname(pending), { recursive: true });
  fs.writeFileSync(pending, `${JSON.stringify(rows)}\n`);
  const res = spawnSync("python3", [WRITE_TASKS, root, "--rows", pending], { encoding: "utf8" });
  assert.equal(res.status, 0, res.stderr || res.stdout);
}

describe("getEpisodeUploadDateMap", () => {
  it("reads canonical tasks.parquet (task as index) and groups by journal published date", async () => {
    const root = tmpStation();
    const sessionId = "sess_aabbccddeeff00112233445566778899";
    fs.mkdirSync(path.join(root, "manifest"), { recursive: true });
    fs.mkdirSync(path.join(root, "live"), { recursive: true });
    fs.mkdirSync(path.join(root, "state", "segments", sessionId), { recursive: true });

    fs.writeFileSync(
      path.join(root, "manifest", "manifest.json"),
      JSON.stringify({
        version: 1,
        episodes: [{ episode_index: 0, session_id: sessionId, frames: 100 }],
      }),
    );
    fs.writeFileSync(
      path.join(root, "manifest", "journal.jsonl"),
      [
        JSON.stringify({
          at: "2026-11-04T10:00:00.000Z",
          event: "published",
          session_id: sessionId,
          episode_index: 0,
        }),
      ].join("\n") + "\n",
    );
    writeCanonicalTasksParquet(root, [
      { task_index: 0, task: `EGO-002 · aabbccdd · 11-08 · 100f` },
    ]);
    fs.writeFileSync(
      path.join(root, "live", "session-registry.json"),
      JSON.stringify({ sessions: { [sessionId]: { startedAt: "2026-11-08T00:00:00.000Z" } } }),
    );

    process.env.STREAM_DATA_ROOT = path.dirname(root);
    const stationId = path.basename(root);
    const { getEpisodeUploadDateMap } = await import("./stream-ingest.mjs");
    const payload = getEpisodeUploadDateMap(stationId);
    assert.equal(payload.episodes.length, 1);
    assert.equal(payload.episodes[0].date, "11-04");
    assert.equal(payload.episodes[0].session_id, sessionId);
  });
});
