/**
 * Collection stream embed: wire LeRobot sidebar delete UX to datalab L2 delete API.
 *
 * Success = server delete + reload → episode row gone (not gray).
 * Gray = viewer-only soft mark; we block it when possible and offer repair.
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
  var repairPrompted = false;

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

  function isInEpisodeSidebar(el) {
    if (!el || !el.closest) return false;
    var lr = document.getElementById("lerobot-root");
    if (!lr || !lr.contains(el)) return false;
    return Boolean(
      el.closest(".group.relative.flex.w-full") ||
        el.closest("[data-datalab-episodes-panel='1']") ||
        el.closest(".dv-groupview"),
    );
  }

  function buttonText(btn) {
    return (btn.textContent || "").replace(/\s+/g, " ").trim();
  }

  function parseEpisodeIndex(label) {
    var m = String(label || "").match(/(?:选择 Episode|Select Episode)\s*(\d+)/i);
    return m ? parseInt(m[1], 10) : null;
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

  function rowIsSoftDeleted(rowEl) {
    if (!rowEl) return false;
    var cls = String(rowEl.className || "");
    if (cls.indexOf("opacity-60") >= 0) return true;
    return Boolean(
      rowEl.querySelector(
        'button[title*="恢复 Episode"], button[title*="Restore episode"], button[title*="Restore Episode"]',
      ),
    );
  }

  function collectSoftDeletedIndices() {
    var root = episodesPanelRoot();
    var out = [];
    var rows = root.querySelectorAll(".group.relative.flex.w-full.min-w-0.flex-col");
    for (var i = 0; i < rows.length; i++) {
      if (!rowIsSoftDeleted(rows[i])) continue;
      var idx = episodeIndexFromRow(rows[i]);
      if (idx != null) out.push(idx);
    }
    return out.sort(function (a, b) {
      return a - b;
    });
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
    if (!btn) return false;
    var label = buttonText(btn);
    if (/^(批量删除|Delete selected|選択を削除)$/i.test(label)) return true;
    var title = (btn.getAttribute("title") || "").trim();
    return /bulkDelete|批量删除|Delete selected/i.test(title);
  }

  function blockEvent(e) {
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
  }

  function handleDeletePointer(e) {
    var btn = e.target && e.target.closest ? e.target.closest("button") : null;
    if (!btn || !isInEpisodeSidebar(btn)) return;
    if (isRowDeleteButton(btn) || isBulkDeleteButton(btn)) {
      blockEvent(e);
    }
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
    }, 5000);
  }

  function confirmDelete(indices) {
    var list = indices
      .map(function (n) {
        return "#" + n;
      })
      .join(", ");
    if (isZh()) {
      return g.confirm(
        "从采集站永久删除 " +
          list +
          "？\n\n" +
          "· 仅删 collection（L2），raw/segments 保留。\n" +
          "· 成功：刷新后该条从列表消失（不会一直灰色）。",
      );
    }
    return g.confirm(
      "Permanently remove " +
        list +
        " from the station?\n\n" +
        "L2 only; raw kept.\n" +
        "Success: row disappears after reload (not gray).",
    );
  }

  function runDelete(indices, opts) {
    opts = opts || {};
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
    if (!opts.skipConfirm && !confirmDelete(indices)) {
      g.location.reload();
      return;
    }

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
          showToast(
            (isZh() ? "删除失败：" : "Delete failed: ") +
              detail +
              (isZh() ? "（将刷新）" : " (reload)"),
            "error",
          );
          setTimeout(function () {
            g.location.reload();
          }, 1200);
          return;
        }
        var list = indices
          .map(function (n) {
            return "#" + n;
          })
          .join(", ");
        showToast(
          isZh()
            ? "删除成功：" + list + " 已移除，正在刷新…"
            : "Removed " + list + ". Reloading…",
          "ok",
        );
        setTimeout(function () {
          g.location.reload();
        }, 500);
      })
      .catch(function () {
        showToast(isZh() ? "网络错误（将刷新）" : "Network error (reload)", "error");
        setTimeout(function () {
          g.location.reload();
        }, 1200);
      })
      .finally(function () {
        busy = false;
      });
  }

  function maybePromptSoftDeletedRepair() {
    if (repairPrompted || busy) return;
    var indices = collectSoftDeletedIndices();
    if (!indices.length) return;
    repairPrompted = true;
    var list = indices
      .map(function (n) {
        return "#" + n;
      })
      .join(", ");
    var ok = isZh()
      ? g.confirm(
          list +
            " 目前只是 viewer 灰色预览，采集站上仍在。\n\n是否现在永久删除并从列表中移除？",
        )
      : g.confirm(
          list +
            " are gray preview marks only; still on server.\n\nPermanently delete and remove from list?",
        );
    if (ok) {
      runDelete(indices, { skipConfirm: true });
    } else {
      repairPrompted = false;
    }
  }

  function onClickCapture(e) {
    var btn = e.target && e.target.closest ? e.target.closest("button") : null;
    if (!btn || !isInEpisodeSidebar(btn)) return;

    removeLegacyToolbarDelete();

    if (isBulkDeleteButton(btn)) {
      blockEvent(e);
      runDelete(collectBulkSelectedIndices());
      return;
    }

    if (isRowDeleteButton(btn)) {
      blockEvent(e);
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
    g.addEventListener("pointerdown", handleDeletePointer, true);
    g.addEventListener("mousedown", handleDeletePointer, true);
    g.addEventListener("click", onClickCapture, true);
    if (!g.__DATALAB_OVERLAY_EPISODE_DELETE_OBSERVER__ && document.body) {
      g.__DATALAB_OVERLAY_EPISODE_DELETE_OBSERVER__ = new MutationObserver(function () {
        removeLegacyToolbarDelete();
        maybePromptSoftDeletedRepair();
      });
      g.__DATALAB_OVERLAY_EPISODE_DELETE_OBSERVER__.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["class"],
      });
    }
    setTimeout(maybePromptSoftDeletedRepair, 800);
    setTimeout(maybePromptSoftDeletedRepair, 2500);
  }

  installHooks();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", installHooks);
  }
})();
