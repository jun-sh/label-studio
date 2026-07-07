/**
 * Browser offline segment import — in-memory tasks + tar.zst ingest (reuses stream-ingest kernel).
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import {
  beginStationImport,
  endStationImport,
  isStationSegmentCommitted,
  processTarZstFromFile,
  refreshStreamEpisodesCatalog,
  stationRoot,
  verifyStationUploadToken,
} from "./stream-ingest.mjs";
import {
  acceptRawTarZstUpload,
  enqueueDeriveJob,
  isDeriveAsyncEnabled,
  maybeEnqueueDeriveAfterUpload,
} from "./derive-async.mjs";

const TASK_TTL_MS = 10 * 60 * 1000;

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function taskStorePath(stationId, taskId) {
  return path.join(stationRoot(stationId), ".upload", "tasks", `${taskId}.json`);
}

function readTaskFromDisk(stationId, taskId) {
  try {
    const raw = fs.readFileSync(taskStorePath(stationId, taskId), "utf8");
    const parsed = JSON.parse(raw);
    return parsed && parsed.taskId === taskId ? parsed : null;
  } catch {
    return null;
  }
}

function writeTaskToDisk(task) {
  if (!task?.taskId || !task?.stationId) return;
  const filePath = taskStorePath(task.stationId, task.taskId);
  ensureDir(path.dirname(filePath));
  const payload = {
    taskId: task.taskId,
    stationId: task.stationId,
    fileName: task.fileName,
    status: task.status,
    segmentId: task.segmentId,
    sessionId: task.sessionId,
    framesCommitted: task.framesCommitted,
    duplicate: task.duplicate,
    deriveStatus: task.deriveStatus || null,
    errorMsg: task.errorMsg,
    updatedAt: task.updatedAt,
  };
  fs.writeFileSync(filePath, JSON.stringify(payload));
}

function removeTaskFromDisk(stationId, taskId) {
  try {
    fs.rmSync(taskStorePath(stationId, taskId), { force: true });
  } catch {
    /* ignore */
  }
}

function parseMultipartFile(buffer, boundary, fieldName) {
  const delim = Buffer.from(`--${boundary}`);
  let start = buffer.indexOf(delim);
  while (start !== -1) {
    let partStart = start + delim.length;
    if (buffer[partStart] === 45 && buffer[partStart + 1] === 45) break;
    if (buffer[partStart] === 13 && buffer[partStart + 1] === 10) partStart += 2;
    const next = buffer.indexOf(delim, partStart);
    const partEnd = next === -1 ? buffer.length : next - 2;
    if (partEnd > partStart) {
      const part = buffer.subarray(partStart, partEnd);
      const headerEnd = part.indexOf("\r\n\r\n");
      if (headerEnd !== -1) {
        const headerText = part.subarray(0, headerEnd).toString("utf8");
        const nameMatch = /name="([^"]+)"/i.exec(headerText);
        if (nameMatch && nameMatch[1] === fieldName) {
          const filenameMatch = /filename="([^"]*)"/i.exec(headerText);
          const body = part.subarray(headerEnd + 4);
          return {
            data: body,
            fileName: filenameMatch?.[1] || "segment.tar.zst",
          };
        }
      }
    }
    start = next;
  }
  return null;
}

function readUploadToFile(req, destPath) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        const buffer = Buffer.concat(chunks);
        const contentType = req.headers["content-type"] || "";
        if (contentType.includes("multipart/form-data")) {
          const match = /boundary=(?:"([^"]+)"|([^;\s]+))/i.exec(contentType);
          const boundary = match?.[1] || match?.[2];
          if (!boundary) {
            reject(new Error("multipart boundary missing"));
            return;
          }
          const parsed = parseMultipartFile(buffer, boundary, "file");
          if (!parsed?.data?.length) {
            reject(new Error('multipart field "file" missing'));
            return;
          }
          ensureDir(path.dirname(destPath));
          fs.writeFileSync(destPath, parsed.data);
          resolve({ fileName: parsed.fileName });
          return;
        }
        if (!buffer.length) {
          reject(new Error("empty upload body"));
          return;
        }
        ensureDir(path.dirname(destPath));
        fs.writeFileSync(destPath, buffer);
        const rawName = req.headers["x-file-name"];
        resolve({ fileName: typeof rawName === "string" && rawName ? rawName : "import.tar.zst" });
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

