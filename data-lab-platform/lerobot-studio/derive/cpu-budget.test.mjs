import { describe, it, afterEach } from "node:test";
import assert from "node:assert/strict";

import {
  deriveCpuBudgetPerWorker,
  deriveCpuBudgetSnapshot,
  deriveEncodeConcurrency,
  deriveExtractConcurrency,
  deriveWorkerCount,
} from "./cpu-budget.mjs";

describe("cpu budget (Q1-4)", () => {
  const prev = {
    count: process.env.DERIVE_WORKER_COUNT,
    budget: process.env.DERIVE_CPU_BUDGET,
    cores: process.env.DERIVE_CPU_CORES,
    threads: process.env.DERIVE_MUX_THREADS_PER_JOB,
  };

  afterEach(() => {
    for (const [key, envKey] of Object.entries({
      count: "DERIVE_WORKER_COUNT",
      budget: "DERIVE_CPU_BUDGET",
      cores: "DERIVE_CPU_CORES",
      threads: "DERIVE_MUX_THREADS_PER_JOB",
    })) {
      if (prev[key] === undefined) delete process.env[envKey];
      else process.env[envKey] = prev[key];
    }
  });

  it("defaults to single worker full budget", () => {
    delete process.env.DERIVE_WORKER_COUNT;
    process.env.DERIVE_CPU_BUDGET = "0.85";
    assert.equal(deriveWorkerCount(), 1);
    assert.equal(deriveCpuBudgetPerWorker(), 0.85);
  });

  it("splits budget evenly across worker count", () => {
    process.env.DERIVE_WORKER_COUNT = "3";
    process.env.DERIVE_CPU_BUDGET = "0.9";
    assert.equal(deriveCpuBudgetPerWorker(), 0.3);
  });

  it("reduces encode concurrency per worker when scaled", () => {
    process.env.DERIVE_CPU_CORES = "128";
    process.env.DERIVE_CPU_BUDGET = "0.85";
    process.env.DERIVE_MUX_THREADS_PER_JOB = "2";
    process.env.DERIVE_WORKER_COUNT = "1";
    const single = deriveEncodeConcurrency();
    process.env.DERIVE_WORKER_COUNT = "3";
    const scaled = deriveEncodeConcurrency();
    assert.ok(scaled < single);
    assert.equal(scaled, Math.floor((128 * (0.85 / 3)) / 2));
  });

  it("snapshot exposes per-worker budget metadata", () => {
    process.env.DERIVE_WORKER_COUNT = "2";
    process.env.DERIVE_CPU_BUDGET = "0.8";
    const snap = deriveCpuBudgetSnapshot();
    assert.equal(snap.workerCount, 2);
    assert.equal(snap.budgetPerWorker, 0.4);
    assert.ok(snap.extractConcurrency >= 1);
  });

  it("extract concurrency honors explicit env and disable switch", () => {
    process.env.DERIVE_EXTRACT_CONCURRENCY = "6";
    assert.equal(deriveExtractConcurrency(), 6);
    process.env.DERIVE_EXTRACT_ENABLED = "0";
    assert.equal(deriveExtractConcurrency(), 1);
  });
});
