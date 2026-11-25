/* QC overview: review progress and delivery state for every package of a collection. */
(() => {
  const { t, init } = window.LA_I18N;
  const API_PREFIX = window.location.pathname.startsWith("/qc") ? "/qc" : "";
  const STAGES = ["rebuilding", "suspicious", "needs_rebuild", "in_review", "not_started", "delivered", "all_removed"];
  const STAGE_RANK = Object.fromEntries(STAGES.map((stage, index) => [stage, index]));
  const DELIVERY_RANK = { stale: 0, missing: 1, never_built: 2, current: 3 };

  const $ = (id) => document.getElementById(id);
  const els = {
    collection: $("ovCollection"),
    search: $("ovSearch"),
    refresh: $("ovRefresh"),
    totals: $("ovTotals"),
    stages: $("ovStages"),
    status: $("ovStatus"),
    table: $("ovTable"),
    body: $("ovBody"),
    back: $("ovBackLink"),
  };
  const query = new URLSearchParams(window.location.search);
  // Embed/locale state must survive navigation between the review page and the overview.
  const withPassthrough = (params) => {
    if (window.LA_I18N.IS_DATALAB_EMBED) params.set("datalab_embed", "1");
    params.set("lang", window.LA_I18N.getLocale());
    return params;
  };
  const state = {
    data: null,
    stage: query.get("stage") || "all",
    sort: { key: "stage", dir: 1 },
  };

  async function api(path) {
    const response = await fetch(`${API_PREFIX}${path}`);
    if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
    return response.json();
  }

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([key, value]) => {
      if (value == null || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value === true ? "" : value);
    });
    children.flat().forEach((child) => child != null && node.append(child));
    return node;
  }

  function formatTime(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    return Number.isNaN(date.getTime())
      ? String(iso)
      : date.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
  }

  function pct(part, total) {
    // Floor: 998/1000 must not read as 100%.
    return total ? Math.floor((part / total) * 100) : 0;
  }

  function syncUrl() {
    const params = withPassthrough(new URLSearchParams());
    if (els.collection.value) params.set("collection", els.collection.value);
    if (state.stage !== "all") params.set("stage", state.stage);
    history.replaceState(null, "", `${window.location.pathname}?${params}`);
    const back = withPassthrough(new URLSearchParams());
    if (els.collection.value) back.set("collection", els.collection.value);
    els.back.href = `./?${back}`;
    $("ovCrumbRoot").href = els.back.href;
  }

  function reviewUrl(row) {
    const params = withPassthrough(
      new URLSearchParams({
        collection: els.collection.value,
        package: row.id,
        local_path: row.local_path || "",
        episode: "0",
      }),
    );
    return `./?${params}`;
  }

  function sortValue(row, key) {
    switch (key) {
      case "stage":
        return STAGE_RANK[row.stage] ?? 99;
      case "progress":
        return row.episodes ? row.reviewed / row.episodes : 0;
      case "delivery":
        return DELIVERY_RANK[row.delivery?.state] ?? 9;
      case "last_qc_change_at":
        return row.last_qc_change_at || "";
      default:
        return row[key] ?? "";
    }
  }

  function visibleRows() {
    const needle = els.search.value.trim().toLowerCase();
    const { key, dir } = state.sort;
    return (state.data?.packages || [])
      .filter((row) => state.stage === "all" || row.stage === state.stage)
      .filter((row) => !needle || String(row.id).toLowerCase().includes(needle))
      .sort((a, b) => {
        const va = sortValue(a, key);
        const vb = sortValue(b, key);
        if (va < vb) return -dir;
        if (va > vb) return dir;
        // Within a stage, nearly finished packages first.
        const progress = sortValue(b, "progress") - sortValue(a, "progress");
        if (key === "stage" && progress) return progress;
        return String(a.id).localeCompare(String(b.id), undefined, { numeric: true });
      });
  }

  function renderTotals() {
    const data = state.data;
    if (!data) {
      els.totals.textContent = "";
      return;
    }
    const totals = data.totals;
    els.totals.textContent = t("ov_totals", {
      packages: data.packages.length,
      episodes: totals.episodes,
      reviewed: totals.reviewed,
      pct: pct(totals.reviewed, totals.episodes),
      approved: totals.approved,
      rejected: totals.rejected,
      suspicious: totals.suspicious,
      overrides: totals.instruction_overrides,
    });
  }

  function renderStages() {
    const counts = state.data?.stage_counts || {};
    const total = state.data?.packages.length || 0;
    els.stages.replaceChildren(
      ...["all", ...STAGES].map((stage) => {
        const count = stage === "all" ? total : counts[stage] || 0;
        const button = el(
          "button",
          {
            type: "button",
            class: `ls-ov-stage ls-ov-stage--${stage}${state.stage === stage ? " is-active" : ""}`,
            "aria-pressed": String(state.stage === stage),
            disabled: stage !== "all" && count === 0 && state.stage !== stage,
          },
          el("span", { text: t(`ov_stage_${stage}`) }),
          el("span", { class: "ls-ov-count", text: String(count) }),
        );
        button.addEventListener("click", () => {
          state.stage = stage;
          syncUrl();
          render();
        });
        return button;
      }),
    );
  }

  function progressCell(row) {
    const seg = (cls, value, label) =>
      value > 0 ? el("span", { class: `ls-ov-seg ${cls}`, style: `flex-grow:${value}`, title: `${label} ${value}` }) : null;
    return el(
      "div",
      { class: "ls-ov-progress" },
      el(
        "div",
        { class: "ls-ov-bar", role: "img", "aria-label": t("ov_progress_aria", { ...row, pct: pct(row.reviewed, row.episodes) }) },
        seg("is-approved", row.approved, t("ov_approved")),
        seg("is-rejected", row.rejected, t("ov_rejected")),
        seg("is-suspicious", row.suspicious, t("ov_suspicious")),
        seg("is-pending", row.pending, t("ov_pending")),
      ),
      el("span", {
        class: "ls-ov-progress-text",
        text: t("ov_progress_text", { reviewed: row.reviewed, total: row.episodes, pct: pct(row.reviewed, row.episodes) }),
      }),
    );
  }

  function deliveryCell(row) {
    const delivery = row.delivery || {};
    if (row.rebuild_job) {
      return el("span", { class: "ls-ov-badge is-info", text: t("ov_delivery_rebuilding", { progress: row.rebuild_job.progress || "" }) });
    }
    const name = String(delivery.output_root || "").split("/").pop();
    const badge = el("span", {
      class: `ls-ov-badge is-delivery-${delivery.state || "never_built"}`,
      text: t(`ov_delivery_${delivery.state || "never_built"}`),
    });
    if (!name) return badge;
    return el(
      "div",
      { class: "ls-ov-delivery", title: delivery.output_root },
      el("div", { class: "ls-ov-badges" }, badge, acceptanceBadge(delivery)),
      el("span", { class: "ls-ov-sub", text: `${name} · ${formatTime(delivery.finished_at)}` }),
    );
  }

  /* Automatic acceptance check of the last delivery; nothing when it never ran. */
  function acceptanceBadge(delivery) {
    const acceptance = delivery.acceptance;
    if (!acceptance || delivery.state === "missing") return null;
    const problems = acceptance.problems || [];
    return el("span", {
      class: `ls-ov-badge ${acceptance.ok ? "is-acceptance-ok" : "is-acceptance-failed"}`,
      text: t(acceptance.ok ? "ov_acceptance_ok" : "ov_acceptance_failed", {
        count: problems.length + (acceptance.problems_truncated || 0),
      }),
      title: acceptance.ok
        ? t("ov_acceptance_ok_title", { time: formatTime(acceptance.checked_at) })
        : problems.join("\n"),
    });
  }

  function renderRows() {
    const rows = visibleRows();
    els.body.replaceChildren(
      ...rows.map((row) =>
        el(
          "tr",
          { class: row.loadable ? "" : "is-unloadable" },
          el("td", { class: "ls-ov-pkg" }, el("strong", { text: row.id }), el("span", { class: "ls-ov-sub", text: t("ov_episodes", { n: row.episodes }) })),
          el("td", {}, el("span", { class: `ls-ov-badge ls-ov-stage-badge--${row.stage}`, text: t(`ov_stage_${row.stage}`) })),
          el("td", {}, progressCell(row)),
          el("td", { class: "num", text: row.suspicious ? String(row.suspicious) : "–" }),
          el("td", { class: "num", text: row.instruction_overrides ? String(row.instruction_overrides) : "–" }),
          el("td", {}, deliveryCell(row)),
          el("td", { class: "ls-ov-sub", text: formatTime(row.last_qc_change_at) || "–" }),
          el("td", {}, row.loadable ? el("a", { class: "ls-btn ls-btn-ghost ls-ov-open", href: reviewUrl(row), text: t("ov_open") }) : null),
        ),
      ),
    );
    els.table.hidden = false;
    els.status.textContent = rows.length ? "" : t("ov_empty");
    document.querySelectorAll("#ovTable th[data-sort]").forEach((th) => {
      const active = th.dataset.sort === state.sort.key;
      th.setAttribute("aria-sort", active ? (state.sort.dir > 0 ? "ascending" : "descending") : "none");
    });
  }

  function render() {
    renderTotals();
    renderStages();
    renderRows();
  }

  async function loadOverview({ refresh = false } = {}) {
    const collection = els.collection.value;
    if (!collection) return;
    syncUrl();
    els.status.textContent = t("ov_loading");
    els.refresh.disabled = true;
    try {
      const refreshParam = refresh ? "&refresh=true" : "";
      state.data = await api(`/api/qc/overview?collection=${encodeURIComponent(collection)}${refreshParam}`);
      if (state.stage !== "all" && !STAGES.includes(state.stage)) state.stage = "all";
      render();
    } catch (error) {
      state.data = null;
      els.table.hidden = true;
      renderTotals();
      els.stages.replaceChildren();
      els.status.textContent = t("ov_load_failed", { error: String(error.message || error) });
    } finally {
      els.refresh.disabled = false;
    }
  }

  async function loadCollections() {
    const data = await api("/api/datasets/collections");
    const available = (data.collections || []).filter((c) => c.status !== "unavailable");
    els.collection.replaceChildren(
      ...available.map((c) => el("option", { value: c.id, text: `${c.title || c.id} (${c.package_count})` })),
    );
    const wanted = query.get("collection");
    if (wanted && available.some((c) => c.id === wanted)) els.collection.value = wanted;
  }

  function applyText() {
    document.title = `${t("ov_title")} · Datalab`;
    window.LA_I18N.applyHeaderText();
    const setText = (id, key) => {
      const node = $(id);
      if (node) node.textContent = t(key);
    };
    setText("ovTitle", "qc_overview_link");
    setText("ovBackLink", "ov_back");
    setText("ovCollectionLabel", "ov_collection");
    setText("ovSearchLabel", "ov_search");
    setText("ovRefresh", "ov_refresh");
    setText("ovColPackage", "ov_col_package");
    setText("ovColStage", "ov_col_stage");
    setText("ovColProgress", "ov_col_progress");
    setText("ovColSuspicious", "ov_col_suspicious");
    setText("ovColOverrides", "ov_col_overrides");
    setText("ovColDelivery", "ov_col_delivery");
    setText("ovColLastChange", "ov_col_last_change");
    els.search.placeholder = t("ov_search_placeholder");
  }

  document.querySelectorAll("#ovTable th[data-sort]").forEach((th) => {
    th.tabIndex = 0;
    const toggle = () => {
      const key = th.dataset.sort;
      state.sort = state.sort.key === key ? { key, dir: -state.sort.dir } : { key, dir: key === "last_qc_change_at" ? -1 : 1 };
      renderRows();
    };
    th.addEventListener("click", toggle);
    th.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
    });
  });
  els.collection.addEventListener("change", () => {
    state.stage = "all";
    loadOverview();
  });
  els.search.addEventListener("input", renderRows);
  els.refresh.addEventListener("click", () => loadOverview({ refresh: true }));
  window.__laOnLocaleChange = () => {
    applyText();
    if (state.data) render();
  };

  init();
  applyText();
  loadCollections()
    .then(() => loadOverview())
    .catch((error) => {
      els.status.textContent = t("ov_load_failed", { error: String(error.message || error) });
    });
})();
