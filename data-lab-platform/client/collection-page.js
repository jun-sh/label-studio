/**
 * /collection (layer 1) — EGO station list only. Online stations open layer 2 via parent SPA.
 */
(function () {
  "use strict";

  var API = "/lerobot/api/collection/stations";
  var CATALOG_API = "/lerobot/api/collection/stations/catalog";
  var CACHE_KEY = "datalab:collection:stations:v1";
  var listEl = document.getElementById("datalab-station-list");
  var MAX_RETRIES = 5;
  var RETRY_BASE_MS = 800;
  var REFRESH_MS = 20_000;
  var FETCH_TIMEOUT_MS = 12_000;

  var stations = [];
  var hasRendered = false;
  var refreshTimer = null;

  function scopeLabel(scope) {
    if (scope === "wan") return "公网";
    if (scope === "lan") return "局域网";
    return scope || "—";
  }

  function navigateToStation(station) {
    if (!station || !station.online) return;
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
      (message || "正在加载采集站列表…") +
      "</p>";
  }

  function showError() {
    if (!listEl) return;
    listEl.innerHTML =
      '<p class="datalab-collection-page__error">加载采集站列表失败，请稍后重试。</p>' +
      '<p class="datalab-collection-page__loading" style="margin-top:8px;">' +
      '<button type="button" class="datalab-collection-page__retry" id="datalab-station-retry">重试</button>' +
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
      listEl.innerHTML = '<p class="datalab-collection-page__error">未找到可用采集站。</p>';
      return;
    }

    listEl.innerHTML = "";
    stations.forEach(function (station) {
      var card = document.createElement("article");
      var online = Boolean(station.online);
      card.className =
        "datalab-station-card " + (online ? "datalab-station-card--online" : "datalab-station-card--offline");

      if (online) {
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

      card.querySelector(".datalab-station-card__name").textContent = station.name;
      card.querySelector(".datalab-station-card__meta").textContent =
        scopeLabel(station.scope) + " · " + (station.host || "—");
      var badge = card.querySelector(".datalab-station-card__badge");
      badge.textContent = online ? "在线" : "离线";
      badge.className +=
        online ? " datalab-station-card__badge--online" : " datalab-station-card__badge--offline";

      if (online) {
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

  function paintCatalogFirst() {
    var cached = readCache();
    if (cached) {
      stations = cached;
      renderList();
      return;
    }
    return fetchJson(CATALOG_API, 5000)
      .then(function (data) {
        stations = (data && data.stations) || [];
        if (stations.length) {
          renderList();
        }
      })
      .catch(function () {
        /* catalog optional */
      });
  }

  function fetchLiveStations(attempt) {
    return fetchJson(API, FETCH_TIMEOUT_MS).then(function (data) {
      var liveList = (data && data.stations) || [];
      if (!hasRendered && stations.length) {
        stations = mergeLiveStatus(stations, liveList);
      } else {
        stations = liveList;
      }
      writeCache(stations);
      renderList();
    });
  }

  function loadStations(options) {
    options = options || {};
    var attempt = 0;
    var userInitiated = Boolean(options.userInitiated);

    if (!hasRendered || userInitiated) {
      showLoading(userInitiated ? "正在重新加载…" : "正在更新在线状态…");
    }

    if (!userInitiated) {
      paintCatalogFirst();
    }

    function tryFetch() {
      return fetchLiveStations(attempt).catch(function () {
        attempt += 1;
        if (attempt < MAX_RETRIES) {
          if (!hasRendered) {
            showLoading("正在加载采集站列表…（" + attempt + "/" + MAX_RETRIES + "）");
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

    return tryFetch();
  }

  function startRefresh() {
    if (refreshTimer) return;
    refreshTimer = setInterval(function () {
      fetchLiveStations(0).catch(function () {
        /* keep last good list */
      });
    }, REFRESH_MS);
  }

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
