/**
 * Keep /data LeRobot iframe interactive when the shell menu marks sidebar-opened.
 * React embed layout briefly sets #datalab-viz-layer { pointer-events: none }.
 */
(function () {
  "use strict";

  function isDataVizRoute() {
    var path = window.location.pathname || "";
    return path === "/data" || /^\/data\/[^/]+/.test(path);
  }

  function keepVizLayerInteractive() {
    var layer = document.getElementById("datalab-viz-layer");
    if (!layer) return;
    if (layer.style.pointerEvents === "none") {
      layer.style.pointerEvents = "";
    }
  }

  function attach() {
    if (!isDataVizRoute()) return;
    var layer = document.getElementById("datalab-viz-layer");
    if (!layer) {
      window.requestAnimationFrame(attach);
      return;
    }
    keepVizLayerInteractive();
    if (layer.dataset.datalabVizPointerFix === "1") return;
    layer.dataset.datalabVizPointerFix = "1";
    new MutationObserver(keepVizLayerInteractive).observe(layer, {
      attributes: true,
      attributeFilter: ["style"],
    });
    window.addEventListener("datalab:layout", keepVizLayerInteractive);
    window.setInterval(keepVizLayerInteractive, 100);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }
})();
