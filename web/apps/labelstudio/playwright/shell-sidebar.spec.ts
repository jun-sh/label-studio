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

async function openShellSidebar(page: import("@playwright/test").Page) {
  await page.evaluate(() => {
    document.querySelector(".main-menu-trigger")?.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });

  for (let attempt = 0; attempt < 25; attempt++) {
    const result = await page
      .evaluate(() => {
        const sidebar = document.querySelector('[data-testid="shell-sidebar-menu"]');
        if (!sidebar) return { ok: false, reason: "no-sidebar" };
        const cs = getComputedStyle(sidebar);
        const links = [...sidebar.querySelectorAll('ul[class*="main-menu"] a')].map((a) =>
          (a.textContent ?? "").trim(),
        );
        const layer = document.getElementById("datalab-viz-layer");
        return {
          ok: true,
          z: Number(cs.zIndex),
          opacity: Number(cs.opacity),
          linkCount: links.length,
          layerPointerEvents: layer ? getComputedStyle(layer).pointerEvents : null,
          layerInContent: Boolean(layer?.parentElement?.closest('[class*="content-wrapper__content"]')),
        };
      })
      .catch(() => ({ ok: false, reason: "navigation" }));

    if (
      result.ok &&
      result.opacity >= 0.99 &&
      result.linkCount >= 3 &&
      (result.layerPointerEvents === null || result.layerPointerEvents === "none")
    ) {
      return result;
    }
    await page.waitForTimeout(200);
  }
  throw new Error("Shell sidebar did not open with visible menu items");
}

test.describe("shell sidebar on /data", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("floating menu opens above data viz embed", async ({ page }) => {
    await page.goto("/data", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await page.waitForTimeout(3000);

    const opened = await openShellSidebar(page);

    // OSS self-hosted shell sidebar uses z-index 100; must stay above #datalab-viz-layer (50).
    expect(opened.z).toBeGreaterThanOrEqual(100);
    expect(opened.linkCount).toBeGreaterThanOrEqual(3);

    const layer = page.locator("#datalab-viz-layer");
    if (await layer.count()) {
      const layerInfo = await layer.evaluate((el) => {
        const parent = el.parentElement;
        return {
          pointerEvents: getComputedStyle(el).pointerEvents,
          parentClass: parent?.className ?? "",
        };
      });
      expect(layerInfo.pointerEvents).toBe("none");
      expect(layerInfo.parentClass).toMatch(/content-wrapper__content/);
    }
  });
});
