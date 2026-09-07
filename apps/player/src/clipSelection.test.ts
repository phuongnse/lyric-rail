import { describe, expect, it } from "vitest";
import {
  formatTimecodeMillis,
  loopedPreviewTime,
  nudgedTimecode,
  parseTimecodeMillis,
  shouldOpenClipEditor,
  validateClipRange,
} from "./clipSelection";

describe("local file clip selection", () => {
  it("opens the editor only for one supported local media file", () => {
    expect(shouldOpenClipEditor(["C:\\Music\\Long Concert.MP4"])).toBe(true);
    expect(shouldOpenClipEditor(["/music/song.flac"])).toBe(true);
    expect(shouldOpenClipEditor(["C:\\Music\\song.lrail"])).toBe(false);
    expect(shouldOpenClipEditor(["song.mp4", "other.mp4"])).toBe(false);
    expect(shouldOpenClipEditor([])).toBe(false);
  });

  it("parses seconds, minute and hour forms to exact milliseconds", () => {
    expect(parseTimecodeMillis("12.345")).toBe(12_345);
    expect(parseTimecodeMillis("02:03.456")).toBe(123_456);
    expect(parseTimecodeMillis("01:02:03.004")).toBe(3_723_004);
    expect(formatTimecodeMillis(3_723_004)).toBe("01:02:03.004");
  });

  it("rejects malformed or out-of-range timestamps", () => {
    for (const value of ["", "1:60", "1:60:00", "-1", "one", "1::2"]) {
      expect(() => parseTimecodeMillis(value)).toThrow();
    }
    expect(() => validateClipRange("00:00:03.000", "00:00:02.000", 10_000)).toThrow();
    expect(() => validateClipRange("0", "11", 10_000)).toThrow();
  });

  it("nudges by a probed frame interval and clamps to the duration", () => {
    expect(nudgedTimecode("00:00:01.000", 33.3667, 2_000)).toBe("00:00:01.033");
    expect(nudgedTimecode("00:00:00.010", -33.3667, 2_000)).toBe("00:00:00.000");
    expect(nudgedTimecode("00:00:01.990", 33.3667, 2_000)).toBe("00:00:02.000");
  });

  it("keeps loop preview inside both Start and End boundaries", () => {
    expect(loopedPreviewTime(500, 1_000, 2_000)).toBe(1_000);
    expect(loopedPreviewTime(1_500, 1_000, 2_000)).toBeUndefined();
    expect(loopedPreviewTime(2_000, 1_000, 2_000)).toBe(1_000);
    expect(loopedPreviewTime(2_500, 1_000, 2_000)).toBe(1_000);
  });
});
