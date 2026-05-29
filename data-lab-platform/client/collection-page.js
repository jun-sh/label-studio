/**
 * /collection (layer 1) — EGO station list only. Online stations open layer 2 via parent SPA.
 */
(function () {
  "use strict";

  var API = "/lerobot/api/collection/stations";
  var listEl = document.getElementById("datalab-station-list");
  var MAX_RETRIES = 4;
  var RETRY_BASE_MS = 600;
  var REFRESH_MS = 20_000;

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

  function fetchStationsOnce() {
    return fetch(API, { credentials: "same-origin", cache: "no-store" }).then(function (res) {
      if (!res.ok) throw new Error("load_failed");
      return res.json();
    });
  }

  function loadStations(options) {
    options = options || {};
    var attempt = 0;
    var userInitiated = Boolean(options.userInitiated);

    if (!hasRendered || userInitiated) {
      showLoading(userInitiated ? "正在重新加载…" : undefined);
    }

    function tryFetch() {
      return fetchStationsOnce()
        .then(function (data) {
          stations = (data && data.stations) || [];
          renderList();
        })
        .catch(function () {
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
      fetchStationsOnce()
        .then(function (data) {
          stations = (data && data.stations) || [];
          if (hasRendered) {
            renderList();
          }
        })
        .catch(function () {
          /* keep last good list on background refresh failure */
        });
    }, REFRESH_MS);
  }

  loadStations();
  startRefresh();
})();
