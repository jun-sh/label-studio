/**
 * /data page — login hint + visualizer iframe synced to Label Studio ui_locale.
 * Supports /data/:datasetId deep links and ?dataset= query aliases.
 */
(function () {
  "use strict";

  var UI_LOCALE_KEY = "ui_locale";

  var frame = document.getElementById("datalab-viz-frame");
  var hint = document.getElementById("datalab-login-hint");

  function mapUiLocaleToVisualizer(uiLocale) {
    var loc = String(uiLocale || "en").toLowerCase();
    if (loc.indexOf("zh") === 0) return "zh";
    return "en";
  }

  function readStoredUiLocale() {
    try {
      return window.localStorage.getItem(UI_LOCALE_KEY) || window.localStorage.getItem("i18nextLng") || "";
    } catch (e) {
      return "";
    }
  }

  function applyPageLocale(uiLocale) {
    var normalized = mapUiLocaleToVisualizer(uiLocale);
    document.documentElement.lang = normalized === "zh" ? "zh-Hans" : "en";
    return normalized;
  }

  function readDatasetFromPath() {
    var match = window.location.pathname.match(/^\/data\/([^/]+)\/?$/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  function buildVisualizerSrc(lang, datasetId) {
    var params = new URLSearchParams();
    params.set("datalab_embed", "1");
    params.set("lang", lang || "en");
    if (datasetId) {
      var httpUrl = datasetUrlOverrides[datasetId];
      params.set("url", httpUrl || "sample://" + datasetId);
    }
    return "/lerobot/?" + params.toString();
  }

  var datasetUrlOverrides = {
    ego_214_hand_pose: "/lerobot/api/sample/ego_214_hand_pose/dataset/",
  };

  function loadDatasetUrlOverrides() {
    return fetch("/lerobot/api/datasets", { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) return;
        return response.json();
      })
      .then(function (items) {
        if (!Array.isArray(items)) return;
        items.forEach(function (entry) {
          if (entry && entry.id && entry.httpDatasetUrl) {
            datasetUrlOverrides[entry.id] = entry.httpDatasetUrl;
          } else if (entry && entry.id && entry.url && String(entry.url).indexOf("/api/sample/") >= 0) {
            datasetUrlOverrides[entry.id] = entry.url;
          }
        });
      })
      .catch(function () {
        /* keep static fallback */
      });
  }

  function normalizeDataVizUrl() {
    var params = new URLSearchParams(window.location.search);
    var queryDataset = params.get("dataset");
    if (queryDataset && !readDatasetFromPath()) {
      window.history.replaceState(null, "", "/data/" + encodeURIComponent(queryDataset));
    }
  }

  function setIframeSrc(lang) {
    if (!frame) return;
    frame.setAttribute("src", buildVisualizerSrc(lang, readDatasetFromPath()));
  }

  function bootstrapLocale(uiLocale) {
    normalizeDataVizUrl();
    var lang = applyPageLocale(uiLocale || readStoredUiLocale() || "en");
    loadDatasetUrlOverrides().finally(function () {
      setIframeSrc(lang);
    });
  }

  bootstrapLocale(readStoredUiLocale());

  fetch("/api/current-user/whoami", { credentials: "same-origin" })
    .then(function (response) {
      if (!response.ok) {
        if (hint) hint.hidden = false;
        return null;
      }
      return response.json();
    })
    .then(function (user) {
      if (!user || !user.id) {
        if (hint) hint.hidden = false;
        bootstrapLocale(readStoredUiLocale());
        return;
      }
      if (hint) hint.hidden = true;
      bootstrapLocale(user.ui_locale || readStoredUiLocale() || "en");
    })
    .catch(function () {
      if (hint) hint.hidden = false;
      bootstrapLocale(readStoredUiLocale());
    });
})();
