const END_RESTART_EPSILON_SECONDS = 0.05;

export function playbackStartTime(
  currentTime: number,
  mediaDuration: number,
  ended: boolean,
): number {
  const safeCurrentTime = Number.isFinite(currentTime) && currentTime >= 0
    ? currentTime
    : 0;
  if (ended) return 0;
  if (
    Number.isFinite(mediaDuration)
    && mediaDuration > 0
    && safeCurrentTime >= mediaDuration - END_RESTART_EPSILON_SECONDS
  ) return 0;
  return safeCurrentTime;
}

export function shouldResyncVideo(audioTime: number, videoTime: number): boolean {
  return Number.isFinite(audioTime)
    && Number.isFinite(videoTime)
    && Math.abs(audioTime - videoTime) > 0.12;
}

export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function clampVolume(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

export function toggleMutedVolume(
  volume: number,
  lastAudibleVolume: number,
): { volume: number; lastAudibleVolume: number } {
  const current = clampVolume(volume);
  const fallback = clampVolume(lastAudibleVolume) || 0.9;
  if (current > 0.001) {
    return { volume: 0, lastAudibleVolume: current };
  }
  return { volume: fallback, lastAudibleVolume: fallback };
}

export async function toggleDocumentFullscreen(
  fullscreenDocument: Pick<Document, "fullscreenElement" | "exitFullscreen">,
  target: Pick<HTMLElement, "requestFullscreen"> | null,
): Promise<"entered" | "exited"> {
  if (fullscreenDocument.fullscreenElement) {
    await fullscreenDocument.exitFullscreen();
    return "exited";
  }
  if (!target) throw new Error("fullscreen target is unavailable");
  await target.requestFullscreen();
  return "entered";
}
