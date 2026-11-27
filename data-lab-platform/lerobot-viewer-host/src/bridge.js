// Messages from the /collection shell (label_studio/templates/datalab/collection_viz.html), which owns
// the 回放/实时 pill and the light/dark switch and posts them into this iframe. Same protocol the old
// engine handled in overlay-collection-mode.js and overlay-collection-theme.js.
import { useEffect, useState } from 'react';

const MODE_MSG = 'datalab-collection-mode';
const THEME_MSG = 'datalab-lerobot-theme';
// The shell reads this key from the iframe's localStorage to show the current theme.
const THEME_KEY = 'theme';

function normalizeTheme(value) {
  const t = String(value || '').toLowerCase();
  return t === 'dark' || t === 'light' ? t : 'system';
}

function readStoredTheme() {
  try {
    return normalizeTheme(localStorage.getItem(THEME_KEY));
  } catch {
    return 'system';
  }
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', theme);
}

const INITIAL_LIVE = {
  mode: 'dataset',
  online: true,
  captureState: 'unknown',
  remotePreview: false,
  allowed: false,
};

/** `theme` for the viewer and sidebar, `live` for the 实时 preview. */
export function useCollectionBridge() {
  const [theme, setTheme] = useState(() => {
    const t = readStoredTheme();
    applyTheme(t);
    return t;
  });
  const [live, setLive] = useState(INITIAL_LIVE);

  useEffect(() => {
    const onMessage = (event) => {
      if (event.source !== window.parent || event.origin !== window.location.origin) return;
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (data.type === THEME_MSG) {
        const next = normalizeTheme(data.theme);
        applyTheme(next);
        setTheme(next);
        try {
          localStorage.setItem(THEME_KEY, next);
        } catch {
          /* storage unavailable */
        }
        return;
      }
      if (data.type !== MODE_MSG) return;
      setLive((cur) => {
        const next = { ...cur };
        if (typeof data.remotePreview === 'boolean') next.remotePreview = data.remotePreview;
        if (typeof data.online === 'boolean') next.online = data.online;
        if (typeof data.captureState === 'string') next.captureState = data.captureState;
        // Same rule as the old engine: preview only while the station is online and idle.
        next.allowed =
          typeof data.remotePreviewAllowed === 'boolean'
            ? next.remotePreview && data.remotePreviewAllowed
            : next.remotePreview && next.online && next.captureState === 'idle';
        if (data.mode === 'preview' || data.mode === 'dataset') next.mode = data.mode;
        return next;
      });
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  return { theme, live };
}

/** Why 实时 is unavailable, as the old engine worded it; empty when preview is allowed. */
export function previewBlockedLabel(live) {
  if (!live.remotePreview) return '远程实时未启用';
  if (!live.online) return '采集离线';
  if (live.captureState !== 'idle') return '采集中远程实时已禁用';
  return live.allowed ? '' : '远程实时不可用';
}
