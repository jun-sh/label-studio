/**
 * Collection embed: parent toolbar drives LeRobot theme via native React setTheme (UI click).
 * Direct DOM/localStorage alone desyncs resolvedTheme and leaves uPlot grid/series on stale colors.
 */
(function () {
  "use strict";

  var g = typeof globalThis !== "undefined" ? globalThis : window;
  if (g.__DATALAB_COLLECTION_THEME__) return;

  function isCollectionStationEmbed() {
    try {
      var params = new URLSearchParams(g.location.search || "");
      if (params.get("datalab_collection") === "1") return true;
      if ((params.get("url") || "").indexOf("/api/stream/") >= 0) return true;
    } catch (e) {
      /* ignore */
    }
    try {
      if (g.parent === g) return false;
      var parentPath = g.parent.location.pathname || "";
      if (parentPath.indexOf("/collection") < 0) return false;
      return Boolean(new URLSearchParams(g.parent.location.search || "").get("station"));
    } catch (e2) {
      return false;
    }
  }

  if (!isCollectionStationEmbed()) return;
  g.__DATALAB_COLLECTION_THEME__ = true;

  var THEME_MSG = "datalab-lerobot-theme";
  var THEME_SYNC_MSG = "datalab-lerobot-theme-sync";
  var THEME_CLICK_STYLE_ID = "datalab-theme-click-unhide";
  var VALID_THEMES = { light: true, dark: true };
  var MENU_LABELS = {
    light: [/^(浅色|Light)$/i],
    dark: [/^(深色|Dark)$/i],
  };

  function normalizeTheme(raw) {
    var t = String(raw || "light").toLowerCase();
    if (t === "dark") return "dark";
    if (t === "system" || t === "auto") {
      try {
        return g.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      } catch (e) {
        return "light";
      }
    }
    return "light";
  }

  function readStoredTheme() {
    try {
      return normalizeTheme(g.localStorage.getItem("theme"));
    } catch (e) {
      return "light";
    }
  }

  function isThemeTriggerButton(btn) {
    if (!btn || btn.tagName !== "BUTTON") return false;
    if (btn.closest("[data-datalab-collection-mode-root]")) return false;
    var aria = btn.getAttribute("aria-label") || "";
    var sr = btn.querySelector(".sr-only");
    var srText = sr ? sr.textContent || "" : "";
    return /theme|主题|テーマ/i.test(aria + " " + srText);
  }

  function findThemeTriggerButton() {
    var buttons = document.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i++) {
      if (isThemeTriggerButton(buttons[i])) return buttons[i];
    }
    return null;
  }

  function menuItemLabel(item) {
    return (item.textContent || "").replace(/\s+/g, " ").trim();
  }

  function findThemeMenuItem(theme) {
    var patterns = MENU_LABELS[theme] || MENU_LABELS.light;
    var items = document.querySelectorAll('[role="menuitem"], [role="menuitemradio"]');
    for (var i = 0; i < items.length; i++) {
      var label = menuItemLabel(items[i]);
      for (var p = 0; p < patterns.length; p++) {
        if (patterns[p].test(label)) return items[i];
      }
    }
    return null;
  }

  function enableThemeClickHelper() {
    if (document.getElementById(THEME_CLICK_STYLE_ID)) return;
    var style = document.createElement("style");
    style.id = THEME_CLICK_STYLE_ID;
    style.textContent =
      "html[data-datalab-collection-embed=\"1\"] #lerobot-root nav{" +
      "display:flex!important;visibility:visible!important;pointer-events:auto!important;" +
      "position:fixed!important;left:-10000px!important;top:0!important;" +
      "height:3rem!important;min-height:3rem!important;max-height:3rem!important;" +
      "opacity:0.01!important;overflow:visible!important;width:960px!important;z-index:2147483646!important;" +
      "}";
    document.head.appendChild(style);
  }

  function disableThemeClickHelper() {
    var el = document.getElementById(THEME_CLICK_STYLE_ID);
    if (el) el.remove();
  }

  function notifyParentTheme(theme) {
    if (g.parent === g) return;
    try {
      g.parent.postMessage(
        {
          type: THEME_SYNC_MSG,
          theme: normalizeTheme(theme),
          source: "iframe-ui",
        },
        g.location.origin,
      );
    } catch (e) {
      /* ignore */
    }
  }

  function themeDomMatches(theme) {
    theme = normalizeTheme(theme);
    var root = document.getElementById("lerobot-root");
    if (!root) return false;
    var isDark = root.classList.contains("dark");
    return theme === "dark" ? isDark : !isDark;
  }

  function applyThemeViaUi(theme, attempt, done) {
    theme = normalizeTheme(theme);
    attempt = attempt || 0;
    done = done || function () {};

    enableThemeClickHelper();

    var trigger = findThemeTriggerButton();
    if (!trigger) {
      if (attempt < 48) {
        g.setTimeout(function () {
          applyThemeViaUi(theme, attempt + 1, done);
        }, 250);
        return;
      }
      disableThemeClickHelper();
      done(false);
      return;
    }

    try {
      trigger.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
      trigger.click();
      trigger.dispatchEvent(new MouseEvent("pointerup", { bubbles: true }));
    } catch (e1) {
      /* ignore */
    }

    g.setTimeout(function () {
      var item = findThemeMenuItem(theme);
      if (!item) {
        if (attempt < 48) {
          applyThemeViaUi(theme, attempt + 1, done);
          return;
        }
        disableThemeClickHelper();
        done(false);
        return;
      }
      try {
        item.click();
      } catch (e2) {
        /* ignore */
      }
      g.setTimeout(function () {
        disableThemeClickHelper();
        var applied = readStoredTheme() === theme && themeDomMatches(theme);
        if (applied) {
          notifyParentTheme(theme);
          done(true);
          return;
        }
        if (attempt < 48) {
          applyThemeViaUi(theme, attempt + 1, done);
          return;
        }
        done(false);
      }, 120);
    }, 80);
  }

  function applyTheme(theme) {
    theme = normalizeTheme(theme);
    if (!VALID_THEMES[theme]) theme = "light";

    if (readStoredTheme() === theme && themeDomMatches(theme)) {
      notifyParentTheme(theme);
      return;
    }

    applyThemeViaUi(theme, 0, function (ok) {
      if (ok) return;
      try {
        g.localStorage.setItem("theme", theme);
      } catch (e) {
        /* ignore */
      }
      g.location.reload();
    });
  }

  function handleParentThemeMessage(event) {
    if (event.source !== g.parent) return;
    if (event.origin !== g.location.origin) return;
    var data = event.data;
    if (!data || typeof data !== "object" || data.type !== THEME_MSG) return;
    applyTheme(data.theme);
  }

  if (!g.__DATALAB_COLLECTION_THEME_MSG_BOUND__) {
    g.__DATALAB_COLLECTION_THEME_MSG_BOUND__ = true;
    g.addEventListener("message", handleParentThemeMessage);
  }

  function bootstrapThemeSync() {
    var theme = readStoredTheme();
    try {
      var raw = g.localStorage.getItem("theme");
      if (raw === "system" || raw === "auto" || raw === null) {
        g.localStorage.setItem("theme", theme);
      }
    } catch (e) {
      /* ignore */
    }
    notifyParentTheme(theme);
  }

  bootstrapThemeSync();
  g.setTimeout(bootstrapThemeSync, 500);
  g.setTimeout(bootstrapThemeSync, 2000);
})();
