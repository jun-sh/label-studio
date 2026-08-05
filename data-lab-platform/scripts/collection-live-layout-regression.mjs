#!/usr/bin/env node
/**
 * Headless regression: collection live preview layout vs replay.
 * Usage: node data-lab-platform/scripts/collection-live-layout-regression.mjs [baseUrl]
 */
import puppeteer from "puppeteer-core";

const BASE = process.argv[2] || "http://127.0.0.1:8080";
const VIEWER_URL =
  `${BASE}/lerobot/?datalab_embed=1&datalab_collection=1&lang=zh` +
  `&url=${encodeURIComponent("/lerobot/api/stream/ego-lan-214/")}&_ts=${Date.now()}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function layoutSignature(gvs) {
  return gvs
    .map((g) => `${g.title || "?"}@${g.x},${g.y},${g.w},${g.h}`)
    .sort()
    .join("|");
}

async function collectState(page) {
  return page.evaluate(() => {
    function gvInfo(gv) {
      const r = gv.getBoundingClientRect();
      const tab = gv.querySelector(".dv-tab, .dv-default-tab");
      const title = (tab?.textContent || gv.getAttribute("title") || "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 40);
      return {
        title,
        x: Math.round(r.x),
        y: Math.round(r.y),
        w: Math.round(r.width),
        h: Math.round(r.height),
        replay: gv.hasAttribute("data-datalab-replay-chrome"),
        preview: Boolean(gv.querySelector("[data-datalab-preview-panel]")),
      };
    }

    function panelChrome(groupview) {
      const root = groupview.querySelector(".dv-content-container");
      if (!root) return { toolbarVisible: false, bodyVisible: false };
      const toolbar = root.querySelector(
        ".flex.items-center.justify-between, .flex.shrink-0.items-center.border-b",
      );
      const toolbarVisible =
        toolbar && getComputedStyle(toolbar).visibility !== "hidden" && toolbar.offsetHeight > 0;
      const canvas = root.querySelector("canvas");
      const scroll = root.querySelector("[data-radix-scroll-area-viewport]");
      const bodyVisible =
        (canvas && getComputedStyle(canvas).visibility !== "hidden" && canvas.offsetHeight > 0) ||
        (scroll && getComputedStyle(scroll).visibility !== "hidden" && scroll.offsetHeight > 0);
      return { toolbarVisible, bodyVisible };
    }

    const bar = document.querySelector(".h-16.border-t.bg-background");
    const transportLeaves = bar
      ? [...bar.querySelectorAll("*")]
          .filter((el) => !el.children.length)
          .map((el) => (el.textContent || "").trim())
          .filter(Boolean)
      : [];

    const gvs = [...document.querySelectorAll(".dv-groupview")].map(gvInfo);
    const chartGv = [...document.querySelectorAll(".dv-groupview")].find((gv) =>
      (gv.textContent || "").includes("折线图"),
    );
    const rawGv = [...document.querySelectorAll(".dv-groupview")].find((gv) =>
      (gv.textContent || "").includes("原始消息"),
    );

    const overlays = [...document.querySelectorAll("img.datalab-collection-preview-overlay")].map(
      (img) => {
        const wrap = img.closest(".group.relative") || img.parentElement;
        const video = wrap?.querySelector("video");
        const ir = img.getBoundingClientRect();
        const vr = video?.getBoundingClientRect();
        const imgCenter = { cx: ir.x + ir.width / 2, cy: ir.y + ir.height / 2 };
        const videoCenter = vr
          ? { cx: vr.x + vr.width / 2, cy: vr.y + vr.height / 2 }
          : null;
        return {
          cam: img.getAttribute("data-datalab-preview-overlay"),
          w: img.naturalWidth,
          h: img.naturalHeight,
          hasSrc: Boolean(img.src),
          offsetFromVideoCenter: videoCenter
            ? {
                dx: Math.round(imgCenter.cx - videoCenter.cx),
                dy: Math.round(imgCenter.cy - videoCenter.cy),
              }
            : null,
        };
      },
    );

    const rawViewport = rawGv?.querySelector("[data-radix-scroll-area-viewport]");

    const chartGv2 = chartGv;
    const chartCanvas = chartGv2?.querySelector("canvas");
    let chartCanvasHash = null;
    if (chartCanvas && chartCanvas.width > 0 && chartCanvas.height > 0) {
      const ctx = chartCanvas.getContext("2d");
      if (ctx) {
        const d = ctx.getImageData(0, 0, Math.min(32, chartCanvas.width), Math.min(32, chartCanvas.height))
          .data;
        let s = 0;
        for (let i = 0; i < d.length; i += 41) s += d[i];
        chartCanvasHash = s;
      }
    }

    const numericLeaves = rawViewport
      ? [...rawViewport.querySelectorAll("*")]
          .filter((el) => !el.children.length)
          .map((el) => (el.textContent || "").trim())
          .filter((t) => /^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(t))
      : [];
    const featuresNonZero = numericLeaves.filter((t) => t !== "0" && t !== "0.0").length;

    return {
      fixedOverlays: document.querySelectorAll("div.fixed.z-50").length,
      previewMode: document.documentElement.getAttribute("data-datalab-collection-preview"),
      gvs,
      overlays,
      chartChrome: chartGv ? panelChrome(chartGv) : null,
      rawChrome: rawGv ? panelChrome(rawGv) : null,
      rawFrameIndex: rawGv
        ? (rawViewport?.textContent || "").match(/"frame_index":\s*(\d+)/)?.[1]
        : null,
      featuresFlexRows: rawViewport?.querySelectorAll(".flex").length || 0,
      featuresHasTree: Boolean(rawViewport?.querySelector(".font-mono")),
      featuresNonZero,
      jointRows: chartGv
        ? chartGv.querySelectorAll("[data-joint], [role=row]").length
        : 0,
      jointsCleared: Boolean(chartGv?.querySelector("[data-datalab-live-joints-cleared]")),
      jointsNoneLabel: [...document.querySelectorAll("span.truncate, button span")]
        .filter((el) => (el.textContent || "").trim() === "未选择")
        .some((el) => {
          const st = getComputedStyle(el);
          const inChart = chartGv && chartGv.contains(el);
          return (
            inChart &&
            st.visibility !== "hidden" &&
            st.display !== "none" &&
            el.offsetParent !== null
          );
        }),
      chartCanvasHash,
      transportLeaves,
      jointsLabelVisible: [...document.querySelectorAll("span, button")]
        .filter((el) => (el.textContent || "").trim() === "全部 joints")
        .some((el) => {
          const st = getComputedStyle(el);
          return st.visibility !== "hidden" && st.display !== "none" && el.offsetParent !== null;
        }),
      featuresLabelVisible: [...document.querySelectorAll("span, button")]
        .filter((el) => /全部\s*Features/i.test((el.textContent || "").trim()))
        .some((el) => {
          const st = getComputedStyle(el);
          return st.visibility !== "hidden" && st.display !== "none" && el.offsetParent !== null;
        }),
    };
  });
}

async function switchLive(page) {
  await page.evaluate(() => {
    window.postMessage(
      {
        type: "datalab-collection-mode",
        mode: "preview",
        parentChrome: true,
        online: true,
        remotePreview: true,
        captureState: "idle",
        remotePreviewAllowed: true,
      },
      window.location.origin,
    );
  });
}

async function main() {
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_PATH || "/usr/bin/google-chrome",
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu", "--window-size=1920,1080"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080 });
  await page.goto(VIEWER_URL, { waitUntil: "networkidle2", timeout: 120_000 });
  await sleep(8000);
  await page.evaluate(() => {
    for (const b of document.querySelectorAll("button")) {
      if ((b.textContent || "").includes("知道了")) b.click();
    }
  });
  await sleep(500);
  await page.evaluate(() => {
    for (const b of document.querySelectorAll("button")) {
      if ((b.textContent || "").includes("知道了")) b.click();
    }
  });
  await sleep(500);

  await page.evaluate(() => {
    for (const el of document.querySelectorAll("button, span")) {
      if (/^全部\s*Features$/i.test((el.textContent || "").trim())) {
        el.click();
        break;
      }
    }
  });
  await sleep(1500);

  await page.evaluate(() => {
    for (const b of document.querySelectorAll("button")) {
      const svg = b.querySelector("svg");
      if (svg?.getAttribute("class")?.includes("lucide-play")) {
        b.click();
        break;
      }
    }
  });
  await sleep(2000);

  const replay = await collectState(page);
  await switchLive(page);
  await sleep(3000);
  const liveMid = await collectState(page);
  await sleep(3000);
  const live = await collectState(page);

  await page.screenshot({
    path: "/tmp/datalab-live-regression-replay.png",
    fullPage: false,
  });
  await switchLive(page);
  await sleep(1000);
  await page.screenshot({
    path: "/tmp/datalab-live-regression-live.png",
    fullPage: false,
  });

  await browser.close();

  const replaySig = layoutSignature(replay.gvs);
  const liveSig = layoutSignature(live.gvs);
  const cameraCountReplay = replay.gvs.filter((g) => /camera/i.test(g.title)).length;
  const cameraCountLive = live.gvs.filter((g) => /camera/i.test(g.title)).length;

  const checks = [
    {
      id: "layout-match",
      pass: replaySig === liveSig,
      detail: replaySig === liveSig ? "dockview geometry identical" : `replay=${replaySig} live=${liveSig}`,
    },
    {
      id: "no-fixed-overlay-live",
      pass: live.fixedOverlays === 0,
      detail: `fixed.z-50 count=${live.fixedOverlays}`,
    },
    {
      id: "four-cameras-replay",
      pass: cameraCountReplay === 4,
      detail: `count=${cameraCountReplay}`,
    },
    {
      id: "four-cameras-live",
      pass: cameraCountLive === 4,
      detail: `count=${cameraCountLive}`,
    },
    {
      id: "four-jpg-overlays",
      pass: live.overlays.length === 4 && live.overlays.every((o) => o.hasSrc && o.w > 0),
      detail: JSON.stringify(live.overlays),
    },
    {
      id: "preview-mode-flag",
      pass: live.previewMode === "1",
      detail: `previewMode=${live.previewMode}`,
    },
    {
      id: "chart-toolbar-visible",
      pass: live.chartChrome?.toolbarVisible === true,
      detail: JSON.stringify(live.chartChrome),
    },
    {
      id: "chart-grid-visible",
      pass: live.chartChrome?.bodyVisible === true && live.jointRows > 0,
      detail: `jointRows=${live.jointRows} chrome=${JSON.stringify(live.chartChrome)}`,
    },
    {
      id: "raw-toolbar-visible",
      pass: live.rawChrome?.toolbarVisible === true,
      detail: JSON.stringify(live.rawChrome),
    },
    {
      id: "raw-json-visible-frame-zero",
      pass:
        live.rawChrome?.bodyVisible === true &&
        live.rawFrameIndex === "0",
      detail: `frame_index=${live.rawFrameIndex} chrome=${JSON.stringify(live.rawChrome)}`,
    },
    {
      id: "features-zero-values-live",
      pass: live.featuresNonZero === 0,
      detail: `nonZeroNumericLeaves=${live.featuresNonZero}`,
    },
    {
      id: "joints-selection-cleared",
      pass: live.jointsCleared === true,
      detail: `jointsCleared=${live.jointsCleared}`,
    },
    {
      id: "joints-none-label",
      pass: live.jointsNoneLabel === true,
      detail: `jointsNoneLabel=${live.jointsNoneLabel}`,
    },
    {
      id: "transport-zero-labels",
      pass:
        live.transportLeaves.includes("0:00.00") &&
        live.transportLeaves.includes("/ 0:00.00") &&
        live.transportLeaves.includes("0") &&
        live.transportLeaves.includes("/ 0"),
      detail: JSON.stringify(live.transportLeaves),
    },
    {
      id: "features-layout-match",
      pass:
        live.featuresFlexRows > 0 &&
        live.featuresHasTree === true &&
        live.featuresFlexRows >= Math.max(10, replay.featuresFlexRows - 5),
      detail: `replayFlex=${replay.featuresFlexRows} liveFlex=${live.featuresFlexRows} tree=${live.featuresHasTree}`,
    },
    {
      id: "camera-overlays-centered",
      pass:
        live.overlays.length === 4 &&
        live.overlays.every(
          (o) =>
            o.offsetFromVideoCenter &&
            Math.abs(o.offsetFromVideoCenter.dx) <= 2 &&
            Math.abs(o.offsetFromVideoCenter.dy) <= 2,
        ),
      detail: JSON.stringify(live.overlays.map((o) => o.offsetFromVideoCenter)),
    },
    {
      id: "joints-label-in-panel",
      pass: live.jointsNoneLabel === true,
      detail: `visible=${live.jointsNoneLabel}`,
    },
    {
      id: "features-label-in-panel",
      pass: live.featuresLabelVisible === true,
      detail: `visible=${live.featuresLabelVisible}`,
    },
  ];

  let failed = 0;
  console.log("Collection live layout regression\n");
  for (const c of checks) {
    const mark = c.pass ? "PASS" : "FAIL";
    if (!c.pass) failed += 1;
    console.log(`[${mark}] ${c.id}: ${c.detail}`);
  }
  console.log(`\nScreenshots: /tmp/datalab-live-regression-replay.png /tmp/datalab-live-regression-live.png`);
  console.log(failed === 0 ? "\nALL PASS" : `\n${failed} FAILED`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
