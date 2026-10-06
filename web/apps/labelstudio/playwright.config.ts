import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.DATALAB_E2E_BASE_URL ?? "http://127.0.0.1:8080";

export default defineConfig({
  testDir: "./playwright",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL,
    ...devices["Desktop Chrome"],
    viewport: { width: 1400, height: 900 },
    trace: "on-first-retry",
  },
});
