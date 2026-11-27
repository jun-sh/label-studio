// Replica of the previous Data Viewer sidebar (upstream standalone app "Episodes / Analysis" tabs,
// 2026-05 build, plus the Data Lab date groups). Class names are copied from that build's DOM
// so Tailwind reproduces the same pixels; see README "Pixel parity".
import { useMemo, useState } from 'react';
import {
  Activity,
  Bot,
  ChartColumn,
  ChartPie,
  Clock,
  Cpu,
  Database,
  Frame,
  List,
  Pencil,
  Search,
  Download,
  SlidersHorizontal,
  Square,
  SquareCheckBig,
  Trash2,
} from 'lucide-react';
import { formatClock } from './episodes.js';
import { confirmDeleteText, deleteEpisodes, openImport } from './station.js';

const cx = (...c) => c.filter(Boolean).join(' ');
const COLLAPSE_KEY = 'datalab-episode-date-collapsed';

const TAB =
  'inline-flex items-center justify-center whitespace-nowrap py-1.5 font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-none border-b-2 min-w-0 gap-1.5 overflow-hidden px-2 text-xs';
// `[&_svg]:size-4` came from the old shadcn Button base: it overrides the icons' own h-3 w-3.
const TOOL_BTN =
  'inline-flex items-center justify-center gap-2 whitespace-nowrap ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 hover:bg-accent rounded-md h-7 shrink-0 px-1 text-xs font-medium text-muted-foreground hover:text-foreground';

function readCollapsed() {
  try {
    return JSON.parse(sessionStorage.getItem(COLLAPSE_KEY) || '{}') || {};
  } catch {
    return {};
  }
}

