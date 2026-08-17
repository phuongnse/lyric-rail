const END_RESTART_EPSILON_SECONDS = 0.05;

/**
 * Resolve the position used when the user presses Play.
 *
 * Chromium does not consistently rewind an ended media element when play() is
 * called again, especially after its audio source was switched. Keeping this
 * decision pure makes the replay-at-end behavior deterministic and testable.
 */
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
  ) {
    return 0;
  }

  return safeCurrentTime;
}
