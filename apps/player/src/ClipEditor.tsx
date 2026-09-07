import { useEffect, useRef, useState, type RefObject } from "react";
import { formatTimecodeMillis, parseTimecodeMillis } from "./clipSelection";
import "./clipEditor.css";

export type LocalClipPreview = {
  clipId: string; suggestedTitle: string; sizeBytes: number; durationMillis: number;
  frameDurationMillis?: number; frameTimesMillis?: number[]; previewUrl: string; videoUrl?: string; videoOffsetMillis?: number;
};
export type ClipSection = { startMillis: number; endMillis: number; title: string };
type Section = ClipSection & { id: number };

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

export default function ClipEditor({ preview, busy, containerRef, onClose, onCommit, onPlay }: {
  preview: LocalClipPreview; busy: boolean; containerRef: RefObject<HTMLDivElement | null>;
  onClose: () => void; onCommit: (sections: ClipSection[]) => Promise<void>; onPlay: () => void;
}) {
  const [sections, setSections] = useState<Section[]>([{ id: 1, startMillis: 0, endMillis: preview.durationMillis, title: preview.suggestedTitle }]);
  const [selected, setSelected] = useState(1);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(false);
  const [error, setError] = useState("");
  const [review, setReview] = useState(false);
  const [volume, setVolume] = useState(.8);
  const nextId = useRef(2);
  const audio = useRef<HTMLAudioElement>(null), video = useRef<HTMLVideoElement>(null);
  const active = sections.find((section) => section.id === selected)!;
  const frames = preview.frameTimesMillis ?? [];
  const duration = preview.durationMillis;
  const videoOffset = preview.videoOffsetMillis ?? 0;
  useEffect(() => { if (audio.current) audio.current.volume = volume; }, [volume]);
  const stop = () => {
    audio.current?.pause(); video.current?.pause();
    const time = (audio.current?.currentTime ?? 0) * 1000;
    if (video.current) video.current.currentTime = Math.max(0, time - videoOffset) / 1000;
    setPosition(time); setPlaying(false);
  };
  const seek = (time: number) => {
    const bounded = Math.max(0, Math.min(duration, time));
    if (audio.current) audio.current.currentTime = bounded / 1000;
    if (video.current) video.current.currentTime = Math.max(0, bounded - videoOffset) / 1000;
    setPosition(bounded);
  };
  const patch = (change: Partial<ClipSection>) => setSections((items) => items.map((item) => item.id === selected ? { ...item, ...change } : item));
  const boundary = (endpoint: "startMillis" | "endMillis", time: number) => {
    const snapped = frames.length ? Math.floor(frames[frameAt(frames, time)]) : Math.round(time);
    patch({ [endpoint]: endpoint === "startMillis" ? Math.min(snapped, active.endMillis - 1) : Math.max(active.startMillis + 1, time >= duration ? duration : snapped) });
  };
  const step = (direction: -1 | 1) => {
    stop(); setReview(false);
    if (frames.length) {
      const index = Math.max(0, Math.min(frames.length - 1, frameAt(frames, position) + direction));
      seek(frames[index] + .01);
    } else seek(position + direction * 10);
  };
  const play = async (selection: boolean) => {
    setError(""); onPlay(); setReview(selection);
    if (selection) seek(active.startMillis);
    else if (position >= duration) seek(0);
    try {
      await Promise.all([audio.current?.play(), position >= videoOffset ? video.current?.play() : undefined]);
      setPlaying(true);
    } catch { stop(); setError("Preview could not play. Try seeking and playing again."); }
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
          if (time < videoOffset) video.current.pause();
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
    if (event.key.toLowerCase() === "i" || event.key.toLowerCase() === "o") { event.preventDefault(); boundary(event.key.toLowerCase() === "i" ? "startMillis" : "endMillis", position); }
    if (event.code === "Space" && !(event.target instanceof HTMLButtonElement)) { event.preventDefault(); if (playing) stop(); else void play(false); }
  }}>
    <header><div><p className="eyebrow">Local media · {sections.length} {sections.length === 1 ? "song" : "songs"}</p><h2 id="clip-editor-title">Choose your songs</h2><p>Select a section, fine-tune its edges, then add every song to your queue.</p></div><button aria-label="Close clip editor" onClick={onClose} disabled={busy}>✕</button></header>
    <div className="clip-workspace">
      <section className="clip-viewer" aria-label="Section player">
        <div className="clip-screen" tabIndex={0} aria-label="Preview player. Left and Right step frames. I sets Start, O sets End, Space plays.">
          {preview.videoUrl ? <video ref={video} src={preview.videoUrl} style={{ visibility: position < videoOffset ? "hidden" : "visible" }} muted playsInline preload="auto" onError={() => { stop(); setError("Video preview could not be decoded on this device."); }} />
            : <div className="clip-audio-art"><span>♫</span><strong>Audio preview</strong><p>{preview.suggestedTitle}</p></div>}
          <audio ref={audio} src={preview.previewUrl} preload="auto" onEnded={() => { if (review && loop) void play(true); else stop(); }} onError={() => { stop(); setError("Audio preview is unavailable."); }} />
          <span className="clip-time-badge">{formatTimecodeMillis(position)}</span>
        </div>
        <div className="clip-transport">
          <button aria-label={frames.length ? "Previous frame" : "Back 10 milliseconds"} onClick={() => step(-1)} disabled={busy}>←</button>
          <button className="primary" onClick={() => playing ? stop() : void play(false)} disabled={busy}>{playing ? "Pause" : "Play"}</button>
          <button aria-label={frames.length ? "Next frame" : "Forward 10 milliseconds"} onClick={() => step(1)} disabled={busy}>→</button>
          <span>{formatTimecodeMillis(position)} / {formatTimecodeMillis(duration)}{frames.length > 0 && <small>Frame {frameAt(frames, position) + 1} / {frames.length}</small>}</span>
          <button onClick={() => void play(true)} disabled={busy}>▶ Review section</button>
          <label className="clip-volume"><span>Volume</span><input aria-label="Preview volume" type="range" min={0} max={1} step={.05} value={volume} onChange={(event) => setVolume(Number(event.target.value))} /></label>
        </div>
        <label className="clip-scrub"><span>Playhead</span><input aria-label="Seek preview" type="range" min={0} max={duration} step={1} value={position} disabled={busy} onChange={(event) => { setReview(false); seek(Number(event.target.value)); }} /></label>
        <div className="clip-timeline" aria-label="Selected sections">
          {sections.map((section, index) => <button key={section.id} className={section.id === selected ? "active" : ""} style={{ left: `${section.startMillis / duration * 100}%`, width: `${(section.endMillis - section.startMillis) / duration * 100}%`, top: `${index % 3 * 9}px` }} onClick={() => choose(section)} aria-label={`Select section ${index + 1}: ${section.title}`} disabled={busy}>{index + 1}</button>)}
        </div>
        <div className="clip-trim-track" style={{ "--clip-start": `${active.startMillis / duration * 100}%`, "--clip-end": `${active.endMillis / duration * 100}%` } as React.CSSProperties}>
          <div className="clip-selected-range" />
          <input aria-label="Drag section start" type="range" min={0} max={duration} step={1} value={active.startMillis} disabled={busy} onChange={(event) => { stop(); boundary("startMillis", Number(event.target.value)); seek(Number(event.target.value)); }} />
          <input aria-label="Drag section end" type="range" min={0} max={duration} step={1} value={active.endMillis} disabled={busy} onChange={(event) => { stop(); boundary("endMillis", Number(event.target.value)); seek(Number(event.target.value)); }} />
        </div>
        <div className="clip-boundaries">{(["startMillis", "endMillis"] as const).map((endpoint) => <label key={endpoint}><span>{endpoint === "startMillis" ? "Start · I" : "End · O"}</span><input key={`${selected}-${active[endpoint]}`} aria-label={endpoint === "startMillis" ? "Section start time" : "Section end time"} defaultValue={formatTimecodeMillis(active[endpoint])} disabled={busy} onBlur={(event) => { try { const time = parseTimecodeMillis(event.target.value); if (time > duration) throw Error(); boundary(endpoint, time); setError(""); } catch { setError("Enter a time within this file, such as 01:23.500."); } }} /><button disabled={busy} onClick={() => boundary(endpoint, position)}>Set at playhead</button></label>)}</div>
        <div className="clip-help"><label><input type="checkbox" checked={loop} onChange={(event) => setLoop(event.target.checked)} /> Loop section</label><span><kbd>←</kbd> <kbd>→</kbd> {frames.length ? "one frame" : "10 ms"} · <kbd>I</kbd> Start · <kbd>O</kbd> End · <kbd>Space</kbd> play</span></div>
      </section>
      <aside className="clip-sections" aria-label="Songs to add">
        <div className="clip-section-heading"><h3>Your songs <span>{sections.length}</span></h3><p>Each section becomes a separate song.</p></div>
        <ol>{sections.map((section, index) => <li key={section.id} className={section.id === selected ? "active" : ""}>
          <button className="clip-section-select" onClick={() => choose(section)} disabled={busy} aria-label={`Edit section ${index + 1}`} aria-pressed={section.id === selected}><b>{String(index + 1).padStart(2, "0")}</b><span>{formatTimecodeMillis(section.endMillis - section.startMillis)}<small>{formatTimecodeMillis(section.startMillis)} → {formatTimecodeMillis(section.endMillis)}</small></span></button>
          <input aria-label={`Song ${index + 1} title`} value={section.title} maxLength={200} disabled={busy} onFocus={() => { if (selected !== section.id) choose(section); }} onChange={(event) => setSections((items) => items.map((item) => item.id === section.id ? { ...item, title: event.target.value } : item))} />
          <div className="clip-section-actions"><button aria-label={`Move section ${index + 1} up`} onClick={() => reorder(index, -1)} disabled={busy || index === 0}>↑</button><button aria-label={`Move section ${index + 1} down`} onClick={() => reorder(index, 1)} disabled={busy || index === sections.length - 1}>↓</button><button aria-label={`Remove section ${index + 1}`} disabled={busy || sections.length === 1} onClick={() => { const remaining = sections.filter((item) => item.id !== section.id); setSections(remaining); if (selected === section.id) choose(remaining[Math.min(index, remaining.length - 1)]); }}>Remove</button></div>
        </li>)}</ol>
        <button className="clip-add-section" disabled={busy || sections.length >= 128} onClick={() => {
          const startMillis = Math.min(duration - 1, Math.floor(position));
          const section = { id: nextId.current++, title: `${preview.suggestedTitle.slice(0, 185)} · ${sections.length + 1}`, startMillis, endMillis: Math.min(duration, startMillis + 30000) };
          setSections([...sections, section]); choose(section);
        }}>＋ Add section at playhead</button>
        <p className="clip-queue-note">Added to the top of Library & queue in this order. Add each song’s lyrics there to start processing.</p>
      </aside>
    </div>
    {error && <p className="clip-error" role="alert">{error}</p>}
    <footer><span>Your original file stays unchanged.</span><button onClick={onClose} disabled={busy}>Cancel</button><button className="primary" disabled={busy || !validSections(sections, duration)} onClick={() => { stop(); setError(""); void onCommit(sections.map(({ startMillis, endMillis, title }) => ({ startMillis, endMillis, title }))).catch(() => setError("Songs could not be added. Your sections are kept here; try again or open Activity after closing this editor.")); }}>{busy ? "Adding songs…" : `Add ${sections.length} ${sections.length === 1 ? "song" : "songs"} to queue`}</button></footer>
  </div>;
}
