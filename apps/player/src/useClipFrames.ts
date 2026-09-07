import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { LocalClipPreview } from "./ClipEditor";

export type FrameWindow = { frameTimesMillis: number[]; fromMillis: number; toMillis: number };

// One active request and one replaceable destination; scrubbing never queues a file scan.
export function useClipFrames(preview: LocalClipPreview, position: number, playing: boolean) {
  const [loaded, setLoaded] = useState<FrameWindow & { clipId: string }>();
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const active = useRef(false);
  const inFlight = useRef<{ id: string; generation: number } | undefined>(undefined);
  const generation = useRef(0);
  const desired = useRef<{ clipId: string; time: number; generation: number } | undefined>(undefined);
  const directVideo = Boolean(preview.direct && preview.videoUrl);
  const covers = (time: number) => !directVideo || Boolean(loaded?.clipId === preview.clipId && time >= loaded.fromMillis && time <= loaded.toMillis - (loaded.toMillis < preview.durationMillis ? 500 : 0));
  const ready = covers(position);
  useEffect(() => {
    const revision = ++generation.current;
    desired.current = undefined;
    if (!directVideo || playing || ready) return;
    const timer = window.setTimeout(() => {
      desired.current = { clipId: preview.clipId, time: Math.floor(position), generation: revision };
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
              if (generation.current === request.generation) { setLoaded({ ...frames, clipId: request.clipId }); setError(""); }
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
  }, [preview.clipId, directVideo, playing, position, ready, retry]);
  return { frames: directVideo ? loaded?.clipId === preview.clipId ? loaded.frameTimesMillis : [] : preview.frameTimesMillis ?? [],
    ready, covers, error: ready ? "" : error, retry: () => { setError(""); setRetry((value) => value + 1); } };
}
