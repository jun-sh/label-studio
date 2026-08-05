/**
 * Load live LeRobot v3 stream folders served at /lerobot/api/stream/{stationId}/ via HTTP.
 */
(function () {
  "use strict";

  var g = typeof globalThis !== "undefined" ? globalThis : window;

  function normPath(p) {
    return String(p || "")
      .replace(/^\/+/, "")
      .replace(/\\/g, "/");
  }

  function joinUrl(base, rel) {
    var b = base.endsWith("/") ? base : base + "/";
    return b + normPath(rel);
  }

  function HttpLeRobotSource(baseUrl) {
    this.base = baseUrl.endsWith("/") ? baseUrl : baseUrl + "/";
    this.objectUrlCache = new Map();
    this.objectUrlLoading = new Map();
    this.datasetRootPrefix = "";
  }

  HttpLeRobotSource.prototype.resolvePath = function (p) {
    return this.datasetRootPrefix + normPath(p);
  };

  HttpLeRobotSource.prototype.exists = async function (p) {
    try {
      var res = await fetch(joinUrl(this.base, this.resolvePath(p)), { method: "HEAD", cache: "no-store" });
      return res.ok;
    } catch (e) {
      return false;
    }
  };

  HttpLeRobotSource.prototype.readText = async function (p) {
    var res = await fetch(joinUrl(this.base, this.resolvePath(p)), { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status + " for " + p);
    return res.text();
  };

  HttpLeRobotSource.prototype.readBytes = async function (p, progress) {
    var res = await fetch(joinUrl(this.base, this.resolvePath(p)), { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status + " for " + p);
    var buf = await res.arrayBuffer();
    progress?.({ phase: "read", loaded: buf.byteLength, total: buf.byteLength });
    return new Uint8Array(buf);
  };

  HttpLeRobotSource.prototype.getObjectUrl = async function (p, mime, progress) {
    var rel = this.resolvePath(p);
    var isVideo = /\.mp4$/i.test(rel) || (mime && String(mime).indexOf("video/") === 0);
    if (isVideo) {
      var direct = joinUrl(this.base, rel);
      progress?.({ phase: "read", loaded: 1, total: 1 });
      return direct;
    }
    var key = (mime || "application/octet-stream") + ":" + rel;
    if (this.objectUrlCache.has(key)) return this.objectUrlCache.get(key);
    if (this.objectUrlLoading.has(key)) return this.objectUrlLoading.get(key);

    var self = this;
    var pending = (async function () {
      var res = await fetch(joinUrl(self.base, self.resolvePath(p)), { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status + " for " + p);
      var blob = await res.blob();
      progress?.({ phase: "read", loaded: blob.size, total: blob.size });
      var url = URL.createObjectURL(blob);
      self.objectUrlCache.set(key, url);
      return url;
    })();

    this.objectUrlLoading.set(key, pending);
    try {
      return await pending;
    } finally {
      this.objectUrlLoading.delete(key);
    }
  };

  HttpLeRobotSource.prototype.invalidateObjectUrl = async function (p) {
    var path = this.resolvePath(p);
    this.objectUrlCache.forEach(function (_url, key) {
      if (key.endsWith(":" + path)) {
        URL.revokeObjectURL(_url);
      }
    });
    this.objectUrlCache.forEach(function (_url, key) {
      if (key.endsWith(":" + path)) {
        this.objectUrlCache.delete(key);
      }
    }, this);
  };

  HttpLeRobotSource.prototype.clear = function () {
    this.objectUrlCache.forEach(function (url) {
      URL.revokeObjectURL(url);
    });
    this.objectUrlCache.clear();
    this.objectUrlLoading.clear();
  };

  var openInFlight = null;
  var openedBaseUrl = null;

  function streamDatasetLabel(baseUrl) {
    var m = String(baseUrl || "").match(/\/api\/stream\/([^/]+)/);
    if (!m) return "EGO 采集站";
    var id = decodeURIComponent(m[1]);
    if (id === "ego-lan-214") return "EGO 采集站 · 214";
    return id.replace(/^ego-/i, "EGO ").replace(/-/g, " · ");
  }

  g.__datalabOpenHttpDataset = async function (ctrl, baseUrl, historyMode) {
    if (g.__DATALAB_STREAM_STABLE__ && openedBaseUrl === baseUrl) {
      return;
    }
    if (openInFlight) {
      return openInFlight;
    }

    openInFlight = (async function () {
      var src = new HttpLeRobotSource(baseUrl);
      g.__DATALAB_STREAM_CTRL__ = ctrl;
      g.__DATALAB_STREAM_BASE__ = baseUrl;
      var datasetLabel = streamDatasetLabel(baseUrl);
      ctrl.deps.setWelcomeRequest(null);
      ctrl.deps.setDatasetLabel(datasetLabel);
      ctrl.deps.upsertTask?.({
        id: "open-http-stream",
        title: datasetLabel,
        phase: "read",
        loaded: 0,
        total: 1,
      });
      try {
        await ctrl.initializeSource(src);
        ctrl.deps.completeTask?.("open-http-stream");
        openedBaseUrl = baseUrl;
        g.__DATALAB_STREAM_STABLE__ = true;
        if (typeof g.__datalabMarkStreamReady === "function") {
          g.__datalabMarkStreamReady();
        }
      } catch (err) {
        ctrl.deps.failTask?.("open-http-stream", String(err.message || err));
        if (typeof g.__datalabMarkStreamFailed === "function") {
          g.__datalabMarkStreamFailed();
        }
        throw err;
      } finally {
        openInFlight = null;
      }
    })();

    return openInFlight;
  };

  /** Intentional no-op: auto-reopen caused joints/split-view flicker. */
  g.__datalabReopenHttpDataset = async function () {
    return;
  };

  function streamBaseFromQuery() {
    try {
      var raw = new URLSearchParams(g.location.search).get("url") || "";
      if (!raw) return null;
      if (raw.indexOf("/api/stream/") >= 0) {
        return raw.startsWith("http") ? raw : g.location.origin + raw;
      }
      if (raw.indexOf("/api/sample/") >= 0 && raw.indexOf("/dataset/") >= 0) {
        return raw.startsWith("http") ? raw : g.location.origin + raw;
      }
      return null;
    } catch (e) {
      return null;
    }
  }

  /** Retry open when iframe was display:none (collection preview tab) on first navigation. */
  function bootstrapStreamFromQuery() {
    var base = streamBaseFromQuery();
    if (!base) return false;
    var ctrl = g.__DATALAB_STREAM_CTRL__;
    if (!ctrl || typeof g.__datalabOpenHttpDataset !== "function") return false;
    if (g.__DATALAB_STREAM_STABLE__ && g.__DATALAB_STREAM_BASE__ === base) return true;
    g.__datalabOpenHttpDataset(ctrl, base, "replace").catch(function () {
      /* overlay shows fallback */
    });
    return true;
  }

  function nudgeStreamUrlOpen() {
    var base = streamBaseFromQuery();
    if (!base) return false;
    try {
      var params = new URLSearchParams(g.location.search || "");
      if (params.get("url")) return false;
      var rel = base.indexOf(g.location.origin) === 0 ? base.slice(g.location.origin.length) : base;
      params.set("url", rel);
      var next = g.location.pathname + "?" + params.toString();
      g.history.replaceState({}, "", next);
      g.dispatchEvent(new PopStateEvent("popstate"));
      return true;
    } catch (e) {
      return false;
    }
  }

  g.__datalabBootstrapStreamOpen = function () {
    if (bootstrapStreamFromQuery()) return true;
    if (nudgeStreamUrlOpen()) return true;
    return false;
  };

  if (streamBaseFromQuery()) {
    var bootstrapTicks = 0;
    var bootstrapTimer = g.setInterval(function () {
      if (g.__datalabBootstrapStreamOpen() || ++bootstrapTicks >= 120) {
        g.clearInterval(bootstrapTimer);
      }
    }, 500);
  }
})();
