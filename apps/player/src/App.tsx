import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { open, save } from "@tauri-apps/plugin-dialog";
import lyricRailMark from "../../../assets/brand/lyricrail-mark.svg";
import "./App.css";
import { Icon, IconButton } from "./Icon";
import {
  LyricOverlay,
  type KaraokePresentation,
  type RenderEvent,
} from "./LyricOverlay";
import {
  activeProcessingTasksByItem,
  adjacentReadyItem,
  issueForLibraryItem,
  sourceDisplayLabel,
  type CatalogSnapshot,
  type LibraryItem,
  shuffledReadyItem,
  visibleRange,
} from "./library";
import {
  clampVolume,
  formatTime,
  playbackStartTime,
  shouldResyncVideo,
  toggleDocumentFullscreen,
  toggleMutedVolume,
} from "./playback";
import { dispatchCommand, type CommandHandlers } from "./commands";
import { FOCUSABLE, useFocusContainment } from "./focus";
import {
  compactFriendlyOutput,
  friendlyOutputText,
  latestModelTransferProgress,
  outputStageLabel,
} from "./modelProgress";
import {
  EMPTY_TASK_STATE,
  applyTaskSnapshot,
  applyTaskUpdate,
  elapsedSeconds,
  filterTaskOutput,
  formatTaskDuration,
  mergeOutputSnapshot,
  normalizeTaskRecord,
  taskOutputNeedsReplay,
  visibleTasks,
  type OutputStream,
  type TaskClientState,
  type TaskOutputLine,
  type TaskOutputSnapshot,
  type TaskRecord,
  type TaskRuntimeUpdate,
  type TaskSnapshot,
} from "./tasks";
import {
  clientIssue,
  mergeIssueSources,
  shouldShowIssueNotice,
  upsertIssue,
  type IssueAction,
  type SystemIssue,
} from "./issues";
import {
  formatTimecodeMillis,
  loopedPreviewTime,
  nudgedTimecode,
  shouldOpenClipEditor,
  validateClipRange,
} from "./clipSelection";

type AudioTrack = { id: string; name: string; url: string; default: boolean };
type OpenPackage = {
  packageId: string;
  metadata: Record<string, unknown>;
  renderPlan: { events?: RenderEvent[] };
  presentation: KaraokePresentation;
  media: { videoUrl: string; audioTracks: AudioTrack[] };
};
type PlayerStatus = {
  version: string;
  platform: string;
  vaultAvailable: boolean;
  processing: { pendingJobs: number; runtimeAvailable: boolean; runtimeError?: string };
};
type LocalClipPreview = {
  clipId: string;
  suggestedTitle: string;
  sizeBytes: number;
  durationMillis: number;
  frameDurationMillis?: number;
  previewUrl: string;
};
const EMPTY_CATALOG: CatalogSnapshot = { items: [], localSources: [], driveSources: [] };
const ROW_HEIGHT = 104;

