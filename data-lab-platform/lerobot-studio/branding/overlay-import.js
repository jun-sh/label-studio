/**
 * Collection embed: Episodes toolbar import button (modal on parent page).
 */
(function () {
  "use strict";

  var g = typeof globalThis !== "undefined" ? globalThis : window;
  if (g.__DATALAB_OVERLAY_IMPORT__) return;

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
  g.__DATALAB_OVERLAY_IMPORT__ = true;

  var core = g.DatalabImportCore;
  if (!core) {
    console.warn("[datalab-import] DatalabImportCore missing");
    return;
  }

  var IMPORT_DONE_MSG = "datalab-import-done";
  var importBtn = null;
  var tickScheduled = false;
  var tickDebounceTimer = null;
  var TICK_DEBOUNCE_MS = 250;

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
      if (btn.closest("[data-datalab-import-root]")) continue;
      if (btn.closest("[data-datalab-collection-mode-root]")) continue;
      if (btn.closest("[data-datalab-import-modal]")) continue;
      if (labelRe.test(buttonText(btn))) return btn;
    }
    return null;
  }

  function findEpisodeSearchInput() {
    var inputs = document.querySelectorAll('input[type="search"], input[type="text"]');
    for (var i = 0; i < inputs.length; i++) {
      var input = inputs[i];
      var ph = (input.getAttribute("placeholder") || "").toLowerCase();
      if (ph.indexOf("search") >= 0 || ph.indexOf("搜索") >= 0) return input;
    }
    return null;
  }

  function findEpisodeToolbarAnchor() {
    var selectBtn = findEpisodeToolbarButton(/^(多选|Select multiple)$/i);
    var editBtn = findEpisodeToolbarButton(/^(编辑|Edit)$/i);
    if (editBtn && editBtn.parentNode) {
      return { row: editBtn.parentNode, editBtn: editBtn, insertAfter: editBtn };
    }
    if (selectBtn && selectBtn.parentNode) {
      return { row: selectBtn.parentNode, editBtn: selectBtn, insertAfter: selectBtn };
    }
    var search = findEpisodeSearchInput();
    if (search && search.parentNode) {
      var row = search.parentNode;
      return { row: row, editBtn: null, insertAfter: search };
    }
    return null;
  }

  function importButtonLabel() {
    return core.pageLang() === "zh" ? "导入" : "Import";
  }

  var DEFAULT_EPISODES_PANEL_PX = 350;
  /** Upstream React resizable panel ships at 256px; only auto-bump from this range. */
  var UPSTREAM_DEFAULT_WIDTH_PX = 256;
  var WIDTH_BOOTSTRAP_TOLERANCE_PX = 32;

  /** Lucide-style upload icon (stroke matches sibling toolbar SVGs). */
  var IMPORT_ICON_INNER =
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>' +
    '<polyline points="17 8 12 3 7 8"></polyline>' +
    '<line x1="12" y1="3" x2="12" y2="15"></line>';

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
    for (i = 0; i < btn.childNodes.length; i++) {
      var node = btn.childNodes[i];
      if (node.nodeType === 3 && String(node.textContent || "").trim()) {
        node.textContent = label;
        return;
      }
    }
    var tail = document.createElement("span");
    tail.textContent = label;
    btn.appendChild(tail);
  }

  function applyImportIcon(btn, templateSvg) {
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
    if (!svg.getAttribute("width")) svg.setAttribute("width", "24");
    if (!svg.getAttribute("height")) svg.setAttribute("height", "24");
    if (!svg.getAttribute("stroke")) svg.setAttribute("stroke", "currentColor");
    if (!svg.getAttribute("fill")) svg.setAttribute("fill", "none");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = IMPORT_ICON_INNER;
  }

  function buildImportButton(templateBtn, stationId) {
    var btn;
    if (templateBtn) {
      btn = templateBtn.cloneNode(true);
      btn.disabled = false;
      btn.removeAttribute("aria-disabled");
      btn.removeAttribute("data-state");
      btn.removeAttribute("aria-pressed");
      btn.setAttribute("data-datalab-import-trigger", "1");
      btn.setAttribute("type", "button");
      setButtonLabel(btn, importButtonLabel());
      applyImportIcon(btn, templateBtn.querySelector("svg"));
    } else {
      btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("data-datalab-import-trigger", "1");
      btn.className = "inline-flex items-center gap-1.5";
      applyImportIcon(btn, null);
      setButtonLabel(btn, importButtonLabel());
    }
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      openModal(stationId);
    });
    return btn;
  }

  function findEpisodesSidebar(anchor) {
    if (!anchor || !anchor.editBtn) return null;
    var el = anchor.editBtn;
    var withInlineWidth = null;
    var flexChild = null;
    while (el && el !== document.body) {
      var sw = el.style && el.style.width ? String(el.style.width).trim() : "";
      if (/^\d+(\.\d+)?px$/.test(sw)) {
        var w = parseFloat(sw);
        if (w >= 150 && w <= 700) withInlineWidth = el;
      }
      var parent = el.parentElement;
      if (parent) {
        var pcls = typeof parent.className === "string" ? parent.className : "";
        if (pcls.indexOf("flex-1") >= 0 && pcls.indexOf("overflow-hidden") >= 0) {
          if (parent.firstElementChild === el) flexChild = el;
        }
      }
      el = el.parentElement;
    }
    return withInlineWidth || flexChild;
  }

  function bootstrapSidebarWidthOnce(sidebar) {
    if (!sidebar) return;
    sidebar.setAttribute("data-datalab-episodes-sidebar", "1");
    if (sidebar.dataset.datalabEpisodesWidthBootstrapped === "1") return;

    var rect = sidebar.getBoundingClientRect();
    var inlineW = parseFloat(String(sidebar.style.width || "").replace("px", "")) || 0;
    var w = rect.width > 0 ? rect.width : inlineW;
    var maxDefault =
      UPSTREAM_DEFAULT_WIDTH_PX + WIDTH_BOOTSTRAP_TOLERANCE_PX;

    if (w > 0 && w <= maxDefault) {
      sidebar.style.width = DEFAULT_EPISODES_PANEL_PX + "px";
    }
    sidebar.dataset.datalabEpisodesWidthBootstrapped = "1";
  }

  function markEpisodesPanel(anchor) {
    if (!anchor || !anchor.editBtn) return;
    var group = anchor.editBtn.closest(".dv-groupview");
    if (group) group.setAttribute("data-datalab-episodes-panel", "1");
    if (anchor.row) anchor.row.setAttribute("data-datalab-episodes-toolbar", "1");

    bootstrapSidebarWidthOnce(findEpisodesSidebar(anchor));
  }

  function resolveImportCore() {
    try {
      if (g.parent !== g && g.parent.DatalabImportCore && g.parent.DatalabImportCore.openModal) {
        return g.parent.DatalabImportCore;
      }
    } catch (e) {
      /* ignore */
    }
    return core && core.openModal ? core : null;
  }

  function openModal(stationId) {
    var hostCore = resolveImportCore();
    if (!hostCore) {
      console.warn("[datalab-import] DatalabImportCore.openModal unavailable");
      return;
    }
    hostCore.openModal(stationId, {
      onSuccess: function () {
        notifyImportDone(stationId);
      },
    });
  }

  function notifyImportDone(stationId) {
    try {
      if (g.parent !== g) {
        g.parent.postMessage(
          { type: IMPORT_DONE_MSG, stationId: stationId },
          g.location.origin,
        );
      }
    } catch (e) {
      /* ignore */
    }
  }

  function findEpisodesSidebar(anchor) {
    if (!importBtn || !importBtn.isConnected) return false;
    if (!anchor || !anchor.row) return false;
    if (importBtn.parentNode !== anchor.row) return false;
    if (anchor.insertAfter && importBtn.previousSibling === anchor.insertAfter) return true;
    return anchor.row.contains(importBtn);
  }

  function installImportButton() {
    var stationId = stationIdFromContext();
    if (!stationId) return;

    var anchor = findEpisodeToolbarAnchor();
    if (!anchor || !anchor.row) return;

    if (importBtn && importBtn.isConnected && isImportButtonPlaced(anchor)) return;

    if (importBtn && importBtn.parentNode) importBtn.remove();

    var stale = anchor.row.querySelector("[data-datalab-import-trigger]");
    if (stale) stale.remove();

    var templateBtn = anchor.editBtn || findEpisodeToolbarButton(/^(多选|Select multiple)$/i);
    importBtn = buildImportButton(templateBtn, stationId);
    markEpisodesPanel(anchor);

    if (anchor.insertAfter && anchor.insertAfter.nextSibling) {
      anchor.row.insertBefore(importBtn, anchor.insertAfter.nextSibling);
    } else if (anchor.insertAfter) {
      anchor.row.appendChild(importBtn);
    } else {
      anchor.row.appendChild(importBtn);
    }
  }

  function tick() {
    installImportButton();
    var anchor = findEpisodeToolbarAnchor();
    if (anchor) markEpisodesPanel(anchor);
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
    setTimeout(tick, 3000);
    if (!g.__DATALAB_OVERLAY_IMPORT_OBSERVER__ && document.body) {
      g.__DATALAB_OVERLAY_IMPORT_OBSERVER__ = new MutationObserver(function () {
        scheduleTick();
      });
      g.__DATALAB_OVERLAY_IMPORT_OBSERVER__.observe(document.body, {
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