class ImportTaskManager {
  constructor() {
    /** @type {Map<string, object>} */
    this.tasks = new Map();
    /** @type {Map<string, NodeJS.Timeout>} */
    this.cleanupTimers = new Map();
  }

  create(stationId, fileName) {
    const taskId = crypto.randomUUID();
    const task = {
      taskId,
      stationId,
      fileName: fileName || "segment.tar.zst",
      status: "uploading",
      segmentId: null,
      sessionId: null,
      framesCommitted: 0,
      duplicate: false,
      errorMsg: null,
      updatedAt: new Date().toISOString(),
    };
    this.tasks.set(taskId, task);
    writeTaskToDisk(task);
    this.scheduleCleanup(taskId);
    return task;
  }

  get(taskId) {
    return this.tasks.get(taskId) || null;
  }

  update(taskId, patch) {
    const task = this.tasks.get(taskId);
    if (!task) return null;
    Object.assign(task, patch, { updatedAt: new Date().toISOString() });
    writeTaskToDisk(task);
    if (task.status === "done" || task.status === "failed" || task.status === "skipped") {
      this.scheduleCleanup(taskId);
    }
    return task;
  }

  scheduleCleanup(taskId) {
    const prev = this.cleanupTimers.get(taskId);
    if (prev) clearTimeout(prev);
    const timer = setTimeout(() => {
      const task = this.tasks.get(taskId);
      if (task) removeTaskFromDisk(task.stationId, taskId);
      this.tasks.delete(taskId);
      this.cleanupTimers.delete(taskId);
    }, TASK_TTL_MS);
    this.cleanupTimers.set(taskId, timer);
  }

  toPublic(task) {
    if (!task) return null;
    return {
      taskId: task.taskId,
      stationId: task.stationId,
      fileName: task.fileName,
      status: task.status,
      segmentId: task.segmentId,
      sessionId: task.sessionId,
      framesCommitted: task.framesCommitted,
      duplicate: task.duplicate,
      deriveStatus: task.deriveStatus || null,
      errorMsg: task.errorMsg,
      updatedAt: task.updatedAt,
    };
  }
}

export const importTaskManager = new ImportTaskManager();

/** One tar.zst at a time per station — parallel imports block the Node event loop and cause 504s. */
const importRunQueues = new Map();

function getImportRunQueue(stationId) {
  if (!importRunQueues.has(stationId)) {
    importRunQueues.set(stationId, { running: false, rawRunning: false, pending: [] });
  }
  return importRunQueues.get(stationId);
}

function enqueueImportTask(taskId, stationId, archivePath, fileName) {
  const q = getImportRunQueue(stationId);
  if (isDeriveAsyncEnabled(stationId)) {
    q.pending.push({ taskId, stationId, archivePath, fileName, rawAsync: true });
    drainRawImportQueue(stationId);
    return;
  }
  q.pending.push({ taskId, stationId, archivePath });
  drainImportQueue(stationId);
}

async function drainRawImportQueue(stationId) {
  const q = getImportRunQueue(stationId);
  if (q.rawRunning) return;
  q.rawRunning = true;
  while (q.pending.length) {
    const job = q.pending.shift();
    if (!job?.rawAsync) continue;
    await runRawImportTask(job.taskId, job.stationId, job.archivePath, job.fileName);
  }
  q.rawRunning = false;
}

async function runRawImportTask(taskId, stationId, archivePath, fileName) {
  try {
    importTaskManager.update(taskId, { status: "validating" });
    const ack = await acceptRawTarZstUpload(stationId, {
      archivePath,
      fileName,
      source: "browser",
    });
    if (ack.job) maybeEnqueueDeriveAfterUpload(stationId, ack);
    if (ack.duplicate) {
      importTaskManager.update(taskId, {
        status: "skipped",
        sessionId: ack.sessionId,
        segmentId: ack.segmentId,
        framesCommitted: 0,
        duplicate: true,
      });
    } else {
      importTaskManager.update(taskId, {
        status: "done",
        sessionId: ack.sessionId,
        segmentId: ack.segmentId,
        framesCommitted: 0,
        duplicate: false,
        deriveStatus: "verified",
      });
    }
  } catch (err) {
    importTaskManager.update(taskId, {
      status: "failed",
      errorMsg: String(err?.message || err).slice(0, 500),
    });
    try {
      if (fs.existsSync(archivePath)) fs.rmSync(archivePath, { force: true });
    } catch {
      /* ignore */
    }
  }
}

