/**
 * Poll live stream status only — no dataset reset/reopen (avoids UI flicker).
 */
(function () {
  "use strict";

  var g = typeof globalThis !== "undefined" ? globalThis : window;
  if (!g || g.__DATALAB_STREAM_POLL__) return;
  g.__DATALAB_STREAM_POLL__ = true;

  function stationId() {
    try {
      var params = new URLSearchParams(g.location.search || "");
      var raw = params.get("url") || "";
      var m = raw.match(/\/lerobot\/api\/stream\/([^/]+)\/?/);
      if (m) return decodeURIComponent(m[1]);
      if (g.parent !== g) {
        return new URLSearchParams(g.parent.location.search || "").get("station");
      }
    } catch (e) {
      /* ignore */
    }
    return null;
  }

  function tick() {
    var station = stationId();
    if (!station) return;
    fetch("/lerobot/api/stream/" + encodeURIComponent(station) + "/status", {
      credentials: "same-origin",
      cache: "no-store",
    })
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .then(function (payload) {
        if (!payload) return;
        g.__DATALAB_STREAM_STATUS__ = payload;
      })
      .catch(function () {
        /* ignore */
      });
  }

  setInterval(tick, 10000);
  setTimeout(tick, 2000);
})();
