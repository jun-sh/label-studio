// Collection-station actions that the old engine added through overlays (episode delete, import).
// Same endpoints and copy as lerobot-studio/branding/overlay-episode-delete.js and overlay-import.js.

const IMPORT_CORE_SRC = '/_datalab/import-core.js?v=7';
const IMPORT_MODAL_CSS = '/_datalab/import-modal.css';

export function confirmDeleteText(indices) {
  const list = indices.map((n) => `#${n}`).join(', ');
  return (
    `确认从采集站永久删除 ${list}？\n\n` +
    '· 本次操作仅移除采集列表中的可回放数据（L2）；原始录制（raw/segments）仍保留。\n' +
    '· 确认后系统将更新列表并刷新视图，对应条目将不再显示于侧边栏。'
  );
}

/** POST the station delete API; resolves on success, rejects with a display message. */
export async function deleteEpisodes(stationId, indices) {
  let res;
  try {
    res = await fetch(`/lerobot/api/collection/stations/${encodeURIComponent(stationId)}/episodes/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ episode_indices: indices, confirm: true, apply: true }),
    });
  } catch {
    throw new Error('网络错误');
  }
  const payload = await res.json().catch(() => null);
  if (!res.ok || !payload || payload.ok === false) {
    throw new Error((payload && (payload.detail || payload.error)) || `HTTP ${res.status}`);
  }
  return payload;
}

let importCorePromise = null;

function parentImportCore() {
  try {
    return window.parent !== window && window.parent.DatalabImportCore?.openModal ? window.parent.DatalabImportCore : null;
  } catch {
    return null; // cross-origin parent
  }
}

/**
 * The platform's import modal (client/import-core.js + import-modal.css). Like the old overlay,
 * prefer the /collection shell's copy so the modal covers the whole page; else load it here once.
 */
function loadImportCore() {
  const fromParent = parentImportCore();
  if (fromParent) return Promise.resolve(fromParent);
  if (window.DatalabImportCore?.openModal) return Promise.resolve(window.DatalabImportCore);
  importCorePromise ||= new Promise((resolve, reject) => {
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = IMPORT_MODAL_CSS;
    document.head.appendChild(css);
    const s = document.createElement('script');
    s.src = IMPORT_CORE_SRC;
    s.onload = () =>
      window.DatalabImportCore?.openModal ? resolve(window.DatalabImportCore) : reject(new Error('import core missing'));
    s.onerror = () => reject(new Error('import core failed to load'));
    document.head.appendChild(s);
  }).catch((e) => {
    importCorePromise = null;
    throw e;
  });
  return importCorePromise;
}

export async function openImport(stationId, onSuccess) {
  const core = await loadImportCore();
  core.openModal(stationId, {
    onSuccess: () => {
      onSuccess?.();
      // Same message the old overlay sent, for a /collection shell that listens for it.
      try {
        if (window.parent !== window) {
          window.parent.postMessage({ type: 'datalab-import-done', stationId }, window.location.origin);
        }
      } catch {
        /* cross-origin parent */
      }
    },
  });
}
