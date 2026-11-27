// Thin host around the unmodified @ioai/lerobot-studio package (MIT).
// Customization only through its public props (showSidebar, dataSource, episodeScope, themeTokens)
// plus the host's own sidebar; no bundle patching, no DOM injection.
import { StrictMode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { LeRobotViewer, createRemoteManifestDataSource } from '@ioai/lerobot-studio';
import '@ioai/lerobot-studio/style.css';
import './theme.css';
import { Sidebar } from './Sidebar.jsx';
import { loadSidebarModel } from './episodes.js';

const API_BASE = '/lerobot/api';
const FONT_SANS =
  "ui-sans-serif, system-ui, sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol', 'Noto Color Emoji'";

// ?url= accepts the same values the /collection and /data shells already pass:
// stream://<id>, sample://<id>, /lerobot/api/stream/<id>/, /lerobot/api/sample/<id>/dataset/
function parseSource(raw) {
  const m = /^(stream|sample):\/\/([\w.-]+)\/?$/.exec(raw || '');
  if (m) return { kind: m[1], id: m[2] };
  const s = /^\/lerobot\/api\/stream\/([\w.-]+)\/?$/.exec(raw || '');
  if (s) return { kind: 'stream', id: s[1] };
  const d = /^\/lerobot\/api\/sample\/([\w.-]+)\/dataset\/?$/.exec(raw || '');
  if (d) return { kind: 'sample', id: d[1] };
  return null;
}

function sourceRoot({ kind, id }) {
  return kind === 'stream' ? `${API_BASE}/stream/${id}` : `${API_BASE}/sample/${id}/dataset`;
}

async function fetchFiles(root) {
  const res = await fetch(`${root}/files.json`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`files.json HTTP ${res.status}`);
  const { files } = await res.json();
  return files.map((f) => ({
    logicalPath: f.path,
    presignedUrl: new URL(`${root}/${f.path}`, window.location.origin).href,
    sizeBytes: f.size,
  }));
}

// Changes when episodes are added or removed (meta/ is rewritten on every ingest or delete).
const LIVE_POLL_MS = 10000;
const filesSignature = (files) =>
  files
    .filter((f) => f.logicalPath.startsWith('meta/'))
    .map((f) => `${f.logicalPath}:${f.sizeBytes}`)
    .sort()
    .join('|');

function App() {
  const params = new URLSearchParams(window.location.search);
  const [src] = useState(() => parseSource(params.get('url')));
  const [state, setState] = useState({ files: null, model: null, error: null, root: null });
  const [selected, setSelected] = useState(null);
  const sigRef = useRef('');

  const load = useCallback(
    async (files) => {
      const root = sourceRoot(src);
      files ||= await fetchFiles(root);
      const model = await loadSidebarModel({
        files,
        infoUrl: `${root}/meta/info.json`,
        datesUrl: src.kind === 'stream' ? `${root}/episode-dates.json` : null,
      });
      sigRef.current = filesSignature(files);
      setState({ files, model, error: null, root });
      // Old viewer opened the lowest episode_index first; keep the selection while it still exists.
      const indices = model.episodes.map((e) => e.episodeIndex);
      setSelected((cur) => (cur != null && indices.includes(cur) ? cur : indices.length ? Math.min(...indices) : null));
    },
    [src],
  );

  useEffect(() => {
    if (!src) return setState((s) => ({ ...s, error: 'Missing or unsupported ?url=' }));
    load().catch((e) => setState((s) => ({ ...s, error: String(e.message || e) })));
  }, [src, load]);

  // Live list: a station keeps recording while the page is open (old engine: stream-live-poll.js).
  useEffect(() => {
    if (src?.kind !== 'stream' || !state.root) return undefined;
    let busy = false;
    const timer = setInterval(async () => {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const files = await fetchFiles(state.root);
        if (filesSignature(files) !== sigRef.current) await load(files);
      } catch {
        /* transient: next tick retries */
      } finally {
        busy = false;
      }
    }, LIVE_POLL_MS);
    return () => clearInterval(timer);
  }, [src, state.root, load]);

  // One episode per data source: the host sidebar drives selection through the public episodeScope option.
  const dataSource = useMemo(() => {
    if (!state.files || selected == null) return null;
    return createRemoteManifestDataSource(state.files, {
      episodeScope: [selected],
      refresh: () => fetchFiles(state.root),
    });
  }, [state.files, state.root, selected]);

  if (state.error) return <pre style={{ padding: 16 }}>{state.error}</pre>;
  if (!state.model) return <div className="dl-root h-full" />;
  return (
    <div className="dl-root flex h-full min-h-0 w-full overflow-hidden bg-background">
      <Sidebar
        model={state.model}
        selected={selected}
        onSelect={setSelected}
        stationId={src.kind === 'stream' ? src.id : null}
        onChanged={() => load()}
      />
      <div className="h-full min-w-0 flex-1">
        {dataSource && (
        <LeRobotViewer
          dataSource={dataSource}
          showSidebar={false}
          theme="system"
          language={params.get('lang') || 'zh'}
          themeTokens={{ radius: '0.625rem', fontFamily: FONT_SANS }}
          onFatalError={(e) => console.error('viewer fatal', e)}
        />
        )}
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
