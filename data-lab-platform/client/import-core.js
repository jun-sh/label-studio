/**
 * Shared offline segment import logic (upload, poll, state).
 */
(function () {
  "use strict";

  var POLL_MS = 2000;
  var MAX_CONCURRENT_UPLOADS = 2;
  var API = "/api/collection/stations";

  var STRINGS = {
    zh: {
      title: "单段应急补传",
      help: "仅用于补传 1 个 .tar.zst 段包，不具备批量商用可靠性",
      agentNotice:
        "批量上传请使用 214 原生 Agent：systemctl --user start ecs-oak-upload-stack.target（pending=0 后 stop）",
      batchWarn:
        "已选择多个文件：浏览器不适合批量导入，请改用 214 upload stack；继续仅作应急尝试。",
      dropHint: "拖拽单个 .tar.zst，或点击选择（批量请用 214 Agent）",
      dropActive: "松开以上传",
      total: "总进度",
      retry: "重试",
      skipped: "已存在，跳过",
      processing: "处理中",
      pollFailed: "无法获取任务状态，请关闭后刷新 Episodes 确认",
      close: "关闭",
      stages: {
        queued: "等待中",
        uploading: "上传中",
        processing: "处理中",
        validating: "处理中",
        extracting: "处理中",
        committing: "处理中",
        done: "成功",
        doneRawAck: "已上传，后台处理中",
        skipped: "已存在，跳过",
        failed: "失败",
      },
    },
    en: {
      title: "Single-segment fallback upload",
      help: "One .tar.zst only — not reliable for batch production use",
      agentNotice:
        "For batch upload use 214 Agent: systemctl --user start ecs-oak-upload-stack.target",
      batchWarn:
        "Multiple files selected: browser is not for batch import; use 214 upload stack.",
      dropHint: "Drop one .tar.zst or browse (use 214 Agent for batch)",
      dropActive: "Release to upload",
      total: "Overall",
      retry: "Retry",
      skipped: "Already ingested, skipped",
      processing: "Processing",
      pollFailed: "Could not poll task status — close and refresh Episodes",
      close: "Close",
      stages: {
        queued: "Queued",
        uploading: "Uploading",
        processing: "Processing",
        validating: "Processing",
        extracting: "Processing",
        committing: "Processing",
        done: "Done",
        doneRawAck: "Uploaded, processing in background",
        skipped: "Already ingested, skipped",
        failed: "Failed",
      },
    },
  };

  function pageLang() {
    var q = "";
    try {
      q = new URLSearchParams(window.location.search).get("lang") || "";
    } catch (e) {
      q = "";
    }
    if (!q && window.parent !== window) {
      try {
        q = new URLSearchParams(window.parent.location.search).get("lang") || "";
      } catch (e2) {
        /* ignore */
      }
    }
    var html = (document.documentElement && document.documentElement.lang) || "";
    return (q || html || "en").toLowerCase().indexOf("zh") === 0 ? "zh" : "en";
  }

  function t(key) {
    var bucket = STRINGS[pageLang()] || STRINGS.en;
    return bucket[key];
  }

  function stageLabel(status, item) {
    var stages = (STRINGS[pageLang()] || STRINGS.en).stages;
    if (status === "done" && item && (item.deriveStatus === "verified" || item.rawAck)) {
      return stages.doneRawAck;
    }
    if (
      status === "validating" ||
      status === "extracting" ||
      status === "committing" ||
      status === "processing"
    ) {
      return stages.processing;
    }
    return stages[status] || status;
  }

  function isTarZst(file) {
    return file && String(file.name || "").toLowerCase().endsWith(".tar.zst");
  }

  function ImportController(stationId, options) {
    this.stationId = stationId;
    this.onStateChange = (options && options.onStateChange) || function () {};
    this.onSuccess = (options && options.onSuccess) || function () {};
    this.items = [];
    this.pollTimer = null;
    this._notifiedDone = Object.create(null);
    this.batchWarn = null;
  }

  ImportController.prototype.enqueueFiles = function (fileList) {
    if (!fileList || !fileList.length) return;
    var tarCount = 0;
    for (var i = 0; i < fileList.length; i++) {
      if (isTarZst(fileList[i])) tarCount += 1;
    }
    if (tarCount > 1) {
      this.batchWarn = t("batchWarn");
    }
    for (var i = 0; i < fileList.length; i++) {
      var file = fileList[i];
      if (!isTarZst(file)) continue;
      this.items.push({
        id: String(Date.now()) + "_" + i + "_" + Math.random().toString(36).slice(2, 8),
        file: file,
        fileName: file.name,
        status: "queued",
        uploadPct: 0,
        taskId: null,
        duplicate: false,
        errorMsg: null,
        framesCommitted: 0,
        deriveStatus: null,
        rawAck: false,
      });
    }
    this._emit();
    this._kickQueue();
  };

  ImportController.prototype.retryItem = function (itemId) {
    var self = this;
    this.items.forEach(function (item) {
      if (item.id !== itemId) return;
      item.status = "queued";
      item.taskId = null;
      item.errorMsg = null;
      self._kickQueue();
    });
    this._emit();
  };

  ImportController.prototype.getProgress = function () {
    var total = this.items.length;
    var done = this.items.filter(function (it) {
      return it.status === "done";
    }).length;
    var skipped = this.items.filter(function (it) {
      return it.status === "skipped";
    }).length;
    var failed = this.items.filter(function (it) {
      return it.status === "failed";
    }).length;
    var active = this.items.filter(function (it) {
      return (
        it.status !== "done" &&
        it.status !== "failed" &&
        it.status !== "skipped" &&
        it.status !== "queued"
      );
    });
    var finished = done + skipped + failed;
    var pct = total ? Math.round(((finished + failed * 0) / total) * 100) : 0;
    if (active.length) {
      var sum = 0;
      active.forEach(function (it) {
        if (it.status === "uploading") sum += it.uploadPct / 100;
        else sum += 0.85;
      });
      pct = Math.min(99, Math.round(((done + skipped + sum) / total) * 100));
    }
    if (finished === total && total > 0) pct = 100;
    var meta = done + skipped + " / " + total;
    if (failed) meta += " (" + failed + " failed)";
    else if (active.length && finished < total) meta += " (" + t("processing") + ")";
    return {
      total: total,
      done: done + skipped,
      failed: failed,
      pct: pct,
      meta: meta,
      hasItems: total > 0,
    };
  };

  ImportController.prototype.destroy = function () {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    this.items.forEach(function (item) {
      if (item._xhr) {
        try {
          item._xhr.abort();
        } catch (e) {
          /* ignore */
        }
        item._xhr = null;
      }
    });
  };

  ImportController.prototype._emit = function () {
    this.onStateChange(this.items, this.getProgress());
  };

  ImportController.prototype._activeUploadCount = function () {
    return this.items.filter(function (it) {
      return (
        it.status === "uploading" ||
        (it.taskId &&
          it.status !== "done" &&
          it.status !== "failed" &&
          it.status !== "skipped")
      );
    }).length;
  };

  ImportController.prototype._kickQueue = function () {
    var self = this;
    var slots = MAX_CONCURRENT_UPLOADS - this._activeUploadCount();
    if (slots <= 0) return;
    var queued = this.items.filter(function (it) {
      return it.status === "queued";
    });
    for (var i = 0; i < Math.min(slots, queued.length); i++) {
      self._uploadItem(queued[i]);
    }
    this._ensurePoll();
  };

  ImportController.prototype._uploadItem = function (item) {
    var self = this;
    item.status = "uploading";
    item.uploadPct = 0;
    item.errorMsg = null;
    item.taskId = null;
    this._emit();

    var form = new FormData();
    form.append("file", item.file, item.fileName);
    var xhr = new XMLHttpRequest();
    item._xhr = xhr;
    xhr.open("POST", API + "/" + encodeURIComponent(this.stationId) + "/import", true);
    xhr.withCredentials = true;
    xhr.upload.onprogress = function (ev) {
      if (!ev.lengthComputable) return;
      item.uploadPct = Math.round((ev.loaded / ev.total) * 100);
      self._emit();
    };
    xhr.onload = function () {
      item._xhr = null;
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          var data = JSON.parse(xhr.responseText || "{}");
          item.taskId = data.taskId;
          item.status = "validating";
          item.uploadPct = 100;
          self._emit();
          self._ensurePoll();
        } catch (e) {
          item.status = "failed";
          item.errorMsg = "invalid response";
          self._emit();
          self._kickQueue();
        }
        return;
      }
      item.status = "failed";
      try {
        var err = JSON.parse(xhr.responseText || "{}");
        item.errorMsg = err.message || err.error || "upload failed";
      } catch (e2) {
        item.errorMsg = "upload failed (" + xhr.status + ")";
      }
      self._emit();
      self._kickQueue();
    };
    xhr.onerror = function () {
      item._xhr = null;
      item.status = "failed";
      item.errorMsg = "network error";
      self._emit();
      self._kickQueue();
    };
    xhr.send(form);
  };

  ImportController.prototype._ensurePoll = function () {
    var self = this;
    if (this.pollTimer) return;
    this.pollTimer = window.setInterval(function () {
      self._pollActive();
    }, POLL_MS);
    this._pollActive();
  };

  ImportController.prototype._pollActive = function () {
    var self = this;
    var pending = this.items.filter(function (it) {
      return (
        it.taskId &&
        it.status !== "done" &&
        it.status !== "failed" &&
        it.status !== "skipped"
      );
    });
    if (!pending.length) {
      if (this.pollTimer) {
        clearInterval(this.pollTimer);
        this.pollTimer = null;
      }
      return;
    }
    pending.forEach(function (item) {
      fetch(
        API +
          "/" +
          encodeURIComponent(self.stationId) +
          "/import/tasks/" +
          encodeURIComponent(item.taskId),
        { credentials: "same-origin", cache: "no-store" },
      )
        .then(function (res) {
          if (!res.ok) {
            item._pollMiss = (item._pollMiss || 0) + 1;
            if (item._pollMiss >= 5) {
              item.status = "failed";
              item.errorMsg = t("pollFailed");
              self._emit();
            }
            return null;
          }
          item._pollMiss = 0;
          return res.json();
        })
        .then(function (task) {
          if (!task) return;
          var prevStatus = item.status;
          item.status = task.status || item.status;
          item.duplicate = Boolean(task.duplicate);
          item.framesCommitted = Number(task.framesCommitted || 0);
          item.deriveStatus = task.deriveStatus || null;
          item.rawAck =
            item.status === "done" &&
            (item.deriveStatus === "verified" ||
              (item.framesCommitted === 0 && !item.duplicate));
          item.errorMsg = task.errorMsg || null;
          if (task.status === "skipped") {
            item.status = "skipped";
            item.duplicate = true;
          }
          if (
            (item.status === "done" || item.status === "skipped") &&
            prevStatus !== item.status &&
            !self._notifiedDone[item.id]
          ) {
            self._notifiedDone[item.id] = true;
            self.onSuccess({
              duplicate: item.status === "skipped" || item.duplicate,
              framesCommitted: item.framesCommitted,
              fileName: item.fileName,
            });
          }
          self._emit();
          if (item.status === "done" || item.status === "failed" || item.status === "skipped") {
            self._kickQueue();
          }
        })
        .catch(function () {
          item._pollMiss = (item._pollMiss || 0) + 1;
          if (item._pollMiss >= 5) {
            item.status = "failed";
            item.errorMsg = t("pollFailed");
            self._emit();
          }
        });
    });
  };

  function bindDropZone(dropEl, fileInput, controller) {
    dropEl.addEventListener("click", function () {
      fileInput.click();
    });
    dropEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        fileInput.click();
      }
    });
    fileInput.addEventListener("change", function () {
      controller.enqueueFiles(fileInput.files);
      fileInput.value = "";
    });
    ["dragenter", "dragover"].forEach(function (ev) {
      dropEl.addEventListener(ev, function (e) {
        e.preventDefault();
        e.stopPropagation();
        dropEl.classList.add("is-dragover");
        var textEl = dropEl.querySelector(".datalab-import-ui__drop-text");
        if (textEl) textEl.textContent = t("dropActive");
      });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      dropEl.addEventListener(ev, function (e) {
        e.preventDefault();
        e.stopPropagation();
        dropEl.classList.remove("is-dragover");
        var textEl = dropEl.querySelector(".datalab-import-ui__drop-text");
        if (textEl) textEl.textContent = t("dropHint");
      });
    });
    dropEl.addEventListener("drop", function (e) {
      e.preventDefault();
      e.stopPropagation();
      controller.enqueueFiles(e.dataTransfer && e.dataTransfer.files);
    });
  }

  function renderImportUi(mountEl, controller) {
    var root = document.createElement("div");
    root.className = "datalab-import-ui";
    root.innerHTML =
      '<p class="datalab-import-ui__agent-notice" role="note"></p>' +
      '<p class="datalab-import-ui__batch-warn" hidden role="alert"></p>' +
      '<div class="datalab-import-ui__drop" tabindex="0" role="button">' +
      '<p class="datalab-import-ui__drop-text"></p>' +
      '<input type="file" class="datalab-import-ui__file" accept=".tar.zst,application/zstd" multiple hidden />' +
      "</div>" +
      '<div class="datalab-import-ui__total" hidden>' +
      '<div class="datalab-import-ui__total-label"></div>' +
      '<div class="datalab-import-ui__bar"><div class="datalab-import-ui__bar-fill"></div></div>' +
      '<div class="datalab-import-ui__total-meta"></div>' +
      "</div>" +
      '<ul class="datalab-import-ui__list" hidden></ul>';

    root.querySelector(".datalab-import-ui__agent-notice").textContent = t("agentNotice");
    root.querySelector(".datalab-import-ui__drop-text").textContent = t("dropHint");
    root.querySelector(".datalab-import-ui__total-label").textContent = t("total");

    var dropEl = root.querySelector(".datalab-import-ui__drop");
    var fileInput = root.querySelector(".datalab-import-ui__file");
    var listEl = root.querySelector(".datalab-import-ui__list");
    var totalEl = root.querySelector(".datalab-import-ui__total");
    var totalFill = root.querySelector(".datalab-import-ui__bar-fill");
    var totalMeta = root.querySelector(".datalab-import-ui__total-meta");
    var batchWarnEl = root.querySelector(".datalab-import-ui__batch-warn");

    bindDropZone(dropEl, fileInput, controller);

    function syncUi() {
      var progress = controller.getProgress();
      batchWarnEl.hidden = !controller.batchWarn;
      if (controller.batchWarn) batchWarnEl.textContent = controller.batchWarn;
      totalEl.hidden = !progress.hasItems;
      listEl.hidden = !progress.hasItems;
      totalFill.style.width = progress.pct + "%";
      totalMeta.textContent = progress.meta;

      listEl.innerHTML = "";
      controller.items.forEach(function (item) {
        var li = document.createElement("li");
        li.className = "datalab-import-ui__row";
        var label = stageLabel(item.status, item);
        if (item.status === "skipped") label = t("skipped");
        var rowPct =
          item.status === "uploading"
            ? item.uploadPct
            : item.status === "done" || item.status === "skipped"
              ? 100
              : item.status === "failed"
                ? 100
                : item.status === "queued"
                  ? 0
                  : 55;
        li.innerHTML =
          '<div class="datalab-import-ui__row-head">' +
          '<span class="datalab-import-ui__fname"></span>' +
          '<span class="datalab-import-ui__stage"></span>' +
          "</div>" +
          '<div class="datalab-import-ui__bar datalab-import-ui__bar--row">' +
          '<div class="datalab-import-ui__bar-fill"></div></div>' +
          '<p class="datalab-import-ui__err" hidden></p>' +
          '<button type="button" class="datalab-import-ui__retry" hidden></button>';
        li.querySelector(".datalab-import-ui__fname").textContent = item.fileName;
        li.querySelector(".datalab-import-ui__stage").textContent = label;
        li.querySelector(".datalab-import-ui__bar-fill").style.width = rowPct + "%";
        if (item.errorMsg) {
          var errEl = li.querySelector(".datalab-import-ui__err");
          errEl.hidden = false;
          errEl.textContent = item.errorMsg;
        }
        if (item.status === "failed") {
          var btn = li.querySelector(".datalab-import-ui__retry");
          btn.hidden = false;
          btn.textContent = t("retry");
          btn.addEventListener("click", function () {
            controller.retryItem(item.id);
          });
        }
        listEl.appendChild(li);
      });
    }

    var userOnStateChange = controller.onStateChange;
    controller.onStateChange = function (items, progress) {
      syncUi();
      if (typeof userOnStateChange === "function") {
        userOnStateChange(items, progress);
      }
    };
    syncUi();

    mountEl.appendChild(root);
    return { root: root, syncUi: syncUi, destroy: function () {} };
  }

  var modalState = {
    el: null,
    controller: null,
    escHandler: null,
    bodyOverflow: "",
  };

  function closeImportModal() {
    if (!modalState.el) return;
    if (modalState.controller) {
      modalState.controller.destroy();
      modalState.controller = null;
    }
    if (modalState.escHandler) {
      document.removeEventListener("keydown", modalState.escHandler);
      modalState.escHandler = null;
    }
    modalState.el.remove();
    modalState.el = null;
    document.documentElement.classList.remove("datalab-import-modal-open");
    document.body.style.overflow = modalState.bodyOverflow;
  }

  function openImportModal(stationId, options) {
    options = options || {};
    if (!stationId) return;
    closeImportModal();

    var labels = STRINGS[pageLang()] || STRINGS.en;
    modalState.bodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.documentElement.classList.add("datalab-import-modal-open");

    var modalEl = document.createElement("div");
    modalEl.className = "datalab-import-modal";
    modalEl.setAttribute("data-datalab-import-modal", "1");
    modalEl.innerHTML =
      '<div class="datalab-import-modal__backdrop" data-datalab-import-close="1"></div>' +
      '<div class="datalab-import-modal__dialog" role="dialog" aria-modal="true" aria-labelledby="datalab-import-title" tabindex="-1">' +
      '<header class="datalab-import-modal__header">' +
      '<div class="datalab-import-modal__titles">' +
      '<h2 id="datalab-import-title" class="datalab-import-modal__title"></h2>' +
      '<p class="datalab-import-modal__help"></p>' +
      '<p class="datalab-import-modal__agent-notice" role="note"></p>' +
      "</div>" +
      '<button type="button" class="datalab-import-modal__icon-close" data-datalab-import-close="1" aria-label=""></button>' +
      "</header>" +
      '<div class="datalab-import-modal__body"></div>' +
      '<footer class="datalab-import-modal__footer">' +
      '<button type="button" class="datalab-import-modal__close" data-datalab-import-close="1"></button>' +
      "</footer>" +
      "</div>";

    modalEl.querySelector(".datalab-import-modal__title").textContent = labels.title;
    modalEl.querySelector(".datalab-import-modal__help").textContent = labels.help;
    modalEl.querySelector(".datalab-import-modal__agent-notice").textContent = labels.agentNotice;
    modalEl.querySelector(".datalab-import-modal__icon-close").textContent = "\u00d7";
    modalEl.querySelector(".datalab-import-modal__icon-close").setAttribute("aria-label", labels.close);
    modalEl.querySelector(".datalab-import-modal__close").textContent = labels.close;

    var body = modalEl.querySelector(".datalab-import-modal__body");
    modalState.controller = new ImportController(stationId, {
      onSuccess: options.onSuccess || function () {},
    });
    renderImportUi(body, modalState.controller);

    modalEl.addEventListener("click", function (e) {
      if (e.target && e.target.getAttribute("data-datalab-import-close") === "1") {
        e.preventDefault();
        closeImportModal();
      }
    });
    modalEl.addEventListener("dragover", function (e) {
      e.stopPropagation();
    });
    modalEl.addEventListener("drop", function (e) {
      e.stopPropagation();
    });

    modalState.escHandler = function (e) {
      if (e.key === "Escape") closeImportModal();
    };
    document.addEventListener("keydown", modalState.escHandler);

    document.body.appendChild(modalEl);
    modalState.el = modalEl;
    var dialog = modalEl.querySelector(".datalab-import-modal__dialog");
    if (dialog && dialog.focus) dialog.focus();
  }

  window.DatalabImportCore = {
    API: API,
    POLL_MS: POLL_MS,
    STRINGS: STRINGS,
    pageLang: pageLang,
    t: t,
    stageLabel: stageLabel,
    isTarZst: isTarZst,
    ImportController: ImportController,
    renderImportUi: renderImportUi,
    bindDropZone: bindDropZone,
    openModal: openImportModal,
    closeModal: closeImportModal,
  };
})();
