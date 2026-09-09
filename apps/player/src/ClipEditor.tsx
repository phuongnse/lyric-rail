import { useEffect, useRef, useState, type RefObject, type KeyboardEvent } from "react";
import { clipView, frameAt, formatTimecodeMillis, parseTimecodeMillis } from "./clipSelection";
export { frameAt } from "./clipSelection";
import "./clipEditor.css";
import { useClipPlayback } from "./useClipPlayback";
import { IconButton } from "./Icon";

export type LocalClipPreview = {
  direct?: boolean; requiresCompatibility?: boolean; clipId: string; suggestedTitle: string; sizeBytes: number; durationMillis: number;
  frameDurationMillis?: number; frameTimesMillis?: number[]; previewUrl: string; videoUrl?: string; videoOffsetMillis?: number;
};
export type ClipSection = { startMillis: number; endMillis: number; title: string };
type Section = ClipSection & { id: number };
type Endpoint = "startMillis" | "endMillis";

export function validSections(sections: ClipSection[], duration: number): boolean {
  return sections.length > 0 && sections.length <= 128 && sections.every((section) => section.title.trim().length > 0 && section.title.length <= 200
    && !/[\u0000-\u001f\u007f-\u009f]/u.test(section.title) && Number.isSafeInteger(section.startMillis) && Number.isSafeInteger(section.endMillis)
    && section.startMillis >= 0 && section.endMillis <= duration && section.startMillis < section.endMillis);
}

