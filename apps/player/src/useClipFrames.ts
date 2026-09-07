import { useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { LocalClipPreview } from "./ClipEditor";

export type FrameWindow = { frameTimesMillis: number[]; fromMillis: number; toMillis: number };

// One active request and one replaceable destination; scrubbing never queues a file scan.
export function useClipFrames(preview: LocalClipPreview, position: number, playing: boolean) {
  const [windows, setWindows] = useState<Array<FrameWindow & { clipId: string }>>([]);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const active = useRef(false);
  const inFlight = useRef<{ id: string; generation: number } | undefined>(undefined);
  const generation = useRef(0);
  const desired = useRef<{ clipId: string; time: number; generation: number } | undefined>(undefined);
  const directVideo = Boolean(preview.direct && preview.videoUrl && !preview.requiresCompatibility);
  // Retain at most two overlapping, bounded responses. Their shared frames join
  // the windows without mistaking a probe boundary for the beginning/end of media.
  const loaded = useMemo(() => {
    const current = windows.filter((window) => window.clipId === preview.clipId);
    if (!current.length) return undefined;
    return { fromMillis: Math.min(...current.map((window) => window.fromMillis)),
      toMillis: Math.max(...current.map((window) => window.toMillis)),
      frameTimesMillis: [...new Set(current.flatMap((window) => window.frameTimesMillis))].sort((a, b) => a - b) };
  }, [windows, preview.clipId]);
  const covers = (time: number) => !preview.requiresCompatibility && (!directVideo || Boolean(loaded?.frameTimesMillis.length
    && time >= loaded.fromMillis && time <= loaded.toMillis
    && (loaded.fromMillis === 0 || time > Math.floor(loaded.frameTimesMillis[0]) + .02)
    && (loaded.toMillis === preview.durationMillis || time < Math.floor(loaded.frameTimesMillis[loaded.frameTimesMillis.length - 1]) - .02)));
  const ready = covers(position);
  useEffect(() => {
    const revision = ++generation.current;
    desired.current = undefined;
    if (!directVideo || playing || ready || error) return;
    let target = position;
    if (loaded?.frameTimesMillis.length && position >= loaded.fromMillis && position <= loaded.toMillis) {
      if (position <= Math.floor(loaded.frameTimesMillis[0]) + .02 && loaded.fromMillis > 0) target = Math.max(0, loaded.frameTimesMillis[0] - 3000);
      else if (position >= Math.floor(loaded.frameTimesMillis[loaded.frameTimesMillis.length - 1]) - .02 && loaded.toMillis < preview.durationMillis) target = Math.min(preview.durationMillis, loaded.frameTimesMillis[loaded.frameTimesMillis.length - 1] + 3000);
    }
    const timer = window.setTimeout(() => {
      desired.current = { clipId: preview.clipId, time: Math.floor(target), generation: revision };
      const pump = async () => {
        if (active.current) return;
        active.current = true;
        try {
          while (desired.current) {
            const request = desired.current; desired.current = undefined;
            const requestId = crypto.randomUUID();
            inFlight.current = { id: requestId, generation: request.generation };
            try {
              const frames = await invoke<FrameWindow>("local_clip_frames", { clipId: request.clipId, timeMillis: request.time, requestId });
              if (generation.current === request.generation) {
                if (!frames.frameTimesMillis.length || (loaded && !ready && frames.frameTimesMillis.every((time) => loaded.frameTimesMillis.includes(time))
                  && frames.fromMillis !== 0 && frames.toMillis !== preview.durationMillis)) setError("No nearby frame timestamps were found. Seek to another point or retry frame details.");
                else {
                  setWindows((previous) => [...previous.slice(-1).filter((window) => window.clipId === request.clipId
                    && window.fromMillis <= frames.toMillis && window.toMillis >= frames.fromMillis), { ...frames, clipId: request.clipId }]);
                  setError("");
                }
              }
            } catch {
              if (generation.current === request.generation) setError("Frame details could not load. You can still play, seek or enter times.");
            } finally { inFlight.current = undefined; }
          }
        } finally { active.current = false; }
      };
      void pump();
    }, 150);
    return () => {
      window.clearTimeout(timer); ++generation.current; desired.current = undefined;
      if (inFlight.current?.generation === revision) void invoke("cancel_clip_frames", { requestId: inFlight.current.id }).catch(() => {});
    };
  }, [preview.clipId, preview.durationMillis, directVideo, playing, position, ready, retry, loaded, error]);
  useEffect(() => { setError(""); }, [position, preview.clipId]);
  return { frames: directVideo ? loaded?.frameTimesMillis ?? [] : preview.frameTimesMillis ?? [],
    ready, covers, error: ready ? "" : error, retry: () => { setError(""); setRetry((value) => value + 1); } };
}
