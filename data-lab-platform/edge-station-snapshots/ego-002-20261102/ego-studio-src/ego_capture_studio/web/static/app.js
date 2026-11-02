async function loadStats() {
  try {
    const r = await fetch("/api/stats");
    if (!r.ok) return;
    const data = await r.json();
    document.querySelectorAll("[data-key]").forEach((el) => {
      const k = el.getAttribute("data-key");
      if (k && Object.prototype.hasOwnProperty.call(data, k)) {
        el.textContent = String(data[k]);
      }
    });
  } catch {
    /* ignore */
  }
}

loadStats();