export function Sidebar({ model, selected, onSelect, stationId, onChanged }) {
  const [tab, setTab] = useState('episodes');
  return (
    <div className="flex h-full w-64 min-w-0 shrink-0 flex-col overflow-x-hidden overflow-hidden border-r border-border">
      <div className="flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden">
        <div className="w-full min-w-0 shrink-0 overflow-x-hidden px-2 py-2">
          <div
            role="tablist"
            className="grid h-auto w-full min-w-0 shrink-0 grid-cols-2 items-center justify-center border-b border-border text-muted-foreground"
          >
            {[
              ['episodes', List, '任务'],
              ['analysis', ChartColumn, '分析'],
            ].map(([id, Icon, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={cx(
                  TAB,
                  tab === id ? 'border-primary text-foreground' : 'border-transparent opacity-70',
                )}
              >
                <Icon className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">{label}</span>
              </button>
            ))}
          </div>
        </div>
        <div role="tabpanel" className="mt-0 min-h-0 min-w-0 flex-1 overflow-hidden">
          {tab === 'episodes' ? (
            <EpisodesPanel
              model={model}
              selected={selected}
              onSelect={onSelect}
              stationId={stationId}
              onChanged={onChanged}
            />
          ) : (
            <AnalysisPanel model={model} />
          )}
        </div>
      </div>
    </div>
  );
}

function EpisodesPanel({ model, selected, onSelect, stationId, onChanged }) {
  const [query, setQuery] = useState('');
  // Edit = per-row delete buttons; multi = checkboxes + bulk delete. Station sources only.
  const [mode, setMode] = useState(null);
  const [picked, setPicked] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const canEdit = Boolean(stationId);

  const showToast = (text, kind = 'ok') => {
    setToast({ text, kind });
    setTimeout(() => setToast((t) => (t && t.text === text ? null : t)), 3200);
  };
  const switchMode = (next) => {
    setMode((cur) => (cur === next ? null : next));
    setPicked(new Set());
  };
  const togglePick = (idx) =>
    setPicked((cur) => {
      const next = new Set(cur);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  const runDelete = async (indices) => {
    if (busy || !indices.length || !window.confirm(confirmDeleteText(indices))) return;
    setBusy(true);
    const list = indices.map((n) => `#${n}`).join(', ');
    try {
      await deleteEpisodes(stationId, indices);
      setPicked(new Set());
      showToast(`删除成功：${list} 已移除，正在更新列表…`);
      await onChanged();
    } catch (e) {
      showToast(`删除失败：${e.message}`, 'error');
    } finally {
      setBusy(false);
    }
  };
  const startImport = () =>
    openImport(stationId, () => onChanged()).catch(() => showToast('导入组件加载失败', 'error'));
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const fps = model.info.fps;
  const q = query.trim().toLowerCase();
  const groups = useMemo(
    () =>
      model.groups
        .map((g) => ({
          ...g,
          rows: q
            ? g.rows.filter((r) => r.label.toLowerCase().includes(q) || `# ${r.episodeIndex}`.includes(q))
            : g.rows,
        }))
        .filter((g) => g.rows.length),
    [model.groups, q],
  );
  // Old overlay default: every group collapsed except the last one; user choice kept per tab session.
  const isCollapsed = (date, i) =>
    collapsed[date] != null ? collapsed[date] : groups.length > 1 && i < groups.length - 1;
  const toggle = (date, i) => {
    const next = { ...collapsed, [date]: !isCollapsed(date, i) };
    setCollapsed(next);
    try {
      sessionStorage.setItem(COLLAPSE_KEY, JSON.stringify(next));
    } catch {
      /* storage unavailable */
    }
  };

  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden bg-background">
      <div className="min-w-0 shrink-0 space-y-3 overflow-x-hidden border-b border-border px-3 pt-0 pb-2">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 overflow-hidden">
          <h2 className="flex min-w-0 items-center gap-1 overflow-hidden text-sm font-normal text-muted-foreground">
            <span>{model.rows.length}</span>
            <span>Episodes</span>
          </h2>
          <div className="flex min-w-0 flex-wrap items-center gap-1 overflow-hidden">
            <button
              type="button"
              className={cx(TOOL_BTN, mode === 'multi' && 'text-foreground')}
              title="多选"
              aria-pressed={mode === 'multi'}
              disabled={!canEdit}
              onClick={() => switchMode('multi')}
            >
              <SquareCheckBig className="h-3 w-3 shrink-0" />
              {mode === 'multi' ? '取消' : '多选'}
            </button>
            <button
              type="button"
              className={cx(TOOL_BTN, mode === 'edit' && 'text-foreground')}
              title="编辑 Episodes"
              aria-pressed={mode === 'edit'}
              disabled={!canEdit}
              onClick={() => switchMode('edit')}
            >
              <Pencil className="mr-1 h-3 w-3 shrink-0" />
              {mode === 'edit' ? '完成' : '编辑'}
            </button>
            <button type="button" className={TOOL_BTN} title="导入" disabled={!canEdit} onClick={startImport}>
              <Download className="mr-1 h-3 w-3 shrink-0" />
              导入
            </button>
          </div>
        </div>
        <div className="group relative flex min-w-0 items-center gap-1 overflow-hidden">
          <div className="relative min-w-0 flex-1 basis-0">
            <Search className="absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/40 transition-colors group-focus-within:text-primary/50" />
            <input
              className="flex h-9 w-full min-w-0 rounded-md border border-transparent bg-muted/50 px-3 py-1 pl-9 text-xs text-foreground shadow-sm transition-colors placeholder:text-muted-foreground/40 focus-visible:border-primary focus-visible:outline-none"
              placeholder="搜索..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          {/* Old layout squeezed this h-9 w-9 button to 16px wide (search field is flex-1 basis-0). */}
          <button
            type="button"
            className="inline-flex h-9 w-4 shrink-0 items-center justify-center gap-2 rounded-md p-0 text-sm font-medium whitespace-nowrap text-foreground ring-offset-background transition-colors hover:bg-accent hover:text-accent-foreground [&_svg]:size-4"
            title="高级"
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      <div className="dl-scroll min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
        {groups.map((g, i) => {
          const folded = isCollapsed(g.date, i);
          return (
            <div key={g.date}>
              <button
                type="button"
                className="dl-date-header"
                data-collapsed={folded ? '1' : '0'}
                aria-expanded={!folded}
                onClick={() => toggle(g.date, i)}
              >
                <span className="dl-date-chevron">▾</span>
                <span className="dl-date-label">{g.label}</span>
                <span className="dl-date-count"> · {g.rows.length}条</span>
              </button>
              {!folded &&
                g.rows.map((r) => (
                  <EpisodeRow
                    key={r.episodeIndex}
                    row={r}
                    fps={fps}
                    active={r.episodeIndex === selected}
                    onSelect={mode === 'multi' ? togglePick : onSelect}
                    mode={mode}
                    picked={picked.has(r.episodeIndex)}
                    busy={busy}
                    onDelete={() => runDelete([r.episodeIndex])}
                  />
                ))}
            </div>
          );
        })}
      </div>
      {mode === 'multi' && (
        <div className="flex min-w-0 shrink-0 items-center justify-between gap-2 border-t border-border px-3 py-2 text-xs">
          <span className="text-muted-foreground">已选 {picked.size} 项</span>
          <button
            type="button"
            className="inline-flex h-7 items-center gap-1 rounded-md bg-destructive px-2 font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
            disabled={busy || picked.size === 0}
            onClick={() => runDelete([...picked].sort((a, b) => a - b))}
          >
            <Trash2 className="h-3.5 w-3.5" />
            删除所选
          </button>
        </div>
      )}
      {toast && (
        <div role="status" className="dl-toast" data-kind={toast.kind}>
          {toast.text}
        </div>
      )}
    </div>
  );
}

function EpisodeRow({ row, fps, active, onSelect, mode, picked, busy, onDelete }) {
  const duration = formatClock(row.length, fps);
  return (
    <div
      className={cx(
        'group relative flex w-full min-w-0 flex-col overflow-hidden border-b border-border/30 text-left transition-colors',
        active
          ? 'bg-accent text-foreground'
          : 'bg-transparent text-muted-foreground/80 hover:bg-accent/50 hover:text-foreground',
      )}
    >
      <div
        role="button"
        tabIndex={0}
        aria-current={active ? 'true' : undefined}
        title={`选择 Episode ${row.episodeIndex}`}
        onClick={() => onSelect(row.episodeIndex)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onSelect(row.episodeIndex);
        }}
        className="flex w-full min-w-0 flex-1 flex-row items-center gap-2 overflow-hidden rounded-sm px-3 py-2.5 text-left focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none"
      >
        {mode === 'multi' &&
          (picked ? (
            <SquareCheckBig className="h-4 w-4 shrink-0 text-primary" aria-label="已选" />
          ) : (
            <Square className="h-4 w-4 shrink-0 text-muted-foreground" aria-label="未选" />
          ))}
        {active && <div className="absolute top-2 bottom-2 left-0 w-1 rounded-r-full bg-primary" />}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <div className="mb-1 flex min-w-0 items-center justify-between gap-2 overflow-hidden">
            <div
              className={cx(
                'inline-flex h-4 shrink-0 items-center rounded-md border px-1.5 py-0.5 font-mono text-[10px] font-medium transition-colors',
                active
                  ? 'border-transparent bg-primary text-primary-foreground hover:bg-primary/80'
                  : 'border-border text-foreground opacity-70',
              )}
            >
              # {row.episodeIndex}
            </div>
            <span
              className={cx(
                'min-w-0 truncate font-mono text-[11px] transition-colors',
                active ? 'font-medium text-foreground/80' : 'text-muted-foreground/60',
              )}
              title={duration}
            >
              {duration}
            </span>
          </div>
          <span
            className={cx(
              'block truncate text-xs leading-snug transition-colors',
              active ? 'font-medium text-foreground' : 'font-normal text-muted-foreground/90',
            )}
            title={row.label}
          >
            {row.label}
          </span>
        </div>
        {mode === 'edit' && (
          <button
            type="button"
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
            title={`删除 Episode ${row.episodeIndex}`}
            aria-label={`删除 Episode ${row.episodeIndex}`}
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

function formatDuration(sec) {
  return sec >= 60 ? `${(sec / 60).toFixed(1)} 分钟` : `${sec.toFixed(1)} 秒`;
}

function Section({ icon: Icon, title, children }) {
  return (
    <section className="min-w-0 space-y-1.5">
      <h3 className="flex min-w-0 items-center gap-2 text-xs font-semibold tracking-wider text-muted-foreground uppercase">
        <Icon className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">{title}</span>
      </h3>
      {children}
    </section>
  );
}

const Divider = () => <div role="none" className="h-[1px] w-full shrink-0 bg-border" />;

function AnalysisPanel({ model }) {
  const { info, episodes } = model;
  const fps = info.fps || 30;
  const lengths = episodes.map((e) => e.length).filter((n) => n > 0);
  const totalFrames = lengths.reduce((a, b) => a + b, 0);
  const len = lengths.length
    ? { min: Math.min(...lengths), max: Math.max(...lengths), avg: Math.round(totalFrames / lengths.length) }
    : { min: 0, max: 0, avg: 0 };
  const avgPos = len.max > len.min ? ((len.avg - len.min) / (len.max - len.min)) * 100 : 50;
  const overview = [
    [Database, '集数', String(info.total_episodes ?? episodes.length)],
    [Frame, '总帧数', Number(info.total_frames ?? totalFrames).toLocaleString('en-US')],
    [Activity, 'FPS', String(fps)],
    [Cpu, '格式版本', String(info.codebase_version ?? '')],
    [Bot, '机器人类型', String(info.robot_type ?? '')],
  ];
  const durations = [
    ['总时长', formatDuration(totalFrames / fps), true],
    ['最小', formatDuration(len.min / fps)],
    ['平均/集', formatDuration(totalFrames / fps / (lengths.length || 1))],
    ['最大', formatDuration(len.max / fps)],
  ];
  const taskCounts = new Map();
  for (const e of episodes) taskCounts.set(e.task, (taskCounts.get(e.task) || 0) + 1);

  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden bg-background">
      <div className="dl-scroll min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
        <div className="flex w-full min-w-0 flex-col space-y-6 px-3 py-3">
          <Section icon={ChartColumn} title="总览">
            <div className="min-w-0 overflow-hidden rounded border border-border/40">
              {overview.map(([Icon, label, value], i) => (
                <div
                  key={label}
                  className={cx(
                    'flex min-w-0 items-center justify-between gap-3 px-2.5 py-2',
                    i < overview.length - 1 && 'border-b border-border/30',
                  )}
                >
                  <span className="flex min-w-0 shrink items-center gap-1.5 text-[11px] text-muted-foreground">
                    <Icon className="h-3 w-3 shrink-0" />
                    <span className="truncate">{label}</span>
                  </span>
                  <span
                    className="max-w-[55%] min-w-0 shrink-0 truncate text-right font-mono text-xs font-semibold text-foreground"
                    title={value}
                  >
                    {value}
                  </span>
                </div>
              ))}
            </div>
          </Section>
          <Divider />
          <Section icon={Clock} title="时长">
            <div className="min-w-0 overflow-hidden rounded border border-border/40">
              {durations.map(([label, value, hi], i) => (
                <div
                  key={label}
                  className={cx(
                    'flex min-w-0 items-center justify-between gap-3 px-2.5 py-2',
                    i < durations.length - 1 && 'border-b border-border/30',
                    hi && 'bg-primary/5',
                  )}
                >
                  <span className={cx('truncate text-[11px]', hi ? 'font-medium text-primary' : 'text-muted-foreground')}>
                    {label}
                  </span>
                  <span
                    className={cx('shrink-0 font-mono text-xs font-semibold', hi ? 'text-primary' : 'text-foreground')}
                    title={value}
                  >
                    {value}
                  </span>
                </div>
              ))}
            </div>
            <div className="min-w-0 space-y-1.5 overflow-hidden rounded border border-border/40 px-2.5 py-2">
              <div className="flex min-w-0 items-center justify-between text-[10px] font-medium text-muted-foreground">
                <span>最小</span>
                <span>平均</span>
                <span>最大</span>
              </div>
              <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div className="absolute inset-0 rounded-full bg-primary/30" />
                <div
                  className="absolute top-0 h-full w-1.5 -translate-x-1/2 rounded-full bg-primary"
                  style={{ left: `${avgPos}%` }}
                />
              </div>
              <div className="flex min-w-0 items-center justify-between font-mono text-xs">
                <span className="text-muted-foreground">{len.min}</span>
                <span className="font-semibold">{len.avg}</span>
                <span className="text-muted-foreground">{len.max}</span>
              </div>
              <div className="text-center text-[9px] tracking-wider text-muted-foreground uppercase">帧</div>
            </div>
          </Section>
          <Divider />
          <Section icon={ChartPie} title="任务分布">
            <div className="flex min-w-0 flex-col gap-1.5">
              {[...taskCounts].map(([task, count]) => {
                const pct = ((count / (episodes.length || 1)) * 100).toFixed(1);
                return (
                  <div
                    key={task}
                    className="min-w-0 space-y-1.5 overflow-hidden rounded border border-border/40 px-2.5 py-2"
                  >
                    <div className="flex min-w-0 items-start justify-between gap-3">
                      <span
                        className="min-w-0 flex-1 truncate text-xs leading-tight font-medium text-foreground/90"
                        title={task}
                      >
                        {task}
                      </span>
                      <div className="flex shrink-0 flex-col items-end gap-0.5 text-right">
                        <span className="font-mono text-xs font-semibold">{count}</span>
                        <span className="font-mono text-[10px] text-muted-foreground">{pct}%</span>
                      </div>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted/80">
                      <div className="h-full rounded-full bg-primary/60 transition-all" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}
