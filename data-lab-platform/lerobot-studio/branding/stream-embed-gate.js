/**
 * Sync boot: hide LeRobot welcome until live stream dataset is ready (scheme A).
 */
(function () {
  "use strict";

  var g = typeof globalThis !== "undefined" ? globalThis : window;
  if (g.__DATALAB_STREAM_GATE__) return;

  function parseStreamFromQuery() {
    try {
      var raw = new URLSearchParams(g.location.search).get("url") || "";
      if (!raw || raw.indexOf("/api/stream/") < 0) return null;
      var m = raw.match(/\/lerobot\/api\/stream\/([^/]+)\/?/);
      if (!m) return null;
      var base = raw.startsWith("http") ? raw : g.location.origin + raw;
      return { stationId: decodeURIComponent(m[1]), baseUrl: base };
    } catch (e) {
      return null;
    }
  }

  var stream = parseStreamFromQuery();
  if (!stream) return;

  g.__DATALAB_STREAM_GATE__ = stream;

  var root = document.documentElement;
  var qs = new URLSearchParams(g.location.search);
  root.setAttribute("data-datalab-embed", "1");
  if (qs.get("datalab_collection") === "1") {
    root.setAttribute("data-datalab-collection-embed", "1");
  }
  root.setAttribute("data-datalab-stream-pending", "1");
  root.removeAttribute("data-datalab-stream-ready");

  var lang = (new URLSearchParams(g.location.search).get("lang") || root.lang || "en").toLowerCase();
  var label =
    lang.indexOf("zh") === 0 ? "正在加载流式数据集…" : "Loading live stream dataset…";
  root.setAttribute("data-datalab-stream-label", label);

  g.__datalabMarkStreamReady = function () {
    root.setAttribute("data-datalab-stream-ready", "1");
    root.removeAttribute("data-datalab-stream-pending");
  };

  g.__datalabMarkStreamFailed = function () {
    if (root.getAttribute("data-datalab-stream-ready") === "1") return;
    root.removeAttribute("data-datalab-stream-pending");
    root.setAttribute(
      "data-datalab-stream-label",
      lang.indexOf("zh") === 0 ? "流式数据暂不可用" : "Live stream unavailable",
    );
  };
})();
