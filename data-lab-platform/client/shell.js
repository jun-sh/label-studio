/**
 * Data Lab zero-intrusion shell — sidebar menu (v0.0.5-compatible).
 * Only inserts 数据/采集; does not move React native items (keeps clicks working).
 */
(function datalabShell() {
  "use strict";

  var global =
    typeof globalThis !== "undefined" ? globalThis : typeof window !== "undefined" ? window : null;
  if (!global) return;

  var PREFERRED_COLOR_SCHEME_KEY = "preferred-color-scheme";
  var LIGHT_THEME = "Light";

  function lockLightTheme() {
    try {
      global.localStorage.setItem(PREFERRED_COLOR_SCHEME_KEY, LIGHT_THEME);
    } catch (_err) {
      /* ignore */
    }
    if (document.documentElement) {
      document.documentElement.setAttribute("data-color-scheme", "light");
    }
  }

  lockLightTheme();

  var SHELL_VERSION = "21";

  if (global.__DATALAB_SHELL_BOOTED__ === SHELL_VERSION) {
    return;
  }
  global.__DATALAB_SHELL_BOOTED__ = SHELL_VERSION;
  global.__DATALAB_SHELL_VERSION__ = SHELL_VERSION;

  var SEL = {
    menu: 'ul[class*="main-menu"]',
    item: 'a[class*="main-menu__item"]',
    icon: 'span[class*="main-menu__item-icon"]',
    spacer: 'li[class*="main-menu__spacer"]',
    trigger: ".main-menu-trigger",
  };

  var MENU_IDS = {
    data: "datalab-menu-data",
    collection: "datalab-menu-collection",
  };

  /** Inline SVG (Humansignal-style, currentColor) */
  var ICONS = {
    data:
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="M19.195 14.0549L20.5441 14.8148C20.6825 14.8932 20.7976 15.0069 20.8777 15.1444C20.9578 15.2818 21 15.4381 21 15.5971C21 15.7562 20.9578 15.9124 20.8777 16.0499C20.7976 16.1873 20.6825 16.3011 20.5441 16.3795L12.8994 20.759C12.6259 20.9169 12.3157 21 12 21C11.6843 21 11.3741 20.9169 11.1006 20.759L3.45594 16.3795C3.31752 16.3011 3.2024 16.1873 3.1223 16.0499C3.0422 15.9124 3 15.7562 3 15.5971C3 15.4381 3.0422 15.2818 3.1223 15.1444C3.2024 15.0069 3.31752 14.8932 3.45594 14.8148L4.805 14.0549M12.8994 13.5648C12.6259 13.7226 12.3157 13.8057 12 13.8057C11.6843 13.8057 11.3741 13.7226 11.1006 13.5648L3.45594 9.18524C3.31752 9.1068 3.2024 8.99306 3.1223 8.85561C3.0422 8.71817 3 8.56194 3 8.40286C3 8.24378 3.0422 8.08755 3.1223 7.9501C3.2024 7.81266 3.31752 7.69892 3.45594 7.62048L11.1006 3.24096C11.3741 3.08311 11.6843 3 12 3C12.3157 3 12.6259 3.08311 12.8994 3.24096L20.5441 7.62048C20.6825 7.69892 20.7976 7.81266 20.8777 7.9501C20.9578 8.08755 21 8.24378 21 8.40286C21 8.56194 20.9578 8.71817 20.8777 8.85561C20.7976 8.99306 20.6825 9.1068 20.5441 9.18524L12.8994 13.5648Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    collection:
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="M12 4V14.6667M12 4L16.4444 8.44444M12 4L7.55556 8.44444M20 14.6667V18.2222C20 18.6937 19.8127 19.1459 19.4793 19.4793C19.1459 19.8127 18.6937 20 18.2222 20H5.77778C5.30628 20 4.8541 19.8127 4.5207 19.4793C4.1873 19.1459 4 18.6937 4 18.2222V14.6667" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    annotation:
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="M15 16L11 20H21V16H15ZM12.06 7.19L3 16.25V20H6.75L15.81 10.94L12.06 7.19ZM5.92 18H5V17.08L12.06 10L13 10.94L5.92 18ZM18.71 8.04C19.1 7.65 19.1 7 18.71 6.63L16.37 4.29C16.17 4.09 15.92 4 15.66 4C15.41 4 15.15 4.1 14.96 4.29L13.13 6.12L16.88 9.87L18.71 8.04Z" fill="currentColor"/></svg>',
  };

  var INJECT_SPECS = [
    { key: "data", href: "/data", match: /^\/data$/, label: "数据" },
    { key: "collection", href: "/collection", match: /^\/collection/, label: "采集" },
  ];

  var state = {
    menuEl: null,
    menuObserver: null,
    menuPending: false,
    navGuardBound: false,
    legacyBackObserver: null,
  };

  function normalizePath(path) {
    if (!path) return "/";
    var p = String(path).split("?")[0].split("#")[0];
    if (p.length > 1 && p.endsWith("/")) p = p.slice(0, -1);
    return p || "/";
  }

  function currentPath() {
    var base = "";
    if (global.APP_SETTINGS && global.APP_SETTINGS.hostname) {
      try {
        base = new URL(global.APP_SETTINGS.hostname, global.location.origin).pathname;
      } catch (_err) {
        base = "";
      }
    }
    base = (base || "").replace(/\/$/, "");
    var path = normalizePath(global.location.pathname);
    if (base && path.indexOf(base) === 0) {
      path = path.slice(base.length) || "/";
      if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
    }
    return path;
  }

  function navigateTo(route) {
    global.location.assign(route);
  }

  function collectionListPath() {
    var base = "";
    if (global.APP_SETTINGS && global.APP_SETTINGS.hostname) {
      try {
        base = new URL(global.APP_SETTINGS.hostname, global.location.origin).pathname;
      } catch (_err) {
        base = "";
      }
    }
    base = (base || "").replace(/\/$/, "");
    return base + "/collection";
  }

  /** Strip ?station= when leaving collection viz (full navigation; same pathname keeps search in SPA). */
  function goToCollectionList() {
    var target = collectionListPath();
    try {
      var url = new URL(target, global.location.origin);
      global.location.replace(url.pathname);
      return;
    } catch (_err) {
      /* ignore */
    }
    global.location.replace(target);
  }

  global.__datalabGoCollectionList = goToCollectionList;

  function isHomeMenuLink(link) {
    if (!link) return false;
    if (link.getAttribute("data-datalab-home-link") === "1") return true;
    if (linkPathname(link.getAttribute("href") || "") === "/") return true;
    return linkPathname(link.getAttribute("href") || "") === "/";
  }

  function isCollectionMenuLink(link) {
    if (!link) return false;
    var li = link.closest("li");
    if (li && li.id === MENU_IDS.collection) return true;
    return linkPathname(link.getAttribute("href") || "") === "/collection";
  }

  function purgeLegacyBackButton() {
    document.querySelectorAll("#datalab-collection-viz-back, .datalab-collection-viz-back").forEach(function (node) {
      node.remove();
    });
  }

  function bindLegacyBackButtonGuard() {
    purgeLegacyBackButton();
    if (state.legacyBackObserver || !document.body) return;
    state.legacyBackObserver = new MutationObserver(purgeLegacyBackButton);
    state.legacyBackObserver.observe(document.body, { childList: true, subtree: true });
  }

  function menuContainsLink(link) {
    if (!link) return false;
    if (link.closest(SEL.menu)) return true;
    return Boolean(link.closest('[class*="sidebar"]'));
  }

  /** Capture-phase guard: React NavLink on /collection keeps ?station= without a hard navigation. */
  function bindCollectionNavGuard() {
    if (state.navGuardBound) return;
    state.navGuardBound = true;
    document.addEventListener(
      "click",
      function (e) {
        var link = e.target && e.target.closest ? e.target.closest(SEL.item) : null;
        if (!link) link = e.target && e.target.closest ? e.target.closest("a[href]") : null;
        if (!link || !menuContainsLink(link)) return;

        var destPath = linkPathname(link.getAttribute("href") || "");
        var onCollection =
          isOnCollectionRoute() || isCollectionChromePage() || collectionLayersPresent();

        if (onCollection && destPath.indexOf("/collection") !== 0) {
          e.preventDefault();
          e.stopImmediatePropagation();
          leaveCollectionNavigate(link.getAttribute("href"));
          return;
        }

        if (!isCollectionVizMode()) return;
        if (!isCollectionMenuLink(link)) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        goToCollectionList();
      },
      true,
    );
  }

  function linkPathname(href) {
    if (!href) return "";
    try {
      return new URL(href, global.location.origin).pathname.replace(/\/$/, "") || "/";
    } catch (_err) {
      return href.split("?")[0].split("#")[0].replace(/\/$/, "") || "/";
    }
  }

  function isDataHref(href) {
    var p = linkPathname(href);
    return p === "/data" || p.endsWith("/data");
  }

  function isGlobalNavHref(href) {
    var p = linkPathname(href);
    return p === "/projects" || p === "/organization";
  }

  /** Project card "..." menus also use main-menu but are not the sidebar. */
  function isContextualMenu(menu) {
    if (!menu) return false;
    var cls = String(menu.className || "");
    if (cls.indexOf("main-menu_contextual") >= 0 || cls.indexOf("main-menu--contextual") >= 0) {
      return true;
    }
    if (menu.closest && menu.closest(".project-card")) return true;
    return false;
  }

  function menuHasNavItems(menu) {
    if (isContextualMenu(menu)) return false;
    var links = menu.querySelectorAll("a[href]");
    var j;
    for (j = 0; j < links.length; j++) {
      if (isGlobalNavHref(links[j].getAttribute("href"))) return true;
    }
    return false;
  }

  function findSidebarMenu() {
    var menus = document.querySelectorAll(SEL.menu);
    var i;
    for (i = 0; i < menus.length; i++) {
      if (menuHasNavItems(menus[i])) return menus[i];
    }
    return null;
  }

  function purgeMisplacedInjectedMenus() {
    document.querySelectorAll(SEL.menu).forEach(function (menu) {
      if (!isContextualMenu(menu) && menuHasNavItems(menu)) return;
      menu.querySelectorAll('[data-datalab-managed="1"]').forEach(function (node) {
        var li = node.tagName === "LI" ? node : node.closest("li");
        if (li && menu.contains(li) && li.parentNode) li.parentNode.removeChild(li);
      });
    });
  }

  function findNativeLi(menu, spec) {
    var items = menu.querySelectorAll(SEL.item);
    var j;
    for (j = 0; j < items.length; j++) {
      var a = items[j];
      var href = a.getAttribute("href") || "";
      var path = linkPathname(href);
      if (spec.isHome) {
        if (a.getAttribute("data-datalab-home-link") === "1") return a.closest("li");
        if (path === "/") return a.closest("li");
        continue;
      }
      if (spec.href) {
        if (spec.href === "/projects" || spec.href === "/organization") {
          if (path === spec.href) return a.closest("li");
          continue;
        }
        if (path.indexOf(spec.href) >= 0) return a.closest("li");
      }
    }
    return null;
  }

  function findHomeLi(menu) {
    var marked = menu.querySelector('[data-datalab-home-link="1"]');
    if (marked) return marked.closest("li");
    var li = findNativeLi(menu, { isHome: true });
    if (!li) return null;
    var link = li.querySelector(SEL.item);
    if (link) link.setAttribute("data-datalab-home-link", "1");
    return li;
  }

  function pageLang() {
    var lang = (document.documentElement && document.documentElement.lang) || "";
    try {
      var q = new URLSearchParams(global.location.search).get("lang");
      if (q) lang = q;
    } catch (_err) {
      /* ignore */
    }
    return String(lang).toLowerCase().indexOf("zh") === 0 ? "zh" : "en";
  }

  function isOnCollectionRoute() {
    return currentPath().indexOf("/collection") === 0;
  }

  function isCollectionVizMode() {
    if (document.body && document.body.dataset.datalabCollectionViz === "1") return true;
    if (!isOnCollectionRoute()) return false;
    try {
      return Boolean(new URLSearchParams(global.location.search).get("station"));
    } catch (_err2) {
      return false;
    }
  }

  var COLLECTION_LAYER_IDS = [
    "datalab-collection-layer",
    "datalab-station-layer",
    "datalab-stream-loading-layer",
  ];

  function collectionLayersPresent() {
    var i;
    for (i = 0; i < COLLECTION_LAYER_IDS.length; i++) {
      if (document.getElementById(COLLECTION_LAYER_IDS[i])) return true;
    }
    return false;
  }

  /** Django collection overlays survive React Router navigations; remove when route changes. */
  function teardownCollectionChrome() {
    var i;
    for (i = 0; i < COLLECTION_LAYER_IDS.length; i++) {
      var layer = document.getElementById(COLLECTION_LAYER_IDS[i]);
      if (layer) layer.remove();
    }
    var legacy = document.getElementById("datalab-viz-layer");
    if (legacy && !legacy.querySelector("#datalab-station-shell")) {
      legacy.remove();
    }
    if (document.body) {
      delete document.body.dataset.datalabCollectionPage;
      delete document.body.dataset.datalabCollectionViz;
    }
    document.querySelectorAll(".content-wrapper__content iframe").forEach(function (node) {
      node.style.display = "";
    });
    try {
      delete global.__datalabCollectionSetDatasetReady;
      delete global.__datalabCollectionApplyStation;
    } catch (_err) {
      /* ignore */
    }
    purgeLegacyBackButton();
  }

  function maybeTeardownCollectionChrome() {
    if (isOnCollectionRoute()) return;
    if (!isCollectionChromePage() && !collectionLayersPresent()) return;
    teardownCollectionChrome();
  }

  function leaveCollectionNavigate(href) {
    teardownCollectionChrome();
    var target = href || "/";
    try {
      target = new URL(target, global.location.origin).href;
    } catch (_err) {
      /* keep target */
    }
    global.location.assign(target);
  }

  /** Remove bare #datalab-viz-layer from legacy React CollectionPage (Django uses #datalab-station-layer). */
  function purgeLegacyReactCollectionViz() {
    if (!isCollectionVizMode()) return;
    var rogue = document.getElementById("datalab-viz-layer");
    if (!rogue || rogue.querySelector("#datalab-station-shell")) return;
    try {
      rogue.remove();
    } catch (_err) {
      /* ignore */
    }
  }

  function getLinkTextLabel(link) {
    if (!link) return "";
    var nodes = link.childNodes;
    var text = "";
    var k;
    for (k = 0; k < nodes.length; k++) {
      if (nodes[k].nodeType === Node.TEXT_NODE) text += nodes[k].textContent || "";
    }
    return text.trim();
  }

  function patchCollectionMenuItem(menu) {
    var collLi = document.getElementById(MENU_IDS.collection);
    if (!collLi || !menu.contains(collLi)) return;
    var link = collLi.querySelector(SEL.item);
    if (!link) return;
    if (isCollectionVizMode()) {
      link.setAttribute("href", collectionListPath());
    }
  }

  function patchHomeMenuItem(menu) {
    var homeLi = findHomeLi(menu);
    if (!homeLi) return;
    var link = homeLi.querySelector(SEL.item);
    if (!link) return;

    if (!link.dataset.datalabHomeOrigHref) {
      link.dataset.datalabHomeOrigHref = link.getAttribute("href") || "/";
      link.dataset.datalabHomeOrigTitle = link.getAttribute("title") || "";
      link.dataset.datalabHomeOrigLabel = getLinkTextLabel(link) || "";
    }

    link.setAttribute("href", link.dataset.datalabHomeOrigHref || "/");
    if (link.dataset.datalabHomeOrigTitle) {
      link.setAttribute("title", link.dataset.datalabHomeOrigTitle);
    } else {
      link.removeAttribute("title");
    }
    link.removeAttribute("aria-label");
    if (link.dataset.datalabHomeOrigLabel) {
      setLinkLabel(link, link.dataset.datalabHomeOrigLabel);
    }
  }

  function getStyleTemplate(menu) {
    return findNativeLi(menu, { href: "/projects" }) || findNativeLi(menu, { href: "/organization" });
  }

  function setLinkLabel(link, label) {
    if (!link || !label) return;
    var nodes = link.childNodes;
    var k;
    for (k = 0; k < nodes.length; k++) {
      if (nodes[k].nodeType === Node.TEXT_NODE && String(nodes[k].textContent || "").trim()) {
        nodes[k].textContent = label;
      }
    }
  }

  function setItemIcon(li, iconHtml) {
    if (!li || !iconHtml) return;
    var icon = li.querySelector(SEL.icon);
    if (icon) icon.innerHTML = iconHtml;
  }

  function patchDocumentTitle() {
    var title = document.title || "";
    if (title.indexOf("项目 |") >= 0) document.title = title.replace(/项目 \|/g, "标注 |");
    if (title.indexOf("项目 ·") >= 0) document.title = title.replace(/项目 ·/g, "标注 |");
  }

  function buildInjectedItem(menu, spec) {
    var templateLink = getStyleTemplate(menu);
    templateLink = templateLink ? templateLink.querySelector(SEL.item) : null;
    var templateIcon = templateLink ? templateLink.querySelector(SEL.icon) : null;

    var li = document.createElement("li");
    li.id = MENU_IDS[spec.key];
    li.setAttribute("data-datalab-managed", "1");

    var link = document.createElement("a");
    link.className = templateLink ? templateLink.className : "ls-main-menu__item";
    link.href = spec.href;
    link.setAttribute("data-external", "");
    link.setAttribute("data-datalab-managed", "1");
    link.setAttribute("data-testid", "datalab-menu-" + spec.key);

    var iconWrap = document.createElement("span");
    iconWrap.className = templateIcon ? templateIcon.className : "ls-main-menu__item-icon";
    iconWrap.innerHTML = ICONS[spec.key] || "";

    link.appendChild(iconWrap);
    link.appendChild(document.createTextNode(spec.label));

    li.appendChild(link);
    return li;
  }

  function purgeNativeDataItems(menu) {
    var links = menu.querySelectorAll("a[href]");
    var toRemove = [];
    var j;
    for (j = 0; j < links.length; j++) {
      if (!isDataHref(links[j].getAttribute("href"))) continue;
      var li = links[j].closest("li");
      if (!li || li.getAttribute("data-datalab-managed") === "1") continue;
      toRemove.push(li);
    }
    for (j = 0; j < toRemove.length; j++) {
      if (toRemove[j].parentNode) toRemove[j].parentNode.removeChild(toRemove[j]);
    }
  }

  function ensureInjectedItem(menu, spec) {
    var li = document.getElementById(MENU_IDS[spec.key]);
    if (!li || !menu.contains(li)) {
      li = buildInjectedItem(menu, spec);
    }
    li.setAttribute("data-datalab-managed", "1");
    setItemIcon(li, ICONS[spec.key]);
    return li;
  }

  function patchProjectsItem(menu) {
    var li = findNativeLi(menu, { href: "/projects" });
    if (!li) return;
    setLinkLabel(li.querySelector(SEL.item), "标注");
    setItemIcon(li, ICONS.annotation);
  }

  function isInjectOrderCorrect(menu) {
    var projectsLi = findNativeLi(menu, { href: "/projects" });
    var dataLi = document.getElementById(MENU_IDS.data);
    var collLi = document.getElementById(MENU_IDS.collection);
    if (!projectsLi || !dataLi || !collLi) return false;
    return projectsLi.previousElementSibling === collLi && collLi.previousElementSibling === dataLi;
  }

  /** Insert 数据、采集 before 标注; do not relocate React menu rows. */
  function placeInjectedItems(menu) {
    var anchor = findNativeLi(menu, { href: "/projects" }) || findNativeLi(menu, { href: "/organization" });
    if (!anchor) return;

    var dataLi = ensureInjectedItem(menu, INJECT_SPECS[0]);
    var collLi = ensureInjectedItem(menu, INJECT_SPECS[1]);

    menu.insertBefore(collLi, anchor);
    menu.insertBefore(dataLi, collLi);
  }

  function syncMenuActiveState(menu) {
    var path = currentPath();

    menu.querySelectorAll('[class*="main-menu__item_active"]').forEach(function (el) {
      el.classList.remove("ls-main-menu__item_active");
      el.classList.remove("main-menu__item_active");
      el.removeAttribute("aria-current");
    });

    function activate(li) {
      if (!li) return;
      var link = li.querySelector(SEL.item);
      if (!link) return;
      link.classList.add("ls-main-menu__item_active");
      link.setAttribute("aria-current", "page");
    }

    if ((path === "/" || path === "") && !isCollectionVizMode()) {
      activate(findHomeLi(menu));
    } else if (path.indexOf("/data") === 0) {
      activate(document.getElementById(MENU_IDS.data));
    } else if (path.indexOf("/collection") === 0) {
      activate(document.getElementById(MENU_IDS.collection));
    } else if (path.indexOf("/projects") === 0) {
      activate(findNativeLi(menu, { href: "/projects" }));
    } else if (path.indexOf("/organization") === 0) {
      activate(findNativeLi(menu, { href: "/organization" }));
    }
  }

  function reconcileMenu() {
    purgeMisplacedInjectedMenus();
    var menu = findSidebarMenu();
    if (!menu) return false;

    purgeNativeDataItems(menu);
    ensureInjectedItem(menu, INJECT_SPECS[0]);
    ensureInjectedItem(menu, INJECT_SPECS[1]);
    if (!isInjectOrderCorrect(menu)) {
      placeInjectedItems(menu);
    }
    patchProjectsItem(menu);
    patchHomeMenuItem(menu);
    patchCollectionMenuItem(menu);
    syncMenuActiveState(menu);
    purgeLegacyReactCollectionViz();

    document.documentElement.setAttribute("data-datalab-shell", SHELL_VERSION);

    if (state.menuEl !== menu) {
      state.menuEl = menu;
      attachMenuObserver(menu);
    }
    return true;
  }

  function scheduleMenuReconcile() {
    if (state.menuPending) return;
    state.menuPending = true;
    global.requestAnimationFrame(function () {
      state.menuPending = false;
      maybeTeardownCollectionChrome();
      reconcileMenu();
    });
  }

  function attachMenuObserver(menu) {
    if (state.menuObserver) {
      state.menuObserver.disconnect();
    }
    state.menuObserver = new MutationObserver(function (mutations) {
      var onlyOurNodes = true;
      var m;
      for (m = 0; m < mutations.length; m++) {
        var nodes = mutations[m].addedNodes;
        var n;
        for (n = 0; n < nodes.length; n++) {
          var node = nodes[n];
          if (node.nodeType !== 1) continue;
          if (node.id === MENU_IDS.data || node.id === MENU_IDS.collection) continue;
          if (node.getAttribute && node.getAttribute("data-datalab-managed") === "1") continue;
          onlyOurNodes = false;
          break;
        }
        if (!onlyOurNodes) break;
      }
      if (!onlyOurNodes) scheduleMenuReconcile();
    });
    state.menuObserver.observe(menu, { childList: true, subtree: false });
  }

  function isCollectionChromePage() {
    if (!document.body) return false;
    return (
      document.body.dataset.datalabCollectionPage === "1" ||
      document.body.dataset.datalabCollectionViz === "1"
    );
  }

  /** Django collection layers sit on body above .app-wrapper; ensure trigger receives clicks. */
  function bindMenubarTriggerPassthrough() {
    document.addEventListener(
      "click",
      function (e) {
        if (!isCollectionChromePage()) return;
        var trigger = e.target && e.target.closest ? e.target.closest(SEL.trigger) : null;
        if (!trigger) return;
        var layer = document.getElementById("datalab-station-layer") || document.getElementById("datalab-collection-layer");
        if (layer && layer.style.pointerEvents !== "none") {
          layer.style.pointerEvents = "none";
        }
      },
      true,
    );
  }

  function bindMenuOpen() {
    document.addEventListener(
      "click",
      function (e) {
        if (e.target && e.target.closest && e.target.closest(SEL.trigger)) {
          global.setTimeout(scheduleMenuReconcile, 0);
          global.setTimeout(scheduleMenuReconcile, 150);
        }
      },
      true,
    );
  }

  function watchForMenu() {
    if (reconcileMenu()) return;
    var bootObserver = new MutationObserver(scheduleMenuReconcile);
    bootObserver.observe(document.documentElement, { childList: true, subtree: true });
  }

  function bindCollectionVizWatcher() {
    global.addEventListener("datalab:menu-reconcile", scheduleMenuReconcile);
    if (!document.body || typeof MutationObserver === "undefined") return;
    var observer = new MutationObserver(function (mutations) {
      var m;
      for (m = 0; m < mutations.length; m++) {
        if (mutations[m].attributeName === "data-datalab-collection-viz") {
          scheduleMenuReconcile();
          return;
        }
      }
    });
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ["data-datalab-collection-viz"],
    });
  }

  function bindHistory() {
    global.addEventListener("popstate", scheduleMenuReconcile);
    var history = global.LSH;
    if (history && typeof history.listen === "function") {
      history.listen(scheduleMenuReconcile);
    }
  }

  function hideThemeToggle() {
    document.querySelectorAll('button[class*="themeToggle"]').forEach(function (btn) {
      btn.style.display = "none";
      btn.setAttribute("data-datalab-chrome", "theme-toggle");
    });
  }

  function applyThemeChrome() {
    lockLightTheme();
    hideThemeToggle();
  }

  function bindThemeChrome() {
    applyThemeChrome();
    if (!document.body || typeof MutationObserver === "undefined") return;
    var observer = new MutationObserver(function () {
      lockLightTheme();
      hideThemeToggle();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function init() {
    patchDocumentTitle();
    bindThemeChrome();
    bindCollectionNavGuard();
    bindLegacyBackButtonGuard();
    bindMenuOpen();
    bindMenubarTriggerPassthrough();
    watchForMenu();
    bindCollectionVizWatcher();
    bindHistory();

    if (isCollectionVizMode()) {
      var ticks = 0;
      var legacyPurgeTimer = global.setInterval(function () {
        purgeLegacyReactCollectionViz();
        ticks += 1;
        if (ticks >= 48) global.clearInterval(legacyPurgeTimer);
      }, 250);
    }

    var pushState = global.history.pushState;
    var replaceState = global.history.replaceState;
    global.history.pushState = function () {
      var r = pushState.apply(this, arguments);
      patchDocumentTitle();
      maybeTeardownCollectionChrome();
      scheduleMenuReconcile();
      return r;
    };
    global.history.replaceState = function () {
      var r = replaceState.apply(this, arguments);
      patchDocumentTitle();
      maybeTeardownCollectionChrome();
      scheduleMenuReconcile();
      return r;
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
