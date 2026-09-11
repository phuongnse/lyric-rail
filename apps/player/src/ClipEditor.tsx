import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { adjacentFrame, clipView, frameAt, formatTimecodeMillis, parseTimecodeMillis } from "./clipSelection";
export { frameAt } from "./clipSelection";
import "./clipEditor.css";
import "./mediaControls.css";
import { useClipPlayback, type LocalClipPreview } from "./useClipPlayback";
export type { LocalClipPreview } from "./useClipPlayback";
import { VideoEditor } from "./VideoEditor";
import { cleanVideoMetadata, validVideoMetadata, type VideoMetadata } from "./videoMetadata";
import { IconButton } from "./Icon";

export type ClipSection = VideoMetadata & { startMillis: number; endMillis: number };
type Section = ClipSection & { id: number };
type Endpoint = "startMillis" | "endMillis";
type Edge = "start" | "end";

export function validSections(sections: ClipSection[], duration: number): boolean {
  return sections.length > 0 && sections.length <= 128 && sections.every((section) => validVideoMetadata(section)
    && Number.isSafeInteger(section.startMillis) && Number.isSafeInteger(section.endMillis)
    && section.startMillis >= 0 && section.endMillis <= duration && section.startMillis < section.endMillis);
}

function cleanSection(section: ClipSection): ClipSection {
  return { ...cleanVideoMetadata(section), startMillis: section.startMillis, endMillis: section.endMillis };
}