export default function ClipEditor({ preview, busy, containerRef, onClose, onCommit, onPlay, onCompatible, preparationError }: {
  preview: LocalClipPreview; busy: boolean; containerRef: RefObject<HTMLDivElement | null>;
  onClose: () => void; onCommit: (sections: ClipSection[]) => Promise<void>; onPlay: () => void; onCompatible?: () => void; preparationError?: string;
}) {
  const [sections, setSections] = useState<Section[]>([{ id: 1, startMillis: 0, endMillis: preview.durationMillis, title: preview.suggestedTitle }]);
  const [selected, setSelected] = useState(1);
  const [loop, setLoop] = useState(false);
  const [review, setReview] = useState(true);
  const [audition, setAudition] = useState<"start" | "end" | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [pendingNudge, setPendingNudge] = useState<{ id: number; endpoint: Endpoint; time: number; direction: -1 | 1 }>();
  const [view, setView] = useState(() => clipView(preview.durationMillis, preview.durationMillis / 2, preview.durationMillis));
  const nextId = useRef(2);
  const active = sections.find((section) => section.id === selected)!;
  const duration = preview.durationMillis, hasVideo = Boolean(preview.videoUrl);
  const playbackRange = review ? {
    startMillis: audition === "end" ? Math.max(active.startMillis, active.endMillis - 5000) : active.startMillis,
    endMillis: audition === "start" ? Math.min(active.endMillis, active.startMillis + 5000) : active.endMillis,
  } : null;
  const { audio, video, position, playing, volume, setVolume, error, setError, mediaError, fail,
    frameData, boundaries, seek, stop, play, ended } = useClipPlayback(preview, playbackRange, loop, busy, onPlay);
  const frames = frameData.frames;
  const focusView = (time: number, span = 10000) => setView(clipView(duration, time, span));
  const fitSection = (section = active) => focusView((section.startMillis + section.endMillis) / 2,
    section.endMillis - section.startMillis + Math.min(10000, (section.endMillis - section.startMillis) / 5));
  const userSeek = (time: number, selection: boolean) => {
    setPendingNudge(undefined); setAudition(null); setReview(selection);
    seek(selection ? Math.max(active.startMillis, Math.min(active.endMillis, time)) : time);
  };
  const goTo = (endpoint: Endpoint) => {
    stop(); userSeek(active[endpoint], true); setAudition(endpoint === "endMillis" ? "end" : "start"); focusView(active[endpoint]);
  };
  const auditionEdge = (edge: "start" | "end") => {
    stop(); setPendingNudge(undefined); setReview(true); setAudition(edge);
    const bounds = { startMillis: edge === "end" ? Math.max(active.startMillis, active.endMillis - 5000) : active.startMillis,
      endMillis: edge === "start" ? Math.min(active.endMillis, active.startMillis + 5000) : active.endMillis };
    focusView((bounds.startMillis + bounds.endMillis) / 2);
    void play(bounds.startMillis, bounds);
  };
  useEffect(() => {
    setPendingNudge(undefined); setReview(true); setAudition(null);
    setView(clipView(duration, duration / 2, duration));
  }, [preview.clipId]);
  useEffect(() => { if (busy) setPendingNudge(undefined); }, [busy]);
  const pendingSections = sections.map((section) => {
    const result = { ...section };
    for (const endpoint of ["startMillis", "endMillis"] as const) {
      const draft = drafts[`${section.id}-${endpoint}`];
      if (draft !== undefined) { try { result[endpoint] = parseTimecodeMillis(draft); } catch { result[endpoint] = NaN; } }
    }
    return result;
  });
  const validDrafts = validSections(pendingSections, duration);
  const invalidSection = pendingSections.findIndex((section) => !validSections([section], duration));
  const videoOffset = preview.videoOffsetMillis ?? 0;
  const patch = (change: Partial<ClipSection>) => setSections((items) => items.map((item) => item.id === selected ? { ...item, ...change } : item));
  const clearDraft = (endpoint: Endpoint) => setDrafts((current) => {
    const next = { ...current }; delete next[`${selected}-${endpoint}`]; return next;
  });
  const snap = (time: number) => time >= duration ? duration : frameData.covers(time) && boundaries.length && time >= boundaries[0]
    ? boundaries[frameAt(boundaries, time)] : Math.round(time);
  const boundary = (endpoint: Endpoint, time: number) => {
    const snapped = snap(time);
    const value = endpoint === "startMillis" ? snapped < active.endMillis ? Math.max(0, snapped) : active.startMillis
      : snapped > active.startMillis ? Math.min(duration, snapped) : active.endMillis;
    patch({ [endpoint]: value }); clearDraft(endpoint); setAudition(null); setPendingNudge(undefined); return value;
  };
  const mark = (endpoint: Endpoint) => {
    if (hasVideo && (!frameData.ready || !frames.length)) return;
    boundary(endpoint, position);
  };
  const adjacent = (time: number, direction: -1 | 1) => {
    const current = Math.floor(time);
    if (!boundaries.length) return current + direction * 10;
    if (current < boundaries[0]) return direction > 0 ? boundaries[0] : current - 10;
    const index = frameAt(boundaries, current);
    return direction > 0 ? boundaries[index + 1] ?? duration
      : boundaries[index] < current ? boundaries[index] : boundaries[index - 1] ?? 0;
  };
  const applyNudge = (endpoint: Endpoint, time: number) => {
    const next = boundary(endpoint, time);
    setReview(true); setAudition(endpoint === "endMillis" ? "end" : "start");
    seek(next); focusView(next, Math.min(10000, view.end - view.start));
  };
  const nudge = (endpoint: Endpoint, direction: -1 | 1) => {
    stop(); setAudition(null); setPendingNudge(undefined);
    const time = active[endpoint];
    if (hasVideo && (!frameData.covers(time) || !frames.length)) {
      seek(time); focusView(time); setPendingNudge({ id: selected, endpoint, time, direction }); frameData.retry();
      return;
    }
    applyNudge(endpoint, adjacent(time, direction));
  };
  useEffect(() => {
    if (!pendingNudge || busy || pendingNudge.id !== selected) return;
    if (frameData.error) { setPendingNudge(undefined); return; }
    if (!frameData.covers(pendingNudge.time) || !frames.length) return;
    applyNudge(pendingNudge.endpoint, adjacent(pendingNudge.time, pendingNudge.direction));
  }, [pendingNudge, frameData.ready, frameData.error, frames, busy, selected]);
  const handleBoundaryKey = (event: KeyboardEvent<HTMLInputElement>, endpoint: Endpoint) => {
    if (busy || event.ctrlKey || event.metaKey || event.altKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    if (event.key === "Home" || event.key === "End") { stop(); seek(boundary(endpoint, event.key === "Home" ? 0 : duration)); return; }
    nudge(endpoint, event.key === "ArrowRight" || event.key === "ArrowUp" ? 1 : -1);
  };
  const step = (direction: -1 | 1) => {
    stop(); setPendingNudge(undefined); setAudition(null);
    if (hasVideo && (!frameData.ready || !frames.length)) return;
    const target = Math.max(0, Math.min(duration, adjacent(position, direction)));
    const index = boundaries.indexOf(target);
    seek(index >= 0 ? frames[index] + .01 : target);
    if (target < view.start || target > view.end) focusView(target, view.end - view.start);
  };
  const choose = (section: Section) => {
    stop(); setPendingNudge(undefined); setReview(true); setAudition(null); setSelected(section.id); seek(section.startMillis); fitSection(section);
  };
  const sectionPosition = Math.max(active.startMillis, Math.min(active.endMillis, position));
  const viewSpan = view.end - view.start;
  const viewPercent = (time: number) => Math.max(0, Math.min(100, (time - view.start) / viewSpan * 100));
  const zoom = (factor: number) => focusView(position, Math.max(250, Math.min(duration, viewSpan * factor)));
  const reorder = (index: number, delta: number) => setSections((items) => {
    const reordered = [...items]; const target = index + delta;
    if (target >= 0 && target < items.length) [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    return reordered;
  });
  return <div ref={containerRef} className="clip-dialog clip-workbench panel" tabIndex={-1} onKeyDown={(event) => {
    if (busy || (event.target instanceof HTMLElement && event.target.closest("input, textarea, select, summary")) || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); step(event.key === "ArrowLeft" ? -1 : 1); }
    if (event.key.toLowerCase() === "i" || event.key.toLowerCase() === "o") { event.preventDefault(); mark(event.key.toLowerCase() === "i" ? "startMillis" : "endMillis"); }
    if (event.code === "Space" && !(event.target instanceof HTMLButtonElement)) { event.preventDefault(); if (playing) stop(); else void play(); }
  }}>
    <header><div><h2 id="clip-editor-title">Trim your song</h2><p>Drag the ends, listen, then add your song.</p></div><IconButton label="Close clip editor" icon="close" onClick={onClose} disabled={busy} /></header>
    <div className="clip-workspace">
      <section className="clip-viewer" aria-label="Section player">
        <div className="clip-screen" tabIndex={0} aria-label="Preview player. Left and Right step frames. I sets Start, O sets End, Space plays.">
          {preview.videoUrl && !preview.requiresCompatibility ? <video ref={video} src={preview.videoUrl} style={{ visibility: position < Math.floor(videoOffset) ? "hidden" : "visible" }} muted playsInline preload="metadata" onError={() => fail("Video preview could not be decoded on this device.")} />
            : <div className="clip-audio-art"><span>♫</span><strong>{preview.requiresCompatibility ? "Compatible preview needed" : "Audio preview"}</strong></div>}
          <audio ref={audio} src={preview.requiresCompatibility ? undefined : preview.previewUrl} preload="metadata" onEnded={ended} onError={() => fail("Audio preview is unavailable.")} />
        </div>
        <div className="clip-transport">
          <button className="clip-play" onClick={() => playing ? stop() : void play()} disabled={busy || preview.requiresCompatibility}>{playing ? "Pause" : "Play"}</button>
          <span className="clip-clock">{formatTimecodeMillis(position)}<small>{review ? audition === "end" ? "Last 5 seconds" : audition === "start" ? "First 5 seconds" : "Selected song" : "Browsing full file"}</small></span>
          <label className="clip-loop"><input type="checkbox" checked={loop} disabled={busy} onChange={(event) => { setLoop(event.target.checked); if (event.target.checked && !review) userSeek(position, true); }} /> Loop</label>
        </div>
        <div className="clip-review-panel" aria-label="Review selected section">
          <input aria-label="Seek within section" aria-valuetext={formatTimecodeMillis(sectionPosition - active.startMillis) + " into section"} type="range" min={active.startMillis} max={active.endMillis} step={1} value={sectionPosition} disabled={busy || preview.requiresCompatibility} onPointerDown={() => userSeek(sectionPosition, true)} onKeyDown={(event) => { if (["Home", "End", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown"].includes(event.key)) userSeek(sectionPosition, true); }} onChange={(event) => userSeek(Number(event.target.value), true)} />
          <div className="clip-review-heading"><span>Listen within this song</span><span>{formatTimecodeMillis(active.endMillis - active.startMillis)}</span></div>
        </div>
      </section>
      <section className="clip-selection" aria-label="Song boundaries">
        <label className="clip-title">Song name<input aria-label={`Song ${sections.findIndex(section => section.id === selected) + 1} title`} value={active.title} maxLength={200} disabled={busy} onChange={(event) => patch({ title: event.target.value })} /></label>
        <div className="clip-boundaries">{(["startMillis", "endMillis"] as const).map((endpoint) => {
          const name = endpoint === "startMillis" ? "Start" : "End";
          return <div className="clip-boundary-card" key={endpoint}>
            <div className="clip-boundary-heading"><label htmlFor={"clip-" + endpoint}>{name}</label><button disabled={busy || (hasVideo && (!frameData.ready || !frames.length))} onClick={() => mark(endpoint)}>Set at playhead</button></div>
            <div className="clip-time-input">
              <IconButton label={"Move " + name + " back " + (hasVideo ? "one frame" : "10 milliseconds")} icon="previous" disabled={busy || preview.requiresCompatibility} onClick={() => nudge(endpoint, -1)} />
              <input id={"clip-" + endpoint} key={selected + "-" + endpoint} aria-label={"Section " + name.toLowerCase() + " time"} value={drafts[selected + "-" + endpoint] ?? formatTimecodeMillis(active[endpoint])} disabled={busy} aria-invalid={!validDrafts} onChange={(event) => { stop(); setPendingNudge(undefined); setDrafts({ ...drafts, [selected + "-" + endpoint]: event.target.value }); }} onBlur={() => {
                const pending = pendingSections.find((section) => section.id === selected)!;
                if (validSections([pending], duration)) {
                  patch({ startMillis: pending.startMillis, endMillis: pending.endMillis }); setAudition(null); setPendingNudge(undefined);
                  setDrafts((current) => { const next = { ...current }; delete next[selected + "-startMillis"]; delete next[selected + "-endMillis"]; return next; });
                }
              }} />
              <IconButton label={"Move " + name + " forward " + (hasVideo ? "one frame" : "10 milliseconds")} icon="next" disabled={busy || preview.requiresCompatibility} onClick={() => nudge(endpoint, 1)} />
            </div>
            <button className="clip-audition" aria-label={endpoint === "startMillis" ? "Play first 5 seconds" : "Play last 5 seconds"} onClick={() => auditionEdge(endpoint === "startMillis" ? "start" : "end")} disabled={busy || preview.requiresCompatibility}>{endpoint === "startMillis" ? "Listen to start" : "Listen to end"}<span>5 sec</span></button>
          </div>;
        })}</div>
      </section>
    </div>
    <section className="clip-detail" aria-label="Select a section from the file">
      <div className="clip-timeline-heading"><strong>Choose the section</strong><span>Drag Start and End</span><button onClick={() => focusView(duration / 2, duration)} disabled={busy}>Whole file</button><button onClick={() => fitSection()} disabled={busy}>Fit song</button></div>
      <div className="clip-ruler"><span>{formatTimecodeMillis(view.start)}</span><span>{formatTimecodeMillis((view.start + view.end) / 2)}</span><span>{formatTimecodeMillis(view.end)}</span></div>
      <input className="clip-detail-seek" aria-label="Seek source timeline" type="range" min={view.start} max={view.end} step={1} value={Math.max(view.start, Math.min(view.end, position))} disabled={busy} onChange={(event) => { const time = Number(event.target.value); userSeek(time, time >= active.startMillis && time <= active.endMillis); }} />
      <div className="clip-trim-track" style={{ "--clip-start": viewPercent(active.startMillis) + "%", "--clip-end": viewPercent(active.endMillis) + "%" } as React.CSSProperties}>
        <div className="clip-selected-range" />
        <input aria-label="Drag section start" title="Drag Start; arrow keys move one frame" type="range" min={view.start} max={view.end} step={1} value={Math.max(view.start, Math.min(view.end, active.startMillis))} style={{ visibility: active.startMillis < view.start || active.startMillis > view.end ? "hidden" : "visible" }} disabled={busy} onKeyDown={(event) => handleBoundaryKey(event, "startMillis")} onChange={(event) => { stop(); seek(boundary("startMillis", Number(event.target.value))); }} />
        <input aria-label="Drag section end" title="Drag End; arrow keys move one frame" type="range" min={view.start} max={view.end} step={1} value={Math.max(view.start, Math.min(view.end, active.endMillis))} style={{ visibility: active.endMillis < view.start || active.endMillis > view.end ? "hidden" : "visible" }} disabled={busy} onKeyDown={(event) => handleBoundaryKey(event, "endMillis")} onChange={(event) => { stop(); seek(boundary("endMillis", Number(event.target.value))); }} />
      </div>
      <details className="clip-precision">
        <summary tabIndex={0}>More controls</summary>
        <div className="clip-zoom-controls"><button aria-label="Zoom in timeline" onClick={() => zoom(.5)} disabled={busy || viewSpan <= 250}>Zoom +</button><button aria-label="Zoom out timeline" onClick={() => zoom(2)} disabled={busy || viewSpan >= duration}>Zoom −</button><button aria-label="Pan timeline earlier" disabled={busy || view.start <= 0} onClick={() => focusView((view.start + view.end) / 2 - viewSpan / 2, viewSpan)}>Earlier</button><button aria-label="Pan timeline later" disabled={busy || view.end >= duration} onClick={() => focusView((view.start + view.end) / 2 + viewSpan / 2, viewSpan)}>Later</button><button onClick={() => goTo("startMillis")} disabled={busy}>Go to Start</button><button onClick={() => goTo("endMillis")} disabled={busy}>Go to End</button></div>
        <div className="clip-frame-controls"><button aria-label={hasVideo ? "Previous frame" : "Back 10 milliseconds"} onClick={() => step(-1)} disabled={busy || (hasVideo && (!frameData.ready || !frames.length))}>{hasVideo ? "Previous frame" : "Back 10 ms"}</button><button aria-label={hasVideo ? "Next frame" : "Forward 10 milliseconds"} onClick={() => step(1)} disabled={busy || (hasVideo && (!frameData.ready || !frames.length))}>{hasVideo ? "Next frame" : "Forward 10 ms"}</button><label className="clip-volume">Volume<input aria-label="Preview volume" type="range" min={0} max={1} step={.05} value={volume} onChange={(event) => setVolume(Number(event.target.value))} /></label></div>
        <p className="clip-shortcuts"><kbd>←</kbd> <kbd>→</kbd> {hasVideo ? "one frame" : "10 ms"} · <kbd>I</kbd> Start · <kbd>O</kbd> End · <kbd>Space</kbd> play / pause</p>
      </details>
    </section>
    {sections.length > 1 && <details className="clip-sections"><summary tabIndex={0}>{sections.length} songs selected <span>Manage songs</span></summary><ol>{sections.map((section, index) => <li key={section.id} className={section.id === selected ? "active" : ""}>
      <button className="clip-section-select" onClick={() => choose(section)} disabled={busy} aria-label={`Edit section ${index + 1}`} aria-pressed={section.id === selected}><b>{index + 1}</b><span>{section.title}<small>{formatTimecodeMillis(section.startMillis)} → {formatTimecodeMillis(section.endMillis)}</small></span></button>
      <div className="clip-section-actions"><button aria-label={`Move section ${index + 1} up`} onClick={() => reorder(index, -1)} disabled={busy || index === 0}>Move up</button><button aria-label={`Move section ${index + 1} down`} onClick={() => reorder(index, 1)} disabled={busy || index === sections.length - 1}>Move down</button><button aria-label={`Remove section ${index + 1}`} disabled={busy} onClick={() => { const remaining = sections.filter((item) => item.id !== section.id); setSections(remaining); if (selected === section.id) choose(remaining[Math.min(index, remaining.length - 1)]); }}>Remove</button></div>
    </li>)}</ol></details>}
    {pendingNudge && <p className="clip-help" role="status">Loading the next frame at {pendingNudge.endpoint === "startMillis" ? "Start" : "End"}… Your adjustment will apply when it is ready.</p>}
    {preview.direct && !preview.requiresCompatibility && hasVideo && !frameData.ready && !playing && <p className="clip-help" role="status">{frameData.error || "Loading nearby frames… Playback and seeking are ready."}{frameData.error && <button onClick={frameData.retry}>Retry frame details</button>}</p>}
    {preview.direct && (mediaError || preview.requiresCompatibility) && onCompatible && <p className="clip-help">{preview.requiresCompatibility ? "This file needs a compatible preview for accurate timing." : "This device may need a compatible preview."} Preparing the whole file can take minutes; you can cancel it.<button disabled={busy} onClick={() => { stop(); onCompatible(); }}>Prepare compatible preview</button></p>}
    {preparationError && <p className="clip-error" role="alert">{preparationError}</p>}
    {(!validDrafts || error) && <p className="clip-error" role="alert">{!validDrafts ? `Check the name, Start and End for song ${invalidSection + 1}. End must be later than Start and within this file.` : error}{invalidSection >= 0 && sections[invalidSection].id !== selected && <button onClick={() => choose(sections[invalidSection])} disabled={busy}>Go to song {invalidSection + 1}</button>}</p>}
    <footer><button className="clip-add-section" disabled={busy || sections.length >= 128} onClick={() => {
      const startMillis = Math.min(duration - 1, snap(position));
      const section = { id: nextId.current++, title: `${preview.suggestedTitle.slice(0, 185)} · ${sections.length + 1}`, startMillis, endMillis: Math.min(duration, startMillis + 30000) };
      setSections([...sections, section]); choose(section);
    }}>Add another song</button><span>Original file stays unchanged</span><button className="primary" disabled={busy || !validDrafts || preview.requiresCompatibility} onClick={() => { stop(); setError(""); void onCommit(pendingSections.map(({ startMillis, endMillis, title }) => ({ startMillis, endMillis, title }))).catch(() => setError("Songs could not be added. Your sections are kept here; try again or open Activity after closing this editor.")); }}>{busy ? "Adding songs…" : `Add ${sections.length} ${sections.length === 1 ? "song" : "songs"} to queue`}</button></footer>
  </div>;
}
