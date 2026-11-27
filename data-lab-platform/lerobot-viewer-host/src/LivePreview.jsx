// 实时 mode: the station's current camera frames as JPG snapshots, polled like the old engine
// (overlay-collection-mode.js): wake the preview once, then every SNAPSHOT_MS fetch each camera,
// staggered by STAGGER_MS, keeping the last good frame when a fetch fails.
import { useEffect, useState } from 'react';

const SNAPSHOT_MS = 500;
const STAGGER_MS = 100;
const CAMERA_PREFIX = 'observation.images.camera_';
// Ego stations' cameras, used when info.json declares none with the prefix above.
const DEFAULT_CAMS = ['front_left', 'front_right', 'rear_left', 'rear_right'];

/** Preview camera ids (`front_left`, …) from the dataset's video features. */
export function previewCameras(info) {
  const cams = Object.keys(info?.features || {})
    .filter((k) => k.startsWith(CAMERA_PREFIX))
    .map((k) => k.slice(CAMERA_PREFIX.length));
  return cams.length ? cams : DEFAULT_CAMS;
}

const stationApi = (stationId) => `/lerobot/api/collection/stations/${encodeURIComponent(stationId)}`;

export function LivePreview({ stationId, cameras, blockedLabel }) {
  const [frames, setFrames] = useState({});

  useEffect(() => {
    if (blockedLabel) return undefined;
    let stopped = false;
    const urls = {};
    const timers = new Set();
    const later = (fn, ms) => {
      const t = setTimeout(() => {
        timers.delete(t);
        fn();
      }, ms);
      timers.add(t);
    };
    const loading = new Set();

    const load = async (cam) => {
      if (loading.has(cam)) return;
      loading.add(cam);
      try {
        const res = await fetch(`${stationApi(stationId)}/preview/${encodeURIComponent(cam)}/jpg?t=${Date.now()}`, {
          cache: 'no-store',
        });
        if (!res.ok) throw new Error(`preview ${res.status}`);
        const blob = await res.blob();
        if (!blob.size || stopped) return;
        const next = URL.createObjectURL(blob);
        if (urls[cam]) URL.revokeObjectURL(urls[cam]);
        urls[cam] = next;
        setFrames((cur) => ({ ...cur, [cam]: next }));
      } catch {
        /* keep the last good frame */
      } finally {
        loading.delete(cam);
      }
    };
    const cycle = () => {
      if (stopped) return;
      cameras.forEach((cam, i) => later(() => !stopped && load(cam), i * STAGGER_MS));
      later(cycle, SNAPSHOT_MS);
    };

    fetch(`${stationApi(stationId)}/preview/wake`, { cache: 'no-store' }).catch(() => {});
    cycle();
    return () => {
      stopped = true;
      timers.forEach(clearTimeout);
      Object.values(urls).forEach((u) => URL.revokeObjectURL(u));
      setFrames({});
    };
  }, [stationId, cameras, blockedLabel]);

  if (blockedLabel) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground" role="status">
        {blockedLabel}
      </div>
    );
  }
  return (
    <div className="dl-live grid h-full min-h-0 gap-1 bg-black p-1" data-count={cameras.length}>
      {cameras.map((cam) => (
        <figure key={cam} className="relative m-0 flex min-h-0 min-w-0 items-center justify-center overflow-hidden">
          {frames[cam] ? (
            <img src={frames[cam]} alt={`实时 ${cam}`} className="h-full w-full object-contain" />
          ) : (
            <span className="text-xs text-white/60">等待画面…</span>
          )}
          <figcaption className="absolute top-1 left-1 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[10px] text-white">
            camera_{cam}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}
