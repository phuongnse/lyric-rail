import { useEffect, useMemo, useRef, useState, type RefObject, type KeyboardEvent } from "react";
import { formatTimecodeMillis, parseTimecodeMillis } from "./clipSelection";
import "./clipEditor.css";
import { useClipFrames } from "./useClipFrames";

export type LocalClipPreview = {
  direct?: boolean; clipId: string; suggestedTitle: string; sizeBytes: number; durationMillis: number;
  frameDurationMillis?: number; frameTimesMillis?: number[]; previewUrl: string; videoUrl?: string; videoOffsetMillis?: number;
};
export type ClipSection = { startMillis: number; endMillis: number; title: string };
type Section = ClipSection & { id: number };
type Endpoint = "startMillis" | "endMillis";

export function frameAt(frames: number[], time: number): number {
  let low = 0, high = frames.length;
  while (low < high) { const middle = (low + high) >>> 1; if (frames[middle] <= time + .01) low = middle + 1; else high = middle; }
  return Math.max(0, low - 1);
}

export function validSections(sections: ClipSection[], duration: number): boolean {
  return sections.length > 0 && sections.length <= 128 && sections.every((section) => section.title.trim().length > 0 && section.title.length <= 200
    && !/[\u0000-\u001f\u007f-\u009f]/u.test(section.title) && Number.isSafeInteger(section.startMillis) && Number.isSafeInteger(section.endMillis)
    && section.startMillis >= 0 && section.endMillis <= duration && section.startMillis < section.endMillis);
}

