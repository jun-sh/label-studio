(function () {
  "use strict";

  var g = typeof globalThis !== "undefined" ? globalThis : window;

  /** Lock light theme before React reads localStorage on collection embed URLs. */
  (function lockCollectionThemeEarly() {
    try {
      var params = new URLSearchParams(g.location.search);
      if (params.get("datalab_collection") !== "1") return;
      localStorage.setItem("theme", "light");
    } catch (e) {
      /* ignore */
    }
  })();
  var SUBTITLE_EN = "Preview robot datasets in browser";
  var chromeApplied = false;

  var REPLACEMENTS = [
    [/Access more advanced capabilities in EmbodiFlow/gi, ""],
    [/EmbodiFlow/gi, ""],
    [/IO-AI\.?TECH/gi, ""],
    [/io-ai\.tech/gi, ""],
    [/LeRobot Studio/gi, "Data Viewer"],
    [/LeRobot Visualizer/gi, "Data Viewer"],
    [/具身智能数据集可视化平台/g, "Data Viewer"],
    [
      /Visualize LeRobot datasets locally, right in your browser/gi,
      SUBTITLE_EN,
    ],
    [/Preview and play robot datasets in your browser/gi, SUBTITLE_EN],
    [/本地浏览器一键预览与播放机器人数据集/g, SUBTITLE_EN],
    [/预览与播放机器人数据集/g, SUBTITLE_EN],
    [/Click a card to 打开数据set/gi, "Click a card to open dataset"],
    [/打开数据set/gi, "open dataset"],
    [/common\.companyName/g, "Data Lab"],
    [/艾欧智能/g, "Data Lab"],
    [/LeRobot v3 Sample/gi, "v3 样例数据集"],
    [/无需上传，直接在浏览器中查看 LeRobot 数据集/g, "无需上传，直接在浏览器中查看数据集"],
    [
      /https:\/\/huggingface\.co\/datasets\/lerobot\/lerobot\/resolve\/main\/lerobotv3\.zip/gi,
      "/lerobot/bundled/sensexperience_ego.zip",
    ],
    [/©\s*\d{4}\s*\.\s*All rights reserved\./gi, ""],
    [/All rights reserved\./gi, ""],
    [/返回首页/g, "返回采集站列表"],
    [/Go to Home/gi, "Back to station list"],
    [/ホームへ/g, "Back to station list"],
  ];

  function isDataLabEmbed() {
    try {
      var params = new URLSearchParams(g.location.search);
      if (params.get("datalab_embed") === "1") return true;
    } catch (e) {
      /* ignore */
    }
    try {
      if (g.parent !== g) {
        var parentPath = g.parent.location.pathname || "";
        if (parentPath.indexOf("/data") >= 0) return true;
        if (parentPath.indexOf("/collection") >= 0) {
          var parentQs = new URLSearchParams(g.parent.location.search || "");
          if (parentQs.get("station")) return true;
        }
      }
    } catch (e2) {
      /* cross-origin guard */
    }
    return false;
  }

  function isDataVizEmbed() {
    return isDataLabEmbed() && !isCollectionStationEmbed();
  }

  function initEmbedFlag() {
    if (isDataLabEmbed()) {
      document.documentElement.setAttribute("data-datalab-embed", "1");
    } else {
      document.documentElement.removeAttribute("data-datalab-embed");
    }
    if (isCollectionStationEmbed()) {
      document.documentElement.setAttribute("data-datalab-collection-embed", "1");
      document.documentElement.removeAttribute("data-datalab-data-viz-embed");
    } else if (isDataVizEmbed()) {
      document.documentElement.setAttribute("data-datalab-data-viz-embed", "1");
      document.documentElement.removeAttribute("data-datalab-collection-embed");
    } else {
      document.documentElement.removeAttribute("data-datalab-collection-embed");
      document.documentElement.removeAttribute("data-datalab-data-viz-embed");
    }
  }

  function goHomeTooltipLabel() {
    return pageLang().indexOf("zh") === 0 ? "返回采集站列表" : "Back to station list";
  }

  function pageLang() {
    var q = "";
    try {
      q = new URLSearchParams(g.location.search).get("lang") || "";
    } catch (e) {
      q = "";
    }
    var html = (document.documentElement && document.documentElement.lang) || "";
    return (q || html || "en").toLowerCase();
  }

  function samplesSectionTitle(text) {
    if (text === "样例数据" || text === "样例数据集") {
      return pageLang().indexOf("zh") === 0 ? "当前数据集" : "Current Datasets";
    }
    return text;
  }

  function replaceText(node) {
    if (!node || node.nodeType !== Node.TEXT_NODE) return;
    var t = node.textContent;
    var next = samplesSectionTitle(t);
    REPLACEMENTS.forEach(function (pair) {
      next = next.replace(pair[0], pair[1]);
    });
    if (next !== t) node.textContent = next;
  }

  function isNavbarTitleCluster(el) {
    if (!el || !el.classList) return false;
    var cls = el.className || "";
    return cls.indexOf("left-1/2") >= 0 && cls.indexOf("top-1/2") >= 0;
  }

  function hideBrandingChrome() {
    if (!document.body) return;

    document
      .querySelectorAll(
        'nav img[alt*="Logo"], nav img[src*="logo.svg"], header img[alt*="Logo"], header img[src*="logo.svg"]',
      )
      .forEach(function (img) {
        if (isNavbarTitleCluster(img.parentElement)) {
          img.style.display = "none";
          img.setAttribute("data-datalab-chrome", "navbar-logo");
          return;
        }
        var box = img.closest("a") || img.parentElement;
        if (box && box.tagName === "A") box.style.display = "none";
        else img.style.display = "none";
      });

    document.querySelectorAll('a[href*="feishu"], a[href*="embodiflow"], a[href*="io-ai.tech"]').forEach(function (a) {
      a.style.display = "none";
    });
  }

  function isCollectionStationEmbed() {
    try {
      var params = new URLSearchParams(g.location.search);
      if (params.get("datalab_collection") === "1") return true;
      var streamUrl = params.get("url") || "";
      if (streamUrl.indexOf("/api/stream/") >= 0) return true;
    } catch (e) {
      /* ignore */
    }
    if (!isDataLabEmbed()) return false;
    try {
      if (g.parent === g) return false;
      var parentPath = g.parent.location.pathname || "";
      if (parentPath.indexOf("/collection") < 0) return false;
      return Boolean(new URLSearchParams(g.parent.location.search || "").get("station"));
    } catch (e2) {
      return false;
    }
  }

  function collectionListUrl() {
    try {
      return g.parent.location.origin + "/collection";
    } catch (e) {
      return g.location.origin + "/collection";
    }
  }

  function goParentCollectionList() {
    var target = collectionListUrl();
    try {
      g.parent.postMessage({ type: "datalab:collection:go-list" }, g.parent.location.origin);
    } catch (e) {
      /* ignore */
    }
    try {
      g.parent.location.replace(target);
      return;
    } catch (e2) {
      /* ignore */
    }
    try {
      g.top.location.replace(target);
    } catch (e3) {
      /* ignore */
    }
  }

  function isLikelyGoHomeButton(btn) {
    if (!btn || btn.tagName !== "BUTTON") return false;
    if (btn.getAttribute("data-datalab-go-home") === "1") return true;
    if (!btn.querySelector("svg")) return false;
    var container = btn.closest('[class*="mr-auto"]');
    if (!container) return false;
    return container.querySelector("button") === btn;
  }

  function patchCollectionGoHome() {
    if (!isCollectionStationEmbed()) return;
    var label = goHomeTooltipLabel();

    document.querySelectorAll("button").forEach(function (btn) {
      if (!isLikelyGoHomeButton(btn)) return;
      btn.setAttribute("data-datalab-go-home", "1");
      btn.removeAttribute("title");
      btn.setAttribute("aria-label", label);
      if (btn.dataset.datalabGoHomeBound === "1") return;
      btn.dataset.datalabGoHomeBound = "1";
      btn.addEventListener(
        "click",
        function (e) {
          if (!isCollectionStationEmbed()) return;
          e.preventDefault();
          e.stopImmediatePropagation();
          goParentCollectionList();
        },
        true,
      );
    });
  }

  function bindCollectionGoHomeGuard() {
    if (g.__DATALAB_GO_HOME_GUARD__) return;
    g.__DATALAB_GO_HOME_GUARD__ = true;
    document.addEventListener(
      "click",
      function (e) {
        if (!isCollectionStationEmbed()) return;
        var btn = e.target && e.target.closest ? e.target.closest("button") : null;
        if (!btn || !isLikelyGoHomeButton(btn)) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        goParentCollectionList();
      },
      true,
    );
  }

  function fixCompanyHomeLink() {
    var home = g.location.origin + "/";
    document.querySelectorAll('nav a[href], aside a[href]').forEach(function (a) {
      var href = a.getAttribute("href") || "";
      if (href === "https://" || href === "https://#") {
        a.setAttribute("href", home);
        a.setAttribute("target", "_self");
        a.removeAttribute("rel");
      }
    });
    document.querySelectorAll("nav span.truncate, aside span.truncate").forEach(function (el) {
      var text = (el.textContent || "").trim();
      if (text === "common.companyName") {
        el.textContent = "Data Lab";
      }
    });
  }

  function hideCopyrightNotice() {
    if (!document.body) return;

    document.querySelectorAll('[data-datalab-chrome="copyright"]').forEach(function (el) {
      el.style.display = "none";
    });

    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    var n;
    while ((n = walker.nextNode())) {
      if (n.parentElement && (n.parentElement.tagName === "SCRIPT" || n.parentElement.tagName === "STYLE")) {
        continue;
      }
      var t = n.textContent || "";
      if (!/All rights reserved|版权所有|rights reserved/i.test(t)) continue;
      var el = n.parentElement;
      if (!el || el === document.body) continue;
      el.style.display = "none";
      el.setAttribute("data-datalab-chrome", "copyright");
    }
  }

  function hideJapaneseLanguageOption() {
    document.querySelectorAll('[role="menuitem"], [role="menuitemradio"], [role="option"]').forEach(function (item) {
      var label = (item.textContent || "").trim();
      if (label !== "日本語") return;
      item.style.display = "none";
      item.setAttribute("data-datalab-chrome", "language-option-ja");
    });
  }

  function hideBrowseLeRobotItem() {
    document.querySelectorAll('[role="menuitem"], [role="menuitemradio"]').forEach(function (item) {
      var text = (item.textContent || "").replace(/\s+/g, "");
      if (!/^(BrowseLeRobot|浏览LeRobot|浏览Lerobot|LeRobotを開く)$/i.test(text)) return;
      item.style.display = "none";
      item.setAttribute("data-datalab-chrome", "browse-lerobot");
    });
  }

  function isThemeMenuItemLabel(text) {
    return /^(自动|跟随系统|浅色|深色|Auto|System|Light|Dark)$/i.test(text);
  }

  function isThemeTriggerButton(btn) {
    if (!btn || btn.tagName !== "BUTTON") return false;
    var aria = btn.getAttribute("aria-label") || "";
    var sr = btn.querySelector(".sr-only");
    var srText = sr ? sr.textContent || "" : "";
    return /theme|主题/i.test(aria + " " + srText);
  }

  function hideThemeDropdownRoot(btn) {
    var span = btn.closest("span.inline-flex");
    var root = span && span.parentElement && span.parentElement.parentElement;
    if (root && root !== document.body) {
      root.style.display = "none";
      root.setAttribute("data-datalab-chrome", "theme-toggle");
      return;
    }
    btn.style.display = "none";
    btn.setAttribute("data-datalab-chrome", "theme-toggle");
  }

  /** Collection station embed: hide theme picker and keep resolved theme light. */
  function forceCollectionLightTheme() {
    if (!isCollectionStationEmbed()) return;
    try {
      localStorage.setItem("theme", "light");
    } catch (e) {
      /* ignore */
    }
    var appRoot = document.getElementById("lerobot-root");
    if (appRoot) appRoot.classList.remove("dark");
    document.querySelectorAll(".dockview-react").forEach(function (dv) {
      dv.classList.remove("dockview-theme-dark");
      dv.classList.add("dockview-theme-light");
    });
  }

  function hideThemeStyleControl() {
    if (!isCollectionStationEmbed()) return;
    forceCollectionLightTheme();

    document.querySelectorAll("button").forEach(function (btn) {
      if (btn.closest("[data-datalab-collection-mode-root]")) return;
      if (isThemeTriggerButton(btn)) {
        hideThemeDropdownRoot(btn);
        return;
      }
      var text = (btn.textContent || "").replace(/\s+/g, " ").trim();
      if (!isThemeMenuItemLabel(text)) return;
      btn.style.display = "none";
      btn.setAttribute("data-datalab-chrome", "theme-style");
    });

    document.querySelectorAll('[role="menuitem"], [role="menuitemradio"]').forEach(function (item) {
      var text = (item.textContent || "").replace(/\s+/g, " ").trim();
      if (!isThemeMenuItemLabel(text)) return;
      item.style.display = "none";
      item.setAttribute("data-datalab-chrome", "theme-style");
    });
  }

  /** Embed mode: hide language toggle on collection embed only (/data keeps it). */
  function hideLanguageSwitcher() {
    if (!isDataLabEmbed() || isDataVizEmbed()) return;

    document.querySelectorAll("button[aria-label]").forEach(function (btn) {
      var aria = btn.getAttribute("aria-label") || "";
      var sr = btn.querySelector(".sr-only");
      var srText = sr ? sr.textContent || "" : "";
      if (!/language|语言|言語/i.test(aria + " " + srText)) return;
      btn.style.display = "none";
      btn.setAttribute("data-datalab-chrome", "language-switch");
    });
  }

  var DATA_VIZ_DATASET_MSG = "datalab:data-viz:dataset";
  var DATA_VIZ_NAVIGATE_MSG = "datalab:data-viz:navigate";
  var lastNotifiedDatasetId;

  function parseDatasetFromLocation() {
    try {
      var params = new URLSearchParams(g.location.search);
      var raw = params.get("url") || "";
      if (!raw) return null;
      var sample = raw.match(/^sample:\/\/([^/?#]+)/i);
      if (sample) return decodeURIComponent(sample[1]);
    } catch (e) {
      /* ignore */
    }
    return null;
  }

  function notifyParentDatasetChange(replace) {
    if (!isDataVizEmbed()) return;
    var datasetId = parseDatasetFromLocation();
    if (datasetId === lastNotifiedDatasetId) return;
    lastNotifiedDatasetId = datasetId;
    try {
      g.parent.postMessage(
        {
          type: DATA_VIZ_DATASET_MSG,
          datasetId: datasetId,
          replace: !!replace,
        },
        g.parent.location.origin,
      );
    } catch (e) {
      /* ignore */
    }
  }

  function buildDataVizIframeSearch(datasetId) {
    var params = new URLSearchParams(g.location.search);
    params.set("datalab_embed", "1");
    if (!params.get("lang")) {
      params.set("lang", pageLang().indexOf("zh") === 0 ? "zh" : "en");
    }
    if (datasetId) {
      params.set("url", "sample://" + datasetId);
    } else {
      params.delete("url");
    }
    return params;
  }

  function navigateDataVizDataset(datasetId) {
    var params = buildDataVizIframeSearch(datasetId);
    var next = g.location.pathname + "?" + params.toString();
    if (next === g.location.pathname + g.location.search) return;
    g.history.replaceState({}, "", next);
    try {
      g.dispatchEvent(new PopStateEvent("popstate"));
    } catch (e2) {
      /* ignore */
    }
    lastNotifiedDatasetId = datasetId || null;
  }

  function installDataVizUrlSync() {
    if (!isDataVizEmbed() || g.__DATALAB_DATA_VIZ_URL_SYNC__) return;
    g.__DATALAB_DATA_VIZ_URL_SYNC__ = true;

    var pushState = g.history.pushState;
    var replaceState = g.history.replaceState;
    g.history.pushState = function () {
      var result = pushState.apply(this, arguments);
      notifyParentDatasetChange(false);
      return result;
    };
    g.history.replaceState = function () {
      var result = replaceState.apply(this, arguments);
      notifyParentDatasetChange(true);
      return result;
    };

    g.addEventListener("popstate", function () {
      notifyParentDatasetChange(true);
    });

    g.addEventListener("message", function (event) {
      if (event.source !== g.parent) return;
      if (!event.data || event.data.type !== DATA_VIZ_NAVIGATE_MSG) return;
      navigateDataVizDataset(event.data.datasetId || null);
    });

    notifyParentDatasetChange(true);

    if (!g.__DATALAB_DATA_VIZ_URL_POLL__) {
      g.__DATALAB_DATA_VIZ_URL_POLL__ = setInterval(function () {
        if (!isDataVizEmbed()) return;
        var datasetId = parseDatasetFromLocation();
        if (datasetId === lastNotifiedDatasetId) return;
        notifyParentDatasetChange(true);
      }, 400);
    }
  }

  function applyChrome() {
    initEmbedFlag();
    hideCopyrightNotice();
    hideJapaneseLanguageOption();
    hideBrowseLeRobotItem();
    hideThemeStyleControl();
    hideLanguageSwitcher();
  }

  /** /data embed: keep full toolbar (browse, export, health, home, theme, language). */
  function applyDataVizNavbarChrome() {
    if (!isDataVizEmbed()) return;

    var root = document.getElementById("lerobot-root") || document.getElementById("root");
    if (!root) return;

    var shell = root.querySelector(":scope > div");
    if (shell) {
      shell.style.height = "100%";
      shell.style.minHeight = "0";
      shell.style.maxHeight = "100%";
    }

    root.querySelectorAll("nav").forEach(function (nav) {
      nav.setAttribute("data-datalab-embed-navbar", "1");
      nav.style.flexShrink = "0";
      nav.style.visibility = "visible";
      nav.style.height = "3rem";
      nav.style.minHeight = "3rem";
      nav.style.maxHeight = "3rem";
      nav.style.overflow = "visible";
      nav.style.pointerEvents = "auto";

      nav.querySelectorAll('[data-datalab-chrome="navbar-side"], [data-datalab-chrome="navbar-logo"]').forEach(
        function (node) {
          node.style.display = "";
          node.style.visibility = "";
          node.style.pointerEvents = "";
          node.style.left = "";
          node.style.right = "";
          node.style.width = "";
          node.style.transform = "";
          node.style.justifyContent = "";
          node.removeAttribute("data-datalab-chrome");
        },
      );

      nav.querySelectorAll(".mr-auto, .ml-auto").forEach(function (side) {
        side.style.display = "";
        side.style.visibility = "";
        side.style.pointerEvents = "";
      });

      nav.querySelectorAll('[data-datalab-chrome="language-switch"]').forEach(function (btn) {
        btn.style.display = "";
        btn.removeAttribute("data-datalab-chrome");
      });

      nav.querySelectorAll("div").forEach(function (node) {
        if (!isNavbarTitleCluster(node)) return;
        node.style.display = "flex";
        node.style.visibility = "visible";
        node.style.pointerEvents = "none";
        node.querySelectorAll("span").forEach(function (span) {
          span.style.display = "";
          span.style.visibility = "visible";
        });
      });
    });
  }

  /** Collection embed: collapse top toolbar; data viz keeps compact title bar. */
  function applyEmbedLayout() {
    if (!isDataLabEmbed()) return;

    var docEl = document.documentElement;
    var body = document.body;
    docEl.style.width = "100%";
    docEl.style.height = "100%";
    body.style.width = "100%";
    body.style.height = "100%";
    body.style.margin = "0";
    body.style.overflow = "hidden";

    if (isDataVizEmbed()) {
      applyDataVizNavbarChrome();
      return;
    }

    var root = document.getElementById("lerobot-root") || document.getElementById("root");
    if (!root) return;

    root.querySelectorAll(":scope > header, nav").forEach(function (bar) {
      bar.style.flex = "0 0 0px";
      bar.style.height = "0";
      bar.style.minHeight = "0";
      bar.style.maxHeight = "0";
      bar.style.overflow = "hidden";
      bar.style.visibility = "hidden";
      bar.style.padding = "0";
      bar.style.margin = "0";
      bar.style.border = "0";
      bar.style.pointerEvents = "none";
      bar.style.display = "none";
    });
  }

  function runOnce() {
    if (!document.body) return;
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    var n;
    while ((n = walker.nextNode())) {
      if (n.parentElement && (n.parentElement.tagName === "SCRIPT" || n.parentElement.tagName === "STYLE")) {
        continue;
      }
      replaceText(n);
    }
    hideBrandingChrome();
    fixCompanyHomeLink();
    patchCollectionGoHome();
    applyChrome();
    applyEmbedLayout();
    chromeApplied = true;
  }

  function scheduleRun() {
    setTimeout(runOnce, 0);
    setTimeout(runOnce, 500);
    setTimeout(runOnce, 1500);
    setTimeout(runOnce, 3000);
    if (!g.__DATALAB_GO_HOME_OBSERVER__ && document.body) {
      g.__DATALAB_GO_HOME_OBSERVER__ = new MutationObserver(function () {
        initEmbedFlag();
        patchCollectionGoHome();
        hideThemeStyleControl();
        applyDataVizNavbarChrome();
      });
      g.__DATALAB_GO_HOME_OBSERVER__.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        characterData: true,
        attributeFilter: ["class"],
      });
    }
  }

  initEmbedFlag();
  bindCollectionGoHomeGuard();
  installDataVizUrlSync();

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleRun);
  } else {
    scheduleRun();
  }

  function dismissBoot() {
    if (typeof g.__APP_LOADER_DONE__ === "function") {
      try {
        g.__APP_LOADER_DONE__();
      } catch (e) {
        /* ignore */
      }
    }
    var boot = document.getElementById("app-boot");
    if (boot) boot.remove();
  }

  setTimeout(dismissBoot, 3000);
  g.addEventListener("load", function () {
    setTimeout(dismissBoot, 5000);
    if (!chromeApplied) runOnce();
  });
})();
