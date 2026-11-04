/**
 * Collection embed: delete selected episodes (L2 only; raw/segments preserved).
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

  var deleteBtn = null;
  var toastEl = null;
  var toastTimer = null;
  var busy = false;
  var tickScheduled = false;
  var tickDebounceTimer = null;
  var TICK_DEBOUNCE_MS = 250;

  var DELETE_ICON_INNER =
    '<polyline points="3 6 5 6 21 6"></polyline>' +
    '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>' +
    '<line x1="10" y1="11" x2="10" y2="17"></line>' +
    '<line x1="14" y1="11" x2="14" y2="17"></line>';

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

  function buttonText(btn) {
    return (btn.textContent || "").replace(/\s+/g, " ").trim();
  }

  function findEpisodeToolbarButton(labelRe) {
    var buttons = document.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      if (btn.closest("[data-datalab-delete-root]")) continue;
      if (btn.closest("[data-datalab-import-modal]")) continue;
      if (labelRe.test(buttonText(btn))) return btn;
    }
    return null;
  }

  function findEpisodeToolbarAnchor() {
    var editBtn = findEpisodeToolbarButton(/^(编辑|Edit)$/i);
    var selectBtn = findEpisodeToolbarButton(/^(多选|Select multiple)$/i);
    if (editBtn && editBtn.parentNode) {
      return { row: editBtn.parentNode, templateBtn: editBtn, insertAfter: editBtn };
    }
    if (selectBtn && selectBtn.parentNode) {
      return { row: selectBtn.parentNode, templateBtn: selectBtn, insertAfter: selectBtn };
    }
    return null;
  }

  function parseEpisodeIndex(label) {
    var m = String(label || "").match(/(?:选择 Episode|Select Episode)\s*(\d+)/i);
    return m ? parseInt(m[1], 10) : null;
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

  function isRowSelected(rowEl) {
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

  function collectSelectedEpisodeIndices() {
    var panel =
      document.querySelector("[data-datalab-episodes-panel='1']") ||
      document.querySelector(".dv-groupview");
    var root = panel || document;
    var out = new Set();

    var nodes = root.querySelectorAll('button[aria-label], [role="button"][aria-label]');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var label = el.getAttribute("aria-label") || "";
      if (!isEpisodeSelectLabel(label)) continue;
      var rowEl = findEpisodeRowEl(el);
      if (!isRowSelected(rowEl)) continue;
      var idx = parseEpisodeIndex(label);
      if (idx != null) out.add(idx);
    }

    if (out.size === 0) {
      var current = root.querySelector(
        'button[aria-current="true"][aria-label*="Episode"], button[aria-current="page"][aria-label*="Episode"]',
      );
      if (current) {
        var curIdx = parseEpisodeIndex(current.getAttribute("aria-label") || "");
        if (curIdx != null) out.add(curIdx);
      }
    }

    return Array.from(out).sort(function (a, b) {
      return a - b;
    });
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

  function setButtonLabel(btn, label) {
    var spans = btn.querySelectorAll("span");
    var i;
    for (i = spans.length - 1; i >= 0; i--) {
      var el = spans[i];
      if (el.querySelector("svg")) continue;
      var text = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (text.length > 0) {
        el.textContent = label;
        return;
      }
    }
    var tail = document.createElement("span");
    tail.textContent = label;
    btn.appendChild(tail);
  }

  function applyDeleteIcon(btn, templateSvg) {
    var svg = btn.querySelector("svg");
    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      btn.insertBefore(svg, btn.firstChild);
    }
    if (templateSvg) {
      ["class", "width", "height", "viewBox", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"].forEach(
        function (attr) {
          var val = templateSvg.getAttribute(attr);
          if (val != null) svg.setAttribute(attr, val);
        },
      );
    }
    if (!svg.getAttribute("viewBox")) svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = DELETE_ICON_INNER;
  }

  function deleteButtonLabel() {
    return isZh() ? "删除" : "Delete";
  }

  function buildDeleteButton(templateBtn) {
    var btn;
    if (templateBtn) {
      btn = templateBtn.cloneNode(true);
      btn.disabled = false;
      btn.removeAttribute("aria-disabled");
      btn.removeAttribute("data-state");
      btn.removeAttribute("aria-pressed");
      btn.setAttribute("data-datalab-delete-trigger", "1");
      btn.setAttribute("type", "button");
      setButtonLabel(btn, deleteButtonLabel());
      applyDeleteIcon(btn, templateBtn.querySelector("svg"));
    } else {
      btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("data-datalab-delete-trigger", "1");
      btn.className = "inline-flex items-center gap-1.5";
      applyDeleteIcon(btn, null);
      setButtonLabel(btn, deleteButtonLabel());
    }
    btn.addEventListener("click", onDeleteClick);
    return btn;
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

  function onDeleteClick(e) {
    e.preventDefault();
    e.stopPropagation();
    if (busy) return;

    var stationId = stationIdFromContext();
    if (!stationId) {
      showToast(isZh() ? "无法识别工位" : "Station id missing", "error");
      return;
    }

    var indices = collectSelectedEpisodeIndices();
    if (indices.length === 0) {
      showToast(
        isZh()
          ? "请先用「多选」勾选要删的条目，或选中当前 episode"
          : "Use multi-select or pick the current episode first",
        "error",
      );
      return;
    }

    if (!confirmDelete(indices)) return;

    busy = true;
    if (deleteBtn) deleteBtn.disabled = true;

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
        if (deleteBtn) deleteBtn.disabled = false;
      });
  }

  function installDeleteButton() {
    var anchor = findEpisodeToolbarAnchor();
    if (!anchor || !anchor.row) return;

    var importBtn = anchor.row.querySelector("[data-datalab-import-trigger]");
    var insertAfter = importBtn || anchor.insertAfter;
    if (!insertAfter) return;

    if (deleteBtn && deleteBtn.isConnected && deleteBtn.previousSibling === insertAfter) {
      return;
    }

    if (deleteBtn && deleteBtn.parentNode) deleteBtn.remove();
    var stale = anchor.row.querySelector("[data-datalab-delete-trigger]");
    if (stale) stale.remove();

    deleteBtn = buildDeleteButton(anchor.templateBtn);
    if (insertAfter.nextSibling) {
      anchor.row.insertBefore(deleteBtn, insertAfter.nextSibling);
    } else {
      anchor.row.appendChild(deleteBtn);
    }
  }

  function tick() {
    installDeleteButton();
  }

  function scheduleTick() {
    if (tickScheduled) return;
    tickScheduled = true;
    if (tickDebounceTimer) clearTimeout(tickDebounceTimer);
    tickDebounceTimer = setTimeout(function () {
      tickScheduled = false;
      tickDebounceTimer = null;
      tick();
    }, TICK_DEBOUNCE_MS);
  }

  function schedule() {
    tick();
    setTimeout(tick, 500);
    setTimeout(tick, 1500);
    if (!g.__DATALAB_OVERLAY_EPISODE_DELETE_OBSERVER__ && document.body) {
      g.__DATALAB_OVERLAY_EPISODE_DELETE_OBSERVER__ = new MutationObserver(function () {
        scheduleTick();
      });
      g.__DATALAB_OVERLAY_EPISODE_DELETE_OBSERVER__.observe(document.body, {
        childList: true,
        subtree: true,
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", schedule);
  } else {
    schedule();
  }
})();
