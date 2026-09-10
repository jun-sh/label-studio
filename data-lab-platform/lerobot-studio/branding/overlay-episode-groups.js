/**
 * Episodes sidebar: group rows by session date (MM-DD) with collapsible headers.
 */
(function () {
  "use strict";

  var g = typeof globalThis !== "undefined" ? globalThis : window;
  if (g.__DATALAB_OVERLAY_EPISODE_GROUPS__) return;

  function isDataLabEmbed() {
    try {
      if (document.documentElement.getAttribute("data-datalab-embed") === "1") {
        return true;
      }
      var params = new URLSearchParams(g.location.search || "");
      if (params.get("datalab_embed") === "1") return true;
    } catch (e) {
      /* ignore */
    }
    try {
      if (g.parent !== g) {
        var parentPath = g.parent.location.pathname || "";
        if (parentPath.indexOf("/data") >= 0) return true;
        if (parentPath.indexOf("/collection") >= 0) {
          return Boolean(
            new URLSearchParams(g.parent.location.search || "").get("station"),
          );
        }
      }
    } catch (e2) {
      /* ignore */
    }
    return false;
  }

  if (!isDataLabEmbed()) return;
  g.__DATALAB_OVERLAY_EPISODE_GROUPS__ = true;

  var TICK_DEBOUNCE_MS = 200;
  var STORAGE_KEY = "datalab-episode-date-collapsed";
  var tickScheduled = false;
  var tickTimer = null;
  var applying = false;
  var collapsedState = loadCollapsedState();
  var uploadDateByIndex = null;
  var uploadDateFetchKey = null;
  var uploadDateFetchPromise = null;

  function parseSessionDateFromTaskText(text) {
    if (!text) return null;
    var parts = String(text)
      .replace(/\s+/g, " ")
      .split("·");
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i].trim();
      var withFrames = part.match(/^(\d{2}-\d{2})(?:\s*\([^)]*\)|\s+\d+f)?$/i);
      if (withFrames) return withFrames[1];
      if (/^\d{2}-\d{2}$/.test(part)) return part;
    }
    return null;
  }

  function resolveEpisodeDate(taskText, episodeIndex) {
    var fromTask = parseSessionDateFromTaskText(taskText);
    if (fromTask) return fromTask;
    if (
      uploadDateByIndex &&
      uploadDateByIndex[episodeIndex] &&
      /^\d{2}-\d{2}$/.test(uploadDateByIndex[episodeIndex])
    ) {
      return uploadDateByIndex[episodeIndex];
    }
    return null;
  }

  function streamStationIdFromUrl() {
    try {
      var raw = new URLSearchParams(g.location.search || "").get("url") || "";
      var m = raw.match(/\/lerobot\/api\/stream\/([^/]+)\/?/i);
      if (m) return decodeURIComponent(m[1]);
    } catch (e) {
      /* ignore */
    }
    return null;
  }

  function ensureUploadDatesLoaded() {
    var stationId = streamStationIdFromUrl();
    if (!stationId) {
      uploadDateByIndex = null;
      uploadDateFetchKey = null;
      return Promise.resolve();
    }
    if (uploadDateFetchKey === stationId && uploadDateByIndex) {
      return Promise.resolve();
    }
    if (uploadDateFetchPromise && uploadDateFetchKey === stationId) {
      return uploadDateFetchPromise;
    }
    uploadDateFetchKey = stationId;
    uploadDateFetchPromise = fetch(
      "/lerobot/api/stream/" + encodeURIComponent(stationId) + "/episode-dates.json",
    )
      .then(function (res) {
        return res.ok ? res.json() : { episodes: [] };
      })
      .then(function (payload) {
        var map = {};
        var episodes = payload && payload.episodes ? payload.episodes : [];
        for (var i = 0; i < episodes.length; i++) {
          var ep = episodes[i];
          if (ep && ep.date && /^\d{2}-\d{2}$/.test(ep.date)) {
            map[Number(ep.episode_index)] = ep.date;
          }
        }
        uploadDateByIndex = map;
      })
      .catch(function () {
        uploadDateByIndex = {};
      })
      .finally(function () {
        uploadDateFetchPromise = null;
      });
    return uploadDateFetchPromise;
  }

  function pageLang() {
    try {
      var q = new URLSearchParams(g.location.search || "").get("lang") || "";
      var html = document.documentElement.lang || "";
      return (q || html || "en").toLowerCase();
    } catch (e) {
      return "en";
    }
  }

  function isZh() {
    return pageLang().indexOf("zh") === 0;
  }

  function loadCollapsedState() {
    try {
      var raw = g.sessionStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};
    }
  }

  function saveCollapsedState() {
    try {
      g.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(collapsedState));
    } catch (e) {
      /* ignore */
    }
  }

  function formatDateGroupLabel(mmdd) {
    if (!mmdd || !/^\d{2}-\d{2}$/.test(mmdd)) {
      return isZh() ? "未知日期" : "Unknown date";
    }
    var bits = mmdd.split("-");
    var month = parseInt(bits[0], 10);
    var day = parseInt(bits[1], 10);
    if (isZh()) return month + "月" + day + "日";
    return mmdd;
  }

  function buttonText(btn) {
    return (btn.textContent || "").replace(/\s+/g, " ").trim();
  }

  function findEpisodeToolbarButton(labelRe) {
    var buttons = document.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      if (labelRe.test(buttonText(btn))) return btn;
    }
    return null;
  }

  function findEpisodeSearchInput() {
    var inputs = document.querySelectorAll('input[type="search"], input[type="text"]');
    for (var i = 0; i < inputs.length; i++) {
      var ph = (inputs[i].getAttribute("placeholder") || "").toLowerCase();
      if (ph.indexOf("search") >= 0 || ph.indexOf("搜索") >= 0) return inputs[i];
    }
    return null;
  }

  function markEpisodesPanel() {
    var editBtn = findEpisodeToolbarButton(/^(编辑|Edit)$/i);
    var selectBtn = findEpisodeToolbarButton(/^(多选|Select multiple)$/i);
    var anchor = editBtn || selectBtn;
    if (anchor) {
      var group = anchor.closest(".dv-groupview");
      if (group) group.setAttribute("data-datalab-episodes-panel", "1");
    }
  }

  function parseEpisodeIndex(label) {
    var m = label.match(/(?:选择 Episode|Select Episode)\s*(\d+)/i);
    return m ? parseInt(m[1], 10) : null;
  }

  function extractTaskText(rowEl, selectBtn) {
    var rowText = (rowEl.textContent || "").replace(/\s+/g, " ").trim();
    if (rowText) return rowText;
    var text = buttonText(selectBtn);
    var m = text.match(/(?:选择 Episode|Select Episode)\s*\d+\s*(.*)$/i);
    if (m && m[1]) return m[1].trim();
    return text;
  }

  function isEpisodeSelectLabel(label) {
    return /^(选择 Episode|Select Episode)\s+\d+/i.test(label || "");
  }

  function findEpisodeRowEl(el) {
    return (
      el.closest(".group") ||
      el.closest("[class*='border-b']") ||
      el.parentElement
    );
  }

  function findEpisodeRows() {
    var nodes = document.querySelectorAll(
      'button[aria-label], [role="button"][aria-label]',
    );
    var rows = [];
    var seen = new Set();

    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var label = el.getAttribute("aria-label") || "";
      if (!isEpisodeSelectLabel(label)) continue;

      var rowEl = findEpisodeRowEl(el);
      if (!rowEl || seen.has(rowEl)) continue;
      seen.add(rowEl);

      var episodeIndex = parseEpisodeIndex(label);
      if (episodeIndex == null) continue;

      var taskText = extractTaskText(rowEl, el);
      var date = resolveEpisodeDate(taskText, episodeIndex);
      rows.push({
        rowEl: rowEl,
        episodeIndex: episodeIndex,
        date: date,
        taskText: taskText,
      });
    }

    rows.sort(function (a, b) {
      return a.episodeIndex - b.episodeIndex;
    });
    return rows;
  }

  function findListContainer(rows) {
    if (!rows.length) return null;
    var first = rows[0].rowEl;
    var parent = first.parentElement;
    while (parent && parent !== document.body) {
      if (parent.contains(rows[rows.length - 1].rowEl)) return parent;
      parent = parent.parentElement;
    }
    return first.parentElement;
  }

  function removeExistingHeaders(container) {
    if (!container) return;
    var headers = container.querySelectorAll("[data-datalab-episode-date-header]");
    for (var i = 0; i < headers.length; i++) {
      headers[i].remove();
    }
  }

  function buildGroups(rows) {
    var groups = [];
    var indexByDate = new Map();

    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var dateKey = row.date || "__unknown__";
      var group = indexByDate.get(dateKey);
      if (!group) {
        group = { dateKey: dateKey, rows: [] };
        indexByDate.set(dateKey, group);
        groups.push(group);
      }
      group.rows.push(row);
    }
    return groups;
  }

  function defaultCollapsed(dateKey, groupIndex, totalGroups) {
    if (collapsedState[dateKey] != null) return collapsedState[dateKey];
    if (totalGroups === 1) return false;
    return groupIndex < totalGroups - 1;
  }

  function applyRowVisibility(group) {
    var hidden = group.header.getAttribute("data-collapsed") === "1";
    for (var i = 0; i < group.rows.length; i++) {
      group.rows[i].rowEl.setAttribute(
        "data-datalab-episode-hidden",
        hidden ? "1" : "0",
      );
    }
  }

  function createHeader(dateKey, count, collapsed) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("data-datalab-episode-date-header", dateKey);
    btn.setAttribute("data-collapsed", collapsed ? "1" : "0");
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");

    var chevron = document.createElement("span");
    chevron.setAttribute("data-datalab-episode-date-chevron", "1");
    chevron.textContent = "▾";
    btn.appendChild(chevron);

    var label = document.createElement("span");
    label.setAttribute("data-datalab-episode-date-label", "1");
    label.textContent =
      dateKey === "__unknown__"
        ? isZh()
          ? "未知日期"
          : "Unknown date"
        : formatDateGroupLabel(dateKey);
    btn.appendChild(label);

    var countEl = document.createElement("span");
    countEl.setAttribute("data-datalab-episode-date-count", "1");
    countEl.textContent =
      " · " + count + (isZh() ? "条" : "");
    btn.appendChild(countEl);

    return btn;
  }

  function applyGrouping() {
    if (applying) return;
    applying = true;
    try {
      markEpisodesPanel();
      var rows = findEpisodeRows();
      if (rows.length < 2) {
        var container = findListContainer(rows);
        if (container) removeExistingHeaders(container);
        for (var i = 0; i < rows.length; i++) {
          rows[i].rowEl.removeAttribute("data-datalab-episode-row");
          rows[i].rowEl.removeAttribute("data-datalab-episode-hidden");
          rows[i].rowEl.removeAttribute("data-datalab-episode-date");
        }
        return;
      }

      var groups = buildGroups(rows);
      if (!groups.length) return;

      var listContainer = findListContainer(rows);
      if (!listContainer) return;

      removeExistingHeaders(listContainer);

      for (var gIdx = 0; gIdx < groups.length; gIdx++) {
        var group = groups[gIdx];
        var collapsed = defaultCollapsed(group.dateKey, gIdx, groups.length);
        var header = createHeader(group.dateKey, group.rows.length, collapsed);

        (function (headerEl, dateKey, groupRows) {
          headerEl.addEventListener("click", function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var isCollapsed = headerEl.getAttribute("data-collapsed") === "1";
            var next = !isCollapsed;
            headerEl.setAttribute("data-collapsed", next ? "1" : "0");
            headerEl.setAttribute("aria-expanded", next ? "false" : "true");
            collapsedState[dateKey] = next;
            saveCollapsedState();
            applyRowVisibility({ header: headerEl, rows: groupRows });
          });
        })(header, group.dateKey, group.rows);

        listContainer.insertBefore(header, group.rows[0].rowEl);
        group.header = header;

        for (var rIdx = 0; rIdx < group.rows.length; rIdx++) {
          var row = group.rows[rIdx];
          row.rowEl.setAttribute("data-datalab-episode-row", "1");
          row.rowEl.setAttribute("data-datalab-episode-date", group.dateKey);
          applyRowVisibility(group);
        }
      }
    } finally {
      applying = false;
    }
  }

  function scheduleTick() {
    if (tickScheduled) return;
    tickScheduled = true;
    if (tickTimer) clearTimeout(tickTimer);
    tickTimer = setTimeout(function () {
      tickScheduled = false;
      tickTimer = null;
      ensureUploadDatesLoaded().finally(applyGrouping);
    }, TICK_DEBOUNCE_MS);
  }

  function boot() {
    ensureUploadDatesLoaded().finally(function () {
      applyGrouping();
    });
    [400, 1200, 2500, 5000, 8000].forEach(function (ms) {
      setTimeout(function () {
        ensureUploadDatesLoaded().finally(applyGrouping);
      }, ms);
    });
    if (!g.__DATALAB_OVERLAY_EPISODE_GROUPS_OBSERVER__ && document.body) {
      g.__DATALAB_OVERLAY_EPISODE_GROUPS_OBSERVER__ = new MutationObserver(
        function () {
          if (applying) return;
          scheduleTick();
        },
      );
      g.__DATALAB_OVERLAY_EPISODE_GROUPS_OBSERVER__.observe(document.body, {
        childList: true,
        subtree: true,
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