async function drainImportQueue(stationId) {
  const q = getImportRunQueue(stationId);
  if (q.running || !q.pending.length) return;
  q.running = true;
  const job = q.pending.shift();
  try {
    await runImportTask(job.taskId, job.stationId, job.archivePath);
  } finally {
    q.running = false;
    if (q.pending.length) drainImportQueue(stationId);
  }
}

async function runImportTask(taskId, stationId, archivePath) {
  const onStatus = (status) => {
    const mapped =
      status === "validating" || status === "extracting" || status === "committing"
        ? "processing"
        : status;
    importTaskManager.update(taskId, { status: mapped });
  };
  beginStationImport(stationId);
  try {
    importTaskManager.update(taskId, { status: "processing" });
    const { body, out } = await processTarZstFromFile(archivePath, stationId, { onStatus });
    if (out.duplicate) {
      importTaskManager.update(taskId, {
        status: "skipped",
        sessionId: body.sessionId,
        segmentId: body.segmentId,
        framesCommitted: 0,
        duplicate: true,
      });
    } else {
      if (!isStationSegmentCommitted(stationId, body.sessionId, body.segmentId)) {
        throw new Error(`segment commit incomplete: ${body.segmentId}`);
      }
      importTaskManager.update(taskId, {
        status: "done",
        sessionId: body.sessionId,
        segmentId: body.segmentId,
        framesCommitted: Number(out.framesCommitted ?? 0),
        duplicate: false,
      });
    }
    refreshStreamEpisodesCatalog(stationId);
  } catch (err) {
    importTaskManager.update(taskId, {
      status: "failed",
      errorMsg: String(err?.message || err).slice(0, 500),
    });
  } finally {
    await endStationImport(stationId);
    try {
      fs.rmSync(archivePath, { force: true });
    } catch {
      /* ignore */
    }
  }
}

export async function handleImportUpload(stationId, req) {
  const auth = verifyStationUploadToken(stationId, req);
  if (!auth.ok) {
    const err = new Error("unauthorized");
    err.statusCode = 401;
    err.reason = auth.reason;
    throw err;
  }

  const root = stationRoot(stationId);
  const incomingDir = path.join(root, ".upload", "incoming");
  const scratchId = crypto.randomUUID();
  const archivePath = path.join(incomingDir, `browser_import_${scratchId}.tar.zst`);

  const task = importTaskManager.create(stationId, "segment.tar.zst");
  try {
    const { fileName } = await readUploadToFile(req, archivePath);
    if (!String(fileName).toLowerCase().endsWith(".tar.zst")) {
      throw new Error("only .tar.zst segment archives are supported");
    }
    importTaskManager.update(task.taskId, { fileName, status: "validating" });
    task.fileName = fileName;
  } catch (err) {
    importTaskManager.update(task.taskId, {
      status: "failed",
      errorMsg: String(err?.message || err).slice(0, 500),
    });
    try {
      fs.rmSync(archivePath, { force: true });
    } catch {
      /* ignore */
    }
    throw err;
  }

  setImmediate(() => {
    enqueueImportTask(task.taskId, stationId, archivePath, task.fileName);
  });

  return { taskId: task.taskId, queued: true };
}

export function handleImportTaskGet(stationId, taskId) {
  let task = importTaskManager.get(taskId);
  if (!task) {
    const disk = readTaskFromDisk(stationId, taskId);
    if (disk) return disk;
    const err = new Error("task_not_found");
    err.statusCode = 404;
    throw err;
  }
  if (task.stationId !== stationId) {
    const err = new Error("task_not_found");
    err.statusCode = 404;
    throw err;
  }
  return importTaskManager.toPublic(task);
}
