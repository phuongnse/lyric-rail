import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useFocusContainment } from "./focus";
import { IconButton } from "./Icon";
import { formatTimecodeMillis } from "./clipSelection";
import { useClipPlayback, type ClipPlaybackRange, type LocalClipPreview } from "./useClipPlayback";
import { cleanVideoMetadata, validVideoMetadata, MAX_VIDEO_TEXT_CHARS, MAX_LYRICS_BYTES, type VideoMetadata } from "./videoMetadata";
import "./clipEditor.css";

// Shared by clipping and Library: the caller supplies media, playback bounds and metadata.
// Saving returns metadata only; this dialog cannot change the caller's range.
export function VideoEditor({ preview, range, value, busy, onClose, onSave, onPlay, onCompatible, preparationError }: {
  preview: LocalClipPreview; range: ClipPlaybackRange; value: VideoMetadata; busy: boolean;
  onClose: () => void; onSave: (value: VideoMetadata) => void; onPlay: () => void; onCompatible?: () => void; preparationError?: string;
}) {
  const [draft, setDraft] = useState(() => ({ title: value.title, artist: value.artist ?? '', composer: value.composer ?? '', lyrics: value.lyrics ?? '' }));
  const dialog = useRef<HTMLDivElement>(null), title = useRef<HTMLInputElement>(null);
  useFocusContainment(true, dialog, title);
  const { audio, video, position, playing, error, mediaError, fail, seek, stop, play, ended } = useClipPlayback(preview, range, false, busy, onPlay, true);
  const duration = range.endMillis - range.startMillis;
  const elapsed = Math.max(0, Math.min(duration, position - range.startMillis));
  const valid = validVideoMetadata(draft);
  const seekClip = (time: number) => seek(range.startMillis + Math.max(0, Math.min(duration, time)));
  return createPortal(<div className="modal-layer clip-video-modal" role="dialog" aria-modal="true" aria-labelledby="video-editor-title"
    onKeyDown={(event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); }
    }}>
    <div className="clip-dialog clip-workbench clip-video-dialog panel" ref={dialog} tabIndex={-1}>
      <header><h2 id="video-editor-title">Edit video</h2><IconButton label="Close video editor" icon="close" onClick={onClose} disabled={busy} /></header>
      <div className="clip-video-content">
        <section aria-label="Video preview">
          <div className="clip-screen">
            {preview.videoUrl && !preview.requiresCompatibility
              ? <video ref={video} aria-label="Selected video preview" src={preview.videoUrl} style={{ visibility: position < Math.max(range.startMillis, Math.floor(preview.videoOffsetMillis ?? 0)) ? 'hidden' : 'visible' }} muted playsInline preload="metadata" onError={() => fail('Video preview could not be decoded on this device.')} />
              : <div className="clip-audio-art"><span aria-hidden="true">♫</span><strong>{preview.requiresCompatibility ? 'Compatible preview needed' : 'Audio preview'}</strong></div>}
            <audio ref={audio} aria-label="Selected video audio" src={preview.requiresCompatibility ? undefined : preview.previewUrl} preload="metadata" onEnded={ended} onError={() => fail('Audio preview is unavailable.')} />
          </div>
          <div className="clip-transport"><button className="clip-play" disabled={busy || preview.requiresCompatibility} onClick={() => playing ? stop() : void play()}>{playing ? 'Pause' : 'Play'}</button>
            <output className="clip-clock">{formatTimecodeMillis(elapsed)} / {formatTimecodeMillis(duration)}</output>
          </div>
          <input className="clip-video-seek" type="range" aria-label="Seek video" aria-valuetext={formatTimecodeMillis(elapsed)} min={0} max={duration} step={1} value={elapsed} disabled={busy || preview.requiresCompatibility} onChange={(event) => seekClip(Number(event.target.value))} />
        </section>
        <section className="clip-video-fields" aria-label="Video information">
          <label>Video name<input ref={title} aria-label="Video name" value={draft.title} maxLength={MAX_VIDEO_TEXT_CHARS} disabled={busy} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
          <div className="clip-metadata-fields">
            <label>Artist<input aria-label="Artist" value={draft.artist} maxLength={MAX_VIDEO_TEXT_CHARS} disabled={busy} onChange={(event) => setDraft({ ...draft, artist: event.target.value })} /></label>
            <label>Composer<input aria-label="Composer" value={draft.composer} maxLength={MAX_VIDEO_TEXT_CHARS} disabled={busy} onChange={(event) => setDraft({ ...draft, composer: event.target.value })} /></label>
          </div>
          <label>Lyrics<textarea aria-label="Lyrics" value={draft.lyrics} maxLength={MAX_LYRICS_BYTES} disabled={busy} onChange={(event) => setDraft({ ...draft, lyrics: event.target.value })} placeholder="Paste lyrics" spellCheck={false} /></label>
        </section>
      </div>
      {(preview.requiresCompatibility || mediaError) && onCompatible && <button disabled={busy} onClick={() => { stop(); onCompatible(); }}>Prepare compatible preview</button>}
      {preparationError && <p className="clip-error" role="alert">{preparationError}</p>}
      {error && <p className="clip-error" role="alert">{error}</p>}
      {!valid && <p className="clip-error" role="alert">Check the video name, information and lyrics.</p>}
      <footer><button disabled={busy} onClick={onClose}>Cancel</button><button className="primary" disabled={busy || !valid} onClick={() => { stop(); onSave(cleanVideoMetadata(draft)); }}>Save</button></footer>
    </div>
  </div>, document.body);
}
