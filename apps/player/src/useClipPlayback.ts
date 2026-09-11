import { useEffect, useMemo, useRef, useState } from "react";
import { frameAt } from "./clipSelection";
import { useClipFrames } from "./useClipFrames";
export type LocalClipPreview = {
  direct?: boolean; requiresCompatibility?: boolean; clipId: string; suggestedTitle: string; sizeBytes: number; durationMillis: number;
  frameDurationMillis?: number; frameTimesMillis?: number[]; previewUrl: string; videoUrl?: string; videoOffsetMillis?: number;
};

export type ClipPlaybackRange = { startMillis: number; endMillis: number };

export function useClipPlayback(preview: LocalClipPreview, range: ClipPlaybackRange | null, loop: boolean, busy: boolean, onPlay: () => void, restrictToRange = false) {
  const audio = useRef<HTMLAudioElement>(null), video = useRef<HTMLVideoElement>(null);
  const [position, setPosition] = useState(0);
  const [status, setStatus] = useState<"paused" | "starting" | "playing">("paused");
  const playing = status !== "paused";
  const [volume, setVolume] = useState(.8), [error, setError] = useState("");
  const [mediaError, setMediaError] = useState(false);
  const generation = useRef(0), wantsPlay = useRef(false), videoStarting = useRef(false);
  const startupTimer = useRef<number | undefined>(undefined);
  const clearStartup = () => { window.clearTimeout(startupTimer.current); startupTimer.current = undefined; };
  const frameData = useClipFrames(preview, position, playing || busy);
  const frames = frameData.frames;
  const boundaries = useMemo(() => frames.map(Math.floor), [frames]);
  const duration = preview.durationMillis, offset = preview.videoOffsetMillis ?? 0;
  const currentRange = useRef(range); currentRange.current = range;
  const videoTime = (time: number) => {
    const bounds = currentRange.current;
    if (restrictToRange && bounds) time = Math.max(bounds.startMillis, Math.min(bounds.endMillis - 1, time));
    const index = frameAt(boundaries, time);
    // Stored millisecond boundaries refer to the measured frame, including VFR.
    return Math.max(0, (boundaries[index] === time ? frames[index] + .01 : time) - offset) / 1000;
  };
  const seek = (time: number) => {
    const bounds = restrictToRange ? currentRange.current : null;
    const bounded = Math.max(bounds?.startMillis ?? 0, Math.min(bounds?.endMillis ?? duration, time));
    if (audio.current) audio.current.currentTime = bounded / 1000;
    if (video.current) video.current.currentTime = videoTime(bounded);
    setPosition(bounded);
    return bounded;
  };
  const stop = () => {
    clearStartup();
    ++generation.current; wantsPlay.current = false;
    audio.current?.pause(); video.current?.pause();
    const time = (audio.current?.currentTime ?? 0) * 1000;
    if (video.current) video.current.currentTime = videoTime(time);
    setPosition(time); setStatus("paused");
  };
  const fail = (message: string) => { stop(); setMediaError(true); setError(message); };
  const play = async (from?: number, bounds = range) => {
    if (busy || preview.requiresCompatibility) return;
    const start = bounds?.startMillis ?? 0, end = bounds?.endMillis ?? duration;
    const time = from ?? (audio.current?.currentTime ?? position / 1000) * 1000;
    const target = time < start || time >= end ? start : time;
    clearStartup();
    const request = ++generation.current;
    wantsPlay.current = true; currentRange.current = bounds;
    setError(""); onPlay(); seek(target); setStatus("starting");
    startupTimer.current = window.setTimeout(() => {
      if (request === generation.current) fail("Preview took too long to start. Try again or prepare a compatible preview.");
    }, 10000);
    const sound = audio.current, picture = video.current;
    try {
      await Promise.all([sound?.play(), target >= Math.floor(offset) ? picture?.play() : undefined]);
      if (request === generation.current) { clearStartup(); setStatus("playing"); }
      else if (!wantsPlay.current) { sound?.pause(); picture?.pause(); }
    } catch {
      if (request === generation.current) fail("Preview could not play on this device.");
    }
  };
  useEffect(() => { if (audio.current) audio.current.volume = volume; }, [volume]);
  useEffect(() => {
    const sound = audio.current, picture = video.current;
    stop(); seek(0); setMediaError(false); setError("");
    return () => { clearStartup(); ++generation.current; wantsPlay.current = false; sound?.pause(); picture?.pause(); };
  }, [preview.clipId]);
  useEffect(() => { if (busy) stop(); }, [busy]);
  useEffect(() => {
    // Audio can advance before the video's play promise settles.
    if (status === "paused") return;
    let frame = 0;
    const tick = () => {
      const time = (audio.current?.currentTime ?? 0) * 1000, bounds = currentRange.current;
      if (bounds && time >= bounds.endMillis) {
        if (loop) seek(bounds.startMillis);
        else { stop(); seek(bounds.endMillis); return; }
      } else if (bounds && time < bounds.startMillis - 5) seek(bounds.startMillis);
      else {
        setPosition(time);
        const picture = video.current;
        if (status === "playing" && picture) {
          const target = Math.max(0, time - offset) / 1000;
          if (time < Math.floor(offset)) picture.pause();
          else if (picture.paused && !picture.ended && !videoStarting.current) {
            videoStarting.current = true;
            const request = generation.current;
            void picture.play().then(() => { if (!wantsPlay.current) picture.pause(); })
              .catch(() => { if (request === generation.current) fail("Video preview could not play."); })
              .finally(() => { videoStarting.current = false; });
          }
          if (Math.abs(picture.currentTime - target) > .08) picture.currentTime = target;
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [status, loop, offset, preview.clipId]);
  const ended = () => {
    const bounds = currentRange.current;
    if (bounds && loop) void play(bounds.startMillis, bounds);
    else stop();
  };
  return { audio, video, position, playing, volume, setVolume, error, setError, mediaError, fail, frameData, boundaries, seek, stop, play, ended };
}
