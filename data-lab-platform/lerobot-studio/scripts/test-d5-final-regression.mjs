#!/usr/bin/env node
/**
 * D5 final regression orchestrator: D1–D4 + writeback + integration + format compliance.
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PY = process.env.PYTHON || "python3";
const NODE = process.execPath;
const EGO_BACKEND = path.resolve(__dirname, "../../embodied-annotate/backend");
const EGO_HAND = path.resolve(__dirname, "../../../../ego-hand-pipeline");
const results = [];

function sha256File(p) {
  if (!fs.existsSync(p)) return null;
  return crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
}

function runStep(id, title, fn) {
  process.stdout.write(`\n[${id}] ${title}… `);
  try {
    fn();
    results.push({ id, title, pass: true });
    console.log("PASS");
  } catch (err) {
    results.push({ id, title, pass: false, error: String(err?.message || err) });
    console.log("FAIL");
    console.error(`  ${err?.message || err}`);
  }
}

function spawnOk(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { encoding: "utf8", ...opts });
  if (r.status !== 0) {
    throw new Error((r.stderr || r.stdout || `exit ${r.status}`).trim());
  }
  return r.stdout;
}

function main() {
  runStep("D1", "Canonical schema smoke", () => {
    spawnOk(NODE, [path.join(__dirname, "test-d1-canonical-schema.mjs")], {
      cwd: path.dirname(__dirname),
    });
  });

  runStep("D3", "EGO annotation API (9 tests)", () => {
    const venvPy = path.join(EGO_BACKEND, ".venv", "bin", "python");
    const py = fs.existsSync(venvPy) ? venvPy : PY;
    spawnOk(py, ["-m", "pytest", "tests/test_ego_api.py", "-q"], {
      cwd: EGO_BACKEND,
      env: { ...process.env, PYTHONPATH: EGO_BACKEND },
    });
  });

  runStep("D4", "Unitree G1 E2E", () => {
    spawnOk(NODE, [path.join(__dirname, "test-d4-unitree-e2e.mjs")], {
      cwd: path.dirname(__dirname),
    });
  });

  runStep("D2", "Hand writeback + sidecar (6 tests)", () => {
    const venvPy = path.join(EGO_HAND, ".venv", "bin", "python");
    const py = fs.existsSync(venvPy) ? venvPy : PY;
    spawnOk(py, ["-m", "pytest", "tests/test_lerobot_writeback.py", "-q"], {
      cwd: EGO_HAND,
      env: { ...process.env, PYTHONPATH: path.join(EGO_HAND, "src") },
    });
  });

  runStep("D5", "Integration + format + pusht regression", () => {
    const egoVenvPy = path.join(EGO_BACKEND, ".venv", "bin", "python");
    const py = fs.existsSync(egoVenvPy) ? egoVenvPy : PY;
    spawnOk(py, [path.join(__dirname, "test-d5-integration.py")], {
      cwd: path.dirname(__dirname),
      env: {
        ...process.env,
        PYTHONPATH: [EGO_BACKEND, path.join(EGO_HAND, "src")].join(path.delimiter),
      },
    });
  });

  const failed = results.filter((r) => !r.pass);
  console.log("\n═══════════════════════════════════════");
  console.log("D5 REGRESSION SUMMARY");
  console.log("═══════════════════════════════════════");
  for (const r of results) {
    console.log(`${r.pass ? "✓" : "✗"} [${r.id}] ${r.title}`);
  }
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  if (failed.length) {
    console.error("\nD5 FAILED — not sealed");
    process.exit(1);
  }
  console.log("\nD5 SEALED — EgoVerse canonical pipeline ready");
  process.exit(0);
}

main();
