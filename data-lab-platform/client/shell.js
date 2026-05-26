/**
 * Data Lab zero-intrusion shell for Label Studio.
 * - Injects left sidebar menu item「数据」
 * - Routes /lerobot-data to in-app iframe (LeRobot at /lerobot/)
 *
 * Host is mounted on .content-wrapper__body (outside React's content tree)
 * so React re-renders do not remove the iframe.
 */
(function datalabShell(global) {
  "use strict";

  var SCRIPT = document.currentScript;
  var CONFIG = {
    route: normalizePath((SCRIPT && SCRIPT.getAttribute("data-route")) || "/lerobot-data"),
    iframeSrc:
      (SCRIPT && SCRIPT.getAttribute("data-iframe-src")) ||
      "/lerobot/?url=sample%3A%2F%2Fsensexperience_ego",
    menuLabel: (SCRIPT && SCRIPT.getAttribute("data-menu-label")) || "数据",
    menuIconTestId: "datalab-lerobot-menu",
  };

  var MENU_ITEM_ID = "datalab-menu-lerobot-data";
  var HOST_ID = "datalab-lerobot-host";

  var state = {
    menuItem: null,
    host: null,
    iframe: null,
    mountParent: null,
    layoutScheduled: false,
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

  function isLerobotRoute() {
    return false;
  }

  function getHistory() {
    return global.LSH || null;
  }

  function navigateTo(route) {
    global.location.assign(route);
  }

  function findSidebarMenu() {
    return document.querySelector(".sidebar .main-menu") || document.querySelector(".main-menu");
  }

  function findContentArea() {
    return document.querySelector(".content-wrapper__content");
  }

  function findMountParent() {
    return document.querySelector(".content-wrapper__body");
  }

  function scheduleSync() {
    global.requestAnimationFrame(function () {
      syncRoute();
      layoutHost();
    });
  }

  function createMenuIcon() {
    var wrap = document.createElement("span");
    wrap.className = "main-menu__item-icon";
    wrap.innerHTML =
      '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
      '<ellipse cx="6" cy="14" rx="2.5" ry="2" fill="currentColor" fill-opacity="0.45"/>' +
      '<ellipse cx="12" cy="14" rx="2.5" ry="2" fill="currentColor" fill-opacity="0.45"/>' +
      '<ellipse cx="18" cy="14" rx="2.5" ry="2" fill="currentColor" fill-opacity="0.45"/>' +
      '<path fill-rule="evenodd" clip-rule="evenodd" d="M4 5.5C4 4.12 5.12 3 6.5 3h11C18.88 3 20 4.12 20 5.5v5c0 1.38-1.12 2.5-2.5 2.5h-11C5.12 13 4 11.88 4 10.5v-5Zm2.5-.5a.5.5 0 0 0-.5.5v5a.5.5 0 0 0 .5.5h11a.5.5 0 0 0 .5-.5v-5a.5.5 0 0 0-.5-.5h-11Z" fill="currentColor"/>' +
      '<path d="M3 17.25c0-.69.56-1.25 1.25-1.25h15.5c.69 0 1.25.56 1.25 1.25v.5c0 .69-.56 1.25-1.25 1.25H4.25C3.56 19 3 18.44 3 17.75v-.5Z" fill="currentColor" fill-opacity="0.55"/>' +
      "</svg>";
    return wrap;
  }

  function setMenuActive(active) {
    if (!state.menuItem) return;
    var link = state.menuItem.querySelector("a, span");
    if (!link) return;
    if (active) {
      link.classList.add("main-menu__item_active");
      link.setAttribute("aria-current", "page");
    } else {
      link.classList.remove("main-menu__item_active");
      link.removeAttribute("aria-current");
    }
  }

  function clearOtherMenuActive() {
    var menu = findSidebarMenu();
    if (!menu) return;
    menu.querySelectorAll(".main-menu__item_active").forEach(function (el) {
      if (state.menuItem && state.menuItem.contains(el)) return;
      el.classList.remove("main-menu__item_active");
    });
  }

  function injectMenuItem() {
    if (document.getElementById(MENU_ITEM_ID)) {
      state.menuItem = document.getElementById(MENU_ITEM_ID);
      return;
    }
    var menu = findSidebarMenu();
    if (!menu) return;

    var li = document.createElement("li");
    li.id = MENU_ITEM_ID;

    var link = document.createElement("a");
    link.className = "main-menu__item";
    link.href = CONFIG.route;
    link.setAttribute("data-external", "");
    link.setAttribute("data-testid", CONFIG.menuIconTestId);
    link.appendChild(createMenuIcon());
    link.appendChild(document.createTextNode(CONFIG.menuLabel));

    link.addEventListener("click", function (event) {
      event.preventDefault();
      if (currentPath() !== CONFIG.route) {
        navigateTo(CONFIG.route);
      } else {
        scheduleSync();
      }
    });

    li.appendChild(link);

    var orgItem = menu.querySelector('a[href*="/organization"]');
    var projectsItem = menu.querySelector('a[href*="/projects"]');
    var anchor = orgItem ? orgItem.closest("li") : projectsItem ? projectsItem.closest("li") : null;

    if (anchor && anchor.nextSibling) {
      menu.insertBefore(li, anchor.nextSibling);
    } else {
      var spacer = menu.querySelector(".main-menu__spacer");
      if (spacer) {
        menu.insertBefore(li, spacer);
      } else {
        menu.appendChild(li);
      }
    }

    state.menuItem = li;
    setMenuActive(isLerobotRoute());
  }

  function ensureHost() {
    var mountParent = findMountParent();
    var content = findContentArea();
    if (!mountParent || !content) return null;

    state.mountParent = mountParent;

    if (!state.host || !state.host.isConnected) {
      if (state.host && state.host.parentNode) {
        state.host.parentNode.removeChild(state.host);
      }

      var host = document.createElement("div");
      host.id = HOST_ID;
      host.className = "datalab-lerobot-host";
      host.setAttribute("role", "region");
      host.setAttribute("aria-label", CONFIG.menuLabel);

      var iframe = document.createElement("iframe");
      iframe.className = "datalab-lerobot-host__frame";
      iframe.title = "LeRobot Dataset Visualizer";
      iframe.setAttribute("loading", "eager");
      iframe.setAttribute("allow", "fullscreen");
      iframe.src = CONFIG.iframeSrc;

      host.appendChild(iframe);
      mountParent.appendChild(host);

      state.host = host;
      state.iframe = iframe;
    }

    return state.host;
  }

  function layoutHost() {
    var host = state.host;
    var content = findContentArea();
    var mountParent = state.mountParent || findMountParent();
    if (!host || !content || !mountParent || !host.classList.contains("datalab-lerobot-host--visible")) {
      return;
    }

    var contentRect = content.getBoundingClientRect();
    var parentRect = mountParent.getBoundingClientRect();

    host.style.top = contentRect.top - parentRect.top + "px";
    host.style.left = contentRect.left - parentRect.left + "px";
    host.style.width = contentRect.width + "px";
    host.style.height = contentRect.height + "px";
  }

  function scheduleLayout() {
    if (state.layoutScheduled) return;
    state.layoutScheduled = true;
    global.requestAnimationFrame(function () {
      state.layoutScheduled = false;
      layoutHost();
    });
  }

  function readColorScheme() {
    var scheme = document.documentElement.getAttribute("data-color-scheme");
    if (scheme) return scheme.toLowerCase();
    var stored = global.localStorage && global.localStorage.getItem("preferred-color-scheme");
    if (stored && stored !== "Auto") return stored.toLowerCase();
    if (global.matchMedia && global.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
    return "light";
  }

  function syncThemeToIframe() {
    if (!state.iframe || !state.iframe.contentWindow) return;
    try {
      state.iframe.contentWindow.postMessage(
        {
          type: "datalab:theme",
          scheme: readColorScheme(),
        },
        global.location.origin
      );
    } catch (_err) {
      /* ignore */
    }
  }

  function syncRoute() {
    injectMenuItem();
    var host = ensureHost();
    var active = isLerobotRoute();

    if (host) {
      host.classList.toggle("datalab-lerobot-host--visible", active);
    }

    if (active) {
      clearOtherMenuActive();
      setMenuActive(true);
      scheduleLayout();
      global.setTimeout(layoutHost, 50);
      global.setTimeout(layoutHost, 300);
      syncThemeToIframe();
      if (state.iframe && state.iframe.getAttribute("src") !== CONFIG.iframeSrc) {
        state.iframe.src = CONFIG.iframeSrc;
      }
    } else {
      setMenuActive(false);
    }
  }

  function watchDom() {
    var bodyObserver = new MutationObserver(function () {
      injectMenuItem();
      syncRoute();
      scheduleLayout();
    });
    bodyObserver.observe(document.body, { childList: true, subtree: true });

    var themeObserver = new MutationObserver(syncThemeToIframe);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-color-scheme"],
    });

    var content = findContentArea();
    if (content) {
      var layoutObserver = new MutationObserver(scheduleLayout);
      layoutObserver.observe(content, { attributes: true, attributeFilter: ["class"] });
    }

    global.addEventListener("resize", scheduleLayout);
    global.addEventListener("storage", function (event) {
      if (event.key === "preferred-color-scheme") syncThemeToIframe();
    });

    if (global.matchMedia) {
      var mq = global.matchMedia("(prefers-color-scheme: dark)");
      if (typeof mq.addEventListener === "function") {
        mq.addEventListener("change", syncThemeToIframe);
      } else if (typeof mq.addListener === "function") {
        mq.addListener(syncThemeToIframe);
      }
    }
  }

  function bindHistory() {
    var history = getHistory();
    if (history && typeof history.listen === "function") {
      history.listen(function () {
        scheduleSync();
      });
    }

    global.addEventListener("popstate", scheduleSync);

    var pushState = global.history.pushState;
    var replaceState = global.history.replaceState;

    global.history.pushState = function () {
      var result = pushState.apply(this, arguments);
      scheduleSync();
      return result;
    };

    global.history.replaceState = function () {
      var result = replaceState.apply(this, arguments);
      scheduleSync();
      return result;
    };
  }

  function waitForShell() {
    var attempts = 0;
    var timer = global.setInterval(function () {
      attempts += 1;
      injectMenuItem();
      if ((findMountParent() && findContentArea()) || attempts > 200) {
        global.clearInterval(timer);
        bindHistory();
        syncRoute();
        scheduleLayout();
      }
    }, 100);
  }

  function onReady() {
    injectMenuItem();
    watchDom();
    waitForShell();
    syncRoute();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", onReady);
  } else {
    onReady();
  }
})(window);