function SectionTimeline({ preview, sections, duration, selected, pendingStart, initialWholeSection, position, busy, onSelect, onOpenEditor, onBoundary, onPosition, onPlay, onDraftValidity, onCompatible, onTimelinePoint, onRemove }: {
  preview: LocalClipPreview; sections: Section[]; duration: number; selected: number | null; pendingStart?: number; initialWholeSection: boolean; position: number; busy: boolean;
  onSelect: (section: Section) => void; onOpenEditor: (section: Section) => void;
  onBoundary: (id: number, endpoint: Endpoint, value: number) => void;
  onPosition: (position: number) => void; onPlay: () => void; onDraftValidity: (valid: boolean) => void; onCompatible?: () => void;
  onTimelinePoint: (time: number) => ClipSection | undefined; onRemove: (section: Section) => void;
}) {
  const [review, setReview] = useState(false);
  const [drafts, setDrafts] = useState<Partial<Record<Endpoint, string>>>({});
  const [pendingNudge, setPendingNudge] = useState<{ id: number; endpoint: Endpoint; time: number; direction: -1 | 1 }>();
  const view = clipView(duration, duration / 2, duration);
  const active = sections.find((section) => section.id === selected);
  const playbackRange = review && active ? { startMillis: active.startMillis, endMillis: active.endMillis } : null;
  const { audio, video, position: previewPosition, playing, error, mediaError, fail, frameData, boundaries,
    seek, stop, play, ended, volume, setVolume } = useClipPlayback(preview, playbackRange, false, busy, onPlay);
  const lastAudibleVolume = useRef(.8);
  const span = view.end - view.start;
  const percent = (time: number) => `${Math.max(0, Math.min(100, (time - view.start) / span * 100))}%`;
  const seekPreview = (time: number, pause = true) => {
    setPendingNudge(undefined);
    setReview(Boolean(active && time >= active.startMillis && time <= active.endMillis));
    if (pause) stop();
    onPosition(seek(time));
  };
  const select = (section: Section) => {
    setReview(true); setDrafts({}); setPendingNudge(undefined); stop(); onPosition(seek(section.startMillis)); onSelect(section);
  };
  const clearDraft = (endpoint: Endpoint) => setDrafts((current) => { const next = { ...current }; delete next[endpoint]; return next; });
  const draftValue = (endpoint: Endpoint) => {
    if (!active) return NaN;
    try { return drafts[endpoint] === undefined ? active[endpoint] : parseTimecodeMillis(drafts[endpoint]!); }
    catch { return NaN; }
  };
  const pending = active ? { ...active, startMillis: draftValue("startMillis"), endMillis: draftValue("endMillis") } : undefined;
  const valid = Boolean(pending && validSections([pending], duration));
  useEffect(() => { onDraftValidity(valid); }, [valid, onDraftValidity]);
  const snapToFrame = (time: number) => {
    const rounded = Math.max(0, Math.min(duration, Math.round(time)));
    return rounded < duration && frameData.covers(rounded) && boundaries.length && rounded >= boundaries[0]
      ? boundaries[frameAt(boundaries, rounded)] : rounded;
  };
  const edgeRange = (section: ClipSection, endpoint: Edge) => endpoint === "start"
    ? { startMillis: section.startMillis, endMillis: Math.min(section.endMillis, section.startMillis + 5000) }
    : { startMillis: Math.max(section.startMillis, section.endMillis - 5000), endMillis: section.endMillis };
  const playBoundary = (endpoint: Edge, section: ClipSection) => {
    if (!validSections([section], duration)) return;
    const bounds = edgeRange(section, endpoint);
    setReview(true); stop(); void play(bounds.startMillis, bounds);
  };
  const changeBoundary = (endpoint: Endpoint, time: number, snap = true, audition = false) => {
    if (!active) return;
    setPendingNudge(undefined);
    const value = Math.max(0, Math.min(duration, snap ? snapToFrame(time) : Math.round(time)));
    const next = endpoint === "startMillis" ? value < active.endMillis ? value : active.startMillis
      : value > active.startMillis ? value : active.endMillis;
    const nextSection = { ...active, [endpoint]: next };
    onBoundary(active.id, endpoint, next); clearDraft(endpoint); seekPreview(next);
    if (audition) playBoundary(endpoint === "startMillis" ? "start" : "end", nextSection);
  };
  const commitDraft = (endpoint: Endpoint) => {
    if (!active || drafts[endpoint] === undefined || !valid) return;
    const nextSection = { ...active, startMillis: pending!.startMillis, endMillis: pending!.endMillis };
    onBoundary(active.id, "startMillis", pending!.startMillis); onBoundary(active.id, "endMillis", pending!.endMillis);
    setDrafts({}); seekPreview(pending![endpoint]); playBoundary(endpoint === "startMillis" ? "start" : "end", nextSection);
  };
  const nudge = (endpoint: Endpoint, direction: -1 | 1) => {
    if (!active) return;
    const time = active[endpoint];
    seekPreview(time);
    if (preview.videoUrl && (!frameData.covers(time) || !boundaries.length)) {
      setPendingNudge({ id: active.id, endpoint, time, direction }); frameData.retry(); return;
    }
    changeBoundary(endpoint, adjacentFrame(boundaries, time, direction, duration), true, true);
  };
  useEffect(() => {
    if (!pendingNudge || busy || pendingNudge.id !== selected) return;
    if (frameData.error) { setPendingNudge(undefined); return; }
    if (frameData.covers(pendingNudge.time) && boundaries.length) {
      changeBoundary(pendingNudge.endpoint, adjacentFrame(boundaries, pendingNudge.time, pendingNudge.direction, duration), true, true);
    }
  }, [pendingNudge, frameData.ready, frameData.error, frameData.frames, busy, selected]);
  const handleKey = (event: KeyboardEvent<HTMLInputElement>, endpoint: Endpoint) => {
    if (busy || event.altKey || event.ctrlKey || event.metaKey) return;
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    if (event.key === "Home" || event.key === "End") changeBoundary(endpoint, event.key === "Home" ? 0 : duration, true, true);
    else nudge(endpoint, event.key === "ArrowRight" || event.key === "ArrowUp" ? 1 : -1);
  };
  useEffect(() => { seek(position); }, [preview.clipId]);
  useEffect(() => { if (busy) setPendingNudge(undefined); }, [busy]);
  useEffect(() => { onPosition(previewPosition); }, [previewPosition]);
  const step = (direction: -1 | 1) => {
    if (preview.videoUrl && (!frameData.ready || !boundaries.length)) return;
    const next = Math.max(0, Math.min(duration, adjacentFrame(boundaries, previewPosition, direction, duration)));
    const frame = boundaries.indexOf(next);
    seekPreview(frame >= 0 ? frameData.frames[frame] + .01 : next);
  };
  const togglePreviewMute = () => {
    if (volume > .001) { lastAudibleVolume.current = volume; setVolume(0); }
    else setVolume(lastAudibleVolume.current);
  };
  const timelinePoint = (time: number) => {
    const value = snapToFrame(time);
    const created = onTimelinePoint(value);
    if (created) {
      setReview(true); setPendingNudge(undefined); setDrafts({}); onPosition(seek(created.startMillis)); playBoundary("start", created);
    } else {
      setReview(false); seekPreview(value);
    }
  };
  const index = active ? sections.indexOf(active) + 1 : 0;
  return <section className="clip-picker" aria-label="Choose sections from source" onKeyDown={(event) => {
    if (busy || event.ctrlKey || event.metaKey || event.altKey || (event.target instanceof HTMLElement && event.target.closest("input,textarea,select,button"))) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); step(event.key === "ArrowLeft" ? -1 : 1); }
    if (event.key.toLowerCase() === "i" || event.key.toLowerCase() === "o") { event.preventDefault(); if (active) changeBoundary(event.key.toLowerCase() === "i" ? "startMillis" : "endMillis", previewPosition, true, true); }
    if (event.code === "Space") { event.preventDefault(); if (playing) stop(); else void play(); }
  }}>
    <div className="clip-screen media-player-frame clip-picker-screen" tabIndex={0} aria-label="Source preview. Click the timeline to set Start then End. Arrow keys step frames. Space plays or pauses.">
      {preview.videoUrl && !preview.requiresCompatibility
        ? <video ref={video} aria-label="Section video preview" src={preview.videoUrl} muted playsInline preload="metadata"
            style={{ visibility: previewPosition < Math.floor(preview.videoOffsetMillis ?? 0) ? "hidden" : "visible" }} onError={() => fail("Video preview is unavailable.")} />
        : <div className="clip-audio-art"><span aria-hidden="true">♫</span><strong>{preview.requiresCompatibility ? "Compatible preview needed" : preview.suggestedTitle}</strong></div>}
      <audio ref={audio} aria-label="Section preview audio clock" src={preview.requiresCompatibility ? undefined : preview.previewUrl}
        preload="metadata" onEnded={ended} onError={() => fail("Audio preview is unavailable.")} />
      <div className="media-control-overlay clip-picker-controls" aria-label="Preview controls">
        <div className="media-control-group clip-picker-control-row">
          <IconButton className="media-control-primary" label={playing ? "Pause" : "Play"} icon={playing ? "pause" : "play"} iconSize={20} onClick={() => { setPendingNudge(undefined); if (playing) stop(); else void play(); }} disabled={busy || preview.requiresCompatibility} />
          <IconButton label="Previous frame" icon="previous" onClick={() => step(-1)} disabled={busy || (Boolean(preview.videoUrl) && (!frameData.ready || !boundaries.length))} />
          <IconButton label="Next frame" icon="next" onClick={() => step(1)} disabled={busy || (Boolean(preview.videoUrl) && (!frameData.ready || !boundaries.length))} />
          <IconButton icon={volume <= .001 ? "volume-muted" : "volume-high"} label={volume <= .001 ? "Unmute preview" : "Mute preview"} onClick={togglePreviewMute} disabled={busy || preview.requiresCompatibility} />
          <output className="media-control-time" aria-label="Preview time">{formatTimecodeMillis(previewPosition)} <span>/ {formatTimecodeMillis(duration)}</span></output>
          {active && <span className="clip-selected-note">Section {index} selected</span>}
        </div>
      </div>
    </div>
    <div className="clip-section-ruler" aria-hidden="true"><span>{formatTimecodeMillis(view.start)}</span><span>{formatTimecodeMillis((view.start + view.end) / 2)}</span><span>{formatTimecodeMillis(view.end)}</span></div>
    <div className="clip-timeline-scroll">
      <div className="clip-section-track" aria-label="Sections on source timeline" onPointerDown={(event) => {
        const target = event.target as HTMLElement;
        if (busy || (target !== event.currentTarget && !target.classList.contains("clip-section-track-line"))) return;
        const rect = event.currentTarget.getBoundingClientRect();
        timelinePoint(view.start + (event.clientX - rect.left) / rect.width * span);
      }}>
        <input className="clip-picker-seek" aria-label="Seek source preview and choose position for a new section" type="range" min={view.start} max={view.end} step={1}
          value={Math.max(view.start, Math.min(view.end, position))} disabled={busy} onChange={(event) => seekPreview(Number(event.target.value), false)} />
        <div className="clip-section-track-line" />
        {pendingStart !== undefined && <i className="clip-picker-pending" style={{ left: percent(pendingStart) }} aria-hidden="true" />}
        {sections.filter((section) => section.endMillis >= view.start && section.startMillis <= view.end).map((section) => {
          const number = sections.indexOf(section) + 1, isActive = section.id === selected;
          return <div className={`clip-section-layer ${isActive ? "active" : ""}`} key={section.id} style={{ zIndex: isActive ? 3 : 1 }}>
            <button className={`clip-section-block ${isActive ? "active" : ""} ${initialWholeSection ? "initial" : ""}`} style={{ left: percent(section.startMillis), width: `calc(${percent(section.endMillis)} - ${percent(section.startMillis)})` }}
              aria-label={`Select section ${number}, ${section.title}, ${formatTimecodeMillis(section.startMillis)} to ${formatTimecodeMillis(section.endMillis)}`} aria-pressed={isActive}
              title={`${section.title} · Double-click to edit`} onClick={() => select(section)} onDoubleClick={() => { stop(); setPendingNudge(undefined); onOpenEditor(section); }} disabled={busy}>
              <span>{number}</span><b>{section.title}</b>
            </button>
            {isActive && (["startMillis", "endMillis"] as const).map((endpoint) => <input key={endpoint} className={`clip-section-handle ${endpoint === "startMillis" ? "start" : "end"}`}
              aria-label={`Section ${number} ${endpoint === "startMillis" ? "start" : "end"} handle`} aria-valuetext={formatTimecodeMillis(section[endpoint])}
              type="range" min={view.start} max={view.end} step={1} value={Math.max(view.start, Math.min(view.end, section[endpoint]))}
              style={{ visibility: section[endpoint] < view.start || section[endpoint] > view.end ? "hidden" : "visible" }} disabled={busy}
              onKeyDown={(event) => handleKey(event, endpoint)} onChange={(event) => changeBoundary(endpoint, Number(event.target.value))} />)}
          </div>;
        })}
        {position >= view.start && position <= view.end && <i className="clip-picker-playhead" style={{ left: percent(position) }} aria-hidden="true" />}
      </div>
    </div>
    <p className="clip-timeline-hint" role="status">{pendingStart !== undefined
      ? `Start ${formatTimecodeMillis(pendingStart)} selected. Click the timeline again to set End.`
      : initialWholeSection ? "Whole file selected. Click the timeline to start a new cut, or edit and queue it as-is."
      : "Click the timeline to set Start, then click again to set End and create a section."}</p>
    {active && <div className="clip-timeline-actions">
      <span>Section {index} · {formatTimecodeMillis(active.startMillis)}–{formatTimecodeMillis(active.endMillis)}</span>
      <button className="clip-edit-action" onClick={() => { stop(); setPendingNudge(undefined); onOpenEditor(active); }} disabled={busy}>Edit video</button>
      <button aria-label={`Remove section ${index}`} onClick={() => { stop(); onRemove(active); }} disabled={busy || sections.length === 0}>Remove</button>
    </div>}
    {active && <div className="clip-picker-boundaries" aria-label={`Exact frames for section ${index}`}>
      {["startMillis", "endMillis"].map((endpoint) => {
        const edge = endpoint as Endpoint, name = edge === "startMillis" ? "Start" : "End";
        return <div className="clip-picker-edge" key={edge}>
          <div className="clip-boundary-heading"><div><label htmlFor={`picker-${edge}`}>{name}</label><output className="clip-frame-readout" aria-label={`Section ${index} ${name.toLowerCase()} frame`}>Frame at {formatTimecodeMillis(active[edge])}</output></div></div>
          <div className="clip-time-input">
            <IconButton label={`Move ${name} back one frame`} icon="previous" onClick={() => nudge(edge, -1)} disabled={busy || preview.requiresCompatibility} />
            <input id={`picker-${edge}`} aria-label={`Section ${index} ${name.toLowerCase()} time`} aria-invalid={!valid} value={drafts[edge] ?? formatTimecodeMillis(active[edge])}
              onChange={(event) => { setPendingNudge(undefined); stop(); setDrafts((current) => ({ ...current, [edge]: event.target.value })); }}
              onBlur={() => commitDraft(edge)} onKeyDown={(event) => { if (event.key === "Enter") { commitDraft(edge); event.currentTarget.blur(); } if (event.key === "Escape") { event.stopPropagation(); clearDraft(edge); } }} disabled={busy} />
            <IconButton label={`Move ${name} forward one frame`} icon="next" onClick={() => nudge(edge, 1)} disabled={busy || preview.requiresCompatibility} />
          </div>
        </div>;
      })}
    </div>}
    {!valid && active && <p className="clip-error" role="alert">Enter a Start before End, within the file. Press Escape to discard a time edit.</p>}
    {pendingNudge && <p className="clip-help" role="status">Loading nearby frames…</p>}
    {frameData.error && <p className="clip-help" role="status">{frameData.error}<button onClick={frameData.retry} disabled={busy}>Retry frame details</button></p>}
    {error && <p className="clip-error" role="alert">{error}</p>}
    {(preview.requiresCompatibility || mediaError) && onCompatible && <button onClick={() => { stop(); onCompatible(); }} disabled={busy}>Prepare compatible preview</button>}
  </section>;
}

