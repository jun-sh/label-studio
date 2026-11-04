/**
 * Collection stream embed: wire LeRobot sidebar delete UX to datalab L2 delete API.
 *
 * Matches io-ai / LeRobot viewer patterns:
 * - Toolbar: 多选 + 编辑/完成 + 导入 (no extra datalab delete chip)
 * - Edit mode: per-row edit + delete icon buttons
 * - Multi-select: bulk delete in the selection bar
 *
 * Upstream delete only toggles client state; for stream:// we persist via API.
 */
(function () {
  "use strict";

  var g = typeof globalThis !== "undefined" ? globalThis : window;
  if (g.__DATALAB_OVERLAY_EPISODE_DELETE__) return;

  function isCollectionStationEmbed() {
    try {
      var params = new URLSearchParams(g.location.search);
      if (params.get("datalab_collection") === "1") return true;
      if ((params.get("url") || "").indexOf("/api/stream/") >= 0) return true;
    } catch (e) {
      /* ignore */
    }
    try {
      if (g.parent !== g) {
        var parentPath = g.parent.location.pathname || "";
        if (parentPath.indexOf("/collection") < 0) return false;
        return Boolean(new URLSearchParams(g.parent.location.search || "").get("station"));
      }
    } catch (e2) {
      return false;
    }
    return false;
  }

  if (!isCollectionStationEmbed()) return;
  g.__DATALAB_OVERLAY_EPISODE_DELETE__ = true;

  var toastEl = null;
  var toastTimer = null;
  var busy = false;
  var hookInstalled = false;

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

  function stationIdFromContext() {
    try {
      var params = new URLSearchParams(g.location.search || "");
      var raw = params.get("url") || "";
      var m = raw.match(/\/lerobot\/api\/stream\/([^/]+)\/?/);
      if (m) return decodeURIComponent(m[1]);
      if (g.parent !== g) {
        var sid = new URLSearchParams(g.parent.location.search || "").get("station");
        if (sid) return sid;
      }
    } catch (e) {
      /* ignore */
    }
    return null;
  }

  function episodesPanelRoot() {
    return (
      document.querySelector("[data-datalab-episodes-panel='1']") ||
      document.querySelector(".dv-groupview") ||
      document.getElementById("lerobot-root") ||
      document.body
    );
  }

  function buttonText(btn) {
    return (btn.textContent || "").replace(/\s+/g, " ").trim();
  }

  function parseEpisodeIndex(label) {
    var m = String(label || "").match(/(?:选择 Episode|Select Episode)\s*(\d+)/i);
    return m ? parseInt(m[1], 10) : null;
  }

  function isEpisodeSelectLabel(label) {
    return /^(选择 Episode|Select Episode)\s+\d+/i.test(label || "");
  }

  function findEpisodeRowEl(el) {
    if (!el || !el.closest) return null;
    return (
      el.closest("[data-datalab-episode-row='1']") ||
      el.closest(".group.relative.flex.w-full") ||
      el.closest(".group")
    );
  }

  function episodeIndexFromRow(rowEl) {
    if (!rowEl) return null;
    var main = rowEl.querySelector('[role="button"][aria-label*="Episode"]');
    if (main) {
      var idx = parseEpisodeIndex(main.getAttribute("aria-label") || "");
      if (idx != null) return idx;
    }
    var badge = rowEl.querySelector(".font-mono");
    if (badge) {
      var m = String(badge.textContent || "").match(/#\s*(\d+)/);
      if (m) return parseInt(m[1], 10);
    }
    return null;
  }

  function rowIsBulkChecked(rowEl) {
    if (!rowEl) return false;
    if (rowEl.querySelector('input[type="checkbox"]:checked')) return true;
    var toggles = rowEl.querySelectorAll('[role="checkbox"]');
    for (var i = 0; i < toggles.length; i++) {
      var t = toggles[i];
      if (t.getAttribute("aria-checked") === "true") return true;
      if (t.getAttribute("data-state") === "checked") return true;
    }
    return false;
  }

  function collectBulkSelectedIndices() {
    var root = episodesPanelRoot();
    var out = new Set();
    var rows = root.querySelectorAll(
      "[data-datalab-episode-row='1'], .group.relative.flex.w-full.min-w-0.flex-col",
    );
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!rowIsBulkChecked(row)) continue;
      var idx = episodeIndexFromRow(row);
      if (idx != null) out.add(idx);
    }
    return Array.from(out).sort(function (a, b) {
      return a - b;
    });
  }

  function isRowDeleteButton(btn) {
    if (!btn || btn.getAttribute("data-datalab-delete-trigger") === "1") return false;
    var row = findEpisodeRowEl(btn);
    if (!row) return false;
    var title = (btn.getAttribute("title") || "").trim();
    if (/delete episode|删除 episode|删除 Episode|エピソードを削除/i.test(title)) return true;
    var cls = String(btn.className || "");
    return cls.indexOf("destructive") >= 0;
  }

  function isBulkDeleteButton(btn) {
    if (!btn || btn.closest("[data-datalab-episode-row='1']")) return false;
    var label = buttonText(btn);
    if (/^(批量删除|Delete selected|選択を削除)$/i.test(label)) return true;
    var title = (btn.getAttribute("title") || "").trim();
    return /bulkDelete|批量删除|Delete selected/i.test(title);
  }

  function removeLegacyToolbarDelete() {
    var stale = document.querySelectorAll("[data-datalab-delete-trigger]");
    for (var i = 0; i < stale.length; i++) {
      stale[i].remove();
    }
  }

  function showToast(message, variant) {
    if (toastTimer) clearTimeout(toastTimer);
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.setAttribute("data-datalab-delete-toast", "1");
      document.body.appendChild(toastEl);
    }
    toastEl.setAttribute("data-variant", variant || "error");
    toastEl.textContent = message;
    toastTimer = setTimeout(function () {
      if (toastEl && toastEl.parentNode) toastEl.parentNode.removeChild(toastEl);
      toastEl = null;
    }, 4500);
  }

  function confirmDelete(indices) {
    var list = indices.map(function (n) {
      return "#" + n;
    }).join(", ");
    if (isZh()) {
      return g.confirm(
        "从采集列表删除以下 episode？\n" +
          list +
          "\n\n仅删除 collection 视图（L2），不会删除 raw/segments。\n此操作不可从 UI 撤销。",
      );
    }
    return g.confirm(
      "Remove these episodes from the collection list?\n" +
        list +
        "\n\nL2/collection only — raw/segments are kept.\nThis cannot be undone from the UI.",
    );
  }

  function runDelete(indices) {
    if (busy) return;
    var stationId = stationIdFromContext();
    if (!stationId) {
      showToast(isZh() ? "无法识别工位" : "Station id missing", "error");
      return;
    }
    if (!indices || indices.length === 0) {
      showToast(
        isZh() ? "请先勾选要删除的 episode" : "Select episode(s) to delete first",
        "error",
      );
      return;
    }
    if (!confirmDelete(indices)) return;

    busy = true;
    fetch(
      "/lerobot/api/collection/stations/" +
        encodeURIComponent(stationId) +
        "/episodes/delete",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          episode_indices: indices,
          confirm: true,
          apply: true,
        }),
      },
    )
      .then(function (res) {
        return res.json().then(function (payload) {
          return { ok: res.ok, payload: payload };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.payload || result.payload.ok === false) {
          var detail =
            (result.payload && (result.payload.detail || result.payload.error)) ||
            (isZh() ? "删除失败" : "Delete failed");
          showToast(String(detail), "error");
          return;
        }
        showToast(
          isZh()
            ? "已删除 " + indices.length + " 条，正在刷新…"
            : "Removed " + indices.length + " episode(s). Reloading…",
          "ok",
        );
        setTimeout(function () {
          g.location.reload();
        }, 600);
      })
      .catch(function () {
        showToast(isZh() ? "网络错误" : "Network error", "error");
      })
      .finally(function () {
        busy = false;
      });
  }

  function onClickCapture(e) {
    var btn = e.target && e.target.closest ? e.target.closest("button") : null;
    if (!btn) return;

    var panel = episodesPanelRoot();
    if (!panel.contains(btn)) return;

    removeLegacyToolbarDelete();

    if (isBulkDeleteButton(btn)) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      runDelete(collectBulkSelectedIndices());
      return;
    }

    if (isRowDeleteButton(btn)) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      var row = findEpisodeRowEl(btn);
      var idx = episodeIndexFromRow(row);
      if (idx == null) {
        showToast(isZh() ? "无法识别 episode 编号" : "Episode index missing", "error");
        return;
      }
      runDelete([idx]);
    }
  }

  function installHooks() {
    removeLegacyToolbarDelete();
    if (hookInstalled) return;
    hookInstalled = true;
    document.addEventListener("click", onClickCapture, true);
    if (!g.__DATALAB_OVERLAY_EPISODE_DELETE_OBSERVER__ && document.body) {
      g.__DATALAB_OVERLAY_EPISODE_DELETE_OBSERVER__ = new MutationObserver(function () {
        removeLegacyToolbarDelete();
      });
      g.__DATALAB_OVERLAY_EPISODE_DELETE_OBSERVER__.observe(document.body, {
        childList: true,
        subtree: true,
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", installHooks);
  } else {
    installHooks();
  }
})();
