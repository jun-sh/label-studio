/**
 * Offline segment import card — parent-page fallback (hidden by default on collection viz).
 */
(function () {
  "use strict";

  var core = window.DatalabImportCore;
  if (!core) return;

  function ImportCard(stationId, mountEl) {
    this.stationId = stationId;
    this.mountEl = mountEl;
    var self = this;
    this.controller = new core.ImportController(stationId, {
      onSuccess: function () {
        if (typeof window.__datalabCollectionSetDatasetReady === "function") {
          window.__datalabCollectionSetDatasetReady(true);
        }
      },
    });
    this._render();
  }

  ImportCard.prototype._render = function () {
    var root = document.createElement("article");
    root.className = "datalab-station-card datalab-import-card";
    var head = document.createElement("div");
    head.className = "datalab-import-card__head";
    head.innerHTML =
      '<div><h3 class="datalab-station-card__name datalab-import-card__title"></h3>' +
      '<p class="datalab-station-card__meta datalab-import-card__help" title=""></p></div>';
    head.querySelector(".datalab-import-card__title").textContent = core.t("title");
    var help = head.querySelector(".datalab-import-card__help");
    help.textContent = core.t("help");
    help.title = core.t("help");
    root.appendChild(head);

    var body = document.createElement("div");
    body.className = "datalab-import-card__body";
    root.appendChild(body);
    this.mountEl.appendChild(root);

    core.renderImportUi(body, this.controller);

    this.root = root;
  };

  function init(stationId) {
    var slot = document.getElementById("datalab-import-card-slot");
    if (!slot || slot.dataset.initialized === "1") return;
    if (slot.hidden || slot.hasAttribute("hidden")) return;
    slot.dataset.initialized = "1";
    new ImportCard(stationId, slot);
  }

  window.DatalabImportCard = { init: init };
})();