export default function ClipEditor({ preview, busy, containerRef, onClose, onCommit, onPlay, onCompatible, preparationError }: {
  preview: LocalClipPreview; busy: boolean; containerRef: RefObject<HTMLDivElement | null>;
  onClose: () => void; onCommit: (sections: ClipSection[]) => Promise<void>; onPlay: () => void; onCompatible?: () => void; preparationError?: string;
}) {
  const [sections, setSections] = useState<Section[]>([{ id: 1, startMillis: 0, endMillis: preview.durationMillis, title: preview.suggestedTitle }]);
  const [selected, setSelected] = useState<number | null>(1);
  const [initialWholeSection, setInitialWholeSection] = useState(true);
  const [pendingStart, setPendingStart] = useState<number>();
  const [timelineError, setTimelineError] = useState("");
  const [mode, setMode] = useState<"select" | "edit">("select");
  const [position, setPosition] = useState(0);
  const [commitError, setCommitError] = useState("");
  const [validDraft, setValidDraft] = useState(true);
  useEffect(() => {
    if (mode === "edit") return;
    containerRef.current?.querySelector<HTMLElement>(".clip-section-block.active")?.focus();
  }, [mode]);
  const nextId = useRef(2);
  const duration = preview.durationMillis;
  const active = sections.find((section) => section.id === selected);
  const valid = validSections(sections, duration);
  const selectSection = (section: Section) => { setSelected(section.id); setPendingStart(undefined); setTimelineError(""); setPosition(section.startMillis); };
  const openEditor = (section: Section) => { selectSection(section); setCommitError(""); setMode("edit"); };
  const updateBoundary = (id: number, endpoint: Endpoint, value: number) => {
    setInitialWholeSection(false);
    setSections((items) => items.map((section) => section.id === id ? { ...section, [endpoint]: value } : section));
  };
  const removeSection = (section: Section) => {
    const index = sections.indexOf(section);
    const remaining = sections.filter((item) => item.id !== section.id);
    setInitialWholeSection(false); setSections(remaining);
    const next = remaining[Math.min(Math.max(0, index), remaining.length - 1)];
    setSelected(next?.id ?? null); setPosition(next?.startMillis ?? 0);
  };
  const timelinePoint = (time: number): ClipSection | undefined => {
    const value = Math.max(0, Math.min(duration, Math.round(time)));
    if (pendingStart === undefined) {
      if (value >= duration) { setTimelineError("Choose a Start before the end of the file."); return; }
      setPendingStart(value); setSelected(null); setSections((items) => initialWholeSection ? [] : items); setInitialWholeSection(false); setTimelineError(""); setPosition(value); return;
    }
    if (value <= pendingStart) { setTimelineError("End must be later than Start. Click a later point."); setPosition(value); return; }
    const section: Section = { id: nextId.current++, startMillis: pendingStart, endMillis: value,
      title: `${preview.suggestedTitle.slice(0, 185)} · ${sections.length + 1}` };
    setSections((items) => [...items, section]); setSelected(section.id); setPendingStart(undefined); setTimelineError(""); setPosition(section.startMillis);
    return section;
  };
  const saveSection = (metadata: VideoMetadata) => {
    setSections((items) => items.map((item) => item.id === selected ? { ...item, artist: undefined, composer: undefined, lyrics: undefined, ...metadata } : item));
    setMode("select"); setValidDraft(true); setPosition(active?.startMillis ?? 0); setCommitError("");
  };
  return <><div inert={mode === "edit"} aria-hidden={mode === "edit" ? true : undefined} ref={containerRef} className="clip-dialog clip-workbench panel" tabIndex={-1}>
    <header><div><h2 id="clip-editor-title">Trim your song</h2><p>{preview.suggestedTitle}</p></div><IconButton label="Close clip editor" icon="close" onClick={onClose} disabled={busy} /></header>
      <SectionTimeline preview={preview} sections={sections} duration={duration} selected={selected} pendingStart={pendingStart} initialWholeSection={initialWholeSection} position={position} busy={busy || mode === "edit"}
        onSelect={selectSection} onOpenEditor={openEditor} onBoundary={updateBoundary} onPosition={setPosition} onPlay={onPlay} onDraftValidity={setValidDraft} onCompatible={onCompatible} onTimelinePoint={timelinePoint} onRemove={removeSection} />
      {timelineError && <p className="clip-error" role="alert">{timelineError}</p>}
      {preparationError && <p className="clip-error" role="alert">{preparationError}</p>}
      {commitError && <p className="clip-error" role="alert">{commitError}</p>}
      {!valid && !timelineError && <p className="clip-error" role="alert">Choose a Start and End on the timeline before adding songs.</p>}
      <footer><span>Original file stays unchanged</span><button className="primary" disabled={busy || !valid || !validDraft || preview.requiresCompatibility} onClick={() => { setCommitError(""); void onCommit(sections.map(cleanSection)).catch(() => setCommitError("Songs could not be added. Your sections are kept here; try again or open Activity.")); }}>{busy ? "Adding songs…" : `Add ${sections.length} ${sections.length === 1 ? "song" : "songs"} to queue`}</button></footer>
  </div>{mode === "edit" && active && <VideoEditor key={selected} preview={preview} range={active} value={active} busy={busy} onClose={() => setMode("select")} onSave={saveSection} onPlay={onPlay} onCompatible={onCompatible} preparationError={preparationError} />}
  </>;
}
