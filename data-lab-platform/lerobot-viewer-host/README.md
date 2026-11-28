# lerobot-viewer-host

Thin React host around the **unmodified** [`@ioai/lerobot-studio`](https://github.com/ioai-tech/lerobot-studio)
npm package (MIT, pinned `1.7.2`). Replaces the old flow (download a 2026-05 prebuilt bundle from
io-ai.tech, then sed-patch it and inject ~10k lines of runtime overlay).

Rule: customize only through the package's public props. No bundle patching, no DOM injection.

## Run (isolated, does not touch the live :8080 stack)

```bash
npm --prefix data-lab-platform/lerobot-viewer-host install
npm --prefix data-lab-platform/lerobot-viewer-host run dev   # http://localhost:7870/lerobot/?url=stream://ego-002
```

Dev server proxies `/lerobot/api/*` to the platform (`DATALAB_PLATFORM_URL`, default `http://10.10.10.34:8080`).
`/lerobot/api/stream/<id>/files.json` is served by a dev-only Vite middleware until `server.mjs` gets the same route.

Source URLs: `?url=stream://<station>`, `?url=sample://<id>`, or `?url=https://.../dataset.zip`.

## Verified (2026-11-23, ego-002, 75 episodes)

- Episode list, 4 × 1280×800 H.264 cameras playing in sync, charts, raw frame panel: OK.
- Parquet read via HTTP Range against the existing `/lerobot/api/stream/...` route: OK (route already returns 206).
- Episodes stored in `data/chunk-000/file-000.parquet` and `file-040.parquet` fail with
  `Column observation.imu_accel not found in table`: those two files lack the three `observation.imu_*`
  columns that `meta/info.json` declares. This is a data defect (LeRobot v3 requires one schema across
  files), not a viewer issue; the old patched viewer only hid it.

## Migration map: old patch/overlay → official API

| Old (lerobot-studio/patches, branding/) | Replacement |
|---|---|
| `apply-branding.sh`, `overlay.js/css` (strings, logo, languages) | `messages`, `language`, `themeTokens` props; own `index.html` |
| `stream-http-source.js`, `patch-lerobot-stream.mjs` | `createRemoteManifestDataSource` + `files.json` (done here) |
| `patch-lerobot-manifest.mjs`, `patch-lerobot-sample-missing.mjs`, `manifest-embed-fix.js` | Host decides the source; no sample manifest needed |
| `stream-live-poll.js` | `refresh` option, or re-create the data source on new episodes |
| `overlay-video-sync`, `overlay-camera-layout*`, `patch-dockview-scalar-chart.sh`, `patch-playback-mode.sh` | Native in 1.7.0; drop |
| `overlay-episode-delete*` | `extensions` toolbar button → existing delete API |
| `overlay-import*` | `DatasetSourceSelector` / `useDragAndDrop` custom welcome page |
| `overlay-hand-keypoints*`, `overlay-hand-render-rich*`, `overlay-depth-preview*` | No video-overlay API upstream. Render as a derived video rendition and switch with an `extensions` toggle (upstream's documented rendition pattern), or request the feature upstream |
| `overlay-episode-groups*` | No upstream equivalent; drop or request upstream |
| QC issues (`lerobot-qc`) | `markers` (feed the built-in Issues filter) |
| Subtask annotation (`embodied-annotate`) | Candidate: `annotation` + `fields` props (segments, review, undo) |

## Parallel deployment (live since 2026-11-23, old engine stays default)

- `server.mjs` serves `files.json` for stream/sample datasets and this build from `/srv/lerobot/next` at `/lerobot/next/`.
- Per-browser switch, shell URL unchanged:
  - on: `http://10.10.10.34:8080/lerobot/next/enable?next=/collection?station=ego-002`
  - off: `http://10.10.10.34:8080/lerobot/next/disable?next=/collection?station=ego-002`
- Deploy a new build (no container recreate, no restart): `bash data-lab-platform/lerobot-viewer-host/tools/deploy-next.sh`
- Episode selection: the host sidebar passes a new data source with `episodeScope: [selected]`
  (the package has no imperative "select episode" API).

## Pixel parity gaps vs baseline (1440×900, ego-002 replay)

Compare `baseline/collection-ego-002-replay-*.jpg` (old) with `baseline/next-1.7.0-ego-002-replay.jpg` (new).

| # | Area | Old | New (1.7.0 baseline) | Path to parity |
|---|---|---|---|---|
| 1 | Sidebar header | `任务` / `分析` tabs, `75 Episodes`, 多选 · 编辑 · 导入 | `75 个片段`, 多选 · 编辑 | **Done** (`src/Sidebar.jsx`); 多选 / 编辑 / 导入 wired (row 10) |
| 2 | Episode list | Collapsible date groups (11月4日 / 11月8日), time of day in label | Flat list, `# n`, no time | **Done**; search, 高级 duration range (seconds, `src/filter.js`, same rule as the old build) |
| 3 | Analysis tab | Totals, duration stats, task distribution | Not in sidebar | **Done** |

Rows 1–3 verified 2026-11-24 with `tools/measure-sidebar.js` on both engines (1440×900): every landmark
(tabs, title, toolbar, search, group headers, rows, selected row, 20 analysis values) has the same
x / y / width / height, font size / weight and color. The old sidebar was upstream's standalone-app
`SidebarTabs` (not exported by the npm package), so it is replicated from that DOM, with the old
slate palette restored in `src/theme.css` for both the sidebar and the viewer.
| 4 | Camera layout | 4 cameras in one row, dark letterbox | 2×2 grid | No prop; upstream feature request or fork |
| 5 | Chart | `已选 205`, 指令 / 状态 toggles | `已选 54`, per-axis legend chips | **Done upstream** in 1.7.2: one plot, every dimension drawn by default, filter menu instead of legend chips |
| 6 | Raw panel | Numeric fields first | Video fields first | Upstream ordering; feature request |
| 7 | Autoplay | Plays on open, "自动播放已开启" notice | Paused | Not possible in 1.7.2: no play prop or imperative player API (`initialSelection` lands paused); upstream feature request |
| 8 | Episode 0 | Plays | `Column observation.imu_accel not found` | Data defect: fix `file-000`/`file-040` schema |
| 9 | Hand keypoints, depth preview | Video overlays | None | Derived rendition + `extensions` toggle |
| 10 | Episode delete, import, live episode list | Overlays | Host sidebar | **Done** (`src/station.js`): 编辑 = per-row delete, 多选 = bulk delete, same API and confirm copy as `overlay-episode-delete.js`; 导入 opens the platform modal (`/_datalab/import-core.js`); `files.json` polled every 10 s, list reloads when `meta/` changes |
| 11 | 实时 mode (JPG preview) | `overlay-collection-mode.js` | Host panel | **Done** (`src/bridge.js`, `src/LivePreview.jsx`): the /collection shell's `datalab-collection-mode` message swaps the viewer for a camera grid polled every 500 ms (100 ms stagger, `preview/wake` first); same online + idle rule, blocked reason shown otherwise |
| 12 | Light / dark from the shell | `overlay-collection-theme.js` | Followed the system | **Done** (`src/bridge.js`): `datalab-lerobot-theme` sets `html[data-theme]` for sidebar and viewer and writes `localStorage.theme` for the shell |

## Still to do before replacing the old engine

1. Close every row above (or accept it explicitly).
2. Build this host in the lerobot image and serve it at `/lerobot/` instead of `download-assets.sh` output.
3. Remove `download-assets.sh`, `patches/`, and the replaced `branding/` overlays.
4. Replace the four io-ai sample datasets (unlicensed for redistribution).
