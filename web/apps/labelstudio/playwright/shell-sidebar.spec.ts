import { expect, test } from "@playwright/test";

const email = process.env.DATALAB_E2E_EMAIL;
const password = process.env.DATALAB_E2E_PASSWORD;

async function login(page: import("@playwright/test").Page) {
  if (!email || !password) {
    test.skip(true, "Set DATALAB_E2E_EMAIL and DATALAB_E2E_PASSWORD");
  }
  await page.goto("/user/login/", { waitUntil: "domcontentloaded" });
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.includes("/user/login"), { timeout: 30_000 });
}

type SidebarProbe = {
  ok: boolean;
  reason?: string;
  z?: number;
  opacity?: number;
  linkCount?: number;
  layerPointerEvents?: string | null;
  sidebarTransform?: string;
};

async function probeSidebar(page: import("@playwright/test").Page): Promise<SidebarProbe> {
  return page.evaluate(() => {
    const sidebar =
      document.querySelector('[data-testid="shell-sidebar-menu"]') ?? document.querySelector(".sidebar.dropdown");
    if (!sidebar) return { ok: false, reason: "no-sidebar" };
    const cs = getComputedStyle(sidebar);
    const links = [...sidebar.querySelectorAll('ul[class*="main-menu"] a, .main-menu a')].map((a) =>
      (a.textContent ?? "").trim(),
    );
    const layer =
      document.getElementById("datalab-viz-layer") ||
      document.getElementById("datalab-collection-layer") ||
      document.getElementById("datalab-station-layer");
    return {
      ok: true,
      z: Number(cs.zIndex),
      opacity: Number(cs.opacity),
      linkCount: links.filter(Boolean).length,
      layerPointerEvents: layer ? getComputedStyle(layer).pointerEvents : null,
      sidebarTransform: cs.transform,
    };
  });
}

async function openShellSidebar(page: import("@playwright/test").Page) {
  await page.locator(".main-menu-trigger").click({ timeout: 10_000 });

  for (let attempt = 0; attempt < 30; attempt++) {
    const result = await probeSidebar(page).catch(() => ({ ok: false, reason: "navigation" } as SidebarProbe));

    if (
      result.ok &&
      (result.opacity ?? 0) >= 0.99 &&
      (result.linkCount ?? 0) >= 3 &&
      (result.layerPointerEvents === null || result.layerPointerEvents === "none")
    ) {
      return result;
    }
    await page.waitForTimeout(200);
  }
  const last = await probeSidebar(page);
  throw new Error(`Shell sidebar did not open: ${JSON.stringify(last)}`);
}

const routes = [
  { path: "/", name: "home" },
  { path: "/data", name: "data", waitMs: 3000 },
  { path: "/collection", name: "collection", waitMs: 2000 },
] as const;

test.describe("shell sidebar across routes", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  for (const route of routes) {
    test(`floating menu on ${route.name} (${route.path})`, async ({ page }) => {
      await page.goto(route.path, { waitUntil: "domcontentloaded", timeout: 60_000 });
      if (route.waitMs) await page.waitForTimeout(route.waitMs);

      const opened = await openShellSidebar(page);

      expect(opened.z ?? 0).toBeGreaterThanOrEqual(100);
      expect(opened.linkCount ?? 0).toBeGreaterThanOrEqual(3);
      expect(opened.sidebarTransform).not.toBe("matrix(1, 0, 0, 1, -240, 0)");
    });
  }
});