export default function ClipEditor({ preview, busy, containerRef, onClose, onCommit, onPlay, onCompatible }: {
  preview: LocalClipPreview; busy: boolean; containerRef: RefObject<HTMLDivElement | null>;
  onClose: () => void; onCommit: (sections: ClipSection[]) => Promise<void>; onPlay: () => void; onCompatible?: () => void;
}) {
  const [sections, setSections] = useState<Section[]>([{ id: 1, startMillis: 0, endMillis: preview.durationMillis, title: preview.suggestedTitle }]);
  const [selected, setSelected] = useState(1);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(false);
  const [error, setError] = useState("");
  const [review, setReview] = useState(false);
  const [volume, setVolume] = useState(.8);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const nextId = useRef(2);
  const audio = useRef<HTMLAudioElement>(null), video = useRef<HTMLVideoElement>(null);
  const active = sections.find((section) => section.id === selected)!;
  const frameData = useClipFrames(preview, position, playing);
  const frames = frameData.frames;
  const hasVideo = Boolean(preview.videoUrl);
  const [mediaError, setMediaError] = useState(false);
  useEffect(() => { setMediaError(false); setError(""); }, [preview.clipId]);
  // Native trim ranges use integer milliseconds. Quantize once, including for lookup,
  // so a displayed boundary never snaps back to the preceding fractional frame.
  const boundaries = useMemo(() => frames.map(Math.floor), [frames]);
  const duration = preview.durationMillis;
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
  const videoTime = (time: number) => {
    const index = frameAt(boundaries, time);
    const presentation = boundaries[index] === time ? frames[index] + .01 : time;
    return Math.max(0, presentation - videoOffset) / 1000;
  };
  useEffect(() => { if (audio.current) audio.current.volume = volume; }, [volume]);
  const stop = () => {
    audio.current?.pause(); video.current?.pause();
    const time = (audio.current?.currentTime ?? 0) * 1000;
    if (video.current) video.current.currentTime = videoTime(time);
    setPosition(time); setPlaying(false);
  };
  const seek = (time: number) => {
    const bounded = Math.max(0, Math.min(duration, time));
    if (audio.current) audio.current.currentTime = bounded / 1000;
    if (video.current) video.current.currentTime = videoTime(bounded);
    setPosition(bounded);
  };
  const patch = (change: Partial<ClipSection>) => setSections((items) => items.map((item) => item.id === selected ? { ...item, ...change } : item));
  const clearDraft = (endpoint: Endpoint) => setDrafts((current) => {
    const next = { ...current }; delete next[`${selected}-${endpoint}`]; return next;
  });
  const snap = (time: number) => time >= duration ? duration : frameData.covers(time) && boundaries.length && time >= boundaries[0]
    ? boundaries[frameAt(boundaries, time)] : Math.round(time);
  const boundary = (endpoint: Endpoint, time: number) => {
    const snapped = snap(time);
    const value = endpoint === "startMillis" ? Math.max(0, Math.min(snapped, active.endMillis - 1)) : Math.min(duration, Math.max(active.startMillis + 1, snapped));
    patch({ [endpoint]: value }); clearDraft(endpoint); return value;
  };
  const mark = (endpoint: Endpoint) => {
    if (hasVideo && !frameData.ready) return;
    boundary(endpoint, position);
  };
  const handleBoundaryKey = (event: KeyboardEvent<HTMLInputElement>, endpoint: Endpoint) => {
    if (busy || event.ctrlKey || event.metaKey || event.altKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation(); stop();
    if (event.key === "Home" || event.key === "End") { seek(boundary(endpoint, event.key === "Home" ? 0 : duration)); return; }
    const current = active[endpoint];
    if (hasVideo && (!frameData.covers(current) || !frames.length)) { seek(current); frameData.retry(); return; }
    const forward = event.key === "ArrowRight" || event.key === "ArrowUp";
    let time = current + (forward ? 10 : -10);
    if (boundaries.length && current >= boundaries[0]) {
      const index = frameAt(boundaries, current);
      time = forward ? boundaries[index + 1] ?? duration : boundaries[index] < current ? boundaries[index] : boundaries[index - 1] ?? 0;
    } else if (forward && boundaries.length) time = Math.min(time, boundaries[0]);
    if (event.key === "Home") time = 0;
    if (event.key === "End") time = duration;
    seek(boundary(endpoint, time));
  };
  const step = (direction: -1 | 1) => {
    stop(); setReview(false);
    if (hasVideo && (!frameData.ready || !frames.length)) return;
    if (frames.length) {
      if (position < boundaries[0]) { seek(direction > 0 ? frames[0] + .01 : position - 10); return; }
      const index = Math.max(0, Math.min(frames.length - 1, frameAt(boundaries, position) + direction));
      seek(frames[index] + .01);
    } else seek(position + direction * 10);
  };
  const play = async (selection: boolean) => {
    setError(""); onPlay(); setReview(selection);
    if (selection) seek(active.startMillis);
    else if (position >= duration) seek(0);
    try {
      await Promise.all([audio.current?.play(), position >= Math.floor(videoOffset) ? video.current?.play() : undefined]);
      setPlaying(true);
    } catch { stop(); setMediaError(true); setError("Preview could not play on this device."); }
  };
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const tick = () => {
      const time = (audio.current?.currentTime ?? 0) * 1000;
      if (review && (time >= active.endMillis || time < active.startMillis - 5)) {
        if (loop) seek(active.startMillis);
        else { stop(); seek(active.endMillis); return; }
      } else {
        setPosition(time);
        if (video.current) {
          const videoTime = Math.max(0, time - videoOffset) / 1000;
          if (time < Math.floor(videoOffset)) video.current.pause();
          else if (video.current.paused && !video.current.ended) void video.current.play().catch(() => { stop(); setError("Video preview could not play."); });
          if (Math.abs(video.current.currentTime - videoTime) > .08) video.current.currentTime = videoTime;
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, review, loop, active.startMillis, active.endMillis]);
  const choose = (section: Section) => { stop(); setReview(false); setSelected(section.id); seek(section.startMillis); };
  const reorder = (index: number, delta: number) => setSections((items) => {
    const reordered = [...items]; const target = index + delta;
    if (target >= 0 && target < items.length) [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    return reordered;
  });
  return <div ref={containerRef} className="clip-dialog clip-workbench panel" tabIndex={-1} onKeyDown={(event) => {
    if (busy || (event.target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); step(event.key === "ArrowLeft" ? -1 : 1); }
    if (event.key.toLowerCase() === "i" || event.key.toLowerCase() === "o") { event.preventDefault(); mark(event.key.toLowerCase() === "i" ? "startMillis" : "endMillis"); }
    if (event.code === "Space" && !(event.target instanceof HTMLButtonElement)) { event.preventDefault(); if (playing) stop(); else void play(false); }
  }}>
    <header><div><p className="eyebrow">Local media · {sections.length} {sections.length === 1 ? "song" : "songs"}</p><h2 id="clip-editor-title">Choose your songs</h2><p>Select a section, fine-tune its edges, then add every song to your queue.</p></div><button aria-label="Close clip editor" onClick={onClose} disabled={busy}>✕</button></header>
    <div className="clip-workspace">
      <section className="clip-viewer" aria-label="Section player">
        <div className="clip-screen" tabIndex={0} aria-label="Preview player. Left and Right step frames. I sets Start, O sets End, Space plays.">
          {preview.videoUrl ? <video ref={video} src={preview.videoUrl} style={{ visibility: position < Math.floor(videoOffset) ? "hidden" : "visible" }} muted playsInline preload="metadata" onError={() => { stop(); setMediaError(true); setError("Video preview could not be decoded on this device."); }} />
            : <div className="clip-audio-art"><span>♫</span><strong>Audio preview</strong><p>{preview.suggestedTitle}</p></div>}
          <audio ref={audio} src={preview.previewUrl} preload="metadata" onEnded={() => { if (review && loop) void play(true); else stop(); }} onError={() => { stop(); setMediaError(true); setError("Audio preview is unavailable."); }} />
          <span className="clip-time-badge">{formatTimecodeMillis(position)}</span>
        </div>
        <div className="clip-transport">
          <button aria-label={hasVideo ? "Previous frame" : "Back 10 milliseconds"} onClick={() => step(-1)} disabled={busy || (hasVideo && (!frameData.ready || !frames.length))}>←</button>
          <button className="primary" onClick={() => playing ? stop() : void play(false)} disabled={busy}>{playing ? "Pause" : "Play"}</button>
          <button aria-label={hasVideo ? "Next frame" : "Forward 10 milliseconds"} onClick={() => step(1)} disabled={busy || (hasVideo && (!frameData.ready || !frames.length))}>→</button>
          <span>{formatTimecodeMillis(position)} / {formatTimecodeMillis(duration)}{frames.length > 0 && <small>{preview.direct ? "Nearby frame" : "Frame"} {frameAt(boundaries, position) + 1} / {frames.length}</small>}</span>
          <button onClick={() => void play(true)} disabled={busy}>▶ Review section</button>
          <label className="clip-volume"><span>Volume</span><input aria-label="Preview volume" type="range" min={0} max={1} step={.05} value={volume} onChange={(event) => setVolume(Number(event.target.value))} /></label>
        </div>
        <label className="clip-scrub"><span>Playhead</span><input aria-label="Seek preview" type="range" min={0} max={duration} step={1} value={position} disabled={busy} onChange={(event) => { setReview(false); seek(Number(event.target.value)); }} /></label>
        <div className="clip-timeline" aria-label="Selected sections">
          {sections.map((section, index) => <button key={section.id} className={section.id === selected ? "active" : ""} style={{ left: `${section.startMillis / duration * 100}%`, width: `${(section.endMillis - section.startMillis) / duration * 100}%`, top: `${index % 3 * 9}px` }} onClick={() => choose(section)} aria-label={`Select section ${index + 1}: ${section.title}`} disabled={busy}>{index + 1}</button>)}
        </div>
        <div className="clip-trim-track" style={{ "--clip-start": `${active.startMillis / duration * 100}%`, "--clip-end": `${active.endMillis / duration * 100}%` } as React.CSSProperties}>
          <div className="clip-selected-range" />
          <input aria-label="Drag section start" type="range" min={0} max={duration} step={1} value={active.startMillis} disabled={busy} onKeyDown={(event) => handleBoundaryKey(event, "startMillis")} onChange={(event) => { stop(); seek(boundary("startMillis", Number(event.target.value))); }} />
          <input aria-label="Drag section end" type="range" min={0} max={duration} step={1} value={active.endMillis} disabled={busy} onKeyDown={(event) => handleBoundaryKey(event, "endMillis")} onChange={(event) => { stop(); seek(boundary("endMillis", Number(event.target.value))); }} />
        </div>
        <div className="clip-boundaries">{(["startMillis", "endMillis"] as const).map((endpoint) => <label key={endpoint}><span>{endpoint === "startMillis" ? "Start · I" : "End · O"}</span><input key={`${selected}-${active[endpoint]}`} aria-label={endpoint === "startMillis" ? "Section start time" : "Section end time"} value={drafts[`${selected}-${endpoint}`] ?? formatTimecodeMillis(active[endpoint])} disabled={busy} aria-invalid={!validDrafts} onChange={(event) => setDrafts({ ...drafts, [`${selected}-${endpoint}`]: event.target.value })} onBlur={() => {
          const pending = pendingSections.find((section) => section.id === selected)!;
          if (validSections([pending], duration)) { patch({ startMillis: pending.startMillis, endMillis: pending.endMillis }); setDrafts((current) => { const next = { ...current }; delete next[`${selected}-startMillis`]; delete next[`${selected}-endMillis`]; return next; }); }
        }} /><button disabled={busy || (hasVideo && !frameData.ready)} onClick={() => mark(endpoint)}>Set at playhead</button></label>)}</div>
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
    {preview.direct && hasVideo && !frameData.ready && !playing && <p className="clip-help" role="status">{frameData.error || "Loading nearby frames… Playback and seeking are ready."}{frameData.error && <button onClick={frameData.retry}>Retry frame details</button>}</p>}
    {preview.direct && mediaError && onCompatible && <p className="clip-help">This device may need a compatible preview. Preparing the whole file can take minutes; you can cancel it.<button disabled={busy} onClick={() => { stop(); onCompatible(); }}>Prepare compatible preview</button></p>}
    {(!validDrafts || error) && <p className="clip-error" role="alert">{!validDrafts ? "Enter valid times within this file. End must be later than Start." : error}</p>}
    <footer><span>Your original file stays unchanged.</span><button onClick={onClose} disabled={busy}>Cancel</button><button className="primary" disabled={busy || !validDrafts} onClick={() => { stop(); setError(""); void onCommit(pendingSections.map(({ startMillis, endMillis, title }) => ({ startMillis, endMillis, title }))).catch(() => setError("Songs could not be added. Your sections are kept here; try again or open Activity after closing this editor.")); }}>{busy ? "Adding songs…" : `Add ${sections.length} ${sections.length === 1 ? "song" : "songs"} to queue`}</button></footer>
  </div>;
}
