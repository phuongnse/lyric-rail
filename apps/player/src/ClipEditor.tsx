import { useEffect, useRef, useState, type RefObject, type KeyboardEvent } from "react";
import { clipView, frameAt, formatTimecodeMillis, parseTimecodeMillis } from "./clipSelection";
export { frameAt } from "./clipSelection";
import "./clipEditor.css";
import { useClipPlayback } from "./useClipPlayback";

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
    if (busy || (event.target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); step(event.key === "ArrowLeft" ? -1 : 1); }
    if (event.key.toLowerCase() === "i" || event.key.toLowerCase() === "o") { event.preventDefault(); mark(event.key.toLowerCase() === "i" ? "startMillis" : "endMillis"); }
    if (event.code === "Space" && !(event.target instanceof HTMLButtonElement)) { event.preventDefault(); if (playing) stop(); else void play(); }
  }}>
    <header><div><p className="eyebrow">Local media · {sections.length} {sections.length === 1 ? "song" : "songs"}</p><h2 id="clip-editor-title">Choose your songs</h2><p>Find your song, listen near each edge, then fine-tune Start and End.</p></div><button aria-label="Close clip editor" onClick={onClose} disabled={busy}>✕</button></header>
    <div className="clip-workspace">
      <section className="clip-viewer" aria-label="Section player">
        <div className="clip-screen" tabIndex={0} aria-label="Preview player. Left and Right step frames. I sets Start, O sets End, Space plays.">
          {preview.videoUrl && !preview.requiresCompatibility ? <video ref={video} src={preview.videoUrl} style={{ visibility: position < Math.floor(videoOffset) ? "hidden" : "visible" }} muted playsInline preload="metadata" onError={() => fail("Video preview could not be decoded on this device.")} />
            : <div className="clip-audio-art"><span>♫</span><strong>{preview.requiresCompatibility ? "Compatible preview needed" : "Audio preview"}</strong><p>{preview.suggestedTitle}</p></div>}
          <audio ref={audio} src={preview.requiresCompatibility ? undefined : preview.previewUrl} preload="metadata" onEnded={ended} onError={() => fail("Audio preview is unavailable.")} />
          <span className="clip-time-badge">{formatTimecodeMillis(position)}</span>
        </div>
        <div className="clip-transport">
          <button aria-label={hasVideo ? "Previous frame" : "Back 10 milliseconds"} onClick={() => step(-1)} disabled={busy || (hasVideo && (!frameData.ready || !frames.length))}>←</button>
          <button className="primary" onClick={() => playing ? stop() : void play()} disabled={busy || preview.requiresCompatibility}>{playing ? "Pause" : "Play"}</button>
          <button aria-label={hasVideo ? "Next frame" : "Forward 10 milliseconds"} onClick={() => step(1)} disabled={busy || (hasVideo && (!frameData.ready || !frames.length))}>→</button>
          <span><strong>{review ? audition === "end" ? "Last 5 seconds" : audition === "start" ? "First 5 seconds" : "Selected section" : "Full source"}</strong>{formatTimecodeMillis(position)} / {formatTimecodeMillis(duration)}{frames.length > 0 && <small>{preview.direct ? "Nearby frame" : "Frame"} {frameAt(boundaries, position) + 1} / {frames.length}</small>}</span>
          <label className="clip-volume"><span>Volume</span><input aria-label="Preview volume" type="range" min={0} max={1} step={.05} value={volume} onChange={(event) => setVolume(Number(event.target.value))} /></label>
        </div>
        <div className="clip-review-panel" aria-label="Review selected section">
          <div className="clip-review-heading"><strong>Listen to this section</strong><span>{formatTimecodeMillis(sectionPosition - active.startMillis)} / {formatTimecodeMillis(active.endMillis - active.startMillis)}</span></div>
          <input aria-label="Seek within section" aria-valuetext={formatTimecodeMillis(sectionPosition - active.startMillis) + " into section"} type="range" min={active.startMillis} max={active.endMillis} step={1} value={sectionPosition} disabled={busy || preview.requiresCompatibility} onChange={(event) => userSeek(Number(event.target.value), true)} />
          <div className="clip-review-actions">
            <button onClick={() => { setReview(true); setAudition(null); void play(undefined, active); }} disabled={busy || preview.requiresCompatibility}>▶ Review section</button>
            <button onClick={() => auditionEdge("start")} disabled={busy || preview.requiresCompatibility}>Play first 5 seconds</button>
            <button className="clip-audition-end" onClick={() => auditionEdge("end")} disabled={busy || preview.requiresCompatibility}>Play last 5 seconds</button>
          </div>
          <p>Seek anywhere above, then Play. Pause keeps your place.</p>
        </div>
        <div className="clip-boundaries">{(["startMillis", "endMillis"] as const).map((endpoint) => {
          const name = endpoint === "startMillis" ? "Start" : "End";
          return <div className="clip-boundary-card" key={endpoint}>
            <div className="clip-boundary-heading"><label htmlFor={"clip-" + endpoint}>{name} <kbd>{endpoint === "startMillis" ? "I" : "O"}</kbd></label><button aria-label={"Go to " + name} disabled={busy} onClick={() => goTo(endpoint)}>Go to {name}</button></div>
            <input id={"clip-" + endpoint} key={selected + "-" + endpoint} aria-label={"Section " + name.toLowerCase() + " time"} value={drafts[selected + "-" + endpoint] ?? formatTimecodeMillis(active[endpoint])} disabled={busy} aria-invalid={!validDrafts} onChange={(event) => { stop(); setDrafts({ ...drafts, [selected + "-" + endpoint]: event.target.value }); }} onBlur={() => {
              const pending = pendingSections.find((section) => section.id === selected)!;
              if (validSections([pending], duration)) {
                patch({ startMillis: pending.startMillis, endMillis: pending.endMillis }); setAudition(null); setPendingNudge(undefined);
                setDrafts((current) => { const next = { ...current }; delete next[selected + "-startMillis"]; delete next[selected + "-endMillis"]; return next; });
              }
            }} />
            <div className="clip-edge-actions">
              <button aria-label={"Move " + name + " back " + (hasVideo ? "one frame" : "10 milliseconds")} disabled={busy || preview.requiresCompatibility} onClick={() => nudge(endpoint, -1)}>− {hasVideo ? "1 frame" : "10 ms"}</button>
              <button aria-label={"Move " + name + " forward " + (hasVideo ? "one frame" : "10 milliseconds")} disabled={busy || preview.requiresCompatibility} onClick={() => nudge(endpoint, 1)}>+ {hasVideo ? "1 frame" : "10 ms"}</button>
              <button disabled={busy || (hasVideo && (!frameData.ready || !frames.length))} onClick={() => mark(endpoint)}>Set at playhead</button>
            </div>
          </div>;
        })}</div>
        <div className="clip-detail" aria-label="Fine trim timeline">
          <div className="clip-zoom-controls"><strong>Fine trim</strong><button onClick={() => fitSection()} disabled={busy}>Fit section</button><button onClick={() => focusView(position)} disabled={busy}>At playhead</button><button aria-label="Zoom in timeline" onClick={() => zoom(.5)} disabled={busy || viewSpan <= 250}>＋</button><button aria-label="Zoom out timeline" onClick={() => zoom(2)} disabled={busy || viewSpan >= duration}>−</button><button aria-label="Pan timeline earlier" disabled={busy || view.start <= 0} onClick={() => focusView((view.start + view.end) / 2 - viewSpan / 2, viewSpan)}>←</button><button aria-label="Pan timeline later" disabled={busy || view.end >= duration} onClick={() => focusView((view.start + view.end) / 2 + viewSpan / 2, viewSpan)}>→</button></div>
          <div className="clip-ruler"><span>{formatTimecodeMillis(view.start)}</span><span>{formatTimecodeMillis((view.start + view.end) / 2)}</span><span>{formatTimecodeMillis(view.end)}</span></div>
          <input className="clip-detail-seek" aria-label="Seek in zoomed timeline" type="range" min={view.start} max={view.end} step={1} value={Math.max(view.start, Math.min(view.end, position))} disabled={busy} onChange={(event) => userSeek(Number(event.target.value), review)} />
          <div className="clip-trim-track" style={{ "--clip-start": viewPercent(active.startMillis) + "%", "--clip-end": viewPercent(active.endMillis) + "%" } as React.CSSProperties}>
            <div className="clip-selected-range" />
            <input aria-label="Drag section start" title="Drag Start; arrow keys move one frame" type="range" min={view.start} max={view.end} step={1} value={Math.max(view.start, Math.min(view.end, active.startMillis))} style={{ visibility: active.startMillis < view.start || active.startMillis > view.end ? "hidden" : "visible" }} disabled={busy} onKeyDown={(event) => handleBoundaryKey(event, "startMillis")} onChange={(event) => { stop(); seek(boundary("startMillis", Number(event.target.value))); }} />
            <input aria-label="Drag section end" title="Drag End; arrow keys move one frame" type="range" min={view.start} max={view.end} step={1} value={Math.max(view.start, Math.min(view.end, active.endMillis))} style={{ visibility: active.endMillis < view.start || active.endMillis > view.end ? "hidden" : "visible" }} disabled={busy} onKeyDown={(event) => handleBoundaryKey(event, "endMillis")} onChange={(event) => { stop(); seek(boundary("endMillis", Number(event.target.value))); }} />
          </div>
        </div>
        <div className="clip-source-overview">
          <label className="clip-scrub"><span>Browse full source</span><input aria-label="Seek preview" type="range" min={0} max={duration} step={1} value={position} disabled={busy} onChange={(event) => userSeek(Number(event.target.value), false)} /></label>
          <div className="clip-timeline" aria-label="Selected sections">
            {sections.map((section, index) => <button key={section.id} className={section.id === selected ? "active" : ""} style={{ left: (section.startMillis / duration * 100) + "%", width: ((section.endMillis - section.startMillis) / duration * 100) + "%", top: (index % 3 * 9) + "px" }} onClick={() => choose(section)} aria-label={"Select section " + (index + 1) + ": " + section.title} disabled={busy}>{index + 1}</button>)}
          </div>
        </div>
        <div className="clip-help"><label><input type="checkbox" checked={loop} onChange={(event) => setLoop(event.target.checked)} /> Loop section</label><span><kbd>←</kbd> <kbd>→</kbd> {hasVideo ? "one frame" : "10 ms"} · <kbd>I</kbd> Start · <kbd>O</kbd> End · <kbd>Space</kbd> play</span></div>
      </section>
      <aside className="clip-sections" aria-label="Songs to add">
        <div className="clip-section-heading"><h3>Your songs <span>{sections.length}</span></h3><p>Each section becomes a separate song.</p></div>
        <ol>{sections.map((section, index) => <li key={section.id} className={section.id === selected ? "active" : ""}>
          <button className="clip-section-select" onClick={() => choose(section)} disabled={busy} aria-label={`Edit section ${index + 1}`} aria-pressed={section.id === selected}><b>{String(index + 1).padStart(2, "0")}</b><span>{formatTimecodeMillis(section.endMillis - section.startMillis)}<small>{formatTimecodeMillis(section.startMillis)} → {formatTimecodeMillis(section.endMillis)}</small></span></button>
          <input aria-label={`Song ${index + 1} title`} value={section.title} maxLength={200} disabled={busy} onFocus={() => { if (selected !== section.id) choose(section); }} onChange={(event) => setSections((items) => items.map((item) => item.id === section.id ? { ...item, title: event.target.value } : item))} />
          <div className="clip-section-actions"><button aria-label={`Move section ${index + 1} up`} onClick={() => reorder(index, -1)} disabled={busy || index === 0}>↑</button><button aria-label={`Move section ${index + 1} down`} onClick={() => reorder(index, 1)} disabled={busy || index === sections.length - 1}>↓</button><button aria-label={`Remove section ${index + 1}`} disabled={busy || sections.length === 1} onClick={() => { const remaining = sections.filter((item) => item.id !== section.id); setSections(remaining); if (selected === section.id) choose(remaining[Math.min(index, remaining.length - 1)]); }}>Remove</button></div>
        </li>)}</ol>
        <button className="clip-add-section" disabled={busy || sections.length >= 128} onClick={() => {
          const startMillis = Math.min(duration - 1, snap(position));
          const section = { id: nextId.current++, title: `${preview.suggestedTitle.slice(0, 185)} · ${sections.length + 1}`, startMillis, endMillis: Math.min(duration, startMillis + 30000) };
          setSections([...sections, section]); choose(section);
        }}>＋ Add section at playhead</button>
        <p className="clip-queue-note">Added to the top of Library & queue in this order. Add each song’s lyrics there to start processing.</p>
      </aside>
    </div>
    {pendingNudge && <p className="clip-help" role="status">Loading the next frame at {pendingNudge.endpoint === "startMillis" ? "Start" : "End"}… Your adjustment will apply when it is ready.</p>}
    {preview.direct && !preview.requiresCompatibility && hasVideo && !frameData.ready && !playing && <p className="clip-help" role="status">{frameData.error || "Loading nearby frames… Playback and seeking are ready."}{frameData.error && <button onClick={frameData.retry}>Retry frame details</button>}</p>}
    {preview.direct && (mediaError || preview.requiresCompatibility) && onCompatible && <p className="clip-help">{preview.requiresCompatibility ? "This file needs a compatible preview for accurate timing." : "This device may need a compatible preview."} Preparing the whole file can take minutes; you can cancel it.<button disabled={busy} onClick={() => { stop(); onCompatible(); }}>Prepare compatible preview</button></p>}
    {preparationError && <p className="clip-error" role="alert">{preparationError}</p>}
    {(!validDrafts || error) && <p className="clip-error" role="alert">{!validDrafts ? "Enter valid times within this file. End must be later than Start." : error}</p>}
    <footer><span>Your original file stays unchanged.</span><button onClick={onClose} disabled={busy}>Cancel</button><button className="primary" disabled={busy || !validDrafts || preview.requiresCompatibility} onClick={() => { stop(); setError(""); void onCommit(pendingSections.map(({ startMillis, endMillis, title }) => ({ startMillis, endMillis, title }))).catch(() => setError("Songs could not be added. Your sections are kept here; try again or open Activity after closing this editor.")); }}>{busy ? "Adding songs…" : `Add ${sections.length} ${sections.length === 1 ? "song" : "songs"} to queue`}</button></footer>
  </div>;
}
