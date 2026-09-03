import { describe, expect, it } from "vitest";
import {
  clampVolume,
  formatTime,
  playbackStartTime,
  shouldResyncVideo,
  toggleDocumentFullscreen,
  toggleMutedVolume,
} from "./playback";

describe("playback helpers", () => {
  it("rewinds ended media and preserves a middle position", () => {
    expect(playbackStartTime(6, 6, true)).toBe(0);
    expect(playbackStartTime(2.5, 6, false)).toBe(2.5);
  });

  it("resyncs only meaningful audio/video drift", () => {
    expect(shouldResyncVideo(3, 2.7)).toBe(true);
    expect(shouldResyncVideo(3, 2.95)).toBe(false);
  });

  it("formats a compact player clock", () => {
    expect(formatTime(65.8)).toBe("1:05");
  });

  it("clamps volume and restores the last audible level after mute", () => {
    expect(clampVolume(1.4)).toBe(1);
    expect(clampVolume(-0.3)).toBe(0);
    const muted = toggleMutedVolume(0.62, 0.9);
    expect(muted).toEqual({ volume: 0, lastAudibleVolume: 0.62 });
    expect(toggleMutedVolume(muted.volume, muted.lastAudibleVolume)).toEqual({
      volume: 0.62,
      lastAudibleVolume: 0.62,
    });
  });

  it("enters and exits fullscreen according to current document state", async () => {
    const calls: string[] = [];
    const target = {
      requestFullscreen: async () => { calls.push("enter"); },
    } as Pick<HTMLElement, "requestFullscreen">;
    const inactiveDocument = {
      fullscreenElement: null,
      exitFullscreen: async () => { calls.push("exit"); },
    } as Pick<Document, "fullscreenElement" | "exitFullscreen">;
    expect(await toggleDocumentFullscreen(inactiveDocument, target)).toBe("entered");
    const activeDocument = {
      fullscreenElement: {} as Element,
      exitFullscreen: async () => { calls.push("exit"); },
    } as Pick<Document, "fullscreenElement" | "exitFullscreen">;
    expect(await toggleDocumentFullscreen(activeDocument, target)).toBe("exited");
    expect(calls).toEqual(["enter", "exit"]);
  });
});
