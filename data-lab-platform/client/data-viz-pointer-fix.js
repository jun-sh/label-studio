/**
 * Keep embed iframes interactive (/data, /collection). Shell scripts must not
 * set pointer-events: none on full-screen layers — that blocks wheel/clicks.
 */
(function () {
  "use strict";

  var LAYER_IDS = [
    "datalab-viz-layer",
    "datalab-station-layer",
    "datalab-collection-layer",
    "datalab-stream-loading-layer",
  ];

  function isEmbedShellRoute() {
    var path = window.location.pathname || "";
    return (
      path === "/data" ||
      /^\/data\/[^/]+/.test(path) ||
      path.indexOf("/collection") === 0
    );
  }

  function keepEmbedLayersInteractive() {
    LAYER_IDS.forEach(function (id) {
      var layer = document.getElementById(id);
      if (!layer) return;
      if (layer.style.pointerEvents === "none") {
        layer.style.pointerEvents = "";
      }
    });
  }

  function attach() {
    if (!isEmbedShellRoute()) return;
    var found = LAYER_IDS.some(function (id) {
      return document.getElementById(id);
    });
    if (!found) {
      window.requestAnimationFrame(attach);
      return;
    }
    keepEmbedLayersInteractive();
    if (document.body.dataset.datalabShellPointerFix === "1") return;
    document.body.dataset.datalabShellPointerFix = "1";
    LAYER_IDS.forEach(function (id) {
      var layer = document.getElementById(id);
      if (!layer) return;
      new MutationObserver(keepEmbedLayersInteractive).observe(layer, {
        attributes: true,
        attributeFilter: ["style"],
      });
    });
    window.addEventListener("datalab:layout", keepEmbedLayersInteractive);
    window.setInterval(keepEmbedLayersInteractive, 100);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }
})();
