import { describe, expect, it } from "vitest";

import { playbackStartTime } from "./playback";

describe("playbackStartTime", () => {
  it("rewinds an ended track", () => {
    expect(playbackStartTime(6, 6, true)).toBe(0);
  });

  it("rewinds a track at the duration boundary even if ended is stale", () => {
    expect(playbackStartTime(5.98, 6, false)).toBe(0);
  });

  it("preserves a valid position in the middle of a track", () => {
    expect(playbackStartTime(2.5, 6, false)).toBe(2.5);
  });

  it("sanitizes an invalid current position", () => {
    expect(playbackStartTime(Number.NaN, Number.NaN, false)).toBe(0);
    expect(playbackStartTime(-1, 6, false)).toBe(0);
  });
});
