/**
 * /collection (layer 1) — EGO station list only. Online/stream stations open layer 2.
 */
(function () {
  "use strict";

  var API = "/lerobot/api/collection/stations";
  var stationPingUrl = function (id) {
    return API + "/" + encodeURIComponent(id) + "/ping";
  };
  var CATALOG_API = "/lerobot/api/collection/stations/catalog";
  var STATIC_CATALOG = "/_datalab/collection-stations.catalog.json";
  var CACHE_KEY = "datalab:collection:stations:v1";
  var listEl = document.getElementById("datalab-station-list");
  var MAX_RETRIES = 3;
  var RETRY_BASE_MS = 800;
  var REFRESH_MS = 20_000;
  var FETCH_TIMEOUT_MS = 6_000;
  var CATALOG_TIMEOUT_MS = 4_000;

  var STATION_NAME_EN = {
    "ego-001": "EGO station · ego-001 (130)",
    "ego-lan-02": "EGO station · Lab B",
    "ego-wan-01": "EGO station · Public node",
  };

  var STRINGS = {
    zh: {
      pageTitle: "EGO 采集站",
      pageSubtitle: "采集站列表 · 在线状态",
      documentTitle: "采集 | Data Lab",
      loadingList: "正在加载采集站列表…",
      reloading: "正在重新加载…",
      loadingRetry: function (n, max) {
        return "正在加载采集站列表…（" + n + "/" + max + "）";
      },
      loadError: "加载采集站列表失败，请稍后重试。",
      retry: "重试",
      noStations: "未找到可用采集站。",
      online: "在线",
      offline: "离线",
      scopeLan: "局域网",
      scopeWan: "公网",
    },
    en: {
      pageTitle: "EGO collection stations",
      pageSubtitle: "Station list · online status",
      documentTitle: "Collection | Data Lab",
      loadingList: "Loading station list…",
      reloading: "Reloading…",
      loadingRetry: function (n, max) {
        return "Loading station list… (" + n + "/" + max + ")";
      },
      loadError: "Failed to load station list. Please try again later.",
      retry: "Retry",
      noStations: "No collection stations available.",
      online: "Online",
      offline: "Offline",
      scopeLan: "LAN",
      scopeWan: "Public",
    },
  };

  var stations = [];
  var hasRendered = false;
  var refreshTimer = null;

  function pageLang() {
    try {
      var q = new URLSearchParams(window.location.search).get("lang");
      if (q) return String(q).toLowerCase().indexOf("zh") === 0 ? "zh" : "en";
    } catch (err) {
      /* ignore */
    }
    try {
      var topWin = window.top || window.parent;
      if (topWin && topWin !== window && topWin.APP_SETTINGS) {
        var fromSettings =
          topWin.APP_SETTINGS.locale ||
          (topWin.APP_SETTINGS.user && topWin.APP_SETTINGS.user.ui_locale) ||
          "";
        if (fromSettings) {
          return String(fromSettings).toLowerCase().indexOf("zh") === 0 ? "zh" : "en";
        }
      }
    } catch (err2) {
      /* cross-origin */
    }
    try {
      var stored = window.localStorage.getItem("ui_locale");
      if (stored) return String(stored).toLowerCase().indexOf("zh") === 0 ? "zh" : "en";
    } catch (err3) {
      /* ignore */
    }
    var docLang =
      (document.documentElement &&
        (document.documentElement.getAttribute("data-ui-locale") ||
          document.documentElement.lang)) ||
      "";
    return String(docLang).toLowerCase().indexOf("zh") === 0 ? "zh" : "en";
  }

  function t(key) {
    var lang = pageLang();
    var bucket = STRINGS[lang] || STRINGS.en;
    return bucket[key];
  }

  function applyPageChrome() {
    var lang = pageLang();
    document.documentElement.lang = lang === "zh" ? "zh-Hans" : "en";
    var titleEl = document.querySelector(".datalab-collection-page__title");
    var subtitleEl = document.querySelector(".datalab-collection-page__subtitle");
    if (titleEl) titleEl.textContent = t("pageTitle");
    if (subtitleEl) subtitleEl.textContent = t("pageSubtitle");
    document.title = t("documentTitle");
  }

  function stationDisplayName(station) {
    if (pageLang() === "en") {
      if (station.name_en) return station.name_en;
      if (STATION_NAME_EN[station.id]) return STATION_NAME_EN[station.id];
    }
    return station.name || "—";
  }

  function scopeLabel(scope) {
    if (scope === "wan") return t("scopeWan");
    if (scope === "lan") return t("scopeLan");
    return scope || "—";
  }

  function navigateToStation(station) {
    if (!station) return;
    if (!station.online && !station.stream) return;
    var url = "/collection?station=" + encodeURIComponent(station.id);
    var topWin = window.top || window.parent || window;

    if (topWin !== window) {
      try {
        topWin.location.assign(url);
        return;
      } catch (err) {
        /* cross-origin fallback below */
      }
      try {
        topWin.postMessage(
          { type: "datalab:collection:select-station", stationId: station.id },
          "*",
        );
        return;
      } catch (err2) {
        /* ignore */
      }
    }
    window.location.href = url;
  }

  function showLoading(message) {
    if (!listEl) return;
    listEl.innerHTML =
      '<p class="datalab-collection-page__loading">' +
      (message || t("loadingList")) +
      "</p>";
  }

  function showError() {
    if (!listEl) return;
    listEl.innerHTML =
      '<p class="datalab-collection-page__error">' + t("loadError") + "</p>" +
      '<p class="datalab-collection-page__loading" style="margin-top:8px;">' +
      '<button type="button" class="datalab-collection-page__retry" id="datalab-station-retry">' +
      t("retry") +
      "</button>" +
      "</p>";
    var btn = document.getElementById("datalab-station-retry");
    if (btn) {
      btn.addEventListener("click", function () {
        loadStations({ userInitiated: true });
      });
    }
  }

  function readCache() {
    try {
      var raw = window.sessionStorage.getItem(CACHE_KEY);
      if (!raw) return null;
      var data = JSON.parse(raw);
      if (data && Array.isArray(data.stations) && data.stations.length) return data.stations;
    } catch (err) {
      /* ignore */
    }
    return null;
  }

  function writeCache(list) {
    try {
      window.sessionStorage.setItem(
        CACHE_KEY,
        JSON.stringify({ stations: list, updatedAt: Date.now() }),
      );
    } catch (err) {
      /* ignore */
    }
  }

  function renderList() {
    if (!listEl) return;
    if (!stations.length) {
      listEl.innerHTML = '<p class="datalab-collection-page__error">' + t("noStations") + "</p>";
      return;
    }

    listEl.innerHTML = "";
    stations.forEach(function (station) {
      var card = document.createElement("article");
      var online = Boolean(station.online);
      var clickable = online || Boolean(station.stream);
      card.className =
        "datalab-station-card " + (online ? "datalab-station-card--online" : "datalab-station-card--offline");

      if (clickable) {
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("aria-disabled", "false");
      } else {
        card.setAttribute("aria-disabled", "true");
      }

      card.innerHTML =
        '<div class="datalab-station-card__body">' +
        '<h3 class="datalab-station-card__name"></h3>' +
        '<p class="datalab-station-card__meta"></p>' +
        "</div>" +
        '<span class="datalab-station-card__badge"></span>';

      card.querySelector(".datalab-station-card__name").textContent = stationDisplayName(station);
      card.querySelector(".datalab-station-card__meta").textContent =
        scopeLabel(station.scope) + " · " + (station.host || "—");
      var badge = card.querySelector(".datalab-station-card__badge");
      badge.textContent = online ? t("online") : t("offline");
      badge.className +=
        online ? " datalab-station-card__badge--online" : " datalab-station-card__badge--offline";

      if (clickable) {
        card.addEventListener("click", function () {
          navigateToStation(station);
        });
        card.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            navigateToStation(station);
          }
        });
      }

      listEl.appendChild(card);
    });
    hasRendered = true;
  }

  function fetchJson(url, timeoutMs) {
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = null;
    if (controller && timeoutMs > 0) {
      timer = setTimeout(function () {
        controller.abort();
      }, timeoutMs);
    }
    return fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      signal: controller ? controller.signal : undefined,
    })
      .then(function (res) {
        if (timer) clearTimeout(timer);
        if (!res.ok) throw new Error("load_failed");
        return res.json();
      })
      .catch(function (err) {
        if (timer) clearTimeout(timer);
        throw err;
      });
  }

  function mergeLiveStatus(catalog, liveList) {
    var liveById = {};
    (liveList || []).forEach(function (s) {
      liveById[s.id] = s;
    });
    return catalog.map(function (base) {
      var live = liveById[base.id];
      if (!live) return base;
      return Object.assign({}, base, {
        online: Boolean(live.online),
        datasetUrl: live.datasetUrl || base.datasetUrl,
        httpDatasetUrl: live.httpDatasetUrl || base.httpDatasetUrl,
      });
    });
  }

  function loadStaticCatalog() {
    return fetchJson(STATIC_CATALOG, CATALOG_TIMEOUT_MS).then(function (data) {
      return Array.isArray(data) ? data : (data && data.stations) || [];
    });
  }

  function paintCatalogFirst() {
    var cached = readCache();
    if (cached) {
      stations = cached;
      renderList();
      return Promise.resolve();
    }
    return loadStaticCatalog()
      .then(function (list) {
        if (!list.length) throw new Error("empty_static_catalog");
        stations = list;
        renderList();
      })
      .catch(function () {
        return fetchJson(CATALOG_API, CATALOG_TIMEOUT_MS).then(function (data) {
          stations = (data && data.stations) || [];
          if (stations.length) renderList();
        });
      });
  }

  function probeStreamStationsOnline() {
    var streamStations = stations.filter(function (s) {
      return Boolean(s.stream);
    });
    if (!streamStations.length) return Promise.resolve();
    return Promise.all(
      streamStations.map(function (station) {
        return fetchJson(stationPingUrl(station.id), 3000)
          .then(function (data) {
            station.online = Boolean(data && data.online);
          })
          .catch(function () {
            /* keep previous badge */
          });
      }),
    ).then(function () {
      writeCache(stations);
      renderList();
    });
  }

  function fetchLiveStations(attempt) {
    var catalogSnapshot = stations.slice();
    return fetchJson(API, FETCH_TIMEOUT_MS)
      .then(function (data) {
        var liveList = (data && data.stations) || [];
        if (catalogSnapshot.length) {
          stations = mergeLiveStatus(catalogSnapshot, liveList);
        } else {
          stations = liveList;
        }
        writeCache(stations);
        renderList();
      })
      .catch(function () {
        if (catalogSnapshot.length && hasRendered) {
          return probeStreamStationsOnline();
        }
        throw new Error("live_fetch_failed");
      });
  }

  function loadStations(options) {
    options = options || {};
    var attempt = 0;
    var userInitiated = Boolean(options.userInitiated);

    if (!hasRendered) {
      showLoading(userInitiated ? t("reloading") : t("loadingList"));
    }

    function tryFetch() {
      return fetchLiveStations(attempt).catch(function () {
        attempt += 1;
        if (attempt < MAX_RETRIES) {
          if (!hasRendered) {
            showLoading(t("loadingRetry")(attempt, MAX_RETRIES));
          }
          return new Promise(function (resolve) {
            setTimeout(resolve, RETRY_BASE_MS * attempt);
          }).then(tryFetch);
        }
        if (!hasRendered) {
          showError();
        }
      });
    }

    return paintCatalogFirst().then(tryFetch);
  }

  function startRefresh() {
    if (refreshTimer) return;
    refreshTimer = setInterval(function () {
      fetchLiveStations(0).catch(function () {
        /* keep last good list */
      });
    }, REFRESH_MS);
  }

  applyPageChrome();

  var cached = readCache();
  if (cached) {
    stations = cached;
    renderList();
    loadStations();
  } else {
    loadStations();
  }
  startRefresh();
})();