function hasNativeBridge(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function Thumbnail({ item }: { item: LibraryItem }) {
  const [source, setSource] = useState<string>();
  useEffect(() => {
    let disposed = false;
    if (!item.hasThumbnail || !hasNativeBridge()) return;
    invoke<string | null>("load_item_thumbnail", { itemId: item.id })
      .then((value) => { if (!disposed && value) setSource(value); })
      .catch(() => undefined);
    return () => { disposed = true; };
  }, [item.id, item.hasThumbnail]);
  if (source) return <img className="song-thumbnail" src={source} alt="" />;
  return (
    <div className="song-thumbnail thumbnail-fallback" aria-hidden="true">
      <span>{item.firstLyricLine || "No lyric preview"}</span>
    </div>
  );
}

type DrawerProps = {
  open: boolean;
  items: LibraryItem[];
  catalog: CatalogSnapshot;
  tasksByItem: ReadonlyMap<string, TaskRecord>;
  selectedId?: string;
  currentId?: string;
  query: string;
  busy: boolean;
  blocked: boolean;
  onClose: () => void;
  onRescan: () => void;
  onQuery: (value: string) => void;
  onSelect: (item: LibraryItem) => void;
  onPlay: (item: LibraryItem) => void;
  onAddFiles: () => void;
  onAddFolder: () => void;
  onDrive: () => void;
  onLyricsFile: (item: LibraryItem) => void;
  onLyricsPaste: (item: LibraryItem) => void;
  onEditLyrics: (item: LibraryItem) => void;
  onRetry: (item: LibraryItem) => void;
  onShowContext: (item: LibraryItem) => void;
  onRemoveSource: (id: string) => void;
  onRecoveryExport: () => void;
  onRecoveryRestore: () => void;
};

export function LibraryDrawer(props: DrawerProps) {
  const viewport = useRef<HTMLDivElement>(null);
  const sourceActionsRef = useRef<HTMLDivElement>(null);
  const localTriggerRef = useRef<HTMLButtonElement>(null);
  const cloudTriggerRef = useRef<HTMLButtonElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(600);
  const [sourceMenu, setSourceMenu] = useState<"local" | "cloud">();
  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setHeight(entry.contentRect.height));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!props.open || props.blocked) setSourceMenu(undefined);
  }, [props.blocked, props.open]);
  useEffect(() => {
    if (!sourceMenu) return;
    sourceActionsRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    const closeMenu = (event: PointerEvent | KeyboardEvent) => {
      if (event.type === "keydown") {
        if ((event as KeyboardEvent).key !== "Escape") return;
        event.preventDefault();
        event.stopPropagation();
      }
      if (event.type === "pointerdown" && sourceActionsRef.current?.contains(event.target as Node)) return;
      setSourceMenu(undefined);
      if (event.type === "keydown") {
        (sourceMenu === "local" ? localTriggerRef.current : cloudTriggerRef.current)?.focus();
      }
    };
    document.addEventListener("pointerdown", closeMenu);
    document.addEventListener("keydown", closeMenu);
    return () => {
      document.removeEventListener("pointerdown", closeMenu);
      document.removeEventListener("keydown", closeMenu);
    };
  }, [sourceMenu]);
  const moveSourceMenuFocus = (event: React.KeyboardEvent<HTMLElement>) => {
    if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')];
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? items.length - 1
        : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    event.preventDefault();
    items[next]?.focus();
  };
  const range = visibleRange(props.items.length, scrollTop, height, ROW_HEIGHT);
  const visible = props.items.slice(range.start, range.end);
  return (
    <>
      <button className={`drawer-scrim ${props.open ? "shown" : ""}`} onClick={props.onClose} aria-label="Close library" tabIndex={props.open && !props.blocked ? 0 : -1} inert={props.blocked} />
      <aside id="library-drawer" className={`library-drawer ${props.open ? "open" : ""}`} aria-hidden={!props.open || props.blocked} inert={!props.open || props.blocked}>
        <header className="drawer-header">
          <div>
            <p className="eyebrow">Library & queue</p>
            <strong>{props.items.length} items</strong>
          </div>
          <div className="drawer-tools">
            <IconButton className="drawer-tool" icon="refresh" label="Rescan library sources" onClick={props.onRescan} />
            <IconButton className="drawer-tool" icon="close" label="Close library" onClick={props.onClose} />
          </div>
        </header>
        <div className="source-actions" ref={sourceActionsRef}>
          <div className="source-action-group">
            <button ref={localTriggerRef} aria-haspopup="menu" aria-expanded={sourceMenu === "local"} onClick={() => setSourceMenu((current) => current === "local" ? undefined : "local")} disabled={props.busy}>Local</button>
            {sourceMenu === "local" && <div className="source-menu" role="menu" aria-label="Local sources" onKeyDown={moveSourceMenuFocus}>
              <button role="menuitem" onClick={() => { setSourceMenu(undefined); props.onAddFiles(); }}>Files</button>
              <button role="menuitem" onClick={() => { setSourceMenu(undefined); props.onAddFolder(); }}>Folder</button>
            </div>}
          </div>
          <div className="source-action-group">
            <button ref={cloudTriggerRef} aria-haspopup="menu" aria-expanded={sourceMenu === "cloud"} onClick={() => setSourceMenu((current) => current === "cloud" ? undefined : "cloud")} disabled={props.busy}>Cloud</button>
            {sourceMenu === "cloud" && <div className="source-menu" role="menu" aria-label="Cloud providers" onKeyDown={moveSourceMenuFocus}>
              <button role="menuitem" onClick={() => { setSourceMenu(undefined); props.onDrive(); }}>Google Drive</button>
            </div>}
          </div>
        </div>
        <label className="search-box">
          <Icon name="search" size={18} />
          <input value={props.query} onChange={(event) => props.onQuery(event.target.value)} placeholder="Search title, artist, composer or lyrics" />
          {props.query && <IconButton className="search-clear" icon="close" iconSize={16} label="Clear library search" onClick={() => props.onQuery("")} />}
        </label>
        <div className="song-list" ref={viewport} onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}>
          {props.items.length === 0 ? (
            <div className="list-empty">
              <strong>No songs yet</strong>
              <span>Choose Local files or folders, or connect a Cloud provider.</span>
            </div>
          ) : (
            <div className="virtual-space" style={{ height: props.items.length * ROW_HEIGHT }}>
              {visible.map((item, index) => {
                const top = (range.start + index) * ROW_HEIGHT;
                const waiting = item.status === "waiting-for-lyrics";
                const playable = item.status === "ready" || item.status === "offline";
                const task = props.tasksByItem.get(item.id);
                const taskActive = task?.status === "queued" || task?.status === "running";
                const taskProgress = task?.progressPercent ?? task?.stageProgressPercent;
                const rowStatus = task && (taskActive || task.status === "failed" || task.status === "cancelled")
                  ? task.status
                  : item.status;
                return (
                  <article
                    className={`song-row ${props.selectedId === item.id ? "selected" : ""} ${props.currentId === item.id ? "current" : ""}`}
                    style={{ transform: `translateY(${top}px)` }}
                    key={item.id}
                    onClick={() => props.onSelect(item)}
                    onDoubleClick={() => playable && props.onPlay(item)}
                  >
                    <Thumbnail item={item} />
                    <div className="song-copy">
                      <strong>{item.title}</strong>
                      <span>{task?.stageTitle || task?.statusMessage || item.statusMessage || item.artist || item.composer || item.firstLyricLine || "Unknown artist"}</span>
                      {item.lyricSnippet && <em>“…{item.lyricSnippet}”</em>}
                      <div className="row-meta">
                        {item.sources.map((source) => <small key={source}>{sourceDisplayLabel(source)}</small>)}
                        <small className={`status ${rowStatus}`}>{rowStatus.replace(/-/g, " ")}</small>
                      </div>
                      {taskActive && (
                        <i className={`row-progress ${taskProgress === undefined ? "indeterminate" : ""}`}><b style={taskProgress === undefined ? undefined : { width: `${taskProgress}%` }} /></i>
                      )}
                    </div>
                    <div className="row-actions" onClick={(event) => event.stopPropagation()}>
                      {playable && <IconButton className="row-icon" icon="play" iconSize={17} label={`Play ${item.title}`} onClick={() => props.onPlay(item)} />}
                      {waiting && <button onClick={() => props.onLyricsPaste(item)}>Paste</button>}
                      {waiting && <button onClick={() => props.onLyricsFile(item)}>TXT</button>}
                      {item.status === "failed" && item.canProcess && <button onClick={() => props.onRetry(item)}>Retry</button>}
                      {(task || ["queued", "processing", "failed", "setup-required"].includes(item.status)) && <button onClick={() => props.onShowContext(item)}>{item.status === "failed" || item.status === "setup-required" ? "View issue" : "View task"}</button>}
                      {playable && item.sources.includes("Disk") && <IconButton className="row-icon" icon="edit" iconSize={16} label={`Edit lyrics for ${item.title}`} onClick={() => props.onEditLyrics(item)} />}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
        <footer className="drawer-footer">
          <p className="processing-note">Local processing uses clear temporary job files; source media is never deleted.</p>
          <div className="source-pills">
            {props.catalog.localSources.map((source) => (
              <span key={source.id}>Local <IconButton className="source-remove" icon="close" iconSize={13} label={`Remove local source ${source.path}`} onClick={() => props.onRemoveSource(source.id)} /></span>
            ))}
            {props.catalog.driveSources.map((source) => (
              <span key={source.id}>Cloud · {source.name} <IconButton className="source-remove" icon="close" iconSize={13} label={`Remove cloud source ${source.name}`} onClick={() => props.onRemoveSource(source.id)} /></span>
            ))}
          </div>
          <details>
            <summary>Recovery</summary>
            <button onClick={props.onRecoveryExport}>Export key bundle</button>
            <button onClick={props.onRecoveryRestore}>Restore on this device</button>
          </details>
        </footer>
      </aside>
    </>
  );
}

export function TaskOutputPane({ lines, truncated, onCopy }: {
  lines: TaskOutputLine[];
  truncated: boolean;
  onCopy: () => void;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const [filter, setFilter] = useState<"all" | OutputStream>("all");
  const [rawOutput, setRawOutput] = useState(false);
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState(lines);
  const [autoScroll, setAutoScroll] = useState(true);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(260);
  const displayed = paused ? frozen : lines;
  const filteredByStream = filterTaskOutput(displayed, filter);
  const filtered = rawOutput ? filteredByStream : compactFriendlyOutput(filteredByStream);
  const rowHeight = 24;
  const range = visibleRange(filtered.length, scrollTop, height, rowHeight, 8);
  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setHeight(entry.contentRect.height));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!paused && autoScroll && viewport.current) {
      viewport.current.scrollTop = viewport.current.scrollHeight;
    }
  }, [autoScroll, filtered.length, paused]);
  const togglePause = () => {
    if (!paused) setFrozen(lines);
    setPaused((value) => !value);
  };
  return (
    <section className={`task-output ${rawOutput ? "raw" : ""}`} aria-label="Realtime task output" aria-live="off">
      <header>
        <div className="output-filters">
          {(["all", "progress", "stdout", "stderr", "system"] as const).map((value) => (
            <button className={filter === value ? "active" : ""} onClick={() => setFilter(value)} key={value}>{value}</button>
          ))}
        </div>
        <div className="output-actions">
          <button className={rawOutput ? "active" : ""} aria-pressed={rawOutput} onClick={() => setRawOutput((value) => !value)}>Raw</button>
          <button onClick={togglePause}>{paused ? "Resume view" : "Pause view"}</button>
          <label><input type="checkbox" checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)} /> Auto-scroll</label>
          <button onClick={onCopy}>Copy</button>
        </div>
      </header>
      {truncated && <p className="output-truncated">Older output was removed by the bounded ring buffer.</p>}
      <div className="task-output-viewport" ref={viewport} onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}>
        <div className="task-output-space" style={{ height: filtered.length * rowHeight }}>
          {filtered.slice(range.start, range.end).map((line, index) => (
            <div className={`task-output-line ${line.stream}`} style={{ transform: `translateY(${(range.start + index) * rowHeight}px)` }} key={line.sequence}>
              <time>{new Date(line.timestampMillis).toLocaleTimeString()}</time><b>{line.stream}</b><span>{rawOutput ? line.stage || "—" : outputStageLabel(line.stage)}</span><code>{rawOutput ? line.text : friendlyOutputText(line)}</code>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function ActivityCenter({
  open,
  issues,
  tasks: taskRecords,
  runningTotal,
  nowMillis,
  tab,
  selectedTaskId,
  selectedIssueId,
  focusTaskId,
  focusIssueId,
  taskOutputById,
  taskOutputTruncatedById,
  headingRef,
  onClose,
  onTab,
  onSelectTask,
  onOpenIssueTask,
  onTaskFocusComplete,
  onIssueFocusComplete,
  onCancelTask,
  onCopyTaskOutput,
  onDismiss,
  onResolve,
  onCopyDiagnostics,
  blocked,
  restoreRef,
}: {
  open: boolean;
  issues: SystemIssue[];
  tasks: TaskRecord[];
  runningTotal: number;
  nowMillis: number;
  tab: "tasks" | "issues";
  selectedTaskId?: string;
  selectedIssueId?: string;
  focusTaskId?: string;
  focusIssueId?: string;
  taskOutputById: Record<string, TaskOutputLine[]>;
  taskOutputTruncatedById: Record<string, boolean>;
  headingRef: React.RefObject<HTMLHeadingElement | null>;
  onClose: () => void;
  onTab: (tab: "tasks" | "issues") => void;
  onSelectTask: (task: TaskRecord) => void;
  onOpenIssueTask: (issue: SystemIssue) => void;
  onTaskFocusComplete: (taskId: string) => void;
  onIssueFocusComplete: (issueId: string) => void;
  onCancelTask: (task: TaskRecord) => void;
  onCopyTaskOutput: () => void;
  onDismiss: (issue: SystemIssue) => void;
  onResolve: (issue: SystemIssue, action: IssueAction) => void;
  onCopyDiagnostics: (issue: SystemIssue) => void;
  blocked: boolean;
  restoreRef: React.RefObject<HTMLElement | null>;
}) {
  const drawerRef = useRef<HTMLElement>(null);
  const focusTaskRef = useRef<HTMLElement>(null);
  const focusIssueRef = useRef<HTMLElement>(null);
  useFocusContainment(open && !blocked, drawerRef, headingRef, restoreRef);
  const activeTasks = taskRecords.filter((task) => task.status === "queued" || task.status === "running");
  useEffect(() => {
    if (!open || blocked || tab !== "tasks" || !focusTaskId) return;
    const target = focusTaskRef.current;
    if (!target) return;
    target.scrollIntoView({ behavior: "auto", block: "center", inline: "nearest" });
    target.focus({ preventScroll: true });
    onTaskFocusComplete(focusTaskId);
  }, [activeTasks, blocked, focusTaskId, onTaskFocusComplete, open, tab]);
  useEffect(() => {
    if (!open || blocked || tab !== "issues" || !focusIssueId) return;
    const target = focusIssueRef.current;
    if (!target) return;
    target.scrollIntoView({ behavior: "auto", block: "center", inline: "nearest" });
    target.focus({ preventScroll: true });
    onIssueFocusComplete(focusIssueId);
  }, [blocked, focusIssueId, issues, onIssueFocusComplete, open, tab]);
  const moveTabFocus = (event: React.KeyboardEvent<HTMLElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    const current = tabs.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    tabs[next]?.focus();
    tabs[next]?.click();
  };
  return (
    <>
      <button className={`issues-scrim ${open ? "shown" : ""}`} onClick={onClose} aria-label="Close activity" tabIndex={open && !blocked ? 0 : -1} inert={blocked} />
      <aside ref={drawerRef} id="system-issues" className={`issues-drawer activity-drawer ${open ? "open" : ""}`} aria-hidden={!open || blocked} inert={!open || blocked}>
        <header className="issues-header">
          <div>
            <p className="eyebrow">Tasks & system health</p>
            <h2 ref={headingRef} tabIndex={-1}>Activity</h2>
          </div>
          <IconButton icon="close" label="Close activity" onClick={onClose} />
        </header>
        <nav className="activity-tabs" aria-label="Activity views" role="tablist" onKeyDown={moveTabFocus}>
          <button role="tab" aria-controls="activity-panel" tabIndex={tab === "tasks" ? 0 : -1} aria-selected={tab === "tasks"} className={tab === "tasks" ? "active" : ""} onClick={() => onTab("tasks")}>Tasks <b>{runningTotal}</b></button>
          <button role="tab" aria-controls="activity-panel" tabIndex={tab === "issues" ? 0 : -1} aria-selected={tab === "issues"} className={tab === "issues" ? "active" : ""} onClick={() => onTab("issues")}>Issues <b>{issues.length}</b></button>
        </nav>
        <div id="activity-panel" className="issues-list" role="tabpanel" aria-label={`${tab} activity`} aria-live={tab === "issues" ? "polite" : "off"}>
          {tab === "tasks" && activeTasks.length === 0 && (
            <div className="issues-empty"><strong>{runningTotal > 0 ? "Queued tasks remain in Library" : "No task is running"}</strong><span>{runningTotal > 0 ? "Use View task on an item to open its exact queued work." : "New processing, scans and downloads will appear here."}</span></div>
          )}
          {tab === "tasks" && runningTotal > activeTasks.length && <p className="activity-limited">Showing {activeTasks.length.toLocaleString()} recently active tasks. Other queued work remains available by ID from its source context.</p>}
          {tab === "tasks" && activeTasks.map((task) => {
            const elapsed = elapsedSeconds(task, nowMillis);
            const selected = selectedTaskId === task.id;
            const barValue = task.progressPercent ?? task.stageProgressPercent;
            const determinate = task.progressMode === "determinate"
              && (barValue != null || task.totalUnits != null);
            const taskOutput = taskOutputById[task.id] ?? [];
            const modelTransfer = task.kind === "model-install"
              && task.statusMessage?.startsWith("Downloading pinned model")
              ? latestModelTransferProgress(taskOutput)
              : undefined;
            const primaryStatus = task.kind === "model-install"
              ? task.statusMessage || task.stageTitle || "Preparing model setup"
              : task.stageTitle || task.statusMessage || (task.status === "queued" ? "Waiting to start" : "Working");
            return (
              <article
                ref={focusTaskId === task.id ? focusTaskRef : undefined}
                className={`task-card ${task.status} ${selected ? "selected" : ""}`}
                aria-label={`${task.title}, ${task.status} task`}
                tabIndex={-1}
                key={task.id}
              >
                <header><div><small>{task.kind.replace(/-/g, " ")}</small><h3>{task.title}</h3></div><span>{task.status}</span></header>
                <p>{primaryStatus}</p>
                <div className="task-times"><span>Elapsed {formatTaskDuration(elapsed)}</span>{task.kind !== "model-install" && task.etaSeconds != null && <span>About {formatTaskDuration(task.etaSeconds)} left</span>}</div>
                {modelTransfer ? (
                  <div className="task-progress model-transfer"><div><span>Current download</span><b>{modelTransfer.percent}%</b></div><i role="progressbar" aria-label="Current model file download" aria-valuemin={0} aria-valuemax={100} aria-valuenow={modelTransfer.percent}><b style={{ width: `${modelTransfer.percent}%` }} /></i><span className="task-units">{modelTransfer.completedLabel} / {modelTransfer.totalLabel}</span></div>
                ) : task.kind === "model-install" && determinate ? (
                  <div className="task-progress"><div><span>Setup progress</span><b>{Math.round(task.progressPercent ?? task.stageProgressPercent ?? 0)}%</b></div>{barValue != null && <i role="progressbar" aria-label="Model setup step progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={barValue}><b style={{ width: `${barValue}%` }} /></i>}</div>
                ) : task.kind !== "model-install" && determinate ? (
                  <div className="task-progress">{task.stageProgressPercent != null && <div><span>Current stage</span><b>{task.stageProgressPercent.toFixed(1)}%</b></div>}{task.progressPercent != null && <div><span>Overall</span><b>{Math.round(task.progressPercent)}%</b></div>}{barValue != null && <i role="progressbar" aria-label={task.progressPercent != null ? "Overall task progress" : "Current stage progress"} aria-valuemin={0} aria-valuemax={100} aria-valuenow={barValue}><b style={{ width: `${barValue}%` }} /></i>}</div>
                ) : <i className="task-indeterminate" role="progressbar" aria-label="Task progress is being measured"><b /></i>}
                {!modelTransfer && task.completedUnits != null && task.totalUnits != null && <span className="task-units">{task.completedUnits.toLocaleString()} / {task.totalUnits.toLocaleString()} {task.unitLabel || "units"}</span>}
                <footer><button onClick={() => onSelectTask(task)}>{selected ? "Hide output" : "Show output"}</button>{task.cancellable && <button onClick={() => onCancelTask(task)}>Cancel</button>}</footer>
                {selected && <TaskOutputPane key={task.id} lines={taskOutput} truncated={Boolean(taskOutputTruncatedById[task.id]) || task.outputTruncated} onCopy={onCopyTaskOutput} />}
              </article>
            );
          })}
          {tab === "issues" && (issues.length === 0 ? (
            <div className="issues-empty"><strong>Everything looks good</strong><span>There are no unresolved issues.</span></div>
          ) : issues.map((issue) => {
            const linkedTask = issue.relatedTaskId
              ? taskRecords.find((task) => task.id === issue.relatedTaskId)
              : undefined;
            const outputOpen = selectedIssueId === issue.id
              && Boolean(issue.relatedTaskId && selectedTaskId === issue.relatedTaskId);
            return <article
              ref={focusIssueId === issue.id ? focusIssueRef : undefined}
              className={`issue-card ${issue.severity}`}
              aria-label={`${issue.title}, issue`}
              tabIndex={-1}
              key={issue.id}
            >
              <header>
                <div><small>{issue.scope.replace(/-/g, " ")}</small><h3>{issue.title}</h3></div>
                {issue.state === "open" && issue.severity !== "blocking" && <IconButton className="issue-dismiss" icon="close" iconSize={15} label={`Dismiss ${issue.title}`} onClick={() => onDismiss(issue)} />}
              </header>
              <p>{issue.summary}</p>
              {issue.occurrences > 1 && <span className="issue-occurrences">Occurred {issue.occurrences} times</span>}
              {issue.state === "resolving" && <p className="issue-resolving">{issue.progressMessage || "Resolution is running"}. Realtime output is available here when the resolution has a linked task.</p>}
              {issue.detail && <details><summary>Technical details</summary><pre>{issue.detail}</pre></details>}
              <footer>
                {issue.detail && <button onClick={() => onCopyDiagnostics(issue)}>Copy diagnostics</button>}
                {issue.relatedTaskId && <button onClick={() => onOpenIssueTask(issue)}>{outputOpen ? "Hide output" : "View output"}</button>}
                {issue.state !== "resolving" && issue.actions.map((action) => (
                  <button className="primary" key={action.kind} onClick={() => onResolve(issue, action)}>{action.label}</button>
                ))}
              </footer>
              {outputOpen && linkedTask && <TaskOutputPane key={linkedTask.id} lines={taskOutputById[linkedTask.id] ?? []} truncated={Boolean(taskOutputTruncatedById[linkedTask.id]) || linkedTask.outputTruncated} onCopy={onCopyTaskOutput} />}
            </article>
          }))}
        </div>
      </aside>
    </>
  );
}

function App() {
  const native = hasNativeBridge();
  const [catalog, setCatalog] = useState<CatalogSnapshot>(EMPTY_CATALOG);
  const [shownItems, setShownItems] = useState<LibraryItem[]>([]);
  const [status, setStatus] = useState<PlayerStatus>();
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string>();
  const [currentId, setCurrentId] = useState<string>();
  const [opened, setOpened] = useState<OpenPackage>();
  const [trackId, setTrackId] = useState("karaoke");
  const [playing, setPlaying] = useState(false);
  const [pendingPlay, setPendingPlay] = useState(false);
  const [shuffle, setShuffle] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(0.9);
  const [fullscreen, setFullscreen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [nativeIssues, setNativeIssues] = useState<SystemIssue[]>([]);
  const [clientIssues, setClientIssues] = useState<SystemIssue[]>([]);
  const [issuesOpen, setIssuesOpen] = useState(false);
  const [activityTab, setActivityTab] = useState<"tasks" | "issues">("tasks");
  const [taskState, setTaskState] = useState<TaskClientState>(EMPTY_TASK_STATE);
  const [selectedTaskId, setSelectedTaskId] = useState<string>();
  const [selectedIssueId, setSelectedIssueId] = useState<string>();
  const [selectedTaskRecord, setSelectedTaskRecord] = useState<TaskRecord>();
  const [pendingTaskFocusId, setPendingTaskFocusId] = useState<string>();
  const [pendingIssueFocusId, setPendingIssueFocusId] = useState<string>();
  const [taskOutputTruncated, setTaskOutputTruncated] = useState<Record<string, boolean>>({});
  const [nowMillis, setNowMillis] = useState(() => Date.now());
  const [utilityOpen, setUtilityOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [confirmIssue, setConfirmIssue] = useState<SystemIssue>();
  const [licenseConfirmed, setLicenseConfirmed] = useState(false);
  const [seenIssueNotice, setSeenIssueNotice] = useState<string>();
  const [lyricDialog, setLyricDialog] = useState<{ item: LibraryItem; mode: "add" | "edit" }>();
  const [lyricDraft, setLyricDraft] = useState("");
  const [clipDialogOpen, setClipDialogOpen] = useState(false);
  const [clipPreview, setClipPreview] = useState<LocalClipPreview>();
  const [clipTitle, setClipTitle] = useState("");
  const [clipStart, setClipStart] = useState("00:00:00.000");
  const [clipEnd, setClipEnd] = useState("00:00:00.000");
  const [clipLoop, setClipLoop] = useState(true);
  const [clipBusy, setClipBusy] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const clipAudioRef = useRef<HTMLAudioElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const issuesHeadingRef = useRef<HTMLHeadingElement>(null);
  const issuesToggleRef = useRef<HTMLButtonElement>(null);
  const utilityToggleRef = useRef<HTMLDivElement>(null);
  const setupDialogRef = useRef<HTMLDivElement>(null);
  const aboutDialogRef = useRef<HTMLDivElement>(null);
  const lastAudibleVolumeRef = useRef(0.9);
  const selectedTaskIdRef = useRef<string | undefined>(undefined);
  const taskReplayRef = useRef(new Map<string, { dirty: boolean }>());
  const modelReplayTaskRef = useRef<string | undefined>(undefined);

  const ready = useMemo(() => catalog.items.filter((item) => item.status === "ready"), [catalog.items]);
  const currentItem = catalog.items.find((item) => item.id === currentId);
  const selectedItem = catalog.items.find((item) => item.id === selectedId);
  const activeTrack = opened?.media.audioTracks.find((track) => track.id === trackId) ?? opened?.media.audioTracks[0];
  const systemIssues = useMemo(
    () => mergeIssueSources(nativeIssues, clientIssues),
    [clientIssues, nativeIssues],
  );
  const activityTasks = useMemo(() => {
    const visible = visibleTasks(taskState.tasks, nowMillis);
    const selected = selectedTaskId
      ? taskState.tasks.find((task) => task.id === selectedTaskId) ?? (selectedTaskRecord?.id === selectedTaskId ? selectedTaskRecord : undefined)
      : undefined;
    return selected && !visible.some((task) => task.id === selected.id)
      ? [selected, ...visible]
      : visible;
  }, [nowMillis, selectedTaskId, selectedTaskRecord, taskState.tasks]);
  const activeTaskCount = taskState.activeTaskCount;
  const processingTasksByItem = useMemo(
    () => activeProcessingTasksByItem(taskState.tasks),
    [taskState.tasks],
  );
  const showUtilityMenu = !native || status?.platform === "windows" || status?.platform === "linux";
  const systemModalOpen = Boolean(confirmIssue) || aboutOpen;
  const anyModalOpen = systemModalOpen || Boolean(lyricDialog) || clipDialogOpen;
  useFocusContainment(Boolean(confirmIssue), setupDialogRef);
  useFocusContainment(aboutOpen, aboutDialogRef, undefined, utilityToggleRef);
  const reportError = useCallback((
    scope: string,
    title: string,
    reason: unknown,
    summary?: string,
    action?: IssueAction,
  ) => {
    setClientIssues((current) => upsertIssue(
      current,
      clientIssue(scope, title, reason, summary, action),
    ));
  }, []);

  const replayTaskOutput = useCallback((taskId: string) => {
    if (!native) return;
    const active = taskReplayRef.current.get(taskId);
    if (active) {
      active.dirty = true;
      return;
    }
    const replay = { dirty: false };
    taskReplayRef.current.set(taskId, replay);
    void (async () => {
      try {
        do {
          replay.dirty = false;
          const snapshot = await invoke<TaskOutputSnapshot>("task_output_snapshot", {
            taskId,
            afterSequence: 0,
          });
          setTaskState((current) => mergeOutputSnapshot(current, taskId, snapshot));
          setTaskOutputTruncated((current) => ({ ...current, [taskId]: snapshot.truncated }));
        } while (replay.dirty);
      } catch (reason) {
        reportError("tasks", "Task output could not be replayed", reason);
      } finally {
        taskReplayRef.current.delete(taskId);
      }
    })();
  }, [native, reportError]);

  const refresh = useCallback(async () => {
    if (!native) return;
    const [nextCatalog, nextStatus] = await Promise.all([
      invoke<CatalogSnapshot>("catalog_snapshot"),
      invoke<PlayerStatus>("player_status"),
    ]);
    const nextIssues = await invoke<SystemIssue[]>("system_issues");
    setCatalog(nextCatalog);
    setStatus(nextStatus);
    setNativeIssues(nextIssues);
  }, [native]);

  useEffect(() => {
    if (!native) {
      setShownItems([]);
      return;
    }
    refresh().catch((reason) => reportError("system", "LyricRail could not refresh", reason));
    let disposed = false;
    const subscribeTasks = async (): Promise<UnlistenFn> => {
      const unlisten = await listen<TaskRuntimeUpdate>("task-runtime-update", (event) => {
        if (!disposed) {
          const selected = selectedTaskIdRef.current;
          setTaskState((current) => applyTaskUpdate(current, {
            ...event.payload,
            output: event.payload.output.filter((line) => (
              line.taskId === selected || line.taskId === "model-install"
            )),
          }));
          const selectedUpdate = selected
            ? event.payload.tasks.find((task) => task.id === selected)
            : undefined;
          if (selectedUpdate) setSelectedTaskRecord(normalizeTaskRecord(selectedUpdate));
          if (selected && event.payload.removedTaskIds.includes(selected)) {
            selectedTaskIdRef.current = undefined;
            setSelectedTaskId(undefined);
            setSelectedTaskRecord(undefined);
            setSelectedIssueId(undefined);
            setPendingTaskFocusId((current) => current === selected ? undefined : current);
          } else if (selected && event.payload.tasksReset && !selectedUpdate) {
            if (taskOutputNeedsReplay(event.payload, selected)) replayTaskOutput(selected);
            void invoke<TaskRecord | null>("task_record", { taskId: selected }).then((task) => {
              if (task) setSelectedTaskRecord(normalizeTaskRecord(task));
              else {
                selectedTaskIdRef.current = undefined;
                setSelectedTaskId(undefined);
                setSelectedTaskRecord(undefined);
                setSelectedIssueId(undefined);
                setPendingTaskFocusId((current) => current === selected ? undefined : current);
              }
            }).catch(() => undefined);
          } else if (selected && taskOutputNeedsReplay(event.payload, selected)) {
            replayTaskOutput(selected);
          }
        }
      });
      try {
        const snapshot = await invoke<TaskSnapshot>("task_runtime_snapshot");
        if (!disposed) setTaskState((current) => applyTaskSnapshot(current, snapshot));
        return unlisten;
      } catch (reason) {
        unlisten();
        throw reason;
      }
    };
    const subscriptions = Promise.all([
      listen<CatalogSnapshot>("library-changed", (event) => !disposed && setCatalog(event.payload)),
      listen<string>("library-import-package", (event) => invoke("add_local_files", { paths: [event.payload] }).catch((reason) => reportError("library", "Package import failed", reason))),
      listen<SystemIssue[]>("system-issues-changed", (event) => !disposed && setNativeIssues(event.payload)),
      listen("recovery-tool-completed", () => {
        Promise.allSettled([
          invoke("rescan_local_sources"),
          invoke("rescan_google_drive"),
        ]).then(refresh).catch((reason) => reportError("recovery", "Library refresh after recovery failed", reason));
      }),
      subscribeTasks(),
    ]);
    invoke<string | null>("take_startup_package")
      .then((path) => path && invoke("add_local_files", { paths: [path] }))
      .catch((reason) => reportError("library", "Startup package import failed", reason));
    invoke("rescan_local_sources").catch((reason) => reportError("library", "Local source scan failed", reason));
    invoke("rescan_google_drive").catch((reason) => reportError("drive", "Drive source scan failed", reason));
    return () => {
      disposed = true;
      invoke("set_playback_active", { playing: false }).catch(() => undefined);
      subscriptions.then((values: UnlistenFn[]) => values.forEach((unlisten) => unlisten())).catch(() => undefined);
    };
  }, [native, refresh, replayTaskOutput, reportError]);

  useEffect(() => {
    if (!taskState.tasks.some((task) => task.status === "queued" || task.status === "running")) return;
    setNowMillis(Date.now());
    const timer = window.setInterval(() => setNowMillis(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [taskState.tasks]);

  useEffect(() => {
    selectedTaskIdRef.current = issuesOpen ? selectedTaskId : undefined;
    if (!issuesOpen || !selectedTaskId || !native) return;
    replayTaskOutput(selectedTaskId);
  }, [issuesOpen, native, replayTaskOutput, selectedTaskId]);

  useEffect(() => {
    if (!issuesOpen) {
      modelReplayTaskRef.current = undefined;
      return;
    }
    const modelTask = taskState.tasks.find((task) => (
      task.kind === "model-install" && (task.status === "queued" || task.status === "running")
    ));
    if (modelTask && modelReplayTaskRef.current !== modelTask.id) {
      modelReplayTaskRef.current = modelTask.id;
      replayTaskOutput(modelTask.id);
    }
  }, [issuesOpen, replayTaskOutput, taskState.tasks]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      if (!query.trim() || !native) {
        setShownItems(catalog.items);
        return;
      }
      invoke<LibraryItem[]>("search_library", { query })
        .then(setShownItems)
        .catch((reason) => reportError("library", "Library search failed", reason));
    }, 140);
    return () => window.clearTimeout(timeout);
  }, [catalog.items, native, query, reportError]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (confirmIssue) {
          setConfirmIssue(undefined);
          setLicenseConfirmed(false);
        }
        else if (aboutOpen) setAboutOpen(false);
        else if (utilityOpen) setUtilityOpen(false);
        else if (issuesOpen) setIssuesOpen(false);
        else if (lyricDialog) setLyricDialog(undefined);
        else if (clipDialogOpen) {
          if (clipBusy) return;
          const clipId = clipPreview?.clipId;
          setClipDialogOpen(false);
          setClipTitle("");
          setClipPreview(undefined);
          setClipBusy(false);
          if (native && clipId) invoke("cancel_local_clip", { clipId }).catch(() => undefined);
        }
        else setDrawerOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [aboutOpen, clipBusy, clipDialogOpen, clipPreview?.clipId, confirmIssue, issuesOpen, licenseConfirmed, lyricDialog, native, utilityOpen]);

  useEffect(() => {
    if (issuesOpen) {
      issuesHeadingRef.current?.focus();
      const issue = activityTab === "issues" ? systemIssues[0] : undefined;
      if (issue) setSeenIssueNotice(`${issue.id}:${issue.updatedAtMillis}`);
    }
  }, [activityTab, issuesOpen, systemIssues]);

  useEffect(() => {
    if (!utilityOpen) return;
    return () => {
      if (!aboutOpen) utilityToggleRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    };
  }, [aboutOpen, utilityOpen]);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = 0;
    const update = (now: number) => {
      const audio = audioRef.current;
      const video = videoRef.current;
      if (audio && now - last >= 33) {
        setTime(audio.currentTime);
        if (video && shouldResyncVideo(audio.currentTime, video.currentTime)) video.currentTime = audio.currentTime;
        last = now;
      }
      frame = requestAnimationFrame(update);
    };
    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, [playing]);

  useEffect(() => {
    if (audioRef.current) audioRef.current.volume = volume;
  }, [opened, volume]);

  useEffect(() => {
    const update = () => setFullscreen(Boolean(document.fullscreenElement));
    update();
    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);

  const applyVolume = (value: number) => {
    const next = clampVolume(value);
    if (next > 0.001) lastAudibleVolumeRef.current = next;
    setVolume(next);
    if (audioRef.current) audioRef.current.volume = next;
  };

  const toggleMute = () => {
    const next = toggleMutedVolume(volume, lastAudibleVolumeRef.current);
    lastAudibleVolumeRef.current = next.lastAudibleVolume;
    setVolume(next.volume);
    if (audioRef.current) audioRef.current.volume = next.volume;
  };

  const toggleFullscreen = async () => {
    try {
      await toggleDocumentFullscreen(
        document,
        document.querySelector<HTMLElement>(".app-shell"),
      );
    } catch (reason) {
      reportError("view", "Fullscreen could not be changed", reason);
    }
  };

  const runBusy = async (
    action: () => Promise<unknown>,
    scope = "system",
    title = "Action could not be completed",
  ) => {
    setBusy(true);
    try { await action(); }
    catch (reason) { reportError(scope, title, reason); }
    finally { setBusy(false); }
  };

  const addFiles = () => runBusy(async () => {
    const selected = await open({ multiple: true, directory: false, filters: [{ name: "Music and LyricRail", extensions: ["lrail", "mp4", "mkv", "mov", "webm", "mp3", "m4a", "flac", "wav", "aac", "ogg", "opus", "avi", "wma"] }] });
    if (!selected) return;
    const paths = Array.isArray(selected) ? selected : [selected];
    if (!shouldOpenClipEditor(paths)) {
      await invoke("add_local_files", { paths });
      return;
    }
    const preview = await invoke<LocalClipPreview>("prepare_local_clip", { path: paths[0] });
    setClipPreview(preview);
    setClipTitle(preview.suggestedTitle);
    setClipStart("00:00:00.000");
    setClipEnd(formatTimecodeMillis(preview.durationMillis));
    setClipLoop(true);
    setClipBusy(false);
    setClipDialogOpen(true);
  }, "library", "Files could not be added");

  const addFolder = () => runBusy(async () => {
    const selected = await open({ multiple: false, directory: true });
    if (typeof selected === "string") await invoke("add_local_folder", { path: selected });
  }, "library", "Folder could not be added");

  const connectDrive = () => runBusy(async () => {
    await invoke("connect_google_drive");
    setDrawerOpen(true);
  }, "drive", "Google Drive could not connect");

  const rescanLibrary = () => runBusy(() => Promise.all([
    invoke("rescan_local_sources"),
    invoke("rescan_google_drive"),
  ]), "library", "Library sources could not be rescanned");

  const toggleLibrary = () => {
    setIssuesOpen(false);
    setDrawerOpen((value) => !value);
  };
  const toggleShuffle = () => setShuffle((value) => !value);

  const closeClipDialog = () => {
    if (clipBusy) return;
    const clipId = clipPreview?.clipId;
    setClipDialogOpen(false);
    setClipTitle("");
    setClipBusy(false);
    setClipPreview(undefined);
    if (native && clipId) invoke("cancel_local_clip", { clipId }).catch(() => undefined);
  };

  const clipPreviewMedia = () => clipAudioRef.current;

  const setClipEndpointFromPlayhead = (endpoint: "start" | "end") => {
    const media = clipPreviewMedia();
    if (!media) return;
    const value = formatTimecodeMillis(media.currentTime * 1000);
    if (endpoint === "start") setClipStart(value); else setClipEnd(value);
  };

  const nudgeClipEndpoint = (endpoint: "start" | "end", direction: -1 | 1) => {
    if (!clipPreview) return;
    const step = clipPreview.frameDurationMillis ?? 10;
    try {
      const current = endpoint === "start" ? clipStart : clipEnd;
      const value = nudgedTimecode(current, direction * step, clipPreview.durationMillis);
      if (endpoint === "start") setClipStart(value); else setClipEnd(value);
    } catch (reason) {
      reportError("clip", "Clip timestamp is invalid", reason);
    }
  };

  const handleClipPreviewTime = (media: HTMLMediaElement) => {
    if (!clipLoop || !clipPreview) return;
    try {
      const range = validateClipRange(clipStart, clipEnd, clipPreview.durationMillis);
      const next = loopedPreviewTime(
        media.currentTime * 1000,
        range.startMillis,
        range.endMillis,
      );
      if (next !== undefined) {
        media.currentTime = next / 1000;
        if (!media.paused) media.play().catch(() => undefined);
      }
    } catch { /* allow partially edited timestamps without interrupting preview */ }
  };

  const commitClip = async (wholeFile: boolean) => {
    if (!clipPreview || clipBusy) return;
    try {
      const range = wholeFile
        ? { startMillis: 0, endMillis: clipPreview.durationMillis }
        : validateClipRange(clipStart, clipEnd, clipPreview.durationMillis);
      setClipBusy(true);
      const snapshot = await invoke<CatalogSnapshot>("commit_local_clip", {
        clipId: clipPreview.clipId,
        startMillis: range.startMillis,
        endMillis: range.endMillis,
        title: clipTitle,
      });
      setCatalog(snapshot);
      setClipTitle("");
      setClipPreview(undefined);
      setClipDialogOpen(false);
      setDrawerOpen(true);
    } catch (reason) {
      reportError("clip", "Clip could not be added", reason);
    } finally {
      setClipBusy(false);
    }
  };

  const playElements = async () => {
    clipAudioRef.current?.pause();
    const audio = audioRef.current;
    const video = videoRef.current;
    if (!audio || !video) return;
    const start = playbackStartTime(audio.currentTime, audio.duration, audio.ended);
    audio.currentTime = start;
    video.currentTime = start;
    const [, audioResult] = await Promise.allSettled([video.play(), audio.play()]);
    if (audioResult.status === "rejected") {
      video.pause();
      throw audioResult.reason;
    }
    setPlaying(true);
    if (native) invoke("set_playback_active", { playing: true }).catch(() => undefined);
  };

  const pauseElements = () => {
    audioRef.current?.pause();
    videoRef.current?.pause();
    setPlaying(false);
    if (native) invoke("set_playback_active", { playing: false }).catch(() => undefined);
  };

  const openItem = async (item: LibraryItem) => {
    if (item.status !== "ready" && item.status !== "offline") return;
    try {
      pauseElements();
      const result = await invoke<OpenPackage>("open_library_item", { itemId: item.id });
      setOpened(result);
      setCurrentId(item.id);
      setSelectedId(item.id);
      setTrackId(result.media.audioTracks.find((track) => track.default)?.id ?? result.media.audioTracks[0]?.id ?? "karaoke");
      setTime(0);
      setDuration(0);
      setPendingPlay(true);
      setDrawerOpen(false);
    } catch (reason) { reportError("playback", "Song could not be opened", reason); }
  };

  const togglePlay = () => playing ? pauseElements() : playElements().catch((reason) => reportError("playback", "Playback could not start", reason));

  const move = (direction: -1 | 1) => {
    const item = shuffle && direction > 0
      ? shuffledReadyItem(catalog.items, currentId)
      : adjacentReadyItem(catalog.items, currentId, direction);
    if (item) openItem(item);
  };

  const switchTrack = (track: AudioTrack) => {
    const audio = audioRef.current;
    if (!audio || track.id === trackId) return;
    const resume = !audio.paused;
    const position = audio.currentTime;
    audio.pause();
    videoRef.current?.pause();
    setTrackId(track.id);
    window.setTimeout(() => {
      const next = audioRef.current;
      if (!next) return;
      const restore = () => {
        next.currentTime = Math.min(position, Number.isFinite(next.duration) ? next.duration : position);
        if (resume) playElements().catch((reason) => reportError("playback", "Audio track could not resume", reason));
      };
      if (next.readyState >= 1) restore(); else next.addEventListener("loadedmetadata", restore, { once: true });
    }, 0);
  };

  const chooseLyrics = (item: LibraryItem) => runBusy(async () => {
    const selected = await open({ multiple: false, directory: false, filters: [{ name: "UTF-8 lyrics", extensions: ["txt"] }] });
    if (typeof selected === "string") await invoke("provide_lyrics_file", { itemId: item.id, path: selected });
  }, "lyrics", "Lyric file could not be added");

  const showLyricDialog = async (item: LibraryItem, mode: "add" | "edit") => {
    setLyricDraft(mode === "edit" && native ? await invoke<string>("item_lyrics", { itemId: item.id }) : "");
    setLyricDialog({ item, mode });
    setDrawerOpen(true);
  };

  const submitLyrics = () => lyricDialog && runBusy(async () => {
    const command = lyricDialog.mode === "edit" ? "revise_item_lyrics" : "provide_lyrics_text";
    await invoke(command, { itemId: lyricDialog.item.id, text: lyricDraft });
    setLyricDialog(undefined);
  }, "lyrics", "Lyrics could not be saved");

  const exportRecovery = () => runBusy(async () => {
    const output = await save({ defaultPath: "library.lrail-recovery", filters: [{ name: "LyricRail recovery", extensions: ["lrail-recovery"] }] });
    if (output) await invoke("launch_recovery_export", { output });
  }, "recovery", "Recovery bundle could not be exported");

  const restoreRecovery = () => runBusy(async () => {
    const bundle = await open({ multiple: false, directory: false, filters: [{ name: "LyricRail recovery", extensions: ["lrail-recovery"] }] });
    if (typeof bundle !== "string") return;
    const driveItem = (selectedItem?.sources.includes("Drive") ? selectedItem : catalog.items.find((item) => item.sources.includes("Drive")));
    if (driveItem) await invoke("launch_recovery_restore_cloud", { bundle, itemId: driveItem.id });
    else {
      const library = await open({ multiple: false, directory: true });
      if (typeof library === "string") await invoke("launch_recovery_restore_local", { bundle, library });
    }
  }, "recovery", "Recovery bundle could not be restored");

  const dismissIssue = (issue: SystemIssue) => {
    if (selectedIssueId === issue.id) {
      selectedTaskIdRef.current = undefined;
      setSelectedTaskId(undefined);
      setSelectedTaskRecord(undefined);
      setSelectedIssueId(undefined);
    }
    if (issue.native) {
      invoke("dismiss_system_issue", { issueId: issue.id })
        .catch((reason) => reportError("issues", "Issue could not be dismissed", reason));
    } else {
      setClientIssues((current) => current.filter((candidate) => candidate.id !== issue.id));
    }
  };

  const resolveIssue = (issue: SystemIssue, action: IssueAction) => {
    if (action.kind === "install-models") {
      setLicenseConfirmed(false);
      setConfirmIssue(issue);
      return;
    }
    if (action.kind === "retry-item" && issue.relatedItemId) {
      void runBusy(
        () => invoke("retry_processing_item", { itemId: issue.relatedItemId }),
        "processing",
        "Song retry failed",
      );
      return;
    }
    if (action.kind === "reconnect-drive") {
      connectDrive();
    }
  };

  const installModels = async () => {
    if (!confirmIssue || !licenseConfirmed) return;
    const issue = confirmIssue;
    setConfirmIssue(undefined);
    setLicenseConfirmed(false);
    setActivityTab("tasks");
    setIssuesOpen(true);
    try {
      await invoke("install_processing_models", {
        issueId: issue.id,
        licenseConfirmed: true,
      });
      await refresh();
    } catch {
      await refresh().catch(() => undefined);
      setActivityTab("issues");
      setIssuesOpen(true);
    }
  };

  const copyIssueDiagnostics = (issue: SystemIssue) => {
    if (!navigator.clipboard) {
      reportError("issues", "Diagnostics could not be copied", "Clipboard access is unavailable");
      return;
    }
    const report = [
      `LyricRail ${status?.version || "0.8.0"}`,
      `Issue: ${issue.code}`,
      `Scope: ${issue.scope}`,
      issue.detail || issue.summary,
    ].join("\n");
    navigator.clipboard.writeText(report)
      .catch((reason) => reportError("issues", "Diagnostics could not be copied", reason));
  };

  const openTaskOutput = (task: TaskRecord) => {
    selectedTaskIdRef.current = task.id;
    setSelectedTaskId(task.id);
    setSelectedTaskRecord(task);
  };

  const selectActivityTask = (task: TaskRecord) => {
    setSelectedIssueId(undefined);
    if (selectedTaskId === task.id) {
      selectedTaskIdRef.current = undefined;
      setSelectedTaskId(undefined);
      setSelectedTaskRecord(undefined);
      return;
    }
    openTaskOutput(task);
  };

  const showActivityTask = (task: TaskRecord) => {
    if (task.status !== "queued" && task.status !== "running") return;
    setSelectedIssueId(undefined);
    setActivityTab("tasks");
    setIssuesOpen(true);
    openTaskOutput(task);
    setPendingTaskFocusId(task.id);
  };

  const completeTaskFocus = useCallback((taskId: string) => {
    setPendingTaskFocusId((current) => current === taskId ? undefined : current);
  }, []);

  const showIssue = (issue: SystemIssue) => {
    setActivityTab("issues");
    setIssuesOpen(true);
    setPendingIssueFocusId(issue.id);
  };

  const completeIssueFocus = useCallback((issueId: string) => {
    setPendingIssueFocusId((current) => current === issueId ? undefined : current);
  }, []);

  const openIssueTask = async (issue: SystemIssue) => {
    const taskId = issue.relatedTaskId;
    if (!taskId) return;
    if (selectedIssueId === issue.id && selectedTaskId === taskId) {
      selectedTaskIdRef.current = undefined;
      setSelectedTaskId(undefined);
      setSelectedTaskRecord(undefined);
      setSelectedIssueId(undefined);
      return;
    }
    let task = taskState.tasks.find((candidate) => candidate.id === taskId)
      ?? (selectedTaskRecord?.id === taskId ? selectedTaskRecord : undefined);
    if (!task && native) {
      try {
        const nativeTask = await invoke<TaskRecord | null>("task_record", { taskId });
        task = nativeTask ? normalizeTaskRecord(nativeTask) : undefined;
      } catch (reason) {
        reportError("tasks", "Linked task output could not be opened", reason);
        return;
      }
    }
    if (task) {
      openTaskOutput(task);
      setSelectedIssueId(issue.id);
    }
    else reportError("tasks", "Linked task output is no longer available", "The bounded task record has expired. Issue details and actions are still available.");
  };

  const showItemContext = async (item: LibraryItem) => {
    const relatedIssue = issueForLibraryItem(item, systemIssues);
    if ((item.status === "failed" || item.status === "setup-required") && relatedIssue) {
      setDrawerOpen(false);
      showIssue(relatedIssue);
      return;
    }
    let task = taskState.tasks.find((candidate) => candidate.id === item.id);
    if (!task && native) {
      try {
        const nativeTask = await invoke<TaskRecord | null>("task_record", { taskId: item.id });
        task = nativeTask ? normalizeTaskRecord(nativeTask) : undefined;
      } catch (reason) {
        reportError("tasks", "Task details could not be opened", reason);
      }
    }
    setDrawerOpen(false);
    if (task && (task.status === "queued" || task.status === "running")) showActivityTask(task);
    else if (relatedIssue) showIssue(relatedIssue);
    else reportError("activity", "Related activity is no longer available", "No active task or matching Issue is available for this item.");
  };

  const cancelActivityTask = (task: TaskRecord) => {
    invoke("cancel_task", { taskId: task.id })
      .catch((reason) => reportError("tasks", `Could not cancel ${task.title}`, reason));
  };

  const copyTaskOutput = () => {
    const lines = selectedTaskId ? taskState.output[selectedTaskId] ?? [] : [];
    if (!navigator.clipboard) {
      reportError("tasks", "Task output could not be copied", "Clipboard access is unavailable");
      return;
    }
    const report = lines.map((line) => [
      new Date(line.timestampMillis).toISOString(),
      line.taskId,
      line.stream,
      line.stage || "-",
      line.text,
    ].join("\t")).join("\n");
    navigator.clipboard.writeText(report)
      .catch((reason) => reportError("tasks", "Task output could not be copied", reason));
  };

  useEffect(() => {
    const handlers: CommandHandlers = {
      "open-files": addFiles,
      "open-folder": addFolder,
      "toggle-library": toggleLibrary,
      "rescan-library": rescanLibrary,
      "previous-song": () => move(-1),
      "play-pause": togglePlay,
      "next-song": () => move(1),
      "toggle-shuffle": toggleShuffle,
      "toggle-mute": toggleMute,
      "toggle-fullscreen": () => { void toggleFullscreen(); },
    };
    const onKey = (event: KeyboardEvent) => {
      dispatchCommand(event, handlers);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const events = opened?.renderPlan.events ?? [];
  const queueBadge = catalog.items.filter((item) => item.status === "processing" || item.status === "queued" || item.status === "waiting-for-lyrics").length;

  return (
    <main className="app-shell">
      <header className="topbar" inert={systemModalOpen}>
        <div className="brand" aria-label="LyricRail">
          <img className="brand-mark" src={lyricRailMark} alt="" />
          <strong>LyricRail</strong>
        </div>
        <div className="now-playing">
          <strong>{currentItem?.title || "Ready to sing"}</strong>
          <span>{currentItem?.artist || currentItem?.firstLyricLine || "Open local media or an encrypted package"}</span>
        </div>
        <div className="topbar-actions">
          <button className={`library-toggle ${drawerOpen ? "active" : ""}`} onClick={toggleLibrary} aria-expanded={drawerOpen} aria-controls="library-drawer">
            Library {queueBadge > 0 && <b>{queueBadge}</b>}
          </button>
          <button ref={issuesToggleRef} className={`issues-toggle ${issuesOpen ? "active" : ""} ${systemIssues.length ? "has-issues" : activeTaskCount ? "has-running" : ""}`} onClick={() => { setDrawerOpen(false); setUtilityOpen(false); setIssuesOpen((value) => !value); }} aria-expanded={issuesOpen} aria-controls="system-issues">
            <Icon name={systemIssues.length ? "alert" : "activity"} size={17} /> Activity {(activeTaskCount + systemIssues.length) > 0 && <b>{activeTaskCount + systemIssues.length}</b>}
          </button>
          {showUtilityMenu && <div ref={utilityToggleRef}><IconButton className="utility-toggle" icon="more" label="Application menu" onClick={() => { setIssuesOpen(false); setUtilityOpen((value) => !value); }} aria-expanded={utilityOpen} /></div>}
          {showUtilityMenu && utilityOpen && (
            <>
              <button className="utility-scrim" aria-label="Close application menu" onClick={() => setUtilityOpen(false)} />
              <nav className="utility-menu" aria-label="Application">
                <button autoFocus onClick={() => { setUtilityOpen(false); setAboutOpen(true); }}>About LyricRail</button>
              </nav>
            </>
          )}
        </div>
      </header>

      <section className="player-area" inert={systemModalOpen}>
        <div className="video-stage panel" ref={stageRef}>
          {opened ? (
            <>
              <video ref={videoRef} src={opened.media.videoUrl} muted playsInline onError={() => reportError("playback", "Video playback failed", "Video range could not be authenticated or downloaded.")} onCanPlay={() => { if (pendingPlay) { setPendingPlay(false); playElements().catch((reason) => reportError("playback", "Playback could not start", reason)); } }} />
              <audio
                ref={audioRef}
                src={activeTrack?.url}
                preload="auto"
                onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || 0)}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onEnded={() => move(1)}
                onError={() => reportError("playback", "Audio playback failed", "Audio range could not be authenticated or downloaded.")}
              />
              <div className="stage-shade" />
              <LyricOverlay events={events} time={time} presentation={opened.presentation} />
              <IconButton className="center-play" icon={playing ? "pause" : "play"} iconSize={28} label={playing ? "Pause song" : "Play song"} onClick={togglePlay} />
            </>
          ) : (
            <div className="empty-stage">
              <div className="empty-brand-lockup">
                <img className="empty-brand-mark" src={lyricRailMark} alt="" />
                <strong>LyricRail</strong>
              </div>
              <h1>Your karaoke, one click away.</h1>
              <p>Choose a ready song from the library. Local media will process quietly in the same queue.</p>
              <button onClick={() => setDrawerOpen(true)}>Open library</button>
            </div>
          )}
        </div>

        <div className="transport panel">
          <div className="timeline">
            <span>{formatTime(time)}</span>
            <input
              type="range"
              min="0"
              max={Math.max(0, duration)}
              step="0.01"
              value={Math.min(time, duration || 0)}
              onChange={(event) => {
                const next = Number(event.target.value);
                if (audioRef.current) audioRef.current.currentTime = next;
                if (videoRef.current) videoRef.current.currentTime = next;
                setTime(next);
              }}
              style={{ "--progress": `${duration ? (time / duration) * 100 : 0}%` } as React.CSSProperties}
            />
            <span>{formatTime(duration)}</span>
          </div>
          <div className="control-row">
            <div className="track-toggle">
              {(opened?.media.audioTracks ?? []).map((track) => (
                <button className={track.id === trackId ? "active" : ""} onClick={() => switchTrack(track)} key={track.id}>{track.name}</button>
              ))}
            </div>
            <div className="main-controls">
              <IconButton className="transport-skip" icon="previous" iconSize={21} label="Previous ready song" onClick={() => move(-1)} disabled={!ready.length} />
              <IconButton className="transport-play" icon={playing ? "pause" : "play"} iconSize={24} label={playing ? "Pause song" : "Play song"} onClick={togglePlay} disabled={!opened} />
              <IconButton className="transport-skip" icon="next" iconSize={21} label="Next ready song" onClick={() => move(1)} disabled={!ready.length} />
            </div>
            <div className="right-controls">
              <IconButton className={shuffle ? "active" : ""} icon="shuffle" label={shuffle ? "Disable shuffle" : "Enable shuffle"} aria-pressed={shuffle} onClick={toggleShuffle} />
              <IconButton icon={volume <= 0.001 ? "volume-muted" : "volume-high"} label={volume <= 0.001 ? "Unmute volume" : "Mute volume"} onClick={toggleMute} />
              <input aria-label="Volume" type="range" min="0" max="1" step="0.01" value={volume} onChange={(event) => applyVolume(Number(event.target.value))} style={{ "--progress": `${volume * 100}%` } as React.CSSProperties} />
              <IconButton icon={fullscreen ? "fullscreen-exit" : "fullscreen"} label={fullscreen ? "Exit fullscreen" : "Enter fullscreen"} onClick={() => { void toggleFullscreen(); }} />
            </div>
          </div>
        </div>
      </section>

      <LibraryDrawer
        open={drawerOpen}
        items={shownItems}
        catalog={catalog}
        tasksByItem={processingTasksByItem}
        selectedId={selectedId}
        currentId={currentId}
        query={query}
        busy={busy || !native}
        blocked={systemModalOpen}
        onClose={() => setDrawerOpen(false)}
        onRescan={rescanLibrary}
        onQuery={setQuery}
        onSelect={(item) => setSelectedId(item.id)}
        onPlay={openItem}
        onAddFiles={addFiles}
        onAddFolder={addFolder}
        onDrive={connectDrive}
        onLyricsFile={chooseLyrics}
        onLyricsPaste={(item) => showLyricDialog(item, "add").catch((reason) => reportError("lyrics", "Lyric editor could not open", reason))}
        onEditLyrics={(item) => showLyricDialog(item, "edit").catch((reason) => reportError("lyrics", "Lyric editor could not open", reason))}
        onRetry={(item) => runBusy(() => invoke("retry_processing_item", { itemId: item.id }), "processing", `Retry failed for ${item.title}`)}
        onShowContext={showItemContext}
        onRemoveSource={(id) => runBusy(() => invoke("remove_library_source", { sourceId: id }), "library", "Library source could not be removed")}
        onRecoveryExport={exportRecovery}
        onRecoveryRestore={restoreRecovery}
      />

      <ActivityCenter
        open={issuesOpen}
        issues={systemIssues}
        tasks={activityTasks}
        runningTotal={taskState.activeTaskCount}
        nowMillis={nowMillis}
        tab={activityTab}
        selectedTaskId={selectedTaskId}
        selectedIssueId={selectedIssueId}
        focusTaskId={pendingTaskFocusId}
        focusIssueId={pendingIssueFocusId}
        taskOutputById={taskState.output}
        taskOutputTruncatedById={taskOutputTruncated}
        headingRef={issuesHeadingRef}
        onClose={() => { setIssuesOpen(false); setPendingTaskFocusId(undefined); setPendingIssueFocusId(undefined); }}
        onTab={setActivityTab}
        onSelectTask={selectActivityTask}
        onOpenIssueTask={(issue) => { void openIssueTask(issue); }}
        onTaskFocusComplete={completeTaskFocus}
        onIssueFocusComplete={completeIssueFocus}
        onCancelTask={cancelActivityTask}
        onCopyTaskOutput={copyTaskOutput}
        onDismiss={dismissIssue}
        onResolve={resolveIssue}
        onCopyDiagnostics={copyIssueDiagnostics}
        blocked={systemModalOpen}
        restoreRef={issuesToggleRef}
      />

      {lyricDialog && (
        <div className="modal-layer" role="dialog" aria-modal="true">
          <div className="lyric-dialog panel">
            <header><div><p className="eyebrow">{lyricDialog.mode === "edit" ? "Confirmed revision" : "Authoritative lyrics"}</p><h2>{lyricDialog.item.title}</h2></div><IconButton className="dialog-close" icon="close" label="Close lyric editor" onClick={() => setLyricDialog(undefined)} /></header>
            <p>{lyricDialog.mode === "edit" ? "Nothing changes until you confirm. The original package remains valid until its revision authenticates." : "Paste exact UTF-8 lyrics, one semantic phrase per line."}</p>
            <textarea value={lyricDraft} onChange={(event) => setLyricDraft(event.target.value)} autoFocus spellCheck />
            <footer><button onClick={() => setLyricDialog(undefined)}>Cancel</button><button className="primary" onClick={submitLyrics} disabled={busy || !lyricDraft.trim()}>{lyricDialog.mode === "edit" ? "Create revision" : "Add to queue"}</button></footer>
          </div>
        </div>
      )}

      {clipDialogOpen && clipPreview && (
        <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="clip-editor-title">
          <div className="clip-dialog panel">
            <header>
              <div><p className="eyebrow">Local file</p><h2 id="clip-editor-title">Choose the section to process</h2></div>
              <IconButton className="dialog-close" icon="close" label="Close clip editor" onClick={closeClipDialog} disabled={clipBusy} />
            </header>
            <p>The whole file is selected by default. A lightweight PCM audio preview works consistently for every supported source while processing keeps the original media unchanged.</p>
            <audio className="clip-preview-audio" ref={clipAudioRef} src={clipPreview.previewUrl} controls preload="metadata" onPlay={(event) => { pauseElements(); handleClipPreviewTime(event.currentTarget); }} onTimeUpdate={(event) => handleClipPreviewTime(event.currentTarget)} onError={() => reportError("clip", "Clip preview failed", "The portable local audio preview could not be played.")} />
            <label className="clip-field">
              <span>Song title</span>
              <input value={clipTitle} onChange={(event) => setClipTitle(event.target.value)} maxLength={200} disabled={clipBusy} />
            </label>
            <div className="clip-range-grid">
              {(["start", "end"] as const).map((endpoint) => (
                <label className="clip-field" key={endpoint}>
                  <span>{endpoint === "start" ? "Start" : "End"}</span>
                  <input value={endpoint === "start" ? clipStart : clipEnd} onChange={(event) => endpoint === "start" ? setClipStart(event.target.value) : setClipEnd(event.target.value)} disabled={clipBusy} />
                  <div className="clip-time-actions">
                    <button onClick={() => nudgeClipEndpoint(endpoint, -1)} disabled={clipBusy}>{clipPreview.frameDurationMillis ? "−1 frame" : "−10 ms"}</button>
                    <button onClick={() => setClipEndpointFromPlayhead(endpoint)} disabled={clipBusy}>Set at playhead</button>
                    <button onClick={() => nudgeClipEndpoint(endpoint, 1)} disabled={clipBusy}>{clipPreview.frameDurationMillis ? "+1 frame" : "+10 ms"}</button>
                  </div>
                </label>
              ))}
            </div>
            <div className="clip-preview-meta">
              <label><input type="checkbox" checked={clipLoop} onChange={(event) => setClipLoop(event.target.checked)} /> Loop selection</label>
              <span>{formatTimecodeMillis(clipPreview.durationMillis)} · {(clipPreview.sizeBytes / 1024 / 1024).toFixed(1)} MiB{clipPreview.frameDurationMillis ? ` · ${clipPreview.frameDurationMillis.toFixed(3)} ms/frame` : ""}</span>
            </div>
            <footer>
              <button onClick={closeClipDialog} disabled={clipBusy}>Cancel</button>
              <button onClick={() => { void commitClip(true); }} disabled={clipBusy || !clipTitle.trim()}>Add whole file</button>
              <button className="primary" onClick={() => { void commitClip(false); }} disabled={clipBusy || !clipTitle.trim()}>{clipBusy ? "Adding…" : "Add selected clip"}</button>
            </footer>
          </div>
        </div>
      )}

      {confirmIssue && (
        <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="model-install-title">
          <div ref={setupDialogRef} className="setup-dialog panel" tabIndex={-1}>
            <header><div><p className="eyebrow">Processing setup</p><h2 id="model-install-title">Install pinned models?</h2></div><IconButton className="dialog-close" icon="close" label="Close model installation confirmation" onClick={() => { setConfirmIssue(undefined); setLicenseConfirmed(false); }} /></header>
            <p>This downloads several gigabytes of machine-local model files. LyricRail verifies every pinned revision and hash before retrying your songs.</p>
            <div className="license-note"><strong>License notice</strong><span>The lyric alignment model is CC-BY-NC-4.0. Other checkpoints remain under their upstream publisher terms and are not redistributed by LyricRail.</span></div>
            <label className="setup-confirm"><input type="checkbox" autoFocus checked={licenseConfirmed} onChange={(event) => setLicenseConfirmed(event.target.checked)} /><span>I reviewed the size and upstream license terms and want to install these pinned models.</span></label>
            <footer><button onClick={() => { setConfirmIssue(undefined); setLicenseConfirmed(false); }}>Cancel</button><button className="primary" disabled={!licenseConfirmed} onClick={() => { void installModels(); }}>Install models</button></footer>
          </div>
        </div>
      )}

      {aboutOpen && (
        <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="about-title">
          <div ref={aboutDialogRef} className="about-dialog panel" tabIndex={-1}>
            <header><div><p className="eyebrow">Private karaoke</p><h2 id="about-title">LyricRail</h2></div><IconButton className="dialog-close" icon="close" label="Close About LyricRail" autoFocus onClick={() => setAboutOpen(false)} /></header>
            <img src={lyricRailMark} alt="" />
            <p>One focused local karaoke core and player.</p>
            <span>Version {status?.version || "0.8.0"}</span>
            <footer><button className="primary" onClick={() => setAboutOpen(false)}>Close</button></footer>
          </div>
        </div>
      )}

      {!native && <div className="notice">Browser preview — native playback and processing controls are disabled.</div>}
      {shouldShowIssueNotice(anyModalOpen, issuesOpen, systemIssues[0], seenIssueNotice) && <button className="issue-toast" onClick={() => { setSeenIssueNotice(`${systemIssues[0]!.id}:${systemIssues[0]!.updatedAtMillis}`); showIssue(systemIssues[0]!); }}><span><strong>{systemIssues[0]!.title}</strong>{systemIssues[0]!.summary}</span><span>View issue</span></button>}
    </main>
  );
}

export default App;
