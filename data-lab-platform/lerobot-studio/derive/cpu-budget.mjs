/**
 * Q1-4: CPU budget split across scaled derive-worker instances.
 */

export function deriveWorkerCount() {
  return Math.max(1, Math.floor(Number(process.env.DERIVE_WORKER_COUNT || 1)));
}

export function deriveCpuBudgetTotal() {
  const raw = Number(process.env.DERIVE_CPU_BUDGET ?? 0.85);
  if (!Number.isFinite(raw) || raw <= 0) return 0.85;
  return Math.min(1, raw);
}

/** Station-wide budget divided evenly per derive-worker replica. */
export function deriveCpuBudgetPerWorker() {
  return deriveCpuBudgetTotal() / deriveWorkerCount();
}

export function deriveEncodeConcurrency(options = {}) {
  const cores = Number(process.env.DERIVE_CPU_CORES || 128);
  const threadsPerJob = Math.max(1, Number(process.env.DERIVE_MUX_THREADS_PER_JOB || 2));
  const budget = options.budget ?? deriveCpuBudgetPerWorker();
  return Math.max(1, Math.floor((cores * budget) / threadsPerJob));
}

/** P1: parallel segment tar extract (IO-bound; default conservative). */
export function deriveExtractConcurrency(options = {}) {
  if (String(process.env.DERIVE_EXTRACT_ENABLED ?? "1").trim() === "0") return 1;
  const explicit = Number(process.env.DERIVE_EXTRACT_CONCURRENCY || 0);
  if (Number.isFinite(explicit) && explicit > 0) {
    return Math.max(1, Math.floor(explicit));
  }
  const encode = deriveEncodeConcurrency(options);
  return Math.max(1, Math.min(8, Math.floor(encode / 8) || 1));
}

export function deriveCpuBudgetSnapshot() {
  const workers = deriveWorkerCount();
  const total = deriveCpuBudgetTotal();
  const perWorker = deriveCpuBudgetPerWorker();
  return {
    workerCount: workers,
    budgetTotal: total,
    budgetPerWorker: perWorker,
    encodeConcurrency: deriveEncodeConcurrency(),
    extractConcurrency: deriveExtractConcurrency(),
    cpuCores: Number(process.env.DERIVE_CPU_CORES || 128),
    threadsPerJob: Math.max(1, Number(process.env.DERIVE_MUX_THREADS_PER_JOB || 2)),
  };
}
